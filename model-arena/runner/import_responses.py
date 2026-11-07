# -*- coding: utf-8 -*-
"""import_responses.py - INGEST externally-produced answers and judge them with the
SAME rule-based judges used for local models, producing a normal LEVEL result file.

Pairs with export_prompts.py. The response file is JSONL: {"id":..., "text":..., "tool_calls":[...]?}
Usage:
  python runner/import_responses.py --level smoke --responses cloud_smoke_responses.jsonl \
      --model-name "cloud-assistant" --tag cloud
"""
import argparse, json, os, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
NL = chr(10)

from run import _judge_for, _request_for, load_jsonl   # noqa: E402  (same judging path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="smoke")
    ap.add_argument("--responses", required=True)
    ap.add_argument("--model-name", default="cloud-assistant")
    ap.add_argument("--tag", default="cloud")
    args = ap.parse_args()

    lvl_path = os.path.join(ROOT, "datasets", "subsets", "%s.jsonl" % args.level)
    rows = {r["id"]: r for r in load_jsonl(lvl_path)}
    resp = {}
    for line in open(args.responses, encoding="utf-8"):
        line = line.strip()
        if line:
            d = json.loads(line)
            resp[d["id"]] = d

    records = []
    t0 = time.perf_counter()
    for qid, r in rows.items():
        if qid not in resp:
            continue
        ans = resp[qid]
        text = ans.get("text") or ""
        result = {"text": text, "tool_calls": ans.get("tool_calls") or []}
        ok, detail = _judge_for(r, text, result)
        records.append({"id": qid, "dim": r.get("dim"), "subcat": r.get("subcat"),
                        "response": text, "tool_calls": result["tool_calls"],
                        "correct": bool(ok), "detail": detail})
    dt = time.perf_counter() - t0

    correct = sum(1 for x in records if x["correct"])
    by_dim = {}
    for x in records:
        d = x.get("dim") or "?"
        b = by_dim.setdefault(d, {"n": 0, "correct": 0})
        b["n"] += 1; b["correct"] += 1 if x["correct"] else 0
    summary = {"level": args.level, "endpoint": "cloud", "model": args.model_name,
               "n": len(records), "correct": correct,
               "accuracy": correct / len(records) if records else 0.0,
               "elapsed_s": dt, "by_dim": by_dim}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + args.tag
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    out = os.path.join(ROOT, "results", "LEVEL_%s_cloud_%s.json" % (args.level, stamp))
    json.dump({"summary": summary, "records": records}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print("=" * 62)
    print("  云端作答判分  level=%s  model=%s" % (args.level, args.model_name))
    print("=" * 62)
    print("  题数 %d  通过 %d  准确率 %.1f%%" % (len(records), correct,
                                                summary["accuracy"] * 100))
    for d, b in sorted(by_dim.items()):
        print("      %-5s %2d/%2d  %3.0f%%" % (d, b["correct"], b["n"], 100 * b["correct"] / b["n"]))
    print(NL + "  -> %s" % os.path.relpath(out, ROOT))
    print("  下一步: python runner/build_report.py && python runner/make_leaderboard.py")
    for x in records:
        if not x["correct"]:
            print("     [fail] %-22s %s" % (x["id"], json.dumps(x["detail"], ensure_ascii=False)[:80]))


if __name__ == "__main__":
    main()
