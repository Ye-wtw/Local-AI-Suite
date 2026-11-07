# -*- coding: utf-8 -*-
"""compare_runs.py - gray-test (canary) regression check.
Compares a LEVEL run against a stored baseline and flags per-dimension drops.
Usage:
  python runner/compare_runs.py --level canary --baseline <file.json>   # explicit baseline
  python runner/compare_runs.py --level canary                          # auto: newest prior run
  python runner/compare_runs.py --level canary --save-baseline          # promote current run to baseline
"""
import argparse, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")
BASE_DIR = os.path.join(ROOT, "results", "baselines")


def load_level_runs(level):
    pats = [os.path.join(RESULTS, "LEVEL_%s_*.json" % level),
            os.path.join(BASE_DIR, "LEVEL_%s_*.json" % level)]
    files = []
    for p in pats:
        files.extend(glob.glob(p))
    return sorted(files, key=os.path.getmtime)


def summary_of(path):
    d = json.load(open(path, encoding="utf-8"))
    if "summary" in d:
        return d["summary"], d
    return d, d


def pct(x):
    return "%.1f%%" % (x * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", required=True, choices=["smoke", "canary", "full"])
    ap.add_argument("--baseline", default="", help="explicit baseline result file")
    ap.add_argument("--threshold", type=float, default=None, help="regression threshold in percentage points")
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(ROOT, "config", "test_levels.json"), encoding="utf-8"))
    spec = cfg["levels"].get(args.level, {})
    thr = args.threshold if args.threshold is not None else spec.get("regression_threshold_pp", 8.0)

    runs = load_level_runs(args.level)
    if not runs:
        print("no %s runs found in results/ - run:  python runner/run.py --endpoint <ep> --suite %s"
              % (args.level, args.level))
        sys.exit(2)
    current_path = runs[-1]
    cur, _ = summary_of(current_path)

    print("=" * 66)
    print("  灰测回归检查  level=%s" % args.level)
    print("=" * 66)
    print("  本次: %s" % os.path.relpath(current_path, ROOT))
    print("  总准确率: %s  (%d 题, %.1fs)" % (pct(cur["accuracy"]), cur["n"], cur["elapsed_s"]))

    if args.save_baseline:
        os.makedirs(BASE_DIR, exist_ok=True)
        dst = os.path.join(BASE_DIR, os.path.basename(current_path))
        json.dump({"summary": cur}, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("  已存为基线: %s" % os.path.relpath(dst, ROOT))
        return

    base_path = args.baseline
    if not base_path:
        # prefer an explicitly saved baseline; otherwise the previous run
        saved = sorted(glob.glob(os.path.join(BASE_DIR, "LEVEL_%s_*.json" % args.level)),
                       key=os.path.getmtime)
        if saved:
            base_path = saved[-1]
        else:
            prior = [f for f in runs[:-1]]
            base_path = prior[-1] if prior else ""
    if not base_path or not os.path.exists(base_path):
        print()
        print("  没有可用基线。先跑一次并保存基线:")
        print("    python runner/compare_runs.py --level %s --save-baseline" % args.level)
        print()
        print("  各维度(本次):")
        for d, b in sorted(cur["by_dim"].items()):
            print("      %-6s %3d/%3d  %s" % (d, b["correct"], b["n"], pct(b["correct"] / b["n"])))
        return

    base, _ = summary_of(base_path)
    print("  基线: %s" % os.path.relpath(base_path, ROOT))
    print("        总准确率 %s" % pct(base["accuracy"]))
    print()
    print("  维度      基线      本次      变化      判定")
    print("  " + "-" * 52)
    alerts = []
    for d in sorted(set(list(base.get("by_dim", {})) + list(cur.get("by_dim", {})))):
        bb = base.get("by_dim", {}).get(d)
        cb = cur.get("by_dim", {}).get(d)
        if not bb or not cb:
            continue
        bacc = bb["correct"] / bb["n"] if bb["n"] else 0
        cacc = cb["correct"] / cb["n"] if cb["n"] else 0
        delta_pp = (cacc - bacc) * 100
        verdict = "OK"
        if delta_pp <= -thr:
            verdict = "*** 回归 ***"
            alerts.append((d, delta_pp))
        elif delta_pp <= -thr / 2:
            verdict = "轻微下降"
        elif delta_pp >= thr / 2:
            verdict = "提升"
        print("  %-8s %7s  %7s  %+7.1fpp  %s" % (d, pct(bacc), pct(cacc), delta_pp, verdict))

    dtot = (cur["accuracy"] - base["accuracy"]) * 100
    print("  " + "-" * 52)
    print("  %-8s %7s  %7s  %+7.1fpp" % ("总计", pct(base["accuracy"]), pct(cur["accuracy"]), dtot))
    print()
    if alerts:
        print("  结论: 检出 %d 个维度回归(阈值 %.1fpp)—— %s"
              % (len(alerts), thr, ", ".join("%s %+.1fpp" % a for a in alerts)))
    else:
        print("  结论: 无回归(阈值 %.1fpp)" % thr)
    print("=" * 66)


if __name__ == "__main__":
    main()
