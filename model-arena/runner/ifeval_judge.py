# -*- coding: utf-8 -*-
"""ifeval_judge.py - rule-based IFEval checking, no LLM judge.

IFEval ships 25 instruction types across the 400 curated questions. Every one of
them is verifiable by code in the official implementation, which is what makes
the benchmark usable here: the project forbids LLM judges.

Two honest limits, both recorded per question instead of hidden:

  1. language:response_language needs a language detector. The official harness
     uses langdetect; this project has zero third-party dependencies, so those
     24 questions are reported as "unsupported" and excluded from the score
     rather than guessed at. A wrong guess would silently corrupt D5.

  2. Sentence and paragraph splitting approximate the official nltk tokenizer
     with regex. For the constraints used here (count >= N sentences, paragraph
     starts with word X) the approximation rarely changes the verdict, but it is
     an approximation and the result records it.
"""
import json
import re

# --- text helpers -------------------------------------------------------

def _words(t):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\x27-]*", t or "")


def _sentences(t):
    """Split on sentence-final punctuation followed by space or end.

    Regex, not nltk: the constraint in the questions is "at least N sentences",
    which a good-enough splitter settles. Abbreviations inflate the count, which
    is why only "at least" style checks are trusted against it.
    """
    parts = re.split(r"(?<=[.!?])\s+", (t or "").strip())
    return [p for p in parts if p.strip()]


def _paragraphs(t):
    parts = re.split(r"\n\s*\n", (t or "").strip())
    return [p for p in parts if p.strip()]


def _cmp(value, relation, target):
    r = (relation or "").lower()
    if r == "at least":
        return value >= target, "%d >= %d" % (value, target)
    if r == "less than":
        return value < target, "%d < %d" % (value, target)
    if r == "at most":
        return value <= target, "%d <= %d" % (value, target)
    if r == "more than":
        return value > target, "%d > %d" % (value, target)
    return value == target, "%d == %d" % (value, target)


# --- one checker per instruction id ------------------------------------

def _no_comma(t, kw, entry):
    return ("," not in t), "comma count=%d" % t.count(",")


def _number_words(t, kw, entry):
    n = len(_words(t))
    return _cmp(n, kw.get("relation"), int(kw.get("num_words") or 0))


def _number_sentences(t, kw, entry):
    n = len(_sentences(t))
    return _cmp(n, kw.get("relation"), int(kw.get("num_sentences") or 0))


def _forbidden_words(t, kw, entry):
    low = (t or "").lower()
    hit = [w for w in (kw.get("forbidden_words") or []) if str(w).lower() in low]
    return (not hit), ("found %s" % hit[:3]) if hit else "none found"


def _frequency(t, kw, entry):
    word = str(kw.get("keyword") or "").lower()
    n = len(re.findall(r"\b" + re.escape(word) + r"\b", (t or "").lower())) if word else 0
    return _cmp(n, kw.get("relation"), int(kw.get("frequency") or 0))


def _letter_frequency(t, kw, entry):
    letter = str(kw.get("letter") or "").lower()
    n = (t or "").lower().count(letter) if letter else 0
    return _cmp(n, kw.get("let_relation"), int(kw.get("let_frequency") or 0))


def _existence(t, kw, entry):
    low = (t or "").lower()
    miss = [k for k in (kw.get("keywords") or []) if str(k).lower() not in low]
    return (not miss), ("missing %s" % miss[:3]) if miss else "all present"


def _repeat_prompt(t, kw, entry):
    want = re.sub(r"\s+", " ", str(kw.get("prompt_to_repeat") or "")).strip().lower()
    have = re.sub(r"\s+", " ", t or "").strip().lower()
    return (bool(want) and want in have), "repeated=%s" % (want[:30] if want else "-")


def _highlighted_sections(t, kw, entry):
    n = len(re.findall(r"\*[^*\n]+\*", t or ""))
    return _cmp(n, "at least", int(kw.get("num_highlights") or 0))


def _quotation(t, kw, entry):
    s = (t or "").strip()
    ok = len(s) >= 2 and s[0] in chr(34) + chr(8220) and s[-1] in chr(34) + chr(8221)
    return ok, "starts=%r ends=%r" % (s[:1], s[-1:])


def _title(t, kw, entry):
    return bool(re.search(r"<<[^>]+>>", t or "")), "title marker"


def _lowercase(t, kw, entry):
    s = (t or "").strip()
    return (s == s.lower()), "all lower=%s" % (s == s.lower())


def _capital(t, kw, entry):
    s = (t or "").strip()
    return (s == s.upper()), "all upper=%s" % (s == s.upper())


def _end_checker(t, kw, entry):
    phrase = str(kw.get("end_phrase") or "")
    s = (t or "").strip()
    return (bool(phrase) and s.endswith(phrase)), "ends with %r" % phrase[:30]


