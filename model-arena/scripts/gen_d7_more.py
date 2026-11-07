# -*- coding: utf-8 -*-
"""gen_d7_more.py - append 30 NEW D7 long-context questions to datasets/full/D7_full.jsonl.

Why this shape: runner/longctx_judge.py is the only D7 judge and it supports exactly
four subcategories with FIXED logic (needle-single -> owner substring, needle-multi ->
exact code set, cross-doc -> the four AlphaWorks/BetaAlpha tokens, long-qa -> "30 天"
and "20%"). The judge may not be modified, so the new questions are built to fit it
instead of the other way round.

New fixtures go under datasets/fixtures/longctx/more/ (a new directory); no existing
fixture and no existing dataset row is touched. Questions reference fixtures through
context_file / context_files - long text is never inlined.

Budget: 18 needle-single + 6 needle-multi + 4 long-qa + 2 cross-doc = 30.

Usage:  python scripts/gen_d7_more.py [--dry-run]
"""
import argparse, json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FX = os.path.join(ROOT, "datasets", "fixtures", "longctx", "more")
TARGET = os.path.join(ROOT, "datasets", "full", "D7_full.jsonl")
NL = chr(10)

# measured on the existing fixtures: ~1.86 chars per token for this Chinese filler
CHARS_PER_TOKEN = 1.86

LEDGER_FILLER = [
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
    "内部控制自查发现两项流程缺陷,已进入整改跟踪清单。",
    "财务共享中心的单据处理时效中位数为十八小时。",
    "备用金清理完成后,零余额账户的数量减少到三个。",
    "年度预算编制的第一轮汇总已完成,待管理层评审后下发。",
    "收入确认的时点判断依据合同条款逐项复核,未发现提前确认。",
    "研发支出的资本化条件评估由技术部门与财务部门联合完成。",
    "应收账款的坏账准备按组合计提,迁徙率参数每季度更新一次。",
    "资金池的归集率提升到百分之九十四,闲置资金规模下降。",
]

SPEC_FILLER = [
    "接口的超时时间默认为三秒,调用方可通过配置项覆盖该默认值。",
    "服务间的重试策略采用指数退避,最大重试次数为三次。",
    "配置中心下发的变更会在十秒内推送到所有在线实例。",
    "日志采集采用异步批量写入,单批次上限为五百条记录。",
    "数据库连接池的初始容量为八,最大容量为三十二。",
    "缓存层的过期策略为写时刷新,读时不再延长有效期。",
    "灰度发布按百分之五、百分之二十、百分之百三档逐步放量。",
    "监控告警的静默窗口为十五分钟,避免抖动引发的重复通知。",
    "消息队列的消费位点每五秒提交一次,异常时回退到上一批次。",
    "序列化协议采用长度前缀加二进制负载,兼容旧版本字段。",
    "限流阈值按实例维度配置,突发流量允许两倍瞬时通过。",
    "健康检查分为存活探针与就绪探针,探测周期均为十秒。",
    "分布式锁的租约为三十秒,续约由持有方在租期过半时发起。",
    "任务调度的分片键使用业务主键哈希,保证同一实体串行处理。",
    "文件上传采用分片续传,单个分片大小为四兆字节。",
    "证书轮换在到期前三十天启动,新旧证书并行信任七天。",
    "链路追踪的采样率在生产环境固定为百分之一。",
    "定时任务的执行记录保留三十天,超期由清理作业归档。",
    "配置项变更需要两人复核,单人无法直接生效。",
    "备份策略为每日全量加每小时增量,保留周期为十四天。",
    "请求链路中的幂等键由客户端生成,服务端保存二十四小时。",
    "容量规划按峰值流量的两倍预留,扩容决策由监控指标触发。",
    "灰度环境的流量镜像比例为百分之十,仅复制不写回。",
    "接口版本的弃用公告提前两个发布周期发出。",
]

CONTRACT_FILLER = [
    "一般性条款,适用于常规业务操作,具体执行以附件为准。",
    "双方应按季度召开履约例会,会议纪要经双方确认后生效。",
    "保密义务在本协议终止后继续有效三年。",
    "不可抗力事件发生后,受影响方应在十日内提供书面说明。",
    "争议解决适用合同签订地法律,先协商后仲裁。",
    "服务级别协议作为附件一,与本协议具有同等效力。",
    "发票应在服务完成后十五个工作日内开具并寄送。",
    "任何一方转让本协议权利义务须经另一方书面同意。",
    "通知送达地址变更应在变更后五日内书面告知。",
    "本协议未尽事宜由双方另行签订补充协议约定。",
]

