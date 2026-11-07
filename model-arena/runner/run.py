# -*- coding: utf-8 -*-
"""run.py - model-arena evaluation CLI (P1: math + speed).
Usage:
  python runner/run.py --endpoint mock --suite math
  python runner/run.py --endpoint local --suite speed --repeats 3
  python runner/run.py --endpoint local --suite all --limit 50
Manual trigger only: nothing runs until you invoke it.
"""
import argparse, json, os, subprocess, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from endpoint import load_endpoints, EndpointError
from judge import judge_math
from checkers import run_checkers, judge_mcq
from chess_judge import judge_chess
from longctx_judge import judge_longctx
from code_judge import judge_code
from agent_judge import judge_agent, tool_specs
from reliability_judge import judge_reliability
from checkpoint import Checkpoint
from agent_env import apply_prefix as agent_apply_prefix
from mock_tools import result_for as mock_tool_result
import environment as envmod
import launch_params as launchmod

RESUME = True          # set False via --no-resume to start a run over
AGENT_MODE = "bare"    # bare | agent-small | agent-mid | agent-real
from d9_judge import judge_d9
import d9_exec as d9exec
import dyn_multi as dynmod
import speed as speedmod

RESULTS = os.path.join(ROOT, "results")

def ts():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

# ---------- safety: VRAM precheck ----------
def vram_check(min_free_mb=1500):
    """Return (ok, detail). Skipped gracefully when nvidia-smi is absent."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return True, "nvidia-smi rc=%d (skipped)" % out.returncode
        line = out.stdout.strip().splitlines()[0]
        total, used, free = [int(x.strip()) for x in line.split(",")]
        detail = "VRAM total=%dMB used=%dMB free=%dMB" % (total, used, free)
        return (free >= min_free_mb), detail
    except FileNotFoundError:
        return True, "nvidia-smi not found (CPU-only or not on PATH) - skipped"
    except Exception as e:
        return True, "vram check skipped: %s" % e

def _judge_d5(row, text):
    """Judge one D5 row by its EXPECT SHAPE, not by assumption.

    D5_full.jsonl mixes two schemas and they need different judges:
      * expect.checkers          -> this project's own checker schema (checkers)
      * expect.instruction_id_list -> IFEval rules (ifeval_judge)

    suite_instruction called run_checkers unconditionally, so the 300
    public-IFEval rows (instruction_id_list only) got checkers=None, and
    run_checkers(None) returned (True, []) - an empty rule set scored as a
    pass. A recorded run showed public-ifeval 300/300 with
    checker_results=[], i.e. a perfect score produced by never checking
    anything. Route by shape and let each judge report its own verdict.
    """
    exp = row.get("expect") or {}
    if exp.get("instruction_id_list"):
        import ifeval_judge as IF
        v = IF.judge_ifeval(row, text)
        return bool(v.get("correct")), {
            "judge": "ifeval",
            "skipped": bool(v.get("skipped")),
            "reason": v.get("reason", ""),
            "detail": v.get("detail", []),
        }
    ok, results = run_checkers(exp.get("checkers"), text)
    return bool(ok), {"judge": "checkers", "checker_results": results}
# ---------- suites ----------
def load_jsonl(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Endpoint-error accounting, shared by every suite.
#
# This bug kept coming back in a different suite each time: an API failure was
# recorded as "correct: False", so a network hiccup looked like the model getting
# the question wrong (it once turned 91.7% into a fake 74.4%). Fixing it per-suite
# meant the next suite still had it, so the logic lives here now and every suite
# calls these two helpers.
# ---------------------------------------------------------------------------
def mark_error(rec, exc):
    """Flag a record as an API failure rather than a wrong answer.

    A per-question TIMEOUT is marked separately (timed_out, not error): the
    model did answer, it just did not stop, so bucketing it with network
    failures would overstate reliability problems. It still leaves the
    accuracy denominator, because a truncated answer cannot be scored.
    """
    msg = str(exc)
    is_timeout = "timed out" in msg.lower() or "deadline" in msg.lower()
    tag = "timeout" if is_timeout else "error"
    print("  [%s] %s: %s" % (tag, rec.get("id"), msg[:90]))
    rec["correct"] = False
    rec["error"] = True
    if is_timeout:
        rec["timed_out"] = True
    rec["error_reason"] = msg[:200]
    return rec


def valid_stats(records):
    """Return (n_valid, n_errors, accuracy). Errors never enter the denominator.

    Timeouts are counted in n_errors (they leave the denominator for the same
    reason) but are also reported as n_timeouts so a reader can tell "the
    endpoint broke" from "the model would not stop".
    """
    n_err = sum(1 for x in records if x.get("error"))
    n_valid = len(records) - n_err
    ok = sum(1 for x in records if x.get("correct") and not x.get("error"))
    return n_valid, n_err, (ok / n_valid if n_valid else 0.0)


def timeout_count(records):
    """How many records were abandoned at the per-question deadline."""
    return sum(1 for x in records if x.get("timed_out"))


def progress_line(i, total, records):
    n_valid, n_err, acc = valid_stats(records)
    return "  %3d/%3d  running acc=%.1f%%%s" % (
        i, total, acc * 100,
        ("  [%d 次接口错误未计入]" % n_err) if n_err else "")


def suite_math(ep, limit=None, verbose=True, mock=False):
    path = os.path.join(ROOT, "datasets", "full", "D3_full.jsonl")
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print("\n[D3 math] %d questions, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        q = r["question"]
        expected = r.get("answer")
        tol = r.get("tolerance", "exact")
        try:
            res = ep.chat([{"role": "user", "content": q + "\n\nPlease end with a line: answer: <value>"}])
            verdict = judge_math(res["text"], expected, tol)
            rec = {"id": r["id"], "subcat": r.get("subcat"), "question": q,
                   "expected": expected, "tolerance": tol, "response": res["text"],
                   "latency_s": res["latency_s"], **{k: verdict[k] for k in ("correct", "extracted", "method", "reason")}}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"), "question": q,
                              "expected": expected, "tolerance": tol, "response": "",
                              "extracted": None, "method": "error", "reason": str(e)}, e)
        records.append(rec)
        if verbose and (i % 10 == 0 or i == len(rows)):
            _ok = sum(1 for x in records if x["correct"])
            _err = sum(1 for x in records if x.get("error"))
            _valid = len(records) - _err
            acc = _ok / _valid if _valid else 0.0
            print("  %3d/%3d  running acc=%.1f%%%s"
                  % (i, len(rows), acc * 100,
                     ("  [%d 次接口错误未计入]" % _err) if _err else ""))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    by_sub = {}
    for x in records:
        s = x.get("subcat") or "unknown"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if x["correct"] else 0
    summary = {"dim": "D3", "suite": "math", "endpoint": ep.name, "model": ep.model,
               "n": len(records), "correct": correct,
               "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
               "elapsed_s": dt, "by_subcat": by_sub}
    return summary, records

def _load_full(dim):
    path = os.path.join(ROOT, "datasets", "full", "%s_full.jsonl" % dim)
    return load_jsonl(path)

def suite_mcq(ep, limit=None, verbose=True):
    """D2 multiple-choice: ask for a letter, judge by extraction."""
    rows = _load_full("D2")
    if limit:
        rows = rows[:limit]
    print()
    print("[D2 MCQ] %d questions, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    # 检查点:552 题要跑 3.6 小时,崩了不能全重来;同时它也是唯一的实时进度来源
    # (日志是块缓冲,不可靠 —— 曾因此误判"卡死"而停掉正常任务)。
    ck = Checkpoint("mcq", ep.name, [x.get("id") for x in rows])
    done = ck.load() if RESUME else {}
    if done and verbose:
        print("  [resume] 已完成 %d 题,跳过" % len(done))
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        qid = r.get("id")
        opts = r.get("options") or {}
        if isinstance(opts, list):
            # Tolerate the legacy list form (["A. ...", "B. ..."]) that one D2 row uses;
            # the rendered prompt is identical to the dict form.
            opts = {item.split(".", 1)[0].strip(): item.split(".", 1)[1].strip()
                    for item in opts if "." in item}
        opt_txt = chr(10).join("%s. %s" % (k, v) for k, v in sorted(opts.items()))
        prompt = (r["question"] + chr(10) + chr(10) + opt_txt + chr(10) + chr(10) +
                  "Reply with the single best option letter only, on a final line: answer: <letter>")
        # 每题先查检查点:长跑要能续,也要能看见进度。
        # 实测教训:552 题跑一整夜,没有进度文件时无法判断"在跑"还是"卡死",
        # 曾因此误停一个正常运行的任务。
        if qid in done:
            records.append(done[qid])
            continue
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            v = judge_mcq(res["text"], r.get("answer_letter"))
            rec = {"id": r["id"], "subcat": r.get("subcat"), "question": r["question"],
                   "expected": r.get("answer_letter"), "response": res["text"],
                   "latency_s": res["latency_s"], **v}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"),
                              "question": r["question"],
                              "expected": r.get("answer_letter"), "response": "",
                              "extracted": None, "reason": str(e)}, e)
        records.append(rec)
        ck.add(rec)                      # 立即 flush:进程被杀也不丢已完成部分
        if verbose and (i % 10 == 0 or i == len(rows)):
            print(progress_line(i, len(rows), records), flush=True)
    dt = time.perf_counter() - t0
    # 连接失败/超时的题**不能算答错**。
    #
    # 实测:552 题里 257 题报 WinError 10061(连接被拒),响应为空。
    # 按"错"计入后 D2 显示 45.3%,而实际拿到答案的 295 题里对了 250 题(84.7%)。
    # 这不是模型弱,是请求没送到 —— AGENTS.md 那条"超时 ≠ 错误"正是针对这个。
    # 分母只算**有效题**(拿到了回答),同时把错误数单独报出来。
    valid = [x for x in records if not x.get("error")]
    errors = len(records) - len(valid)
    correct = sum(1 for x in valid if x["correct"])
    by_sub = {}
    for x in valid:
        s = x.get("subcat") or "unknown"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if x["correct"] else 0
    acc = correct / len(valid) if valid else 0.0
    if errors:
        print("  [warn] %d 题连接失败(未计入正确率),有效题 %d" % (errors, len(valid)))
    return ({"dim": "D2", "suite": "mcq", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "n_valid": len(valid), "n_errors": errors,
             "correct": correct,
             "accuracy": acc,
             "accuracy_with_errors": correct / len(records) if records else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub,
             "error_ids": [x.get("id") for x in records if x.get("error")]},
            records)

def suite_instruction(ep, limit=None, verbose=True):
    """D5 instruction-following: rule checkers, all-or-nothing per question."""
    rows = _load_full("D5")
    if limit:
        rows = rows[:limit]
    print()
    print("[D5 instruction] %d questions, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        prompt = r["user"]
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            passed, results = _judge_d5(r, res["text"])
            rec = {"id": r["id"], "subcat": r.get("subcat"), "prompt": prompt,
                   "response": res["text"], "latency_s": res["latency_s"],
                   "correct": passed, "checker_results": results.get("checker_results", []),
                   "judge": results.get("judge")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"), "prompt": prompt,
                              "response": "", "checker_results": [], "reason": str(e)}, e)
        records.append(rec)
        if verbose and (i % 10 == 0 or i == len(rows)):
            acc = sum(1 for x in records if x["correct"]) / len(records)
            print("  %3d/%3d  running pass=%.1f%%" % (i, len(rows), acc * 100))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    # per-checker-type stats
    ctype = {}
    for x in records:
        for cr in x.get("checker_results", []):
            b = ctype.setdefault(cr["type"], {"n": 0, "passed": 0})
            b["n"] += 1
            b["passed"] += 1 if cr["passed"] else 0
    return ({"dim": "D5", "suite": "instruction", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct,
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "elapsed_s": dt, "by_checker": ctype}, records)

def suite_chess(ep, limit=None, verbose=True):
    """D10 board-game dimension (chess): engine-verified answers."""
    path = os.path.join(ROOT, "datasets", "generated", "D10_generated.jsonl")
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print()
    print("[D10 棋类] %d questions, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    # 检查点:长跑要能续,也要能看见进度(日志是块缓冲,不可靠)
    ck = Checkpoint("chess", ep.name, [x.get("id") for x in rows])
    done = ck.load() if RESUME else {}

    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        try:
            res = ep.chat([{"role": "user", "content": r["question"]}])
            v = judge_chess(r, res["text"])
            rec = {"id": r["id"], "subcat": r.get("subcat"), "fen": r.get("fen"),
                   "question": r["question"], "response": res["text"],
                   "latency_s": res["latency_s"],
                   "expected": r.get("answer") or r.get("answer_san"),
                   "correct": v.get("correct"), "reason": v.get("reason")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"),
                              "question": r["question"], "response": "",
                              "reason": str(e)}, e)
        records.append(rec)
        ck.add(rec)                       # 每题落盘:崩溃可续,进度可见
        if verbose and (i % 10 == 0 or i == len(rows)):
            print(progress_line(i, len(rows), records))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    by_sub = {}
    for x in records:
        s = x.get("subcat") or "unknown"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if x["correct"] else 0
    return ({"dim": "D10", "suite": "chess", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct,
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub}, records)

def _prompt_for(row):
    """Build the model prompt for one question, dispatching by dimension."""
    dim = row.get("dim")
    if dim == "D2":
        opts = row.get("options") or {}
        # Two shapes exist in the bank: {"A": "..."} (15,783 rows) and a bare
        # ["A. ...", "B. ..."] list (3 legacy rows). Calling .items() on the list
        # crashed the whole canary run at question 20, taking every dimension
        # after it down with it - a single malformed row must never do that.
        if isinstance(opts, dict):
            opt_txt = chr(10).join("%s. %s" % (k, v) for k, v in sorted(opts.items()))
        elif isinstance(opts, list):
            opt_txt = chr(10).join(str(x) for x in opts)
        else:
            opt_txt = ""
        if row.get("terse"):
            # terse mode: for paired probes we only need the choice, and cutting the
            # model's self-explanation takes the run from ~25s to ~6s per question.
            # Both sides of the pair use the same prompt, so the DELTA stays valid
            # even though the absolute score is not comparable to earlier runs.
            return (row["question"] + chr(10) + chr(10) + opt_txt + chr(10) + chr(10) +
                    "Do not explain. Do not reason step by step. "
                    "Output exactly one line: answer: <letter>")
        return (row["question"] + chr(10) + chr(10) + opt_txt + chr(10) + chr(10) +
                "Reply with the single best option letter only, on a final line: answer: <letter>")
    if dim == "D3":
        return row["question"] + chr(10) + chr(10) + "Please end with a line: answer: <value>"
    if dim == "D5":
        return row["user"]
    if dim == "D10":
        return row["question"]
    if dim == "D4":
        return _code_prompt(row)
    if dim == "D6":
        return row.get("user") or ""
    if dim == "D8":
        return _reliability_prompt(row)
    if dim == "D9":
        # Exec tasks carry their files in exec.files, NOT in the question text.
        # The model was being asked to "read check_ini.py and fix app.ini" while
        # seeing neither, so every config-fix question scored 1/9 and every reply
        # was "please paste the files". The files are part of the task.
        text = row.get("user") or ""
        ex = row.get("exec") or {}
        files = ex.get("files") or {}
        if files:
            parts = [text, "", "=" * 60, "工作区文件（内容如下，请直接使用，不要要求用户提供）：", "=" * 60]
            for name, body in files.items():
                parts.append("")
                parts.append("--- %s ---" % name)
                parts.append(str(body).rstrip())
            parts.append("")
            parts.append("=" * 60)
            target = ex.get("fix_file")
            if target:
                parts.append("请只输出修正后的 %s 完整内容，放在代码块里。" % target)
            return chr(10).join(parts)
        return text
    if dim == "D7":
        parts = []
        if row.get("context_file"):
            parts.append(_read_ctx(row["context_file"]))
        for cf in row.get("context_files", []) or []:
            parts.append(_read_ctx(cf))
        return chr(10).join(parts) + chr(10) + chr(10) + row["question"]
    return row.get("question") or row.get("user") or ""


TOOL_SCHEMAS = {}


def _load_tool_schemas():
    """Load config/tool_schemas.json so tools carry real parameter schemas (BFCL-style).
    A bare name with no parameters makes a careful model refuse: it cannot know
    what arguments the tool needs."""
    global TOOL_SCHEMAS
    if TOOL_SCHEMAS:
        return TOOL_SCHEMAS
    path = os.path.join(ROOT, "config", "tool_schemas.json")
    if os.path.exists(path):
        try:
            TOOL_SCHEMAS = json.load(open(path, encoding="utf-8"))
        except Exception:
            TOOL_SCHEMAS = {}
    return TOOL_SCHEMAS


_VALID_JSON_TYPES = {"object", "array", "string", "integer", "number",
                     "boolean", "null"}


def _sanitize_schema(node):
    """Coerce non-standard JSON-Schema type names anywhere in the tree.
    A single bad type makes an OpenAI-compatible server reject the WHOLE request
    (llama-server returns HTTP 500), which silently zeroes an entire question set -
    that is exactly what happened with BFCL's 100 "dict" parameters."""
    fix = {"dict": "object", "list": "array", "tuple": "array", "set": "array",
           "str": "string", "int": "integer", "float": "number", "bool": "boolean",
           "text": "string", "double": "number"}
    if isinstance(node, dict):
        return {k: (fix.get(v.lower(), v)
                    if (k == "type" and isinstance(v, str)) else _sanitize_schema(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_sanitize_schema(x) for x in node]
    return node


def assert_tool_specs(specs):
    """Fail loudly on a malformed tool spec instead of letting the server 500."""
    for t in specs or []:
        if not isinstance(t, dict) or t.get("type") != "function" or "function" not in t:
            raise ValueError("tool spec not in OpenAI shape: %r" % (str(t)[:120],))
        fn = t["function"]
        if not fn.get("name"):
            raise ValueError("tool spec without a name")
        pt = (fn.get("parameters") or {}).get("type")
        if pt is not None and pt not in _VALID_JSON_TYPES:
            raise ValueError("invalid JSON-Schema type %r in tool %r" % (pt, fn.get("name")))
    return True


def tool_specs(names):
    """Build OpenAI tool specs for a list of tool names, using the schema registry."""
    reg = _load_tool_schemas()
    out = []
    for n in names or []:
        if isinstance(n, dict):
            # A bare {name, description, parameters} (BFCL's native shape) must still
            # be wrapped for the OpenAI-compatible API, otherwise the server rejects
            # the request and every question in the set fails.
            if "type" in n and "function" in n:
                out.append(n)                      # already an OpenAI tool spec
            else:
                out.append({"type": "function", "function": n})
            continue
        spec = reg.get(n)
        if spec is None:
            spec = {"type": "function",
                    "function": {"name": n, "description": n,
                                 "parameters": {"type": "object", "properties": {}}}}
        spec = {"type": "function",
                "function": {"name": spec["function"]["name"],
                             "description": spec["function"].get("description", ""),
                             "parameters": _sanitize_schema(
                                 spec["function"].get("parameters")
                                 or {"type": "object", "properties": {}})}}
        out.append(spec)
    assert_tool_specs(out)
    return out


def _inject_agent_env(msgs):
    """Prepend the agent tool environment to the first user message.

    Only the first message is prefixed: that is where a real harness puts its tool
    block, and repeating it per turn would misrepresent both the token count and the
    prefill cost that the measurement is about.
    """
    if AGENT_MODE == "bare" or not msgs:
        return msgs
    out = list(msgs)
    for i, m in enumerate(out):
        if m.get("role") == "user":
            out[i] = dict(m)
            out[i]["content"] = agent_apply_prefix(m.get("content") or "", AGENT_MODE)
            break
    return out


def _request_for(row):
    """Build (messages, extra_kwargs) for one question. D6 needs the tools parameter."""
    dim = row.get("dim")
    if dim == "D6":
        sub = row.get("subcat")
        if sub == "multi-turn-state":
            msgs = [{"role": m["role"], "content": m["content"]}
                    for m in row.get("dialogue", []) if m.get("role") in ("user", "assistant")]
            if not msgs or msgs[-1]["role"] != "user":
                msgs.append({"role": "user", "content": "请确认最终安排。"})
            return msgs, {}
        # ALWAYS route through tool_specs: a raw dict from a dataset may be a bare
        # {name, description, parameters} (BFCL) or carry an invalid JSON-Schema type,
        # and passing it straight to the server is what caused the BFCL 500s.
        specs = tool_specs(row.get("tools") or [])
        kwargs = {}
        if specs:
            kwargs["tools"] = specs
            kwargs["tool_choice"] = "auto"
        return _inject_agent_env([{"role": "user", "content": row.get("user", "")}]), kwargs
    msgs = [{"role": "user", "content": _prompt_for(row)}]
    return _inject_agent_env(msgs), {}


def _judge_for(row, text, result=None):
    """Judge one answer, dispatching by dimension."""
    dim = row.get("dim")
    if dim == "D3":
        v = judge_math(text, row.get("answer"), row.get("tolerance", "exact"))
        return bool(v.get("correct")), v
    if dim == "D2":
        v = judge_mcq(text, row.get("answer_letter"))
        return bool(v.get("correct")), v
    if dim == "D5":
        return _judge_d5(row, text)
    if dim == "D10":
        v = judge_chess(row, text)
        return bool(v.get("correct")), v
    if dim == "D7":
        v = judge_longctx(row, text)
        return bool(v.get("correct")), v
    if dim == "D4":
        v = judge_code(row, text)
        return bool(v.get("correct")), v
    if dim == "D6":
        v = judge_agent(row, result or {}, text)
        return bool(v.get("correct")), v
    if dim == "D8":
        v = judge_reliability(row, text)
        return bool(v.get("correct")), v
    if dim == "D9":
        qs, base = _load_d9()
        b = base.get(row.get("id"))
        if not b:
            return False, {"reason": "no baseline checks for " + str(row.get("id"))}
        v = judge_d9(b, text)
        return bool(v.get("correct")), v
    return False, {"reason": "unknown dim " + str(dim)}


def suite_subset(ep, level, verbose=True):
    """Run a predefined test level (smoke / canary / full)."""
    path = os.path.join(ROOT, "datasets", "subsets", "%s.jsonl" % level)
    rows = load_jsonl(path)
    # ctx gate from test_levels.json (D7 questions carry min_ctx)
    max_ctx = 0
    try:
        lv = json.load(open(os.path.join(ROOT, "config", "test_levels.json"), encoding="utf-8"))
        max_ctx = lv.get("levels", {}).get(level, {}).get("max_ctx", 0) or 0
    except Exception:
        max_ctx = 0
    ctx_dropped = []
    if max_ctx:
        keep, dropped = [], []
        for r in rows:
            need = r.get("min_ctx") or 0
            if need and need > max_ctx:
                dropped.append(r.get("id"))
            else:
                keep.append(r)
        if dropped:
            print("  [ctx gate] 跳过 %d 题(需要 ctx > %d): %s"
                  % (len(dropped), max_ctx, ", ".join(str(x) for x in dropped[:6])))
        rows = keep
        ctx_dropped = list(dropped)
    print()
    print("[%s] %d questions, endpoint=%s model=%s" % (level.upper(), len(rows), ep.name, ep.model))

    # ---- resume: a long run must survive a reboot / sleep / API stall ----
    ck = Checkpoint(level, ep.name, [r.get("id") for r in rows])
    done = ck.load() if RESUME else {}

    # ---- environment + launch provenance (R1) ----
    # Detected, recorded, never blocking: a slightly noisy run is still useful when
    # the conditions are attached to it.
    env_before = envmod.assess()
    launch_snap = launchmod.capture(host=getattr(ep, "host", None) or "127.0.0.1",
                                    port=None)
    if env_before.get("trust_level") != "high":
        print("  [env] 干扰检测:trust=%s  %s"
              % (env_before["trust_level"], "; ".join(env_before.get("warnings") or [])))
    _procs = (launch_snap.get("processes") or [])
    if _procs and _procs[0].get("raw"):
        dv = _procs[0].get("derived") or {}
        print("  [launch] %s | ctx=%s ngl=%s kv=%s"
              % (_procs[0].get("exe"), dv.get("context", "?"),
                 dv.get("gpu_offload", "?"), dv.get("kv_cache", "?")))
    if done:
        print("  [resume] 从检查点恢复:%d/%d 题已完成,续跑剩余 %d 题"
              % (len(done), len(rows), len(rows) - len(done)))
        print("           %s" % os.path.relpath(ck.path, ROOT))
    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        prev = done.get(r.get("id"))
        if prev is not None:
            records.append(prev)          # already finished in an earlier attempt
            if verbose and (i % 10 == 0 or i == len(rows)):
                print("  %3d/%3d  (已从检查点跳过)" % (i, len(rows)))
            continue
        msgs, kwargs = _request_for(r)
        try:
            if kwargs.get("tools"):
                # D6 must use the real multi-round tool loop, same as suite_agent
                calls, final_text, first_text, loop_s = _run_tool_loop(
                    ep, msgs, kwargs["tools"])
                res = {"text": final_text or first_text, "tool_calls": calls,
                       "latency_s": loop_s}
            else:
                res = ep.chat(msgs, **kwargs)
            ok, detail = _judge_for(r, res.get("text", ""), res)
            res.setdefault("latency_s", None)
            u = res.get("usage") or {}
            rec = {"id": r.get("id"), "dim": r.get("dim"), "subcat": r.get("subcat"),
                   "probe_of": r.get("probe_of"),      # needed for contamination pairing
                   "response": res["text"],
                   "tool_calls": res.get("tool_calls") or [],   # needed for D6 re-judging
                   "latency_s": res["latency_s"],
                   "prompt_tokens": u.get("prompt_tokens"),
                   "completion_tokens": u.get("completion_tokens"),
                   "correct": ok, "detail": detail}
        except EndpointError as e:
            rec = mark_error({"id": r.get("id"), "dim": r.get("dim"),
                              "subcat": r.get("subcat"), "response": "",
                              "detail": {"reason": "endpoint error: " + str(e)[:120]}}, e)
        records.append(rec)
        ck.add(rec)                    # flushed immediately: survives a kill
        if verbose and (i % 10 == 0 or i == len(rows)):
            _ok = sum(1 for x in records if x["correct"])
            _err = sum(1 for x in records if x.get("error"))
            _valid = len(records) - _err
            acc = _ok / _valid if _valid else 0.0
            print("  %3d/%3d  running acc=%.1f%%%s"
                  % (i, len(rows), acc * 100,
                     ("  [%d 次接口错误未计入]" % _err) if _err else ""))
    ck.close()
    dt = time.perf_counter() - t0
    env_after = envmod.assess()
    env_after["drift"] = envmod.diff_before_after(env_before, env_after)
    env_after["before"] = {"trust_level": env_before.get("trust_level"),
                           "warnings": env_before.get("warnings")}
    if env_before.get("trust_level") != env_after.get("trust_level"):
        print("  [env] 评测期间信任度变化:%s -> %s"
              % (env_before.get("trust_level"), env_after.get("trust_level")))
    correct = sum(1 for x in records if x["correct"])
    n_err = sum(1 for x in records if x.get("error"))
    n_valid = len(records) - n_err
    by_dim = {}
    for x in records:
        if x.get("error"):
            continue                      # never count an API failure as an answer
        d = x.get("dim") or "?"
        b = by_dim.setdefault(d, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if x["correct"] else 0
    pin = sum((x.get("prompt_tokens") or 0) for x in records)
    pout = sum((x.get("completion_tokens") or 0) for x in records)
    summary = {"level": level, "endpoint": ep.name, "model": ep.model,
               "n": len(records), "correct": correct,
               "n_valid": n_valid, "n_errors": n_err,
               "accuracy": correct / n_valid if n_valid else 0.0,
               "accuracy_with_errors": correct / len(records) if records else 0.0,
               "elapsed_s": dt, "by_dim": by_dim,
               "prompt_tokens": pin, "completion_tokens": pout,
               "est_cost_cny": None,
               "agent_mode": AGENT_MODE,
               "max_ctx_gate": max_ctx or None,
               "ctx_skipped": ctx_dropped,
               "notes": ([("ctx 门控跳过 %d 题(需 ctx > %d),该维度得分仅代表此上下文以内"
                          % (len(ctx_dropped), max_ctx))] if ctx_dropped else []),
               "_ckpt_path": ck.path, "_ckpt_key": ck.key,
               "environment": env_after,
               "launch": launch_snap}
    return summary, records


def _read_ctx(rel_path):
    p = os.path.join(ROOT, "datasets", rel_path)
    return open(p, encoding="utf-8").read()


def suite_longctx(ep, limit=None, max_ctx=8192, verbose=True):
    """D7 long context. Records REAL prompt tokens so a degradation curve can be plotted."""
    path = os.path.join(ROOT, "datasets", "full", "D7_full.jsonl")
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    usable, skipped = [], []
    for r in rows:
        need = r.get("min_ctx") or 0
        if max_ctx and need and need > max_ctx:
            skipped.append((r.get("id"), need))
        else:
            usable.append(r)
    print()
    print("[D7 长上下文] %d 题可用, %d 题因超出 max_ctx=%d 跳过, endpoint=%s"
          % (len(usable), len(skipped), max_ctx, ep.name))
    for sid, need in skipped:
        print("    [skip] %s 需要 ctx>=%d" % (sid, need))
    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(usable, 1):
        parts = []
        if r.get("context_file"):
            parts.append(_read_ctx(r["context_file"]))
        for cf in r.get("context_files", []) or []:
            parts.append(_read_ctx(cf))
        prompt = chr(10).join(parts) + chr(10) + chr(10) + r["question"]
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            v = judge_longctx(r, res["text"])
            usage = res.get("usage") or {}
            rec = {"id": r["id"], "subcat": r.get("subcat"),
                   "size": r.get("context_tokens_approx"), "depth": r.get("needle_depth"),
                   "prompt_tokens": usage.get("prompt_tokens"),
                   "response": res["text"], "latency_s": res["latency_s"],
                   "correct": bool(v.get("correct")), "reason": v.get("reason"),
                   "partial_score": v.get("partial_score")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"),
                              "size": r.get("context_tokens_approx"),
                              "depth": r.get("needle_depth"), "response": "",
                              "reason": str(e)}, e)
        records.append(rec)
        if verbose:
            print("  %2d/%2d  %-28s %s  (%.1fs)"
                  % (i, len(usable), rec["id"], "PASS" if rec["correct"] else "fail",
                     rec.get("latency_s") or 0))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_sub, by_size = {}, {}
    for x in records:
        s = x.get("subcat") or "?"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0, "errors": 0})
        b["n"] += 1; b["correct"] += 1 if x["correct"] else 0
        b["errors"] += 1 if x.get("error") else 0
        if s == "needle-single":
            k = x.get("size")
            c = by_size.setdefault(k, {"n": 0, "correct": 0, "errors": 0, "depths": {}})
            c["n"] += 1; c["correct"] += 1 if x["correct"] else 0
            c["errors"] += 1 if x.get("error") else 0
            if x.get("error"):
                c["depths"][x.get("depth")] = "ERROR"
            else:
                c["depths"][x.get("depth")] = "PASS" if x["correct"] else "fail"
    return ({"dim": "D7", "suite": "longctx", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct, "errors": errors, "skipped": len(skipped),
             "valid_n": len(records) - errors,
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "accuracy_valid": correct / (len(records) - errors) if (len(records) - errors) else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub, "by_size": by_size}, records)


