import re
# -*- coding: utf-8 -*-
"""agent_judge.py - D6 agent / tool-calling judging.
Prefers NATIVE tool_calls from the endpoint; falls back to scanning the text for
tool names and JSON blocks, so models without a tool template are still scored.
Subcategories:
  tool-decision    : called set must include should_call, exclude must_not_call
  multi-step       : ordered must_call_sequence + argument checks
  multi-turn-state : final answer must reflect the UPDATED state, not the stale one
"""
import json, re

JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def tool_specs(names):
    """Build minimal OpenAI function specs from bare tool names."""
    specs = []
    for n in names or []:
        specs.append({"type": "function", "function": {
            "name": n, "description": "Tool: %s" % n,
            "parameters": {"type": "object", "properties": {}, "required": []}}})
    return specs


# Past-tense cues that mean "I ran it", as opposed to "it exists" or "I could".
# Kept narrow on purpose: a broad vocabulary here would flip honest refusals back
# into false positives, which is the bug this function exists to prevent.
_EXEC_PREFIX_EN = ("i used ", "i called ", "i ran ", "i invoked ", "i executed ",
                   "i have used ", "i already used ", "i searched ",
                   "i queried ", "i sent ", "i read ", "i checked ")
_EXEC_PREFIX_CN = ("已用", "已经用", "已调用", "调用了", "已执行", "我用了",
                   "我已", "已经调用", "已通过", "我用")
_EXEC_SUFFIX_CN = ("搜索", "查询", "查了", "找到了", "调用完成")
# Connectors that let a coordinate list inherit the verb of its first item.
#
# "、" is deliberately absent. A Chinese enumeration comma joins a LIST exactly
# the way it joins coordinated calls: "可用工具：压缩图片（compress_image）、PDF转Word
# （convert_pdf）、发送邮件（send_email）" names three tools without calling any.
# Treating 、 as evidence of execution scored an honest refusal as three
# forbidden calls. Only connectors that carry a completed verb forward qualify:
# "I called A and B" / "我已用A还有B" read as actions, 、 does not.
# Only connectors that join CLAUSES, never punctuation that joins nouns.
# ", " and " & " listed plain enumerations too ("available tools: a, b, c"), so
# they are out; " and " / "还有" at least imply a continued assertion.
_JOINERS = (" and ", "还有", "以及")


def _looks_executed(text, start, end):
    """True when the mention of a tool reads as a completed call."""
    # window before the name is where the verb lives ("我用了 X 查询")
    before = text[max(0, start - 32):start].lower()
    after = text[end:end + 12].lower()
    for p in _EXEC_PREFIX_EN:
        if p in before:
            return True
    for p in _EXEC_PREFIX_CN:
        if p in before:
            return True
    # "X搜索" / "X查了" - verb immediately follows the tool name
    for s in _EXEC_SUFFIX_CN:
        if after.strip().startswith(s):
            return True
    # A coordinate list inherits the verb from its first element:
    #   "I called get_balance and track_package"  -> both were called
    # The verb must still be present in the window; a bare connector is not
    # enough, because the same connectors join plain enumerations.
    for conn in _JOINERS:
        idx = before.rfind(conn)
        if idx >= 0 and len(before) - idx <= 12:
            head = before[:idx]
            for p in _EXEC_PREFIX_EN + _EXEC_PREFIX_CN:
                if p in head:
                    return True
    return False


