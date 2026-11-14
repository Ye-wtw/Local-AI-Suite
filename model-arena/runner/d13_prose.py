# -*- coding: utf-8 -*-
"""d13_prose.py - D13 文笔/剧情创作，纯形式指标，纯标准库。

== 这个维度测什么，不测什么 ==

测：形式合规性 —— 复读、套话、感官覆盖、句长节奏、剧情推进、对话占比、标点丰富度、字数。
不测：文学价值。没有任何规则能判断"写得好不好"，而本项目禁止 LLM 裁判（铁律 1）。
所以本模块及其汇总**不会出现"文笔好/差"这类措辞**，只报可复现的形式量。

== 为什么编号是 D13 而不是需求里的 D11 ==
D11 已被视觉维度占用（vision.py / datasets/fixtures/vision/d11_*.png /
run_all.py 的 ("vision","D11","视觉") 映射），D12 是压缩。D1-D12 全满。
改动已占用的编号会波及现有 suite 的判分与历史结果（铁律 3 要求改判分器后重跑历史），
成本远高于换一个号码。

== 短篇 / 长篇 ==
同一维度内按 subcat 分档：
  prose/narrative/dialogue/rewrite/constraint  -> 短篇（300-800 字）
  上述每个 subcat 另有 _long 后缀          -> 长篇（2000-4000 字）
指标共用一套；汇总里分档报中位数，因为"短篇写得紧凑"和"长篇能撑住"
不是同一种能力。

== 一个必须记住的校准事实（2026-11-10 本机实测）==
同一模型、同一提示词，只改采样参数：
  temp=0（贪心）       -> 三个 seed 输出字节完全相同，distinct-2 = 0.984
  temp=1.0/top-p=0.95  -> 三个 seed 各不相同，distinct-2 = 1.000
结论：(a) distinct_n 有效，能区分复读与变化；
      (b) **端点采样参数会大幅改变 D13 分数**，所以汇总必须记录 temp/top_p/top_k，
          否则不同采样下的分数不可比。summarize() 的返回值里带 sampling 字段。
"""
import math
import re

# ---------------- 基础切分 ----------------

HAN = re.compile(r"[\u4e00-\u9fff]")
SENT_SPLIT = re.compile(r"[。！？!?…]+|\n\s*\n")
PUNCT_KINDS = set("，。！？；：、…—（）《》「」『』“”‘’,.!?;:")


def han_only(text):
    """只保留汉字 —— 长度类指标一律基于它，避免英文/数字/标点污染。"""
    return "".join(HAN.findall(text or ""))


def han_count(text):
    return len(han_only(text))


def split_sentences(text):
    """按中文句末标点与空行切句，丢掉空白句。"""
    parts = SENT_SPLIT.split(text or "")
    return [p.strip() for p in parts if p and p.strip()]


# ---------------- 指标 ----------------

def distinct_n(text, n=2):
    """不同 n-gram / 全部 n-gram。1.0 = 无重复；越低越重复。

    在**汉字串**上算，不跨标点 —— 否则"。他"这类跨句 n-gram 会虚高多样性。
    """
    s = han_only(text)
    if len(s) < n:
        return 0.0
    grams = [s[i:i + n] for i in range(len(s) - n + 1)]
    if not grams:
        return 0.0
    return len(set(grams)) / float(len(grams))


def ttr(text):
    """不同字 / 总字数。中文正常约 0.25-0.45；过高说明刻意堆砌生僻字。"""
    s = han_only(text)
    if not s:
        return 0.0
    return len(set(s)) / float(len(s))


def cv_sentence_len(text):
    """句长变异系数 = 标准差 / 均值（按汉字数）。太低 = 句子长短雷同、节奏平。"""
    lens = [han_count(s) for s in split_sentences(text)]
    lens = [n for n in lens if n > 0]
    if len(lens) < 2:
        return 0.0
    mean = sum(lens) / float(len(lens))
    if mean <= 0:
        return 0.0
    var = sum((n - mean) ** 2 for n in lens) / float(len(lens))
    return math.sqrt(var) / mean


