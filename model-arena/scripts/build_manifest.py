# -*- coding: utf-8 -*-
"""build_manifest.py - consolidate all datasets into datasets/full/ + generate manifest.json
Merges: _samples (curated examples) + generated (self-made pool) into per-dimension full files.
Also writes README and integrity report.
"""
import json, os, glob, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, "datasets", "full")
os.makedirs(FULL, exist_ok=True)

def load_all(pattern):
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, pattern))):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception as e:
                    print("  SKIP bad line in %s: %s" % (f, e))
    return rows

def dedupe(rows):
    seen = set()
    out = []
    for r in rows:
        q = r.get("question") or r.get("user") or r.get("prompt") or r.get("book") or ""
        # needle questions differ in expect.needles, not question text
        sub = r.get("subcat")
        if sub in ("multi-needle", "needle-multi") and r.get("expect", {}).get("needles"):
            needle_key = tuple(sorted(str(n) for n in r["expect"]["needles"]))
            key = (r.get("dim"), "needle-multi", str(r.get("context_file")), needle_key)
        elif sub == "needle-single":
            key = (r.get("dim"), "needle-single", str(r.get("expect", {}).get("code")))
        elif sub == "cross-doc":
            key = (r.get("dim"), "crossdoc", tuple(sorted(r.get("context_files", []))))
        elif sub == "long-qa":
            key = (r.get("dim"), "longqa", str(r.get("context_file", "")))
        else:
            key = (r.get("dim"), q[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

DIMS = {
    "D1": ["D1_speed.json"],
    "D2": ["datasets/_samples/D2_knowledge.jsonl", "datasets/generated/D2_generated.jsonl",
           "datasets/public/mmlu.jsonl", "datasets/public/gpqa.jsonl"],
    "D3": ["datasets/_samples/D3_math.jsonl", "datasets/generated/D3_generated.jsonl",
           "datasets/public/math500.jsonl"],
    "D4": ["datasets/_samples/D4_code.jsonl", "datasets/generated/D4_generated.jsonl"],
    "D5": ["datasets/_samples/D5_instruction.jsonl", "datasets/generated/D5_generated.jsonl",
           "datasets/public/ifeval.jsonl"],
    "D6": ["datasets/_samples/D6_agent.jsonl", "datasets/generated/D6_generated.jsonl",
           "datasets/public/bfcl.jsonl"],
    "D7": ["datasets/generated/D7_generated.jsonl"],
    "D8": ["datasets/_samples/D8_reliability.jsonl", "datasets/generated/D8_generated.jsonl"],
    "D9": ["datasets/_samples/D9_realworld_full.jsonl"],
    "D10": ["datasets/generated/D10_generated.jsonl"],
}

def main():
    manifest = {"project": "model-arena", "version": "0.1", "generated": None, "dims": {}, "integrity": {}}
    totals = {}
    for dim, patterns in DIMS.items():
        rows = []
        for p in patterns:
            if p.endswith(".jsonl") and os.path.exists(os.path.join(ROOT, p)):
                for line in open(os.path.join(ROOT, p), encoding="utf-8"):
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
        if dim == "D1":
            manifest["dims"]["D1"] = {"files": patterns, "rows": 1, "note": "speed config (JSON)"}
            totals["D1"] = 1
            continue
        rows = dedupe(rows)
        # global id renumbering: guarantee uniqueness across samples + generated + authoritative
        seen_ids = set()
        for r in rows:
            base = r.get("id") or "%s_%s_0000" % (dim, r.get("subcat", "x"))
            if base not in seen_ids:
                seen_ids.add(base)
                r["id"] = base
            else:
                n = len(seen_ids)
                new_id = "%s_%s_%04d" % (dim, r.get("subcat", "x"), n)
                while new_id in seen_ids:
                    n += 1
                    new_id = "%s_%s_%04d" % (dim, r.get("subcat", "x"), n)
                seen_ids.add(new_id)
                r["id"] = new_id
        rows.sort(key=lambda r: r.get("id", ""))
        out = os.path.join(FULL, "%s_full.jsonl" % dim)
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # integrity
        h = hashlib.md5()
        with open(out, "rb") as f:
            h.update(f.read())
        src_counter = {}
        for r in rows:
            s = r.get("source", "unknown")
            src_counter[s] = src_counter.get(s, 0) + 1
        manifest["dims"][dim] = {"file": "datasets/full/%s_full.jsonl" % dim, "rows": len(rows), "hash": h.hexdigest()[:12], "sources": src_counter}
        totals[dim] = len(rows)
        print("D%s: %d rows (sources: %s)" % (dim, len(rows), src_counter))
    total = sum(totals.values())
    manifest["total_rows"] = total
    with open(os.path.join(ROOT, "datasets", "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("\nTOTAL rows (D2-D9):", total)
    print("manifest -> datasets/manifest.json")

if __name__ == "__main__":
    main()
