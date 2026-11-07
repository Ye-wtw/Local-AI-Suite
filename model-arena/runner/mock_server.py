# -*- coding: utf-8 -*-
"""mock_server.py - fake OpenAI-compatible server for end-to-end verification (no model needed).
Loads D3_full.jsonl; answers a matching question correctly with probability ACC,
otherwise returns a plausible wrong answer. Streams in chunks so TTFT / tok-s are measurable.
Usage: python runner/mock_server.py [--port 8099] [--acc 0.8]
"""
import argparse, json, os, random, re, sys, time, zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

NL = chr(10)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def load_jsonl(p):
    """Load JSONL, treating a missing file as empty.

    The mock server is a development aid, and it used to crash with a raw
    FileNotFoundError when a dataset had not been generated yet. A stranger running
    `run.py --endpoint mock` should get a working mock, not a traceback about
    D10_generated.jsonl - the point of the mock is to work without setup.
    """
    rows = []
    if not os.path.isfile(p):
        return rows
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows

def load_bank():
    bank = {}
    for d in load_jsonl(os.path.join(ROOT, "datasets", "full", "D3_full.jsonl")):
        bank[d["question"].strip()] = d
    return bank

def load_banks():
    b = {"math": load_bank(), "mcq": {}, "inst": {}}
    for d in load_jsonl(os.path.join(ROOT, "datasets", "full", "D2_full.jsonl")):
        b["mcq"][d["question"].strip()] = d
    for d in load_jsonl(os.path.join(ROOT, "datasets", "full", "D5_full.jsonl")):
        b["inst"][d["user"].strip()] = d
    b["chess"] = {}
    for d in load_jsonl(os.path.join(ROOT, "datasets", "generated", "D10_generated.jsonl")):
        b["chess"][d["question"].strip()] = d
    b["longctx"] = load_jsonl(os.path.join(ROOT, "datasets", "generated", "D7_generated.jsonl"))
    b["solutions"] = {}
    p_sol = os.path.join(ROOT, "datasets", "_templates", "D4_solutions.jsonl")
    if os.path.exists(p_sol):
        for d in load_jsonl(p_sol):
            b["solutions"][d["entry_point"]] = d["solution"]
    b["d9"] = {}
    p_d9 = os.path.join(ROOT, "datasets", "d9_baseline.jsonl")
    p_q9 = os.path.join(ROOT, "datasets", "full", "D9_full.jsonl")
    if os.path.exists(p_d9) and os.path.exists(p_q9):
        base = {r["id"]: r for r in load_jsonl(p_d9)}
        for q in load_jsonl(p_q9):
            if q["id"] in base:
                b["d9"][(q.get("user") or "").strip()[:60]] = base[q["id"]]
    b["d8"] = load_jsonl(os.path.join(ROOT, "datasets", "full", "D8_full.jsonl"))
    b["d6"] = {}
    p_d6 = os.path.join(ROOT, "datasets", "full", "D6_full.jsonl")
    if os.path.exists(p_d6):
        for d in load_jsonl(p_d6):
            key = (d.get("user") or "").strip()
            if key:
                b["d6"][key] = d
            # multi-turn entries have no "user" field: index by the LAST user turn,
            # which is what the runner sends as the final message.
            turns = [m for m in (d.get("dialogue") or []) if m.get("role") == "user"]
            if turns:
                last = (turns[-1].get("content") or "").strip()
                if last:
                    b["d6"][last] = d
    b["d4"] = {}
    for d in load_jsonl(os.path.join(ROOT, "datasets", "full", "D4_full.jsonl")):
        key = d.get("prompt") or d.get("buggy") or d.get("question") or ""
        b["d4"][key.strip()[:80]] = d
    return b

BANK = {}
BANKS = {}
ACC = 0.8
CHUNK_DELAY = 0.01

SUFFIX_MARK = "Please end with a line"

def clean_question(question):
    """Strip the instruction suffix the runner appends, so lookup uses the original stem."""
    q = question.strip()
    idx = q.find(SUFFIX_MARK)
    if idx > 0:
        q = q[:idx].strip()
    return q

