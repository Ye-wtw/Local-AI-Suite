# -*- coding: utf-8 -*-
"""longctx_judge.py - rule-based judging for the D7 long-context dimension.
Subcategories:
  needle-single : the owner of a GIVEN project code must appear in the answer
  needle-multi  : every code listed, and NO fabricated extra codes
  cross-doc     : how many of the known contradictions were found
  long-qa       : both required values present
"""
import re

CODE_RE = re.compile(r"PRJ[-_ ]?(\d{3,6})", re.I)


def _norm_codes(text):
    return set("PRJ-" + m.group(1) for m in CODE_RE.finditer(text or ""))


# How many DISTINCT owners a reply may name before it is treated as a list.
# The question already states the code, so "PRJ-1234 由谁负责" answered with just
# "陈工" is correct and must pass. The attack worth blocking is a reply that
# dumps every owner in the document hoping one of them lands on this question.
# One name (the right one) passes; a roster of several does not.
MAX_OWNERS_IN_REPLY = 1


def judge_needle_single(entry, response):
    """The expected owner must appear, and the reply must not be an owner roster.

    History (both attempts were wrong in opposite directions, kept so the next
    edit does not repeat them):

    v1 - pass if the owner string appears anywhere. A reply listing every owner
         found in the document scored 6/6 on questions it never answered.

    v2 - require the owner within 200 chars of the code. That dropped the
         enumeration attack to 0/6, but the code is ALREADY IN THE QUESTION
         ("文档中项目编号 PRJ-6746 由谁负责?"), so a correct two-character answer
         ("陈工") was marked wrong and a whole long-context run scored 0/36 on
         answers that were all right.

    v3 - the actual attack is enumeration, not missing context echo: count how
         many distinct owner names the reply contains. Naming the right one
         passes; naming a roster does not.
    """
    exp = entry.get("expect", {})
    code, owner = exp.get("code", ""), exp.get("owner", "")
    txt = (response or "").strip()
    if not owner:
        return {"correct": False, "reason": "no expected owner", "expected": ""}

    named = [o for o in (exp.get("all_owners") or []) if o and o in txt]
    roster = exp.get("all_owners") or []
    if roster:
        distinct = sorted(set(named))
        if owner not in txt:
            return {"correct": False,
                    "reason": "owner %s MISSING (reply named %s)" % (owner, distinct),
                    "expected": owner, "named": distinct}
        if len(distinct) > MAX_OWNERS_IN_REPLY:
            return {"correct": False,
                    "reason": "roster of %d owners %s - enumeration, not retrieval"
                              % (len(distinct), distinct),
                    "expected": owner, "named": distinct}
        return {"correct": True, "reason": "owner %s (single name)" % owner,
                "expected": owner, "named": distinct}

    # No owner list was provided for this entry: fall back to a plain presence
    # check. Cannot detect enumeration here, so the caller should supply the list.
    got = owner in txt
    return {"correct": bool(got),
            "reason": "owner %s %s (no owner roster supplied)" % (owner, "found" if got else "MISSING"),
            "expected": owner, "named": [owner] if got else []}


def judge_needle_multi(entry, response):
    want = set(c.upper() for c in entry.get("expect", {}).get("needles", []))
    got = _norm_codes(response)
    missing = sorted(want - got)
    fabricated = sorted(got - want)
    ok = not missing and not fabricated
    return {"correct": ok,
            "reason": "missing=%s fabricated=%s" % (missing, fabricated),
            "expected": sorted(want), "missing": missing, "fabricated": fabricated,
            "recall": (len(want) - len(missing)) / len(want) if want else 0.0}


def judge_cross_doc(entry, response):
    txt = (response or "")
    checks = {
        "名称": ("AlphaWorks" in txt or "alphaworks" in txt.lower()) and ("BetaAlpha" in txt or "betaalpha" in txt.lower()),
        "总部": "上海" in txt and "深圳" in txt,
        "时间": ("6月30" in txt or "六月三十" in txt or "6 月 30" in txt) and ("9月30" in txt or "九月三十" in txt or "9 月 30" in txt),
        "保留率": "90" in txt and "70" in txt,
    }
    found = [k for k, v in checks.items() if v]
    score = len(found) / len(checks)
    return {"correct": score >= 0.75,          # 3/4 以上算通过
            "reason": "found %d/4: %s" % (len(found), found),
            "must_find": entry.get("expect", {}).get("must_find", []),
            "partial_score": score}


def judge_long_qa(entry, response):
    txt = response or ""
    has_days = bool(re.search(r"30\s*天", txt)) or "三十天" in txt
    has_fee = bool(re.search(r"20\s*%", txt)) or "百分之二十" in txt
    ok = has_days and has_fee
    return {"correct": ok, "reason": "days=%s fee=%s" % (has_days, has_fee),
            "expected": entry.get("expect", {}).get("answer_refs", [])}


def judge_longctx(entry, response):
    sub = entry.get("subcat")
    if sub == "needle-single":
        return judge_needle_single(entry, response)
    if sub == "needle-multi":
        return judge_needle_multi(entry, response)
    if sub == "cross-doc":
        return judge_cross_doc(entry, response)
    if sub == "long-qa":
        return judge_long_qa(entry, response)
    return {"correct": False, "reason": "unknown subcat " + str(sub)}
