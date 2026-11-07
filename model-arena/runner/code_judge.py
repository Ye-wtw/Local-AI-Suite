# -*- coding: utf-8 -*-
"""code_judge.py - D4 code judging: extract the model's code, run it in the sandbox.
Subcategories: python / debug (pass@1 via tests) and sql (result-set equivalence).
"""
import re
from sandbox import run_python_tests, run_sql_equivalence

PY_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S | re.I)
SQL_FENCE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.S | re.I)


def extract_python(response):
    """Pull Python code out of a model reply (prefers fenced blocks)."""
    if not response:
        return ""
    blocks = PY_FENCE.findall(response)
    if blocks:
        # longest fenced block is usually the real implementation
        return max(blocks, key=len).strip()
    # no fence: strip prose lines, keep plausible code
    lines = []
    for ln in response.splitlines():
        s = ln.rstrip()
        if re.match(r"^\s*(def |class |import |from |@|#)", s) or re.match(r"^\s{4,}\S", s) or s.strip() == "":
            lines.append(s)
    code = "\n".join(lines).strip()
    return code


def extract_sql(response):
    if not response:
        return ""
    blocks = SQL_FENCE.findall(response)
    if blocks:
        return max(blocks, key=len).strip()
    m = re.search(r"\b(SELECT\b.*?)(?:;|$)", response, re.S | re.I)
    return m.group(1).strip() if m else ""


def judge_code(entry, response):
    sub = entry.get("subcat")
    if sub == "sql":
        sql = extract_sql(response)
        ref = entry.get("check") or ""
        schema = entry.get("schema") or ""
        if not sql:
            return {"correct": False, "stage": "no_sql", "reason": "no SQL found in reply"}
        r = run_sql_equivalence(sql, ref, schema)
        return {"correct": bool(r.get("passed")), "stage": r.get("stage"),
                "reason": r.get("error", "")[:300], "extracted": sql[:200]}

    code = extract_python(response)
    test = entry.get("test") or ""
    entry_point = entry.get("entry_point") or ""
    if not code:
        return {"correct": False, "stage": "no_code", "reason": "no Python code found in reply"}
    r = run_python_tests(code, test, entry_point)
    return {"correct": bool(r.get("passed")), "stage": r.get("stage"),
            "reason": (r.get("error") or "")[:300], "extracted": code[:200]}
