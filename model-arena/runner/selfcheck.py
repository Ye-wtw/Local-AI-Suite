# -*- coding: utf-8 -*-
"""selfcheck.py - static integrity check of the question bank. No model calls.

Why this exists: a full run is the most expensive way to discover a broken
question. Everything below is checkable in seconds, offline.

Three false-positive rounds taught the hard part: EVERY DIMENSION HAS ITS OWN
SCHEMA. D4 stores the prompt in "prompt", D7/D10 in "question", D8/D9 in "user",
D6 in "dialogue", D4 SQL carries its reference answer in "check" not "test".
Checking a single field name reported 1260 healthy questions as empty.

Usage:
  python runner/selfcheck.py            # report
  python runner/selfcheck.py --strict   # exit 1 on any HIGH finding
"""
import argparse
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Prompt field candidates, per dimension, in priority order.
PROMPT_FIELD = {
    "D2": ["question", "user"], "D3": ["question", "user"],
    "D4": ["prompt", "task", "question", "user"],
    "D5": ["user", "prompt"], "D6": ["dialogue", "user"],
    "D7": ["question", "user"], "D8": ["user", "question"],
    "D9": ["user", "question"], "D10": ["question", "user"],
}
# Judging payload candidates, per dimension.
CHECK_FIELD = {
    "D2": ["answer_letter", "answer"], "D3": ["answer"],
    "D4": ["test", "check", "buggy"], "D5": ["expect", "checks"],
    "D6": ["expect"], "D7": ["expect", "checks"],
    "D8": ["expect", "checks"], "D9": ["checks", "expect", "exec"],
    "D10": ["expect", "answer"],
}


def _nonempty(v):
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return bool(v)
    return True


def get_any(row, fields):
    for f in fields:
        if _nonempty(row.get(f)):
            return row[f]
    return None


def check_bank(full_dir):
    """Return a list of (severity, where, message)."""
    issues = []

    def add(sev, where, what):
        issues.append((sev, where, what))

    for path in sorted(glob.glob(os.path.join(full_dir, "*.jsonl"))):
        dim = os.path.basename(path).replace("_full.jsonl", "")
        pf = PROMPT_FIELD.get(dim, ["user", "question", "prompt"])
        cf = CHECK_FIELD.get(dim, ["expect", "checks"])
        rows = []
        for ln, line in enumerate(open(path, encoding="utf-8"), 1):
            if not line.strip():
                continue
            try:
                rows.append((ln, json.loads(line)))
            except Exception as e:
                add("HIGH", "%s:%d" % (dim, ln), "unparseable JSON: %s" % e)

        seen = collections.Counter(r.get("id") for _, r in rows)
        for qid, n in seen.items():
            if n > 1:
                add("HIGH", dim, "duplicate id %s x%d" % (qid, n))

        for _, r in rows:
            qid = r.get("id", "?")
            prompt = get_any(r, pf)
            if prompt is None:
                add("HIGH", qid, "empty prompt (looked for %s)" % "/".join(pf))
            if get_any(r, cf) is None:
                add("HIGH", qid, "no judging payload (looked for %s)" % "/".join(cf))

            # injection items must carry the external body they claim to summarise
            if r.get("subcat") == "injection":
                txt = str(prompt or "")
                if len(txt) < 60:
                    add("HIGH", qid, "injection prompt only %d chars - body likely missing" % len(txt))

            # hallucination items must distinguish "answerable" from "unverifiable"
            if r.get("subcat") == "hallucination":
                exp = r.get("expect") or {}
                if not any(exp.get(k) for k in ("correct_any", "should_admit_unknown", "should_cite")):
                    add("MED", qid, "no correct_any / should_admit_unknown / should_cite")

            for c in (r.get("checks") or []):
                t = c.get("type")
                if t in ("any_of", "all_of", "min_count") and not c.get("values"):
                    add("HIGH", qid, "%s has no values" % t)
                if t == "no_invention" and not c.get("pattern"):
                    add("HIGH", qid, "no_invention has no pattern")
                if t == "cite_from" and not c.get("allowed"):
                    add("HIGH", qid, "cite_from has no allowed list")

            ex = r.get("exec") or {}
            if ex:
                run = str(ex.get("run") or "")
                if run and not any(k in run for k in ("python", "node", "pwsh", "cmd", "bash", ".exe", "./")):
                    add("HIGH", qid, "exec.run lacks an interpreter: %r" % run[:40])
                if not ex.get("expect_stdout") and ex.get("expect_exit") is None:
                    add("MED", qid, "exec has neither expect_stdout nor expect_exit")
    return issues


def check_subsets(root):
    """A level file must cover exactly what the full bank holds."""
    issues = []
    full_ids = set()
    for path in glob.glob(os.path.join(root, "datasets", "full", "*.jsonl")):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                full_ids.add(json.loads(line)["id"])
    lvl = os.path.join(root, "datasets", "subsets", "full.jsonl")
    if not os.path.exists(lvl):
        return [("HIGH", "subsets/full", "level file missing - run make_subset.py --all")]
    sub_ids = set()
    for line in open(lvl, encoding="utf-8"):
        if line.strip():
            sub_ids.add(json.loads(line)["id"])
    missing = full_ids - sub_ids
    if missing:
        issues.append(("HIGH", "subsets/full",
                       "covers %d of %d bank questions (%d missing) - rebuild with make_subset.py --all"
                       % (len(sub_ids), len(full_ids), len(missing))))
    stale = sub_ids - full_ids
    if stale:
        issues.append(("MED", "subsets/full", "%d ids no longer in the bank" % len(stale)))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 when any HIGH finding exists")
    args = ap.parse_args()

    issues = check_bank(os.path.join(ROOT, "datasets", "full"))
    issues += check_subsets(ROOT)

    sev = collections.Counter(s for s, _, _ in issues)
    print("=" * 72)
    print("question bank self-check")
    print("=" * 72)
    print("findings: %d  HIGH=%d  MED=%d" % (len(issues), sev.get("HIGH", 0), sev.get("MED", 0)))
    for s in ("HIGH", "MED"):
        sub = [i for i in issues if i[0] == s]
        if not sub:
            continue
        print()
        print("--- %s (%d) ---" % (s, len(sub)))
        for _, where, what in sub[:20]:
            print("   %-26s %s" % (where, what[:80]))
    if args.strict and sev.get("HIGH"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())