def _code_prompt(row):
    """Build the prompt for one D4 question."""
    sub = row.get("subcat")
    if sub == "sql":
        return ("数据库表结构:" + chr(10) + (row.get("schema") or "") + chr(10) + chr(10) +
                row.get("question", "") + chr(10) + chr(10) +
                "请只输出一条 SQL 查询,放在 ```sql 代码块中。")
    if sub == "debug":
        return ("下面这段 Python 代码有问题:" + chr(10) + "```python" + chr(10) +
                (row.get("buggy") or "") + chr(10) + "```" + chr(10) + chr(10) +
                row.get("task", "") + chr(10) + chr(10) +
                "请给出修复后的完整函数,放在 ```python 代码块中,不要改动函数名。")
    return ("请补全下面的 Python 函数实现:" + chr(10) + "```python" + chr(10) +
            (row.get("prompt") or "") + chr(10) + "```" + chr(10) + chr(10) +
            "请只输出完整的函数实现,放在 ```python 代码块中。")


def suite_code(ep, limit=None, verbose=True):
    """D4 code: pass@1 in an isolated sandbox."""
    path = os.path.join(ROOT, "datasets", "full", "D4_full.jsonl")
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print()
    print("[D4 代码] %d 题, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        try:
            res = ep.chat([{"role": "user", "content": _code_prompt(r)}])
            v = judge_code(r, res["text"])
            rec = {"id": r["id"], "subcat": r.get("subcat"), "response": res["text"],
                   "latency_s": res["latency_s"], "correct": bool(v.get("correct")),
                   "stage": v.get("stage"), "reason": v.get("reason")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"), "response": "",
                              "reason": str(e)}, e)
        records.append(rec)
        if verbose:
            print("  %2d/%2d  %-22s %-4s  %s (%.1fs)"
                  % (i, len(rows), rec["id"], "PASS" if rec["correct"] else "fail",
                     str(rec.get("stage")), rec.get("latency_s") or 0))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_sub, by_stage = {}, {}
    for x in records:
        s = x.get("subcat") or "?"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0, "errors": 0})
        b["n"] += 1; b["correct"] += 1 if x["correct"] else 0
        b["errors"] += 1 if x.get("error") else 0
        st = x.get("stage") or "?"
        by_stage[st] = by_stage.get(st, 0) + 1
    return ({"dim": "D4", "suite": "code", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct, "errors": errors,
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub, "by_stage": by_stage}, records)


