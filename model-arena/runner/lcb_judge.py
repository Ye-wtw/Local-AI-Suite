# -*- coding: utf-8 -*-
"""lcb_judge.py - judge LiveCodeBench-style competitive programming problems.

These are NOT HumanEval. The difference decides how a submission must be tested:

  HumanEval : model fills in a function body; the harness imports it and calls
              assertions. Same process, function-level.
  LCB       : model writes a WHOLE program that reads stdin and writes stdout.
              It must be launched as a process, once per test case, and its
              stdout compared to the expected output.

Feeding a stdin/stdout problem to the HumanEval judge would import a script with
no callable function and fail every submission, so this module is separate.

Test protocol (what the public LiveCodeBench harness does):
  * every public test runs in a FRESH process with the case input on stdin
  * output is compared after stripping trailing whitespace per line
  * ALL public tests must pass
  * a non-zero exit, a timeout, or empty output is a failure

Scoring is pass@1 on public tests only. Hidden tests are not shipped with the
dataset, so a pass here means "correct on the published samples", not "accepted
by the original judge". That distinction is recorded in every result.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

NL = chr(10)
BT3 = chr(96) * 3

# A fence is three backticks, an optional language tag, then the body.
FENCE_PY = re.compile(BT3 + r"(?:python|py)?\s*\n(.*?)" + BT3, re.S | re.I)
FENCE_ANY = re.compile(BT3 + r"[a-zA-Z0-9_+-]*\s*\n(.*?)" + BT3, re.S)


def extract_program(text):
    """Pull the model program out of its reply.

    Prefer a python-tagged fence, then any fence. If neither exists, take the
    whole reply: models sometimes emit bare code for a "write a program" prompt.
    """
    t = text or ""
    m = FENCE_PY.search(t)
    if m:
        return m.group(1).strip()
    m = FENCE_ANY.search(t)
    if m:
        return m.group(1).strip()
    return t.strip()


def _norm(s):
    """Compare outputs ignoring trailing spaces and final newlines only.

    LiveCodeBench is strict about content but tolerant of trailing whitespace.
    Loosening further (collapsing all whitespace) would let a wrong but
    similar-looking answer pass.
    """
    if s is None:
        return ""
    lines = [ln.rstrip() for ln in s.replace(chr(13) + chr(10), chr(10)).split(chr(10))]
    while lines and lines[-1] == "":
        lines.pop()
    return NL.join(lines)


# A reply with no code-shaped token is prose, not code. Running prose produces a
# SyntaxError, which reads as "the model wrote broken code" when the truth is
# "the model never wrote code at all". That difference matters when triaging a
# low score: one is a coding failure, the other is a refusal/format failure.
CODE_HINTS = ("import ", "def ", "print(", "input(", "for ", "while ", "if ",
              " = ", "sys.stdin", "sys.stdout", "return ", "[", "{")


def looks_like_code(code):
    c = code or ""
    if len(c.strip()) < 8:
        return False
    return any(h in c for h in CODE_HINTS)


def run_program(code, tests, timeout_s=10, work_root=None):
    """Run one program against every public test. Returns a verdict dict."""
    if not code or not code.strip():
        return {"correct": False, "reason": "no code extracted",
                "passed": 0, "total": len(tests), "kind": "empty"}
    if not looks_like_code(code):
        return {"correct": False, "passed": 0, "total": len(tests),
                "kind": "no_code",
                "reason": "reply contains no code (%r)" % (code[:60],)}
    if not tests:
        return {"correct": False, "reason": "no public tests shipped",
                "passed": 0, "total": 0}

    root = work_root or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "results", ".lcbwork")
    os.makedirs(root, exist_ok=True)
    # Use makedirs + a counter, NOT mkdtemp. mkdtemp creates the directory 0o700,
    # and on Windows this sandbox the child process cannot even create its own
    # script inside it: PermissionError on open(). sandbox.py hit the same wall
    # earlier and the whole run died. makedirs inherits the parent ACL instead.
    work = None
    for n in range(1000):
        cand = os.path.join(root, "run_%d_%d" % (os.getpid(), n))
        try:
            os.makedirs(cand, exist_ok=False)
            work = cand
            break
        except FileExistsError:
            continue
    if work is None:
        return {"correct": False, "reason": "cannot create work dir",
                "passed": 0, "total": len(tests)}
    path = os.path.join(work, "main.py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)

        passed = 0
        failures = []
        for i, case in enumerate(tests, 1):
            inp = case.get("input") or ""
            want = _norm(case.get("output"))
            try:
                proc = subprocess.run(
                    [sys.executable, "main.py"],
                    input=inp, cwd=work, capture_output=True, text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                failures.append("case %d: timeout %ds" % (i, timeout_s))
                continue
            except Exception as e:
                failures.append("case %d: %s" % (i, str(e)[:60]))
                continue

            got = _norm(proc.stdout)
            if proc.returncode != 0:
                err = (proc.stderr or "").strip().split(NL)[-1][:80]
                failures.append("case %d: exit=%d %s" % (i, proc.returncode, err))
            elif got != want:
                failures.append("case %d: expected %r got %r"
                                % (i, want[:40], got[:40]))
            else:
                passed += 1

        ok = passed == len(tests)
        return {
            "correct": ok,
            "passed": passed,
            "total": len(tests),
            "failures": failures[:3],
            "reason": ("%d/%d public tests passed" % (passed, len(tests)))
                      + ("" if ok else "; " + "; ".join(failures[:2])),
            "scored_by": "lcb_stdin_stdout",
            "kind": "ran",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def judge_lcb(entry, response, timeout_s=10):
    """Judge one LCB row given the model raw reply."""
    code = extract_program(response)
    tests = entry.get("public_tests") or []
    v = run_program(code, tests, timeout_s=timeout_s)
    v["extracted_chars"] = len(code)
    return v


if __name__ == "__main__":
    demo = [{"input": "1", "output": "2"}, {"input": "5", "output": "6"}]
    good = "import sys" + NL + "print(int(sys.stdin.read().strip()) + 1)"
    bad = "print(0)"
    print("good:", judge_lcb({"public_tests": demo}, BT3 + "python" + NL + good + NL + BT3))
    print("bad :", judge_lcb({"public_tests": demo}, BT3 + "python" + NL + bad + NL + BT3))
