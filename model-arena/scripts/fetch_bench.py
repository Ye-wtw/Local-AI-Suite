# -*- coding: utf-8 -*-
"""fetch_bench.py - pull the 11 authoritative public benchmarks and normalise them
into one JSONL schema with provenance.

Why a separate script from fetch_public.py: these eleven are the sets the published
leaderboards quote (MMLU-Pro, GPQA-diamond, MATH-500, GSM8K, HumanEval,
LiveCodeBench, ARC-Challenge, BFCL v3, SimpleQA, TruthfulQA, IFEval). Five of them
only ship as Parquet, so runner/parquet_lite.py does the decoding with no
third-party dependency.

Every row carries the unified fields:
    id / dim / question / choices (optional) / answer / source / license /
    contamination_risk
plus per-benchmark extras. Multiple-choice rows additionally carry options
(A/B/C/D dict) and answer_letter, which is what runner/judge.judge_mcq consumes.

Usage:
  python scripts/fetch_bench.py --list
  python scripts/fetch_bench.py --only arc,humaneval
  python scripts/fetch_bench.py                 # every benchmark
  python scripts/fetch_bench.py --only gsm8k --force
"""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "runner"))

import fetch_public                      # reuse: option shuffling, grading helpers
import parquet_lite

OUT = os.path.join(ROOT, "datasets", "public")
CACHE = os.path.join(ROOT, "cache", "bench")
MIRROR = "https://hf-mirror.com"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
SEED = 20261010
NL = chr(10)

# name -> repo / file / license / dim / how to parse
BENCH = {
    "mmlu_pro": {
        "repo": "TIGER-Lab/MMLU-Pro", "file": "data/test-00000-of-00001.parquet",
        "license": "MIT", "dim": "D2", "kind": "parquet", "rows": 12032,
        "note": "MMLU-Pro:10 选 1 研究生级知识题,比 MMLU 更难更抗污染"},
    "gpqa": {
        "repo": "Wanfq/gpqa", "file": "gpqa_diamond.csv",
        "license": "CC-BY-4.0", "dim": "D2", "kind": "csv", "rows": 198,
        "note": "GPQA-diamond:PhD 撰写,Google-proof"},
    "math500": {
        "repo": "HuggingFaceH4/MATH-500", "file": "test.jsonl",
        "license": "MIT", "dim": "D3", "kind": "jsonl", "rows": 500,
        "note": "MATH-500:竞赛数学,带最终答案"},
    "gsm8k": {
        "repo": "openai/gsm8k", "file": "main/test-00000-of-00001.parquet",
        "license": "MIT", "dim": "D3", "kind": "parquet", "rows": 1319,
        "note": "GSM8K test:小学数学应用题"},
    "humaneval": {
        "repo": "openai/openai_humaneval", "file": "openai_humaneval/test-00000-of-00001.parquet",
        "license": "MIT", "dim": "D4", "kind": "parquet", "rows": 164,
        "note": "HumanEval:函数补全,带单测"},
    "livecodebench": {
        "repo": "livecodebench/code_generation_lite", "file": "test6.jsonl",
        "license": "Apache-2.0", "dim": "D4", "kind": "jsonl", "rows": 175,
        "note": "LiveCodeBench v6:竞赛编程,按日期切分抗污染"},
    "arc": {
        "repo": "allenai/ai2_arc", "file": "ARC-Challenge/test-00000-of-00001.parquet",
        "license": "Apache-2.0", "dim": "D2", "kind": "parquet", "rows": 1172,
        "note": "ARC-Challenge:小学科学推理,4 选 1"},
    "bfcl": {
        "repo": "gorilla-llm/Berkeley-Function-Calling-Leaderboard",
        "file": "BFCL_v3_exec_simple.json",
        "license": "Apache-2.0", "dim": "D6", "kind": "jsonl", "rows": 100,
        "note": "BFCL v3 exec-simple:函数调用,参数级判分"},
    "simpleqa": {
        "repo": "basicv8vc/SimpleQA", "file": "simple_qa_test_set.csv",
        "license": "MIT", "dim": "D8", "kind": "csv", "rows": 4326,
        "note": "SimpleQA:短事实问答,专测幻觉"},
    "truthfulqa": {
        "repo": "truthfulqa/truthful_qa",
        "file": "generation/validation-00000-of-00001.parquet",
        "license": "Apache-2.0", "dim": "D8", "kind": "parquet", "rows": 817,
        "note": "TruthfulQA generation:诱导性提问,测是否复述常见误解"},
    "ifeval": {
        "repo": "google/IFEval", "file": "ifeval_input_data.jsonl",
        "license": "Apache-2.0", "dim": "D5", "kind": "jsonl", "rows": 541,
        "note": "IFEval:可机器验证的指令遵循"},
}

