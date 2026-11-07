# -*- coding: utf-8 -*-
"""test_judges.py — 判分器回归测试(本会话踩过的坑,逐个钉死).

为什么需要它:AGENTS.md 铁律第 3 条说「判分器比模型更容易出错」。
实测这轮修掉的 4 个缺陷里 3 个在判分器:
  1. D12 把「解释时提到别人」当成「背名单」判错
  2. D11 的「圆圈」用 rect 画成方块 -> 题目无解
  3. D4 的 {cases!r} 占位符没替换 -> 37 题全判错
这些都不会让程序崩溃,只会让分数悄悄出错 —— 所以必须有测试钉住。

Run: python runner/test_judges.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  " + detail) if detail and not ok else ""))


def test_d12_judge():
    """D12 压缩判分器:配对完整就算对,提到别人不算背名单。"""
    import compression as C

    sc = C.build_scenario(seed=20261110)
    owner = sc["target_owner"]
    code = sc["target_code"]
    others = [o for c, o in sc["planted"].items() if c != code]

    v = C.judge_retention(sc, "%s 由 %s 负责" % (code, owner))
    check("D12 完整答案判对", v["correct"] is True, str(v))

    # 模型最常见的自然写法:姓 + 职务分开说
    v = C.judge_retention(sc, "姓%s,职务%s" % (owner[0], owner[1:]))
    check("D12 拼接式写法判对", v["correct"] is True, str(v))

    # 关键回归:解释时提到别的负责人,不该判错
    if others:
        resp = "%s 由 %s 负责。另外摘要里还有 %s,那是别的项目。" % (
            code, owner, others[0])
        v = C.judge_retention(sc, resp)
        check("D12 提到别人仍判对", v["correct"] is True, str(v))

    # 反向:答错的人必须判错
    v = C.judge_retention(sc, "%s 由 张三负责" % code)
    check("D12 编造判错", v["correct"] is False, str(v))

    # 反向:只给姓氏不给职务,不算还原
    v = C.judge_retention(sc, "姓%s" % owner[0])
    check("D12 只答姓氏判错", v["correct"] is False, str(v))


def test_d11_items():
    """D11 视觉:题目必须真的可判(图与答案一致),不能有同形不同名的形状。"""
    import tempfile
    import vision as V

    tmp = os.path.join(ROOT, "results", ".test_vision")
    os.makedirs(tmp, exist_ok=True)
    items = V.build_all(tmp)

    check("D11 题数 >= 40", len(items) >= 40, "实际 %d" % len(items))

    subs = set(x["subcat"] for x in items)
    for need in ("ocr", "pattern", "detail", "color", "layout", "bars",
                 "dense", "occlusion"):
        check("D11 含子类 %s" % need, need in subs)

    # 每条都必须:有答案 + 有图 + 判分器认自己给的答案
    bad = []
    for x in items:
        if not x.get("answer") or not os.path.exists(x.get("image", "")):
            bad.append(x.get("id"))
            continue
        v = V.judge_vision(x, x["answer"])
        if not v.get("correct"):
            bad.append(x.get("id"))
    check("D11 每题自洽(答案能判对)", not bad, str(bad[:4]))

    # 反向:乱答必须判错
    v = V.judge_vision(items[0], "完全答非所问")
    check("D11 乱答判错", v["correct"] is False, str(v))


def test_d4_lcb():
    """D4 LiveCodeBench 题:测试代码不能残留 {cases!r} 占位符。"""
    path = os.path.join(ROOT, "datasets", "full", "D4_full.jsonl")
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    lcb = [r for r in rows if r.get("subcat") == "public-livecodebench"]
    check("D4 含 LiveCodeBench 题", len(lcb) > 0, "实际 %d" % len(lcb))

    # 这就是那个真 bug:{cases!r} 没被替换,37 题全带着字面量
    broken = [r["id"] for r in lcb if "{cases" in (r.get("test") or "")]
    check("D4 LCB 测试代码无占位符残留", not broken, str(broken[:3]))

    miss = [r["id"] for r in lcb
            if not (r.get("test") or "").startswith("def check(")
            or r.get("entry_point") != "solve"]
    check("D4 LCB 接口统一(solve + check)", not miss, str(miss[:3]))

    # 每条 LCB 题都必须能跑判分器(空答案 -> no_code,不是崩)
    import code_judge
    if lcb:
        try:
            r = code_judge.judge_code(lcb[0], "")
            ok = (r.get("correct") is False) and r.get("stage") == "no_code"
        except Exception as e:
            ok, r = False, str(e)[:60]
        check("D4 空答案不崩且判错", ok, str(r)[:80])


def test_dataset_bank():
    """题库规模与文档一致 —— 文档写错了会误导读者。"""
    expect = {"D2": 552, "D3": 351, "D4": 117, "D5": 401, "D6": 147,
              "D7": 120, "D8": 100, "D9": 110, "D10": 71}
    for dim, want in expect.items():
        p = os.path.join(ROOT, "datasets", "full", "%s_full.jsonl" % dim)
        n = sum(1 for l in io.open(p, encoding="utf-8") if l.strip())
        check("%s 题数 = %d" % (dim, want), n == want, "实际 %d" % n)


def main():
    print("=" * 58)
    print(" 判分器回归测试")
    print("=" * 58)
    for fn in (test_d12_judge, test_d11_items, test_d4_lcb, test_dataset_bank):
        print()
        print("[%s]" % fn.__name__)
        try:
            fn()
        except Exception as e:
            FAIL.append(fn.__name__)
            print("  FAIL 整组异常: %s" % str(e)[:100])

    print()
    print("=" * 58)
    print(" 通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print(" 失败项:")
        for f in FAIL:
            print("   - " + f)
    print("=" * 58)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())