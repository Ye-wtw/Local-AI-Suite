# -*- coding: utf-8 -*-
"""checkers.py - verifiable instruction-following checkers (D5) + MCQ judge helper (D2).
All rule-based: no LLM, no cost, deterministic.
Supported checker types:
  sentence_count, contains, not_contains, contains_any, last_sentence_ends_with,
  length_range, json_valid, string, int, float, array_len, field, style_keyword
"""
import json, re

# ---------- text helpers ----------
_CN_END = "。！？!?"
_SENT_SPLIT = re.compile(r"[。！？!?]+|(?<=[.])[ \t\n]+")


def split_sentences(text):
    """Split into sentences; tolerant of Chinese and English punctuation."""
    t = (text or "").strip()
    if not t:
        return []
    # protect decimals like 3.14 from being split
    parts = _SENT_SPLIT.split(t)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # drop list markers / leading bullets
        p = re.sub(r"^[-*\u2022\d]+[.)\u3001]?\s*", "", p).strip()
        if p:
            out.append(p)
    return out


def strip_code_fence(text):
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    return m.group(1) if m else t


def extract_json(text):
    """Best-effort JSON extraction (handles code fences and surrounding prose)."""
    t = strip_code_fence(text)
    try:
        return json.loads(t), None
    except Exception as e:
        err = str(e)
    # try first {...} block
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1]), None
        except Exception as e:
            err = str(e)
    return None, err


def _get_path(obj, path):
    cur = obj
    for seg in str(path).split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return None, False
    return cur, True


# ---------- individual checkers ----------
def check_one(chk, response):
    """Return (passed: bool, detail: str)."""
    t = chk.get("type")
    txt = response or ""

    if t == "sentence_count":
        n = len(split_sentences(txt))
        want = chk["value"]
        return n == want, "sentences=%d want=%d" % (n, want)

    if t == "contains":
        ok = str(chk["value"]).lower() in txt.lower()
        return ok, "contains %r" % chk["value"]

    if t == "not_contains":
        ok = str(chk["value"]).lower() not in txt.lower()
        return ok, "not_contains %r" % chk["value"]

    if t in ("contains_any", "style_keyword"):
        vals = chk.get("values") or []
        hit = [v for v in vals if str(v).lower() in txt.lower()]
        return bool(hit), "matched %s of %s" % (hit, vals)

    if t == "last_sentence_ends_with":
        want = chk["value"]
        sents = split_sentences(txt)
        ok = bool(sents) and sents[-1].rstrip().endswith(want)
        if not ok:
            ok = txt.strip().endswith(want)
        return ok, "last sentence ends with %r" % want

    if t == "length_range":
        n = len(txt.strip())
        lo, hi = chk.get("min", 0), chk.get("max", 10 ** 9)
        return lo <= n <= hi, "len=%d want %d..%d" % (n, lo, hi)

    if t == "json_valid":
        obj, err = extract_json(txt)
        return obj is not None, "json parse: %s" % ("ok" if obj is not None else err)

    # ---- JSON field checks (need parsed object) ----
    if t in ("string", "int", "float", "array_len", "field"):
        obj, err = extract_json(txt)
        if obj is None:
            return False, "no JSON: %s" % err
        name = chk.get("name") or chk.get("path")
        kind = chk.get("value_type") or (t if t != "field" else chk.get("type"))
        val, found = _get_path(obj, name)
        if not found:
            return False, "field %r missing" % name
        if t == "array_len" or kind == "array":
            if not isinstance(val, list):
                return False, "%r is not an array" % name
            want = chk["value"]
            return len(val) == want, "len(%s)=%d want=%d" % (name, len(val), want)
        if t == "string" or kind == "string":
            return isinstance(val, str), "%r is %s" % (name, type(val).__name__)
        if t == "int" or kind == "int":
            if isinstance(val, bool) or not isinstance(val, int):
                return False, "%r is %s (want int)" % (name, type(val).__name__)
            rng = chk.get("range")
            if rng:
                return rng[0] <= val <= rng[1], "%s=%s want in %s" % (name, val, rng)
            return True, "%s=%s" % (name, val)
        if t == "float" or kind == "float":
            ok = isinstance(val, (int, float)) and not isinstance(val, bool)
            return ok, "%r is %s" % (name, type(val).__name__)
        return True, "field %r present" % name

    return False, "unknown checker type %r" % t


