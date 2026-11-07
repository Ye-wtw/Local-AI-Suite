# -*- coding: utf-8 -*-
"""fetch_public.py - import PUBLIC benchmarks so our numbers can be compared with
published tables (DeepSeek's news table, Artificial Analysis, papers).

Every imported question carries provenance: repo, file, license, fetch date, revision.
That matters because a fine-tuned model may have trained on these sets - see
runner/contamination.py for the paraphrase probe.

Source host: hf-mirror.com (huggingface.co is unreachable from this machine).

Usage:
  python scripts/fetch_public.py --list
  python scripts/fetch_public.py --only mmlu,gpqa,math500,ifeval,bfcl
  python scripts/fetch_public.py --only mmlu --mmlu-limit 300
"""
import argparse, csv, io, json, os, random, ssl, tarfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "datasets", "public")
DL = os.path.join(ROOT, "cache")
MIRROR = "https://hf-mirror.com"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
SEED = 20261010
NL = chr(10)

SOURCES = {
    "mmlu":   {"repo": "cais/mmlu", "file": "data.tar", "license": "MIT",
               "dim": "D2", "note": "57 学科研究生/职业考试题,4 选 1"},
    "gpqa":   {"repo": "Wanfq/gpqa", "file": "gpqa_diamond.csv", "license": "CC-BY-4.0",
               "dim": "D2", "note": "PhD 撰写,Google-proof,极难"},
    "math500":{"repo": "HuggingFaceH4/MATH-500", "file": "test.jsonl", "license": "MIT",
               "dim": "D3", "note": "竞赛数学,带逐步解答"},
    "ifeval": {"repo": "google/IFEval", "file": "ifeval_input_data.jsonl", "license": "Apache-2.0",
               "dim": "D5", "note": "可验证指令遵循"},
    "bfcl":   {"repo": "gorilla-llm/Berkeley-Function-Calling-Leaderboard",
               "file": "BFCL_v3_exec_simple.json", "license": "Apache-2.0",
               "dim": "D6", "note": "函数调用,exec 类"},
}


def fetch(repo, file, binary=True, timeout=180):
    url = "%s/datasets/%s/resolve/main/%s" % (MIRROR, repo, file)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    data = urllib.request.urlopen(req, timeout=timeout, context=CTX).read()
    print("   下载 %-52s %7.1f KB  %.1fs" % (file, len(data) / 1024, time.time() - t0))
    return data


def meta(name, extra=None):
    s = SOURCES[name]
    m = {"source": "public:" + name, "source_name": name,
         "contamination_risk": "high",   # these sets are widely used in training corpora
         "repo": s["repo"], "file": s["file"],
         "license": s["license"], "fetched": time.strftime("%Y-%m-%d"),
         "note": s["note"]}
    if extra:
        m.update(extra)
    return m


def _gradeable(ans):
    """True when the answer is a plain number / fraction / radical our math judge
    can check without an LLM. Keeps MATH-500 selections fair and reproducible."""
    import re as _re
    a = (ans or "").strip()
    if not a or len(a) > 40:
        return False
    a = a.replace("$", "").replace(chr(92) + "!", "").replace("{,}", ",").strip()
    a = _re.sub(r"^" + chr(92)*2 + r"(?:text|mathrm)\{(.*)\}$", r"", a)
    a = a.replace(",", "")
    if _re.fullmatch(r"-?\d+(?:\.\d+)?", a):
        return True
    if _re.fullmatch(r"-?\d+\s*/\s*-?\d+", a):
        return True
    if _re.fullmatch(r"-?\d*\s*" + chr(92)*2 + r"sqrt\{?\d+\}?", a):
        return True
    return False


# ---------------- per-benchmark converters ----------------

