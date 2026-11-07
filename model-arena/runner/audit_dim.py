# -*- coding: utf-8 -*-
"""audit_dim.py - 用当前判分器离线重判某个维度的失败题。

为什么需要：D6/D8 的实测经验是失败题里约一半是判分器问题。
不改代码、不花钱、不占 GPU 就能查出来。

Usage:
  python runner/audit_dim.py D9
  python runner/audit_dim.py D9 --limit 20
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def load_bank(dim):
    """判分依据的来源。

    D9 is the exception: its questions live in datasets/full/D9_full.jsonl but the
    per-question CHECKS live in datasets/d9_baseline.jsonl (judge_d9 reads a
    baseline record). Loading D9_full and calling judge_d9 therefore answers
    "no checks defined" for every question - which is a loader mistake, not a
    model failure, and would have looked like a huge judge defect.
    """
    if dim == "D9":
        # D9 judging criteria live in TWO places, and a run needs both:
        #   datasets/d9_baseline.jsonl   32 questions (d9_dsh_*)
        #   datasets/full/D9_full.jsonl  78 questions carry their own checks (d9m_*)
        # Loading only the baseline reported 78 questions as "missing from bank",
        # which reads like a data defect but is a loader that knows one path.
        out = {}
        for p in (os.path.join(ROOT, "datasets", "d9_baseline.jsonl"),
                  os.path.join(ROOT, "datasets", "full", "D9_full.jsonl")):
            if not os.path.exists(p):
                continue
            for line in open(p, encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                qid = d.get("id")
                if not qid:
                    continue
                # a record that already has checks wins over one that does not
                if qid not in out or (d.get("checks") and not out[qid].get("checks")):
                    out[qid] = d
        return out
    p = os.path.join(ROOT, "datasets", "full", "%s_full.jsonl" % dim)
    if not os.path.exists(p):
        return {}
    out = {}
    for line in open(p, encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            out[d.get("id")] = d
    return out


def collect_failures(dim, prefer_latest=True):
    """从所有结果文件里收集该维度的失败记录（带 response）。"""
    seen = {}
    files = []
    for f in glob.glob(os.path.join(ROOT, "results", "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        s = d.get("summary") if isinstance(d.get("summary"), dict) else d
        ep = (s.get("endpoint") if isinstance(s, dict) else None) or ""
        if "mock" in ep.lower():
            continue
        recs = d.get("records") or []
        if not recs:
            continue
        mt = os.path.getmtime(f)
        for r in recs:
            if not isinstance(r, dict):
                continue
            if (r.get("dim") or "") != dim:
                continue
            if r.get("correct"):
                continue
            if not r.get("response"):
                continue
            qid = r.get("id")
            if not qid:
                continue
            # 同一题保留最新的那次
            if qid not in seen or mt > seen[qid]["_mt"]:
                rec = dict(r)
                rec["_mt"] = mt
                rec["_file"] = os.path.basename(f)
                seen[qid] = rec
    return list(seen.values())


def rejudge(dim, entry, rec):
    """用当前判分器重判一条记录，返回 (correct, reason)。"""
    text = rec.get("response") or ""
    if dim == "D2":
        import judge as J
        v = J.judge_mcq(text, entry.get("answer_letter"), entry.get("options"))
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D3":
        import judge as J
        v = J.judge_math(text, str(entry.get("answer") or ""),
                         tolerance=entry.get("tolerance"))
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D4":
        if entry.get("public_tests"):
            import lcb_judge as L
            v = L.judge_lcb(entry, text)
        else:
            import code_judge as C
            v = C.judge_code(entry, text)
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D6":
        import agent_judge as A
        v = A.judge_agent(entry, {"tool_calls": rec.get("tool_calls") or []}, text)
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D7":
        import longctx_judge as L
        v = L.judge_longctx(entry, text) if hasattr(L, "judge_longctx") else {}
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D8":
        import reliability_judge as R
        v = R.judge_hallucination(entry, text)
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D9":
        import d9_judge as D
        v = D.judge_d9(entry, text)
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    if dim == "D10":
        import chess_judge as C
        fn = getattr(C, "judge_chess", None) or getattr(C, "judge", None)
        if not fn:
            return None, "no judge function"
        v = fn(entry, text)
        return bool(v.get("correct")), str(v.get("reason") or "")[:90]
    return None, "unsupported dim"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dim")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=3)
    a = ap.parse_args()

    bank = load_bank(a.dim)
    fails = collect_failures(a.dim)
    if a.limit:
        fails = fails[:a.limit]

    print("=" * 70)
    print("  %s 失败题复核 · 题库 %d 题 · 待复核 %d 题" % (a.dim, len(bank), len(fails)))
    print("=" * 70)

    changed = []
    still_bad = []
    no_entry = []
    unsupported = 0

    for rec in fails:
        qid = rec.get("id")
        entry = bank.get(qid)
        if not entry:
            no_entry.append(qid)
            continue
        ok, why = rejudge(a.dim, entry, rec)
        if ok is None:
            unsupported += 1
            continue
        if ok:
            changed.append((qid, why, rec))
        else:
            still_bad.append((qid, why, rec))

    print()
    print("  由错判正: %d" % len(changed))
    print("  仍判错  : %d" % len(still_bad))
    print("  题库缺失: %d" % len(no_entry))
    if unsupported:
        print("  判分器无入口: %d" % unsupported)

    if changed:
        print()
        print("  ---- 由错判正的题（判分器缺陷）----")
        for qid, why, rec in changed[:a.show]:
            print("  %s" % qid)
            print("     新判定: %s" % why)
            print("     回答  : %s" % (rec.get("response") or "")[:110].replace(chr(10), " "))

    if still_bad and a.show:
        print()
        print("  ---- 仍判错的题（前 %d 条，可能是真错）----" % min(a.show, len(still_bad)))
        for qid, why, rec in still_bad[:a.show]:
            print("  %s" % qid)
            print("     理由: %s" % why)
            print("     回答: %s" % (rec.get("response") or "")[:110].replace(chr(10), " "))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())