def run_checkers(checkers, response):
    """Run all checkers. Returns (all_passed, results[])."""
    results = []
    for chk in checkers or []:
        ok, detail = check_one(chk, response)
        results.append({"type": chk.get("type"), "passed": ok, "detail": detail})
    return all(r["passed"] for r in results), results


# ---------- D2 MCQ ----------
# Option labels are A-J (MMLU-Pro has 10) or 1-10: a few ARC items label their
# options numerically and their answer_letter is "1".."4". The isolated-label
# fallback is deliberately narrow - it only looks at the tail of the response -
# because a \b([A-J])\b scan over the whole text matches any stray capital in
# the reasoning body and silently reports the wrong choice.
_MCQ_MAX_LETTERS = 10          # A..J
_MCQ_MAX_NUMERIC = 10          # 1..10
_MCQ_TAIL_WINDOW = 200         # isolated-label fallback scans only this far back
_MCQ_TRIM = "()[]（）【】.。、,，:：;；*`'\" \t\r\n"


def _norm_label(s):
    """Normalise one option label for comparison: '(c)' -> 'C', ' 2 ' -> '2'."""
    if s is None:
        return ""
    return str(s).strip().strip(_MCQ_TRIM).strip().upper()


def _label_regex(labels):
    """Non-capturing alternation over the labels, or None when there are none."""
    letters = sorted({x for x in labels if len(x) == 1 and x.isalpha()})
    nums = sorted({x for x in labels if x.isdigit()}, key=lambda s: (-len(s), s))
    alts = []
    if letters:
        contiguous = letters == [chr(ord(letters[0]) + i) for i in range(len(letters))]
        if contiguous and len(letters) > 1:
            alts.append("[%s-%s%s-%s]" % (letters[0], letters[-1],
                                          letters[0].lower(), letters[-1].lower()))
        else:
            alts.append("(?:%s)" % "|".join(letters + [x.lower() for x in letters]))
    if nums:
        alts.append("(?:%s)" % "|".join(nums))
    return "(?:%s)" % "|".join(alts) if alts else None


def judge_mcq(response_text, answer_letter):
    """Extract an option label (A-J or 1-10). Returns {correct, extracted, expected}.

    Priority: whole response is the label -> explicit "answer: X" marker ->
    line-leading marker -> a line holding only the label -> an isolated
    uppercase label inside the last 200 characters.
    """
    txt = (response_text or "").strip()
    exp = _norm_label(answer_letter)
    out = {"correct": False, "extracted": None, "expected": exp}
    if not txt:
        return out

    # An expected numeric label means this question is numbered, so widen the
    # accepted set to 1..10 even though the caller did not pass the options.
    letters = [chr(ord("A") + i) for i in range(_MCQ_MAX_LETTERS)]
    labels = letters + ([str(i) for i in range(1, _MCQ_MAX_NUMERIC + 1)]
                        if exp.isdigit() else [])
    lab = _label_regex(labels)
    if lab is None:
        return out

    whole = _norm_label(txt)
    if whole in labels:
        out["extracted"] = whole
        out["correct"] = whole == exp
        return out

    pats = [
        # 1) explicit marker: "答案是 C" / "answer: C" / "answer is (C)"
        (re.compile(r"(?:答案|answer)\s*(?:是|为|is|:)?\s*[:：]?\s*[\(\[（]?\s*(%s)\s*[\)\]）]?" % lab, re.I), txt),
        # 2) line-leading marker: "C. ..." / "(C) ..." / "C) ..."
        (re.compile(r"^\s*[\(\[（]?\s*(%s)\s*[\)\]）]?\s*[\.、）)\]:：\-]" % lab, re.M), txt),
        # 3) a line that is nothing but the label
        (re.compile(r"^\s*[\(\[（]?\s*(%s)\s*[\)\]）]?\s*[\.、]?\s*$" % lab, re.M), txt),
        # 4) isolated UPPERCASE label, only inside the tail of the response.
        #    Case-sensitive on purpose: a lowercase "a" in prose is the article,
        #    not a choice.
        (re.compile(r"\b([A-J])\b"), txt[-_MCQ_TAIL_WINDOW:]),
    ]
    for p, scope in pats:
        ms = p.findall(scope)
        if ms:
            out["extracted"] = _norm_label(ms[-1])
            break
    if out["extracted"]:
        out["correct"] = out["extracted"] == exp
    return out
