# -*- coding: utf-8 -*-
"""d9_judge.py - rule-based judging for D9 (real-world tasks).
No LLM judge: each question carries machine-checkable checks distilled from a
ONE-TIME cloud baseline answer. Checks:
  any_of     : at least one of the listed strings/number-forms must appear
  all_of     : every listed item must appear
  min_count  : at least N of the listed items must appear
  forbidden  : none of these may appear (anti-fabrication / anti-guessing)
  number     : a number must appear within tolerance
Scoring: weighted coverage of the required checks, minus forbidden penalties.
"""
import re


def _has(text, needle):
    n = str(needle)
    if n in text:
        return True
    # tolerate common number formatting: 190.9 -> "190.9", "190.90", "190.9 元"
    try:
        val = float(n)
    except Exception:
        return False
    for m in re.finditer(r"-?\d+(?:\.\d+)?", text):
        try:
            if abs(float(m.group(0)) - val) < 1e-9:
                return True
        except Exception:
            continue
    return False


# ---------- semantic tolerance layer ----------
# D9 checks are written by hand, and hand-written checks drift toward the exact
# wording of the answer they were written against. That punished any answer that
# expressed the same requirement differently (the same brittleness found in the
# cloud comparison). We normalize common equivalent expressions before matching.

_WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_WEEK_ALT = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期天"]
_DAYWORD = ["第一天", "第二天", "第三天", "第四天", "第五天", "第六天", "第七天"]

SYNONYMS = {
    "甜点": ["最优点", "最优值", "甜蜜点", "最佳点", "平衡点", "最优区间", "最优比例"],
    "不是越高越好": ["并非越高越好", "不是越多越好", "并非越多越好", "不是越大越好"],
    "存在最优": ["找最优", "找到最优", "寻优", "最优区间", "边际收益"],
    "复测": ["重测", "实测", "自己测", "本地验证", "自行验证", "亲自测试", "实际测量"],
    "本机": ["本地", "自己机器", "自己机器上", "自己的硬件", "当前硬件"],
    "不能直接迁移": ["不能照搬", "不可直接套用", "需按自己硬件调整", "因人而异"],
    "轮换": ["轮替", "交替", "换着吃", "轮换菜"],
    "不重复": ["不相同", "均不相同", "避免连续两天相同", "不重样", "各不相同"],
    "不连排": ["避免连续两天相同", "不连续", "相邻两天"],
}


def expand_text(text):
    """Return text plus injected canonical tokens for equivalent expressions."""
    t = text or ""
    extra = []
    # day markers: 周一..周日 / 第一天.. / Day1.. -> inject Day<N> tokens
    days = 0
    for i, w in enumerate(_WEEK):
        if w in t:
            days += 1
    for i, w in enumerate(_WEEK_ALT):
        if w in t:
            days += 1
    for i, w in enumerate(_DAYWORD):
        if w in t:
            days += 1
    for i in range(1, 8):
        if ("Day%d" % i) in t or ("D%d" % i) in t or ("第%d天" % i) in t:
            days += 1
    days = min(days, 7)
    for i in range(1, days + 1):
        extra.append("Day%d" % i)
        extra.append("第%d天" % i)
    # curated synonyms: if the answer says the variant, also expose the canonical
    for canon, variants in SYNONYMS.items():
        for v in variants:
            if v in t:
                extra.append(canon)
                break
    # whitespace brittleness: "第 1 天" must match a check written as "第1天".
    # Emit a space-stripped variant of both the text and the injected tokens.
    stripped = re.sub(r"[ 	]+", "", t)
    extra_stripped = re.sub(r"[ 	]+", "", " ".join(extra))
    return t + " " + " ".join(extra) + " " + stripped + " " + extra_stripped