def _number_placeholders(t, kw, entry):
    n = len(re.findall(r"\[[^\]]+\]", t or ""))
    return _cmp(n, "at least", int(kw.get("num_placeholders") or 0))


def _number_bullets(t, kw, entry):
    n = len(re.findall(r"^\s*[*-]\s+", t or "", re.M))
    return _cmp(n, "at least", int(kw.get("num_bullets") or 0))


def _number_paragraphs(t, kw, entry):
    n = len(_paragraphs(t))
    return _cmp(n, "at least", int(kw.get("num_paragraphs") or 0))


def _postscript(t, kw, entry):
    marker = str(kw.get("postscript_marker") or "P.S.")
    return (marker in (t or "")), "marker %r" % marker


def _two_responses(t, kw, entry):
    n = (t or "").count("*" * 6)
    return (n >= 1), "separators=%d" % n


def _capital_word_frequency(t, kw, entry):
    n = sum(1 for w in _words(t) if len(w) >= 2 and w.isupper())
    return _cmp(n, kw.get("capital_relation"), int(kw.get("capital_frequency") or 0))


def _json_format(t, kw, entry):
    s = (t or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try:
        json.loads(s)
        return True, "valid json"
    except Exception as e:
        return False, "invalid json: %s" % str(e)[:40]


def _constrained_response(t, kw, entry):
    ok_forms = ["my answer is yes.", "my answer is no.", "my answer is maybe.",
                "yes", "no", "maybe"]
    s = re.sub(r"\s+", " ", (t or "").strip().lower())
    return (s in ok_forms), "reply=%r" % s[:30]


def _nth_paragraph_first_word(t, kw, entry):
    ps = _paragraphs(t)
    k = int(kw.get("nth_paragraph") or 1)
    want = str(kw.get("first_word") or "").lower()
    if len(ps) < k:
        return False, "only %d paragraphs, needed %d" % (len(ps), k)
    ws = _words(ps[k - 1])
    got = ws[0].lower() if ws else ""
    return (got == want), "para%d starts %r want %r" % (k, got, want)


def _multiple_sections(t, kw, entry):
    splitter = str(kw.get("section_spliter") or kw.get("section_splitter") or "Section")
    n = (t or "").count(splitter)
    return _cmp(n, "at least", int(kw.get("num_sections") or 0))


CHECKERS = {
    "punctuation:no_comma": _no_comma,
    "length_constraints:number_words": _number_words,
    "length_constraints:number_sentences": _number_sentences,
    "keywords:forbidden_words": _forbidden_words,
    "keywords:frequency": _frequency,
    "keywords:letter_frequency": _letter_frequency,
    "keywords:existence": _existence,
    "combination:repeat_prompt": _repeat_prompt,
    "detectable_format:number_highlighted_sections": _highlighted_sections,
    "startend:quotation": _quotation,
    "detectable_format:title": _title,
    "change_case:english_lowercase": _lowercase,
    "change_case:english_capital": _capital,
    "startend:end_checker": _end_checker,
    "detectable_content:number_placeholders": _number_placeholders,
    "detectable_format:number_bullet_lists": _number_bullets,
    "length_constraints:number_paragraphs": _number_paragraphs,
    "detectable_content:postscript": _postscript,
    "combination:two_responses": _two_responses,
    "change_case:capital_word_frequency": _capital_word_frequency,
    "detectable_format:json_format": _json_format,
    "detectable_format:constrained_response": _constrained_response,
    "length_constraints:nth_paragraph_first_word": _nth_paragraph_first_word,
    "detectable_format:multiple_sections": _multiple_sections,
}

# Needs a language detector; excluded and reported, never guessed.
UNSUPPORTED = {"language:response_language"}


def judge_ifeval(entry, response):
    """A question passes only when EVERY listed instruction passes."""
    exp = entry.get("expect") or {}
    ids = exp.get("instruction_id_list") or []
    kws = exp.get("kwargs") or []
    txt = response or ""

    if not ids:
        return {"correct": False, "reason": "no instructions", "detail": []}

    if any(i in UNSUPPORTED for i in ids):
        return {"correct": False, "skipped": True,
                "reason": "needs language detector (not available)",
                "detail": [{"id": i, "ok": None, "note": "unsupported"} for i in ids]}

    detail = []
    ok_all = True
    for i, iid in enumerate(ids):
        kw = kws[i] if i < len(kws) else {}
        fn = CHECKERS.get(iid)
        if fn is None:
            ok, note = False, "no checker for %s" % iid
        else:
            try:
                ok, note = fn(txt, kw, entry)
            except Exception as e:
                ok, note = False, "checker error: %s" % str(e)[:50]
        ok_all = ok_all and ok
        detail.append({"id": iid, "ok": ok, "note": note})

    failed = [d["id"] for d in detail if not d["ok"]]
    return {"correct": ok_all, "skipped": False,
            "reason": "all %d passed" % len(ids) if ok_all
                      else ("failed: " + ", ".join(failed[:3])),
            "detail": detail}