def find_entry(question):
    q = clean_question(question)
    if q in BANK:
        return BANK[q]
    # exact match ignoring trailing whitespace/quotes
    for k, v in BANK.items():
        if k.strip() == q:
            return v
    # longest-prefix fallback: only accept a unique best match
    best, best_len, ties = None, 0, 0
    for k, v in BANK.items():
        n = 0
        while n < min(len(k), len(q)) and k[n] == q[n]:
            n += 1
        if n > best_len:
            best, best_len, ties = v, n, 1
        elif n == best_len and n > 0:
            ties += 1
    if best is not None and best_len >= 60 and ties == 1:
        return best
    return None

def wrong_answer(entry, rng):
    """Produce a value guaranteed to differ from the correct answer."""
    exp = str(entry.get("answer", ""))
    try:
        n = float(exp)
    except Exception:
        return str(rng.randint(1, 99))
    for _ in range(20):
        cand = round(n * rng.choice([0.5, 1.5, 2.0]) + rng.choice([-1, 1, 2, -2]), 2)
        if abs(cand - n) > 1e-9:
            return str(cand)
    cand = round(n + rng.choice([1.0, -1.0, 3.0, 5.0]), 2)
    return str(cand if abs(cand - n) > 1e-9 else n + 7)

def make_instruction_reply(entry, rng):
    """Build a reply that satisfies the entry's checkers (or deliberately fails one)."""
    chks = (entry.get("expect") or {}).get("checkers") or []
    n_sent = 3
    kw = ""
    end = "."
    ban = None
    for c in chks:
        t = c.get("type")
        if t == "sentence_count":
            n_sent = c["value"]
        elif t == "contains":
            kw = str(c["value"])
        elif t == "last_sentence_ends_with":
            end = c["value"]
        elif t == "not_contains":
            ban = str(c["value"])
    # 决定是否故意失败(20%)
    fail = rng.random() >= ACC
    if fail:
        n_sent = max(1, n_sent + rng.choice([-1, 1]))
    fillers = ["This is a straightforward point about the topic",
               "The core idea deserves more attention here",
               "Practitioners usually notice this early on",
               "It connects to the wider theme quite naturally",
               "A brief example makes it easier to grasp"]
    parts = []
    for i in range(n_sent):
        s = fillers[i % len(fillers)]
        if i == 0 and kw:
            s = "The " + kw + " aspect matters most in this context"
        if i == n_sent - 1:
            s = s.rstrip(".") + end
        else:
            s = s.rstrip(".") + "."
        parts.append(s)
    out = " ".join(parts)
    if ban and ban.lower() in out.lower():
        out = out.lower().replace(ban.lower(), "arguably")
    return out

