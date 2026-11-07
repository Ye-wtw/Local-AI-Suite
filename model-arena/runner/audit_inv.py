# -*- coding: utf-8 -*-
"""audit_inv.py - 盘点各维度可离线复核的失败题。

读所有非 mock 结果的 records，按 records[].dim 归组，
统计每个维度有多少失败题带 response（能离线重判）。

⚠ 维度在 records[].dim，不在 summary.dim：套件级结果（LEVEL_*）
  记录整个套件，summary 里没有单一维度。读错位置会把 1055 题归为"未知"。

Usage:
  python runner/audit_inv.py
"""
import collections
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    agg = collections.defaultdict(lambda: {"n": 0, "fails": 0, "with_resp": 0})
    files_ok = 0

    for f in glob.glob(os.path.join(ROOT, "results", "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        s = d.get("summary") if isinstance(d.get("summary"), dict) else d
        recs = d.get("records") or []
        if not recs:
            continue
        ep = (s.get("endpoint") if isinstance(s, dict) else None) or ""
        if "mock" in ep.lower():
            continue
        files_ok += 1
        fallback = ((s.get("dim") if isinstance(s, dict) else None)
                    or (s.get("suite") if isinstance(s, dict) else None) or "?")
        for r in recs:
            if not isinstance(r, dict):
                continue
            dim = r.get("dim") or fallback
            a = agg[dim]
            a["n"] += 1
            if not r.get("correct"):
                a["fails"] += 1
                if r.get("response"):
                    a["with_resp"] += 1

    print("有效结果文件: %d" % files_ok)
    print()
    print("%-8s %8s %8s %10s" % ("维度", "题数", "失败", "可复核"))
    print("-" * 40)
    tn = tf = tr = 0
    for dim in sorted(agg, key=lambda x: (len(str(x)), str(x))):
        a = agg[dim]
        tn += a["n"]; tf += a["fails"]; tr += a["with_resp"]
        print("%-8s %8d %8d %10d" % (dim, a["n"], a["fails"], a["with_resp"]))
    print("-" * 40)
    print("%-8s %8d %8d %10d" % ("合计", tn, tf, tr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())