# mock tool results so a multi-round tool loop can proceed offline
MOCK_TOOL_RESULTS = {
    "read_file": "- [ ] 交季度报告 (P0)" + chr(10) + "- [ ] 回复客户邮件 (P1)" + chr(10) + "- [x] 归档旧纪要",
    "list_dir": "todo.md  notes.md  archive/",
    "search_flights": "CA1501 08:00; MU5102 10:30; HU7605 14:20",
    "book_flight": "OK booked CA1501",
    "list_customers": "cust1,cust2,cust3 (共 10 位)",
    "send_email_batch": "OK queued 10 emails",
    "send_email": "OK sent",
    "get_weather": "晴, 12-20C",
    "convert_pdf": "OK converted to docx",
    "extract_entities": "amount=1200000; date=2026-03-01",
    "translate": "translated text",
    "track_order": "in transit",
    "get_phone_balance": "38.50 CNY",
}


def _mock_tool_result(name, args=None):
    """Scenario-aware: derive the fake result from the tool AND its arguments, so a
    capable model does not abandon the step because the stub looks wrong."""
    return mock_tool_result(name, args)


def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def _run_tool_loop(ep, msgs, specs, max_rounds=4, verbose=False):
    """Drive a REAL multi-round tool loop: model -> tool_calls -> synthetic result -> repeat.
    Expecting a full tool chain in ONE turn is unrealistic; agents work across rounds.
    Returns (all_tool_calls, final_text, first_text, elapsed_s).

    elapsed_s 是**各轮之和**,不是最后一轮的耗时 —— 多轮工具调用里
    单轮延迟没有意义,用户关心的是这一题总共花了多久。"""
    all_calls, final_text, first_text = [], "", ""
    t0 = time.perf_counter()
    convo = list(msgs)
    for rnd in range(max_rounds):
        res = ep.chat(convo, tools=specs, tool_choice="auto")
        text = res.get("text") or ""
        if rnd == 0:
            first_text = text
        calls = res.get("tool_calls") or []
        if not calls:
            final_text = text
            break
        all_calls.extend(calls)
        convo.append({"role": "assistant", "content": text or None, "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            name = fn.get("name") or ""
            convo.append({"role": "tool", "tool_call_id": c.get("id") or name,
                          "content": _mock_tool_result(name, _parse_args(fn.get("arguments")))})
        if verbose:
            print("      [round %d] called %s" % (rnd + 1, [ (c.get('function') or {}).get('name') for c in calls ]))
    return all_calls, final_text, first_text, time.perf_counter() - t0


def suite_agent(ep, limit=None, verbose=True):
    """D6 agent: native tool calling when available, text/JSON fallback otherwise."""
    path = os.path.join(ROOT, "datasets", "full", "D6_full.jsonl")
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print()
    print("[D6 Agent] %d 题, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    # 检查点:长跑要能续,也要能看见进度(日志是块缓冲,不可靠)
    ck = Checkpoint("agent", ep.name, [x.get("id") for x in rows])
    done = ck.load() if RESUME else {}

    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        sub = r.get("subcat")
        kwargs = {}
        # multi-turn-state is a plain dialogue; the others take a tool list
        if sub == "multi-turn-state":
            msgs = [{"role": m["role"], "content": m["content"]}
                    for m in r.get("dialogue", []) if m.get("role") in ("user", "assistant")]
            if not msgs or msgs[-1]["role"] != "user":
                msgs.append({"role": "user", "content": "请确认最终安排。"})
        else:
            msgs = [{"role": "user", "content": r.get("user", "")}]
            # ALWAYS through tool_specs (3rd occurrence of this same bypass):
            # a raw dict may be BFCL's bare shape or carry an invalid schema type,
            # and either one makes the server reject the whole request with HTTP 500.
            specs = tool_specs(r.get("tools") or [])
            if specs:
                kwargs["tools"] = specs
                kwargs["tool_choice"] = "auto"
        try:
            if kwargs.get("tools"):
                calls, final_text, first_text, loop_s = _run_tool_loop(
                    ep, msgs, kwargs["tools"])
                res = {"text": final_text or first_text, "tool_calls": calls,
                       "latency_s": loop_s}
                v = judge_agent(r, res, res["text"])
                rec = {"id": r["id"], "subcat": sub, "response": res["text"],
                       "tool_calls": calls, "rounds_called": len(calls),
                       # 原来这里是硬编码不写,导致 D6 的 147 题里只有 1 题
                       # 有耗时,性能区算出来全是 0。多轮耗时 = 各轮之和。
                       "latency_s": loop_s,
                       "correct": bool(v.get("correct")), "source": v.get("source"),
                       "called": v.get("called"), "reason": v.get("reason")}
            else:
                res = ep.chat(msgs, **kwargs)
                v = judge_agent(r, res, res.get("text", ""))
                rec = {"id": r["id"], "subcat": sub, "response": res.get("text", ""),
                       "tool_calls": res.get("tool_calls") or [],
                       "latency_s": res.get("latency_s"),
                       "correct": bool(v.get("correct")), "source": v.get("source"),
                       "called": v.get("called"), "reason": v.get("reason")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": sub, "response": "",
                              "reason": str(e)}, e)
        records.append(rec)
        ck.add(rec)                       # 每题落盘:崩溃可续,进度可见
        if verbose and (i % 10 == 0 or i == len(rows)):
            _ok = sum(1 for x in records if x["correct"])
            _err = sum(1 for x in records if x.get("error"))
            _valid = len(records) - _err
            acc = _ok / _valid if _valid else 0.0
            print("  %3d/%3d  running acc=%.1f%%%s"
                  % (i, len(rows), acc * 100,
                     ("  [%d 次接口错误未计入]" % _err) if _err else ""))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_sub, by_src = {}, {}
    for x in records:
        s = x.get("subcat") or "?"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0, "errors": 0})
        b["n"] += 1; b["correct"] += 1 if x["correct"] else 0
        b["errors"] += 1 if x.get("error") else 0
        src_name = x.get("source") or "none"
        by_src[src_name] = by_src.get(src_name, 0) + 1
    return ({"dim": "D6", "suite": "agent", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct, "errors": errors,
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub, "by_source": by_src}, records)


