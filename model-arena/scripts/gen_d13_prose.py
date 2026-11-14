# -*- coding: utf-8 -*-
"""gen_d13_prose.py - 生成 D13 文笔/剧情题库，并做双向验证。

为什么用生成器而不是手写 jsonl：
手改 jsonl 会在追加时覆盖已有行（AGENTS.md「加新题目的正确姿势」）。而且
--verify 能把"判分器坏了"变成可检测事件 —— 本项目 7 个缺陷里 7 个在判分/基础设施，
所以生成器必须自带双向验证，不能只验证"正例能过"。

用法：
    python scripts/gen_d13_prose.py --out datasets/full/D13_full.jsonl
    python scripts/gen_d13_prose.py --verify
"""
import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "runner"))

import d13_prose as D  # noqa: E402

DIM = "D13"

# ---------- 每题的 check 模板 ----------
#
# 铁律：**题面用自然语言提出的每条硬要求，都必须有对应 check**。
# 审计第一版时发现 7 处"要求了但没判"（至少 8 句、必须出现「铁轨」、
# 恰好一段对话、改写要保留信息…）—— 那样的题模型完全不按要求写也能拿 100 分。
# 所以下面每个 builder 都显式带上它题面里承诺的每一项。

# 标点门槛的依据（实测，不是拍的）：
# 同一模型跑 8 段真实中文散文，标点种类是 3,4,4,4,4,3,4,6 —— 中位数 4。
# 最初设成 6，结果 5 题全挂而文字质量并不差：那不是模型差，是门槛要求前 10%。
# 定 3 只拦"通篇只有句号"的极端单调，那才是这个指标真正想排除的。
MIN_PUNCT_FLOOR = 3


def core_checks(min_han, max_han, min_sensory, min_punct=MIN_PUNCT_FLOOR,
                min_cv=0.25, min_sent=None, max_sent=None,
                min_turns=0, extra=None):
    """所有题共用的一组形式判据。每个参数都对应题面里写过的一句要求。"""
    cs = [
        {"type": "cliche_hits", "op": "not_contains", "value": [], "label": "无套话/AI腔"},
        {"type": "han_count", "op": "in_range", "value": [min_han, max_han], "label": "字数区间"},
        {"type": "sensory_cats", "op": "coverage", "value": min_sensory, "label": "感官覆盖"},
        {"type": "cv_sentence_len", "op": "gte", "value": min_cv, "label": "句长有变化"},
        {"type": "distinct_2", "op": "gte", "value": 0.55, "label": "不复读(2-gram)"},
        {"type": "ttr", "op": "in_range", "value": [0.20, 0.65], "label": "词汇丰富度"},
        {"type": "punct_kinds", "op": "gte", "value": min_punct, "label": "标点不单调"},
    ]
    if min_sent is not None:
        cs.append({"type": "sentence_count", "op": "gte", "value": min_sent,
                   "label": "最少句数"})
    if max_sent is not None:
        cs.append({"type": "sentence_count", "op": "lte", "value": max_sent,
                   "label": "最多句数"})
    if min_turns:
        cs.append({"type": "turn_hits", "op": "gte", "value": min_turns,
                   "label": "有剧情推进"})
    for c in (extra or []):
        cs.append(c)
    return cs


def q(qid, subcat, user, checks, notes, length="short"):
    return {"id": qid, "dim": DIM, "source": "selfmade", "subcat": subcat,
            "length": length, "user": user, "expect": {"checks": checks}, "notes": notes}