def _run_check(chk, text):
    # match on both the raw and the space-stripped forms
    flat = re.sub(r"[ 	]+", "", text or "")
    t = chk.get("type")
    vals = chk.get("values") or ([chk["value"]] if "value" in chk else [])
    if t == "any_of":
        hit = [v for v in vals if _has(text, v)]
        return bool(hit), "命中 %s" % (hit[:3] if hit else "无")
    if t == "all_of":
        miss = [v for v in vals if not _has(text, v)]
        return not miss, ("缺 %s" % miss[:3]) if miss else "齐全"
    if t == "min_count":
        hit = [v for v in vals if _has(text, v)]
        need = chk.get("n", 1)
        return len(hit) >= need, "命中 %d/%d(需 %d)" % (len(hit), len(vals), need)
    if t == "forbidden":
        hit = [v for v in vals if _has(text, v)]
        return not hit, ("出现 %s" % hit[:2]) if hit else "未出现"
    if t == "number":
        want = chk.get("value")
        tol = chk.get("tolerance", 0.01)
        found = False
        for m in re.finditer(r"-?\d+(?:\.\d+)?", text):
            try:
                if abs(float(m.group(0)) - float(want)) <= tol:
                    found = True
                    break
            except Exception:
                continue
        return found, "期望 %s(±%s)" % (want, tol)

    # ------------------------------------------------------------------
    # Anti-fabrication checks.
    #
    # A real task ("read this log and tell me what failed") has no single
    # correct sentence, so an any_of check would either pass everything or
    # demand exact wording. What IS checkable is whether the answer stayed
    # inside the evidence: did it quote identifiers that exist, or invent
    # ones? D8 measured a 74% fabrication rate on invented subjects, so this
    # is the failure real tasks actually hit.
    # ------------------------------------------------------------------
    if t == "cite_from":
        # at least N identifiers listed in 'allowed' must appear -> the answer
        # is grounded in the supplied material rather than free-floating.
        allowed = [str(x) for x in (chk.get("allowed") or [])]
        need = int(chk.get("n", 1))
        hit = [a for a in allowed if _has(text, a)]
        return len(hit) >= need, "引用 %d/%d(需 %d): %s" % (len(hit), len(allowed), need, hit[:3])

    if t == "no_invention":
        # Any identifier that LOOKS like the domain's identifiers but is not in
        # 'allowed' counts as invented.
        #
        # The pattern MUST be written with non-word lookarounds rather than \b:
        # in mixed CJK/ASCII text \b still matches between 汉字 and letters, so
        # "\bamount\w*\b" swallowed the whole run
        # "amountamount_centsorders字段名不对..." and reported it as an invented
        # identifier. That turned a correct answer into a failure.
        pat = chk.get("pattern")
        allowed = set(str(x) for x in (chk.get("allowed") or []))
        if not pat:
            return True, "no pattern"
        # Gluing is normal in mixed text: "amount_cents字段" and
        # "amountamount_cents" both occur in real replies and neither has a word
        # boundary a regex can use. Substituting allowed tokens out does NOT
        # work either, because "amount" is a prefix of both "amount_cents" and
        # the invented "amount_total": removing it leaves "_total", which no
        # longer matches the pattern and the fabrication disappears.
        #
        # Correct order: enumerate the candidates first, THEN decide. A
        # candidate is legitimate when it equals an allowed token; an allowed
        # token that is only a prefix of a longer run ("amount" inside
        # "amount_total") does not legitimise the run.
        invented = set()
        for m in re.finditer(pat, text):
            tok = m.group(0)
            if tok in allowed:
                continue
            # a run may begin with an allowed token only when that token ends
            # cleanly - i.e. the rest is a separator, never more identifier
            if any(tok.startswith(a) and tok[len(a):len(a) + 1] not in
                   ("", "_", "-", ".", " ", "\t", "\n") for a in allowed):
                invented.add(tok)
            elif tok not in allowed:
                invented.add(tok)
        invented = sorted(invented)
        return not invented, ("编造 %s" % invented[:3]) if invented else "无编造"

    if t == "admits_limit":
        # the supplied material cannot answer the question; a pass requires the
        # model to say so instead of producing a plausible number.
        words = [str(x) for x in (chk.get("values") or [])]
        if not words:
            words = ["未提及", "没有提到", "文档中不包含", "无法从", "日志中没有",
                     "未记录", "不存在", "没有相关", "查不到", "无法确定",
                     # a model that declines to invent usually says one of these
                     "无法核实", "无法验证", "不能确认", "无法确认", "没有足够信息",
                     "未提供", "摘要中未", "没有给出", "无法判断"]
        hit = [w for w in words if _has(text, w)]
        return bool(hit), "承认信息不足: %s" % (hit[:2] or "未承认")

    return False, "unknown check %r" % t


def judge_d9(entry, response):
    """entry = a d9_baseline record (has 'checks'); response = model answer."""
    raw = response or ""
    # expand_text exists to forgive wording differences in SEMANTIC checks. It
    # appends a whitespace-free copy of the reply, and identifier checks then
    # read that copy as one invented token ("amountamount_cents"). Those check
    # types must see the raw text; everything else keeps the expansion.
    text = expand_text(raw)
    IDENT_CHECKS = ("no_invention", "cite_from")
    checks = entry.get("checks") or []
    if not checks:
        return {"correct": False, "reason": "no checks defined for this question"}
    results, got_w, tot_w = [], 0.0, 0.0
    for chk in checks:
        w = float(chk.get("weight", 1.0))
        body = raw if chk.get("type") in IDENT_CHECKS else text
        if chk.get("type") == "forbidden":
            ok, note = _run_check(chk, text)
            results.append({"label": chk.get("label", "禁止项"), "ok": ok,
                            "weight": w, "note": note, "kind": "forbidden"})
            continue
        ok, note = _run_check(chk, body)
        tot_w += w
        got_w += w if ok else 0.0
        results.append({"label": chk.get("label", chk.get("type")), "ok": ok,
                        "weight": w, "note": note, "kind": "required"})
    coverage = (got_w / tot_w) if tot_w else 0.0
    violations = [r["label"] for r in results if r["kind"] == "forbidden" and not r["ok"]]
    # pass = full required coverage and no forbidden violation
    ok = coverage >= 0.999 and not violations
    return {"correct": ok, "coverage": coverage, "violations": violations,
            "checks": results,
            "reason": "覆盖 %.0f%%%s" % (coverage * 100,
                                        (",违规:" + ",".join(violations)) if violations else "")}
