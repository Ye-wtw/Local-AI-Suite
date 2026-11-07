# -*- coding: utf-8 -*-
"""_integrity_t3.py - temporary structural check of the four appended datasets.

Confirms, for each of D4/D7/D8/D9:
  * the row count reached the T3 target
  * every NEW row carries the fields the acceptance criteria name
  * ids are unique across the whole file
  * the pre-existing rows were not rewritten (count preserved, prefix hash recorded)

Usage: python scripts/_integrity_t3.py
"""
import hashlib, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, "datasets", "full")
NL = chr(10)

SPEC = {
    # D4 has two row shapes: python/debug carry prompt|buggy + test + entry_point,
    # sql carries schema + question + check. Both are what runner/code_judge.py reads.
    "D4": dict(pre="d4m_", target=80, before=38,
               need=["id", "dim", "subcat", "scoring", "notes"],
               either=[]),
    "D7": dict(pre="d7m_", target=60, before=30,
               need=["id", "dim", "subcat", "question", "expect", "notes", "source"],
               either=["context_file", "context_files"]),
    "D8": dict(pre="d8m_", target=100, before=78,
               need=["id", "dim", "subcat", "user", "expect", "notes", "source"],
               either=[]),
    "D9": dict(pre="d9m_", target=100, before=32,
               need=["id", "dim", "subcat", "user", "expect", "notes", "source", "checks"],
               either=[]),
}


def main():
    bad = 0
    for dim, spec in SPEC.items():
        path = os.path.join(FULL, "%s_full.jsonl" % dim)
        lines = [ln for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
        rows = [json.loads(ln) for ln in lines]
        new = [r for r in rows if r["id"].startswith(spec["pre"])]
        old = [r for r in rows if not r["id"].startswith(spec["pre"])]

        print("%s: %d rows (target %d, was %d) | new %d | pre-existing %d"
              % (dim, len(rows), spec["target"], spec["before"], len(new), len(old)))

        if len(rows) < spec["target"]:
            print("   FAIL count below target")
            bad += 1
        if len(old) != spec["before"]:
            print("   FAIL pre-existing row count changed: %d != %d"
                  % (len(old), spec["before"]))
            bad += 1

        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            print("   FAIL duplicate ids")
            bad += 1

        missing = 0
        for r in new:
            for f in spec["need"]:
                if f not in r or r[f] in (None, "", [], {}):
                    print("   FAIL %s missing field %r" % (r["id"], f))
                    missing += 1
            if spec["either"] and not any(r.get(f) for f in spec["either"]):
                print("   FAIL %s has none of %s" % (r["id"], spec["either"]))
                missing += 1
            if dim == "D4":
                if r["subcat"] == "sql":
                    for f in ("schema", "question", "check"):
                        if not r.get(f):
                            print("   FAIL %s sql row missing %r" % (r["id"], f))
                            missing += 1
                else:
                    for f in ("test", "entry_point"):
                        if not r.get(f):
                            print("   FAIL %s missing %r" % (r["id"], f))
                            missing += 1
                    if not (r.get("prompt") or r.get("buggy")):
                        print("   FAIL %s has neither prompt nor buggy" % r["id"])
                        missing += 1
        bad += missing
        if not missing:
            print("   ok: all %d new rows carry the required fields" % len(new))

        h = hashlib.sha256(NL.join(lines[:spec["before"]]).encode("utf-8")).hexdigest()[:16]
        print("   original-prefix sha256[:16] = %s" % h)

        if dim == "D9":
            ex = [r for r in new if r.get("kind") == "exec"]
            st = [r for r in new if r.get("kind") == "static"]
            print("   executable-judged: %d | rule-judged prose: %d" % (len(ex), len(st)))
            if len(ex) < 40:
                print("   FAIL fewer than 40 executable tasks")
                bad += 1
    print()
    print("INTEGRITY: %s" % ("PASS" if bad == 0 else "%d PROBLEM(S)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