def build():
    """24 题：短篇 18 + 长篇 6，覆盖 5 个 subcat。"""
    rows = []

    # ============ prose 散文/景物（短篇 300-800，感官>=2）============
    prose_specs = [
        ("雨后的旧菜市场", "清晨刚收摊的市场", 3),
        ("夜班末班车", "深夜公交车厢里的最后几个乘客", 3),
        ("雪后的巷子", "一场大雪之后的窄巷", 2),
        ("夏天的河堤", "傍晚时分堤岸上的光景", 3),
    ]
    for i, (title, scene, sens) in enumerate(prose_specs, 1):
        rows.append(q(
            "D13_prose_%04d" % i, "prose",
            "写一段 300-800 字的中文景物描写，主题是「%s」（%s）。要求："
            "①至少覆盖 %d 类感官（视觉/听觉/嗅觉/触觉/味觉）；"
            "②句长要有明显变化，不要整段都是一个长度；"
            "③不要写总结陈词，不要用「总之」「首先」「这让我」这类词；"
            "④标点要有多样性，至少 3 种。"
            % (title, scene, sens),
            core_checks(300, 800, sens, min_punct=3, min_cv=0.30),
            "景物题：考感官覆盖与句长节奏；套话命中即失败"))

    # ============ narrative 短篇叙事（必须有转折 + 至少 8 句）============
    narr_specs = [
        ("一次误会", "两个人因为一件小事产生误会，最后解开", 2),
        ("失约", "约好见面却没能赴约", 2),
        ("钥匙", "一把找不到的钥匙引出的故事", 2),
        ("最后一班渡船", "赶到码头时船正要开", 3),
    ]
    for i, (title, _d, sens) in enumerate(narr_specs, 1):
        rows.append(q(
            "D13_narrative_%04d" % i, "narrative",
            "写一个 400-800 字的中文短篇故事，主题是「%s」。要求："
            "①必须有明确的转折或意外；②至少 %d 类感官细节；"
            "③句长有变化；④不许用「总之」「首先」「这让我」「不得不说」这类套话；"
            "⑤至少 8 个句子。" % (title, sens),
            core_checks(400, 800, sens, min_punct=3, min_cv=0.28, min_sent=8,
                        min_turns=2),
            "叙事题：转折词命中数作「有推进」的形式代理；句数下限对应题面的「至少 8 句」"))

    # ============ dialogue 对话（对话占比 >= 25%）============
    dlg_specs = [
        ("深夜便利店", "店员和顾客的对话", 2),
        ("争吵的父女", "两代人的冲突", 2),
        ("诊室门外", "两个家属在走廊里的低声交谈", 2),
    ]
    for i, (title, _d, sens) in enumerate(dlg_specs, 1):
        rows.append(q(
            "D13_dialogue_%04d" % i, "dialogue",
            "写一段 350-800 字的中文对话场景：%s。要求："
            "①对话部分（引号内）至少占总字数的 25%%；"
            "②用「」或“”标注对话；③至少 %d 类感官描写；"
            "④不许用「总之」「首先」这类总结词。" % (title, sens),
            core_checks(350, 800, sens, min_punct=3, min_cv=0.25,
                        extra=[{"type": "dialogue_ratio", "op": "gte", "value": 0.25,
                                "label": "对话占比"}]),
            "对话题：dialogue_ratio 是形式代理，不评台词好坏"))

    # ============ rewrite 改写润色（保留信息 + 节奏）============
    flat = ("那是一间很小的屋子。屋子里面有一张桌子。桌子上放着一盏灯。"
            "灯是旧的。灯上面有很多灰。屋子里还有一个窗户。窗户外面是一条街。"
            "街上没有什么人。天色已经很晚了。")
    rows.append(q(
        "D13_rewrite_0001", "rewrite",
        "下面这段文字平铺直叙、节奏单调：\n\n%s\n\n"
        "请改写它，要求：①保留原有的全部信息（屋子小、桌子、灯是旧的且有灰、"
        "窗户、窗外有街、街上没人、天色已晚）；"
        "②字数 250-600；③句长要有变化；④加入至少 3 类感官细节；"
        "⑤不要用「总之」「首先」「这让我」这类套话。" % flat,
        core_checks(250, 600, 3, min_punct=3, min_cv=0.30,
                    extra=[{"type": "keeps_info", "op": "gte",
                            "min": 1.0, "label": "信息全部保留",
                            "value": [["屋子", "房间"], ["桌"], ["灯"],
                                      ["灰", "灰尘"], ["窗"], ["街"],
                                      ["没人", "没有一个人", "空无一人", "无人"],
                                      ["天色", "很晚", "夜深", "天黑"]]}]),
        "改写题：keeps_info 逐点核对信息有没有丢，不评改得好不好看"))

    # ============ constraint 硬约束创作 ============
    rows.append(q(
        "D13_constraint_0001", "constraint",
        "写一段 200-400 字的中文片段，必须：①出现「铁轨」这个意象；"
        "②恰好包含一段对话（用「」标注）；③至少 2 类感官；"
        "④不得出现「首先」「其次」「总之」「作为一个AI」。",
        core_checks(200, 400, 2, min_punct=3, min_cv=0.28,
                    extra=[{"type": "contains", "op": "contains",
                            "value": ["铁轨"], "label": "含指定意象"},
                           {"type": "dialogue_count", "op": "in_range",
                            "value": [1, 1], "label": "恰好一段对话"},
                           {"type": "not_contains", "op": "not_contains",
                            "value": ["首先", "其次", "作为一个AI"],
                            "label": "禁用词"}]),
        "硬约束题：意象、对话段数、禁用词三项都必须真的判到"))

    rows.append(q(
        "D13_constraint_0002", "constraint",
        "写一段 300-500 字的中文场景：深夜的医院走廊。要求："
        "①至少 4 类感官（视觉/听觉/嗅觉/触觉/味觉中选 4）；"
        "②句长变化明显；③不得出现任何总结性套话（总之/综上/不得不说/值得注意的是）；"
        "④标点种类至少 5 种。",
        core_checks(300, 500, 4, min_punct=5, min_cv=0.32),
        "硬约束题：高感官覆盖 + 高标准点多样性"))

    rows.append(q(
        "D13_prose_0005", "prose",
        "写一段 350-700 字的中文景物描写，主题是「老屋的天井」"
        "（午后阳光落在长了青苔的砖地上）。要求：①至少覆盖 3 类感官；"
        "②句长要有明显变化；③不要写总结陈词，不许用「总之」「首先」这类词；"
        "④标点至少 3 种。",
        core_checks(350, 700, 3, min_punct=3, min_cv=0.30),
        "景物题：室内光线的感官层次"))

    rows.append(q(
        "D13_narrative_0005", "narrative",
        "写一个 450-800 字的中文短篇故事，主题是「旧照片背后的一行字」。要求："
        "①必须有明确的转折或意外；②至少 2 类感官细节；③句长有变化；"
        "④不许用「总之」「首先」「这让我」这类套话；⑤至少 9 个句子。",
        core_checks(450, 800, 2, min_punct=3, min_cv=0.28, min_sent=9,
                    min_turns=2),
        "叙事题：由物件引出的发现式转折"))

    rows.append(q(
        "D13_dialogue_0004", "dialogue",
        "写一段 400-800 字的中文对话场景：雨夜，便利店门口，两个陌生人借一把伞。"
        "要求：①对话部分（引号内）至少占总字数的 25%；②用「」或“”标注对话；"
        "③至少 2 类感官描写；④不许用「总之」「首先」这类总结词。",
        core_checks(400, 800, 2, min_punct=3, min_cv=0.25,
                    extra=[{"type": "dialogue_ratio", "op": "gte", "value": 0.25,
                            "label": "对话占比"}]),
        "对话题：借伞的小场景"))

    rows.append(q(
        "D13_constraint_0003", "constraint",
        "写一段 250-500 字的中文片段，必须：①第二人称「你」开头；"
        "②出现「钥匙」这个物件；③含至少 2 类感官；④不得出现「总之」「首先」。",
        core_checks(250, 500, 2, min_punct=3, min_cv=0.28,
                    extra=[{"type": "contains", "op": "contains",
                            "value": ["钥匙"], "label": "含指定物件"},
                           {"type": "not_contains", "op": "not_contains",
                            "value": ["总之", "首先"], "label": "禁用词"}]),
        "硬约束题：物件必须出现"))
    rows[-1]["user_head"] = "你"   # 供 verify 检查第二人称，判分侧暂不强制

    # ============ chat 日常对话（反 AI 腔）============
    #
    # 设计要点：题目必须**诱导**模型犯 AI 腔，否则测不出来。
    # 实测扫 34320 条真实回答，AI 腔只在特定问法下才大量出现，
    # 技术题几乎不触发 —— 所以这里刻意选了会被 AI 腔污染的场景：
    # 简单操作、情绪倾诉、简短事实、吐槽请求。
    #
    # 判据一律用阈值（lte），不用一票否决：模型偶尔一个「首先」不是毛病，
    # 整段都在分点说教才是。这与创作题 cliche_hits 的一票否决口径不同，是故意的。

    chat_specs = [
        ("D13_chat_0001",
         "我手机上的微信怎么删掉？",
         "极简操作问；AI 腔典型表现是「当然可以！以下是步骤 1.2.3.」",
         40),
        ("D13_chat_0002",
         "烦死了，今天又被领导骂了。",
         "情绪倾诉；AI 腔典型表现是「我理解你的感受，希望这些建议能帮到你」+ 分点建议",
         120),
        ("D13_chat_0003",
         "北京现在几点？",
         "一句话事实问；AI 腔典型表现是「这是一个好问题，需要注意时区…」",
         60),
        ("D13_chat_0004",
         "帮我写个 Python 函数算斐波那契。",
         "技术但要求简短；AI 腔典型表现是先声明「当然可以」再列 1.2.3. 讲解",
         200),
        ("D13_chat_0005",
         "这个红色和蓝色哪个好看？",
         "主观选择问；AI 腔典型表现是「这取决于多个因素」+ 分点权衡",
         120),
        ("D13_chat_0006",
         "嗯。",
         "最短回应；测是否会过度解读、硬凑一段安慰",
         60),
    ]
    for qid, user, note, max_han in chat_specs:
        rows.append(q(
            qid, "chat", user,
            [
                {"type": "chat_sycophancy", "op": "lte", "value": 0,
                 "label": "不奉承"},
                {"type": "chat_preachy", "op": "lte", "value": 0,
                 "label": "不说教"},
                {"type": "chat_bullets", "op": "lte", "value": 0,
                 "label": "不分点"},
                {"type": "chat_bold", "op": "lte", "value": 0,
                 "label": "不加粗"},
                {"type": "han_count", "op": "lte", "value": max_han,
                 "label": "长度配得上问题"},
            ],
            note,
        ))

    # 反例库：这些是 chat 类要抓的典型坏样子，--verify 逐条验证判不过
    rows.append(q(
        "D13_chat_0007", "chat",
        "今天下雨了。",
        [
            {"type": "chat_sycophancy", "op": "lte", "value": 0, "label": "不奉承"},
            {"type": "chat_preachy", "op": "lte", "value": 0, "label": "不说教"},
            {"type": "chat_bullets", "op": "lte", "value": 0, "label": "不分点"},
            {"type": "han_count", "op": "lte", "value": 80,
             "label": "长度配得上问题"},
        ],
        "陈述句；测会不会硬凑建议",
    ))

    # ============ 长篇（_long 后缀）============
    long_specs = [
        ("D13_prose_long_0001", "prose_long",
         "写一篇 2000-4000 字的中文散文，主题是「一座城市的四季」。要求："
         "①至少覆盖 4 类感官；②句长变化明显；"
         "③不许用「总之」「首先」「这让我」「不得不说」等套话；"
         "④至少 40 个句子；⑤不许用「以上就是」收尾。",
         core_checks(2000, 4000, 4, min_punct=5, min_cv=0.32, min_sent=40)),
        ("D13_narrative_long_0001", "narrative_long",
         "写一篇 2500-4000 字的中文短篇小说，主题是「一封寄错地址的信」。要求："
         "①至少三次转折；②至少 3 类感官；③句长有变化；"
         "④至少 50 个句子；⑤不许用套话总结。",
         core_checks(2500, 4000, 3, min_punct=3, min_cv=0.30, min_sent=50,
                     min_turns=3)),
        ("D13_dialogue_long_0001", "dialogue_long",
         "写一段 2000-3500 字的中文长对话场景：「一场迟到了十年的会面」。要求："
         "①对话（引号内）占比至少 30%；②至少 2 类感官；"
         "③句长有变化；④不许用套话。",
         core_checks(2000, 3500, 2, min_punct=3, min_cv=0.28,
                     extra=[{"type": "dialogue_ratio", "op": "gte", "value": 0.30,
                             "label": "对话占比"}])),
        ("D13_prose_long_0002", "prose_long",
         "写一篇 2000-3500 字的中文散文，主题是「夜里的海」。要求："
         "①至少覆盖 3 类感官；②句长变化明显；③至少 35 个句子；"
         "④不许用「总之」「首先」这类套话；⑤标点至少 4 种。",
         core_checks(2000, 3500, 3, min_punct=4, min_cv=0.30, min_sent=35)),
        ("D13_narrative_long_0002", "narrative_long",
         "写一篇 2200-3500 字的中文短篇小说，主题是「最后一班地铁」。要求："
         "①至少两次转折；②至少 2 类感官；③句长有变化；"
         "④至少 45 个句子；⑤不许用套话总结。",
         core_checks(2200, 3500, 2, min_punct=3, min_cv=0.28, min_sent=45,
                     min_turns=2)),
        ("D13_constraint_long_0001", "constraint_long",
         "写一篇 2000-3500 字的中文叙事，必须：①出现「灯塔」这个意象；"
         "②至少 3 类感官；③至少 40 个句子；④不得出现「总之」「首先」「作为一个AI」。",
         core_checks(2000, 3500, 3, min_punct=3, min_cv=0.28, min_sent=40,
                     extra=[{"type": "contains", "op": "contains",
                             "value": ["灯塔"], "label": "含指定意象"},
                            {"type": "not_contains", "op": "not_contains",
                             "value": ["总之", "首先", "作为一个AI"],
                             "label": "禁用词"}])),
    ]
    for qid, sub, user, cs in long_specs:
        rows.append(q(qid, sub, user, cs,
                      "长篇：检验长度拉长后 distinct_2 不塌陷、节奏不消失",
                      length="long"))

    return rows