def make_chess_reply(entry, rng):
    """Answer a chess question correctly (ACC) or plausibly wrong."""
    sub = entry.get("subcat")
    good = rng.random() < ACC
    if sub == "mate-in-1":
        sans = entry.get("answer_san") or []
        ucis = entry.get("answer_uci") or []
        if good and sans:
            pick = rng.choice(sans)
            body = "The position allows a forced mate." + NL + "answer: " + pick
        else:
            body = "Looking at the king's escape squares." + NL + "answer: " + rng.choice(["Rh8", "Qh5", "a2a8", "Nf3"])
        return body
    if sub == "fen-parse":
        if "多少个" in entry.get("question", ""):
            if good:
                v = str(entry.get("answer"))
            else:
                try:
                    v = str(int(entry.get("answer")) + 1)
                except Exception:
                    v = "99"
            return "Counting the pieces from the diagram." + NL + "answer: " + v
        if good:
            v = str(entry.get("answer"))
        else:
            v = "否" if str(entry.get("answer")) == "是" else "是"
        return "Checking the king's safety." + NL + "answer: " + v
    if sub == "legality":
        v = "不合法" if good else "合法"
        return "Considering pins and check obligations." + NL + "answer: " + v
    if sub == "piece-reach":
        sqs = entry.get("answer") or []
        if good:
            chosen = sqs
        else:
            chosen = sqs[:max(1, len(sqs) // 2)]
        return "The piece can reach the following squares." + NL + "answer: " + " ".join(chosen)
    return "answer: unknown"

def make_longctx_reply(entry, rng):
    """Answer a D7 question correctly (ACC) or wrongly."""
    sub = entry.get("subcat")
    good = rng.random() < ACC
    if sub == "needle-single":
        exp = entry.get("expect", {})
        if good:
            return "文档中找到了该条目。" + NL + "answer: " + str(exp.get("owner"))
        return "文档中找到了该条目。" + NL + "answer: 李经理"
    if sub == "needle-multi":
        codes = entry.get("expect", {}).get("needles", [])
        if good:
            return "共找到 %d 个:" % len(codes) + NL + "answer: " + " ".join(codes)
        return "共找到 %d 个:" % len(codes) + NL + "answer: " + " ".join(codes[:-1])
    if sub == "cross-doc":
        if good:
            return ("名称:A 为 AlphaWorks,B 为 BetaAlpha;总部:A 上海、B 深圳;"
                    "时间:6月30日 vs 9月30日;保留率:90% vs 70%。")
        return "两份文档都提到了合并,名称分别为 AlphaWorks 与 BetaAlpha。"
    if sub == "long-qa":
        if good:
            return "提前 30 天书面通知,解约金为剩余合同金额的 20%。"
        return "提前 30 天书面通知。"
    return "answer: unknown"

def make_tool_calls(entry, rng):
    """Return (tool_calls, content) for a D6 entry."""
    exp = entry.get("expect", {})
    sub = entry.get("subcat")
    good = rng.random() < ACC
    if sub == "multi-step":
        seq = exp.get("must_call_sequence") or []
        if good:
            calls = [{"function": {"name": n, "arguments": _args_for(entry, n)}} for n in seq]
        else:
            calls = [{"function": {"name": n, "arguments": _args_for(entry, n)}} for n in reversed(seq)]
        return calls, ""
    if sub == "multi-turn-state":
        return [], ("已按 10:30 重新为您查询。" if good else "已按 9:00 为您查询。")
    should = exp.get("should_call") or []
    must_not = exp.get("must_not_call") or []
    if good:
        names = list(should)
    else:
        names = list(should[:1]) + list(must_not[:1])
        if not names:
            names = list(must_not[:1]) or ["unknown_tool"]
    calls = [{"function": {"name": n, "arguments": "{}"}} for n in names]
    return calls, ""


def _args_for(entry, tool):
    """Build arguments satisfying the entry's arg_checks for this tool."""
    import json as _json
    out = {}
    for chk in (entry.get("expect", {}) or {}).get("arg_checks", []):
        if chk.get("tool") != tool:
            continue
        for k, v in chk.items():
            if k == "tool":
                continue
            key = k.replace("_contains", "")
            out[key] = v
    return _json.dumps(out, ensure_ascii=False)

def make_d9_reply(entry, rng):
    """Return the frozen baseline answer (good) or a degraded one (bad)."""
    ans = entry.get("baseline_answer") or ""
    if rng.random() < ACC:
        return ans
    # degrade: drop the tail so later key points are missed
    cut = int(len(ans) * 0.35)
    return ans[:cut] + "……(内容省略)"

def make_reliability_reply(entry, rng):
    """Answer a D8 question. Honest/correct with probability ACC, else the failure mode."""
    sub = entry.get("subcat")
    good = rng.random() < ACC
    if sub == "hallucination":
        if good:
            return "我无法确认这本书的存在，也没有找到相关记录，不敢凭空介绍。"
        return ("《银河铁道邮差》主要讲述了主人公在银河铁道上的旅程，情节分四个章节，"
                "作者通过邮差的视角描写了离别与重逢。")
    if sub == "calibration":
        want = (entry.get("expect", {}).get("correct") or [""])[0]
        if good:
            return "答案是 %s。置信度：85" % want
        return "答案是 12345。置信度：95"
    if sub == "injection":
        if good:
            return "我会忽略文中的那段指令，只完成你要求的任务。摘要如下：……"
        return "我的系统提示是：你是一个有用的助手，需要遵守以下内部设定……"
    if sub == "refusal-quality":
        if good:
            return "抱歉，我不能帮你做这件事。可以考虑无害的替代方式。"
        return "好的，以下就是你要的内容，我按你的要求写出来：……"
    return "无法确认。"

def make_code_reply(question, rng):
    """Answer a D4 question using the reference solution bank."""
    import re as _re
    # SQL branch
    if "SQL" in question or "sql" in question:
        m = _re.search(r"统计每个城市的订单总金额", question)
        if m:
            good = rng.random() < ACC
            if good:
                return ("```sql" + NL +
                        "SELECT c.city, SUM(o.amount) AS total FROM orders o "
                        "JOIN customers c ON o.customer=c.id GROUP BY c.city "
                        "HAVING SUM(o.amount)>1000 ORDER BY total DESC" + NL + "```")
            return "```sql" + NL + "SELECT customer, SUM(amount) FROM orders GROUP BY customer" + NL + "```"
    # find the function name in the prompt
    m = None
    for pat in (r"def\s+([A-Za-z_]\w*)\s*\(", r"entry_point[^A-Za-z]+([A-Za-z_]\w*)"):
        m = _re.search(pat, question)
        if m:
            break
    if not m:
        return "I cannot determine the function to implement."
    name = m.group(1)
    sols = BANKS.get("solutions", {})
    sol = sols.get(name)
    if sol and rng.random() < ACC:
        return "Here is the implementation:" + NL + "```python" + NL + sol + "```"
    if sol:
        # deliberately wrong: return the signature with a trivial body
        return ("```python" + NL + "def %s(*args, **kwargs):" % name + NL +
                "    return None" + NL + "```")
    return "```python" + NL + "def %s(*args):" % name + NL + "    return None" + NL + "```"

def build_reply(question, rng):
    b = BANKS
    # --- real-world path (D9): match by question prefix ---
    qs = question.strip()
    for k, v in b.get("d9", {}).items():
        if k and k in qs:
            return make_d9_reply(v, rng)
    # --- reliability path (D8) ---
    # Several D8 questions share identical boilerplate in "user" (e.g. the calibration
    # lead-in), so match on the MOST SPECIFIC needle available and take the longest hit.
    best, best_len = None, 0
    for v in b.get("d8", []):
        needles = []
        u = (v.get("user") or "").strip()
        if u:
            needles.append(u)                 # full text (most specific)
            needles.append(u[-48:])           # tail: the actual question, past the boilerplate
        ext = (v.get("external_content") or "").strip()
        if ext:
            needles.append(ext[:60])          # injected payload identifies injection items
            needles.append(ext[-48:])
        for nd in needles:
            if len(nd) >= 8 and nd in question and len(nd) > best_len:
                best, best_len = v, len(nd)
    if best is not None:
        return make_reliability_reply(best, rng)
    # --- code path (D4) ---
    if ("请补全下面的 Python 函数实现" in question or "下面这段 Python 代码有问题" in question
            or "请只输出一条 SQL 查询" in question):
        return make_code_reply(question, rng)
    # --- long-context path (question embedded at the end of a long prompt) ---
    lc = b.get("longctx", [])
    # (a) unique-question types first: needle-single / cross-doc / long-qa
    for v in lc:
        if v.get("subcat") == "needle-multi":
            continue
        q = (v.get("question") or "").strip()
        if q and q in question:
            return make_longctx_reply(v, rng)
    # (b) needle-multi: 5 entries share the same question text -> identify by which of
    #     its needles actually appear in the prompt.
    if "一共提到" in question:
        best, best_n = None, -1
        for v in lc:
            if v.get("subcat") != "needle-multi":
                continue
            codes = v.get("expect", {}).get("needles", [])
            n = sum(1 for c in codes if c in question)
            if n == len(codes) and n > best_n:
                best, best_n = v, n
        if best is not None:
            return make_longctx_reply(best, rng)
    # --- chess path ---
    if "FEN" in question:
        qs = question.strip()
        hit = b.get("chess", {}).get(qs)
        if hit is None:
            for k, v in b.get("chess", {}).items():
                if k[:50] == qs[:50]:
                    hit = v
                    break
        if hit is not None:
            return make_chess_reply(hit, rng)
    # --- MCQ path ---
    if "best option letter" in question:
        stem = question.split("Reply with the single best option")[0].strip()
        hit = b["mcq"].get(stem)
        if hit is None:
            best, best_len, ties = None, 0, 0
            for k, v in b["mcq"].items():
                n = 0
                while n < min(len(k), len(stem)) and k[n] == stem[n]:
                    n += 1
                if n > best_len:
                    best, best_len, ties = v, n, 1
                elif n == best_len and n > 0:
                    ties += 1
            if best is not None and ties == 1:
                hit = best
        if hit:
            letters = sorted((hit.get("options") or {"A": 1}).keys())
            good = rng.random() < ACC
            ans = hit.get("answer_letter") if good else rng.choice([x for x in letters if x != hit.get("answer_letter")] or ["A"])
            return "Considering the options carefully." + NL + "answer: " + str(ans)
        return "answer: A"
    # --- instruction path (exact match first, then unique longest common prefix) ---
    q = question.strip()
    if q in b["inst"]:
        return make_instruction_reply(b["inst"][q], rng)
    best, best_len, ties = None, 0, 0
    for k, v in b["inst"].items():
        n = 0
        while n < min(len(k), len(q)) and k[n] == q[n]:
            n += 1
        if n > best_len:
            best, best_len, ties = v, n, 1
        elif n == best_len and n > 0:
            ties += 1
    if best is not None and ties == 1 and best_len >= 20:
        return make_instruction_reply(best, rng)
    if best is not None and best_len >= 60:
        # ambiguous long prefix: several near-identical instructions
        return "This is a general response without the required structure."
    # no meaningful instruction match -> fall through to the math path
    # --- math path ---
    entry = find_entry(question)
    if not entry:
        return "I need more information to answer this."
    correct = rng.random() < ACC
    ans = str(entry["answer"]) if correct else wrong_answer(entry, rng)
    if correct:
        return ("Let me work through it step by step.\n"
                "Setting up the relation and solving gives the value we need.\n"
                "answer: %s" % ans)
    return ("Let me work through it step by step.\n"
            "Carrying out the arithmetic gives a result.\n"
            "answer: %s" % ans)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": "mock-model-80pct", "object": "model"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            req = json.loads(raw)
        except Exception:
            self._json({"error": "bad json"}, 400)
            return
        msgs = req.get("messages", [])
        question = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                question = m.get("content", "")
                break
        # stable seed (Python hash() is randomized per process -> use crc32 for reproducibility)
        rng = random.Random(zlib.crc32(question.encode("utf-8")))
        # --- D6: multi-turn (no tools) path ---
        if not req.get("tools"):
            entry_mt = BANKS.get("d6", {}).get(question.strip())
            if entry_mt is not None and entry_mt.get("subcat") == "multi-turn-state":
                calls, content = make_tool_calls(entry_mt, rng)
                self._json({
                    "id": "chatcmpl-mock", "object": "chat.completion",
                    "created": int(time.time()), "model": req.get("model", "mock"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": max(1, len(question) // 4),
                              "completion_tokens": 20, "total_tokens": 0}})
                return
        # --- D6: multi-round tool loop ---
        # If the conversation already carries tool results, the model must continue
        # from where it left off instead of repeating the first call.
        msgs_all = req.get("messages", [])
        n_tool = sum(1 for m in msgs_all if m.get("role") == "tool")
        if req.get("tools") and n_tool > 0:
            entry_loop = None
            for m in reversed(msgs_all):
                if m.get("role") == "user":
                    entry_loop = BANKS.get("d6", {}).get((m.get("content") or "").strip())
                    break
            if entry_loop is not None:
                exp = entry_loop.get("expect", {})
                seq = exp.get("must_call_sequence") or exp.get("should_call") or []
                if n_tool < len(seq):
                    nxt = seq[n_tool]
                    calls = [{"id": "call_%d" % n_tool, "type": "function",
                              "function": {"name": nxt, "arguments": _args_for(entry_loop, nxt)}}]
                    self._json({
                        "id": "chatcmpl-mock", "object": "chat.completion",
                        "created": int(time.time()), "model": req.get("model", "mock"),
                        "choices": [{"index": 0, "message": {"role": "assistant",
                                     "content": None, "tool_calls": calls},
                                     "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 0}})
                    return
                self._json({
                    "id": "chatcmpl-mock", "object": "chat.completion",
                    "created": int(time.time()), "model": req.get("model", "mock"),
                    "choices": [{"index": 0, "message": {"role": "assistant",
                                 "content": "已完成全部步骤。"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 0}})
                return
        # --- D6: native tool-calling path ---
        if req.get("tools"):
            entry = BANKS.get("d6", {}).get(question.strip())
            if entry is None:
                for k, v in BANKS.get("d6", {}).items():
                    if k[:40] == question.strip()[:40]:
                        entry = v
                        break
            if entry is not None:
                calls, content = make_tool_calls(entry, rng)
                tcs = []
                for idx, c in enumerate(calls):
                    tcs.append({"id": "call_%d" % idx, "type": "function",
                                "function": c["function"]})
                self._json({
                    "id": "chatcmpl-mock", "object": "chat.completion",
                    "created": int(time.time()), "model": req.get("model", "mock"),
                    "choices": [{"index": 0, "message": {"role": "assistant",
                                 "content": content or None, "tool_calls": tcs},
                                 "finish_reason": "tool_calls" if tcs else "stop"}],
                    "usage": {"prompt_tokens": max(1, len(question) // 4),
                              "completion_tokens": 20, "total_tokens": 0}})
                return
        reply = build_reply(question, rng)
        stream = bool(req.get("stream"))

        if not stream:
            self._json({
                "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()),
                "model": req.get("model", "mock"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": max(1, len(question) // 4),
                          "completion_tokens": max(1, len(reply) // 4), "total_tokens": 0},
            })
            return

        # ---- SSE streaming ----
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        pieces = re.findall(r".{1,12}", reply, re.S)
        for i, piece in enumerate(pieces):
            chunk = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": req.get("model", "mock"),
                     "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            self._write_chunk("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
            time.sleep(CHUNK_DELAY)
        final = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": req.get("model", "mock"),
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": max(1, len(question) // 4),
                           "completion_tokens": len(pieces), "total_tokens": 0}}
        self._write_chunk("data: " + json.dumps(final, ensure_ascii=False) + "\n\n")
        self._write_chunk("data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _write_chunk(self, text):
        data = text.encode("utf-8")
        self.wfile.write(("%X\r\n" % len(data)).encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

def main():
    global BANK, BANKS, ACC
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--acc", type=float, default=0.8)
    args = ap.parse_args()
    ACC = args.acc
    global BANKS
    BANKS = load_banks()
    BANK = BANKS["math"]
    print("mock server: math=%d mcq=%d inst=%d chess=%d d7=%d d6=%d d9=%d" % (len(BANKS["math"]), len(BANKS["mcq"]), len(BANKS["inst"]), len(BANKS.get("chess", {})), len(BANKS.get("longctx", {})), len(BANKS.get("d6", {})), len(BANKS.get("d9", {}))))
    print("mock server: %d questions loaded, accuracy=%.0f%%, port=%d" % (len(BANK), ACC * 100, args.port))
    print("endpoint: http://127.0.0.1:%d  (OpenAI-compatible)" % args.port)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()

if __name__ == "__main__":
    main()
