# -*- coding: utf-8 -*-
"""cost_report.py - estimate API spend from result files (for budget control).
Prices are per 1M tokens and configurable in config/prices.json.
Usage: python runner/cost_report.py [--level smoke]
"""
import argparse, glob, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = {"deepseek": {"in": 2.0, "out": 8.0, "cache_hit_in": 0.5, "currency": "CNY"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="")
    args = ap.parse_args()
    ppath = os.path.join(ROOT, "config", "prices.json")
    prices = json.load(open(ppath, encoding="utf-8")) if os.path.exists(ppath) else DEFAULT
    pats = [os.path.join(ROOT, "results", "LEVEL_%s*.json" % args.level)] if args.level            else [os.path.join(ROOT, "results", "LEVEL_*.json")]
    tot = 0.0
    print("%-34s %-8s %10s %10s %12s" % ("file", "model", "in_tok", "out_tok", "est_cost"))
    print("-" * 78)
    for p in sorted(glob.glob(pats[0])):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        s = d.get("summary") or {}
        ep = s.get("endpoint") or ""
        recs = d.get("records") or []
        pin = s.get("prompt_tokens") or sum((r.get("prompt_tokens") or 0) for r in recs)
        pout = s.get("completion_tokens") or sum((r.get("completion_tokens") or 0) for r in recs)
        if not pin:
            continue
        pr = prices.get(ep) or {"in": 0.0, "out": 0.0}
        c = pin / 1e6 * pr.get("in", 0) + pout / 1e6 * pr.get("out", 0)
        tot += c
        print("%-34s %-8s %10d %10d %11.4f" % (os.path.basename(p)[:34], ep, pin, pout, c))
    print("-" * 78)
    cur = "CNY"
    for v in prices.values():
        if isinstance(v, dict) and v.get("currency"):
            cur = v["currency"]; break
    print("  合计估算: %.4f %s(价格见 config/prices.json,仅本地估算)" % (tot, cur))


if __name__ == "__main__":
    main()