# 套话/AI 腔。命中即该题不通过（提示词明确要求）。
CLICHE = [
    "总之", "总而言之", "综上所述", "总的来说", "以上就是", "首先", "其次",
    "这让我", "不由得", "不禁想起", "可以说", "不得不说", "值得注意的是",
    "作为一个AI", "作为一个ai", "作为AI", "作为ai", "我是AI", "我是ai",
    "作为人工智能", "希望对你", "让我们一起",
    "深深", "仿佛在诉说", "似乎在诉说",
]

# 感官词表：**只放词，不放单字**。
#
# 为什么：单字 "光" 会同时命中「光亮」和「光说」，误报率极高。项目 AGENTS.md
# 的反例清单里有同类教训（\b 正则在 CJK 混排里失效）。实测若用单字，
# 一段纯说明文也能拿满感官覆盖，指标立刻失去意义。
SENSORY = {
    "visual": ["光影", "光斑", "暮色", "斜阳", "昏暗", "昏黄", "斑驳", "朦胧", "倒影",
               "夜色", "苍白", "灰白", "褐色", "亮光", "暗影", "灯光", "雾气", "余晖",
               "阴影", "闪烁", "光线", "颜色", "远处", "窗棂"],
    "audio": ["咔嗒", "吱呀", "沙沙", "嗡鸣", "窸窣", "回响", "低语", "吆喝", "咳嗽",
              "脚步", "响声", "寂静", "喧闹", "嘈杂", "嘈杂声", "滴水声", "风声",
              "雨声", "呼吸声", "吱嘎", "铮", "轰鸣"],
    "smell": ["香味", "臭气", "腥味", "霉味", "烟味", "油烟", "汗味", "潮气", "土腥",
              "孜然", "炭火", "香气", "气味", "芬芳", "焦味", "药味", "霉臭"],
    "touch": ["冰凉", "粗糙", "湿冷", "滚烫", "发烫", "黏腻", "干裂", "冻僵", "寒冷",
              "温热", "柔软", "坚硬", "刺骨", "滑腻", "湿漉漉", "暖和", "潮乎乎"],
    "taste": ["苦涩", "甘甜", "酸甜", "咸腥", "寡淡", "辛辣", "油腻", "甜味", "苦味",
              "发酸", "发苦", "回味", "齿间"],
}

# 词根：允许 0-2 个后接汉字，覆盖「腥→腥气/腥味」「咸→咸涩/咸腥」这类派生。
# 只用双字或双字以上的根，单字会误报（"光" 命中 "光说"）。
SENSORY_ROOTS = {
    "visual": ["天光", "光线", "灯光", "日光", "月光", "斑驳", "昏黄", "灰白", "黯淡",
               "暗沉", "闪烁", "雾", "暮色", "夜色", "倒影", "影子", "光晕", "晶莹"],
    "audio": ["声响", "声音", "回响", "咔嚓", "吱呀", "沙沙", "低语", "滴答", "嘈杂",
              "喧闹", "脚步", "嗡", "清脆", "闷响", "呼吸声", "风声", "雨声"],
    "smell": ["腥气", "腥味", "气味", "香气", "香味", "臭味", "霉味", "潮气", "土腥",
              "油烟", "汗味", "药味", "焦味", "芬芳", "气息"],
    "touch": ["冰凉", "刺骨", "黏腻", "粗糙", "湿冷", "滚烫", "温热", "柔软", "坚硬",
              "冻僵", "寒冷", "发凉", "生疼", "滑腻", "湿漉漉", "暖和", "微凉"],
    "taste": ["苦涩", "甘甜", "酸甜", "咸涩", "咸腥", "寡淡", "辛辣", "油腻", "发苦",
              "发酸", "回味", "齿间", "舌尖", "甜味", "苦味", "涩"],
}