# ---------- 双向验证 ----------

GOOD_SAMPLE = chr(10).join([
    "雨停了——菜市场的塑料棚还在滴水。棚布被晨光打成半透明的橘色；"
    "水珠顺着褶皱滚下来，砸在铁皮秤盘上，一声一声地脆响。"
    "鱼摊的腥气混着泥土的潮味涌上来，压得人下意识屏住呼吸。",
    "",
    "老陈蹲在湿漉漉的水泥地上收摊。他的手指冻得发僵——捏着尼龙绳打了两遍结，"
    "才系住。隔壁卖卤味的女人把最后半盆卤汤倒进桶里，孜然和炭火的味道随之淡下去。"
    "远处有人在推车，轮子碾过积水，溅起一片扇形的水花：那声音闷闷的，像谁在叹气。",
    "",
    "「今天就这样吧？」隔壁女人收拾着摊子问。",
    "",
    "「嗯。」老陈应了一声，没抬头。",
    "",
    "他抬头看了一眼天：云层裂开一道缝，光斜斜地切进棚子，照在秤杆上。"
    "那一刻他忽然觉得，今天摔碎的那个碗，好像也没那么要紧。",
])

BAD_CASES = {
    "空回答": "",
    "只有客套话": "好的，以下是。",
    "复读机": "他走进屋子。" * 5,
    "套话堆砌": ("总之，这段描写很重要。首先，我们要注意场景。其次，人物的心情要写好。"
                 "这让我想到了很多东西。综上所述，以上就是全部内容。"),
    "AI腔": ("作为一个AI，我无法真正感受雨后的空气。不过我可以用文字描述它。"
             "总之，希望你能够理解我的局限。首先，我要说明这一点。"),
    "跑题写代码": chr(10).join([
        "def main():",
        "    data = []",
        "    for i in range(10):",
        "        data.append(i * 2)",
        "    return data",
        "",
        "这段代码的时间复杂度是 O(n)，空间复杂度也是 O(n)。",
    ]),
    "字数不达标": "雨停了。老陈收摊。天色亮了。",
}

