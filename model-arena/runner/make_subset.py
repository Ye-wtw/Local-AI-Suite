# -*- coding: utf-8 -*-
"""make_subset.py - build stratified, reproducible smoke / canary subsets.
Reads the per-dimension full datasets, samples by subcategory, and writes a single
JSONL carrying a 'dim' tag so one runner pass can dispatch to the right judge.
Usage: python runner/make_subset.py [--level smoke|canary] [--all]
"""
import json, os, random, argparse, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Every level reads datasets/full. Two dimensions used to point at
# datasets/generated, which is a stale 30-question slice of D7 (the real bank has
# 120) and an old D10 copy - so the "full" level silently ran 559 of 2017
# questions. Level building must never read a second source of truth.
SOURCES = {
    "D2": os.path.join(ROOT, "datasets", "full", "D2_full.jsonl"),
    "D3": os.path.join(ROOT, "datasets", "full", "D3_full.jsonl"),
    "D5": os.path.join(ROOT, "datasets", "full", "D5_full.jsonl"),
    "D10": os.path.join(ROOT, "datasets", "full", "D10_full.jsonl"),
    "D7": os.path.join(ROOT, "datasets", "full", "D7_full.jsonl"),
    "D4": os.path.join(ROOT, "datasets", "full", "D4_full.jsonl"),
    "D6": os.path.join(ROOT, "datasets", "full", "D6_full.jsonl"),
    "D8": os.path.join(ROOT, "datasets", "full", "D8_full.jsonl"),
    "D9": os.path.join(ROOT, "datasets", "full", "D9_full.jsonl"),
}


def load(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def stratified_sample(rows, k, rng, key="subcat"):
    """Sample k rows, spread across subcategories as evenly as possible."""
    if k == 0:
        return []
    if k < 0 or k >= len(rows):
        return rows
    buckets = {}
    for r in rows:
        buckets.setdefault(r.get(key) or "unknown", []).append(r)
    for b in buckets.values():
        rng.shuffle(b)
    names = sorted(buckets)
    out = []
    i = 0
    while len(out) < k:
        name = names[i % len(names)]
        if buckets[name]:
            out.append(buckets[name].pop())
        elif all(not buckets[n] for n in names):
            break
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="", help="smoke | canary | full")
    ap.add_argument("--all", action="store_true", help="build all levels")
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(ROOT, "config", "test_levels.json"), encoding="utf-8"))
    levels = cfg["levels"]
    seed = cfg.get("sampling", {}).get("seed", 42)
    out_dir = os.path.join(ROOT, "datasets", "subsets")
    os.makedirs(out_dir, exist_ok=True)

    targets = list(levels) if args.all else ([args.level] if args.level else ["smoke", "canary"])
    cache = {}
    for lvl in targets:
        if lvl not in levels:
            print("unknown level:", lvl)
            continue
        spec = levels[lvl]
        per = spec.get("per_dim", {})
        rng = random.Random(seed + sum(ord(c) for c in lvl))
        rows_out = []
        for dim, src in SOURCES.items():
            if dim not in per:
                continue
            if dim not in cache:
                cache[dim] = load(src)
            k = per[dim]
            picked = stratified_sample(cache[dim], k, rng)
            for r in picked:
                rr = dict(r)
                rr["_level"] = lvl
                rows_out.append(rr)
        path = os.path.join(out_dir, "%s.jsonl" % lvl)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows_out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        from collections import Counter
        c = Counter(r["dim"] for r in rows_out)
        extra = ""
        if spec.get("max_ctx"):
            extra = "  (max_ctx=%d)" % spec["max_ctx"]
        print("%-8s -> %-34s %3d 题  %s%s" % (lvl, os.path.relpath(path, ROOT), len(rows_out), dict(sorted(c.items())), extra))


if __name__ == "__main__":
    main()
