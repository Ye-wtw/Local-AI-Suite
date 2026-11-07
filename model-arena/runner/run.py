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
