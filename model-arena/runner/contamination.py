# -*- coding: utf-8 -*-
"""contamination.py - compare original vs probed accuracy to estimate memorization.

If a model scores much higher on the original public questions than on shuffled or
paraphrased twins, it is likely recalling the item rather than solving it.

Contamination index = (acc_original - acc_probe) / acc_original, in percentage points
of the original. A small gap means the score is probably earned; a large gap means
the public number should be footnoted as possibly contaminated.

Usage:
  python runner/contamination.py --orig results/LEVEL_probe_*.json --probe results/LEVEL_probe_*.json
  python runner/contamination.py --orig results/A.json --probe results/B.json --json
"""
import argparse, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
from stats import wilson_ci, mcnemar   # noqa: E402


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    recs = {r["id"]: r for r in (d.get("records") or [])}
    return d.get("summary") or {}, recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", default="")
    ap.add_argument("--probe", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="reports/contamination.md")
    args = ap.parse_args()
    if not args.orig or not args.probe:
        print("need --orig and --probe"); return
    so, ro = load(args.orig)
    sp, rp = load(args.probe)

    # pair each probe record with its original via probe_of (fallback: strip suffix)
    pairs = []
    for pid, pr in rp.items():
        oid = pr.get("probe_of") or pid.replace("_shuf", "").replace("_para", "")
        # never pair a record with itself: when --orig and --probe point at the same
        # file, the original rows would otherwise "tie" against themselves and dilute
        # the measured gap
        if oid == pid:
            continue
        if oid in ro:
            mode = pr.get("probe_mode") or ("shuffle" if "_shuf" in pid else
                                            ("paraphrase" if "_para" in pid else "?"))
            pairs.append((oid, bool(ro[oid].get("correct")), bool(pr.get("correct")), mode))
    if not pairs:
        print("no paired questions found (need probe_of or a matching id)")
        return

    n = len(pairs)
    ko = sum(1 for _, a, _, _ in pairs if a)
    kp = sum(1 for _, _, b, _ in pairs if b)
    ao, ap_ = ko / n, kp / n
    lo, ho = wilson_ci(ko, n)
    lp, hp = wilson_ci(kp, n)
    mc = mcnemar([(a, b) for _, a, b, _ in pairs])
    idx = (ao - ap_) / ao * 100 if ao > 0 else 0.0
    modes = sorted(set(m for _, _, _, m in pairs))

    lines = []
    lines.append("=" * 68)
    lines.append("  污染检测 (probe: %s)" % ", ".join(modes))
    lines.append("=" * 68)
    lines.append("  配对题数 %d" % n)
    lines.append("  原始公开题   %.1f%%  95%%CI [%.1f%%, %.1f%%]" % (ao * 100, lo * 100, ho * 100))
    lines.append("  探针变体题   %.1f%%  95%%CI [%.1f%%, %.1f%%]" % (ap_ * 100, lp * 100, hp * 100))
    lines.append("  落差 %.1fpp   污染指数 %.1f%%" % ((ao - ap_) * 100, idx))
    lines.append("  McNemar: 原题独对 %d, 探针独对 %d, p = %.4f (%s)"
                 % (mc["n10"], mc["n01"], mc["p"], mc["verdict"]))
    lines.append("")
    discordant = mc["n10"] + mc["n01"]
    sig = mc["p"] < 0.05
    if discordant < 10:
        v = ("证据不足:只有 %d 题发生分歧,检验力太低 —— 既不能证明污染,也不能排除。"
             "要下结论需扩大样本(建议 >=300 题)" % discordant)
    elif not sig:
        v = "未见显著污染:落差存在但统计上不可区分于随机波动"
    elif idx <= 20:
        v = "轻度疑似:有记忆成分,建议在榜单上标注该来源"
    else:
        v = "高度疑似污染:公开题分数不可直接采信,应以探针分为准"
    lines.append("  判定: %s" % v)
    lines.append("  (%s)" % ("达到显著" if sig else "未达显著 p=%.3f" % mc["p"]))
    lines.append("")
    lines.append("  说明:探针题与原题语义相同,只改了选项顺序或措辞。")
    lines.append("       真会解题的模型两者应当接近;只会背答案的模型会大幅掉分。")
    print("\n".join(lines))

    if args.out:
        try:
            os.makedirs(os.path.dirname(os.path.join(ROOT, args.out)), exist_ok=True)
            with open(os.path.join(ROOT, args.out), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print("\n  -> %s" % args.out)
        except Exception as e:
            print("  (report write failed: %s)" % e)
    if args.json:
        print(json.dumps({"n": n, "acc_orig": ao, "acc_probe": ap_, "index": idx,
                          "mcnemar": mc}, ensure_ascii=False))


if __name__ == "__main__":
    main()
