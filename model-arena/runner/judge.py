# -*- coding: utf-8 -*-
"""judge.py - rule-based answer judging (no LLM, zero cost).
Currently covers: math final-answer extraction + numeric/fraction equivalence.
"""
import re
from fractions import Fraction

# ---------- answer extraction ----------
BOXED = re.compile(r"\boxed\{([^{}]+)\}")
ANSWER_CN = re.compile(r"(?:答案|结果|答)\s*(?:是|为|:)?\s*[:：]?\s*\*{0,2}([^\n。，；,;]{1,40})")
ANSWER_EN = re.compile(r"(?:answer|result)\s*(?:is|:)\s*\*{0,2}([^\n,;]{1,40})", re.I)
FINAL_LINE = re.compile(r"(?:final|最终)\s*(?:answer|答案)?\s*[:：]?\s*([^\n]{1,40})", re.I)
NUM = re.compile(r"-?\d+(?:\.\d+)?(?:\s*/\s*-?\d+(?:\.\d+)?)?")
# radical forms models commonly emit: 6\sqrt{17}, 6√17, \sqrt{612}, √17
RADICAL = re.compile(r"(\d+(?:\.\d+)?)?\s*(?:\\sqrt\{?(\d+(?:\.\d+)?)\}?|√\s*(\d+(?:\.\d+)?))")
FRAC = re.compile(r"\\frac\{\s*(-?\d+(?:\.\d+)?)\}\{\s*(-?\d+(?:\.\d+)?)\}")
SQRT_EXPR = re.compile(r"(\d+(?:\.\d+)?)?\*?sqrt\(?(\d+(?:\.\d+)?)\)?")
# assignment like "x = 7" / "y=9.6" (strong signal when solving for a variable)
ASSIGN = re.compile(r"[a-zA-Z]\s*=\s*(-?\d+(?:\.\d+)?(?:\s*/\s*-?\d+(?:\.\d+)?)?)")


def _clean_fragment(s):
    s = s.strip().strip("*`$ \t")
    s = s.replace(",", "").replace("，", "")
    s = re.sub(r"[。．\.]+$", "", s)
    return s.strip()


def extract_final_answer(text):
    """Best-effort extraction of the model's final answer. Returns (value_str, method)."""
    if not text:
        return None, "empty"
    # 1) \boxed{...} - take the last one
    boxes = BOXED.findall(text)
    if boxes:
        return _clean_fragment(boxes[-1]), "boxed"
    # 2) explicit "answer is / 答案是"
    for pat, mth in ((ANSWER_CN, "answer_cn"), (ANSWER_EN, "answer_en"), (FINAL_LINE, "final_line")):
        ms = pat.findall(text)
        if ms:
            cand = _clean_fragment(ms[-1])
            mfr = FRAC.search(cand)
            if mfr:
                return "%s/%s" % (mfr.group(1), mfr.group(2)), "latex_frac"
            # radical FIRST: "6\sqrt{17}" must not be read as the leading "6"
            mr = RADICAL.search(cand)
            if mr:
                coef = mr.group(1) or "1"
                rad = mr.group(2) or mr.group(3)
                return "%s*sqrt(%s)" % (coef, rad), "radical"
            n = NUM.search(cand)
            if n:
                return n.group(0).replace(" ", ""), mth
            if cand:
                return cand, mth
    # 2.5) radical form anywhere as the final answer (e.g. "answer: 6\sqrt{17}")
    for pat in (ANSWER_CN, ANSWER_EN, FINAL_LINE):
        for m in pat.findall(text):
            mr = RADICAL.search(m)
            if mr:
                coef = mr.group(1) or "1"
                rad = mr.group(2) or mr.group(3)
                return "%s*sqrt(%s)" % (coef, rad), "radical"
    # 3) variable assignment "x = 7" (strongest remaining signal)
    assigns = ASSIGN.findall(text)
    if assigns:
        return assigns[-1].replace(" ", ""), "assignment"
    # 4) last numeric token in the text
    nums = NUM.findall(text)
    if nums:
        return nums[-1].replace(" ", ""), "last_number"
    return None, "none"


