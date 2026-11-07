# -*- coding: utf-8 -*-
"""doccheck.py - verify that documented commands actually exist.

Why this exists: documentation drifts. A README that says "python runner/env"
when the module is "python runner/arena_cli.py env" sends every new user into an
error, and the author never finds out because they never run their own docs.

This checks, without executing anything:
  1. every "python <path>.py" mentioned in the docs resolves to a real file
  2. every "python runner/<name>" without .py has a matching .py sibling
  3. every --suite value mentioned matches a real choice in run.py

Usage: python runner/doccheck.py [--strict]
"""
import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Only the docs a reader is expected to FOLLOW. Archived notes (docs/plans,
# docs/dev, docs/audits) are historical records: PLAN.md describes what someone
# intended months ago and legitimately names scripts that were renamed or
# dropped since. Checking those would flag history as broken, which teaches
# people to ignore the checker.
# 文档位置:根目录只留 README(入口) 和 AGENTS(项目规则),
# 其余收进 docs/ —— 2026-11-07 架构整理时挪的。
# 改这个列表必须同步磁盘上的真实位置,否则 doccheck 会静默跳过那份文档。
DOCS = ["README.md", "AGENTS.md", "docs/USAGE.md", "docs/ARCHITECTURE.md",
        "docs/CONTRIBUTING.md", "docs/STATUS.md"]
DOC_GLOBS = []


def real_scripts():
    out = set()
    for d in ("runner", "scripts", "tools"):
        for f in glob.glob(os.path.join(ROOT, d, "*.py")):
            out.add("%s/%s" % (d, os.path.basename(f)))
    return out


def real_suites():
    """Suite names run.py actually accepts."""
    path = os.path.join(HERE, "run.py")
    if not os.path.exists(path):
        return set()
    src = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r'--suite["\']\s*,.*?choices\s*=\s*\[(.*?)\]', src, re.S)
    if not m:
        m = re.search(r"choices\s*=\s*\[(.*?)\]", src, re.S)
    if not m:
        return set()
    return set(re.findall(r'"([\w-]+)"', m.group(1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is wrong")
    args = ap.parse_args()

    scripts = real_scripts()
    suites = real_suites()
    problems = []

    targets = [os.path.join(ROOT, d) for d in DOCS]
    for g in DOC_GLOBS:
        targets.extend(glob.glob(os.path.join(ROOT, g), recursive=True))

    for path in targets:
        if not os.path.exists(path):
            continue
        doc = os.path.relpath(path, ROOT)
        txt = open(path, encoding="utf-8").read()

        # python runner/x.py  (explicit file). Skip templates: docs legitimately
        # write "runner/xxx.py" or "runner/<dim>.py" as something to fill in.
        for m in re.finditer(r"python\s+((?:runner|scripts|tools)/[\w.]+\.py)", txt):
            rel = m.group(1)
            leaf = rel.rsplit("/", 1)[-1]
            # 2+ repeated placeholder letters: "xx.py", "xxx.py", "runner/<dim>.py"
            if re.fullmatch(r"(x{2,}|y{2,}|z{2,}|<[\w-]+>|dim|name|your[-_]?\w*)\.py", leaf):
                continue
            if rel not in scripts:
                ln = txt[:m.start()].count(chr(10)) + 1
                problems.append((doc, ln, rel, "file does not exist"))

        # python runner/x  (missing .py, or genuinely absent)
        for m in re.finditer(r"python\s+((?:runner|scripts|tools)/[\w]+)(?![\w./])", txt):
            rel = m.group(1)
            if rel + ".py" not in scripts:
                ln = txt[:m.start()].count(chr(10)) + 1
                problems.append((doc, ln, rel, "no such module (missing .py?)"))

        # --suite <name> in prose/code blocks
        if suites:
            for m in re.finditer(r"--suite\s+([\w-]+)", txt):
                name = m.group(1)
                # "choices" and bare words appear when prose explains the flag
                if name in ("choices", "level", "suite"):
                    continue
                if name not in suites and not name.startswith("<"):
                    ln = txt[:m.start()].count(chr(10)) + 1
                    problems.append((doc, ln, "--suite " + name, "not a real suite"))

    print("=" * 68)
    print("documentation check")
    print("=" * 68)
    print("scripts found : %d" % len(scripts))
    print("suites found  : %d  (%s)" % (len(suites), ", ".join(sorted(suites)[:8])))
    print()
    if not problems:
        print("no broken commands in docs")
        return 0
    print("%d problem(s):" % len(problems))
    for doc, ln, what, why in problems:
        print("  %-16s L%-4d %-24s %s" % (doc, ln, what[:24], why))
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
