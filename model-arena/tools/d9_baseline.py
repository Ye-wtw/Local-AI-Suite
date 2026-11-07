# -*- coding: utf-8 -*-
"""d9_baseline.py - (re)generate the D9 baseline answers with any endpoint.
The one-time cloud run: D9 is the only dimension that would otherwise need a paid
LLM judge on every evaluation. Instead we freeze ONE good answer set, distill it into
machine-checkable 'checks', and score local models with rules (zero marginal cost).

Usage:
  python scripts/d9_baseline.py --endpoint deepseek            # regenerate answers
  python scripts/d9_baseline.py --endpoint deepseek --dry-run  # show, do not write
  python scripts/d9_baseline.py --selfcheck                    # verify existing baseline

Notes:
  - 'checks' are NEVER overwritten: only 'baseline_answer' / 'baseline_source' change.
  - After regenerating, the script re-runs each answer through the judge and reports
    coverage, so you can see whether the new baseline meets the bar.
"""
import argparse, json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "runner"))

from endpoint import load_endpoints, EndpointError   # noqa: E402
from d9_judge import judge_d9                          # noqa: E402

NL = chr(10)
BASE = os.path.join(ROOT, "datasets", "d9_baseline.jsonl")
QUESTIONS = os.path.join(ROOT, "datasets", "full", "D9_full.jsonl")

SYSTEM = ("你是一个严谨的助手。请直接完成任务,给出可核对的结论与关键数字;"
          "不确定的地方要明确标注,不要编造。回答用中文,结构清晰。")


def load_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def save_jsonl(p, rows):
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + NL)


def selfcheck():
    rows = load_jsonl(BASE)
    ok = 0
    skipped = 0
    for r in rows:
        if not r.get("checks"):
            skipped += 1
            print("SKIP %-14s 尚无 checks(需先提炼)" % r["id"])
            continue
        res = judge_d9(r, r.get("baseline_answer", ""))
        ok += res["correct"]
        flag = "OK  " if res["correct"] else "FAIL"
        print("%s %-14s %s" % (flag, r["id"], res["reason"][:70]))
        if not res["correct"]:
            for c in res["checks"]:
                if not c["ok"]:
                    print("       missing: %s | %s" % (c["label"], c["note"]))
    total = len(rows) - skipped
    print("%sbaseline self-check: %d/%d%s" % (NL, ok, total,
          (" (跳过 %d 题无 checks)" % skipped) if skipped else ""))
    return total > 0 and ok == total
    print("%sbaseline self-check: %d/%d" % (NL, ok, len(rows)))
    return ok == len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="", help="endpoint profile name")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "endpoints.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.selfcheck or not args.endpoint:
        if not os.path.exists(BASE):
            print("no baseline file yet; run with --endpoint <name> first")
            return
        selfcheck()
        if not args.endpoint:
            return

    endpoints, _ = load_endpoints(args.config)
    if args.endpoint not in endpoints:
        print("unknown endpoint %r; available: %s" % (args.endpoint, ", ".join(endpoints)))
        return
    ep = endpoints[args.endpoint]
    ok, detail = ep.health()
    print("endpoint %s: %s | %s" % (ep.name, "OK" if ok else "UNREACHABLE", detail))
    if not ok:
        return

    base = {r["id"]: r for r in load_jsonl(BASE)} if os.path.exists(BASE) else {}
    qs = load_jsonl(QUESTIONS)
    if args.limit:
        qs = qs[:args.limit]
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    out = []
    for i, q in enumerate(qs, 1):
        rec = dict(base.get(q["id"], {"id": q["id"]}))
        rec.setdefault("checks", [])
        try:
            res = ep.chat([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": q["user"]}])
            rec["baseline_answer"] = res["text"]
            rec["baseline_source"] = "%s-%s" % (ep.name, stamp)
            verdict = judge_d9(rec, res["text"]) if rec["checks"] else {"reason": "no checks"}
            print("  %2d/%2d %-14s %s" % (i, len(qs), q["id"], verdict["reason"][:60]))
        except EndpointError as e:
            print("  %2d/%2d %-14s ERROR %s" % (i, len(qs), q["id"], str(e)[:60]))
            rec.setdefault("baseline_answer", "")
        out.append(rec)

    if args.dry_run:
        print(NL + "(dry-run: nothing written)")
        return
    save_jsonl(BASE, out)
    print(NL + "written: %s (%d records)" % (os.path.relpath(BASE, ROOT), len(out)))
    print(NL + "self-check on the new baseline:")
    selfcheck()


if __name__ == "__main__":
    main()
