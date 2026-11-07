# -*- coding: utf-8 -*-
"""arena_cli.py - the single command surface shared by the desktop app, the DSH
plugin and a human at a terminal.

One entry point instead of many, because the plugin and the desktop UI must show
the SAME numbers. When each grew its own formatting, the two drifted and it became
unclear which one was right.

Every subcommand is read-only unless its name says otherwise. `run` prints the
command it would execute and refuses to start without --yes, so a tool that can
occupy the GPU for hours is never triggered by an accidental call.

Usage:
  python runner/arena_cli.py status
  python runner/arena_cli.py compare --a deepseek-flash --b Qwen3.6-35B-APEX
  python runner/arena_cli.py launch [--profile apex-256k]
  python runner/arena_cli.py sweep [--stage 0]
  python runner/arena_cli.py run --suite coarse [--yes]
  python runner/arena_cli.py env
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
RESULTS = os.path.join(ROOT, "results")


# ---------------------------------------------------------------- helpers
def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _summary_of(path):
    try:
        d = _load(path)
    except Exception:
        return None
    s = d.get("summary") or d.get("level") or {}
    return s or None


def _latest_per_model(level=None):
    """Newest LEVEL run per model. Old runs are kept on disk but must not be what a
    status query reports, or the answer depends on file ordering."""
    best = {}
    for p in glob.glob(os.path.join(RESULTS, "LEVEL_*.json")):
        s = _summary_of(p)
        if not s:
            continue
        if level and s.get("level") != level:
            continue
        model = s.get("model") or "?"
        stamp = os.path.getmtime(p)
        if model not in best or stamp > best[model][0]:
            best[model] = (stamp, p, s)
    return best


# ---------------------------------------------------------------- commands
def cmd_status(args):
    lb = os.path.join(RESULTS, "leaderboard.json")
    runs = _latest_per_model()
    lines = ["Model Arena 状态", "=" * 46, ""]
    lines.append("结果库: %d 次运行(%d 个模型有最新记录)"
                 % (len(glob.glob(os.path.join(RESULTS, "LEVEL_*.json"))), len(runs)))
    lines.append("")

    if os.path.exists(lb):
        try:
            board = _load(lb)
        except Exception:
            board = None
        if board and board.get("runs"):
            lines.append("模型                    覆盖   加权   可靠性   实战   耗时")
            lines.append("-" * 62)
            for r in board["runs"]:
                dims = r.get("dims") or {}
                cov = len(dims) / 8.0
                idx = r.get("index")
                def g(d):
                    v = dims.get(d)
                    return ("%.0f%%" % (v["acc"] * 100)) if v else "—"
                lines.append("%-22s %4.0f%%  %6s  %6s  %6s  %6s"
                             % (r.get("model", "?")[:22], cov * 100,
                                ("%.1f" % (idx * 100)) if idx else "—",
                                g("D8"), g("D9"),
                                ("%.0fs" % r["elapsed_s"]) if r.get("elapsed_s") else "—"))
            lines.append("")
            lines.append("注:加权分使用自设计权重,仅作参考。判断强弱请看 arena_compare 的配对结果。")
    else:
        lines.append("尚无 leaderboard.json,先运行 build_report。")
        lines.append("")
        for m, (_, p, s) in sorted(runs.items()):
            lines.append("  %-24s %s  %.1f%%" % (m[:24], s.get("level"), (s.get("accuracy") or 0) * 100))

    # environment trust of the newest run
    newest = sorted(runs.values(), key=lambda x: x[0])[-1:] if runs else []
    if newest:
        s = newest[0][2]
        env = s.get("environment") or {}
        lines.append("")
        lines.append("最近一次运行的环境信任度: %s" % (env.get("trust_level") or "未记录"))
        for w in (env.get("warnings") or [])[:2]:
            lines.append("  ⚠ %s" % w)
    return NL.join(lines)


def cmd_compare(args):
    """Delegate to compare_models so there is exactly one implementation of the
    paired test; a second copy here would eventually disagree with it."""
    import subprocess
    argv = [sys.executable, os.path.join(HERE, "compare_models.py"),
            "--a", args.a, "--b", args.b]
    if args.level:
        argv += ["--level", args.level]
    r = subprocess.run(argv, capture_output=True, text=True, errors="ignore")
    return (r.stdout or "") + (NL + r.stderr if r.stderr else "")


def cmd_launch(args):
    from profiles import load_store
    store = load_store()
    lines = []

    if args.profile:
        p = store["profiles"].get(args.profile)
        if not p:
            return "无此配置档: %s%s可用: %s" % (args.profile, NL, ", ".join(store["profiles"]) or "(无)")
        lines.append("配置档: %s" % args.profile)
        lines.append("-" * 46)
        lines.append("模型: %s" % os.path.basename(p.get("model") or "?"))
        lines.append("视觉: %s" % ("有 mmproj" if p.get("mmproj") else "无"))
        lines.append("exe:  %s" % p.get("exe"))
        lines.append("")
        lines.append("可扫描变量:")
        for k, v in sorted((p.get("vars") or {}).items()):
            lines.append("  %-20s %s" % (k, v))
        lines.append("")
        lines.append("完整命令行:")
        lines.append("  " + (p.get("raw") or "(未记录)"))
        return NL.join(lines)

    # newest run that captured a launch snapshot
    best = None
    for p in glob.glob(os.path.join(RESULTS, "LEVEL_*.json")):
        s = _summary_of(p)
        if not s:
            continue
        procs = ((s.get("launch") or {}).get("processes") or [])
        if procs and procs[0].get("raw"):
            t = os.path.getmtime(p)
            if not best or t > best[0]:
                best = (t, p, s, procs[0])
    if best:
        _, p, s, proc = best
        lines.append("最近一次捕获到启动参数的运行: %s" % os.path.basename(p))
        lines.append("模型: %s | 级别: %s" % (s.get("model"), s.get("level")))
        lines.append("-" * 46)
        dv = proc.get("derived") or {}
        for k, v in dv.items():
            lines.append("  %-14s %s" % (k, v))
        lines.append("")
        lines.append("完整启动命令行(%d 字符):" % len(proc.get("raw") or ""))
        lines.append("  " + (proc.get("raw") or ""))
    else:
        lines.append("尚无运行捕获到启动命令行。")
        lines.append("启动本地模型后跑一次评测即可自动捕获。")

    if store["profiles"]:
        lines.append("")
        lines.append("已保存的配置档:")
        for n, pr in sorted(store["profiles"].items()):
            lines.append("  %-20s ctx=%s" % (n, (pr.get("vars") or {}).get("--ctx-size", "?")))
    return NL.join(lines)


def cmd_sweep(args):
    import subprocess
    argv = [sys.executable, os.path.join(HERE, "sweep.py")]
    if args.stage is not None:
        argv += ["--stage", str(args.stage)]
    r = subprocess.run(argv, capture_output=True, text=True, errors="ignore")
    out = (r.stdout or "") + (NL + r.stderr if r.stderr else "")
    # append the funnel cost, which is what makes the plan actionable
    try:
        r2 = subprocess.run([sys.executable, os.path.join(HERE, "sweep_stages.py"), "--stages"],
                            capture_output=True, text=True, errors="ignore")
        if r2.stdout:
            out += NL + r2.stdout.split("按当前方案")[-1].join(["按当前方案", ""])
    except Exception:
        pass
    return out


def cmd_env(args):
    import environment as envmod
    e = envmod.assess()
    lines = ["当前环境", "=" * 46]
    lines.append("信任度: %s" % e.get("trust_level"))
    for r in e.get("reasons") or []:
        lines.append("  理由: %s" % r)
    for w in e.get("warnings") or []:
        lines.append("  ⚠ %s" % w)
    g = e.get("gpu") or {}
    if g:
        lines.append("")
        lines.append("GPU: 使用率 %s%% | 显存 %s/%s MiB | 温度 %s°C | 功耗 %s W"
                     % (g.get("util_pct"), g.get("mem_used_mib"), g.get("mem_total_mib"),
                        g.get("temp_c"), g.get("power_w")))
    so = e.get("self_overhead") or {}
    if so:
        lines.append("本项目自身开销: CPU %ss | 内存 %s MiB"
                     % (so.get("runner_cpu_s"), so.get("runner_rss_mib")))
    lines.append("")
    lines.append("干扰只记录不阻断:trust_level 表示该次测量的可信度,不表示结果无效。")
    return NL.join(lines)


def cmd_run(args):
    argv = [sys.executable, os.path.join(HERE, "run.py"),
            "--suite", args.suite, "--endpoint", args.endpoint]
    if args.agent_mode:
        argv += ["--agent-mode", args.agent_mode]
    cmdline = " ".join(('"%s"' % a if " " in a else a) for a in argv)
    if not args.yes:
        return NL.join([
            "未执行 —— 评测会占用 GPU 且耗时较长。",
            "",
            "将运行的命令:",
            "  " + cmdline,
            "",
            "确认后加 --yes 执行。",
        ])
    import subprocess
    print("运行: " + cmdline, flush=True)
    r = subprocess.run(argv, cwd=ROOT)
    return "退出码: %d" % r.returncode


def cmd_cost(args):
    """What a full run would cost, before any money is spent."""
    import cost as C
    s = C.summary()
    lines = ["%s %s %s %s %s" % ("维度".ljust(8), "题数".rjust(6),
                                 "输入token".rjust(12), "输出token".rjust(10),
                                 "成本元".rjust(8))]
    for r in s["rows"]:
        lines.append("%s %s %s %s %s" % (
            r["dim"].ljust(8), str(r["n"]).rjust(6),
            str(r["in_tokens"]).rjust(12), str(r["out_tokens"]).rjust(10),
            ("%.2f" % r["cost_cny"]).rjust(8)))
    lines.append("-" * 48)
    lines.append("%s %s %s" % ("合计".ljust(8), str(s["total_n"]).rjust(6),
                               ("%.2f 元" % s["total_cost_cny"]).rjust(30)))
    lines.append("")
    lines.append("不含 D7: %d 题, %.2f 元" % (s["without_d7_n"], s["without_d7_cost_cny"]))
    lines.append("仅 D7  : %.2f 元（长上下文是成本主因）" % s["d7_cost_cny"])
    lines.append("")
    lines.append("提示: D7 是 120 题却占九成成本，日常评测可以不带它。")
    return chr(10).join(lines)


def cmd_tui(args):
    """Delegate to arena_tui so the dashboard shares one implementation."""
    import arena_tui
    argv = []
    if getattr(args, "once", False):
        argv.append("--once")
    if getattr(args, "plain", False):
        argv.append("--plain")
    old = sys.argv
    try:
        sys.argv = ["arena_tui"] + argv
        arena_tui.main()
    finally:
        sys.argv = old
    return ""


def main():
    ap = argparse.ArgumentParser(description="Model Arena 统一命令行")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="已评测的模型与结果概览")

    c = sub.add_parser("compare", help="两个模型的配对比较")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--level", default="canary")

    l = sub.add_parser("launch", help="启动参数快照")
    l.add_argument("--profile", default="")

    s = sub.add_parser("sweep", help="参数扫描计划(不执行)")
    s.add_argument("--stage", default="0")

    sub.add_parser("env", help="当前机器干扰状况")

    sub.add_parser("cost", help="跑之前先看要花多少钱(云端)")

    t = sub.add_parser("tui", help="终端仪表盘(看运行进度与最近结果)")
    t.add_argument("--once", action="store_true", help="只渲染一帧")
    t.add_argument("--plain", action="store_true", help="无 ANSI，追加输出")

    r = sub.add_parser("run", help="发起评测(默认只打印命令)")
    r.add_argument("--suite", required=True)
    r.add_argument("--endpoint", default="local")
    r.add_argument("--agent-mode", default="")
    r.add_argument("--yes", action="store_true")

    args = ap.parse_args()
    if not args.cmd:
        print(__doc__)
        return

    fn = {"status": cmd_status, "compare": cmd_compare, "launch": cmd_launch,
          "sweep": cmd_sweep, "env": cmd_env, "run": cmd_run, "tui": cmd_tui,
          "cost": cmd_cost}[args.cmd]
    print(fn(args))


if __name__ == "__main__":
    main()
