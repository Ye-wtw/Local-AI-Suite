# -*- coding: utf-8 -*-
"""make_coarse.py - build the 20-question coarse screen for parameter sweeps.

Why not just reuse the smoke subset: the coarse screen has ONE job - tell you
whether a flag changed anything - so every question must be one whose answer is
sensitive to the kind of change the sweep makes. Twenty insensitive questions
produce twenty identical scores and a false "no effect" verdict.

Sensitivity mapping (why each block is here):

  math (8)        numeric precision. KV cache quantisation and Flash Attention
                  perturb the attention path first where an exact value matters:
                  a small numeric drift turns 24.74 into 24.71 and the answer flips.
  long-ctx (4)    RoPE scaling and context length. These only move an answer when
                  the question needs information from far away.
  instruction (4) sampling parameters. Temperature and top-k change whether the
                  exact format / constraint is honoured.
  reliability (4) sampling parameters again, from the other side: a higher
                  temperature makes a model overclaim confidence and hallucinate more.

The screen stays small (20) because it must run in under a minute per combo, and
it stays balanced because a screen dominated by one dimension can only detect
changes that affect that dimension.

Usage:
  python runner/make_coarse.py                # write datasets/subsets/coarse.jsonl
  python runner/make_coarse.py --show         # list what was picked and why
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FULL = os.path.join(ROOT, "datasets", "full")
OUT = os.path.join(ROOT, "datasets", "subsets", "coarse.jsonl")

NL = chr(10)
SEED = 20261010

# Budget per block. Deliberately not equal: numeric precision is the most likely
# thing a launch flag disturbs, so it gets the most questions.
BLOCKS = [
    {"name": "math", "dim": "D3", "n": 8,
     "why": "数值精度:KV 量化与 Flash Attention 最先在精确值上体现",
     "pick": lambda r: r.get("subcat") in ("algebra", "geometry", "probability",
                                           "sequence", "chinese-word-problem", "age-problem")},
    # min_ctx in the data is the true requirement (17024 for a "16k" item, because the
    # filler plus the question exceed the nominal size). Matching on 16000 found
    # nothing; the block is built from a range instead, with one depth per size so a
    # RoPE change shows up wherever it bites.
    {"name": "longctx", "dim": "D7", "n": 4,
     "why": "RoPE 外推与上下文长度:只在跨距离取信息时显现",
     "pick": lambda r: 12000 <= (r.get("min_ctx") or 0) <= 70000
                       and r.get("subcat") in ("needle-single", "needle-multi", "long-qa"),
     "stratify": "context_tokens_approx"},
    {"name": "instruction", "dim": "D5", "n": 4,
     "why": "采样参数:温度与 top-k 决定约束是否被严格遵守",
     # the D5 verifiable pool repeats subjects with different constraints; dedupe on an
     # earlier prefix so a slot is not spent on a subject already covered
     "dedupe_on": "subject",
     "pick": lambda r: (r.get("expect", {}).get("checkers")
                        and r.get("subcat") == "verifiable")},
    {"name": "reliability", "dim": "D8", "n": 4,
     "why": "采样参数的另一面:高温会让模型更爱过度自信与编造",
     "pick": lambda r: r.get("subcat") in ("calibration", "hallucination")},
]


def load(dim):
    p = os.path.join(FULL, "%s_full.jsonl" % dim)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def build(rng, verbose=False):
    picked, notes = [], []
    for b in BLOCKS:
        pool = [r for r in load(b["dim"]) if b["pick"](r)]
        # deterministic: sort then sample, so the same screen is rebuilt every time
        pool.sort(key=lambda r: r.get("id") or "")
        rng.shuffle(pool)
        key = b.get("stratify")
        if key:
            # one question per stratum, so a context-length effect cannot be missed by
            # accidentally drawing four questions of the same size
            groups = {}
            for r in pool:
                groups.setdefault(r.get(key), []).append(r)
            take = []
            for k in sorted(groups, key=lambda x: (x is None, x)):
                if len(take) >= b["n"]:
                    break
                take.append(groups[k][0])
        else:
            # Prefer distinct subjects. Two questions about "photography" with
            # different sentence counts are not duplicates, but drawing the same
            # subject twice wastes a slot that could cover another topic.
            take, seen_key = [], set()
            dk = b.get("dedupe_on")
            for r in pool:
                if dk:
                    text = (r.get("user") or r.get("question") or "")
                    k = text[:70]
                    if k in seen_key:
                        continue
                    seen_key.add(k)
                take.append(r)
                if len(take) >= b["n"]:
                    break
        if len(take) < b["n"]:
            notes.append("%s 只凑到 %d/%d 题" % (b["name"], len(take), b["n"]))
        for r in take:
            r = dict(r)
            r["_coarse_block"] = b["name"]
            r["_coarse_why"] = b["why"]
            picked.append(r)
        if verbose:
            print("  %-12s %2d 题  %s" % (b["name"], len(take), b["why"]))
            for r in take:
                print("      %-24s %s" % (r.get("id"), (r.get("question") or r.get("user") or "")[:46]))
    return picked, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    rng = random.Random(SEED)
    picked, notes = build(rng, verbose=args.show or True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + NL)

    print()
    print("  粗筛题集: %d 题 -> %s" % (len(picked), os.path.relpath(args.out, ROOT)))
    if notes:
        for n in notes:
            print("  ⚠ %s" % n)
    print()
    print("  用途:阶段 1b —— 56 秒跑完,判断某个参数是否真的影响质量。")
    print("  判定:与大基线差 < 5pp 视为无显著影响,该变量出局。")


if __name__ == "__main__":
    main()
