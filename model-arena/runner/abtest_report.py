# -*- coding: utf-8 -*-
"""abtest_report.py - 把 abtest.py 的结果文件渲染成四维度 A/B 对照表。

三个硬规矩:
  1. 差异只认 McNemar 配对检验(runner/stats.py),不认"看起来更高"。两边答的是
     同一批题,所以必须配对;比较两个独立准确率会把配对信息扔掉。
  2. 覆盖率不足 60% 的维度显式标注"不可下结论",而不是照样算出一个百分比。
  3. 速度取 speed_probe 的客户端 p50 tok/s,成本按 config/prices.json 的每百万
     token 价目算。没有数据就写 n/a,不拿 0 冒充。

用法:
  python runner/abtest_report.py --in results/abtest_mock_<ts>.json
  python runner/abtest_report.py --in results/abtest_x.json --a A --b B --json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from stats import mcnemar, wilson_ci, cohens_h    # noqa: E402

NL = chr(10)
MIN_COVERAGE = 0.60


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt_p(p):
    if p is None:
        return "n/a"
    if p < 0.0001:
        return "<0.0001"
    return "%.4f" % p


def _speed(a):
    sp = a.get("speed") or {}
    v = sp.get("output_tps_p50")
    return v if isinstance(v, (int, float)) and v > 0 else None


def _cost(a):
    c = a.get("cost") or {}
    v = c.get("est_cost")
    return v if isinstance(v, (int, float)) else None


def compare_dim(a_arm, b_arm, dimension, blocks):
    """一个维度上的配对比较。返回可以直接打印的 dict。

    coverage 的分母必须是计划题数,不是"实际答了多少题" —— 否则一个只抽到 8 题的
    维度会报 8/8=100% 覆盖,而那正是这份报告要避免的误导。
    """
    ra = {r["id"]: r for r in (a_arm.get("records") or []) if r.get("dimension") == dimension}
    rb = {r["id"]: r for r in (b_arm.get("records") or []) if r.get("dimension") == dimension}
    common = sorted(set(ra) & set(rb))
    # 接口错误不是"答错",两边都从配对里剔除 —— 否则一次网络抖动会被读成能力差异
    pairs, ids = [], []
    err_a = err_b = 0
    for qid in common:
        x, y = ra[qid], rb[qid]
        if x.get("error"):
            err_a += 1
            continue
        if y.get("error"):
            err_b += 1
            continue
        pairs.append((bool(x.get("correct")), bool(y.get("correct"))))
        ids.append(qid)
    n = len(pairs)
    ka = sum(1 for p in pairs if p[0])
    kb = sum(1 for p in pairs if p[1])
    acc_a = ka / float(n) if n else 0.0
    acc_b = kb / float(n) if n else 0.0
    mc = mcnemar(pairs) if pairs else {"n01": 0, "n10": 0, "p": None,
                                       "verdict": "no paired data"}
    la, ha = wilson_ci(ka, n) if n else (0.0, 0.0)
    lb, hb = wilson_ci(kb, n) if n else (0.0, 0.0)

    planned = sum(b.get("planned") or 0 for b in (blocks or [])
                  if b.get("dimension") == dimension)
    if not planned:
        planned = len(ra) or len(rb) or n
    coverage = (n / float(planned)) if planned else 0.0
    return {
        "dimension": dimension,
        "planned": planned,
        "paired_n": n,
        "errors_a": err_a,
        "errors_b": err_b,
        "a_correct": ka, "b_correct": kb,
        "a_acc": acc_a, "b_acc": acc_b,
        "a_ci": [la, ha], "b_ci": [lb, hb],
        "delta_pp": (acc_a - acc_b) * 100.0,
        "mcnemar": mc,
        "cohens_h": cohens_h(acc_a, acc_b) if n else 0.0,
        "coverage": coverage,
        "conclusive": coverage >= MIN_COVERAGE and n > 0,
        "ids": ids,
    }


def dim_order(plan_or_result):
    order = []
    for b in plan_or_result.get("blocks") or []:
        d = b.get("dimension")
        if d and d not in order:
            order.append(d)
    for a in plan_or_result.get("arms") or []:
        for r in a.get("records") or []:
            d = r.get("dimension")
            if d and d not in order:
                order.append(d)
    return order


def pick_arms(data, want_a, want_b):
    arms = [a for a in (data.get("arms") or []) if not a.get("skipped")]
    if not arms:
        return None, None
    if want_a and want_b:
        da = next((a for a in arms if a["key"] == want_a), None)
        db = next((a for a in arms if a["key"] == want_b), None)
        return da, db
    local = [a for a in arms if a.get("kind") == "local"] or arms
    a = local[0]
    b = local[1] if len(local) > 1 else (arms[1] if len(arms) > 1 else None)
    return a, b


def main():
    ap = argparse.ArgumentParser(description="渲染 A/B 对照报告(四维度 + McNemar + 速度 + 成本)")
    ap.add_argument("--in", dest="inp", required=True, help="results/abtest_*.json")
    ap.add_argument("--a", default="", help="A 臂的 key(默认取第一个 local 臂)")
    ap.add_argument("--b", default="", help="B 臂的 key(默认取第二个 local 臂)")
    ap.add_argument("--json", action="store_true", help="额外输出机器可读的 JSON")
    args = ap.parse_args()

    data = load(args.inp)
    a_arm, b_arm = pick_arms(data, args.a, args.b)
    if a_arm is None:
        print("结果文件里没有任何跑完的臂: %s" % args.inp, file=sys.stderr)
        return 2
    if b_arm is None:
        print("只有一臂有数据,无法做配对比较;先补齐 B 臂。", file=sys.stderr)
        return 2

    dims = dim_order(data)
    rows = [compare_dim(a_arm, b_arm, d, data.get("blocks") or []) for d in dims]

    print("=" * 100)
    print("  A/B 对照报告   %s" % os.path.basename(args.inp))
    print("=" * 100)
    print("  A 臂: %-28s 模型 %-22s 速度 %-8s 成本 %s"
          % (a_arm["key"], str(a_arm.get("model"))[:22],
             ("%.1f tok/s" % _speed(a_arm)) if _speed(a_arm) else "n/a",
             ("%.4f %s" % (_cost(a_arm), (a_arm.get("cost") or {}).get("currency", "CNY")))
             if _cost(a_arm) is not None else "n/a"))
    print("  B 臂: %-28s 模型 %-22s 速度 %-8s 成本 %s"
          % (b_arm["key"], str(b_arm.get("model"))[:22],
             ("%.1f tok/s" % _speed(b_arm)) if _speed(b_arm) else "n/a",
             ("%.4f %s" % (_cost(b_arm), (b_arm.get("cost") or {}).get("currency", "CNY")))
             if _cost(b_arm) is not None else "n/a"))
    print("  模式: %s | 题库 %d 题 | 运行 %s"
          % (data.get("mode"), data.get("n_questions") or 0, data.get("started")))
    print()
    print("  四维度对照(配对 McNemar 检验;接口错误不计入分母)")
    print("  " + "-" * 96)
    print("  %-20s %10s %10s %9s %11s %8s %8s  %s"
          % ("维度", "A 正确率", "B 正确率", "差值", "McNemar p", "样本量", "覆盖率", "结论"))
    print("  " + "-" * 96)
    for r in rows:
        verdict = "不可下结论" if not r["conclusive"] else (
            "A 显著优于 B" if (r["mcnemar"].get("p") is not None
                              and r["mcnemar"]["p"] < 0.05 and r["delta_pp"] > 0) else
            "B 显著优于 A" if (r["mcnemar"].get("p") is not None
                              and r["mcnemar"]["p"] < 0.05 and r["delta_pp"] < 0) else
            "无显著差异")
        p_txt = _fmt_p(r["mcnemar"].get("p"))
        print("  %-20s %9.1f%% %9.1f%% %+8.1fpp %11s %8d %7.0f%%  %s"
              % (r["dimension"], r["a_acc"] * 100, r["b_acc"] * 100, r["delta_pp"],
                 p_txt, r["paired_n"], r["coverage"] * 100, verdict))
    print("  " + "-" * 96)

    # 速度 / 成本对照
    print()
    print("  速度与成本")
    print("  " + "-" * 96)
    print("  %-20s %16s %16s %16s" % ("项目", "A", "B", "说明"))
    print("  " + "-" * 96)
    sa, sb = _speed(a_arm), _speed(b_arm)
    print("  %-20s %16s %16s %16s"
          % ("输出速度 p50", ("%.1f tok/s" % sa) if sa else "n/a",
             ("%.1f tok/s" % sb) if sb else "n/a", "speed_probe 客户端计时"))
    ta = (a_arm.get("speed") or {}).get("ttft_s_p50")
    tb = (b_arm.get("speed") or {}).get("ttft_s_p50")
    print("  %-20s %16s %16s %16s"
          % ("TTFT p50", ("%.2f s" % ta) if ta else "n/a",
             ("%.2f s" % tb) if tb else "n/a", "首 token 延迟"))
    ca, cb = _cost(a_arm), _cost(b_arm)
    cur = (a_arm.get("cost") or {}).get("currency", "CNY")
    print("  %-20s %16s %16s %16s"
          % ("估算成本", ("%.4f %s" % (ca, cur)) if ca is not None else "n/a",
             ("%.4f %s" % (cb, cur)) if cb is not None else "n/a",
             "config/prices.json 每百万 token 价目"))
    ta_ = (a_arm.get("cost") or {}).get("prompt_tokens") or 0
    tb_ = (a_arm.get("cost") or {}).get("completion_tokens") or 0
    print("  %-20s %16s %16s %16s"
          % ("A token 用量", "%d in / %d out" % (ta_, tb_), "", ""))
    ua = (b_arm.get("cost") or {}).get("prompt_tokens") or 0
    ub = (b_arm.get("cost") or {}).get("completion_tokens") or 0
    print("  %-20s %16s %16s %16s"
          % ("B token 用量", "%d in / %d out" % (ua, ub), "", ""))
    print("  " + "-" * 96)

    # 逐维度细节
    print()
    print("  逐维度细节")
    print("  " + "-" * 96)
    for r in rows:
        mc = r["mcnemar"]
        print("  [%s] 配对样本 %d 题(计划 %d,覆盖 %.0f%%)"
              % (r["dimension"], r["paired_n"], r["planned"], r["coverage"] * 100))
        print("      A %d/%d = %.1f%%  95%%CI [%.1f%%, %.1f%%]"
              % (r["a_correct"], r["paired_n"], r["a_acc"] * 100,
                 r["a_ci"][0] * 100, r["a_ci"][1] * 100))
        print("      B %d/%d = %.1f%%  95%%CI [%.1f%%, %.1f%%]"
              % (r["b_correct"], r["paired_n"], r["b_acc"] * 100,
                 r["b_ci"][0] * 100, r["b_ci"][1] * 100))
        print("      配对: A 独对 %d 题, B 独对 %d 题, p = %s (%s), Cohen's h = %.3f"
              % (mc.get("n10", 0), mc.get("n01", 0), _fmt_p(mc.get("p")),
                 mc.get("verdict"), r["cohens_h"]))
        if r["errors_a"] or r["errors_b"]:
            print("      接口错误已剔除: A %d 题, B %d 题" % (r["errors_a"], r["errors_b"]))
        if not r["conclusive"]:
            print("      ⚠ 不可下结论:覆盖率 %.0f%% 低于 %.0f%% 的门槛,该维度样本不足以支撑差异判断。"
                  % (r["coverage"] * 100, MIN_COVERAGE * 100))
        print()

    blocked = [r["dimension"] for r in rows if not r["conclusive"]]
    if blocked:
        print("  不可下结论的维度: %s" % ", ".join(blocked))
    else:
        print("  所有维度覆盖率均 >= %.0f%%,结论可下。" % (MIN_COVERAGE * 100))

    if data.get("notes"):
        print()
        print("  运行备注")
        print("  " + "-" * 96)
        for n in data["notes"]:
            print("    - %s" % n)
    print("=" * 100)

    if args.json:
        payload = {
            "input": os.path.abspath(args.inp),
            "a": a_arm["key"], "b": b_arm["key"],
            "min_coverage": MIN_COVERAGE,
            "dimensions": [{k: v for k, v in r.items() if k != "ids"} for r in rows],
            "speed": {"a": sa, "b": sb},
            "cost": {"a": ca, "b": cb, "currency": cur},
            "blocked_dimensions": blocked,
        }
        print(NL + json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