def called_names(result, text, known):
    """Return (names_in_order, source)."""
    tc = (result or {}).get("tool_calls") or []
    names = []
    for c in tc:
        fn = (c or {}).get("function") or {}
        if fn.get("name"):
            names.append(fn["name"])
    if names:
        return names, "native"
    # fallback 1: JSON block with "name"/"tool"/"function" keys
    for m in JSON_BLOCK.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            if not isinstance(it, dict):
                continue
            for key in ("name", "tool", "function", "tool_name"):
                v = it.get(key)
                if isinstance(v, str) and v in known:
                    names.append(v)
                elif isinstance(v, dict) and v.get("name") in known:
                    names.append(v["name"])
        if names:
            return names, "json"
    # fallback 2: a tool name mentioned as EXECUTED, not merely named.
    #
    # Mentioning a tool is not calling it. A model that answers "I cannot play
    # music, but search_music_sheet could find the sheet" is doing exactly the
    # right thing on an irrelevance question, and the old rule scored it as a
    # forbidden call. That inverted the benchmark: honest explanation and actual
    # misuse became indistinguishable. So a mention only counts when the sentence
    # says the call happened ("I used X", "called X", "已用X查询").
    hits = []
    t = text or ""
    for n in known or []:
        for m in re.finditer(r"\b%s\b" % re.escape(n), t):
            if _looks_executed(t, m.start(), m.end()):
                hits.append((m.start(), n))
    hits.sort()
    return [n for _, n in hits], ("text" if hits else "none")


def _arg_haystack(result, text):
    """Build a searchable string from tool calls, decoding JSON-escaped arguments.
    Models often emit arguments as an escaped JSON string (今日), so a plain
    substring test against the raw payload misses non-ASCII values."""
    parts = []
    for c in (result or {}).get("tool_calls") or []:
        fn = (c or {}).get("function") or {}
        parts.append(str(fn.get("name") or ""))
        args = fn.get("arguments")
        if isinstance(args, str):
            parts.append(args)
            try:
                obj = json.loads(args)
                parts.append(json.dumps(obj, ensure_ascii=False))
                if isinstance(obj, dict):
                    for v in obj.values():
                        parts.append(str(v))
            except Exception:
                pass
        elif isinstance(args, dict):
            parts.append(json.dumps(args, ensure_ascii=False))
            for v in args.values():
                parts.append(str(v))
    parts.append(text or "")
    return chr(10).join(parts)

TIME_CN = re.compile(r"(\d{1,2})\s*[点:]\s*(\d{1,2})?\s*(半)?")
TIME_ISO = re.compile(r"(\d{1,2}):(\d{2})")


def normalize_times(text):
    """'10点半' / '10 点 30' -> '10:30', so time answers compare regardless of wording."""
    out = []
    for m in TIME_ISO.finditer(text or ""):
        out.append("%d:%02d" % (int(m.group(1)), int(m.group(2))))
    for m in TIME_CN.finditer(text or ""):
        h = int(m.group(1))
        if m.group(3):
            out.append("%d:30" % h)
        elif m.group(2):
            out.append("%d:%02d" % (h, int(m.group(2))))
    return out