# 判分器支持的判据类型。题面里提出的每条硬要求都必须落在其中之一，
# 否则那道题的"要求"等于没判 —— 审计第一版时正是这样漏了 7 处。
SUPPORTED_CHECKS = {
    "cliche_hits", "sensory_cats", "turn_hits", "distinct_2", "distinct_3",
    "ttr", "cv_sentence_len", "dialogue_ratio", "dialogue_count", "han_count",
    "punct_kinds", "opening_repeat", "sentence_count", "contains",
    "not_contains", "keeps_info",
    # chat 语言感（实测词表来自 34320 条真实回答，见 d13_prose.py）
    "chat_opener", "chat_sycophancy", "chat_preachy", "chat_bullets",
    "chat_bold", "chat_para",
}


def audit_coverage(rows):
    """题面写了的硬要求，判据里到底有没有对应的 check。

    这是本次完善的核心断言：一版 D13 有 7 处"题面要求了但判分器没判"
    （至少 8 句 / 必须出现「铁轨」/ 恰好一段对话 / 改写保留信息…），
    那样的题模型完全不按要求写也能拿 100 分。把这项检查做进 --verify，
    以后再加题就漏不掉。
    """
    problems = []
    for r in rows:
        user = r["user"]
        types = {c["type"] for c in r["expect"]["checks"]}
        for t in types:
            if t not in SUPPORTED_CHECKS:
                problems.append("%s: 判据类型 %s 判分器不支持" % (r["id"], t))
        # 题面点名要出现的字/词
        for m in re.finditer(r"必须(?:出现|包含)「([^」]+)」", user):
            if "contains" not in types:
                problems.append("%s: 要求含「%s」但没有 contains" % (r["id"], m.group(1)))
        # 题面要求禁用词
        if re.search(r"不得出现|不许用", user) and "cliche_hits" not in types:
            problems.append("%s: 题面禁用了词但没有 cliche_hits" % r["id"])
        # 句数
        for m in re.finditer(r"至少\s*(\d+)\s*个句子", user):
            if "sentence_count" not in types:
                problems.append("%s: 要求至少 %s 句但没有 sentence_count"
                                % (r["id"], m.group(1)))
        # 对话段数
        if "恰好包含" in user and "对话" in user and "dialogue_count" not in types:
            problems.append("%s: 要求恰好一段对话但没有 dialogue_count" % r["id"])
        # 对话占比
        if re.search(r"对话.{0,6}至少.{0,8}\d+\s*%", user) and "dialogue_ratio" not in types:
            problems.append("%s: 要求对话占比但没有 dialogue_ratio" % r["id"])
        # 改写信息保留
        if "改写" in user and "保留" in user and "keeps_info" not in types:
            problems.append("%s: 改写题要求保留信息但没有 keeps_info" % r["id"])
        # 感官
        if re.search(r"至少\s*\d+\s*类感官", user) and "sensory_cats" not in types:
            problems.append("%s: 要求感官但没有 sensory_cats" % r["id"])
        # 标点
        if re.search(r"标点.{0,6}(至少|多样性)", user) and "punct_kinds" not in types:
            problems.append("%s: 要求标点多样性但没有 punct_kinds" % r["id"])
    return problems


