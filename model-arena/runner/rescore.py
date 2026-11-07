# -*- coding: utf-8 -*-
"""rescore.py - 用当前判分器重算历史结果(不跑模型,纯离线)。

为什么需要:改判分器后,旧结果文件里的 correct 字段是**旧判分器下的判决**。
直接拿来对比新旧数字会得出错误结论(甚至看不出修好了还是改坏了)。
重算一遍既修正数字,也能发现"修一个坏一个"的反向翻转。
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))

from chess_judge import judge_chess  # noqa: E402
from judge import judge_math  # noqa: E402
from checkers import judge_mcq, run_checkers  # noqa: E402
from code_judge import judge_code  # noqa: E402
from agent_judge import judge_agent  # noqa: E402
from reliability_judge import judge_reliability  # noqa: E402
from d9_judge import judge_d9  # noqa: E402


def rescore_one(path):
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    s = d.get("summary") or {}
    dim = s.get("dim")
    recs = d.get("records") or []
    if not recs:
        return None
    rows = {}
    ds = os.path.join(ROOT, "datasets", "full", "%s_full.jsonl" % dim)
    if os.path.isfile(ds):
        with io.open(ds, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    x = json.loads(line)
                    rows[x.get("id")] = x
    changed = []
    for r in recs:
        q = rows.get(r.get("id"))
        if not q or r.get("error"):
            continue
        old = bool(r.get("correct"))
        if dim == "D10":
            v = judge_chess(q, r.get("response") or "")
        elif dim == "D3":
            v = judge_math(r.get("response") or "", q.get("answer"),
                           q.get("tolerance", "exact"))
        elif dim == "D2":
            v = judge_mcq(r.get("response") or "", q.get("answer_letter"))
        elif dim == "D4":
            v = judge_code(q, r.get("response") or "")
        elif dim == "D5":
            passed, _x = run_checkers(q.get("expect", {}).get("checkers"),
                                      r.get("response") or "")
            v = {"correct": bool(passed)}
        elif dim == "D6":
            # run.py 传的 result 是 {"text","tool_calls","latency_s"}。
            # 直接传 {} 会让判分器看不到任何工具调用 -> 全判"漏调用",
            # 实测把 88.4% 重算成 5.4%(130 题假翻转)。
            res = {"text": r.get("response") or "",
                   "tool_calls": r.get("tool_calls") or [],
                   "latency_s": r.get("latency_s")}
            v = judge_agent(q, res, res["text"])
        elif dim == "D8":
            v = judge_reliability(q, r.get("response") or "")
        elif dim == "D9":
            # 两个坑:
            #   1. judge_d9 要 **baseline 检查项 b**,不是题库行 q —— 传 q 得到
            #      "no checks defined",46 题假翻转。
            #   2. exec 类题(d9m_*)走的是**跑子进程实际执行**那条路,
            #      结果里带 scored_by="exec"。用规则判分重算它们必然假翻转。
            #      exec 题只能重跑,不能离线重算,所以保留原判。
            if r.get("scored_by") == "exec" or r.get("baseline_source") == "exec":
                continue
            import run as _R
            _qs, _base = _R._load_d9()
            b = _base.get(r.get("id"))
            if not b:
                continue
            v = judge_d9(b, r.get("response") or "")
        else:
            return "skip"
        nc = bool(v.get("correct"))
        if nc != old:
            changed.append({"id": r.get("id"), "from": old, "to": nc,
                            "reason": str(v.get("reason"))[:60]})
        r["correct"] = nc
        r["rescored"] = True
    valid = [r for r in recs if not r.get("error")]
    cor = sum(1 for r in valid if r.get("correct"))
    s["correct"] = cor
    s["n_valid"] = len(valid)
    s["n_errors"] = len(recs) - len(valid)
    s["accuracy"] = (cor / len(valid)) if valid else 0.0
    s["rescore_note"] = "用当前判分器重算,翻转 %d 题" % len(changed)
    d["rescore_changes"] = changed
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(ROOT, "results", base + "_rescored.json")
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return {"dim": dim, "old": None, "new": cor, "n": len(valid),
            "flips": changed, "out": os.path.basename(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    for p in a.files:
        r = rescore_one(p)
        if r is None or r == "skip":
            print("%s: 跳过(维度暂不支持重算)" % os.path.basename(p))
            continue
        print("%s  维度=%s  重算后 %d/%d = %.1f%%  翻转 %d 题 -> %s" % (
            os.path.basename(p), r["dim"], r["new"], r["n"],
            r["new"] / r["n"] * 100 if r["n"] else 0,
            len(r["flips"]), r["out"]))
        for c in r["flips"]:
            print("    %-22s %s -> %s  (%s)" % (c["id"], c["from"], c["to"], c["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