TURN = ["却", "但", "然而", "忽然", "突然", "没想到", "不料", "结果", "直到",
        "原来", "谁料", "偏偏", "谁知", "紧接着", "转头", "回头", "于是", "竟然",
        "反而", "终于"]

QUOTE_RE = re.compile(r"[「『“\"]([^」』”\"]*)[」』”\"]")


# ---------------- 单题判分 ----------------

def _sense_hits(text, words, roots):
    """命中判定：词表精确匹配 **或** 词根+一到两个修饰字。

    为什么不能只做精确匹配：实测真模型写「鱼腥气」「咸涩」「叮。清脆」
    而词表里是「腥味」「咸腥」，于是五类感官全覆盖的段落被判"感官不足"。
    这正是 AGENTS.md 反例清单里"措辞精确匹配"的同类错误 —— 判分器比模型更容易出错。

    为什么也不能退回单字：单字"光"会同时命中「光亮」和「光说」。所以只用
    **双字词根**，允许后面跟 0-2 个汉字（腥 → 腥气/腥味，但不匹配单独的"腥"）。
    """
    hits = []
    for w in words:
        if w in text:
            hits.append(w)
    for r in roots:
        if re.search(r + r"[\u4e00-\u9fff]{0,2}", text):
            hits.append(r)
    # 去重但保序
    seen = set()
    out = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out



# ---------------- chat 语言感（反 AI 腔）----------------
#
# D13 原本只测「创作」。日常对话是另一类能力：措辞克制、不奉承、不说教、
# 不把一句话拆成一堆 Markdown 分点。本地模型最常犯的就是这个。
#
# 词表不是凭空想的，来自实测：扫本机 results/ 611 个文件、34320 条真实回答，
# 统计这些短语的实际出现频次 ——
#   以下是 541 / 首先 120 / 当然可以 44 / 作为一个 33 / 需要注意的是 20
#   / 综上所述 18 / 总之 10 / 简而言之 2 / 值得注意的是 2
# 所以下面每一条都有据可查，而不是我猜的。

CHAT_OPENERS = [
    # 开场套话：模型习惯先声明一句再说正文
    "当然可以", "当然", "好的", "没问题", "很高兴为你", "让我来",
    "我来为你", "以下就是", "以下分析", "以下是", "下面是",
]

CHAT_SYCOPHANCY = [
    # 奉承：真实语料里出现 33 次「作为一个」，44 次「当然可以」
    "好问题", "这个问题很", "你的问题很好", "非常好的问题",
    "作为一个", "作为AI", "作为一个人工智能", "我是AI",
    "我理解你的", "你的感受", "希望能帮", "希望这能帮", "希望对你",
    "希望这些", "希望这份", "如果还有", "随时告诉我",
]

CHAT_PREACHY = [
    # 说教/总结陈词：把回答写成文章而不是对话
    "首先", "其次", "再次", "最后", "综上", "总之", "总的来说",
    "值得注意的是", "需要注意的是", "需要指出", "简而言之",
    "总结一下", "一句话总结", "总的来说",
]

CHAT_STRUCTURE_MARKERS = [
    # 结构标记：把一句话答成汇报
    "1.", "2.", "3.", "一、", "二、", "三、",
    "- ", "* ", "###", "##", "**",
]


def chat_tone(text):
    """量一份回答的「AI 腔」程度。全部是**存在性**判断，不做主观打分。

    返回计数而不是布尔值：模型偶尔说一次「首先」不算毛病，整段都是才是。
    所以判据用 lte 阈值，而不是 not_contains 的一票否决 —— 
    这和 cliche_hits（创作题一票否决）口径不同，是故意的：
    创作题要求「整篇没有套话」，对话题只要求「别把对话写成公文」。
    """
    txt = text or ""
    head = txt[:80]
    return {
        "chat_opener": sum(1 for w in CHAT_OPENERS if w in head),
        "chat_sycophancy": sum(1 for w in CHAT_SYCOPHANCY if w in txt),
        "chat_preachy": sum(1 for w in CHAT_PREACHY if w in txt),
        "chat_bullets": _count_bullets(txt),
        "chat_bold": txt.count("**") // 2,
        "chat_para": len([p for p in txt.split(chr(10) + chr(10)) if p.strip()]),
    }


