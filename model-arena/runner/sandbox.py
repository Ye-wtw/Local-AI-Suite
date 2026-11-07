# -*- coding: utf-8 -*-
"""sandbox.py - run model-written Python against tests in an isolated subprocess.
Isolation: separate process, -I (isolated) interpreter mode, temp working dir,
hard timeout, no inherited environment. Sufficient for pass@1 correctness checks
(not a security boundary against deliberately hostile code).
"""
import os, shutil, subprocess, sys, tempfile, textwrap

HARNESS = r'''
import sys, traceback, json

MODEL_CODE = {model_code!r}
TEST_CODE = {test_code!r}
ENTRY = {entry!r}

ns = {{"__name__": "__main__"}}
result = {{"passed": False, "stage": "exec_model", "error": ""}}
try:
    exec(compile(MODEL_CODE, "<model>", "exec"), ns)
except Exception:
    result["error"] = traceback.format_exc(limit=3)
    print("@@RESULT@@" + json.dumps(result))
    sys.exit(0)

fn = ns.get(ENTRY)
if fn is None:
    result["stage"] = "lookup"
    result["error"] = "entry point %r not defined" % ENTRY
    print("@@RESULT@@" + json.dumps(result))
    sys.exit(0)

try:
    if "def check(" in TEST_CODE:
        exec(compile(TEST_CODE, "<test>", "exec"), ns)
        ns["check"](fn)
    else:
        ns["candidate"] = fn
        exec(compile(TEST_CODE, "<test>", "exec"), ns)
    result["passed"] = True
    result["stage"] = "ok"
except AssertionError:
    result["stage"] = "assert"
    result["error"] = traceback.format_exc(limit=4)
except Exception:
    result["stage"] = "runtime"
    result["error"] = traceback.format_exc(limit=4)

print("@@RESULT@@" + json.dumps(result))
'''


_WORK_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "results", ".sandbox")


_work_seq = [0]


def _work_dir():
    """A writable scratch dir under the project.

    tempfile.mkdtemp is avoided entirely, even with dir=: on this host the
    restriction is not only the 0o700 mode, mkdtemp also applies a restrictive
    DACL that the sandboxed token cannot write through. A plain makedirs with a
    monotonically increasing name simply works.
    """
    os.makedirs(_WORK_ROOT, exist_ok=True)
    while True:
        _work_seq[0] += 1
        d = os.path.join(_WORK_ROOT, "sbx_%d_%d" % (os.getpid(), _work_seq[0]))
        if not os.path.exists(d):
            os.makedirs(d)
            return d


def run_python_tests(model_code, test_code, entry_point, timeout=15):
    """Execute model_code then test_code. Returns dict {passed, stage, error, stdout}."""
    if not model_code or not model_code.strip():
        return {"passed": False, "stage": "empty", "error": "no code extracted", "stdout": ""}
    script = HARNESS.format(model_code=model_code, test_code=test_code or "", entry=entry_point or "")
    # tempfile.mkdtemp() creates a 0o700 directory. The DSH sandbox runs commands
    # under a restricted token that cannot write into one, so the very first
    # write raised PermissionError and took the whole run down with it (hit at
    # question 91 of a canary run). Work dirs are created under the project
    # instead, where the session is allowed to write, and removed afterwards.
    tmp = _work_dir()
    try:
        return _run_in_dir(tmp, script, timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_in_dir(tmp, script, timeout):
    path = os.path.join(tmp, "harness.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    try:
        p = subprocess.run([sys.executable, "-I", path],
                           capture_output=True, text=True, timeout=timeout,
                           cwd=tmp, env={"PYTHONIOENCODING": "utf-8"})
        out = p.stdout or ""
        err = p.stderr or ""
    except subprocess.TimeoutExpired:
        return {"passed": False, "stage": "timeout",
                "error": "exceeded %ds (possible infinite loop)" % timeout, "stdout": ""}
    except Exception as e:
        return {"passed": False, "stage": "spawn", "error": str(e), "stdout": ""}
    marker = "@@RESULT@@"
    if marker in out:
        import json as _json
        payload = out.split(marker, 1)[1].strip().splitlines()[0]
        try:
            res = _json.loads(payload)
        except Exception:
            res = {"passed": False, "stage": "parse", "error": payload[:300]}
        res["stdout"] = out.split(marker)[0][-500:]
        return res
    return {"passed": False, "stage": "crash", "error": (err or out)[-500:], "stdout": out[-300:]}


def run_sql_equivalence(model_sql, reference_sql, schema_sql, timeout=15):
    """Create the schema with generated sample rows, run both queries, compare row sets."""
    import sqlite3
    script = textwrap.dedent('''
        import sqlite3, json
        conn = sqlite3.connect(":memory:")
        conn.executescript({schema!r})
        # Deterministic sample data built to DISCRIMINATE clauses:
        #   cust1 -> 3000 (>1000, kept by HAVING)
        #   cust2 ->  200 (<=1000, dropped by HAVING but present without it)
        #   cust3 ->  900 (<=1000, dropped by HAVING)
        # Without this spread, a query missing HAVING would wrongly compare equal.
        cities = ["Beijing", "Shanghai", "Shenzhen"]
        for cid in range(1, 4):
            conn.execute("INSERT INTO customers VALUES (?,?,?)", (cid, "cust%d" % cid, cities[cid-1]))
        rows_data = [(1, 1, 1500.0), (2, 1, 1500.0),
                     (3, 2, 100.0), (4, 2, 100.0),
                     (5, 3, 900.0)]
        for oid, cust, amt in rows_data:
            conn.execute("INSERT INTO orders VALUES (?,?,?,?)",
                         (oid, cust, amt, "2026-01-%02d" % (oid * 3)))
        conn.commit()
        def rows(sql):
            try:
                cur = conn.execute(sql)
                return sorted(tuple(str(x) for x in r) for r in cur.fetchall())
            except Exception as e:
                return "ERR:" + str(e)
        out = {{"model": rows({model!r}), "ref": rows({ref!r})}}
        out["passed"] = out["model"] == out["ref"] and not str(out["model"]).startswith("ERR")
        print("@@RESULT@@" + json.dumps(out))
    ''').format(schema=schema_sql or "", model=model_sql or "", ref=reference_sql or "")
    # Same 0o700 problem as run_python_tests - and this one only showed up at
    # question 99 because the canary subset puts SQL last. Both paths now share
    # _work_dir() so a fix cannot be half-applied again.
    tmp = _work_dir()
    try:
        return _run_sql_in_dir(tmp, script, timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_sql_in_dir(tmp, script, timeout):
    path = os.path.join(tmp, "sqlcheck.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    try:
        p = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True,
                           timeout=timeout, cwd=tmp, env={"PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return {"passed": False, "stage": "timeout", "error": "sql timeout", "stdout": ""}
    out = p.stdout or ""
    marker = "@@RESULT@@"
    if marker in out:
        import json as _json
        payload = out.split(marker, 1)[1].strip().splitlines()[0]
        try:
            res = _json.loads(payload)
        except Exception:
            res = {"passed": False, "stage": "parse", "error": payload[:200]}
        res.setdefault("stage", "ok" if res.get("passed") else "mismatch")
        if not res.get("passed"):
            res["error"] = "model=%s ref=%s" % (str(res.get("model"))[:120], str(res.get("ref"))[:120])
        res["stdout"] = ""
        return res
    return {"passed": False, "stage": "crash", "error": (p.stderr or out)[-400:], "stdout": out[-200:]}
