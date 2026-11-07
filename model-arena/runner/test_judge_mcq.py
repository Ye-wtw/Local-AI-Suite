# -*- coding: utf-8 -*-
"""test_judge_mcq.py - self-check for the D2 MCQ judge (runner/checkers.judge_mcq).

Run: python runner/test_judge_mcq.py

Covers the three shapes the public benchmarks actually contain:
  * 4 options A-D        (gpqa 198, mmlu 285)      - behaviour must not change
  * 10 options A-J       (mmlu_pro 12032)          - 55% of answers are E-J
  * numeric labels 1-4   (arc, 22 items)           - answer_letter is "1".."4"
plus a false-capture guard, so a letter inside the reasoning body cannot decide
the verdict.

Read-only against datasets/: it only reads public jsonl and never writes there.
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import checkers  # noqa: E402
import judge  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "datasets", "public")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))


def load(name):
    path = os.path.join(DATA, name + ".jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ask(letter):
    """The response a well-behaved model would give."""
    return "答案是 %s" % letter


def other_label(letter, rows_letters):
    """A deliberately wrong label from the same question's own option set."""
    pool = [x for x in rows_letters if x != letter]
    return pool[0] if pool else ("B" if str(letter).upper() != "B" else "C")


def run_baseline(name, rows, label_of, sample=None, tag=""):
    """Every row answered correctly must judge True."""
    if sample is not None and len(rows) > sample:
        rows = random.Random(20260101).sample(rows, sample)
    bad = []
    for r in rows:
        exp = label_of(r)
        v = checkers.judge_mcq(ask(exp), exp)
        if not v["correct"]:
            bad.append((r.get("id"), exp, v.get("extracted")))
    check("%s%s correct-answer recall %d/%d" % (name, tag, len(rows) - len(bad), len(rows)),
          not bad, "" if not bad else "first failures: %r" % (bad[:3],))
    return rows


def run_counterexamples(name, rows, label_of, n=20):
    """Swapping the correct answer for another one must judge False."""
    rng = random.Random(424242)
    picked = rng.sample(rows, min(n, len(rows)))
    bad = []
    for r in picked:
        exp = label_of(r)
        opts = r.get("options") or {}
        pool = [k for k in opts.keys() if str(k).upper() != str(exp).upper()]
        if not pool:
            pool = [c for c in "ABCDEFGHIJ" if c != str(exp).upper()]
        wrong = rng.choice(pool)
        v = checkers.judge_mcq(ask(wrong), exp)
        if v["correct"]:
            bad.append((r.get("id"), exp, wrong, v.get("extracted")))
    check("%s counterexamples %d (wrong label -> False)" % (name, len(picked)),
          not bad, "" if not bad else "wrongly accepted: %r" % (bad[:3],))


def run_false_capture_guard():
    """Letters in the reasoning body must not override an explicit conclusion."""
    print("\n-- false-capture guard --")
    cases = [
        # (response, expected, should_be_correct, why)
        ("The options A and B are tempting, but C follows from the data.\n答案: C", "C", True,
         "A/B in prose, explicit 答案: C wins"),
        ("First I rule out A. Then B. Then D.\n答案是 C", "C", True,
         "eliminated letters, explicit answer wins"),
        ("A, B and D are all wrong.\nanswer: C", "C", True,
         "English marker, eliminated letters in prose"),
        ("In case A the reaction proceeds, but the answer is C.", "C", True,
         "explicit marker inside a sentence"),
        ("So the final line is\nC", "C", True,
         "label alone on its own line"),
        ("I considered A and B.\n\nC", "C", True,
         "label on its own line after prose"),
        # The decisive guard: no explicit marker and the only letter sits in the
        # BODY, further than 200 characters from the end. The old
        # \b([A-D])\b-over-the-whole-text scan captured it; the tail window must not.
        (("Option A is clearly wrong because the mass is conserved. " * 6).strip()
         + " " + ("The reasoning continues here without any conclusion. " * 3),
         "A", False,
         "no marker, body letter >200 chars from the end is not captured"),
        # Parity case: a letter INSIDE the 200-char tail is still honoured, which
        # is what keeps short "The answer is A." style responses working.
        ("Option A is clearly wrong because the mass is conserved.", "A", True,
         "letter inside the 200-char tail is still honoured (unchanged)"),
    ]
    for txt, exp, want, why in cases:
        v = checkers.judge_mcq(txt, exp)
        check("guard: %s" % why, v["correct"] == want,
              "expected correct=%s got correct=%s extracted=%r" % (want, v["correct"], v.get("extracted")))

    # a model that states the right answer but the body mentions the wrong one
    v = checkers.judge_mcq("B looks plausible at first, but it fails.\n\n答案是 J", "J")
    check("guard: long reasoning with many letters, explicit J wins", v["correct"] is True,
          "extracted=%r" % (v.get("extracted"),))

    # the tail window is what bounds the fallback: same letter, different distance.
    # 'A' appears exactly once, right at the start, then >200 chars of filler.
    long_body = "Option A is wrong. " + ("filler text with no letters. " * 10)
    dist = len(long_body) - long_body.rindex("A")
    v_far = checkers.judge_mcq(long_body, "A")
    check("guard: tail window is 200 chars (A at %d from end -> not captured)" % dist,
          dist > 200 and v_far["extracted"] is None,
          "extracted=%r" % (v_far.get("extracted"),))
    near = ("The text mentions A here. " * 3).strip()
    v_near = checkers.judge_mcq(near, "A")
    check("guard: letter inside the window is captured (A at %d from end)" % (len(near) - near.rindex("A")),
          v_near["extracted"] == "A", "extracted=%r" % (v_near.get("extracted"),))