def _to_number(s):
    """Parse '7', '9.6', '3/10', '6*sqrt(17) (LaTeX radical)' -> number; None if not numeric."""
    if s is None:
        return None
    s = s.strip().replace(" ", "")
    mf = FRAC.fullmatch(s)
    if mf:
        try:
            return float(mf.group(1)) / float(mf.group(2))
        except Exception:
            pass
    mself = SQRT_EXPR.fullmatch(s)
    if mself:
        coef = float(mself.group(1)) if mself.group(1) else 1.0
        try:
            return coef * (float(mself.group(2)) ** 0.5)
        except Exception:
            pass
    mrad = RADICAL.search(s)
    if mrad:
        coef = float(mrad.group(1)) if mrad.group(1) else 1.0
        rad = mrad.group(2) or mrad.group(3)
        try:
            return coef * (float(rad) ** 0.5)
        except Exception:
            pass
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)", s)
    if m:
        try:
            return Fraction(m.group(1)) / Fraction(m.group(2))
        except Exception:
            return None
    try:
        return Fraction(s) if "." not in s else float(s)
    except Exception:
        return None


def judge_math(response_text, expected, tolerance="exact"):
    """Return dict {correct, extracted, method, expected, reason}"""
    got, method = extract_final_answer(response_text)
    exp = str(expected).strip()
    result = {"correct": False, "extracted": got, "method": method,
              "expected": exp, "tolerance": tolerance, "reason": ""}
    if got is None:
        result["reason"] = "no answer extracted"
        return result
    if tolerance == "equivalent-fraction":
        a, b = _to_number(got), _to_number(exp)
        if a is None or b is None:
            result["reason"] = "non-numeric fraction compare"
            return result
        # Fraction vs float compare with small epsilon
        try:
            ok = abs(float(a) - float(b)) < 1e-6
        except Exception:
            ok = str(a) == str(b)
        result["correct"] = bool(ok)
        result["reason"] = "fraction equivalence"
        return result
    if tolerance in ("exact", "aime-exact", "exact-string"):
        a, b = _to_number(got), _to_number(exp)
        if a is not None and b is not None:
            try:
                result["correct"] = abs(float(a) - float(b)) < 1e-9
            except Exception:
                result["correct"] = str(a) == str(b)
            result["reason"] = "numeric exact"
        else:
            result["correct"] = got.strip().lower() == exp.lower()
            result["reason"] = "string exact"
        return result
    # numeric tolerance
    try:
        tol = float(tolerance)
    except Exception:
        tol = 0.01
    a, b = _to_number(got), _to_number(exp)
    if a is None or b is None:
        result["reason"] = "non-numeric tolerance compare"
        return result
    result["correct"] = abs(float(a) - float(b)) <= tol
    result["reason"] = "numeric tolerance %s" % tol
    return result

# ---------- generic MCQ judge ----------
# The implementation lives in checkers.py, which is what run.py actually calls
# (run.py L17: from checkers import run_checkers, judge_mcq). This name is kept
# as a thin alias so the two copies cannot drift apart again: a second private
# copy here is exactly how judge_mcq stayed hardcoded to [A-D] (and missed ARC's
# numeric labels) while the live one in checkers.py had already moved on.
from checkers import judge_mcq as _checkers_judge_mcq  # noqa: E402


def judge_mcq(response_text, answer_letter, options=None):
    """Deprecated alias for checkers.judge_mcq - the judge run.py actually uses.

    Kept for callers that still import judge.judge_mcq. options is accepted for
    signature compatibility and ignored: the live judge is already bounded to
    A-J plus the numeric labels 1-10. New code should import from checkers.
    """
    return _checkers_judge_mcq(response_text, answer_letter)