def do_mmlu(limit, rng):
    """cais/mmlu data.tar -> data/test/*.csv (question, A, B, C, D, answer)"""
    os.makedirs(DL, exist_ok=True)
    cache = os.path.join(DL, "mmlu_data.tar")
    if os.path.exists(cache):
        data = open(cache, "rb").read()
        print("   使用缓存 cache/mmlu_data.tar (%.1f MB)" % (len(data) / 1e6))
    else:
        data = fetch(SOURCES["mmlu"]["repo"], "data.tar")
        open(cache, "wb").write(data)
    tf = tarfile.open(fileobj=io.BytesIO(data))
    rows = []
    for n in tf.getnames():
        if not n.startswith("data/test/") or not n.endswith(".csv"):
            continue
        subject = os.path.basename(n)[:-4].replace("_test", "")
        txt = tf.extractfile(n).read().decode("utf-8", "ignore")
        # NOTE: cais/mmlu CSVs have NO header row - row 0 is already data.
        # DictReader would eat the first question as the header, so use positional rows:
        # [question, A, B, C, D, answer]
        for i, r in enumerate(csv.reader(io.StringIO(txt))):
            if len(r) < 6:
                continue
            q = (r[0] or "").strip()
            opts = {"A": (r[1] or "").strip(), "B": (r[2] or "").strip(),
                    "C": (r[3] or "").strip(), "D": (r[4] or "").strip()}
            ans = (r[5] or "").strip().upper()
            if not q or ans not in opts:
                continue
            rows.append({"id": "pub_mmlu_%s_%04d" % (subject[:18], i), "dim": "D2",
                         "subcat": "public-mmlu", "question": q, "options": opts,
                         "answer_letter": ans, **meta("mmlu", {"subject": subject})})
    if limit and len(rows) > limit:
        by = {}
        for r in rows:
            by.setdefault(r["subject"], []).append(r)
        keep, per = [], max(1, limit // max(1, len(by)))
        for s in sorted(by):
            sub = by[s]; rng.shuffle(sub); keep.extend(sub[:per])
        rng.shuffle(keep)
        rows = keep[:limit]
    return rows


def _place_options(correct, wrongs, rng):
    """Place one correct and three wrong options into A-D at seeded random positions.
    Without this the correct answer sits at a fixed letter and the benchmark can be
    gamed by always answering that letter."""
    items = [correct] + list(wrongs)
    rng.shuffle(items)
    letters = "ABCD"
    opts = dict(zip(letters, items))
    return opts, letters[items.index(correct)]


def do_gpqa(limit, rng):
    txt = fetch(SOURCES["gpqa"]["repo"], SOURCES["gpqa"]["file"]).decode("utf-8", "ignore")
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(txt))):
        q = (r.get("Question") or "").strip()
        correct = (r.get("Correct Answer") or "").strip()
        wrongs = [(r.get("Incorrect Answer %d" % k) or "").strip() for k in (1, 2, 3)]
        if not q or not correct or not all(wrongs):
            continue
        # CRITICAL: randomize option positions. GPQA ships a fixed correct/wrong
        # split; keeping the correct answer at "A" everywhere would let a model
        # score 100% by always answering A. Seeded, so it stays reproducible.
        opts, letter = _place_options(correct, wrongs, rng)
        rows.append({"id": "pub_gpqa_%04d" % i, "dim": "D2", "subcat": "public-gpqa",
                     "question": q, "options": opts, "answer_letter": letter,
                     **meta("gpqa", {"domain": (r.get("High-level domain") or "").strip()})})
    rng.shuffle(rows)
    return rows[:limit] if limit else rows


