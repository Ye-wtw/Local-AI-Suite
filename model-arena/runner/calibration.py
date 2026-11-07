# -*- coding: utf-8 -*-
"""calibration.py - Expected Calibration Error for confidence-reporting questions.

Why: a yes/no accuracy hides the thing D8 exists to measure. A model can be right
90% of the time while being catastrophically overconfident, and it can be right 70%
with perfect calibration. Serious evaluators score task types with the metric that
fits them (e.g. Codeforces is reported as an Elo rating, not a percentage), so
confidence questions get ECE, not accuracy.

ECE = sum_bins (n_bin / N) * |mean_confidence_bin - accuracy_bin|

Usage: python runner/calibration.py [--file results/LEVEL_x.json]
       python runner/calibration.py --all
"""
import argparse, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from stats import ece   # noqa: E402


def collect(path):
    d = json.load(open(path, encoding="utf-8"))
    confs, oks = [], []
    for r in d.get("records") or []:
        if r.get("dim") != "D8":
            continue
        det = r.get("detail") or {}
        c = det.get("confidence")
        if c is None:
            continue
        confs.append(min(1.0, max(0.0, float(c) / 100.0)))
        oks.append(1.0 if r.get("correct") else 0.0)
    return d.get("summary") or {}, confs, oks


def report(path):
    s, confs, oks = collect(path)
    if not confs:
        print("%-46s 无置信度数据" % os.path.basename(path)[:46])
        return
    e, detail = ece(confs, oks)
    acc = sum(oks) / len(oks)
    mean_conf = sum(confs) / len(confs)
    over = mean_conf - acc
    print("=" * 66)
    print("  校准报告  %s" % os.path.basename(path))
    print("=" * 66)
    print("  模型 %s | 报告置信度的题 %d 道" % (s.get("model"), len(confs)))
    print("  平均置信度 %.1f%%  实际准确率 %.1f%%  偏差 %+.1fpp (%s)"
          % (mean_conf * 100, acc * 100, over * 100,
             "过度自信" if over > 0.05 else ("过度保守" if over < -0.05 else "校准良好")))
    print("  ECE = %.3f  (%s)" % (e, "优" if e < 0.05 else ("可" if e < 0.15 else "差")))
    print()
    print("  分箱明细:")
    for b in detail:
        print("    %-10s n=%-3d 置信 %.2f  实际 %.2f  差 %.2f"
              % (b["bin"], b["n"], b["mean_conf"], b["accuracy"],
                 abs(b["accuracy"] - b["mean_conf"])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.file:
        report(args.file); return
    pats = ["results/LEVEL_*.json"] if args.all else ["results/LEVEL_canary_*.json"]
    got = False
    for p in sorted(glob.glob(os.path.join(ROOT, pats[0]))):
        s, c, o = collect(p)
        if c:
            report(p); print(); got = True
    if not got:
        print("没有找到含置信度数据的运行结果")


if __name__ == "__main__":
    main()
