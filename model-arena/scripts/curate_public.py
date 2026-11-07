# -*- coding: utf-8 -*-
"""curate_public.py - build a balanced public-benchmark set from what is on disk.

Why this exists: the 12 downloaded benchmarks total 21,629 questions, and taking
them all would make knowledge (D2) 58% of the bank while D7/D9/D10 get nothing -
public sets simply do not cover long context, real-world tasks or board games.
A bank whose shape is decided by what happened to be downloadable measures the
benchmark list, not the model.

So: one or two authoritative sets per dimension, capped, with provenance kept.

Quotas below are chosen for a 2-3 hour local run at ~50 tok/s:
  D2 knowledge  GPQA-diamond 198 (graduate level, harder to memorise than MMLU)
  D3 math       MATH-500 500 + GSM8K 300 (symbolic + word problems)
  D4 code       LCB v6 175 (already wired) + HumanEval 164
  D5 following  IFEval 541 (the only verifiable instruction set here)
  D6 tools      BFCL 100 (the only tool-calling set here)
  D8 honesty    SimpleQA 300 (sampled) + TruthfulQA 300 (sampled)
  D7/D9/D10     none - public sets do not cover them; the self-built bank does

Usage:
  python scripts/curate_public.py --dry-run
  python scripts/curate_public.py --write
"""
import argparse
import glob
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "datasets", "public")
OUT = os.path.join(ROOT, "datasets", "subsets", "public_balanced.jsonl")
NL = chr(10)

# name -> how many to keep (None = everything)
# Balance target: no dimension above ~25% of the public block. MATH-500 at full
# size plus GSM8K-300 made D3 31% of everything, while D2 knowledge (the dimension
# a user most wants to compare) sat at 8%. Trim math, keep knowledge as is - GPQA
# has only 198 questions and they are the hardest available.
QUOTA = {
    "gpqa": 198,          # D2: graduate-level, all of it
    "math500": 300,       # D3: sample, 500 total
    "gsm8k": 200,         # D3: sample, 1319 total
    "livecodebench": 175, # D4: all (v6)
    "humaneval": 164,     # D4: all
    "ifeval": 400,        # D5: sample, 541 total
    "bfcl": 100,          # D6: all
    "simpleqa": 250,      # D8: sample, 4326 total
    "truthfulqa": 250,    # D8: sample, 817 total
}

# Explicitly excluded, with the reason, so a reader does not think it was forgotten.
EXCLUDED = {
    "mmlu": "285 questions, fully overlapped by GPQA + self-built D2; contamination high",
    "mmlu_pro": "12032 questions would make D2 58% of the bank; superseded by GPQA here",
    "arc": "1172 elementary science, overlapped by GPQA; contamination high",
}


def load(name, n, rng):
    path = os.path.join(SRC, name + ".jsonl")
    if not os.path.exists(path):
        return None, 0, "file missing"
    rows = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            rows.append(json.loads(line))
    total = len(rows)
    if n is None or n >= total:
        return rows, total, None
    # stratified when the set carries a subject/topic/category field, so a sample
    # is not all one subject; otherwise plain shuffle.
    key = next((k for k in ("subject", "topic", "category", "domain", "subcat")
                if rows and rows[0].get(k)), None)
    if key:
        buckets = {}
        for r in rows:
            buckets.setdefault(str(r.get(key)), []).append(r)
        for b in buckets.values():
            rng.shuffle(b)
        picked, names = [], sorted(buckets)
        i = 0
        while len(picked) < n and any(buckets[x] for x in names):
            nm = names[i % len(names)]
            if buckets[nm]:
                picked.append(buckets[nm].pop())
            i += 1
        return picked, total, key
    rng.shuffle(rows)
    return rows[:n], total, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--seed", type=int, default=20261101)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    out, report = [], []
    for name, n in sorted(QUOTA.items()):
        rows, total, strat = load(name, n, rng)
        if rows is None:
            report.append((name, 0, total, "MISSING"))
            continue
        for r in rows:
            rr = dict(r)
            rr["_bench"] = name
            out.append(rr)
        report.append((name, len(rows), total, ("strat:" + strat) if strat else "all/shuffle"))

    print("=" * 74)
    print("public benchmark curation")
    print("=" * 74)
    print("%-16s %8s %8s  %s" % ("benchmark", "kept", "total", "sampling"))
    for name, kept, total, how in report:
        print("%-16s %8d %8d  %s" % (name, kept, total, how))
    print("%-16s %8d" % ("TOTAL", len(out)))
    print()
    print("excluded on purpose:")
    for k, why in EXCLUDED.items():
        print("  %-12s %s" % (k, why))
    print()

    by_dim = {}
    for r in out:
        d = r.get("dim") or "?"
        by_dim[d] = by_dim.get(d, 0) + 1
    print("resulting shape (public only):")
    for d in sorted(by_dim):
        print("  %-5s %5d  %4.1f%%" % (d, by_dim[d], 100.0 * by_dim[d] / len(out) if out else 0))

    if a.dry_run or not a.write:
        print()
        print("(dry run; add --write to save)")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + NL)
    print()
    print("wrote %d rows -> %s" % (len(out), OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())