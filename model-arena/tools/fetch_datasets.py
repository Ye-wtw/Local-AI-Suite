# -*- coding: utf-8 -*-
"""fetch_datasets.py - download authoritative benchmarks (run where internet is available).
Each fetch is cached to datasets/authoritative/<name>.jsonl; re-running is a no-op for
completed downloads. Use --limit to cap items per set (cost control).
Requires: pip install datasets (HuggingFace hub). If HF is blocked, try HF_ENDPOINT=https://hf-mirror.com.
Usage: python tools/fetch_datasets.py [--limit 200] [--only mmlu_pro,aime]
"""
import os, sys, json, argparse, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH = os.path.join(ROOT, "datasets", "authoritative")
os.makedirs(AUTH, exist_ok=True)

def cache_path(name):
    return os.path.join(AUTH, name + ".jsonl")

def emit(name, rows, limit):
    path = cache_path(name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print("  [cached] %s (%d rows)" % (name, sum(1 for _ in open(path, encoding="utf-8"))))
        return path
    with open(path, "w", encoding="utf-8") as f:
        for r in rows[:limit]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("  [saved] %s (%d rows)" % (name, min(len(rows), limit)))
    return path

def fetch_mmlu_pro(limit):
    from datasets import load_dataset
    ds = load_dataset("TIGER-Lab/MMLU-Pro", split="test")
    rows = []
    for item in ds:
        opts = {chr(65+i): item["options"][i] for i in range(len(item["options"]))}
        rows.append({"id": "mmlupro_%s" % hashlib.md5(item["question"].encode()).hexdigest()[:10],
                     "dim": "D2", "source": "mmlu-pro", "license": "MIT",
                     "subcat": "knowledge", "type": "mcq", "question": item["question"],
                     "options": opts, "answer_letter": item["answer"],
                     "category": item.get("category", ""), "notes": "TIGER-Lab/MMLU-Pro test split"})
    emit("D2_mmlu_pro", rows, limit)

def fetch_aime(limit):
    from datasets import load_dataset
    ds = load_dataset("HuggingFaceH4/aime_2024", split="train")
    rows = []
    for i, item in enumerate(ds):
        rows.append({"id": "aime_%04d" % i, "dim": "D3", "source": "aime-2024", "license": "MIT",
                     "subcat": "competition-math", "type": "math",
                     "question": item["problem"], "answer": item["answer"],
                     "tolerance": "aime-exact", "notes": "AIME 2024"})
    emit("D3_aime", rows, limit)

def fetch_gsm8k(limit):
    from datasets import load_dataset
    ds = load_dataset("openai/gsm8k", "main", split="test")
    rows = []
    for i, item in enumerate(ds):
        rows.append({"id": "gsm8k_%04d" % i, "dim": "D3", "source": "gsm8k", "license": "MIT",
                     "subcat": "word-problem", "type": "math",
                     "question": item["question"], "answer": item["answer"].split("####")[-1].strip(),
                     "tolerance": "exact-string", "notes": "GSM8K test"})
    emit("D3_gsm8k", rows, limit)

def fetch_humaneval(limit):
    from datasets import load_dataset
    ds = load_dataset("openai_humaneval", split="test")
    rows = []
    for i, item in enumerate(ds):
        rows.append({"id": "humaneval_%03d" % i, "dim": "D4", "source": "humaneval", "license": "MIT",
                     "subcat": "python", "prompt": item["prompt"],
                     "test": item["test"], "entry_point": item["entry_point"],
                     "scoring": "sandbox-pass@1", "notes": "HumanEval original"})
    emit("D4_humaneval", rows, limit)

def fetch_ifeval(limit):
    from datasets import load_dataset
    ds = load_dataset("google/IFEval", split="train")
    rows = []
    for i, item in enumerate(ds):
        rows.append({"id": "ifeval_%04d" % i, "dim": "D5", "source": "ifeval", "license": "MIT",
                     "subcat": "verifiable", "user": item["prompt"],
                     "expect": {"checkers": item["instruction_id_list"]},
                     "notes": "IFEval instruction"})
    emit("D5_ifeval", rows, limit)

def fetch_bfcl(limit):
    from datasets import load_dataset
    ds = load_dataset("gorilla-llm/BFCL", split="test")
    rows = []
    for i, item in enumerate(ds):
        rows.append({"id": "bfcl_%04d" % i, "dim": "D6", "source": "bfcl", "license": "Apache-2.0",
                     "subcat": "tool-call", "user": item.get("query", ""),
                     "tools": item.get("functions", []),
                     "expect": {"ground_truth": item.get("function_results", [])},
                     "scoring": "bfcl-ast-match"})
    emit("D6_bfcl", rows, limit)

FETCHERS = {
    "mmlu_pro": fetch_mmlu_pro,
    "aime": fetch_aime,
    "gsm8k": fetch_gsm8k,
    "humaneval": fetch_humaneval,
    "ifeval": fetch_ifeval,
    "bfcl": fetch_bfcl,
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="max items per set (cost control)")
    ap.add_argument("--only", default="", help="comma list of fetchers, default=all")
    args = ap.parse_args()
    only = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else list(FETCHERS)
    try:
        import datasets  # noqa
    except ImportError:
        print("ERROR: 'datasets' package missing. Run: pip install datasets")
        print("If HuggingFace is blocked, set: HF_ENDPOINT=https://hf-mirror.com")
        sys.exit(1)
    print("Fetching with limit=%d: %s" % (args.limit, ", ".join(only)))
    for name in only:
        if name in FETCHERS:
            print("[fetch] %s" % name)
            FETCHERS[name](args.limit)
        else:
            print("[skip] unknown fetcher: %s" % name)
    print("Done. Authoritative datasets in:", AUTH)

if __name__ == "__main__":
    main()