ORDER = ["mmlu_pro", "gpqa", "math500", "gsm8k", "humaneval", "livecodebench",
         "arc", "bfcl", "simpleqa", "truthfulqa", "ifeval"]


# ------------------------------------------------------------------ transport
def cache_path(name):
    return os.path.join(CACHE, name + "_" + os.path.basename(BENCH[name]["file"]))


def download(name, force=False, timeout=300):
    """Fetch one benchmark file through the mirror, caching it under cache/bench."""
    spec = BENCH[name]
    path = cache_path(name)
    if os.path.exists(path) and not force:
        data = open(path, "rb").read()
        print("   cache  %-46s %8.2f MB" % (os.path.basename(path), len(data) / 1e6))
        return data
    url = "%s/datasets/%s/resolve/main/%s" % (MIRROR, spec["repo"], spec["file"])
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    data = urllib.request.urlopen(req, timeout=timeout, context=CTX).read()
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    print("   down   %-46s %8.2f MB  %.1fs"
          % (os.path.basename(path), len(data) / 1e6, time.time() - t0))
    return data


def common(name, extra=None):
    spec = BENCH[name]
    row = {
        "source": "public:" + name,
        "source_name": name,
        "license": spec["license"],
        "contamination_risk": "high",
        "repo": spec["repo"],
        "file": spec["file"],
    }
    if extra:
        row.update(extra)
    return row


def mcq(options, letter):
    """Emit both the unified choices list and the runner's options/answer_letter."""
    letters = sorted(options)
    return {"choices": [options[k] for k in letters],
            "options": options,
            "answer_letter": letter}


# -------------------------------------------------------------------- parsers
def parse_mmlu_pro(name, data, rng):
    t = parquet_lite.read_parquet(data=data)
    cols = t.columns
    rows = []
    letters = "ABCDEFGHIJ"
    for i in range(t.num_rows):
        opts_raw = cols["options"][i] or []
        opts = {}
        for k, txt in enumerate(opts_raw):
            txt = (txt or "").strip()
            if txt:
                opts[letters[k]] = txt
        ans_i = cols["answer_index"][i]
        ans_txt = (cols["answer"][i] or "").strip()
        if not opts:
            continue
        letter = letters[ans_i] if isinstance(ans_i, int) and 0 <= ans_i < len(letters) else ans_txt
        if letter not in opts:
            continue
        q = (cols["question"][i] or "").strip()
        if not q:
            continue
        rows.append(dict(
            {"id": "pub_mmlu_pro_%s" % cols["question_id"][i], "dim": "D2",
             "subcat": "public-mmlu-pro", "question": q,
             "answer": opts[letter], "answer_index": ans_i,
             "category": (cols.get("category") or [None] * t.num_rows)[i]},
            **mcq(opts, letter), **common(name)))
    return rows


def parse_gsm8k(name, data, rng):
    t = parquet_lite.read_parquet(data=data)
    rows = []
    for i in range(t.num_rows):
        q = (t.columns["question"][i] or "").strip()
        a = (t.columns["answer"][i] or "")
        if not q:
            continue
        # the reference answer ends with "#### <number>"
        final = a.rsplit("####", 1)[-1].strip() if "####" in a else a.strip().splitlines()[-1:] or [""]
        if isinstance(final, list):
            final = final[0] if final else ""
        rows.append({"id": "pub_gsm8k_%04d" % i, "dim": "D3", "subcat": "public-gsm8k",
                     "question": q, "answer": str(final).strip(),
                     "solution": a, "tolerance": "0.01", **common(name)})
    return rows


def parse_humaneval(name, data, rng):
    t = parquet_lite.read_parquet(data=data)
    rows = []
    for i in range(t.num_rows):
        tid = t.columns["task_id"][i]
        rows.append({"id": "pub_humaneval_%s" % str(tid).replace("/", "_"),
                     "dim": "D4", "subcat": "public-humaneval",
                     "question": t.columns["prompt"][i],
                     "answer": t.columns["canonical_solution"][i],
                     "entry_point": t.columns["entry_point"][i],
                     "test": t.columns["test"][i],
                     "expect": {"code": True, "entry_point": t.columns["entry_point"][i]},
                     **common(name)})
    return rows


def parse_arc(name, data, rng):
    t = parquet_lite.read_parquet(data=data)
    cols = t.columns
    ch = cols["choices"]
    rows = []
    for i in range(t.num_rows):
        texts = (ch.get("text") or [None])[i] or []
        labels = (ch.get("label") or [None])[i] or []
        opts = dict((str(lab).strip().upper(), (txt or "").strip())
                    for lab, txt in zip(labels, texts) if txt)
        key = str(cols["answerKey"][i] or "").strip().upper()
        q = (cols["question"][i] or "").strip()
        if not q or key not in opts:
            continue
        rows.append(dict(
            {"id": "pub_arc_%s" % cols["id"][i], "dim": "D2",
             "subcat": "public-arc-challenge", "question": q, "answer": opts[key]},
            **mcq(opts, key), **common(name)))
    return rows