def _coerce(v):
    """Normalise a value for comparison: 30 / '30' / 30.0 are equal, and lists are
    compared structurally rather than by string form.

    Why: BFCL ground truth is a python-call string, so a list may arrive as
    "[[2, 3], [4, 5]]" while the model sends a real array. Comparing the two as
    raw text fails on spacing alone, which mis-scored correct calls.
    """
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, tuple)):
        return [_coerce(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce(x) for k, x in v.items()}
    t = str(v).strip().strip('"').strip("'")
    if t[:1] in "[(" and t[-1:] in "])":
        try:
            import ast as _ast
            return _coerce(_ast.literal_eval(t))
        except Exception:
            pass
    try:
        f = float(t)
        return int(f) if f == int(f) else f
    except Exception:
        pass
    # A lambda argument is compared by its body: BFCL ground truth may name the
    # parameter x while the model names it t ("lambda t: 3*t**2" == "lambda x: 3*x**2").
    # Renaming a lambda's parameter does not change what it computes.
    if t.lower().startswith("lambda"):
        try:
            head, _, body = t.partition(":")
            params = [x.strip() for x in head[6:].split(",") if x.strip()]
            canon = body
            for i, pname in enumerate(params):
                pat = re.compile(r"\b%s\b" % re.escape(pname))
                canon = pat.sub("p%d" % i, canon)
            canon = re.sub(r"\s+", "", canon).replace('"', "'")
            return "lambda:" + canon.lower()
        except Exception:
            pass
    return re.sub(r"\s+", "", t).lower()


def judge_bfcl_args(entry, result):
    """BFCL-style argument check: right function AND right argument values.
    Name-only matching lets a model score by guessing which function to call."""
    exp = entry.get("expect") or {}
    want_name = exp.get("bfcl_name")
    want_args = exp.get("bfcl_args") or {}
    calls = (result or {}).get("tool_calls") or []
    if not calls:
        return {"correct": False, "reason": "no tool calls (expected %s)" % want_name,
                "called": []}
    called = [((c.get("function") or {}).get("name")) for c in calls]
    hit = None
    for c in calls:
        f = c.get("function") or {}
        if f.get("name") == want_name:
            hit = f
            break
    if hit is None:
        return {"correct": False, "called": called,
                "reason": "wrong function: called %s, expected %s" % (called, want_name)}
    raw = hit.get("arguments")
    if isinstance(raw, str):
        try:
            got = json.loads(raw)
        except Exception:
            got = {}
    elif isinstance(raw, dict):
        got = raw
    else:
        got = {}
    bad = []
    for k, v in want_args.items():
        if k not in got:
            bad.append("%s missing" % k)
        elif _coerce(got[k]) != _coerce(v):
            bad.append("%s=%r want %r" % (k, got.get(k), v))
    return {"correct": not bad, "called": called, "name_ok": True,
            "got_args": got, "want_args": want_args,
            "reason": ("args OK" if not bad else "; ".join(bad))}


# A model may honestly report that no available tool can do the job. Recognising
# that is the CORRECT behaviour for an impossible request - but only when it also
# does not claim success, which would be a fabrication.
HONEST_FAIL = ("做不到", "无法", "不能", "不支持", "没有", "无法完成", "不能完成",
               "cannot", "unable", "not supported", "no tool", "没有合适",
               "需要先", "建议改用", "不具备", "无法直接", "不支持修改",
               # 工具名 + 否定,常见于"仅支持 A,不提供 B"这类表述
               "仅支持", "并不直接", "不直接提供", "不提供", "需要转", "先转",
               "不包含", "没有提供", "未提供", "缺乏")
FALSE_SUCCESS = ("已完成", "已成功", "已经完成", "已修改", "已换成", "已替换",
                 "done", "completed successfully", "successfully", "已为您完成")


# Negations that invert a completion claim. "不能当作已经完成" is the opposite of
# "已经完成", but substring matching cannot tell them apart - it scored the most
# careful answer in the D6 set as a fabricated success.
_NEGATORS = ("不能当作", "不能算作", "不能视为", "不能认为", "不算", "并非",
             "不是", "未", "没有", "无法", "不能", "不该", "不应", "别")


def _claims_success(text):
    """True when a completion word appears WITHOUT a nearby negation."""
    t = text or ""
    for k in FALSE_SUCCESS:
        start = 0
        while True:
            i = t.find(k, start)
            if i < 0:
                break
            # look back a short window: "不能当作（已经完成）" negates it
            window = t[max(0, i - 10):i]
            if not any(n in window for n in _NEGATORS):
                return True
            start = i + len(k)
    return False


def _honest_failure(text):
    t = (text or "")
    return (any(k in t for k in HONEST_FAIL), _claims_success(t))


def judge_tool_decision(entry, result, text):
    exp = entry.get("expect", {})
    known = [t for t in (entry.get("tools") or []) if isinstance(t, str)]

    # A question whose mock tool returns incomplete or wrong data. The honest
    # answer points at the SPECIFIC gap and stops; a fabricated one claims the
    # task finished. Two things are therefore checked, and the first one is
    # narrower than the old global HONEST_FAIL vocabulary on purpose: that list
    # fires on any "无法" anywhere, which also matches a model explaining what it
    # COULD do. Per-question markers say what this task's gap actually is.
    if exp.get("expect_honest_failure"):
        t = text or ""
        markers = exp.get("honest_failure_markers") or []
        named_gap = [m for m in markers if m in t]
        said_no, claimed_yes = _honest_failure(t)
        called, src = called_names(result, t, known)
        # Passing needs a concrete gap named AND no claim of completion.
        ok = bool(named_gap) and not claimed_yes
        return {"correct": ok, "source": src, "called": called,
                "named_gap": named_gap[:3],
                "why": exp.get("honest_failure_why"),
                "reason": "指明缺失=%s 谎称成功=%s 命中词=%s"
                          % (bool(named_gap), claimed_yes, named_gap[:3])}

    called, src = called_names(result, text, known)
    cset = set(called)
    should = set(exp.get("should_call") or [])
    must_not = set(exp.get("must_not_call") or [])
    missing = sorted(should - cset)
    forbidden = sorted(must_not & cset)
    extra = sorted(cset - should - must_not - set(known))
    ok = not missing and not forbidden and not extra
    return {"correct": ok, "source": src, "called": called,
            "reason": "missing=%s forbidden=%s extra=%s" % (missing, forbidden, extra)}


def judge_multi_step(entry, result, text):
    exp = entry.get("expect", {})
    known = []
    for t in (entry.get("tools") or []):
        if isinstance(t, dict):
            known.append((t.get("function") or {}).get("name"))
        elif isinstance(t, str):
            known.append(t)
    known = [k for k in known if k]
    called, src = called_names(result, text, known)
    seq = exp.get("must_call_sequence") or []
    must_not = set(exp.get("must_not_call") or [])
    # order check: the required sequence must appear as a subsequence
    it = iter(called)
    ordered_ok = all(any(c == want for c in it) for want in seq)
    forbidden = sorted(must_not & set(called))
    # argument checks against raw tool_calls
    arg_ok, arg_note = True, []
    checks = exp.get("arg_checks") or []
    if checks:
        raw = _arg_haystack(result, text)
        for chk in checks:
            tool = chk.get("tool")
            for key, val in chk.items():
                if key == "tool":
                    continue
                if val not in raw:
                    arg_ok = False
                    arg_note.append("%s.%s missing %r" % (tool, key, val))
    ok = ordered_ok and not forbidden and arg_ok
    return {"correct": ok, "source": src, "called": called,
            "reason": "order=%s forbidden=%s args=%s" % (ordered_ok, forbidden, arg_note)}


def judge_multi_turn(entry, result, text):
    exp = entry.get("expect", {})
    txt = text or ""
    checks = exp.get("state_tracking") or []
    # heuristic: must mention the updated value and not contradict with the stale one
    updated_ok = True
    notes = []
    hay = txt + " " + " ".join(normalize_times(txt))
    for c in checks:
        nums = re.findall(r"\d+(?:[:：]\d+)?", c)
        if len(nums) >= 2:
            new = nums[-1]
            old = nums[0]
            if new not in hay:
                updated_ok = False
                notes.append("missing updated %s" % new)
            elif old in txt and new not in txt:
                updated_ok = False
                notes.append("stale %s" % old)
    if not checks:
        updated_ok = len(txt.strip()) > 0
    return {"correct": updated_ok, "reason": "; ".join(notes) or "state ok"}


def judge_agent(entry, result, text):
    exp0 = entry.get("expect") or {}
    if exp0.get("bfcl_check_args"):
        v = judge_bfcl_args(entry, result)
        v["source"] = "bfcl"
        return v
    sub = entry.get("subcat")
    if sub == "multi-step":
        return judge_multi_step(entry, result, text)
    if sub == "multi-turn-state":
        return judge_multi_turn(entry, result, text)
    return judge_tool_decision(entry, result, text)
