#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键跑完所有维度,再自动出报告与榜单。

为什么要有它:一次完整评测是 11 条独立命令(endpoint/suite/preset-id 各写一遍),
漏一条就是漏一个维度,而且漏了不会报错 —— 只是榜单上那一格是「—」,
看起来像「这个模型没这项数据」,实际是「没人跑过」。

用法:
    python runner/run_all.py --endpoint kvmem --preset-id KV17-Bonsai2T5-200K
    python runner/run_all.py --endpoint kvmem --preset-id X --only D2,D3     # 只跑指定维度
    python runner/run_all.py --endpoint kvmem --preset-id X --skip D7        # 跳过指定维度
    python runner/run_all.py --endpoint kvmem --preset-id X --dry            # 只打印计划

它做什么:
  1. 按固定顺序逐维度调 run.py(子进程,一个维度失败不影响后面的)
  2. 每个维度读回结果 sumary,汇总题数/正确率/跳过数/耗时
  3. 全部跑完自动 build_report + leaderboard
  4. 打印一张汇总表,跳过的维度会写明原因

它不做什么:
  * 不替你启动模型服务。引擎要先自己起好(run.py 会做 health 检查)。
  * 不估算任何数字。读不到就写「—」。
"""
import argparse
import json
import os
import subprocess
import sys
import time

# Windows 控制台是 GBK:子进程输出里一旦出现无法解码的字节(我们用了
# errors="replace",会产生 U+FFFD),print 就会抛 UnicodeEncodeError 把整轮跑测带崩。
# 实测:第 7 个维度打印跳过原因时就崩了,后面 3 个维度全没跑。
# 这里把标准输出切成 UTF-8 并兜底,写不出就跳过那一行,绝不让打印杀死跑测。
NL = chr(10)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _say(text):
    """打印一行,任何编码问题都不许把进程带走,也不许静默吞掉输出。

    踩过的坑(两次):一次是 GBK 控制台遇到 U+FFFD 直接抛 UnicodeEncodeError
    把整轮跑测带崩;另一次是兜底写进了同一个坏掉的流,于是"输出明明有却看不见"。
    这里三级兜底:print -> sys.stderr -> 直接写 fd。
    """
    try:
        print(text)
        return
    except Exception:
        pass
    try:
        sys.stderr.write(str(text) + NL)
        sys.stderr.flush()
        return
    except Exception:
        pass
    try:
        os.write(2, (str(text) + NL).encode("utf-8", "replace"))
    except Exception:
        pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "runner", "run.py")
RESULTS = os.path.join(ROOT, "results")

# 维度顺序:先快后慢。理由——跑错配置时能早发现(前几个维度几秒就出结果),
# 不至于等了两小时才发现 endpoint 写错了。
ORDER = [
    ("code",        "D4",  "代码"),
    ("reliability", "D8",  "抗幻觉"),
    ("instruction", "D5",  "指令"),
    ("agent",       "D6",  "工具"),
    ("chess",       "D10", "棋类"),
    ("math",        "D3",  "数学"),
    ("realworld",   "D9",  "实战"),
    ("mcq",         "D2",  "选择"),
    ("longctx",     "D7",  "长上下文"),
    ("vision",      "D11", "视觉"),
    ("compression", "D12", "压缩"),
    ("writing",     "D13", "文笔"),
]


def _latest_result(dim):
    """找该维度最新的一份结果文件,读回 summary。没跑出文件就返回 None。"""
    import glob
    pats = [os.path.join(RESULTS, "%s_*.json" % dim),
            os.path.join(RESULTS, "*_%s_*.json" % dim)]
    cand = []
    for pat in pats:
        cand.extend(glob.glob(pat))
    cand = [p for p in cand if "refill" not in p and "retry" not in p]
    if not cand:
        return None
    newest = max(cand, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8") as f:
            return json.load(f).get("summary")
    except Exception:
        return None


def run_one(suite, endpoint, preset_id, extra):
    """跑一个维度。返回 (ok, summary, elapsed, tail_output)。"""
    cmd = [sys.executable, "-u", RUN,
           "--endpoint", endpoint, "--suite", suite,
           "--preset-id", preset_id, "--yes", "--skip-vram-check"]
    cmd.extend(extra)
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        ok = p.returncode == 0
    except Exception as e:
        out, ok = "spawn failed: %s" % e, False
    return ok, (time.perf_counter() - t0), out


def main():
    ap = argparse.ArgumentParser(description="跑完所有维度再出报告与榜单")
    ap.add_argument("--endpoint", required=True, help="config/endpoints.json 里的端点名")
    ap.add_argument("--preset-id", required=True, help="结果里记的预设名")
    ap.add_argument("--only", default="", help="只跑这些维度(套件名,逗号分隔)")
    ap.add_argument("--skip", default="", help="跳过这些维度(套件名,逗号分隔)")
    ap.add_argument("--limit", type=int, default=0, help="每维度只跑前 N 题(冒烟用)")
    ap.add_argument("--dry", action="store_true", help="只打印计划,不真跑")
    ap.add_argument("--no-report", action="store_true", help="跑完不出报告")
    args = ap.parse_args()

    only = set(x.strip() for x in args.only.split(",") if x.strip())
    skip = set(x.strip() for x in args.skip.split(",") if x.strip())

    plan = []
    for suite, dim, label in ORDER:
        if only and suite not in only:
            continue
        if suite in skip:
            continue
        plan.append((suite, dim, label))

    _say("=" * 66)
    _say(" 一键评测  endpoint=%s  preset=%s" % (args.endpoint, args.preset_id))
    _say(" 计划 %d 个维度: %s" % (len(plan), ", ".join(d for _, d, _ in plan)))
    if args.limit:
        _say(" 每维度限 %d 题(冒烟模式,结果不能当正式分数)" % args.limit)
    _say("=" * 66)

    if args.dry:
        for suite, dim, label in plan:
            _say("  %-4s %-12s %s" % (dim, suite, label))
        return 0

    extra = []
    if args.limit:
        extra = ["--limit", str(args.limit)]

    rows = []
    t_all = time.perf_counter()
    for i, (suite, dim, label) in enumerate(plan, 1):
        _say('')
        _say("--- [%d/%d] %s %s ---" % (i, len(plan), dim, label))
        ok, dt, out = run_one(suite, args.endpoint, args.preset_id, extra)
        s = _latest_result(dim)

        n = (s or {}).get("n") or 0
        # 不同维度的 summary 字段不一样,逐个兜底:
        #   常规维度用 accuracy_valid / accuracy
        #   D12(压缩)用 baseline_acc(压缩前) — 它的"能力"体现在这里,
        #     compacted_acc 是压缩后的,拿它当主分会让 D12 在榜上偏低。
        acc = (s or {}).get("accuracy_valid")
        if acc is None:
            acc = (s or {}).get("accuracy")
        if acc is None:
            acc = (s or {}).get("baseline_acc")
        if acc is None:
            acc = (s or {}).get("retention")
        sk = (s or {}).get("skipped") or 0
        err = (s or {}).get("n_errors") or (s or {}).get("errors") or 0

        # 判定顺序很重要:先看"整维跳过"(那是设计内的,不是失败),
        # 再看进程返回码,最后才看题数。原来只判 ok,n 为 0 也算成功,
        # 于是 D12 明明没出分却计入"有可判分结果"。
        if (s or {}).get("skipped") and not n:
            note = "整维跳过: " + ((s or {}).get("skip_reason") or "该端点不具备此能力")
            ok = True                      # 跳过是预期行为,不算失败
        elif not ok:
            note = "命令失败,见下方输出尾部"
        elif n == 0:
            note = "没跑出可判分的题"
            ok = False
        elif acc is None:
            note = "有题但算不出准确率"
            ok = False
        else:
            note = ""

        rows.append({"dim": dim, "suite": suite, "label": label, "ok": ok,
                     "n": n, "acc": acc, "skipped": sk, "errors": err,
                     "elapsed": dt, "note": note})

        if note:
            _say("    ! %s" % note)
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
            for ln in tail:
                _say("      " + ln[:100])
        else:
            _say("    %d 题 正确率 %.1f%%%s  %.0fs" % (
                n, (acc or 0) * 100,
                ("  跳过 %d" % sk) if sk else "", dt))

    total = time.perf_counter() - t_all

    _say('')
    _say("=" * 66)
    _say(" 汇总  (总耗时 %.1f 分钟)" % (total / 60))
    _say("=" * 66)
    _say(" %-4s %-12s %6s %8s %6s %8s  %s" % ("维度", "套件", "题数", "正确率", "跳过", "耗时s", "备注"))
    good = 0
    for r in rows:
        accs = "—" if r["acc"] is None else "%.1f%%" % (r["acc"] * 100)
        _say(" %-4s %-12s %6d %8s %6d %8.0f  %s" % (
            r["dim"], r["suite"], r["n"], accs, r["skipped"], r["elapsed"], r["note"][:40]))
        if r["n"] > 0:
            good += 1
    _say('')
    _say(" %d/%d 个维度出了可判分的结果" % (good, len(rows)))

    if not args.no_report:
        _say('')
        _say("--- 出报告与榜单 ---")
        for script in ("build_report.py", "batch_report.py"):
            path = os.path.join(ROOT, "runner", script)
            if not os.path.exists(path):
                continue
            extra_args = ["--out", "results/batch_report.md", "--max-lines", "2000"] \
                if script == "batch_report.py" else []
            p = subprocess.run([sys.executable, path] + extra_args,
                               cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            _say("  %-20s %s" % (script, "OK" if p.returncode == 0 else "FAILED"))
            if p.returncode != 0:
                _say("    " + ((p.stderr or p.stdout or "").strip().splitlines() or [""])[-1][:100])

    if any(r["note"] and not r["ok"] for r in rows):
        _say('')
        _say("有维度执行失败。它们的结果没有入库,榜单上对应格子会是「—」。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())