def parse_truthfulqa(name, data, rng):
    t = parquet_lite.read_parquet(data=data)
    cols = t.columns
    rows = []
    for i in range(t.num_rows):
        q = (cols["question"][i] or "").strip()
        if not q:
            continue
        best = (cols["best_answer"][i] or "").strip()
        rows.append({"id": "pub_truthfulqa_%04d" % i, "dim": "D8",
                     "subcat": "public-truthfulqa", "question": q,
                     "answer": best,
                     "correct_answers": cols["correct_answers"][i] or [],
                     "incorrect_answers": cols["incorrect_answers"][i] or [],
                     "qa_type": cols["type"][i], "category": cols["category"][i],
                     **common(name)})
    return rows


def parse_gpqa(name, data, rng):
    txt = data.decode("utf-8", "ignore")
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(txt))):
        q = (r.get("Question") or "").strip()
        correct = (r.get("Correct Answer") or "").strip()
        wrongs = [(r.get("Incorrect Answer %d" % k) or "").strip() for k in (1, 2, 3)]
        if not q or not correct or not all(wrongs):
            continue
        # GPQA ships a fixed correct/wrong split; shuffle positions or a model could
        # score 100% by always answering "A". Seeded, so it stays reproducible.
        opts, letter = fetch_public._place_options(correct, wrongs, rng)
        rows.append(dict(
            {"id": "pub_gpqa_%04d" % i, "dim": "D2", "subcat": "public-gpqa",
             "question": q, "answer": correct,
             "domain": (r.get("High-level domain") or "").strip()},
            **mcq(opts, letter), **common(name)))
    return rows