SURNAMES = ["王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴",
            "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗"]
ROLES = ["总", "工", "经理", "主管", "总监", "组长"]


def build_body(rng, target_chars, filler):
    parts, n = [], 0
    while n < target_chars:
        s = rng.choice(filler)
        parts.append(s)
        n += len(s)
    return "".join(parts)[:target_chars]


def insert_needles(rng, body, needles):
    """needles = [(code, owner, depth)] -> body with a sentence inserted at each depth."""
    out = body
    for code, owner, depth in needles:
        pos = int(len(out) * depth)
        cut = out.find("。", pos)
        pos = len(out) - 1 if cut == -1 else cut + 1
        ins = "项目 %s 由 %s 负责统筹推进,相关进展已同步至周报。" % (code, owner)
        out = out[:pos] + ins + out[pos:]
    return out


def make_owners(rng, n):
    names = rng.sample(SURNAMES, n)
    return [nm + rng.choice(ROLES) for nm in names]


def make_codes(rng, n, taken):
    out = []
    while len(out) < n:
        c = "PRJ-%d" % rng.randint(1000, 9999)
        if c not in taken:
            taken.add(c)
            out.append(c)
    return out


def write_fixture(name, text):
    os.makedirs(FX, exist_ok=True)
    p = os.path.join(FX, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text)


def needle_doc(rng, fam, size_name, tokens, filler, taken_codes, rows):
    target_chars = int(tokens * CHARS_PER_TOKEN)
    owners = make_owners(rng, 3)
    codes = make_codes(rng, 3, taken_codes)
    depths = [("head", 0.10), ("middle", 0.50), ("tail", 0.90)]
    needles = [(codes[i], owners[i], depths[i][1]) for i in range(3)]
    body = insert_needles(rng, build_body(rng, target_chars, filler), needles)
    fname = "%s_%s.txt" % (fam, size_name)
    chars = write_fixture(fname, body)
    rel = "fixtures/longctx/more/" + fname
    min_ctx = tokens + 1024

    for i, (code, owner, depth) in enumerate(needles):
        rows.append({
            "id": "d7m_needle_%s_%s_%s" % (fam, size_name, depths[i][0]),
            "dim": "D7", "source": "selfmade-needle", "license": "CC0",
            "subcat": "needle-single", "type": "longctx",
            "context_file": rel, "context_chars": chars,
            "context_tokens_approx": tokens,
            "needle_depth": depths[i][0], "min_ctx": min_ctx,
            "question": "文档中项目编号 %s 由谁负责?请只回答负责人姓名/职务。" % code,
            "expect": {"code": code, "owner": owner, "scoring": "owner string match"},
            "notes": "gen_d7_more: family=%s size=%s depth=%s" % (fam, size_name, depths[i][0]),
        })

    rows.append({
        "id": "d7m_multineedle_%s_%s" % (fam, size_name),
        "dim": "D7", "source": "selfmade-needle", "license": "CC0",
        "subcat": "needle-multi", "type": "longctx",
        "context_file": rel, "context_chars": chars,
        "context_tokens_approx": tokens, "min_ctx": min_ctx,
        "question": "文档中一共提到了几个项目编号?请全部列出,不要编造文档中没有的编号。",
        "expect": {"needles": codes, "scoring": "all found, no fabricated extra"},
        "notes": "gen_d7_more: exhaustive recall, family=%s size=%s" % (fam, size_name),
    })


def contract_doc(rng, tag, tokens, depth, rows):
    target_chars = int(tokens * CHARS_PER_TOKEN)
    filler = build_body(rng, target_chars, CONTRACT_FILLER)
    clause = (NL + "第 42 条 —— 提前终止:" + NL +
              "42.1 客户如需提前终止本协议,应提前不少于 30 天以书面形式通知公司。" + NL +
              "42.2 提前终止时,客户应支付相当于剩余合同金额 20% 的解约金。" + NL +
              "42.3 通知应发送至 legal@company.example.com。" + NL)
    pos = int(len(filler) * depth)
    cut = filler.find("。", pos)
    pos = len(filler) - 1 if cut == -1 else cut + 1
    body = filler[:pos] + clause + filler[pos:]
    fname = "contract_%s.txt" % tag
    chars = write_fixture(fname, body)
    rows.append({
        "id": "d7m_longqa_%s" % tag,
        "dim": "D7", "source": "selfmade", "license": "CC0",
        "subcat": "long-qa", "type": "longctx",
        "context_file": "fixtures/longctx/more/" + fname,
        "context_chars": chars, "context_tokens_approx": tokens,
        "min_ctx": tokens + 1024,
        "question": "根据该协议文档,客户提前终止合约需要提前多少天书面通知?解约金比例是多少?请分别给出通知天数和比例。",
        "expect": {"answer_refs": ["提前天数: 30 天", "解约金: 剩余合同金额的 20%"],
                   "scoring": "both values correct"},
        "notes": "gen_d7_more: clause at %.0f%% depth, %d chars" % (depth * 100, chars),
    })