def run_numeric():
    print("\n-- numeric labels (arc) --")
    rows = load("arc")
    numeric = [r for r in rows if str(r.get("answer_letter", "")).isdigit()]
    print("  arc numeric-label items: %d" % len(numeric))
    bad = []
    for r in numeric:
        exp = str(r["answer_letter"])
        v = checkers.judge_mcq(ask(exp), exp)
        if not v["correct"]:
            bad.append((r.get("id"), exp, v.get("extracted")))
    check("arc numeric correct-answer recall %d/%d" % (len(numeric) - len(bad), len(numeric)),
          not bad, "" if not bad else "first failures: %r" % (bad[:3],))

    # a numeric question must not be confused by an adjacent number in the prose
    v = checkers.judge_mcq("Choice 2 has 3 sub-parts.\n答案是 3", "3")
    check("numeric: '3' in prose does not beat explicit '答案是 3'", v["correct"] is True,
          "extracted=%r" % (v.get("extracted"),))
    return numeric


def run_alias():
    print("\n-- judge.judge_mcq alias --")
    v = judge.judge_mcq("答案是 C", "C", {"A": "x", "B": "y", "C": "z", "D": "w"})
    check("judge.judge_mcq still importable and correct", v["correct"] is True,
          "got %r" % (v,))
    v2 = judge.judge_mcq("答案是 J", "J")
    check("judge.judge_mcq delegates to checkers (J accepted)", v2["correct"] is True,
          "got %r" % (v2,))


def run_shapes():
    print("\n-- return shape --")
    v = checkers.judge_mcq("答案是 C", "C")
    check("keys are exactly {correct, extracted, expected}",
          set(v.keys()) == {"correct", "extracted", "expected"}, "got %r" % (sorted(v.keys()),))
    check("correct is a bool", isinstance(v["correct"], bool), "got %r" % (type(v["correct"]).__name__,))
    v = checkers.judge_mcq("", "C")
    check("empty response -> correct False, extracted None",
          v["correct"] is False and v["extracted"] is None, "got %r" % (v,))
    check("signature is 2 positional args",
          checkers.judge_mcq.__code__.co_argcount == 2,
          "argcount=%d" % checkers.judge_mcq.__code__.co_argcount)


def main():
    print("=" * 74)
    print("judge_mcq self-check")
    print("=" * 74)

    gpqa = load("gpqa")
    mmlu_pro = load("mmlu_pro")
    arc = load("arc")

    print("\n-- 4 options A-D: gpqa (%d) --" % len(gpqa))
    run_baseline("gpqa", gpqa, lambda r: str(r["answer_letter"]))
    run_counterexamples("gpqa", gpqa, lambda r: str(r["answer_letter"]))

    print("\n-- 4 options A-D: mmlu (%d) --" % len(load("mmlu")))
    mmlu = load("mmlu")
    run_baseline("mmlu", mmlu, lambda r: str(r["answer_letter"]))
    run_counterexamples("mmlu", mmlu, lambda r: str(r["answer_letter"]))

    print("\n-- 10 options A-J: mmlu_pro (%d) --" % len(mmlu_pro))
    e_j = [r for r in mmlu_pro if str(r["answer_letter"]).upper() in "EFGHIJ"]
    print("  answers in E-J: %d (%.1f%%)" % (len(e_j), 100.0 * len(e_j) / len(mmlu_pro)))
    run_baseline("mmlu_pro", mmlu_pro, lambda r: str(r["answer_letter"]))
    print("  -- E-J subset explicitly --")
    run_baseline("mmlu_pro", e_j, lambda r: str(r["answer_letter"]), tag=" E-J")
    run_counterexamples("mmlu_pro", mmlu_pro, lambda r: str(r["answer_letter"]))

    print("\n-- arc letters (%d) --" % len([r for r in arc if str(r["answer_letter"]).isalpha()]))
    arc_alpha = [r for r in arc if str(r["answer_letter"]).isalpha()]
    run_baseline("arc", arc_alpha, lambda r: str(r["answer_letter"]))
    run_counterexamples("arc", arc_alpha, lambda r: str(r["answer_letter"]))

    run_numeric()
    run_counterexamples("arc-numeric", [r for r in arc if str(r["answer_letter"]).isdigit()],
                        lambda r: str(r["answer_letter"]), n=20)
    run_false_capture_guard()
    run_alias()
    run_shapes()

    print("\n" + "=" * 74)
    print("PASS: %d   FAIL: %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: %s" % f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