def parse_math500(name, data, rng):
    rows = []
    for i, line in enumerate(data.decode("utf-8", "ignore").splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        ans = str(d.get("answer") or "").strip()
        if not ans:
            continue
        rows.append({"id": "pub_math500_%04d" % i, "dim": "D3",
                     "subcat": "public-math500",
                     "question": (d.get("problem") or "").strip(),
                     "answer": ans, "solution": d.get("solution") or "",
                     "tolerance": "0.01",
                     "subject": d.get("subject"), "level": d.get("level"),
                     "gradeable": fetch_public._gradeable(ans), **common(name)})
    return rows


def parse_ifeval(name, data, rng):
    rows = []
    for i, line in enumerate(data.decode("utf-8", "ignore").splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        rows.append({"id": "pub_ifeval_%04d" % i, "dim": "D5",
                     "subcat": "public-ifeval",
                     "question": (d.get("prompt") or "").strip(),
                     "answer": "",
                     "expect": {"ifeval": True,
                                "instruction_id_list": d.get("instruction_id_list") or [],
                                "kwargs": d.get("kwargs") or []},
                     "key": d.get("key"), **common(name)})
    return rows


def parse_livecodebench(name, data, rng):
    rows = []
    for i, line in enumerate(data.decode("utf-8", "ignore").splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        q = (d.get("question_content") or "").strip()
        if not q:
            continue
        tests = d.get("public_test_cases") or "[]"
        if isinstance(tests, str):
            try:
                tests = json.loads(tests)
            except Exception:
                tests = []
        rows.append({"id": "pub_lcb_%s" % (d.get("question_id") or i), "dim": "D4",
                     "subcat": "public-livecodebench", "question": q,
                     "answer": "",
                     "title": d.get("question_title"),
                     "starter_code": d.get("starter_code") or "",
                     "public_tests": tests[:3],
                     "difficulty": d.get("difficulty"),
                     "platform": d.get("platform"),
                     "contest_date": d.get("contest_date"),
                     **common(name)})
    return rows


def parse_simpleqa(name, data, rng):
    txt = data.decode("utf-8", "ignore")
    rdr = csv.DictReader(io.StringIO(txt))
    rows = []
    for i, r in enumerate(rdr):
        low = dict((k.lower().strip(), v) for k, v in r.items() if k)
        q = (low.get("problem") or low.get("question") or "").strip()
        a = (low.get("answer") or "").strip()
        if not q:
            continue
        rows.append({"id": "pub_simpleqa_%04d" % i, "dim": "D8",
                     "subcat": "public-simpleqa", "question": q, "answer": a,
                     "topic": low.get("topic"), "answer_type": low.get("answer_type"),
                     "multi_step": low.get("multi_step"),
                     **common(name)})
    return rows


def parse_bfcl(name, data, rng):
    import re as _re
    rows = []
    for line in data.decode("utf-8", "ignore").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        q = d.get("question") or []
        if isinstance(q, list):
            parts = []
            for m in q:
                if isinstance(m, dict):
                    parts.append(str(m.get("content") or ""))
                elif isinstance(m, list):
                    for x in m:
                        if isinstance(x, dict):
                            parts.append(str(x.get("content") or ""))
            q = " ".join(x for x in parts if x).strip()
        funcs = d.get("function") or []
        gt = d.get("ground_truth") or []
        if not q or not funcs or not gt:
            continue
        m = _re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$", str(gt[0]), _re.S)
        if not m:
            continue
        want_name = m.group(1)
        want_args = fetch_public._parse_call_args(m.group(2))
        rows.append({"id": "pub_bfcl_" + str(d.get("id") or len(rows)), "dim": "D6",
                     "subcat": "public-bfcl", "question": q,
                     "answer": str(gt[0]),
                     "tools": [fetch_public._normalize_tool(f) for f in funcs],
                     "expect": {"should_call": [want_name], "must_not_call": [],
                                "bfcl_name": want_name, "bfcl_args": want_args,
                                "bfcl_check_args": True,
                                "reason": "BFCL exec-simple:需调用指定函数且参数匹配"},
                     **common(name)})
    return rows


PARSERS = {
    "mmlu_pro": parse_mmlu_pro, "gpqa": parse_gpqa, "math500": parse_math500,
    "gsm8k": parse_gsm8k, "humaneval": parse_humaneval,
    "livecodebench": parse_livecodebench, "arc": parse_arc, "bfcl": parse_bfcl,
    "simpleqa": parse_simpleqa, "truthfulqa": parse_truthfulqa,
    "ifeval": parse_ifeval,
}


# ----------------------------------------------------------------------- main
def local_rows(name):
    p = os.path.join(OUT, name + ".jsonl")
    if not os.path.exists(p):
        return None
    n = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def cmd_list():
    print("%-14s %-6s %-12s %8s  %s" % ("benchmark", "dim", "license", "rows", "local"))
    print("-" * 78)
    total = 0
    for name in ORDER:
        spec = BENCH[name]
        n = local_rows(name)
        mark = ("yes (%d)" % n) if n is not None else "no"
        if n is None:
            mark = "no"
        print("%-14s %-6s %-12s %8d  %s" % (name, spec["dim"], spec["license"],
                                            spec["rows"], mark))
        total += 1
    print("-" * 78)
    have = sum(1 for x in ORDER if local_rows(x) is not None)
    print("%d benchmarks, %d already downloaded -> datasets/public/" % (total, have))


def load_prov():
    p = os.path.join(OUT, "_provenance.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def run(names, force=False):
    os.makedirs(OUT, exist_ok=True)
    rng = random.Random(SEED)
    prov = load_prov()
    failed = []
    for name in names:
        if name not in BENCH:
            print("skip unknown benchmark: %s" % name)
            continue
        spec = BENCH[name]
        print("[%s] %s" % (name, spec["note"]))
        try:
            data = download(name, force=force)
            rows = PARSERS[name](name, data, rng)
        except parquet_lite.UnsupportedEncoding as e:
            print("   FAILED (parquet): %s" % e)
            failed.append((name, str(e)))
            continue
        except Exception as e:
            print("   FAILED: %s: %s" % (type(e).__name__, str(e)[:160]))
            failed.append((name, "%s: %s" % (type(e).__name__, str(e)[:160])))
            continue
        if not rows:
            print("   0 rows, skip")
            failed.append((name, "0 rows"))
            continue
        out = os.path.join(OUT, name + ".jsonl")
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + NL)
        prov[name] = {
            "rows": len(rows), "dim": spec["dim"], "repo": spec["repo"],
            "file": spec["file"], "license": spec["license"],
            "fetched": time.strftime("%Y-%m-%d"),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()[:16],
            "url": "%s/datasets/%s/resolve/main/%s" % (MIRROR, spec["repo"], spec["file"]),
        }
        delta = "" if len(rows) == spec["rows"] else "   (expected %d)" % spec["rows"]
        print("   -> %d rows  datasets/public/%s.jsonl%s" % (len(rows), name, delta))
    with open(os.path.join(OUT, "_provenance.json"), "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
    print()
    print("provenance -> datasets/public/_provenance.json (%d entries)" % len(prov))
    if failed:
        print("FAILED: %s" % ", ".join(n for n, _ in failed))
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma separated benchmark names")
    ap.add_argument("--list", action="store_true", help="show the 11 benchmarks and local state")
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return 0
    names = [x.strip() for x in args.only.split(",") if x.strip()] or list(ORDER)
    return run(names, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
