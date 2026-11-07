# -*- coding: utf-8 -*-
"""chess_judge.py - rule-based judging for the D10 board-game dimension.
All verification is done by chess_core (pure Python); no LLM needed.
"""
import re
from chess_core import parse_fen, mates_in_1, parse_chess_answer, answers_match, legal_moves, move_to_uci

SQ_RE = re.compile(r"\b([a-h][1-8])\b")
YES = ("是", "yes", "true", "被将军", "正在被将军")
NO = ("否", "no", "false", "没有", "未被将军")
LEGAL_ONLY = ("合法", "legal", "是合法的")
ILLEGAL = ("不合法", "非法", "illegal", "违规")


def _last_line(text):
    if not text:
        return ""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""


def judge_mate1(entry, response):
    """Find-the-mate: answer must match one of the mating moves."""
    st = parse_fen(entry["fen"])
    mates = mates_in_1(st)
    parsed = parse_chess_answer(response)
    ok, why = answers_match(st, mates, parsed)
    return {"correct": bool(ok), "reason": why,
            "parsed": parsed, "expected_san": entry.get("answer_san"),
            "engine_mates": [move_to_uci(m) for m in mates]}


def judge_number(entry, response):
    want = str(entry.get("answer", "")).strip()
    nums = re.findall(r"-?\d+", response or "")
    got = nums[-1] if nums else None
    return {"correct": got == want, "extracted": got, "expected": want, "reason": "last number"}


def _answer_fragment(response):
    """Return the text after the trailing 'answer:' marker, or the last line."""
    tail = _last_line(response)
    m = re.search(r"answer\s*[:：]\s*(.+)", tail, re.I)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"answer\s*[:：]\s*(.+)", response or "", re.I | re.S)
    if m2:
        return m2.group(1).strip().splitlines()[0]
    return tail


def judge_yesno(entry, response):
    want = str(entry.get("answer", "")).strip()
    frag = _answer_fragment(response)
    got = None
    # explicit answer line wins
    if any(k in frag for k in ("否", "no", "false", "未", "没有", "不")):
        got = "否"
    elif any(k in frag for k in ("是", "yes", "true")):
        got = "是"
    if got is None:
        low = (response or "").lower()
        if "没有被将军" in low or "不是被将军" in low or "未被将军" in low:
            got = "否"
        elif "被将军" in low or "in check" in low:
            got = "是"
    return {"correct": got == want, "extracted": got, "expected": want, "reason": "yes/no match"}


def judge_legality(entry, response):
    want = str(entry.get("answer", "")).strip()   # 不合法
    frag = _answer_fragment(response)
    got = None
    if any(k in frag for k in ILLEGAL):
        got = "不合法"
    elif any(k in frag for k in LEGAL_ONLY):
        got = "合法"
    if got is None:
        tail = _last_line(response) + " " + (response or "")[-120:]
        stripped = tail
        for k in ILLEGAL:
            stripped = stripped.replace(k, " ")
        if any(k in stripped for k in LEGAL_ONLY):
            got = "合法"
        elif any(k in tail for k in ILLEGAL):
            got = "不合法"
    return {"correct": got == want, "extracted": got, "expected": want, "reason": "legality match"}


def judge_reach(entry, response):
    """Set comparison: every required square listed, no extra squares claimed.

    Only the ANSWER portion counts. A model that lists the legal squares and then
    explains 'g1 is occupied so it cannot go there' must not be charged with g1.
    """
    want = set(entry.get("answer") or [])
    txt = response or ""
    # 1) an explicit "answer:" line wins
    m = re.search(r"answer\s*[:：]\s*(.+)", txt, re.I)
    seg = None
    if m:
        seg = m.group(1)
    else:
        # 2) otherwise pick the line/sentence carrying the MOST squares: that is the list
        best, best_n = "", 0
        _sep = "[" + chr(10) + chr(0x3002) + chr(0xFF1B) + ";" + "]"
        for piece in re.split(_sep, txt):
            n = len(SQ_RE.findall(piece))
            if n > best_n:
                best, best_n = piece, n
        seg = best
    got = set(SQ_RE.findall(seg or ""))
    if not got:
        got = set(SQ_RE.findall(txt.replace(entry.get("fen", ""), " ")))
    # the question names where the piece STANDS ("位于 a1 的车"); that square is not a
    # destination, so drop it if the answer repeats it
    origin = set(SQ_RE.findall(entry.get("question") or ""))
    got = {x for x in got if x not in origin}
    missing = sorted(want - got)
    extra = sorted(got - want)
    ok = not missing and not extra
    return {"correct": ok, "reason": "missing=%s extra=%s" % (missing, extra),
            "missing": missing, "extra": extra, "expected": sorted(want),
            "counted_from": (seg or "")[:80]}


def judge_chess(entry, response):
    sub = entry.get("subcat")
    if sub == "mate-in-1":
        return judge_mate1(entry, response)
    if sub == "fen-parse":
        q = entry.get("question", "")
        if "多少个" in q:
            return judge_number(entry, response)
        return judge_yesno(entry, response)
    if sub == "legality":
        return judge_legality(entry, response)
    if sub == "piece-reach":
        return judge_reach(entry, response)
    return {"correct": False, "reason": "unknown subcat " + str(sub)}