def _count_bullets(text):
    """数「分点」的数量：行首的 1. / - / 一、 这类。"""
    import re as _re
    n = 0
    for line in (text or "").splitlines():
        s = line.strip()
        if _re.match(r"^(\d+[.):]|[-*+]\s|[一二三四五六七八九十]+[、.])", s):
            n += 1
    return n


def measure(text):
    """一次算出全部指标，供 judge 与汇总共用。"""
    txt = text or ""
    sens = {k: _sense_hits(txt, ws, SENSORY_ROOTS.get(k, []))
            for k, ws in SENSORY.items()}
    quoted = "".join(QUOTE_RE.findall(txt))
    return {
        "han_count": han_count(txt),
        "distinct_2": distinct_n(txt, 2),
        "distinct_3": distinct_n(txt, 3),
        "ttr": ttr(txt),
        "cv_sentence_len": cv_sentence_len(txt),
        "cliche_hits": [w for w in CLICHE if w in txt],
        "sensory_cats": sorted(k for k, v in sens.items() if v),
        "sensory_detail": {k: v[:3] for k, v in sens.items() if v},
        "turn_hits": [w for w in TURN if w in txt],
        "dialogue_ratio": (han_count(quoted) / float(han_count(txt))) if han_count(txt) else 0.0,
        "punct_kinds": len({c for c in txt if c in PUNCT_KINDS}),
        "opening_repeat": _opening_repeat(txt),
        # 下面四个是为了让"题面写了的硬要求"真的被判到。
        # 审计发现 7 处题面要求没有对应判据（如"至少 8 个句子""必须出现铁轨"），
        # 那样的题模型完全不按要求写也能拿 100 分。
        "sentence_count": len([s for s in split_sentences(txt) if han_count(s) > 0]),
        "dialogue_count": len(QUOTE_RE.findall(txt)),
        "contains": txt,          # 供 contains/not_contains 用原始文本匹配
        # chat 语言感：只有在题目是 chat 类时才会被 check 用到
        **chat_tone(txt),
    }


def _opening_repeat(text):
    """连续句子的前两字相同次数。模板化/复读会高。"""
    sents = [han_only(s) for s in split_sentences(text)]
    sents = [s for s in sents if len(s) >= 2]
    n = 0
    for a, b in zip(sents, sents[1:]):
        if a[:2] == b[:2]:
            n += 1
    return n