def _reliability_prompt(row):
    """D8 prompt. Injection questions embed the poisoned external content."""
    user = row.get("user") or ""
    ext = row.get("external_content")
    if ext:
        return (user + chr(10) + chr(10) + "--- 待处理内容 ---" + chr(10) + ext +
                chr(10) + "--- 内容结束 ---")
    return user


def suite_reliability(ep, limit=None, verbose=True):
    """D8 reliability: hallucination / calibration / injection / refusal."""
    path = os.path.join(ROOT, "datasets", "full", "D8_full.jsonl")
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print()
    print("[D8 可靠性] %d 题, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    # 检查点:长跑要能续,也要能看见进度(日志是块缓冲,不可靠)
    ck = Checkpoint("reliability", ep.name, [x.get("id") for x in rows])
    done = ck.load() if RESUME else {}

    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        try:
            res = ep.chat([{"role": "user", "content": _reliability_prompt(r)}])
            v = judge_reliability(r, res.get("text", ""))
            rec = {"id": r["id"], "subcat": r.get("subcat"), "response": res.get("text", ""),
                   "latency_s": res.get("latency_s"), "correct": bool(v.get("correct")),
                   "verdict": v.get("verdict"), "reason": v.get("reason")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"), "response": "",
                              "reason": str(e)}, e)
        records.append(rec)
        ck.add(rec)                       # 每题落盘:崩溃可续,进度可见
        if verbose and (i % 10 == 0 or i == len(rows)):
            print(progress_line(i, len(rows), records))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_sub = {}
    for x in records:
        s = x.get("subcat") or "?"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0, "errors": 0})
        b["n"] += 1; b["correct"] += 1 if x["correct"] else 0
        b["errors"] += 1 if x.get("error") else 0
    # calibration-specific breakdown
    cal = {}
    for x in records:
        if x.get("subcat") == "calibration" and x.get("verdict"):
            cal[x["verdict"]] = cal.get(x["verdict"], 0) + 1
    return ({"dim": "D8", "suite": "reliability", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct, "errors": errors,
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub, "calibration_verdicts": cal}, records)


def _load_d9():
    """D9 questions + their frozen machine-checkable baseline.

    The frozen baseline only covers the hand-written questions that predate the
    generator. A question carrying its own `checks` (or an `exec` block) is
    already machine-checkable without one, so fall back to the question row.
    Silently skipping those is how 70 generated questions became unrunnable
    while still counting as "in the bank".
    """
    qs = load_jsonl(os.path.join(ROOT, "datasets", "full", "D9_full.jsonl"))
    bp = os.path.join(ROOT, "datasets", "d9_baseline.jsonl")
    base = {}
    if os.path.exists(bp):
        for r in load_jsonl(bp):
            base[r["id"]] = r
    for q in qs:
        if q["id"] in base:
            continue
        if q.get("checks") or q.get("exec"):
            base[q["id"]] = q
    return qs, base


