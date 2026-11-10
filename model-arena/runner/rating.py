# -*- coding: utf-8 -*-
"""rating.py - paired-comparison rating models, to cross-check the tests in stats.py.

stats.py answers "is A better than B on THESE questions" with a paired test. That is
the right question for two models, and the wrong shape for a leaderboard: with N
models you want a single consistent ranking where every model's score accounts for
WHO it was compared against.

Arena does this with Bradley-Terry. Their pipeline is open source (arena-rank,
Apache-2.0) but not reachable from this machine - github.com is blocked by the local
hosts file and codeload times out - so this is an independent implementation of the
published method, not a copy. It exists to CROSS-CHECK our own numbers, which is the
useful part: if a simple win-rate ranking and a fitted Bradley-Terry ranking disagree
about the order, one of them is being fooled by an unbalanced comparison schedule,
and that is worth knowing before publishing a leaderboard.

  bradley_terry(wins)   MLE fit of P(i beats j) = s_i / (s_i + s_j)
  bootstrap_bt(...)     resample questions, refit, report the rank interval
  win_rate(wins)        the naive baseline, for comparison

Usage:
  python runner/rating.py --from-paired results/leaderboard.json
  python runner/rating.py --selftest
"""
import argparse
import glob
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    from console import make_stdout_safe
    make_stdout_safe()
except Exception:
    pass
RESULTS = os.path.join(ROOT, "results")
NL = chr(10)
SEED = 20261010


def bradley_terry(wins, iters=500, tol=1e-9, prior=1.0):
    """Fit Bradley-Terry strengths by MM (minorization-maximization).

    wins: {(i, j): count} meaning i beat j `count` times.
    Returns {model: strength}, normalised so the values are comparable across runs.

    MM is used rather than gradient descent because it is monotone and needs no step
    size: each iteration is a closed form, so a bad starting point cannot diverge.

    `prior` adds a small symmetric count to every matchup. Without it a model that
    won (or lost) every single game has no finite MLE - its strength diverges, and a
    37-for-37 entry came out at 149.5 against a field around 0.5, which is not a
    rating, it is an artifact of complete separation. The prior keeps the ordering
    while bounding the values.
    """
    models = sorted({m for pair in wins for m in pair})
    if not models:
        return {}
    s = {m: 1.0 for m in models}

    # regularise: every unordered pair gets `prior` wins on each side
    reg = {}
    for (i, j), n in wins.items():
        reg[(i, j)] = reg.get((i, j), 0) + n
        reg.setdefault((j, i), reg.get((j, i), 0))
    for i in models:
        for j in models:
            if i < j:
                reg[(i, j)] = reg.get((i, j), 0) + prior
                reg[(j, i)] = reg.get((j, i), 0) + prior

    for _ in range(iters):
        w = {m: 0.0 for m in models}
        denom = {m: 0.0 for m in models}
        for (i, j), n in reg.items():
            if n <= 0:
                continue
            w[i] += n
            total = s[i] + s[j]
            denom[i] += n / total
            denom[j] += n / total
        new = {}
        for m in models:
            new[m] = (w[m] / denom[m]) if denom[m] > 0 else s[m]
        gm = math.exp(sum(math.log(max(v, 1e-12)) for v in new.values()) / len(new))
        new = {m: v / gm for m, v in new.items()}
        delta = max(abs(new[m] - s[m]) for m in models)
        s = new
        if delta < tol:
            break
    return s


def win_rate(wins):
    """Naive ranking: wins / games. Kept as a baseline because it ignores opponent
    strength, so a disagreement with Bradley-Terry is diagnostic."""
    played = {m: 0 for pair in wins for m in pair}
    won = {m: 0 for pair in wins for m in pair}
    for (i, j), n in wins.items():
        played[i] += n
        won[i] += n
        played[j] += n
    return {m: (won[m] / played[m] if played[m] else 0.0) for m in played}


