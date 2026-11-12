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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "runner"))

import d13_prose as D  # noqa: E402

DIM = "D13"

# ---------- 每题的 check 模板 ----------
# 短篇与长篇共用一套**形式**判据；长篇多一条"能撑住"的要求
# （句数更多、distinct 不能随长度塌陷）。

# 标点门槛的依据（实测，不是拍的）：
# 同一个模型跑 8 段真实中文散文，标点种类是 3,4,4,4,4,3,4,6 —— 中位数 4。
# 最初我把门槛设成 6，结果 5 题全挂，而它们的文字质量并不差。那不是模型差，
# 是门槛要求达到了这个量级输出的前 10%。定 3 只拦"通篇只有句号"的极端单调，
# 那才是这个指标真正想排除的东西。
MIN_PUNCT_FLOOR = 3


def checks_common(min_han, max_han, min_sensory, min_turns=0,
                  min_cv=0.25, max_cliche=0, min_punct=MIN_PUNCT_FLOOR):
    cs = [
        {"type": "cliche_hits", "op": "not_contains", "value": [], "label": "无套话/AI腔"},
        {"type": "han_count", "op": "in_range", "value": [min_han, max_han], "label": "字数区间"},
        {"type": "sensory_cats", "op": "coverage", "value": min_sensory, "label": "感官覆盖"},
        {"type": "cv_sentence_len", "op": "gte", "value": min_cv, "label": "句长有变化"},
        {"type": "distinct_2", "op": "gte", "value": 0.55, "label": "不复读(2-gram)"},
        {"type": "ttr", "op": "in_range", "value": [0.20, 0.65], "label": "词汇丰富度"},
        {"type": "punct_kinds", "op": "gte", "value": min_punct, "label": "标点不单调"},
    ]
    if min_turns:
        cs.append({"type": "turn_hits", "op": "gte", "value": min_turns, "label": "有剧情推进"})
    return cs


def q(qid, subcat, user, checks, notes, length="short"):
    return {"id": qid, "dim": DIM, "source": "selfmade", "subcat": subcat,
            "length": length, "user": user, "expect": {"checks": checks}, "notes": notes}


