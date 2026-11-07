# -*- coding: utf-8 -*-
"""cost.py - estimate what a run will cost BEFORE spending money on it.

Why this exists: D7 (long context) is 120 of 4,054 questions but accounts for
about 65% of the API bill, because 60 of them need 128k-256k of context each.
A user who clicks "run everything" against a cloud endpoint pays for that
without being told. Printing the estimate first turns an invisible 15 CNY into
a decision.

Prices are the DeepSeek public list price at the time of writing. They are a
parameter, not a constant to trust: pass your own if you use another provider.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# CNY per million tokens. Conservative: input slightly above list, output on par.
PRICE_IN = 1.0
PRICE_OUT = 2.0

# Typical output length per question, by suite kind. Code answers are long,
# multiple-choice answers are a letter plus a sentence.
OUT_TOKENS = {
    "mcq": 40, "math": 250, "code": 600, "agent": 400, "longctx": 150,
    "reliability": 300, "realworld": 400, "dynamic": 300, "chess": 60,
    "instruction": 400, "compression": 500, "vision": 200, "default": 300,
}

CHARS_PER_TOKEN = 2.5      # mixed zh/en, conservative


def _load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _qtext(row):
    q = row.get("question") or row.get("prompt") or row.get("user") or ""
    if isinstance(q, list):
        q = " ".join(str(x) for x in q)
    return str(q)


def estimate_suite(path, kind="default"):
    """Token and cost estimate for one dataset file."""
    rows = _load(path)
    if not rows:
        return None
    in_chars = sum(len(_qtext(r)) for r in rows)
    # A row that declares min_ctx needs that much context even when the question
    # text is short - the material is generated at runtime. Counting only the
    # question text would under-report D7 by two orders of magnitude.
    in_ctx = sum(r.get("min_ctx") or 0 for r in rows)
    in_tokens = max(in_chars / CHARS_PER_TOKEN, in_ctx)
    out_tokens = len(rows) * OUT_TOKENS.get(kind, OUT_TOKENS["default"])
    cost = in_tokens / 1e6 * PRICE_IN + out_tokens / 1e6 * PRICE_OUT
    return {"file": os.path.basename(path), "n": len(rows),
            "in_tokens": round(in_tokens), "out_tokens": round(out_tokens),
            "cost_cny": round(cost, 2)}


def _dim_kind(dim):
    return {"D2": "mcq", "D3": "math", "D4": "code", "D5": "instruction",
            "D6": "agent", "D7": "longctx", "D8": "reliability",
            "D9": "realworld", "D10": "chess"}.get(dim, "default")


def estimate_all():
    """Estimate every dataset file, grouped so the D7 outlier is visible."""
    out = []
    for f in sorted(os.listdir(os.path.join(ROOT, "datasets", "full"))):
        if not f.endswith(".jsonl"):
            continue
        dim = f.split("_")[0]
        e = estimate_suite(os.path.join(ROOT, "datasets", "full", f),
                           _dim_kind(dim))
        if e:
            e["dim"] = dim
            out.append(e)
    pub = os.path.join(ROOT, "datasets", "subsets", "public_balanced.jsonl")
    e = estimate_suite(pub, "mcq")
    if e:
        e["dim"] = "PUBLIC"
        out.append(e)
    return out


def summary():
    rows = estimate_all()
    total_n = sum(r["n"] for r in rows)
    total_cost = sum(r["cost_cny"] for r in rows)
    no_d7 = [r for r in rows if r["dim"] != "D7"]
    return {
        "rows": rows,
        "total_n": total_n,
        "total_cost_cny": round(total_cost, 2),
        "without_d7_n": sum(r["n"] for r in no_d7),
        "without_d7_cost_cny": round(sum(r["cost_cny"] for r in no_d7), 2),
        "d7_cost_cny": round(sum(r["cost_cny"] for r in rows if r["dim"] == "D7"), 2),
    }


if __name__ == "__main__":
    s = summary()
    print("%-8s %6s %12s %10s %8s" % ("维度", "题数", "输入token", "输出token", "成本元"))
    for r in s["rows"]:
        print("%-8s %6d %12d %10d %8.2f"
              % (r["dim"], r["n"], r["in_tokens"], r["out_tokens"], r["cost_cny"]))
    print("-" * 50)
    print("%-8s %6d %12s %10s %8.2f" % ("合计", s["total_n"], "", "", s["total_cost_cny"]))
    print()
    print("不含 D7: %d 题, %.2f 元" % (s["without_d7_n"], s["without_d7_cost_cny"]))
    print("仅 D7  : %.2f 元（长上下文是成本主因）" % s["d7_cost_cny"])