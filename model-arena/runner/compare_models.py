# -*- coding: utf-8 -*-
"""compare_models.py - RELATIVE comparison between two runs, no weights involved.
Because both runs answer the SAME questions, this is a paired comparison:
  - per-dimension delta (percentage points)
  - per-question win / loss / tie, plus the questions that differ
Weighted absolute indices are deliberately NOT used here: the weights are
self-designed, and averaging across dimensions assumes they are commensurable.
Usage:
  python runner/compare_models.py --level canary --a Qwen3.6-35B-APEX --b Qwen3.5-9B
  python runner/compare_models.py --level canary --list
  python runner/compare_models.py --file-a <result.json> --file-b <result.json>
"""
import argparse, glob, json, os, re, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")
LABEL = {"D1": "速度", "D2": "选择", "D3": "数学", "D4": "代码", "D5": "指令",
         "D6": "Agent", "D7": "长上下文", "D8": "可靠性", "D9": "实战", "D10": "棋类"}


def load_records(path):
    d = json.load(open(path, encoding="utf-8"))
    recs = d.get("records")
    if recs is None:
        return None, d.get("summary", {})
    return {r["id"]: r for r in recs}, d.get("summary", {})


def list_runs(level):
    pats = [os.path.join(RESULTS, "LEVEL_%s_*.json" % level)] if level else \
           [os.path.join(RESULTS, "LEVEL_*.json")]
    out = []
    for p in sorted(glob.glob(pats[0])):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        s = d.get("summary") or {}
        out.append((p, s.get("model") or "?", s.get("level") or "?", len(d.get("records") or []),
                    s.get("accuracy")))
    return out


def pick(level, model):
    cands = [(p, m, lv, n, acc) for (p, m, lv, n, acc) in list_runs(level) if m == model]
    return cands[-1][0] if cands else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="canary")
    ap.add_argument("--a", default="")
    ap.add_argument("--b", default="")
    ap.add_argument("--file-a", default="")
    ap.add_argument("--file-b", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show-diffs", type=int, default=12)
    args = ap.parse_args()

    if args.list:
        print("%-12s %-24s %5s %8s  %s" % ("level", "model", "n", "acc", "file"))
        for p, m, lv, n, acc in list_runs(args.level):
            print("%-12s %-24s %5d %7.1f%%  %s" % (lv, m, n, (acc or 0) * 100, os.path.basename(p)))
        return

    pa = args.file_a or pick(args.level, args.a)
    pb = args.file_b or pick(args.level, args.b)
    if not pa or not pb:
        print("need two runs. use --list to see what is available.")
        sys.exit(2)
    ra, sa = load_records(pa)
    rb, sb = load_records(pb)
    if ra is None or rb is None:
        print("one of the files has no per-question records; only LEVEL runs can be paired.")
        sys.exit(3)

    ma, mb = sa.get("model") or "A", sb.get("model") or "B"
    common = sorted(set(ra) & set(rb))
    print("=" * 74)
    print("  相对比较(配对):%s  vs  %s" % (ma, mb))
    print("=" * 74)
    print("  A: %s" % os.path.basename(pa))
    print("  B: %s" % os.path.basename(pb))
    print("  共同题数: %d (A 独有 %d, B 独有 %d)"
          % (len(common), len(set(ra) - set(rb)), len(set(rb) - set(ra))))
    if not common:
        print("  没有可配对的题 —— 两次运行必须是同一级别。")
        return

    # per dimension
    dims = defaultdict(lambda: {"a": 0, "b": 0, "n": 0, "win": 0, "loss": 0, "tie": 0})
    win = loss = tie = 0
    diffs = []
    for qid in common:
        ra_ok = bool(ra[qid].get("correct"))
        rb_ok = bool(rb[qid].get("correct"))
        dim = ra[qid].get("dim") or "?"
        d = dims[dim]
        d["n"] += 1
        d["a"] += 1 if ra_ok else 0
        d["b"] += 1 if rb_ok else 0
        if ra_ok and not rb_ok:
            d["win"] += 1; win += 1
            diffs.append((dim, qid, "A 胜"))
        elif rb_ok and not ra_ok:
            d["loss"] += 1; loss += 1
            diffs.append((dim, qid, "B 胜"))
        else:
            d["tie"] += 1; tie += 1

    print()
    print("  维度         A         B       差值       A胜/B胜/平")
    print("  " + "-" * 60)
    for dim in sorted(dims):
        d = dims[dim]
        if not d["n"]:
            continue
        aa, bb = d["a"] / d["n"], d["b"] / d["n"]
        delta = (aa - bb) * 100
        mark = "  " if abs(delta) < 5 else ("A+" if delta > 0 else "B+")
        print("  %-10s %7.1f%% %8.1f%%  %+7.1fpp %s  %d/%d/%d"
              % (LABEL.get(dim, dim), aa * 100, bb * 100, delta, mark,
                 d["win"], d["loss"], d["tie"]))
    na, nb = len(common), len(common)
    A = sum(1 for q in common if ra[q].get("correct")) / na
    B = sum(1 for q in common if rb[q].get("correct")) / nb
    print("  " + "-" * 60)
    print("  %-10s %7.1f%% %8.1f%%  %+7.1fpp    %d/%d/%d"
          % ("总计", A * 100, B * 100, (A - B) * 100, win, loss, tie))
    print()
    print("  配对判定: A 胜 %d, B 胜 %d, 持平 %d  →  %s"
          % (win, loss, tie,
             ("两者无显著差异" if abs(win - loss) <= max(2, 0.1 * (win + loss))
              else ("%s 更好" % (ma if win > loss else mb)))))
    print("  说明: 配对比较只看同一批题上谁对谁错,不受权重与量纲影响。")
    if diffs and args.show_diffs:
        print()
        print("  分歧题(最多列 %d 条):" % args.show_diffs)
        for dim, qid, who in diffs[:args.show_diffs]:
            print("    %-12s %-22s %s" % (LABEL.get(dim, dim), qid, who))


if __name__ == "__main__":
    main()