def bootstrap_bt(wins, iters=200, seed=SEED):
    """Resample the PAIR COUNTS and refit, to get a rank interval.

    Resampling pairs (not questions) is the conservative choice here: it treats each
    observed matchup as one draw, so the interval widens when the schedule is thin.
    """
    rng = random.Random(seed)
    keys = [k for k, v in wins.items() if v > 0]
    if len(keys) < 2:
        return {}
    ranks = {}
    order_counts = {}
    for _ in range(iters):
        sample = {}
        for k in keys:
            # Poisson bootstrap: each matchup contributes a resampled count
            c = sum(1 for _ in range(int(wins[k])) if rng.random() < 0.5) * 2
            if c > 0:
                sample[k] = c
        s = bradley_terry(sample, iters=200)
        if not s:
            continue
        ranked = sorted(s, key=lambda m: -s[m])
        for pos, m in enumerate(ranked, 1):
            ranks.setdefault(m, []).append(pos)
        order_counts[tuple(ranked)] = order_counts.get(tuple(ranked), 0) + 1
    out = {}
    for m, rs in ranks.items():
        rs_sorted = sorted(rs)
        out[m] = {"rank_p50": rs_sorted[len(rs_sorted) // 2],
                  "rank_lo": rs_sorted[int(0.025 * len(rs_sorted))],
                  "rank_hi": rs_sorted[int(0.975 * len(rs_sorted)) - 1],
                  "n": len(rs)}
    top = max(order_counts, key=order_counts.get) if order_counts else ()
    return {"per_model": out, "most_common_order": list(top),
            "order_share": (order_counts.get(top, 0) / iters) if iters else 0.0}


def wins_from_paired(paired):
    """Turn the per-question correctness maps into matchup counts.

    paired maps "model|level|stamp" -> {map: {qid: 0/1}}. Two runs on the same level
    are compared question by question; a question both got right or both got wrong
    carries no information and is dropped, exactly as in a paired test.
    """
    runs = []
    for key, rec in (paired or {}).items():
        recs = rec.get("map") or {}
        if recs:
            runs.append((rec.get("model") or key, rec.get("level"), recs))
    wins = {}
    for a in range(len(runs)):
        for b in range(a + 1, len(runs)):
            ma, la, ra = runs[a]
            mb, lb, rb = runs[b]
            if la != lb:
                continue                       # only compare within the same level
            common = set(ra) & set(rb)
            aw = sum(1 for q in common if ra[q] and not rb[q])
            bw = sum(1 for q in common if rb[q] and not ra[q])
            if aw or bw:
                wins[(ma, mb)] = wins.get((ma, mb), 0) + aw
                wins[(mb, ma)] = wins.get((mb, ma), 0) + bw
    return wins


def _selftest():
    """A synthetic schedule with a KNOWN order, including an unbalanced one where the
    naive ranking should be wrong and Bradley-Terry should recover the truth."""
    print("=" * 66)
    print("  rating.py 自检")
    print("=" * 66)
    print()
    # true strengths: A > C > B > D. B is NOT second best; it only looks good
    # because its schedule is soft.
    truth = {"A": 0.6, "C": 0.2, "B": -0.6, "D": -4.0}
    def p(i, j):
        return 1 / (1 + math.exp(-(truth[i] - truth[j])))
    rng = random.Random(SEED)

    # balanced round robin: everyone plays everyone the same number of times
    w1 = {}
    for i, j in (("A", "B"), ("A", "C"), ("B", "C")):
        for _ in range(400):
            if rng.random() < p(i, j):
                w1[(i, j)] = w1.get((i, j), 0) + 1
            else:
                w1[(j, i)] = w1.get((j, i), 0) + 1
    s1 = bradley_terry(w1)
    order1 = sorted(s1, key=lambda m: -s1[m])
    # w1 only contains A, B, C - D is absent from the balanced schedule, so compare
    # against the truth order restricted to the models actually present.
    truth_rank_full = sorted(truth, key=lambda m: -truth[m])
    truth_rank = [m for m in truth_rank_full if m in s1]
    print("  均衡赛程 (真实强度 %s):" % " > ".join(truth_rank))
    print("    Bradley-Terry 排名:", " > ".join(order1),
          "| 正确" if order1 == truth_rank else "| 错误")
    for m in order1:
        print("      %s  strength=%.3f  win_rate=%.3f" % (m, s1[m], win_rate(w1)[m]))

    # Unbalanced schedule designed so the naive ranking MUST be wrong:
    #   B is genuinely weaker than A, but only ever plays the weakest model D and
    #   wins nearly every time. A plays B and C, so A's win rate is dragged down by
    #   real opponents. A pure win-rate ranking puts B first; BT should not.
    w2 = {}
    # A faces only real opponents (B and C) - a hard schedule, so its win rate sags.
    for _ in range(100):
        if rng.random() < p("A", "B"):
            w2[("A", "B")] = w2.get(("A", "B"), 0) + 1
        else:
            w2[("B", "A")] = w2.get(("B", "A"), 0) + 1
    for _ in range(100):
        if rng.random() < p("A", "C"):
            w2[("A", "C")] = w2.get(("A", "C"), 0) + 1
        else:
            w2[("C", "A")] = w2.get(("C", "A"), 0) + 1
    # B mostly farms the weakest model D - a soft schedule that inflates its win rate
    for _ in range(500):
        if rng.random() < p("B", "D"):
            w2[("B", "D")] = w2.get(("B", "D"), 0) + 1
        else:
            w2[("D", "B")] = w2.get(("D", "B"), 0) + 1
    s2 = bradley_terry(w2)
    wr2 = win_rate(w2)
    print()
    print("  不均衡赛程 (B 只赢弱对手 D):")
    print("    Bradley-Terry:", " > ".join(sorted(s2, key=lambda m: -s2[m])))
    print("    朴素胜率      :", " > ".join(sorted(wr2, key=lambda m: -wr2[m])))
    bt_rank = sorted(s2, key=lambda m: -s2[m])
    wr_rank = sorted(wr2, key=lambda m: -wr2[m])
    truth_rank = sorted(truth, key=lambda m: -truth[m])
    print("    真实强度      :", " > ".join(truth_rank))
    print()
    print("    BT 排对了吗   :", "是" if bt_rank == truth_rank else "否 (%s)" % " > ".join(bt_rank))
    print("    胜率排对了吗 :", "是" if wr_rank == truth_rank else "否 (%s)" % " > ".join(wr_rank))
    if bt_rank != wr_rank:
        print("    → 两者不一致,这正是需要 Bradley-Terry 的原因:")
        print("      某模型靠刷弱对手把胜率刷了上去,但它并不更强。")
    else:
        print("    → 两者一致(本例的赛程偏斜不足以改变顺序)")

    boot = bootstrap_bt(w1, iters=60)
    print()
    print("  自举排名区间(均衡赛程):")
    for m, v in sorted((boot.get("per_model") or {}).items(), key=lambda x: x[1]["rank_p50"]):
        print("    %s  rank p50=%d [%d, %d]" % (m, v["rank_p50"], v["rank_lo"], v["rank_hi"]))
    print("    最常见顺序: %s (%.0f%%)" % (" > ".join(boot.get("most_common_order") or []),
                                        (boot.get("order_share") or 0) * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-paired", default="")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--bootstrap", type=int, default=0)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    if args.selftest or not args.from_paired:
        _selftest()
        return

    path = args.from_paired
    if not os.path.exists(path):
        print("找不到:", path)
        return 2
    paired = json.load(open(path, encoding="utf-8")).get("paired") or {}
    wins = wins_from_paired(paired)
    if not wins:
        print("没有可比较的配对数据(需要同一级别的多次运行)")
        return 0

    s = bradley_terry(wins)
    wr = win_rate(wins)
    order = sorted(s, key=lambda m: -s[m])
    print("=" * 70)
    print("  Bradley-Terry 排名 vs 朴素胜率")
    print("=" * 70)
    print("  %-24s %10s %10s %8s" % ("模型", "BT 强度", "胜率", "对局数"))
    print("  " + "-" * 56)
    played = {m: 0 for pair in wins for m in pair}
    for (i, j), n in wins.items():
        played[i] += n
    for m in order:
        print("  %-24s %10.3f %9.1f%% %8d" % (m[:24], s[m], wr[m] * 100, played[m]))

    wr_order = sorted(wr, key=lambda m: -wr[m])
    print()
    if order == wr_order:
        print("  两种排名一致 —— 赛程没有明显偏斜。")
    else:
        print("  ⚠ 两种排名不一致:")
        print("     BT : %s" % " > ".join(order))
        print("     胜率: %s" % " > ".join(wr_order))
        print("     原因通常是某模型只和弱对手交手;BT 会校正对手强度,朴素胜率不会。")

    if args.bootstrap:
        boot = bootstrap_bt(wins, iters=args.bootstrap)
        print()
        print("  自举排名区间(%d 次重采样):" % args.bootstrap)
        for m, v in sorted((boot.get("per_model") or {}).items(), key=lambda x: x[1]["rank_p50"]):
            print("    %-24s rank %d [%d, %d]" % (m[:24], v["rank_p50"], v["rank_lo"], v["rank_hi"]))
        print("    最常见顺序: %s (%.0f%%)"
              % (" > ".join(boot.get("most_common_order") or []),
                 (boot.get("order_share") or 0) * 100))
        if args.json:
            json.dump({"strengths": s, "win_rate": wr, "bootstrap": boot},
                      open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("    -> %s" % args.json)


if __name__ == "__main__":
    main()
