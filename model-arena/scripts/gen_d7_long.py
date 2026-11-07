# -*- coding: utf-8 -*-
"""gen_d7_long.py - append long-tier D7 questions, ONE DOCUMENT PER QUESTION.

Why one document per question: the existing long-tier questions share a single
128k/256k fixture between several needle-single items, and each item only asks
"does this owner string appear". Measured consequence: a reply that simply lists
every owner found in the document scores 6/6. That measures enumeration, not
retrieval, so a model that never locates anything still passes.

Each question generated here gets its own fixture holding exactly ONE needle, so
listing every name cannot pass more than the one question whose needle it is.

Budget: 8 items at 128k + 8 items at 256k = 16 new long-tier questions.
Together with the 8 existing ones that is 12 per tier, enough for a paired test
at the resolution this tier can afford.

Usage:  python scripts/gen_d7_long.py [--dry-run] [--verify]
"""
import argparse, json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FX = os.path.join(ROOT, "datasets", "fixtures", "longctx", "long")
TARGET = os.path.join(ROOT, "datasets", "full", "D7_full.jsonl")
NL = chr(10)

CHARS_PER_TOKEN = 1.86

FILLER = [
    "本期应付账款账龄结构保持稳定,九十天以内占比为百分之八十七点四。",
    "总账与明细账的对账工作已于月末完成,未发现未达账项差异。",
    "费用报销单据的合规抽查覆盖了十二个部门,退回补充材料的有七笔。",
    "固定资产折旧按年限平均法计提,本期新增三项电子设备。",
    "银行承兑汇票的到期托收全部按期完成,无逾期票据。",
    "成本核算口径与上季度保持一致,未发生会计政策变更。",
    "往来款清理专项工作推进到第三阶段,已核销历史挂账十四笔。",
    "预算执行率在本期达到百分之九十一点二,略高于滚动预测。",
    "税务申报按属地要求完成,附加税费的计算基数已复核。",
    "资金计划的月度滚动更新已发布,现金余额覆盖三个月运营支出。",
    "存货跌价准备的计提依据经复核后维持原估计,未作调整。",
    "关联方交易的定价政策沿用上年度的成本加成方式。",
    "在建工程转固的验收单据齐全,折旧起算月份按准则执行。",
    "审计调整分录已全部过账,试算平衡表借贷双方一致。",
    "合同履约成本的归集按项目辅助核算,分摊规则未变更。",
    "本期汇兑损益主要来自一笔外币采购的结算时点差异。",
]

SURNAMES = ["沈", "韩", "傅", "彭", "董", "袁", "蒋", "蔡", "潘", "田",
            "钟", "石", "崔", "龚", "秦", "江", "史", "顾", "侯", "邵"]
ROLES = ["工程师", "主管", "专员", "经理", "总监", "组长"]

DEPTHS = [("head", 0.06), ("quarter", 0.25), ("middle", 0.50),
          ("three-quarter", 0.75), ("tail", 0.94)]


def make_code(rng, used):
    while True:
        c = "PRJ-%04d" % rng.randint(1000, 9999)
        if c not in used:
            used.add(c)
            return c


def make_owner(rng, used):
    while True:
        o = rng.choice(SURNAMES) + rng.choice(ROLES)
        if o not in used:
            used.add(o)
            return o


def build_body(rng, target_chars):
    parts, n = [], 0
    while n < target_chars:
        s = rng.choice(FILLER)
        parts.append(s)
        n += len(s)
    return "".join(parts)[:target_chars]


def plant(body, code, owner, depth_frac):
    """Insert exactly one needle sentence at the requested depth."""
    pos = int(len(body) * depth_frac)
    cut = body.find("。", pos)
    pos = len(body) - 1 if cut == -1 else cut + 1
    ins = "项目 %s 由 %s 负责统筹推进,相关进展已同步至周报。" % (code, owner)
    return body[:pos] + ins + body[pos:]