def _check_ok(chk, m):
    """按 check 的 op 判一条。返回 (ok, 实测值, 阈值)。"""
    t = chk.get("type")
    op = chk.get("op")
    val = chk.get("value")
    got = m.get(t)

    if t == "cliche_hits":
        # 命中即失败：语义是"必须 0 命中"
        ok = (not got) if op in (None, "not_contains", "lte") else bool(got)
        return ok, got, "空"
    if t == "sensory_cats":
        n = len(got or [])
        if op == "coverage" or op == "gte":
            return n >= int(val), n, val
        if op == "lte":
            return n <= int(val), n, val
        return n >= int(val), n, val
    if t == "turn_hits":
        n = len(got or [])
        return n >= int(val), n, val
    if t in ("distinct_2", "distinct_3", "ttr", "cv_sentence_len", "dialogue_ratio"):
        g = float(got or 0.0)
        # in_range 的 value 是 [lo, hi]，必须先分支再转型 —— 直接 float(val)
        # 会在 ttR 这类用区间的题上抛 TypeError（实测把整个 --verify 打断）。
        if op == "in_range":
            lo, hi = float(val[0]), float(val[1])
            return lo <= g <= hi, round(g, 4), [lo, hi]
        v = float(val)
        if op == "lte":
            return g <= v, round(g, 4), v
        return g >= v, round(g, 4), v
    if t in ("sentence_count", "dialogue_count", "burstiness"):
        g = int(round(got if isinstance(got, (int, float)) else 0))
        if op == "in_range":
            lo, hi = int(val[0]), int(val[1])
            return lo <= g <= hi, g, [lo, hi]
        v = int(val)
        if op == "lte":
            return g <= v, g, v
        return g >= v, g, v
    if t in ("chat_opener", "chat_sycophancy", "chat_preachy", "chat_bullets",
             "chat_bold", "chat_para"):
        # chat 语言感一律用**阈值**判，不用一票否决。
        # 模型偶尔说一次「首先」不算毛病，整段都在说才是。所以默认 lte。
        g = int(got or 0)
        if op == "in_range":
            lo, hi = int(val[0]), int(val[1])
            return lo <= g <= hi, g, [lo, hi]
        v = int(val)
        if op == "gte":
            return g >= v, g, v
        return g <= v, g, v
    if t == "keeps_info":
        # 改写题专用：给出若干"必须保留的信息点"，每个点的任一别名出现即算保留。
        # 不判"改得好不好"（那没有规则解），只判信息有没有丢。
        groups = val if isinstance(val, list) else [val]
        text = m.get("contains", "")
        lost = []
        for g in groups:
            alts = g if isinstance(g, list) else [g]
            if not any(a in text for a in alts):
                lost.append(alts[0])
        keep = (len(groups) - len(lost)) / float(len(groups)) if groups else 1.0
        if op == "gte":
            return keep >= float(val if not isinstance(val, list) else 1.0), \
                round(keep, 3), ">=%s" % chk.get("min", 1.0)
        need = float(chk.get("min", 1.0))
        return keep >= need, round(keep, 3), need
    if t == "contains":
        # value 可以是字符串或字符串列表，全部必须出现
        need = val if isinstance(val, list) else [val]
        missing = [w for w in need if w not in m.get("contains", "")]
        return (not missing), (missing or "all present"), need
    if t == "not_contains":
        need = val if isinstance(val, list) else [val]
        hit = [w for w in need if w in m.get("contains", "")]
        return (not hit), (hit or "none"), need
    if t in ("han_count", "punct_kinds", "opening_repeat"):
        g = int(got or 0)
        if op == "in_range":
            lo, hi = int(val[0]), int(val[1])
            return lo <= g <= hi, g, [lo, hi]
        v = int(val)
        if op == "lte":
            return g <= v, g, v
        return g >= v, g, v
    return False, got, val


def judge_prose(entry, response):
    """判一题。correct = 全部 check 通过（与 D5 一致）；score = 通过率×100。"""
    txt = (response or "").strip()
    exp = entry.get("expect") or {}
    checks = exp.get("checks") or []
    if not txt:
        return {"correct": False, "score": 0.0, "reason": "empty reply",
                "detail": [], "no_han": True}
    # 回复里一个汉字都没有 = **没有作答**，不是"写得差"。
    #
    # 与 AGENTS.md 铁律 6 同源：连接失败/超时不算答错，因为模型没有作答的机会。
    # 这里同理 —— 中文创作题回一句英文 "I need more information"（实测 mock
    # 端点就是这样）会让所有形式指标归零，看上去像"这个模型文笔极差"，
    # 实际是它根本没写中文。标记 no_han 让上层能单独统计，而不是塞进分母。
    if han_count(txt) == 0:
        return {"correct": False, "score": 0.0,
                "reason": "no Chinese characters in reply (not judged as poor writing)",
                "detail": [], "no_han": True}
    m = measure(txt)
    detail = []
    passed = 0
    for chk in checks:
        ok, got, thr = _check_ok(chk, m)
        if ok:
            passed += 1
        detail.append({"type": chk.get("type"), "ok": bool(ok),
                       "value": got, "threshold": thr, "label": chk.get("label")})
    total = len(checks) or 1
    score = 100.0 * passed / total
    failed = [d["type"] for d in detail if not d["ok"]]
    reason = ("all %d passed" % total) if not failed else ("failed: " + ", ".join(failed[:4]))
    return {"correct": passed == len(checks) and bool(checks),
            "score": round(score, 1), "reason": reason, "detail": detail}