def suite_realworld(ep, limit=None, verbose=True):
    """D9 real-world tasks, scored by RULES against a frozen cloud baseline.
    No LLM judge -> zero marginal cost."""
    qs, base = _load_d9()
    if limit:
        qs = qs[:limit]
    missing = [q["id"] for q in qs if q["id"] not in base or not base[q["id"]].get("checks")]
    print()
    print("[D9 实战] %d 题, endpoint=%s model=%s" % (len(qs), ep.name, ep.model))
    if missing:
        print("  *** %d 题没有基准检查项,将跳过: %s" % (len(missing), ", ".join(missing[:5])))
    records = []
    # 检查点:长跑要能续,也要能看见进度(日志是块缓冲,不可靠)
    ck = Checkpoint("realworld", ep.name, [x.get("id") for x in qs])
    done = ck.load() if RESUME else {}

    t0 = time.perf_counter()
    exec_root = os.path.join(ROOT, "results", ".d9exec")
    for i, q in enumerate(qs, 1):
        b = base.get(q["id"])
        # A question is runnable if it has rule checks OR an executable block.
        # Exec questions have no checks by design - they are scored by running.
        if not b or not (b.get("checks") or b.get("exec")):
            continue
        try:
            # Use _prompt_for so exec tasks receive their files. Sending q["user"]
            # alone asked the model to "fix app.ini" while showing it neither the
            # file nor the validator, and every reply was "please paste the files".
            res = ep.chat([{"role": "user", "content": _prompt_for(q)}])
            # The exec block lives on the QUESTION row, not on the baseline. Testing
            # b.get("exec") was always False, so every exec question fell through to
            # keyword checks and the runner never executed anything: a correct
            # config that failed to say "端口越界" scored 0.
            ex = q.get("exec") or b.get("exec")
            if ex:
                # Exec task: the only honest check is to run the candidate code.
                merged = dict(b)
                merged["exec"] = ex
                v = d9exec.run_exec_task(merged, res.get("text", ""), exec_root)
                rec = {"id": q["id"], "subcat": q.get("subcat"), "response": res.get("text", ""),
                       "latency_s": res.get("latency_s"), "correct": bool(v.get("correct")),
                       "coverage": 1.0 if v.get("correct") else 0.0,
                       "violations": [], "checks": None, "reason": v.get("reason"),
                       "exit_code": v.get("exit_code"), "timed_out": v.get("timeout"),
                       "scored_by": "exec", "baseline_source": "exec"}
            else:
                v = judge_d9(b, res.get("text", ""))
                rec = {"id": q["id"], "subcat": q.get("subcat"), "response": res.get("text", ""),
                       "latency_s": res.get("latency_s"), "correct": bool(v.get("correct")),
                       "coverage": v.get("coverage"), "violations": v.get("violations"),
                       "checks": v.get("checks"), "reason": v.get("reason"),
                       "scored_by": "rules", "baseline_source": b.get("baseline_source")}
        except EndpointError as e:
            rec = mark_error({"id": q["id"], "subcat": q.get("subcat"), "response": "",
                              "reason": str(e)}, e)
        records.append(rec)
        ck.add(rec)                       # 每题落盘:崩溃可续,进度可见
        if verbose:
            print("  %2d/%2d  %-14s %-4s 覆盖 %3.0f%%  %s"
                  % (i, len(qs), q["id"], "PASS" if rec["correct"] else "fail",
                     (rec.get("coverage") or 0) * 100, (rec.get("reason") or "")[:40]))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    covs = [x["coverage"] for x in records if x.get("coverage") is not None]
    by_sub = {}
    for x in records:
        s = x.get("subcat") or "?"
        b2 = by_sub.setdefault(s, {"n": 0, "correct": 0})
        b2["n"] += 1; b2["correct"] += 1 if x["correct"] else 0
    return ({"dim": "D9", "suite": "realworld", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "correct": correct, "errors": errors, "skipped": len(missing),
             "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
             "mean_coverage": (sum(covs) / len(covs)) if covs else 0.0,
             "elapsed_s": dt, "by_subcat": by_sub,
             "baseline_source": (list(base.values())[0].get("baseline_source") if base else None)}, records)


def suite_compression(ep, limit=None, verbose=True, seeds=None):
    """D12: paired before/after compaction probe.

    The same scenario is asked twice - once with the full document, once after a
    lossy summariser replaced it. The difference is the retention loss, and the
    pairing is what makes the comparison meaningful (McNemar on the two outcomes).
    """
    import compression as comp
    # 8 -> 24:每个场景要问两遍(压缩前/压缩后)才算一次配对,8 个种子
    # 只产生 8 组 McNemar 配对,统计上太单薄。24 组就能看出稳定趋势。
    # 已验证 24 个种子产生 24 个互不重复的场景(按 question+summary 哈希).
    seeds = seeds or [20261101 + i for i in range(24)]
    if limit:
        seeds = seeds[:limit]
    print()
    print("[D12 压缩降智] %d 个场景, endpoint=%s model=%s" % (len(seeds), ep.name, ep.model))
    base_recs, comp_recs = [], []
    t0 = time.perf_counter()
    for i, seed in enumerate(seeds, 1):
        sc = comp.build_scenario(seed=seed)
        sid = "d12_%d" % seed
        try:
            r1 = ep.chat([{"role": "user", "content": comp.build_prompt(sc, compacted=False)}])
            v1 = comp.judge_retention(sc, r1.get("text", ""))
            base_recs.append({"id": sid, "correct": v1["correct"], "reason": v1["reason"],
                              "response": r1.get("text", ""), "mode": "full"})
        except EndpointError as e:
            base_recs.append(mark_error({"id": sid, "mode": "full", "response": ""}, e))
        try:
            # Do NOT pass the baseline answer forward. It contains the very fact
            # being tested ("PRJ-xxxx 由 某某 负责"), so feeding it back turns
            # this into a copying test and every scenario scores 100%. After
            # compaction the model sees ONLY the lossy summary plus the question.
            r2 = ep.chat([{"role": "user", "content": comp.build_prompt(sc, compacted=True)}])
            v2 = comp.judge_retention(sc, r2.get("text", ""))
            comp_recs.append({"id": sid, "correct": v2["correct"], "reason": v2["reason"],
                              "response": r2.get("text", ""), "mode": "compacted"})
        except EndpointError as e:
            comp_recs.append(mark_error({"id": sid, "mode": "compacted", "response": ""}, e))
        if verbose:
            b = base_recs[-1].get("correct")
            c = comp_recs[-1].get("correct")
            print("  %2d/%2d %-16s 压缩前=%-5s 压缩后=%-5s" %
                  (i, len(seeds), sid, "PASS" if b else "fail", "PASS" if c else "fail"))
    dt = time.perf_counter() - t0
    stat = comp.summarize_retention(base_recs, comp_recs)
    stat.update({"dim": "D12", "suite": "compression", "endpoint": ep.name,
                 "model": ep.model, "elapsed_s": dt, "compression_ratio": 0.075})
    return stat, base_recs + comp_recs