def build_plan():
    rng = random.Random(20261031)
    used_codes, used_owners = set(), set()
    items = []
    # 30 per tier so a paired test can resolve a 10pp difference; the tier is
    # the only place where the four engine routes actually diverge.
    for tier_tok, count in ((131072, 30), (262144, 30)):
        for i in range(count):
            depth_name, depth_frac = DEPTHS[i % len(DEPTHS)]
            code = make_code(rng, used_codes)
            owner = make_owner(rng, used_owners)
            target_chars = int(tier_tok * CHARS_PER_TOKEN)
            body = build_body(rng, target_chars)
            body = plant(body, code, owner, depth_frac)
            tier = "%dk" % (tier_tok // 1024)
            fname = "solo_%s_%s_%d.txt" % (tier, depth_name, i)
            items.append({
                "id": "d7long_%s_%s_%d" % (tier, depth_name, i),
                "dim": "D7",
                "subcat": "needle-single",
                "type": "longctx",
                "source": "selfmade-needle-solo",
                "license": "CC0",
                "context_file": "fixtures/longctx/long/" + fname,
                "min_ctx": tier_tok + 1024,
                "context_chars": len(body),
                "context_tokens_approx": tier_tok,
                "needle_depth": depth_name,
                "question": "文档中项目 %s 由谁负责?只回答负责人姓名/职务。" % code,
                "expect": {"code": code, "owner": owner, "scoring": "owner string match"},
                "notes": "solo needle, size=%s depth=%s, one document per question" % (tier, depth_name),
                "_body": body,
                "_fname": fname,
            })
    return items


def write_all(items, dry_run=False):
    os.makedirs(FX, exist_ok=True)
    rows = []
    for it in items:
        body = it.pop("_body")
        fname = it.pop("_fname")
        if not dry_run:
            with open(os.path.join(FX, fname), "w", encoding="utf-8") as f:
                f.write(body)
        rows.append(it)
    if dry_run:
        return rows
    existing = []
    if os.path.exists(TARGET):
        with open(TARGET, encoding="utf-8") as f:
            existing = [json.loads(l) for l in f if l.strip()]
    have = set(r["id"] for r in existing)
    added = 0
    with open(TARGET, "a", encoding="utf-8") as f:
        for r in rows:
            if r["id"] in have:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + NL)
            added += 1
    return added


def verify():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "runner"))
    from longctx_judge import judge_needle_single
    rows = [json.loads(l) for l in open(TARGET, encoding="utf-8") if l.strip()]
    mine = [r for r in rows if r["id"].startswith("d7long_")]
    print("  new long-tier questions: %d" % len(mine))
    bad = 0
    for r in mine:
        p = os.path.join(ROOT, "datasets", r["context_file"])
        if not os.path.isfile(p):
            print("    MISSING fixture:", r["id"]); bad += 1; continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        code, owner = r["expect"]["code"], r["expect"]["owner"]
        # the needle must appear exactly once, and no other PRJ code may exist
        import re
        codes = re.findall(r"PRJ[-_ ]?\d{3,6}", txt)
        if txt.count(code) != 1:
            print("    %s: code appears %d times (want 1)" % (r["id"], txt.count(code))); bad += 1
        if len(set(codes)) != 1:
            print("    %s: %d distinct codes in doc (want 1)" % (r["id"], len(set(codes)))); bad += 1
        # positive: pairing the code with its owner passes
        if not judge_needle_single(r, "项目 %s 的负责人是 %s" % (code, owner))["correct"]:
            print("    %s: correct answer judged WRONG" % r["id"]); bad += 1
        # negative 1: naming someone else must fail
        if judge_needle_single(r, "项目 %s 的负责人是 张三丰" % code)["correct"]:
            print("    %s: wrong owner judged RIGHT" % r["id"]); bad += 1
        # negative 2: owner without the code must fail (enumeration attack)
        if judge_needle_single(r, "负责人是 " + owner)["correct"]:
            print("    %s: owner alone judged RIGHT" % r["id"]); bad += 1
    print("  verify failures: %d" % bad)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        return 1 if verify() else 0
    items = build_plan()
    if a.dry_run:
        for it in items:
            print("  %-26s %-8s min_ctx=%-8s chars=%d" % (
                it["id"], it["needle_depth"], it["min_ctx"], it["context_chars"]))
        print("  total: %d (dry run, nothing written)" % len(items))
        return 0
    added = write_all(items)
    print("  appended %d new questions to D7_full.jsonl" % added)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