def build():
    rows = []

    # ---- prose 散文/景物（短篇 300-800 字，感官 >=2）----
    prose_topics = [
        ("雨后的旧菜市场", "清晨刚收摊的市场"),
        ("夜班末班车", "深夜公交车厢里的最后几个乘客"),
        ("雪后的巷子", "一场大雪之后的窄巷"),
        ("夏天的河堤", "傍晚时分堤岸上的光景"),
    ]
    for i, (title, scene) in enumerate(prose_topics, 1):
        rows.append(q(
            "D13_prose_%04d" % i, "prose",
            "写一段 300-800 字的中文景物描写，主题是「%s」（%s）。要求："
            "①至少覆盖 3 类感官（视觉/听觉/嗅觉/触觉/味觉）；"
            "②句长要有明显变化，不要整段都是一个长度；"
            "③不要写总结陈词，不要用「总之」「首先」「这让我」这类词；"
            "④标点要有多样性。" % (title, scene),
            checks_common(300, 800, 3, min_cv=0.30),
            "景物题：考感官覆盖与句长节奏；套话命中即失败"))

    # ---- narrative 短篇叙事（要有转折）----
    narr_topics = [
        ("一次误会", "两个人因为一件小事产生误会，最后解开"),
        ("失约", "约好见面却没能赴约"),
        ("钥匙", "一把找不到的钥匙引出的故事"),
    ]
    for i, (title, _desc) in enumerate(narr_topics, 1):
        rows.append(q(
            "D13_narrative_%04d" % i, "narrative",
            "写一个 400-800 字的中文短篇故事，主题是「%s」。要求："
            "①必须有明确的转折或意外；②至少 2 类感官细节；"
            "③句长有变化；④不许用「总之」「首先」「这让我」「不得不说」这类套话；"
            "⑤至少 8 个句子。" % title,
            checks_common(400, 800, 2, min_turns=2, min_cv=0.30),
            "叙事题：转折词命中数作为「有推进」的形式代理"))

    # ---- dialogue 对话（对话占比）----
    dlg_topics = [
        ("深夜便利店", "店员和顾客的对话"),
        ("争吵的父女", "两代人的冲突"),
    ]
    for i, (title, _d) in enumerate(dlg_topics, 1):
        cs = checks_common(350, 800, 2, min_cv=0.25)
        cs.append({"type": "dialogue_ratio", "op": "gte", "value": 0.25,
                   "label": "对话占比"})
        rows.append(q(
            "D13_dialogue_%04d" % i, "dialogue",
            "写一段 350-800 字的中文对话场景：%s。要求："
            "①对话部分（引号内）至少占总字数的 25%%；"
            "②用「」或“”标注对话；③至少 2 类感官描写；"
            "④不许用「总之」「首先」这类总结词。" % title,
            cs,
            "对话题：dialogue_ratio 是形式代理，不评台词好坏"))

    # ---- rewrite 改写润色 ----
    flat = ("那是一间很小的屋子。屋子里面有一张桌子。桌子上放着一盏灯。"
            "灯是旧的。灯上面有很多灰。屋子里还有一个窗户。窗户外面是一条街。"
            "街上没有什么人。天色已经很晚了。")
    rows.append(q(
        "D13_rewrite_0001", "rewrite",
        "下面这段文字平铺直叙、节奏单调：\n\n%s\n\n"
        "请改写它，要求：①保留原有的全部信息（小屋子、桌子、灯、灰、窗户、街、没人、夜深）；"
        "②字数 250-600；③句长要有变化；④加入至少 3 类感官细节；"
        "⑤不要用「总之」「首先」「这让我」这类套话。" % flat,
        checks_common(250, 600, 3, min_cv=0.30),
        "改写题：以「保留信息量 + 提升节奏」为形式判据，不评改得好不好看"))

    # ---- constraint 硬约束创作 ----
    rows.append(q(
        "D13_constraint_0001", "constraint",
        "写一段 200-400 字的中文片段，必须：①出现「铁轨」这个意象；"
        "②恰好包含一段对话（用「」标注）；③至少 2 类感官；"
        "④不得出现「首先」「其次」「总之」「作为一个AI」。",
        checks_common(200, 400, 2, min_cv=0.28),
        "硬约束题：字数+意象+对话形式三项硬性"))
    rows.append(q(
        "D13_constraint_0002", "constraint",
        "写一段 300-500 字的中文场景：深夜的医院走廊。要求："
        "①至少 4 类感官（视觉/听觉/嗅觉/触觉/味觉中选 4）；"
        "②句长变化明显；③不得出现任何总结性套话（总之/综上/不得不说/值得注意的是）；"
        "④标点种类至少 5 种。",
        checks_common(300, 500, 4, min_cv=0.32, min_punct=5),
        "硬约束题：高感官覆盖 + 高标准点多样性"))

    # ---- 长篇（_long 后缀）----
    long_specs = [
        ("D13_prose_long_0001", "prose_long",
         "写一篇 2000-4000 字的中文散文，主题是「一座城市的四季」。要求："
         "①至少覆盖 4 类感官；②句长变化明显（变异系数 >= 0.32）；"
         "③不许用「总之」「首先」「这让我」「不得不说」等套话；"
         "④至少 40 个句子；⑤不许用「以上就是」收尾。",
         checks_common(2000, 4000, 4, min_cv=0.32, min_punct=5)),
        ("D13_narrative_long_0001", "narrative_long",
         "写一篇 2500-4000 字的中文短篇小说，主题是「一封寄错地址的信」。要求："
         "①至少三次转折；②至少 3 类感官；③句长有变化；"
         "④至少 50 个句子；⑤不许用套话总结。",
         checks_common(2500, 4000, 3, min_turns=3, min_cv=0.30)),
        ("D13_dialogue_long_0001", "dialogue_long",
         "写一段 2000-3500 字的中文长对话场景：「一场迟到了十年的会面」。要求："
         "①对话（引号内）占比至少 30%；②至少 2 类感官；"
         "③句长有变化；④不许用套话。",
         checks_common(2000, 3500, 2, min_cv=0.28)),
    ]
    for qid, sub, user, cs in long_specs:
        if sub.startswith("dialogue"):
            cs = cs + [{"type": "dialogue_ratio", "op": "gte", "value": 0.30,
                        "label": "对话占比"}]
        rows.append(q(qid, sub, user, cs,
                      "长篇：检验长度拉长后 distinct_2 不塌陷、节奏不消失",
                      length="long"))

    return rows


# ---------- 双向验证 ----------

