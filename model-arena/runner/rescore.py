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
from longctx_judge import judge_longctx  # noqa: E402


_D11_CACHE = {}


def _d11_items():
    """Rebuild the D11 items exactly as suite_vision does (images are generated)."""
    if "items" not in _D11_CACHE:
        import vision as vismod
        root = os.path.join(ROOT, "datasets", "fixtures", "vision")
        _D11_CACHE["items"] = vismod.build_all(root)
    return _D11_CACHE["items"]


def rescore_one(path):
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    s = d.get("summary") or {}
    dim = s.get("dim")
    recs = d.get("records") or []
    if not recs:
        # An empty file is not an unsupported dimension. Reporting it as one
        # hid which files actually need work (实测 6 个 D11 空文件全报"维度暂不支持")。
        return {"dim": dim, "old": None, "new": None, "n": 0, "flips": [],
                "out": None, "skipped": "文件里没有 records(那次跑测没产出任何记录)"}
    if not dim:
        # A LEVEL_/run_ file has NO summary["dim"]: it is one run across every
        # dimension, so its records carry their own "dim". The old code built
        # rows from "datasets/full/None_full.jsonl" (no such file), every q was
        # then None, and the loop below skipped all 1949 records - yet it still
        # wrote "<name>_rescored.json" with the ORIGINAL verdicts. Verified:
        # LEVEL_full_deepseek-flash_...cloudfull.json has 401 D5 records all
        # correct=True with checker_results=[] (the empty-rule-set pass), and
        # its _rescored twin is byte-identical on those verdicts. A file that
        # says "rescored" while changing nothing is worse than no rescore at all.
        dim = "MULTI"
    rows = {}
    dims = set()
    if dim == "MULTI":
        for r in recs:
            dd = r.get("dim")
            if dd:
                dims.add(dd)
    else:
        dims.add(dim)
    for dd in sorted(dims):
        ds = os.path.join(ROOT, "datasets", "full", "%s_full.jsonl" % dd)
        if not os.path.isfile(ds):
            # D11 (vision) and D12 (compaction) have no row bank: both build their
            # items FROM CODE at run time, so the only way to re-judge them is to
            # rebuild the same items. D11 items are derived from the generated
            # fixture images and carry a stable id; D12 scenarios are derived from
            # the seed embedded in the id ("d12_<seed>"). Verified deterministic:
            # build_all over two different roots yields identical ids AND answers.
            if dd == "D11":
                for x in _d11_items():
                    rows[x.get("id")] = x
            continue
        with io.open(ds, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    x = json.loads(line)
                    rows[x.get("id")] = x
    changed = []
    touched = 0
    for r in recs:
        if r.get("error"):
            continue
        rd = r.get("dim") or dim          # LEVEL files carry their own dim on the record
        if rd == "D12":
            # See the long note in the D12 branch below: the scenario cannot be
            # rebuilt from its seed any more, so this file must be refused here -
            # before the row lookup, which has no D12 bank to find and would
            # otherwise report a misleading "missing row" instead.
            return {"dim": "D12", "old": None, "new": None, "n": 0, "flips": [],
                    "out": None,
                    "skipped": "D12 场景由 seed 生成且生成器已变更,同 seed 不再可复现;"
                               "离线重算会得到假分数,只能重跑模型"}
        q = rows.get(r.get("id"))
        if not q:
            continue
        touched += 1
        old = bool(r.get("correct"))
        if rd == "D10":
            v = judge_chess(q, r.get("response") or "")
        elif rd == "D3":
            v = judge_math(r.get("response") or "", q.get("answer"),
                           q.get("tolerance", "exact"))
        elif rd == "D2":
            v = judge_mcq(r.get("response") or "", q.get("answer_letter"))
        elif rd == "D4":
            v = judge_code(q, r.get("response") or "")
        elif rd == "D5":
            # 按 expect 形状分派：checkers 走本项目校验器，instruction_id_list 走 IFEval。
            # 旧代码一律用 run_checkers，300 条 public-IFEval 因此被空规则集判成通过。
            import run as _R
            passed, _x = _R._judge_d5(q, r.get("response") or "")
            v = {"correct": bool(passed), "reason": str(_x.get("reason", ""))[:60]}
        elif rd == "D6":
            # run.py 传的 result 是 {"text","tool_calls","latency_s"}。
            # 直接传 {} 会让判分器看不到任何工具调用 -> 全判"漏调用",
            # 实测把 88.4% 重算成 5.4%(130 题假翻转)。
            res = {"text": r.get("response") or "",
                   "tool_calls": r.get("tool_calls") or [],
                   "latency_s": r.get("latency_s")}
            v = judge_agent(q, res, res["text"])
        elif rd == "D8":
            v = judge_reliability(q, r.get("response") or "")
        elif rd == "D11":
            import vision as vismod
            v = vismod.judge_vision(q, r.get("response") or "")
        elif rd == "D12":
            # D12 CANNOT be re-judged offline, and pretending otherwise is worse
            # than refusing. The scenario is built from a seed, but build_scenario
            # has been edited since these runs (the same-surname distractor pass),
            # which shifted the whole random sequence: seed 20261101 used to yield
            # "石工程师" and now yields "江总监". Verified: every stored response
            # answers a DIFFERENT project than the rebuilt scenario asks about, so
            # a rebuild-based rescore scored 0/48 - all 48 flips were artifacts.
            # Re-running the model is the only honest way to re-judge D12.
            return {"dim": "D12", "old": None, "new": None, "n": 0, "flips": [],
                    "out": None,
                    "skipped": "D12 场景由 seed 生成且生成器已变更,同 seed 不再可复现;"
                               "离线重算会得到假分数,只能重跑模型"}
        elif rd == "D7":
            # D7 was missing, so LEVEL_/run_ files (which mix every dimension)
            # hit the "unsupported dim" branch and the whole file was skipped.
            # That left the empty-rule-set D5 verdicts in place on those files.
            v = judge_longctx(q, r.get("response") or "")
        elif rd == "D9":
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
            # 一条记录遇到不支持的维度就把整份文件判为 skip 是对的：
            # 多维度文件的 by_dim 会因此只更新一部分，剩下那部分仍是旧判决，
            # 一半新一半旧的数字比不重算更误导。
            return "skip"
        nc = bool(v.get("correct"))
        if nc != old:
            changed.append({"id": r.get("id"), "from": old, "to": nc,
                            "reason": str(v.get("reason"))[:60]})
        r["correct"] = nc
        r["rescored"] = True
    valid = [r for r in recs if not r.get("error")]
    cor = sum(1 for r in valid if r.get("correct"))
    if dim == "MULTI":
        # 多维度文件没有全局 accuracy；它按 by_dim 汇总，榜单读的也是 by_dim。
        # 只写 correct/n_valid 会让上层 "correct/nm" 与 by_dim 互相矛盾。
        bd = s.get("by_dim") or {}
        for dkey, blk in bd.items():
            dr = [r for r in valid if (r.get("dim") or "") == dkey]
            if dr:
                blk["correct"] = sum(1 for r in dr if r.get("correct"))
                blk["n"] = len(dr)
                old_n = blk.get("n")
        s["correct"] = cor
        s["n_valid"] = len(valid)
    else:
        s["correct"] = cor
        s["n_valid"] = len(valid)
        s["n_errors"] = len(recs) - len(valid)
        s["accuracy"] = (cor / len(valid)) if valid else 0.0
    # A file whose verdicts did not change is NOT a rescore. Writing it as one
    # made every untouched row look verified against the current judge - which
    # is exactly the silent failure this tool exists to prevent.
    s["rescore_note"] = ("用当前判分器重算 %d 条记录,翻转 %d 题" % (touched, len(changed))
                         if touched else "没有一条记录可重算(缺少对应题库行)")
    if not touched:
        # 一条都没重算就别生成文件。旧行为照样写 _rescored.json,
        # 于是"看起来已用当前判分器验证过"的文件其实是旧判决的副本。
        return {"dim": dim, "old": None, "new": cor, "n": len(valid),
                "flips": [], "out": None,
                "skipped": "没有一条记录可重算(缺少对应题库行)"}
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
        if r.get("skipped"):
            print("%s: 跳过(%s)" % (os.path.basename(p), r["skipped"]))
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