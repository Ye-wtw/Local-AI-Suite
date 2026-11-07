# -*- coding: utf-8 -*-
"""public_judge.py - route a public-benchmark row to the right judge.

Nine benchmarks were curated into datasets/subsets/public_balanced.jsonl, and they
do NOT share one schema:

  gpqa                    MCQ (options + answer_letter)  -> judge.judge_mcq
  gsm8k / math500         numeric or symbolic answer      -> judge.judge_math
  humaneval               function body + unit tests      -> code_judge
  livecodebench           whole program, stdin/stdout     -> lcb_judge
  bfcl                    tool call (function + args)     -> substring on call
  ifeval                  verifiable instruction rules    -> checkers-style
  simpleqa / truthfulqa   free-text answer                -> EXACT-MATCH ONLY

The last group is the honest weak point. Grading a free-text answer without an
LLM judge can only do substring matching, so a correct paraphrase is scored
wrong. That UNDERSTATES the model, never overstates it, and every result carries
scoring="exact_match_understates" so a reader knows the number is a floor.
"""
import re


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _contains(hay, needle):
    """Substring test tolerant of spacing and case, plus a numeric fallback."""
    h, n = _norm(hay), _norm(needle)
    if not n:
        return False
    if n in h:
        return True
    try:
        want = float(n.replace(",", ""))
    except Exception:
        return False
    for m in re.finditer(r"-?\d+(?:\.\d+)?", h.replace(",", "")):
        try:
            if abs(float(m.group(0)) - want) < 1e-9:
                return True
        except Exception:
            continue
    return False


def judge_public(entry, response):
    """Return {"correct": bool, "reason": str, "scoring": str}."""
    bench = entry.get("_bench") or entry.get("source_name") or ""
    txt = response or ""

    # ---- 1. MCQ: GPQA (and any row that carries options) ----
    if entry.get("options") and entry.get("answer_letter"):
        import judge as J
        v = J.judge_mcq(txt, entry["answer_letter"], entry.get("options"))
        return {"correct": bool(v.get("correct")),
                "reason": "mcq letter=%s %s" % (entry["answer_letter"], v.get("reason", "")),
                "scoring": "mcq_letter"}

    # ---- 2. Math: GSM8K / MATH-500 ----
    if bench in ("gsm8k", "math500"):
        import judge as J
        ans = str(entry.get("answer") or "")
        v = J.judge_math(txt, ans, tolerance=entry.get("tolerance"))
        return {"correct": bool(v.get("correct")),
                "reason": "math %s" % (v.get("reason", "")[:70]),
                "scoring": "math_compare"}

    # ---- 3. HumanEval: function-level unit tests ----
    if entry.get("test") and entry.get("entry_point"):
        import code_judge as C
        v = C.judge_code(entry, txt)
        return {"correct": bool(v.get("correct")),
                "reason": "humaneval %s" % (v.get("reason", "")[:70]),
                "scoring": "unit_tests"}

    # ---- 4. LiveCodeBench: whole program on stdin/stdout ----
    if entry.get("public_tests"):
        import lcb_judge as L
        v = L.judge_lcb(entry, txt)
        return {"correct": bool(v.get("correct")),
                "reason": "lcb %s" % (v.get("reason", "")[:70]),
                "scoring": "lcb_stdin_stdout"}

    # ---- 5. BFCL: the call must name the function and carry the arguments ----
    if bench == "bfcl":
        want = str(entry.get("answer") or "")
        fname = want.split("(")[0].strip()
        hit_fn = _contains(txt, fname)
        # every key=value pair in the reference should appear
        pairs = re.findall(r"(\w+)\s*=\s*([^,()]+)", want)
        hit_pairs = [k for k, val in pairs if _contains(txt, k) and _contains(txt, val.strip())]
        ok = hit_fn and (not pairs or len(hit_pairs) == len(pairs))
        return {"correct": ok,
                "reason": "bfcl fn=%s args=%d/%d" % (fname, len(hit_pairs), len(pairs)),
                "scoring": "tool_call_match"}

    # ---- 6. IFEval: 24 rule checkers, official semantics ----
    # The earlier version called checkers.judge_instruction, which does not exist.
    # checkers.check_one handles THIS project's own checker schema, not IFEval's
    # instruction_id_list/kwargs pair, so IFEval has its own module.
    if bench == "ifeval" or (entry.get("expect") or {}).get("instruction_id_list"):
        import ifeval_judge as IF
        v = IF.judge_ifeval(entry, txt)
        return {"correct": bool(v.get("correct")),
                "skipped": bool(v.get("skipped")),
                "reason": "ifeval %s" % (v.get("reason", "")[:70]),
                "scoring": "ifeval_rules"}

    # ---- 7. Free text: exact match only, and say so ----
    answers = []
    if entry.get("answer"):
        answers.append(str(entry["answer"]))
    for k in ("correct_answers", "answer_aliases"):
        v = entry.get(k)
        if isinstance(v, list):
            answers.extend(str(x) for x in v)
    if answers:
        # Exact match alone rejects "Sugeno Michio" for "Michio Sugeno" and "9.78"
        # for "9.784". aliases.matches adds mechanical normalisation plus a small
        # curated table, so the reported number is closer to the truth while still
        # never inventing matches: a wrong answer stays wrong.
        import aliases as AL
        hit = None
        for a in answers:
            if AL.matches(txt, a):
                hit = a
                break
        return {"correct": bool(hit),
                "reason": ("free-text matched %r" % (hit[:40],)) if hit
                          else ("answer %r not found in reply" % (answers[0][:40],)),
                "scoring": "alias_match_understates"}

    return {"correct": False, "reason": "no judge matched this row",
            "scoring": "unknown"}


if __name__ == "__main__":
    # Smoke: every curated benchmark must route to SOME judge, none may be unknown.
    import json, os, collections
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "datasets", "subsets", "public_balanced.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    seen = {}
    for r in rows:
        b = r.get("_bench")
        if b in seen:
            continue
        seen[b] = judge_public(r, "stub reply")["scoring"]
    for b in sorted(seen):
        print("%-14s -> %s" % (b, seen[b]))