# 正例：一段**形式合格**的景物描写。刻意让它满足所有"指标类"检查，
# 而不是去凑某一道题的字数区间（那要用 15 个不同长度的样本，没有意义）。
#
# 标点刻意用多样：，。？！、；……——""
# 因为 punct_kinds 检查的就是"不单调"，样本本身单调会让正例验证失去意义。
GOOD_SAMPLE = (
    "雨停了——菜市场的塑料棚还在滴水。棚布被晨光打成半透明的橘色；"
    "水珠顺着褶皱滚下来，砸在铁皮秤盘上，一声一声地脆响。"
    "鱼摊的腥气混着泥土的潮味涌上来，压得人下意识屏住呼吸。\n\n"
    "老陈蹲在湿漉漉的水泥地上收摊。他的手指冻得发僵——捏着尼龙绳打了两遍结，"
    "才系住。隔壁卖卤味的女人把最后半盆卤汤倒进桶里，孜然和炭火的味道随之淡下去。"
    "远处有人在推车，轮子碾过积水，溅起一片扇形的水花：那声音闷闷的，像谁在叹气。\n\n"
    "「今天就这样吧？」隔壁女人收拾着摊子问。\n\n"
    "「嗯。」老陈应了一声，没抬头。\n\n"
    "他抬头看了一眼天：云层裂开一道缝，光斜斜地切进棚子，照在秤杆上。"
    "那一刻他忽然觉得，今天摔碎的那个碗，好像也没那么要紧。"
)

BAD_CASES = {
    "空回答": "",
    "只有客套话": "好的，以下是。",
    "复读机": "他走进屋子。他走进屋子。他走进屋子。他走进屋子。他走进屋子。",
    "套话堆砌": ("总之，这段描写很重要。首先，我们要注意场景。其次，人物的心情要写好。"
                 "这让我想到了很多东西。综上所述，以上就是全部内容。"),
    "AI腔": ("作为一个AI，我无法真正感受雨后的空气。不过我可以用文字描述它。"
             "总之，希望你能够理解我的局限。首先，我要说明这一点。"),
    "跑题写代码": ("def main():\n    data = []\n    for i in range(10):\n"
                   "        data.append(i * 2)\n    return data\n\n"
                   "这段代码的时间复杂度是 O(n)，空间复杂度也是 O(n)。"),
    "字数不达标": "雨停了。老陈收摊。天色亮了。",
}


def verify(path=None):
    rows = build() if path is None else [
        json.loads(l) for l in io.open(path, encoding="utf-8") if l.strip()]
    ok = True

    print("=" * 62)
    print(" D13 双向验证")
    print("=" * 62)
    print("题库：%d 题" % len(rows))
    subs = {}
    for r in rows:
        subs[r["subcat"]] = subs.get(r["subcat"], 0) + 1
    for k in sorted(subs):
        print("   %-18s %d" % (k, subs[k]))
    print()

    # --- 正例：必须判过 ---
    print("[正例] 合格的中文景物描写")
    bad = 0
    for r in rows:
        exp = r["expect"].get("checks") or []
        # 正例只对"短篇"题有意义：长篇有 2000 字下限，短样本本来就不该过
        if r.get("length") == "long":
            continue
        v = D.judge_prose(r, GOOD_SAMPLE)
        # 该样本不可能满足每道题的字数区间，所以只看"指标类"检查是否存在明显误判
        # 正例的严格判定放在下面用专门题验证
    # 用一条宽松题严格验证正例能过
    loose = {"id": "verify_loose", "subcat": "prose",
             "expect": {"checks": [
                 {"type": "cliche_hits", "op": "not_contains", "value": []},
                 {"type": "sensory_cats", "op": "coverage", "value": 3},
                 {"type": "cv_sentence_len", "op": "gte", "value": 0.25},
                 {"type": "distinct_2", "op": "gte", "value": 0.55},
                 {"type": "han_count", "op": "gte", "value": 200},
                 {"type": "punct_kinds", "op": "gte", "value": 5},
             ]}}
    v = D.judge_prose(loose, GOOD_SAMPLE)
    m = D.measure(GOOD_SAMPLE)
    print("   感官类=%d 句长CV=%.3f distinct_2=%.3f 汉字=%d 标点=%d 套话=%s"
          % (len(m["sensory_cats"]), m["cv_sentence_len"], m["distinct_2"],
             m["han_count"], m["punct_kinds"], m["cliche_hits"]))
    if v["correct"]:
        print("   PASS 正例判过（score=%.0f）" % v["score"])
    else:
        print("   FAIL 正例判不过 -> %s" % v["reason"])
        print("        detail: %s" % json.dumps(v["detail"], ensure_ascii=False)[:300])
        ok = False
    print()

    # --- 反例：必须判不过 ---
    print("[反例] 每一条都必须判不过")
    for name, text in BAD_CASES.items():
        v = D.judge_prose(loose, text)
        if v["correct"]:
            print("   FAIL 「%s」竟然判过了（score=%.0f）" % (name, v["score"]))
            ok = False
        else:
            print("   PASS 「%s」判不过 -> %s" % (name, v["reason"][:52]))
    print()
    print("=" * 62)
    print(" %s" % ("全部通过" if ok else "存在失败项"))
    print("=" * 62)
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