def do_math500(limit, rng):
    txt = fetch(SOURCES["math500"]["repo"], SOURCES["math500"]["file"]).decode("utf-8", "ignore")
    rows = []
    for i, line in enumerate(txt.splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        # MATH-500 carries the final answer directly; only keep ones our rule-based
        # judge can actually grade (plain numbers / fractions / radicals).
        ans = str(d.get("answer") or "").strip()
        if not _gradeable(ans):
            continue
        rows.append({"id": "pub_math500_%04d" % i, "dim": "D3", "subcat": "public-math500",
                     "question": (d.get("problem") or "").strip(), "answer": ans, "raw_answer": str(d.get("answer") or ""),
                     "tolerance": "0.01",
                     **meta("math500", {"subject": (d.get("subject") or ""),
                                        "level": d.get("level")})})
    rng.shuffle(rows)
    return rows[:limit] if limit else rows


def do_ifeval(limit, rng):
    txt = fetch(SOURCES["ifeval"]["repo"], SOURCES["ifeval"]["file"]).decode("utf-8", "ignore")
    rows = []
    for i, line in enumerate(txt.splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        rows.append({"id": "pub_ifeval_%04d" % i, "dim": "D5", "subcat": "public-ifeval",
                     "user": (d.get("prompt") or "").strip(),
                     "expect": {"ifeval": True,
                                "instruction_id_list": d.get("instruction_id_list") or [],
                                "kwargs": d.get("kwargs") or []},
                     "raw": d,
                     **meta("ifeval")})
    rng.shuffle(rows)
    return rows[:limit] if limit else rows


# JSON-Schema type names that an OpenAI-compatible server rejects outright.
# BFCL ships "dict" everywhere; llama-server answers such a request with HTTP 500,
# which made all 100 questions fail before a single one was graded.
_TYPE_FIX = {"dict": "object", "list": "array", "tuple": "array", "set": "array",
             "str": "string", "int": "integer", "float": "number", "bool": "boolean",
             "text": "string", "double": "number"}


def _normalize_schema(node):
    """Recursively coerce non-standard JSON-Schema type names."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = _TYPE_FIX.get(v.lower(), v)
            else:
                out[k] = _normalize_schema(v)
        return out
    if isinstance(node, list):
        return [_normalize_schema(x) for x in node]
    return node


def _normalize_tool(t):
    """Return {name, description, parameters} with a valid JSON Schema."""
    if not isinstance(t, dict):
        return t
    name = t.get("name") or (t.get("function") or {}).get("name")
    desc = t.get("description") or (t.get("function") or {}).get("description") or ""
    params = t.get("parameters") or (t.get("function") or {}).get("parameters") or {}
    if not params:
        params = {"type": "object", "properties": {}}
    return {"name": name, "description": desc, "parameters": _normalize_schema(params)}


def _parse_call_args(argstr):
    """Split "a=1, matA=[[2,3],[4,5]], s='x,y'" on TOP-LEVEL commas only.

    A naive [^,]+ split cuts nested lists and strings in half, which made the
    judge compare "[[2, 3], [4, 5]]" against "[[2" and report a correct call as
    wrong. Depth counting fixes it without evaluating anything.
    """
    out, buf, depth, quote = {}, "", 0, None
    pieces = []
    for ch in argstr:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch; buf += ch; continue
        if ch in "([{":
            depth += 1; buf += ch; continue
        if ch in ")]}":
            depth -= 1; buf += ch; continue
        if ch == "," and depth == 0:
            pieces.append(buf); buf = ""; continue
        buf += ch
    if buf.strip():
        pieces.append(buf)
    for piece in pieces:
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def do_bfcl(limit, rng):
    """BFCL exec-simple is JSONL: one object per line with a real tool schema and a
    ground-truth python call string, so we can check arguments, not just the name."""
    txt = fetch(SOURCES["bfcl"]["repo"], SOURCES["bfcl"]["file"]).decode("utf-8", "ignore")
    import re as _re
    rows = []
    for line in txt.splitlines():
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
        # ground truth like "calc_binomial_probability(n=20, k=5, p=0.6)"
        want_name, want_args = None, {}
        m = _re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$", str(gt[0]), _re.S)
        if m:
            want_name = m.group(1)
            want_args = _parse_call_args(m.group(2))
        if not want_name:
            continue
        funcs = [_normalize_tool(f) for f in funcs]
        rows.append({"id": "pub_bfcl_" + str(d.get("id") or len(rows)), "dim": "D6",
                     "subcat": "public-bfcl", "user": q,
                     "tools": funcs,
                     "expect": {"should_call": [want_name], "must_not_call": [],
                                "bfcl_name": want_name, "bfcl_args": want_args,
                                "bfcl_check_args": True,
                                "reason": "BFCL exec-simple:需调用指定函数且参数匹配"},
                     **meta("bfcl")})
    rng.shuffle(rows)
    return rows[:limit] if limit else rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="mmlu,gpqa,math500,ifeval,bfcl")
    ap.add_argument("--mmlu-limit", type=int, default=300)
    ap.add_argument("--gpqa-limit", type=int, default=198)
    ap.add_argument("--math500-limit", type=int, default=300)
    ap.add_argument("--ifeval-limit", type=int, default=300)
    ap.add_argument("--bfcl-limit", type=int, default=200)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for k, v in SOURCES.items():
            print("%-10s %-48s %-14s %s" % (k, v["repo"] + "/" + v["file"], v["license"], v["note"]))
        return

    os.makedirs(OUT, exist_ok=True)
    os.makedirs(DL, exist_ok=True)
    want = [x.strip() for x in args.only.split(",") if x.strip()]
    limits = {"mmlu": args.mmlu_limit, "gpqa": args.gpqa_limit, "math500": args.math500_limit,
              "ifeval": args.ifeval_limit, "bfcl": args.bfcl_limit}
    rng = random.Random(SEED)
    prov = {}
    for name in want:
        fn = {"mmlu": do_mmlu, "gpqa": do_gpqa, "math500": do_math500,
              "ifeval": do_ifeval, "bfcl": do_bfcl}.get(name)
        if not fn:
            print("skip unknown source:", name); continue
        print("[%s] %s" % (name, SOURCES[name]["note"]))
        try:
            rows = fn(limits[name], rng)
        except Exception as e:
            print("   FAILED:", type(e).__name__, str(e)[:110]); continue
        if not rows:
            print("   0 rows, skip"); continue
        out = os.path.join(OUT, "%s.jsonl" % name)
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + NL)
        prov[name] = {"rows": len(rows), "dim": SOURCES[name]["dim"],
                      "repo": SOURCES[name]["repo"], "file": SOURCES[name]["file"],
                      "license": SOURCES[name]["license"],
                      "fetched": time.strftime("%Y-%m-%d")}
        print("   -> %d questions  datasets/public/%s.jsonl" % (len(rows), name))
    json.dump(prov, open(os.path.join(OUT, "_provenance.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print()
    print("provenance -> datasets/public/_provenance.json")
    print("next: python scripts/build_manifest.py && python runner/make_subset.py")


if __name__ == "__main__":
    main()
