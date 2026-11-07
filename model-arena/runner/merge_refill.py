# -*- coding: utf-8 -*-
"""merge_refill.py - 把补跑结果并回主结果,产出**完整**的一次评测。

为什么要单独一步而不是让 refill 直接改原文件:
  * 原文件是"当时实际发生了什么"的忠实记录(含 257 条连接失败),不该被篡改;
  * 补跑是"事后把没答上的题补答",属于数据修复,要留下可追溯的痕迹;
  * 合并结果里每题带 source 字段(original / refilled),报告能看出来。

合并口径:
  * 只并入**原记录 error 的题**,原记录答对的题一律不动;
  * 一份补跑结果覆盖一题,重复 id 只取最后一条有效记录;
  * accuracy 分母 = 有效题数(无 error),与 AGENTS.md 第 6 条一致。
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True)
    ap.add_argument("--refill", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    with io.open(a.orig, encoding="utf-8") as f:
        orig = json.load(f)
    with io.open(a.refill, encoding="utf-8") as f:
        refill = json.load(f)

    recs = orig.get("records") or []
    rr = refill.get("records") or []
    # 补跑记录按 id 建索引,只保留有效(拿到回答的)那条
    by_id = {}
    for r in rr:
        if r.get("error"):
            continue
        by_id[r.get("id")] = r

    merged = []
    n_filled = 0
    for r in recs:
        if r.get("error") and r.get("id") in by_id:
            m = dict(by_id[r["id"]])
            m["source"] = "refilled"
            m["first_error"] = (r.get("error_reason") or r.get("reason") or "")[:120]
            merged.append(m)
            n_filled += 1
        else:
            m = dict(r)
            m["source"] = "original"
            merged.append(m)

    valid = [x for x in merged if not x.get("error")]
    cor = sum(1 for x in valid if x.get("correct"))
    s = dict(orig.get("summary") or {})
    s.update({
        "n": len(merged),
        "n_valid": len(valid),
        "n_errors": len(merged) - len(valid),
        "correct": cor,
        "accuracy": (cor / len(valid)) if valid else 0.0,
        "refilled_count": n_filled,
        "merge_note": "补跑并入 %d 题;accuracy 分母为有效题数" % n_filled,
    })
    out = dict(orig)
    out["summary"] = s
    out["records"] = merged
    out["merged_from_refill"] = os.path.basename(a.refill)

    path = a.out or os.path.join(
        ROOT, "results",
        os.path.splitext(os.path.basename(a.orig))[0] + "_merged.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("并入 %d 题  有效 %d/%d  正确 %d  正确率 %.1f%%" % (
        n_filled, len(valid), len(merged), cor,
        (cor / len(valid) * 100) if valid else 0))
    print("-> " + os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