def verify(path=None):
    rows = build() if path is None else [
        json.loads(l) for l in io.open(path, encoding="utf-8") if l.strip()]
    ok = True

    print("=" * 64)
    print(" D13 双向验证")
    print("=" * 64)
    print("题库：%d 题" % len(rows))
    subs, lens = {}, {}
    for r in rows:
        subs[r["subcat"]] = subs.get(r["subcat"], 0) + 1
        lens[r["length"]] = lens.get(r["length"], 0) + 1
    for k in sorted(subs):
        print("   %-20s %d" % (k, subs[k]))
    print("   短篇 %d / 长篇 %d" % (lens.get("short", 0), lens.get("long", 0)))
    print()

    # --- 判据覆盖率自检（本次完善的核心）---
    print("[覆盖] 题面的每条硬要求都要有对应判据")
    problems = audit_coverage(rows)
    if problems:
        for p_ in problems:
            print("   FAIL " + p_)
        ok = False
    else:
        print("   PASS %d 题的题面要求全部有对应判据" % len(rows))
    print()

    # --- 正例 ---
    print("[正例] 一段形式合格的中文景物描写必须判过")
    loose = {"id": "verify_loose", "subcat": "prose", "expect": {"checks": [
        {"type": "cliche_hits", "op": "not_contains", "value": []},
        {"type": "sensory_cats", "op": "coverage", "value": 3},
        {"type": "cv_sentence_len", "op": "gte", "value": 0.25},
        {"type": "distinct_2", "op": "gte", "value": 0.55},
        {"type": "han_count", "op": "gte", "value": 200},
        {"type": "punct_kinds", "op": "gte", "value": 3},
        {"type": "sentence_count", "op": "gte", "value": 8},
        {"type": "dialogue_count", "op": "gte", "value": 1},
    ]}}
    m = D.measure(GOOD_SAMPLE)
    v = D.judge_prose(loose, GOOD_SAMPLE)
    print("   感官=%d类 句数=%d CV=%.3f distinct_2=%.3f 汉字=%d 标点=%d 对话段=%d 套话=%s"
          % (len(m["sensory_cats"]), m["sentence_count"], m["cv_sentence_len"],
             m["distinct_2"], m["han_count"], m["punct_kinds"],
             m["dialogue_count"], m["cliche_hits"]))
    if v["correct"]:
        print("   PASS 正例判过（score=%.0f）" % v["score"])
    else:
        print("   FAIL 正例判不过 -> %s" % v["reason"])
        print("        %s" % json.dumps([d for d in v["detail"] if not d["ok"]],
                                         ensure_ascii=False)[:300])
        ok = False
    print()

    # --- 反例 ---
    print("[反例] 每一条都必须判不过")
    for name, text in BAD_CASES.items():
        v = D.judge_prose(loose, text)
        if v["correct"]:
            print("   FAIL 「%s」竟然判过了（score=%.0f）" % (name, v["score"]))
            ok = False
        else:
            print("   PASS 「%s」判不过 -> %s" % (name, v["reason"][:50]))
    print()

    # --- chat 语言感：AI 腔必须被抓 ---
    print("[chat] 日常对话的 AI 腔必须判不过")
    chat_entry = {"expect": {"checks": [
        {"type": "chat_sycophancy", "op": "lte", "value": 0},
        {"type": "chat_preachy", "op": "lte", "value": 0},
        {"type": "chat_bullets", "op": "lte", "value": 0},
        {"type": "han_count", "op": "lte", "value": 60},
    ]}}
    chat_cases = [
        ("好回答", "不需要，直接删掉就行。", True),
        ("奉承开场", "当然可以！这是一个好问题。", False),
        ("说教总结", "首先你需要打开设置，其次找到账户，总之就是这样。", False),
        ("分点罗列", "1. 打开设置\n2. 找到账户\n3. 点击删除", False),
        ("希望帮到你", "希望这些能帮到你，随时告诉我。", False),
        ("过度展开", "这个问题需要从多个角度来分析，" + "很" * 80, False),
    ]
    for name, text, want_pass in chat_cases:
        v = D.judge_prose(chat_entry, text)
        got = v["correct"]
        if got == want_pass:
            print("   PASS 「%s」%s" % (name, "判过" if want_pass else "判不过"))
        else:
            print("   FAIL 「%s」期望%s，实际%s（%s）"
                  % (name, "判过" if want_pass else "判不过",
                     "判过" if got else "判不过", v["reason"]))
            ok = False
    print()

    # --- 关键回归：题面要求的"漏判"曾经让不合格答案满分 ---
    print("[回归] 缺句数/缺意象的答案必须被判错")
    cases = [
        ("没写够句数", {"expect": {"checks": [
            {"type": "sentence_count", "op": "gte", "value": 8}]}}, "短句。"),
        ("没写指定意象", {"expect": {"checks": [
            {"type": "contains", "op": "contains", "value": ["铁轨"]}]}},
         "他沿着铁道走，风很大。"),
        ("多写了对话", {"expect": {"checks": [
            {"type": "dialogue_count", "op": "in_range", "value": [1, 1]}]}},
         "「走吧。」「好。」"),
        ("改写丢信息", {"expect": {"checks": [
            {"type": "keeps_info", "op": "gte", "min": 1.0,
             "value": [["屋子"], ["桌"], ["灯"]]}]}},
         "房间里有一张桌子。"),
    ]
    for name, e, text in cases:
        v = D.judge_prose(e, text)
        if v["correct"]:
            print("   FAIL 「%s」应当判错却判过了" % name)
            ok = False
        else:
            print("   PASS 「%s」判错" % name)
    print()
    print("=" * 64)
    print(" %s" % ("全部通过" if ok else "存在失败项"))
    print("=" * 64)
    return 0 if ok else 1




def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "datasets", "full", "D13_full.jsonl"))
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()

    if a.verify:
        src = a.out if os.path.isfile(a.out) else None
        return verify(src)

    rows = build()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with io.open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("wrote %d rows -> %s" % (len(rows), os.path.relpath(a.out, ROOT)))
    subs = {}
    for r in rows:
        subs[r["subcat"]] = subs.get(r["subcat"], 0) + 1
    for k in sorted(subs):
        print("   %-18s %d" % (k, subs[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())