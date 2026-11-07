# -*- coding: utf-8 -*-
"""rejudge.py - re-score a stored LEVEL result with the CURRENT judges.
Result files keep every raw response, so after fixing a judge you can re-score
without paying for the model again.

Usage:
  python runner/rejudge.py --in results/LEVEL_smoke_deepseek_xxx.json --tag rejudged
  python runner/rejudge.py --latest --level smoke --tag rejudged
"""
import argparse, glob, json, os, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from run import _judge_for, load_jsonl   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--level", default="smoke")
    ap.add_argument("--tag", default="rejudged")
    ap.add_argument("--write", action="store_true", help="persist a new result file")
    args = ap.parse_args()

    path = args.inp
    if not path:
        cands = sorted(glob.glob(os.path.join(ROOT, "results",
                                              "LEVEL_%s_*.json" % args.level)),
                       key=os.path.getmtime)
        cands = [c for c in cands if args.tag not in os.path.basename(c)]
        if not cands:
            print("no result file found for level", args.level)
            return
        path = cands[-1]
    d = json.load(open(path, encoding="utf-8"))
    lvl = d.get("summary", {}).get("level") or args.level
    lvl_path = os.path.join(ROOT, "datasets", "subsets", "%s.jsonl" % lvl)
    rows = {r["id"]: r for r in load_jsonl(lvl_path)}

    before = d.get("summary", {}).get("correct", 0)
    t0 = time.perf_counter()
    changed, records = [], []
    for rec in d.get("records", []):
        q = rows.get(rec["id"])
        if not q:
            records.append(rec); continue
        text = rec.get("response") or ""
        result = {"text": text, "tool_calls": rec.get("tool_calls") or []}
        ok, detail = _judge_for(q, text, result)
        if bool(ok) != bool(rec.get("correct")):
            changed.append((rec["id"], rec.get("correct"), bool(ok), detail))
        rec = dict(rec)
        rec["correct"] = bool(ok)
        rec["detail"] = detail
        records.append(rec)

    correct = sum(1 for x in records if x["correct"])
    by_dim = {}
    for x in records:
        dd = x.get("dim") or "?"
        b = by_dim.setdefault(dd, {"n": 0, "correct": 0})
        b["n"] += 1; b["correct"] += 1 if x["correct"] else 0
    s = dict(d.get("summary") or {})
    s.update({"correct": correct, "accuracy": correct / len(records) if records else 0.0,
              "by_dim": by_dim, "rejudged_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    print("=" * 62)
    print("  重判  %s" % os.path.basename(path))
    print("=" * 62)
    print("  重判前 %d/%d  重判后 %d/%d  (%+.0f 题)"
          % (before, len(records), correct, len(records), correct - before))
    for dd, b in sorted(by_dim.items()):
        print("      %-5s %2d/%2d  %3.0f%%" % (dd, b["correct"], b["n"], 100 * b["correct"] / b["n"]))
    if changed:
        print("  判定发生变化的题:")
        for qid, was, now, detail in changed:
            print("    %-22s %s -> %s  | %s" % (qid, was, now,
                  json.dumps(detail, ensure_ascii=False)[:70]))
    if args.write:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + args.tag
        out = os.path.join(ROOT, "results",
                           "LEVEL_%s_%s_%s.json" % (lvl, s.get("endpoint", "ep"), stamp))
        # A rejudged file MUST carry the NEW judge version. Without it the output
        # is indistinguishable from the stale file it replaces, and the
        # leaderboard has no way to prefer the corrected numbers.
        try:
            import judge_version as JV
            s["judge_version"] = JV.judge_version()
            s["rejudged_from"] = os.path.basename(path)
        except Exception:
            pass
        json.dump({"summary": s, "records": records,
                   "judge_version": s.get("judge_version")},
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("  -> %s  (judge_version=%s)" % (os.path.relpath(out, ROOT),
                                               s.get("judge_version", "?")))
    else:
        print("  (未写盘;加 --write 保存重判结果)")


if __name__ == "__main__":
    main()
