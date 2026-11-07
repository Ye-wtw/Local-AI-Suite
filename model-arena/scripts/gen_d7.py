# -*- coding: utf-8 -*-
"""gen_d7.py - long-context dimension (D7) generator.
Produces fixtures at several context sizes AND questions that probe the same
retrieval skill at different (size, depth) cells -> yields a degradation curve.
Question types:
  needle-single : find the owner of a GIVEN project code (targeted lookup)
  needle-multi  : list ALL codes in the document (exhaustive recall + anti-fabrication)
  cross-doc     : contradictions between two documents
  long-qa       : precise clause lookup in a long terms document
Usage: python scripts/gen_d7.py
"""
import json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NL = chr(10)

SIZES = [
    ("4k", 4000), ("8k", 8000), ("16k", 16000), ("32k", 32000),
    ("64k", 64000), ("128k", 128000), ("256k", 256000),
]
DEPTHS = [("head", 0.10), ("middle", 0.50), ("tail", 0.90)]

FILLER = [
    "本季度各业务单元的运营指标整体处于预期区间之内,未出现显著偏离。",
    "供应链管理团队完成了年度供应商复核,共有三十七家供应商通过合规检查。",
    "研发部门在本期推进了三个重点项目的里程碑评审,其中两个按期交付。",
    "市场部完成了新一轮品牌曝光投放,覆盖华东与华南六个重点城市。",
    "财务口径显示应收账款周转天数较上季度缩短了约四点二天。",
    "人力资源部组织了两次内部培训,参与人数累计达到一百二十人次。",
    "客户服务中心的首次响应时长中位数为四十七分钟,较上期有所改善。",
    "信息安全团队完成了一次渗透测试演练,未发现高危漏洞。",
    "生产制造环节的设备综合效率维持在百分之八十二左右的水准。",
    "法务部门复核了十四份对外合同,主要涉及条款表述的规范性调整。",
    "仓储物流的准时交付率为百分之九十六点三,略高于既定目标。",
    "产品团队收集了约两千条用户反馈,已按主题完成初步归类。",
    "质量管理体系通过了外部审核机构的第一阶段现场评审。",
    "本期新增专利申请五项,其中两项进入实质审查阶段。",
    "区域销售网络新增两个服务网点,覆盖范围内客户满意度保持稳定。",
]

FIRST = ["王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙", "马", "朱", "胡"]


def build_doc(rng, target_tokens, needles):
    """Build filler text of ~target_tokens (measured ~1.86 char/token), inserting needles."""
    # measured on Qwen3.5-9B: ~1.86 chars per token for this Chinese filler
    target_chars = int(target_tokens * 1.86)
    parts = []
    while sum(len(p) for p in parts) < target_chars:
        parts.append(rng.choice(FILLER))
    body = "".join(parts)
    # cut to size
    body = body[:target_chars]
    # insert needles at depths
    for code, owner, depth in needles:
        pos = int(len(body) * depth)
        # snap to a sentence boundary-ish
        nl = body.find("。", pos)
        if nl == -1:
            nl = len(body) - 1
        pos = nl + 1
        insert = "项目 %s 由 %s 负责统筹推进,相关进展已同步至周报。" % (code, owner)
        body = body[:pos] + insert + body[pos:]
    return body


