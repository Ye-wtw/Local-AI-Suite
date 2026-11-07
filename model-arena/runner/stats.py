# -*- coding: utf-8 -*-
"""stats.py - statistical rigor layer, following what established evaluators report.

Why this exists: a point estimate like "90.3% vs 87.3%" invites over-reading.
  - Artificial Analysis / HELM report uncertainty, not bare numbers.
  - tau-bench reports pass^k (consistency), not a single run.
  - Paired benchmarks use McNemar's test, not two independent accuracies.

Provides:
  wilson_ci(k, n)            - Wilson score interval for one accuracy
  bootstrap_ci(pairs)        - bootstrap CI, paired-aware
  mcnemar(a_correct, b_correct) - exact paired test + effect size
  cohens_h(p1, p2)           - effect size for two proportions
  pass_k(per_question)       - consistency across k repeats (tau-bench style)
  ece(confidences, correct)  - Expected Calibration Error (for confidence questions)
Usage:
  python runner/stats.py --a results/LEVEL_x.json --b results/LEVEL_y.json
"""
import argparse, glob, json, math, os, random


def wilson_ci(k, n, z=1.96):
    """Wilson score interval - correct for small n and extreme proportions."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def bootstrap_ci(pairs, iters=5000, seed=20261010):
    """pairs = [(a_correct, b_correct), ...]; bootstrap the DIFFERENCE a-b."""
    if not pairs:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    n = len(pairs)
    diffs = []
    for _ in range(iters):
        s = [pairs[rng.randrange(n)] for _ in range(n)]
        a = sum(1 for x in s if x[0]) / n
        b = sum(1 for x in s if x[1]) / n
        diffs.append(a - b)
    diffs.sort()
    lo = diffs[int(0.025 * iters)]
    hi = diffs[int(0.975 * iters) - 1]
    obs = (sum(1 for x in pairs if x[0]) - sum(1 for x in pairs if x[1])) / n
    return (obs, lo, hi)


def mcnemar(pairs):
    """Exact binomial McNemar for paired correctness (the right test when both
    models answer the SAME questions). Returns dict with n01, n10, p, verdict."""
    n01 = sum(1 for a, b in pairs if (not a) and b)   # B right, A wrong
    n10 = sum(1 for a, b in pairs if a and (not b))   # A right, B wrong
    n = n01 + n10
    if n == 0:
        return {"n01": 0, "n10": 0, "p": 1.0, "verdict": "identical"}
    # two-sided exact binomial test, p=0.5
    k = min(n01, n10)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    p = min(1.0, 2 * tail)
    return {"n01": n01, "n10": n10, "p": p,
            "verdict": ("significant" if p < 0.05 else "not significant")}


def cohens_h(p1, p2):
    """Effect size for two proportions. 0.2 small, 0.5 medium, 0.8 large."""
    def phi(p):
        p = min(max(p, 1e-9), 1 - 1e-9)
        return 2 * math.asin(math.sqrt(p))
    return abs(phi(p1) - phi(p2))


def pass_k(per_question_correct, k):
    """tau-bench style: probability that a question is answered correctly on
    ALL k independent attempts, averaged over questions."""
    if not per_question_correct:
        return 0.0
    tot = 0.0
    for attempts in per_question_correct:      # each = list of bools, len k
        if len(attempts) >= k:
            tot += 1.0 if all(attempts[:k]) else 0.0
    return tot / len(per_question_correct)


def ece(confidences, corrects, bins=10):
    """Expected Calibration Error: mean |accuracy - confidence| per bin.
    The proper way to score a model that reports confidence."""
    if not confidences:
        return 0.0, []
    buckets = [[] for _ in range(bins)]
    for c, ok in zip(confidences, corrects):
        idx = min(bins - 1, max(0, int(c * bins)))
        buckets[idx].append(1.0 if ok else 0.0)
    n = len(confidences)
    e = 0.0
    detail = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        acc = sum(b) / len(b)
        conf = (i + 0.5) / bins
        w = len(b) / n
        e += w * abs(acc - conf)
        detail.append({"bin": "%.1f-%.1f" % (i / bins, (i + 1) / bins),
                       "n": len(b), "mean_conf": round(conf, 3), "accuracy": round(acc, 3)})
    return e, detail


def load_level(path):
    d = json.load(open(path, encoding="utf-8"))
    s = d.get("summary") or {}
    return s, {r["id"]: r for r in (d.get("records") or [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="")
    ap.add_argument("--b", default="")
    ap.add_argument("--level", default="canary")
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not args.a or not args.b:
        print("usage: python runner/stats.py --a <LEVEL_a.json> --b <LEVEL_b.json>")
        return
    sa, ra = load_level(args.a)
    sb, rb = load_level(args.b)
    common = sorted(set(ra) & set(rb))
    if not common:
        print("no overlapping questions - cannot pair")
        return
    pairs = [(bool(ra[q]["correct"]), bool(rb[q]["correct"])) for q in common]
    ka = sum(1 for x in pairs if x[0]); kb = sum(1 for x in pairs if x[1])
    n = len(pairs)
    la, ha = wilson_ci(ka, n); lb, hb = wilson_ci(kb, n)
    obs, lo, hi = bootstrap_ci(pairs)
    mc = mcnemar(pairs)
    h = cohens_h(ka / n, kb / n)

    print("=" * 70)
    print("  统计显著性(A: %s  B: %s,共同题 %d)" % (sa.get("model"), sb.get("model"), n))
    print("=" * 70)
    print("  A 准确率 %.1f%%  95%%CI [%.1f%%, %.1f%%]" % (ka / n * 100, la * 100, ha * 100))
    print("  B 准确率 %.1f%%  95%%CI [%.1f%%, %.1f%%]" % (kb / n * 100, lb * 100, hb * 100))
    print("  差值 A-B  %.1fpp  bootstrap 95%%CI [%.1f, %.1f]pp" % (obs * 100, lo * 100, hi * 100))
    print("  效应量 Cohen's h = %.3f (%s)" % (h, "小" if h < 0.2 else ("中" if h < 0.5 else "大")))
    print()
    print("  McNemar 配对检验: A 独对 %d 题, B 独对 %d 题, p = %.4f -> %s"
          % (mc["n10"], mc["n01"], mc["p"], mc["verdict"]))
    if lo <= 0 <= hi:
        print("  ⚠ 差值的 95%%CI 跨过 0 -> 这一差异在统计上不可区分")
    else:
        print("  → 差值 CI 不跨 0,该差异可信")
    print()
    print("  说明:同一批题上比较必须用配对检验(McNemar),不能比较两个独立准确率。")


if __name__ == "__main__":
    main()