# ---------------- 汇总 ----------------

def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def summarize(records, sampling=None):
    """按 subcat 分档报中位数。

    只报总分等于把风格差异抹掉：两个模型可能通过率相同，一个是"写得短所以不重复"，
    另一个是"写得长但节奏平"。分项中位数才看得出区别。
    """
    # 三类要分开：连接失败（没机会作答）/ 没写汉字（同样没作答）/ 有效作答
    errored = [r for r in records if r.get("error")]
    no_han = [r for r in records if not r.get("error") and r.get("no_han")]
    valid = [r for r in records if not r.get("error") and not r.get("no_han")]
    correct = sum(1 for r in valid if r.get("correct"))
    acc = (correct / float(len(valid))) if valid else 0.0

    def agg(rs):
        ms = [r.get("metrics") or {} for r in rs]
        ms = [m for m in ms if m]
        if not ms:
            return {}
        return {
            "n": len(rs),
            "pass_rate": round(sum(1 for r in rs if r.get("correct")) / float(len(rs)), 4) if rs else 0.0,
            "median_distinct_2": round(_median([m.get("distinct_2") for m in ms]) or 0, 4),
            "median_ttr": round(_median([m.get("ttr") for m in ms]) or 0, 4),
            "median_cv_sentence_len": round(_median([m.get("cv_sentence_len") for m in ms]) or 0, 4),
            "median_sensory_cats": _median([len(m.get("sensory_cats") or []) for m in ms]),
            "median_turn_hits": _median([len(m.get("turn_hits") or []) for m in ms]),
            "median_dialogue_ratio": round(_median([m.get("dialogue_ratio") for m in ms]) or 0, 4),
            "median_punct_kinds": _median([m.get("punct_kinds") for m in ms]),
            "cliche_rate": round(
                sum(1 for m in ms if m.get("cliche_hits")) / float(len(ms)), 4),
            # chat 类的 AI 腔分项：中位数为 0 说明多数回答干净，
            # 大于 0 说明这批输出普遍带腔调 —— 比单看通过率有信息量
            "median_chat_sycophancy": _median([m.get("chat_sycophancy") for m in ms]),
            "median_chat_preachy": _median([m.get("chat_preachy") for m in ms]),
            "median_chat_bullets": _median([m.get("chat_bullets") for m in ms]),
        }

    by_sub = {}
    for r in valid:
        by_sub.setdefault(r.get("subcat") or "?", []).append(r)
    # 短篇/长篇分档（subcat 以 _long 结尾算长篇）
    # chat 单独一档：它和创作不是同一种能力，混在一起报会把差异抹平
    chat = [r for r in valid if (r.get("subcat") or "") == "chat"]
    creative = [r for r in valid if (r.get("subcat") or "") != "chat"]
    short = [r for r in creative if not (r.get("subcat") or "").endswith("_long")]
    long_ = [r for r in creative if (r.get("subcat") or "").endswith("_long")]

    return {
        "dim": "D13", "suite": "writing",
        "n": len(records), "correct": correct,
        "n_valid": len(valid), "n_errors": len(errored),
        "n_no_han": len(no_han),
        "no_han_ids": [r.get("id") for r in no_han],
        "accuracy": acc,
        "accuracy_with_errors": (correct / float(len(records))) if records else 0.0,
        "mean_score": round(sum(r.get("score") or 0 for r in valid) / float(len(valid)), 1) if valid else 0.0,
        # 采样参数必须随分数一起记录：实测 temp=0 与 temp=1.0 的 distinct_2
        # 分别是 0.984 与 1.000，不记录就没法跨模型比较。
        "sampling": sampling or {},
        "short": agg(short),
        "long": agg(long_),
        "chat": agg(chat),
        "creative": agg(creative),
        "by_subcat": {k: agg(v) for k, v in sorted(by_sub.items())},
    }