DOC_C = ("董事会纪要(4月2日):经双方股东批准,Alpha Tech 与 Beta Works 的合并方案正式生效。"
         "新主体沿用 AlphaWorks 作为公司名称,注册地及总部设在上海,交割窗口定在 6 月 30 日之前,"
         "核心员工保留率承诺不低于 90%。" + NL)
DOC_D = ("供应商告知函(4月20日):关于 Alpha Tech 与 Beta Works 的合并,我方收到的口径与贵司不同。"
         "新公司名称将使用 BetaAlpha,总部仍在深圳办公,交割时间推迟到 9 月 30 日前后,"
         "人员保留比例调整为 70%。" + NL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rng = random.Random(20261101)
    taken = set()
    rows = []

    for size_name, tokens in [("6k", 6000), ("16k", 16000), ("32k", 32000), ("64k", 64000)]:
        needle_doc(rng, "ledger", size_name, tokens, LEDGER_FILLER, taken, rows)
    for size_name, tokens in [("12k", 12000), ("24k", 24000)]:
        needle_doc(rng, "spec", size_name, tokens, SPEC_FILLER, taken, rows)

    for tag, tokens, depth in [("a", 8000, 0.25), ("b", 14000, 0.55),
                               ("c", 22000, 0.80), ("d", 30000, 0.92)]:
        contract_doc(rng, tag, tokens, depth, rows)

    write_fixture("doc_c.txt", DOC_C)
    write_fixture("doc_d.txt", DOC_D)
    rows.append({
        "id": "d7m_crossdoc_001", "dim": "D7", "source": "selfmade", "license": "CC0",
        "subcat": "cross-doc", "type": "longctx",
        "context_files": ["fixtures/longctx/doc_a.txt", "fixtures/longctx/doc_b.txt"],
        "question": "文档 A 与文档 B 都描述了同一桩并购。请以表格形式逐条列出两份文档之间互相矛盾的地方。",
        "expect": {"must_find": ["合并后名称 AlphaWorks / BetaAlpha",
                                 "总部 上海 / 深圳",
                                 "交割时间 6月30日 / 9月30日",
                                 "员工保留率 90% / 70%"],
                   "scoring": "每找到一处矛盾得 1 分,满分 4"},
        "notes": "gen_d7_more: same pair as d7_crossdoc_000, table-form answer",
    })
    rows.append({
        "id": "d7m_crossdoc_002", "dim": "D7", "source": "selfmade", "license": "CC0",
        "subcat": "cross-doc", "type": "longctx",
        "context_files": ["fixtures/longctx/more/doc_c.txt", "fixtures/longctx/more/doc_d.txt"],
        "question": "一份董事会纪要与一份供应商告知函描述了同一桩并购,但关键信息对不上。请把所有不一致的细节列出来。",
        "expect": {"must_find": ["合并后名称 AlphaWorks / BetaAlpha",
                                 "总部 上海 / 深圳",
                                 "交割时间 6月30日 / 9月30日",
                                 "员工保留率 90% / 70%"],
                   "scoring": "每找到一处矛盾得 1 分,满分 4"},
        "notes": "gen_d7_more: second contradiction pair (doc_c/doc_d)",
    })

    # ---- append only ids that are not already present; never rewrite existing rows
    existing = []
    if os.path.exists(TARGET):
        with open(TARGET, encoding="utf-8") as f:
            existing = [ln for ln in f if ln.strip()]
    have = set()
    for ln in existing:
        try:
            have.add(json.loads(ln)["id"])
        except Exception:
            pass
    new = [r for r in rows if r["id"] not in have]

    if args.dry_run:
        print("dry-run: %d new rows, %d already present" % (len(new), len(rows) - len(new)))
        return
    with open(TARGET, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + NL)

    from collections import Counter
    c = Counter(r["subcat"] for r in rows)
    print("gen_d7_more: appended %d rows (skipped %d existing)" % (len(new), len(rows) - len(new)))
    for k in sorted(c):
        print("   %-14s %d" % (k, c[k]))
    print("fixtures written to datasets/fixtures/longctx/more/")
    print("D7_full.jsonl now has %d rows" % (len(existing) + len(new)))


if __name__ == "__main__":
    main()