def main():
    rng = random.Random(20261010)
    fx = os.path.join(ROOT, "datasets", "fixtures", "longctx")
    os.makedirs(fx, exist_ok=True)
    rows = []

    for size_name, tokens in SIZES:
        # 3 distinct needles, one per depth cell
        codes = []
        for d_i, (depth_name, depth) in enumerate(DEPTHS):
            code = "PRJ-%d" % rng.randint(1000, 9999)
            owner = rng.choice(FIRST) + rng.choice(["总", "工", "经理", "主管"])
            codes.append((code, owner, depth, depth_name))
        needles = [(c, o, d) for (c, o, d, _) in codes]
        doc = build_doc(rng, tokens, needles)
        fname = "report_%s.txt" % size_name
        with open(os.path.join(fx, fname), "w", encoding="utf-8") as f:
            f.write(doc)
        chars = len(doc)

        # single-needle questions (one per depth)
        for code, owner, depth, depth_name in codes:
            rows.append({
                "id": "d7_needle_%s_%s" % (size_name, depth_name),
                "dim": "D7", "source": "selfmade-needle", "license": "CC0",
                "subcat": "needle-single", "type": "longctx",
                "context_file": "fixtures/longctx/" + fname,
                "context_chars": chars, "context_tokens_approx": tokens,
                "needle_depth": depth_name, "min_ctx": tokens + 1024,
                "question": "文档中项目编号 %s 由谁负责?请只回答负责人姓名/职务。" % code,
                "expect": {"code": code, "owner": owner, "scoring": "owner string match"},
                "notes": "size=%s depth=%s" % (size_name, depth_name)})

        # multi-needle question (exhaustive recall + no fabrication)
        all_codes = [c for (c, o, d, _) in codes]
        rows.append({
            "id": "d7_multineedle_%s" % size_name,
            "dim": "D7", "source": "selfmade-needle", "license": "CC0",
            "subcat": "needle-multi", "type": "longctx",
            "context_file": "fixtures/longctx/" + fname,
            "context_chars": chars, "context_tokens_approx": tokens,
            "min_ctx": tokens + 1024,
            "question": "文档中一共提到了几个项目编号?请全部列出。",
            "expect": {"needles": all_codes, "scoring": "all found, no fabricated extra"},
            "notes": "exhaustive recall, size=%s" % size_name})

    # ---- cross-document contradiction ----
    doc_a = ("并购公告(3月1日):Alpha Tech 将与 Beta Works 合并。合并后主体命名为 AlphaWorks,"
             "总部迁至上海,预计 6 月 30 日前完成交割,员工保留率目标 90%。" + NL)
    doc_b = ("并购进展(3月15日):Alpha Tech 与 Beta Works 确认合并。合并后主体命名为 BetaAlpha,"
             "总部继续留在深圳,预计 9 月 30 日前完成交割,员工保留目标 70%。" + NL)
    with open(os.path.join(fx, "doc_a.txt"), "w", encoding="utf-8") as f:
        f.write(doc_a)
    with open(os.path.join(fx, "doc_b.txt"), "w", encoding="utf-8") as f:
        f.write(doc_b)
    rows.append({
        "id": "d7_crossdoc_000", "dim": "D7", "source": "selfmade", "license": "CC0",
        "subcat": "cross-doc", "type": "longctx",
        "context_files": ["fixtures/longctx/doc_a.txt", "fixtures/longctx/doc_b.txt"],
        "question": "文档 A 与文档 B 都描述了同一桩并购。请列出两份文档之间**互相矛盾**的地方。",
        "expect": {"must_find": ["合并后名称 AlphaWorks / BetaAlpha",
                                 "总部 上海 / 深圳",
                                 "交割时间 6月30日 / 9月30日",
                                 "员工保留率 90% / 70%"],
                   "scoring": "每找到一处矛盾得 1 分,满分 4"},
        "notes": "4 contradictions"})

    # ---- long-document precise QA ----
    terms = []
    for i in range(160):
        terms.append("第 %d 条:一般性条款,适用于常规业务操作,具体执行以附件为准。" % (i + 1))
    terms.append(NL + "第 42 条 —— 提前终止:" + NL)
    terms.append("42.1 客户如需提前终止本协议,应提前不少于 30 天以书面形式通知公司。" + NL)
    terms.append("42.2 提前终止时,客户应支付相当于剩余合同金额 20% 的解约金。" + NL)
    terms.append("42.3 通知应发送至 legal@company.example.com。" + NL)
    for i in range(160, 320):
        terms.append("第 %d 条:一般性条款,适用于常规业务操作,具体执行以附件为准。" % (i + 1))
    with open(os.path.join(fx, "terms_32k.txt"), "w", encoding="utf-8") as f:
        f.write("".join(terms))
    rows.append({
        "id": "d7_longqa_000", "dim": "D7", "source": "selfmade", "license": "CC0",
        "subcat": "long-qa", "type": "longctx",
        "context_file": "fixtures/longctx/terms_32k.txt",
        "min_ctx": 12000,
        "question": "根据该协议文档,客户提前终止合约需要提前多少天书面通知?解约金比例是多少?",
        "expect": {"answer_refs": ["提前天数: 30 天", "解约金: 剩余合同金额的 20%"],
                   "scoring": "both values correct"},
        "notes": "precise clause lookup"})

    out = os.path.join(ROOT, "datasets", "generated", "D7_generated.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + NL)
    from collections import Counter
    c = Counter(r["subcat"] for r in rows)
    print("D7_generated.jsonl: %d 题" % len(rows))
    for k, v in sorted(c.items()):
        print("   %-14s %d" % (k, v))
    print("fixtures:")
    for s in SIZES:
        p = os.path.join(fx, "report_%s.txt" % s[0])
        print("   report_%s.txt  %d 字符 (约 %d tokens)" % (s[0], os.path.getsize(p), s[1]))
    print("-> " + out)


if __name__ == "__main__":
    main()