def suite_vision(ep, limit=None, verbose=True):
    """D11: can the model read what is actually drawn?

    Images are generated in code so the ground truth is exact - a photo would
    need an LLM judge, which this project forbids. Scoring is an exact match on
    the answer, tolerant of surrounding prose.
    """
    import vision as vismod
    root = os.path.join(ROOT, 'datasets', 'fixtures', 'vision')
    items = vismod.build_all(root)
    if limit:
        items = items[:limit]
    print()
    print("[D11 视觉] %d 题, endpoint=%s model=%s" % (len(items), ep.name, ep.model))

    # 没视觉能力的模型直接跳过整个维度,而不是逐题失败。
    #
    # 为什么:视觉需要引擎加载 mmproj 视觉投影器。裸文本引擎收到图片会
    # 返回 400/404 或干脆忽略图片 —— 逐题跑下去只是把 9 道题全记成错误,
    # 最后由 accuracy 算成 0%,看起来像"模型读图能力为零",实际是
    # "这个引擎根本没有视觉通道"。两者不能混为一谈。
    #
    # 先发一张最小探测图:能正常返回文本才继续;否则返回 skipped 标记,
    # 上层看到 skipped 就不计入分母,报告里写「不适用」。
    probe = None
    try:
        probe = ep.chat_vision("请只回答 OK", items[0]["image"])
    except EndpointError:
        probe = None
    except Exception:
        probe = None
    if probe is None or not (probe.get("text") or "").strip():
        print("  [skip] 该端点不接受图片输入(未加载 mmproj?),D11 跳过")
        return {"dim": "D11", "suite": "vision", "endpoint": ep.name, "model": ep.model,
                "skipped": True, "skip_reason": "no vision channel (mmproj not loaded)",
                "n": 0, "correct": 0, "accuracy": None, "elapsed_s": 0.0}, []
    records = []
    t0 = time.perf_counter()
    for i, it in enumerate(items, 1):
        try:
            res = ep.chat_vision(it["question"], it["image"])
            v = vismod.judge_vision(it, res.get("text", ""))
            rec = {"id": it["id"], "subcat": it["subcat"], "correct": bool(v["correct"]),
                   "reason": v.get("reason"), "response": res.get("text", ""),
                   "latency_s": res.get("latency_s"), "image": it["image"]}
        except EndpointError as e:
            rec = mark_error({"id": it["id"], "subcat": it["subcat"], "response": ""}, e)
        records.append(rec)
        if verbose:
            print("  %2d/%2d %-18s %-5s %s" % (i, len(items), it["id"],
                                               "PASS" if rec["correct"] else "fail",
                                               str(rec.get("response"))[:40].replace(chr(10), " ")))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_sub = {}
    for x in records:
        s = x.get("subcat") or "?"
        b = by_sub.setdefault(s, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if x["correct"] else 0
    return ({
        "dim": "D11", "suite": "vision", "endpoint": ep.name, "model": ep.model,
        "n": len(records), "correct": correct, "errors": errors,
        "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
        "elapsed_s": dt, "by_subcat": by_sub,
    }, records)

def suite_hint(ep, limit=None, verbose=True):
    """D8h: the hallucination questions WITH an explicit "may not exist" hint.

    Control experiment. The original wording ("Please briefly introduce this
    book: 《X》.") implies the book exists, and the model cooperates with that
    assumption. If scores jump with the hint, the original measures
    susceptibility to a leading prompt, not the absence of a verification habit.
    """
    import reliability_judge as RJ
    path = os.path.join(ROOT, 'datasets', 'full', 'D8_hint.jsonl')
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print()
    print("[D8h 幻觉(带提示)] %d 题, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))
    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        try:
            res = ep.chat([{"role": "user", "content": r["user"]}])
            v = RJ.judge_hallucination(r, res.get("text", ""))
            rec = {"id": r["id"], "subcat": r.get("subcat"), "response": res.get("text", ""),
                   "latency_s": res.get("latency_s"), "correct": bool(v.get("correct")),
                   "reason": v.get("reason"), "hedged": v.get("hedged"),
                   "fabricated": v.get("fabricated")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"), "response": ""}, e)
        records.append(rec)
        if verbose and (i % 10 == 0 or i == len(rows)):
            print(progress_line(i, len(rows), records))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    hedged = sum(1 for x in records if x.get("hedged"))
    errors = sum(1 for x in records if x.get("error"))
    return ({
        "dim": "D8h", "suite": "hint", "endpoint": ep.name, "model": ep.model,
        "n": len(records), "correct": correct, "errors": errors,
        "n_valid": len([x for x in records if not x.get("error")]), "n_errors": len([x for x in records if x.get("error")]), "accuracy": (sum(1 for x in records if not x.get("error") and x["correct"]) / max(1, len([x for x in records if not x.get("error")]))), "accuracy_with_errors": correct / len(records) if records else 0.0,
        "hedged": hedged, "elapsed_s": dt,
    }, records)

def suite_lcb(ep, limit=None, verbose=True, timeout_s=10):
    """LiveCodeBench v6: competitive programming, stdin/stdout grading.

    Different from suite_code (D4): those are function-completion tasks imported
    in-process. Here the model must write a complete program, which is launched
    as a subprocess per test case. A model that solves the problem but returns a
    bare function scores zero - and that is correct, because the original judge
    also runs it as a program.

    Only public tests are graded (hidden tests are not shipped), so a pass means
    "correct on published samples". The summary records that.
    """
    import lcb_judge as LCB
    path = os.path.join(ROOT, "datasets", "public", "livecodebench.jsonl")
    # datasets/public/ 在 .gitignore 里(体积大、可用 fetch_public.py 重新下载)。
    # 于是刚解包/刚 clone 的人没有这个文件,而 --suite all 一定会走到这里。
    # 旧行为是直接抛 FileNotFoundError 把整轮自检带走 —— 新人双击就失败,
    # 且报错只说"没有这个文件",看不出"该去下载"。
    if not os.path.exists(path):
        print()
        print("[LCB v6 竞赛编程] 跳过: 缺少 %s" % os.path.relpath(path, ROOT))
        print("        公开基准需先下载: python scripts/fetch_bench.py")
        # 同 suite_public:调用方按二元组解包,不能返回 None。
        return ({"dim": "LCB", "suite": "lcb", "endpoint": ep.name,
                 "model": ep.model, "n": 0, "correct": 0, "accuracy": None,
                 "elapsed_s": 0.0, "by_subcat": {},
                 "skipped": True, "skip_reason": "datasets/public/livecodebench.jsonl 不存在"},
                [])
    rows = load_jsonl(path)
    if limit:
        rows = rows[:limit]
    print()
    print("[LCB v6 竞赛编程] %d 题, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))

    ck = Checkpoint("lcb", ep.name, [r.get("id") for r in rows])
    done = ck.load() if RESUME else {}
    if done:
        print("  [resume] 从检查点恢复:%d/%d 题已完成" % (len(done), len(rows)))

    records = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        prev = done.get(r.get("id"))
        if prev is not None:
            records.append(prev)
            if verbose and (i % 10 == 0 or i == len(rows)):
                print(progress_line(i, len(rows), records))
            continue
        prompt = (r.get("question") or "").strip() + chr(10) * 2 + (
            "Write a complete Python program that reads from standard input and "
            "writes the answer to standard output. Output only the code in one "
            "```python block. Do not explain.")
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            v = LCB.judge_lcb(r, res.get("text", ""), timeout_s=timeout_s)
            rec = {"id": r["id"], "subcat": r.get("subcat"), "response": res.get("text", ""),
                   "latency_s": res.get("latency_s"), "correct": bool(v.get("correct")),
                   "passed": v.get("passed"), "total": v.get("total"),
                   "reason": v.get("reason"), "difficulty": r.get("difficulty"),
                   "platform": r.get("platform")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("subcat"), "response": ""}, e)
        records.append(rec)
        ck.add(rec)  # Checkpoint 的接口是 add();写成 append() 会在 public/lcb 维度直接 AttributeError
        if verbose and (i % 10 == 0 or i == len(rows)):
            print(progress_line(i, len(rows), records))
    dt = time.perf_counter() - t0
    correct = sum(1 for x in records if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_diff = {}
    for x in records:
        k = x.get("difficulty") or "?"
        b = by_diff.setdefault(k, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if x["correct"] else 0
    return ({
        "dim": "D4", "suite": "lcb", "endpoint": ep.name, "model": ep.model,
        "n": len(records), "correct": correct, "errors": errors,
        "accuracy": correct / len(records) if records else 0.0,
        "elapsed_s": dt, "by_difficulty": by_diff,
        "scoring": "public tests only (hidden tests not shipped)",
        "benchmark": "LiveCodeBench v6 (test6.jsonl)",
    }, records)

def suite_public(ep, limit=None, verbose=True):
    """Run the curated public benchmarks (datasets/subsets/public_balanced.jsonl).

    2037 questions from 9 authoritative sets, balanced across dimensions so no
    single capability dominates. Each row carries `_bench` for provenance and
    routes through public_judge.

    Two scoring caveats recorded in the summary, not hidden:
      * simpleqa/truthfulqa use exact match (no LLM judge), so the number is a
        FLOOR - a correct paraphrase scores 0.
      * IFEval questions needing a language detector are skipped, not guessed.
    """
    import public_judge as PJ
    path = os.path.join(ROOT, "datasets", "subsets", "public_balanced.jsonl")
    # 这份合集是由 datasets/public/ 下的原始基准切出来的。原始数据在 .gitignore
    # (体积大、可重下),所以刚 clone 的人可能连这个 subsets 文件都没有 ——
    # --suite all 会走到这里。缺了就明确说明该跑哪个脚本,而不是抛 FileNotFoundError
    # 让整轮自检崩掉(实测新人双击自检就是这样失败的)。
    if not os.path.exists(path):
        print()
        print("[PUBLIC 公开基准] 跳过: 缺少 %s" % os.path.relpath(path, ROOT))
        print("        先下载原始基准再切片: python scripts/fetch_bench.py")
        # 调用方是 s, recs = suite_public(...),必须返回二元组。
        # 早先在这里 return None,结果自检跑到 public 就 TypeError
        # "cannot unpack non-iterable NoneType object" —— 比原来的 FileNotFound
        # 还难懂。跳过要返回一个带 skipped 标记的 summary,而不是空值。
        return ({"dim": "PUBLIC", "suite": "public", "endpoint": ep.name,
                 "model": ep.model, "n": 0, "correct": 0, "accuracy": None,
                 "elapsed_s": 0.0, "by_subcat": {},
                 "skipped": True, "skip_reason": "datasets/subsets/public_balanced.jsonl 不存在"},
                [])
    rows = load_jsonl(path)
    if limit and limit < len(rows):
        # Stratify by benchmark. Taking rows[:limit] returned the first N of a
        # single benchmark, because the file is written in name order - a --limit
        # 60 run measured only BFCL and reported it as the whole public block.
        by_b = {}
        for r in rows:
            by_b.setdefault(r.get("_bench") or "?", []).append(r)
        names = sorted(by_b)
        picked, i = [], 0
        while len(picked) < limit and any(by_b[n] for n in names):
            n = names[i % len(names)]
            if by_b[n]:
                picked.append(by_b[n].pop(0))
            i += 1
        rows = picked
    print()
    print("[PUBLIC 公开基准] %d 题, endpoint=%s model=%s" % (len(rows), ep.name, ep.model))

    # Resume: a 2037-question run must survive a sleep or a stalled API.
    # Only suite_subset used Checkpoint before; the newer suites silently lost all
    # progress on interruption, and the web control plane could not see them.
    ck = Checkpoint("public", ep.name, [r.get("id") for r in rows])
    done = ck.load() if RESUME else {}
    if done:
        print("  [resume] 从检查点恢复:%d/%d 题已完成" % (len(done), len(rows)))

    records = []
    t0 = time.perf_counter()
    skipped = 0
    for i, r in enumerate(rows, 1):
        prev = done.get(r.get("id"))
        if prev is not None:
            records.append(prev)
            if prev.get("skipped"):
                skipped += 1
            if verbose and (i % 25 == 0 or i == len(rows)):
                print(progress_line(i, len(rows), records))
            continue
        prompt = _public_prompt(r)
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            v = PJ.judge_public(r, res.get("text", ""))
            if v.get("skipped"):
                skipped += 1
            rec = {"id": r["id"], "subcat": r.get("_bench"), "dim": r.get("dim"),
                   "response": res.get("text", ""), "latency_s": res.get("latency_s"),
                   "correct": bool(v.get("correct")), "skipped": bool(v.get("skipped")),
                   "scoring": v.get("scoring"), "reason": v.get("reason")}
        except EndpointError as e:
            rec = mark_error({"id": r["id"], "subcat": r.get("_bench"),
                              "dim": r.get("dim"), "response": ""}, e)
        records.append(rec)
        ck.add(rec)  # Checkpoint 的接口是 add();写成 append() 会在 public/lcb 维度直接 AttributeError
        if verbose and (i % 25 == 0 or i == len(rows)):
            print(progress_line(i, len(rows), records))
    dt = time.perf_counter() - t0
    scored = [x for x in records if not x.get("skipped")]
    correct = sum(1 for x in scored if x["correct"])
    errors = sum(1 for x in records if x.get("error"))
    by_bench, by_dim = {}, {}
    for x in scored:
        for bucket, key in ((by_bench, x.get("subcat")), (by_dim, x.get("dim"))):
            b = bucket.setdefault(key or "?", {"n": 0, "correct": 0})
            b["n"] += 1
            b["correct"] += 1 if x["correct"] else 0
    return ({
        "dim": "PUBLIC", "suite": "public", "endpoint": ep.name, "model": ep.model,
        "n": len(scored), "correct": correct, "errors": errors, "skipped": skipped,
        "accuracy": correct / len(scored) if scored else 0.0,
        "elapsed_s": dt, "by_bench": by_bench, "by_dim": by_dim,
        "caveats": ["simpleqa/truthfulqa: exact match only -> floor, not ceiling",
                    "ifeval language:response_language skipped (no detector)"],
    }, records)


def _public_prompt(row):
    """Build the prompt for one public-benchmark row."""
    bench = row.get("_bench") or ""
    q = (row.get("question") or "").strip()
    opts = row.get("options") or {}
    if opts:
        opt_txt = chr(10).join("%s. %s" % (k, v) for k, v in sorted(opts.items()))
        return (q + chr(10) * 2 + opt_txt + chr(10) * 2 +
                "Reply with the single best option letter, final line: answer: <letter>")
    if row.get("public_tests"):
        return (q + chr(10) * 2 +
                "Write a complete Python program reading stdin and writing stdout. "
                "Output only the code in one " + chr(96) * 3 + "python block, no explanation.")
    if row.get("test") and row.get("entry_point"):
        return (q + chr(10) * 2 +
                "Complete the function. Output only the code in one " + chr(96) * 3 +
                "python block.")
    if bench in ("gsm8k", "math500"):
        return q + chr(10) * 2 + "End with a line: answer: <value>"
    if bench == "bfcl":
        tools = row.get("tools")
        tdesc = json.dumps(tools, ensure_ascii=False)[:1200] if tools else ""
        return (q + chr(10) * 2 + (("Available tools: " + tdesc + chr(10) * 2) if tdesc else "") +
                "Reply with the tool call in the form name(arg=value, ...).")
    if bench == "ifeval":
        return q
    return q + chr(10) * 2 + "Answer directly and concisely."

def suite_speed(ep, repeats=3, verbose=True):
    cfg = os.path.join(ROOT, "datasets", "_samples", "D1_speed.json")
    return speedmod.run_speed_suite(ep, cfg, repeats=repeats, verbose=verbose)

# ---------- output ----------
# 当前进程正在评测的预设 id。由 main() 从 --preset-id 设定,供 save() 统一注入。
#
# 为什么不逐个 save 调用点传:套件有十几个,漏掉一个就会出现"某套件的结果
# 不知道自己属于哪套预设",而报告层再也没法把它归位。集中一处注入,
# 新增套件自动继承。
_PRESET_ID = {"id": ""}


def save(name, payload):
    """Write a result file, stamped with the judge version that produced it.

    The stamp is what lets build_report tell a stale score from a fresh one.
    Without it, a result scored by an older judge is indistinguishable from a
    current one, and the leaderboard happily mixes them.
    """
    # 预设身份注入:一份不知道自己是谁测出来的分数不可追溯。
    if _PRESET_ID["id"] and isinstance(payload, dict):
        payload.setdefault("preset", _PRESET_ID["id"])
    try:
        import judge_version as JV
        if isinstance(payload, dict) and "judge_version" not in payload:
            # Stamp the version of the suite that produced this file, not the
            # global one: "public" and "lcb" have their own judges, and editing
            # one must not invalidate D2..D10 results that cannot reach it.
            suite = None
            if isinstance(payload.get("summary"), dict):
                suite = payload["summary"].get("suite")
            elif payload.get("suite"):
                suite = payload.get("suite")
            payload["judge_version"] = JV.judge_version(suite)
            if suite and suite in JV.SUITE_FILES:
                payload["judge_version_core"] = JV.judge_version()
    except Exception:
        pass
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("  -> %s" % os.path.relpath(path, ROOT))
    return path

def print_summary(m):
    print("\n" + "=" * 58)
    print("  结果汇总  endpoint=%s  model=%s" % (m.get("endpoint"), m.get("model")))
    print("=" * 58)
    lvl = m.get("level")
    if lvl:
        tok = ""
        if lvl.get("prompt_tokens"):
            tok = "  tokens in=%d out=%d" % (lvl["prompt_tokens"], lvl.get("completion_tokens") or 0)
        print("  [%s] %d 题  通过 %d  准确率 %.1f%%  耗时 %.1fs%s"
              % (lvl["level"].upper(), lvl["n"], lvl["correct"], (lvl["accuracy"] or 0.0) * 100,
                 lvl["elapsed_s"], tok))
        for d, b in sorted(lvl["by_dim"].items()):
            print("      %-6s %3d/%3d  %3.0f%%" % (d, b["correct"], b["n"], 100 * b["correct"] / b["n"]))
    math = m.get("math") or (m if "accuracy" in m else None)
    if math:
        print("  [D3 数学] %d 题  正确 %d  准确率 %.1f%%  耗时 %.1fs"
              % (math["n"], math["correct"], (math["accuracy"] or 0.0) * 100, math["elapsed_s"]))
        for s, b in sorted(math["by_subcat"].items()):
            print("      %-18s %2d/%2d  %3.0f%%" % (s, b["correct"], b["n"], 100 * b["correct"] / b["n"]))
    rw = m.get("realworld")
    if rw:
        print("  [D9 实战] %d 题  通过 %d  准确率 %.1f%%  平均覆盖 %.0f%%  基准=%s"
              % (rw["n"], rw["correct"], (rw["accuracy"] or 0.0) * 100,
                 rw["mean_coverage"] * 100, rw.get("baseline_source") or "?"))
        for s, b in sorted(rw["by_subcat"].items()):
            print("      %-16s %2d/%2d" % (s, b["correct"], b["n"]))
        print("      判定方式: 规则检查项覆盖(零成本,无 LLM 裁判)")
    rl = m.get("reliability")
    if rl:
        print("  [D8 可靠性] %d 题  通过 %d  准确率 %.1f%%  耗时 %.1fs"
              % (rl["n"], rl["correct"], (rl["accuracy"] or 0.0) * 100, rl["elapsed_s"]))
        for s, b in sorted(rl["by_subcat"].items()):
            print("      %-16s %2d/%2d" % (s, b["correct"], b["n"]))
        if rl.get("calibration_verdicts"):
            print("      校准分布: %s" % rl["calibration_verdicts"])
    ag = m.get("agent")
    if ag:
        print("  [D6 Agent] %d 题  通过 %d  准确率 %.1f%%  耗时 %.1fs"
              % (ag["n"], ag["correct"], (ag["accuracy"] or 0.0) * 100, ag["elapsed_s"]))
        for s, b in sorted(ag["by_subcat"].items()):
            print("      %-18s %2d/%2d" % (s, b["correct"], b["n"]))
        if ag.get("by_source"):
            print("      判定来源(原生tool_calls/JSON/文本): %s" % ag["by_source"])
    cd = m.get("code")
    if cd:
        print("  [D4 代码] %d 题  通过 %d  准确率 %.1f%%  耗时 %.1fs"
              % (cd["n"], cd["correct"], (cd["accuracy"] or 0.0) * 100, cd["elapsed_s"]))
        for s, b in sorted(cd["by_subcat"].items()):
            print("      %-10s %2d/%2d" % (s, b["correct"], b["n"]))
        if cd.get("by_stage"):
            print("      失败阶段分布: %s" % cd["by_stage"])
    lc = m.get("longctx")
    if lc:
        errs = lc.get("errors", 0)
        print("  [D7 长上下文] %d 题  通过 %d  准确率 %.1f%%  (跳过 %d 题)"
              % (lc["n"], lc["correct"], (lc["accuracy"] or 0.0) * 100, lc["skipped"]))
        if errs:
            print("      *** 端点错误 %d 题(服务中断/超时),这些不是模型答错 ***" % errs)
            print("      有效题准确率: %.1f%% (%d/%d)"
                  % (lc.get("accuracy_valid", 0) * 100, lc["correct"], lc.get("valid_n", 0)))
        for s, b in sorted(lc["by_subcat"].items()):
            tag = "  (%d 题端点错误)" % b.get("errors", 0) if b.get("errors") else ""
            print("      %-16s %2d/%2d%s" % (s, b["correct"], b["n"], tag))
        if lc.get("by_size"):
            print("      —— 降智曲线(needle-single,按上下文长度) ——")
            for size in sorted(lc["by_size"], key=lambda x: (x is None, x)):
                b = lc["by_size"][size]
                if b.get("errors") == b["n"]:
                    verdict = "全部端点错误 - 该档数据无效"
                else:
                    verdict = str(b["depths"])
                print("      %8s tokens  %2d/%2d   %s" % (size, b["correct"], b["n"], verdict))
    pub = m.get("public")
    if pub:
        print("  [PUBLIC 公开基准] %d 题  通过 %d  准确率 %.1f%%  耗时 %.0fs  跳过 %d"
              % (pub["n"], pub["correct"], (pub["accuracy"] or 0.0) * 100,
                 pub["elapsed_s"], pub.get("skipped", 0)))
        print("      按基准:")
        for b_, v_ in sorted(pub.get("by_bench", {}).items()):
            print("        %-14s %3d/%3d  %3.0f%%" % (b_, v_["correct"], v_["n"],
                                                      100 * v_["correct"] / v_["n"]))
        for c_ in pub.get("caveats", []):
            print("      注意: %s" % c_)
    lcb = m.get("lcb")
    if lcb:
        print("  [LCB v6 竞赛编程] %d 题  通过 %d  准确率 %.1f%%  耗时 %.0fs"
              % (lcb["n"], lcb["correct"], (lcb["accuracy"] or 0.0) * 100, lcb["elapsed_s"]))
        for d_, b_ in sorted(lcb.get("by_difficulty", {}).items()):
            print("      %-8s %2d/%2d  %3.0f%%" % (d_, b_["correct"], b_["n"],
                                                    100 * b_["correct"] / b_["n"]))
        print("      判分: %s" % lcb.get("scoring", ""))
    vis = m.get("vision")
    if vis:
        # 跳过的维度没有 accuracy(None),不能直接乘 100 ——
        # 实测这一行在"端点无视觉通道"时抛 TypeError 把整个进程带崩,
        # 于是同一批里排在它后面的维度全没跑。
        if vis.get("skipped") or vis.get("accuracy") is None:
            print("  [D11 视觉] 跳过: %s" % (vis.get("skip_reason") or "该端点无视觉通道"))
        else:
            print("  [D11 视觉] %d 题  通过 %d  准确率 %.1f%%  耗时 %.1fs"
                  % (vis["n"], vis["correct"], (vis["accuracy"] or 0.0) * 100, vis["elapsed_s"]))
        for s_, b_ in sorted(vis.get("by_subcat", {}).items()):
            print("      %-10s %2d/%2d  %3.0f%%" % (s_, b_["correct"], b_["n"],
                                                    100 * b_["correct"] / b_["n"]))
    cmp_ = m.get("compression")
    if cmp_:
        p = cmp_.get("mcnemar_p")
        print("  [D12 压缩阵智] %d 个场景  压缩前 %.0f%%  压缩后 %.0f%%  保留率 %.0f%%"
              % (cmp_["n"], cmp_["baseline_acc"] * 100, cmp_["compacted_acc"] * 100,
                 cmp_["retention"] * 100))
        print("      配对: 丢 %d 题 / 捡 %d 题  McNemar p=%s  压缩比 %.1f%%"
              % (cmp_.get("lost", 0), cmp_.get("gained", 0),
                 ("%.4f" % p) if isinstance(p, float) else "n/a",
                 cmp_.get("compression_ratio", 0) * 100))
    dyn = m.get("dynamic")
    if dyn:
        print("  [DYN 动态多轮] %d 题  解决 %d  解决率 %.1f%%  平均轮数 %.2f  超时率 %.0f%%  耗时 %.1fs"
              % (dyn["n"], dyn["solved"], dyn["solve_rate"] * 100,
                 dyn["mean_rounds"], dyn["timeout_rate"] * 100, dyn["elapsed_s"]))
        med = dyn.get("median_time_to_green_s")
        if med is not None:
            print("      中位首次通过用时 %.1fs（超时 %ds 标注，不按 0 分计）"
                  % (med, dyn["timeout_s"]))
        else:
            print("      没有一题在 %d 轮内解决（上限 %ds/题）"
                  % (dyn["max_rounds"], dyn["timeout_s"]))
    for key, label in (("mcq", "D2 选择题"), ("instruction", "D5 指令遵循"), ("chess", "D10 棋类")):
        sec = m.get(key)
        if not sec:
            continue
        print("  [%s] %d 题  通过 %d  准确率 %.1f%%  耗时 %.1fs"
              % (label, sec["n"], sec["correct"], (sec["accuracy"] or 0.0) * 100, sec["elapsed_s"]))
        if key in ("mcq", "chess"):
            for s, b in sorted(sec["by_subcat"].items()):
                print("      %-18s %2d/%2d  %3.0f%%" % (s, b["correct"], b["n"], 100 * b["correct"] / b["n"]))
        else:
            for s, b in sorted(sec.get("by_checker", {}).items()):
                print("      %-18s %2d/%2d  %3.0f%%" % (s, b["passed"], b["n"], 100 * b["passed"] / b["n"]))
    for r in m.get("speed", {}).get("results", []):
        if r.get("runs"):
            print("  [D1 速度] %-8s ttft p50=%.2fs p95=%.2fs | out p50=%.1f tok/s | e2e500 p50=%.2fs"
                  % (r["workload"], r["ttft_s"]["p50"], r["ttft_s"]["p95"],
                     r["output_tps"]["p50"], r["e2e_500_s"]["p50"]))
        else:
            print("  [D1 速度] %-8s FAILED: %s" % (r["workload"], r.get("error")))
    print("=" * 58)

def _estimate_selection(args, summary):
    """Cost of what this invocation will actually run, not the whole bank."""
    suite = args.suite
    by_dim = {r["dim"]: r for r in summary.get("rows") or []}
    if suite == "all":
        return {"n": summary["total_n"], "cost": summary["total_cost_cny"]}
    if suite == "canary":
        # canary is a fixed 137-question sample; scale by average cost per question
        n = 137
        avg = summary["total_cost_cny"] / max(1, summary["total_n"])
        return {"n": n, "cost": round(avg * n, 2)}
    dim_map = {"math": "D3", "mcq": "D2", "instruction": "D5", "chess": "D10",
               "longctx": "D7", "code": "D4", "agent": "D6",
               "reliability": "D8", "realworld": "D9", "public": "PUBLIC"}
    d = dim_map.get(suite)
    if d and d in by_dim:
        r = by_dim[d]
        n = args.limit or r["n"]
        scale = n / max(1, r["n"])
        return {"n": n, "cost": round(r["cost_cny"] * scale, 2)}
    # unknown suite: fall back to the average, clearly labelled as an estimate
    avg = summary["total_cost_cny"] / max(1, summary["total_n"])
    n = args.limit or 100
    return {"n": n, "cost": round(avg * n, 2)}


def main():
    ap = argparse.ArgumentParser(description="model-arena evaluation runner")
    ap.add_argument("--endpoint", default="local", help="endpoint profile name in config/endpoints.json")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "endpoints.json"))
    ap.add_argument("--agent-mode", default="bare",
                    choices=["bare", "agent-small", "agent-mid", "agent-real"],
                    help="在题目前注入 agent 工具环境(模拟 DSH 等 harness 的上下文占用)")
    ap.add_argument("--no-resume", action="store_true",
                    help="忽略检查点,从头重跑(默认自动续跑)")
    ap.add_argument("--suite", default="math", choices=["math", "speed", "mcq", "instruction", "chess", "longctx", "code", "agent", "reliability", "realworld", "dynamic", "compression", "vision", "hint", "lcb", "public", "smoke", "canary", "full", "probe", "calib", "coarse", "slow", "agent-probe", "all"])
    ap.add_argument("--max-rounds", type=int, default=3, help="dynamic suite: attempts per task")
    ap.add_argument("--task-timeout", type=int, default=300, help="dynamic suite: per-task wall clock cap (seconds)")
    ap.add_argument("--limit", type=int, default=None, help="max questions (math)")
    ap.add_argument("--repeats", type=int, default=3, help="speed repeats")
    ap.add_argument("--min-free-vram", type=int, default=1500, help="abort if free VRAM below this (MB)")
    ap.add_argument("--deadline-s", type=int, default=300,
                    help="per-question wall-clock cap in seconds; a question that exceeds it is recorded as a timeout, not a wrong answer. 0 disables")
    ap.add_argument("--max-tokens", type=int, default=16384,
                    help="per-request output cap; measured max here is 11126 so this only stops runaway loops. 0 disables")
    ap.add_argument("--skip-vram-check", action="store_true")
    ap.add_argument("--tag", default="", help="suffix for the result file name")
    # 预设身份必须**写进结果文件**,不能只进文件名。
    #
    # 实测踩到的:批量队列跑了两套预设,两套用的模型别名相同(mock-model-80pct),
    # 结果文件的 model 字段完全一样,于是"哪个分数属于哪套预设"无从判断。
    # 文件名里的 tag 也救不了:tag 是批次名,不是预设名,两套预设共用同一个 tag。
    # 一份不知道自己是谁测出来的分数,就是不可追溯的分数。
    ap.add_argument("--preset-id", default="",
                    help="标识这份结果属于哪套预设(写进结果文件)")
    ap.add_argument("--yes", action="store_true",
                    help="skip the cloud cost confirmation (required when non-interactive)")
    ap.add_argument("--max-ctx", type=int, default=8192, help="skip D7 questions needing more context")
    args = ap.parse_args()
    global RESUME, AGENT_MODE
    AGENT_MODE = getattr(args, "agent_mode", "bare") or "bare"
    if getattr(args, "no_resume", False):
        RESUME = False
        # a forced fresh run must not leave a stale checkpoint behind
        import checkpoint as _ckp
        for _k, _n, _ in Checkpoint.list_all():
            try:
                os.remove(os.path.join(_ckp.CKPT_DIR, _k + ".jsonl"))
                print("  [no-resume] 已清除检查点 %s(%d 题)" % (_k, _n))
            except OSError:
                pass

    endpoints, cfg = load_endpoints(args.config)
    if args.endpoint not in endpoints:
        print("ERROR: endpoint '%s' not in %s (available: %s)"
              % (args.endpoint, args.config, ", ".join(endpoints) or "none"))
        sys.exit(2)
    ep = endpoints[args.endpoint]
    # Per-question guards, applied once here so every suite inherits them.
    # 0 means "disable that guard" for callers who really want no limit.
    ep.deadline_s = args.deadline_s or None
    ep.max_tokens = args.max_tokens or None

    print("model-arena P1 runner")
    print("  endpoint : %s (%s)" % (ep.name, ep.base_url))
    print("  model    : %s" % ep.model)
    ok, detail = ep.health()
    print("  health   : %s | %s" % ("OK" if ok else "UNREACHABLE", detail))
    if not ok:
        print("\n  The endpoint is not responding. Start it first, e.g.:")
        print("    llama-server -m <model.gguf> --host 127.0.0.1 --port 8080 -ngl 32")
        sys.exit(3)

    if not args.skip_vram_check:
        vok, vdetail = vram_check(args.min_free_vram)
        print("  vram     : %s%s" % (vdetail, "" if vok else "  <-- BELOW THRESHOLD"))
        if not vok:
            print("  ABORT: free VRAM below %d MB. Close other GPU apps or lower -ngl."
                  % args.min_free_vram)
            sys.exit(4)


    # ---- cost gate: a paid endpoint spends real money, so say how much first ----
    # Decided from the endpoint PROFILE, not from the URL shape. Guessing "https
    # means cloud" misses a paid gateway on an IP:port, and guessing "127.0.0.1
    # means free" misses a tunnel. The profile may state it; otherwise fall back
    # to a loopback check, which is the only inference that is usually right.
    _host = (ep.base_url or "").lower()
    _loopback = ("127.0.0.1" in _host or "localhost" in _host or "::1" in _host)
    _declared = getattr(ep, "cost", None)          # "free" / "paid" if configured
    if _declared:
        _is_paid = (_declared == "paid")
    else:
        _is_paid = not _loopback
    if _is_paid and not getattr(args, "yes", False):
        try:
            import cost as COST
            _s = COST.summary()
            _sel = _estimate_selection(args, _s)
            print()
            print("  ---- 付费端点花费预估 ----")
            print("  端点 %s" % ep.base_url)
            print("  套件 %-10s 约 %d 题，预估 %.2f 元" % (args.suite, _sel["n"], _sel["cost"]))
            print("  提示：D7 长上下文 120 题占九成成本，日常评测可避开 --suite longctx")
            print()
            print("  继续请输入 yes，其它任意键退出：", end="")
            sys.stdout.flush()
            _ans = input().strip().lower()
            if _ans != "yes":
                print("  已取消，未产生任何费用。")
                sys.exit(0)
            print()
        except EOFError:
            print("  非交互环境，付费端点需要 --yes 确认，已退出。")
            sys.exit(5)
        except Exception as _e:
            print("  [cost] 预估失败(%s)，继续执行。" % str(_e)[:50])

    out = {"endpoint": ep.name, "model": ep.model, "started": ts(),
           "base_url": ep.base_url, "suite": args.suite}
    # 预设身份进三处:文件名、总结果、每个套件结果。只写文件名的话,
    # 事后按文件名找得到、按内容却对不上,报告层就没法按预设拆开。
    preset_id = (args.preset_id or "").strip()
    _PRESET_ID["id"] = preset_id
    if preset_id:
        out["preset"] = preset_id
    stamp = ts() + (("-" + args.tag) if args.tag else "")

    if args.suite in ("math", "all"):
        s, recs = suite_math(ep, limit=args.limit)
        out["math"] = s
        save("D3_math_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("speed", "all"):
        s = suite_speed(ep, repeats=args.repeats)
        out["speed"] = s
        save("D1_speed_%s_%s.json" % (ep.name, stamp), s)
    if args.suite in ("mcq", "all"):
        s, recs = suite_mcq(ep, limit=args.limit)
        out["mcq"] = s
        save("D2_mcq_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("instruction", "all"):
        s, recs = suite_instruction(ep, limit=args.limit)
        out["instruction"] = s
        save("D5_instruction_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("chess", "all"):
        s, recs = suite_chess(ep, limit=args.limit)
        out["chess"] = s
        save("D10_chess_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("longctx", "all"):
        s, recs = suite_longctx(ep, limit=args.limit, max_ctx=args.max_ctx)
        out["longctx"] = s
        save("D7_longctx_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("code", "all"):
        s, recs = suite_code(ep, limit=args.limit)
        out["code"] = s
        save("D4_code_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("agent", "all"):
        s, recs = suite_agent(ep, limit=args.limit)
        out["agent"] = s
        save("D6_agent_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("reliability", "all"):
        s, recs = suite_reliability(ep, limit=args.limit)
        out["reliability"] = s
        save("D8_reliability_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("realworld", "all"):
        s, recs = suite_realworld(ep, limit=args.limit)
        out["realworld"] = s
        save("D9_realworld_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("dynamic", "all"):
        s, recs = dynmod.suite_dynamic(ep, limit=args.limit, max_rounds=args.max_rounds,
                                       timeout_s=args.task_timeout)
        out["dynamic"] = s
        save("DYN_multi_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("public", "all"):
        s, recs = suite_public(ep, limit=args.limit)
        out["public"] = s
        save("PUBLIC_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("lcb", "all"):
        s, recs = suite_lcb(ep, limit=args.limit)
        out["lcb"] = s
        save("LCB_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("compression", "all"):
        s, recs = suite_compression(ep, limit=args.limit)
        out["compression"] = s
        save("D12_compression_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    # NOT in "all": this suite is the CONTROL VARIANT of the hallucination
    # experiment (same questions plus a "may not exist" hint). Including it in a
    # normal run would double-count D8 and mix a control group into the ranking.
    if args.suite == "hint":
        s, recs = suite_hint(ep, limit=args.limit)
        out["hint"] = s
        save("D8h_hint_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    # NOT in "all": vision needs the engine launched WITH --mmproj. A plain text
    # engine returns empty strings for every image question, which would score a
    # silent 0 and drag the total down for a reason that has nothing to do with
    # the model. Run it explicitly once mmproj is loaded.
    if args.suite == "vision":
        s, recs = suite_vision(ep, limit=args.limit)
        out["vision"] = s
        save("D11_vision_%s_%s.json" % (ep.name, stamp), {"summary": s, "records": recs})
    if args.suite in ("smoke", "canary", "full", "probe", "calib", "coarse", "slow", "agent-probe"):
        s, recs = suite_subset(ep, args.suite)
        out["level"] = s
        save("LEVEL_%s_%s_%s.json" % (args.suite, ep.name, stamp), {"summary": s, "records": recs})
        # the result file is on disk now, so the checkpoint has served its purpose
        _cp = s.get("_ckpt_path")
        if _cp:
            try:
                os.remove(_cp)
                print("  检查点已清理(结果已落盘)")
            except OSError:
                pass
            for _k in ("_ckpt_path", "_ckpt_key"):
                s.pop(_k, None)

    print_summary(out)
    save("run_%s_%s.json" % (ep.name, stamp), out)

if __name__ == "__main__":
    main()
