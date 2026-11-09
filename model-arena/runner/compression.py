# -*- coding: utf-8 -*-
"""compression.py - D12: does the model get dumber after the context is compacted?

The question this answers: a harness that hits the context limit summarises the
older turns and keeps going. The summary is lossy. Two things can then go wrong,
and they are different failures:

  retention  - facts from BEFORE the compaction are still recallable AFTER it
  stability  - the model does not start contradicting itself once compacted

Both are measured against the un-compacted baseline on the SAME questions, so
the comparison is paired (McNemar on the two outcomes).

How compaction is simulated: the runner does not depend on any particular
harness, so it applies a deterministic, lossy summariser of its own and states
the compression ratio. That keeps the test reproducible; a harness that
compresses differently will show different numbers, and the record says so.

Design notes:
  - the "before" facts are planted as verifiable tokens (same style as D7), so
    recall is checkable without an LLM judge
  - the summary is built to KEEP the question but DROP most planted facts, which
    is what a lossy summariser does; retention then measures what survived
  - no model call happens in this module: it builds prompts and scores replies
"""
import random
import re

NL = chr(10)

PLANT_FACTOR = 0.0      # reserved; kept explicit so the ratio is not a magic number


def build_scenario(seed=20261101, n_facts=18, filler_sentences=200):
    """Build one compaction scenario: a long doc with planted facts + a DEGRADED summary.

    The summary must not simply drop the answer - a summariser that says "the rest
    was omitted" makes the task impossible and the model is right to answer "I
    cannot tell". Measured: 4/4 scenarios scored 0% post-compaction, and every
    reply correctly quoted the omission notice. That measures honesty, not
    retention.

    What a real lossy summariser does is keep the FACT but damage the DETAIL:
    "PRJ-1022 有负责人（姓石）" instead of "PRJ-1022 由 石工程师 负责".
    The question then asks for the full owner, so the model has to reconstruct the
    damaged part. Retention measures whether it can.
    """
    rng = random.Random(seed)
    SURNAME = "沈韩傅彭董袁蒋蔡潘田钟石崔龚秦江史顾侯邵"
    ROLE = ["工程师", "主管", "专员", "经理", "总监", "组长"]
    filler = [
        "本期应付账款账龄结构保持稳定,九十天以内占比为百分之八十七点四。",
        "总账与明细账的对账工作已于月末完成,未发现未达账项差异。",
        "费用报销单据的合规抽查覆盖了十二个部门,退回补充材料的有七笔。",
        "固定资产折旧按年限平均法计提,本期新增三项电子设备。",
        "银行承兑汇票的到期托收全部按期完成,无逾期票据。",
        "成本核算口径与上季度保持一致,未发生会计政策变更。",
        "往来款清理专项工作推进到第三阶段,已核销历史挂账十四笔。",
        "预算执行率在本期达到百分之九十一点二,略高于滚动预测。",
    ]
    planted = {}
    used = set()
    for i in range(n_facts):
        while True:
            code = "PRJ-%04d" % rng.randint(1000, 9999)
            if code not in used:
                used.add(code); break
        while True:
            owner = rng.choice(SURNAME) + rng.choice(ROLE)
            if owner not in planted.values():
                break
        planted[code] = owner

    # 难度因子:让**别的项目也用同一个姓氏**。
    #
    # 不加这个,摘要里「姓氏 石」全篇只有一个对应人,模型只要把姓氏和职务
    # 拼起来就对了 —— 实测 24/24 满分,说明它做的是"拼接",不是"分辨"。
    # 真实压缩场景里同一份文档会出现多个同姓的人,模型必须先认出
    # "PRJ-1022 这一条"对应哪个人,再拼片段,这才是保留能力的真考验。
    #
    # 做法:挑若干个非目标项目,把它们的负责人换成与目标同姓、不同职务。
    # 只在 n_facts 足够时启用(至少 8 个事实),否则干扰项会挤占主体。
    if n_facts >= 8:
        codes_pre = list(planted)
        tgt = codes_pre[-1]
        tsurname = planted[tgt][0]
        others = [c for c in codes_pre if c != tgt]
        roles_used = {planted[tgt][1:]}
        # 注意:这里**不能**写 while True + "角色用完就重试"。
        # ROLE 只有 6 个,而在 n_facts=18 时 half(others)≈8,第 7 个必然
        # 永远找不到新角色 -> 无限循环,整个 build_scenario 挂死
        # (实测脚本连续多次毫无输出,就是卡在这里)。
        # 改成有界重试:候选角色不够就跳过,少几个同姓干扰项也无所谓。
        spare_roles = [r for r in ROLE if r not in roles_used]
        for c in others[:max(2, len(others) // 2)]:
            if not spare_roles:
                break
            role = spare_roles.pop(0)
            planted[c] = tsurname + role

    lines = list(filler) * (max(1, filler_sentences // len(filler)))
    codes = list(planted)
    for i, code in enumerate(codes):
        pos = int(len(lines) * (i + 1) / (len(codes) + 1))
        lines.insert(pos, "项目 %s 由 %s 负责统筹推进,相关进展已同步至周报。" % (code, planted[code]))
    doc = NL.join(lines)

    target = codes[-1]
    target_owner = planted[target]

    # Degraded summary: the TARGET fact survives as FRAGMENTS that must be
    # recombined, not as a bare surname. Two earlier attempts both scored 0% for
    # the wrong reason:
    #   v1 dropped the fact and said "omitted"  -> model correctly said unknown
    #   v2 kept only "姓石"                      -> model correctly said "only surname"
    # Both measured honesty, not reconstruction. The fragments below are enough
    # to rebuild the answer ("石" + "工程师"), so a drop to 0% would finally mean
    # a real loss of the ability to reconstruct.
    summary_lines = ["【压缩摘要】以下为长文档经压缩后的要点,部分措辞已简化。"]
    # 难度 v3:目标行的片段**倒序**给出,并且摘要里塞进"近乎正确但错"的诱饵。
    #
    # 为什么还要加:18 个事实 + 5 个同姓干扰之后,9B(9B)与 27B 仍都拿 100%。
    # 说明"同姓"只让人多看一眼,不需要真正重组结构。倒序把任务从
    # "找到那一条"推进一步到"按正确顺序拼回去":
    #   正序:姓氏 沈, 职务为总监  -> 直接读出来就对了
    #   倒序:职务 总监, 姓氏 沈  -> 必须知道"姓在前、职务在后"才是完整姓名
    # 诱饵行给一个同姓但职务差一个字的假人,答对照抄诱饵就会答错。
    roles_all = list(ROLE)
    decoy_role = next((r for r in roles_all if r != target_owner[1:]), roles_all[0])
    for c in codes:
        owner = planted[c]
        if c == target:
            # 难度 v5:目标的两段**拆开**，中间隔着别的项目的一行。
            #
            # 之前所有版本都把「职务」和「姓氏」放在同一行，模型读一行、
            # 调个顺序就答对了 —— 实测真模型 24/24，区分度为 0。
            # 拆开之后，模型必须先确认这两段说的是**同一个项目**（两行都带编号），
            # 再跨行拼回完整姓名，这才需要跟踪而不是朗读。
            #
            # 信息量没有减少：答案依然完全可从摘要推出，
            # 所以降到 0% 只会是"没能跨行重建"，不会是"题目无解"
            # （那测的是诚实，上一版踩过这个坑）。
            summary_lines.append("项目 %s(编号已确认): 职务为 %s。" % (c, owner[1:]))
        else:
            summary_lines.append("项目 %s 由 %s 负责。" % (c, owner))
    # 诱饵:与目标同姓、职务近似,放在目标行附近增加混淆
    decoy_code = "PRJ-%04d" % rng.randint(1000, 9999)
    while decoy_code in planted:
        decoy_code = "PRJ-%04d" % rng.randint(1000, 9999)
    insert_at = summary_lines.index("项目 %s(编号已确认): 职务为 %s。" % (target, target_owner[1:]))
    summary_lines.insert(insert_at,
        "项目 %s 的负责人: 姓氏 %s, 职务为%s(该条为其他项目的记录)。"
        % (decoy_code, target_owner[0], decoy_role))

    # 难度 v4(温和版):给**目标项目本身**加一条残缺记录,但正确信息仍在。
    #
    # 为什么这样加而不再砍信息:D12 前两版试图"砍到只有姓氏"来制造难度,
    # 结果题变成无解 —— 模型答"信息不足"是正确行为,测出来却是 0%,
    # 那测的是诚实不是保留(代码注释里记着这次教训)。
    #
    # v4 不给无解,只给**歧义**:目标项目出现两次,一条只写职务(缺姓氏),
    # 一条是倒序的完整片段。正确答案依然可从后者拼出,但模型必须
    # 判断"哪条才是完整的",而不是把两条都抄上或随便选一条。
    # 抄残缺那条 -> 缺姓氏 -> 判错;两条都抄 -> 按枚举判错。
    # 两段分置摘要两端，谁都不落在"最后一行"这个最容易被读到的位置：
    #   - 职务行插到开头三分之一处
    #   - 姓氏行插到结尾前几位
    # 中间隔着十几个别项目的行，模型必须先靠编号认出是同一条记录，
    # 再跨行拼回完整姓名 —— 这才测"跟踪"，而不是"朗读末尾"。
    role_line = "项目 %s(编号已确认): 职务为 %s。" % (target, target_owner[1:])
    if role_line in summary_lines:
        summary_lines.remove(role_line)
    summary_lines.insert(max(2, len(summary_lines) // 3), role_line)
    # 姓氏行**不带项目编号**，改用"指向上文那条职务记录"的引用：
    # "上面第 N 条写着职务的那位负责人，姓氏为 X"。
    #
    # 为什么还要再进一步：拆行之后模型依然 24/24 —— 因为两行都带 PRJ-8983，
    # 它只要按编号搜两次就能拼上，那是检索不是重建。去掉编号后，必须先
    # 定位职务那条记录，再顺着"那一处"的引用回到姓氏，两个片段才连得上。
    # 信息仍完整（两条记录都在摘要里，索引也写明了），所以答错只可能是
    # "没跟住引用"，不会是"题目无解"。
    role_idx = summary_lines.index(role_line)
    surname_line = ("上文第 %d 条注明的职务，其负责人姓氏为 %s"
                    "(该条与上条为同一项目)。" % (role_idx, target_owner[0]))
    summary_lines.insert(len(summary_lines) - 2, surname_line)
    summary = NL.join(summary_lines)

    question = ("文档中项目 %s 由谁负责?请同时写出项目编号与负责人姓名/职务。" % target)

    return {
        "doc": doc,
        "planted": planted,
        "question": question,
        "summary": summary,
        "kept_facts": codes[:-1],
        "dropped_facts": [target],
        "target_code": target,
        "target_owner": target_owner,
        "target_surname": target_owner[0],
    }


def judge_retention(scenario, response):
    """Pass when the answer still pairs the target code with its owner.

    Same pairing rule as judge_needle_single: the code is already in the
    question, so the owner alone is the answer; but the owner must be the RIGHT
    one, and a roster of every owner in the document must not pass.
    """
    txt = (response or "").strip()
    code = scenario["target_code"]
    owner = scenario["target_owner"]
    if not txt:
        return {"correct": False, "reason": "empty reply", "paired": False}
    others = [o for c, o in scenario["planted"].items() if c != code]
    named_others = [o for o in others if o in txt]

    # 判"是否把姓氏和职务都还原了",而不是"是否逐字照抄"。
    #
    # 压缩摘要把姓名拆成了两段(「姓氏 石」「职务 工程师」),模型最自然的
    # 复述是「姓石,职务工程师」或「石 工程师」。旧实现只认 owner 整串
    # 连续出现,于是这类正确还原全被判错 —— 这正是项目铁律点名的
    # "措辞精确匹配"陷阱。实测该写法被判 MISSING。
    #
    # 保留原有的反枚举约束:同时列出多个负责人,算背名单不算回忆。
    surname, role = owner[0], owner[1:]
    has_surname = surname in txt
    has_role = role in txt

    # 编号也必须是**这一个**，不只是一个编号。
    #
    # 题面明写「请同时写出项目编号与负责人姓名/职务」，而旧判据只看姓+职务，
    # 于是「PRJ-2491 由 江总监 负责」判对 —— 那是别的项目。实测负对照抓到这一点：
    # 同姓干扰正是为了让模型必须靠编号区分，判分器却把唯一的区分依据丢掉了。
    # 只要回答里出现了别的 PRJ 编号而目标编号不在，就不算还原。
    code_ok = code in txt
    other_codes = [c for c in (scenario.get("planted") or {}) if c != code and c in txt]
    # 注意:这里曾经有一个 "has_surname and has_role and len(named_others) <= 1"
    # 的分支先返回 True,导致下面这段"提到别人不算背名单"的判断永远走不到 ——
    # 即修了 bug 却留下死代码,表现是"改了源码、行为没变"(实测注入 False
    # 后判分结果仍然是 True)。删掉那个重复分支,只保留下面这一处判据。
    #
    # 反枚举只在"列出多个候选答案"时生效,不能因为**解释时提到别人**就判错。
    #
    # 实测误判:9B 正确答出「PRJ-4580 由 沈专员 负责」,随后解释
    # "摘要里还有沈工程师、沈主管等其他项目的人,不要混淆" ——
    # 那是论证,不是背名单。旧规则看到 2 个其他负责人就判 roster,
    # 把一道完全正确的题翻成错。
    #
    # 新判据:只有当**答案本身不成形**(配对不完整)且提到多个负责人时才按枚举处理。
    # 配对完整时,提及别人一律不影响正确性 —— 那正是合理的排除论证。
    # 界线要划准：**只提别人编号**才算答错；完全不提编号不算错。
    #
    # 题面里已经写着目标编号，所以「姓陈,职务总监」这种不重复编号的答法是对的 ——
    # 强制要求复述编号会把正确回答判错（实测 test_judges.py 的「D12 拼接式写法判对」
    # 因此从 PASS 翻成 FAIL）。而「PRJ-2491 由 江总监 负责」明确指向另一个项目，
    # 必须判错，否则同姓干扰这一整段设计就白做了。
    if other_codes and not code_ok:
        return {"correct": False,
                "reason": "answered for %s, not %s - owner alone does not say WHICH project"
                          % (other_codes[:2], code),
                "code_ok": False, "other_codes": other_codes}
    if has_surname and has_role:
        return {"correct": True,
                "reason": "owner %s reconstructed for %s (mentions %d others in reasoning)"
                          % (owner, code, len(named_others)), "paired": True,
                "code_ok": True}
    if owner in txt:
        return {"correct": False,
                "reason": "named %s plus %d other owners - roster, not recall"
                          % (owner, len(named_others)), "paired": True}
    return {"correct": False,
            "reason": "owner %s not reconstructed (surname=%s role=%s)"
                      % (owner, has_surname, has_role), "paired": False}


def build_prompt(scenario, compacted, baseline_answer=None):
    """Compose the user message for the pre- or post-compaction probe."""
    if not compacted:
        return scenario["doc"] + NL + NL + scenario["question"]
    parts = [scenario["summary"]]
    if baseline_answer:
        parts.append("【此前你的回答】" + baseline_answer)
    parts.append(scenario["question"])
    return NL + NL.join(parts)


def summarize_retention(baseline_recs, compacted_recs):
    """Paired comparison: retention rate and whether it dropped significantly.

    baseline_recs / compacted_recs are lists of {id, correct}. They must cover
    the same ids; the pairing is what makes the McNemar test valid.
    """
    b = {r["id"]: bool(r.get("correct")) for r in baseline_recs}
    c = {r["id"]: bool(r.get("correct")) for r in compacted_recs}
    ids = [i for i in b if i in c]
    if not ids:
        return {"n": 0, "baseline_acc": 0.0, "compacted_acc": 0.0,
                "retention": 0.0, "note": "no paired ids"}
    base_ok = sum(1 for i in ids if b[i])
    comp_ok = sum(1 for i in ids if c[i])
    n = len(ids)
    # discordant pairs drive McNemar
    b_only = sum(1 for i in ids if b[i] and not c[i])
    c_only = sum(1 for i in ids if c[i] and not b[i])
    try:
        import math
        m = b_only + c_only
        if m == 0:
            p = 1.0
        else:
            k = min(b_only, c_only)
            p = 2.0 * sum(math.comb(m, j) for j in range(k + 1)) / (2 ** m)
            p = min(1.0, p)
    except Exception:
        p = None
    return {
        "n": n,
        "baseline_acc": base_ok / n,
        "compacted_acc": comp_ok / n,
        "retention": (comp_ok / base_ok) if base_ok else 0.0,
        "lost": b_only,
        "gained": c_only,
        "mcnemar_p": p,
    }