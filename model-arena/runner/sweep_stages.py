# -*- coding: utf-8 -*-
"""sweep_stages.py - the funnel scheduler for parameter optimisation.

Design rule that drives everything here: run the CHEAP tests first, and let them
delete options before the expensive ones run. Measured costs on this machine:

    speed probe            30 s per combo    (A class: throughput only)
    quality coarse (20 q)  56 s per combo    (B class: bare mode)
    cross dual            ~7 min per combo   (C class: throughput AND questions)
    agent coarse (20 q)   ~16 min per combo  (85k of tool definitions in the prompt)
    agent fine (137 q)   ~112 min per combo

A single question costs 2.8 s bare but 48.9 s with 159 tool definitions in the
prompt, because prefill dominates. That 17x gap is why agent-mode validation is
staged last and only ever runs on survivors.

The scheduler produces a PLAN and the exact commands, but executes nothing unless
asked: the user develops first and runs later.
"""
import json
import os
import re

NL = chr(10)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Costs in seconds, all measured (see module docstring).
COST = {
    "speed_probe": 30,
    "coarse_bare": 56,           # 20 questions x 2.8 s
    "fine_bare": 384,            # 137 questions x 2.8 s
    "cross_dual": 414,           # 30 s probe + 384 s fine
    "coarse_agent": 978,         # 20 x 48.9 (agent-real)
    "fine_agent": 6699,          # 137 x 48.9
}

# Stage ladder. Each stage declares what it needs and what it may eliminate, so the
# scheduler can reason about ordering instead of hard-coding a sequence.
STAGES = [
    {"id": "0", "name": "约束筛除", "cost": 0, "input": "space",
     "eliminates": "物理不可行",
     "why": "纯算术,零成本 —— 装不下的组合根本没资格进测试"},
    {"id": "1a", "name": "速度扫描", "cost": COST["speed_probe"], "input": "survivors",
     "eliminates": "慢到不可用的配置",
     "why": "30 秒就能测出一个配置的吞吐,没必要为它跑题本"},
    {"id": "1b", "name": "质量粗筛", "cost": COST["coarse_bare"], "input": "survivors",
     "eliminates": "对质量无影响的参数",
     "why": "20 道高敏感题 56 秒;差异 < 5pp 的变量直接出局"},
    {"id": "2", "name": "交叉验证", "cost": COST["cross_dual"], "input": "quality+cross",
     "eliminates": "伪权衡(看似省显存实则降智)",
     "why": "交叉类必须双向测:速度与质量一起量,才能画出权衡曲线"},
    {"id": "3", "name": "Agent 模式验证", "cost": COST["coarse_agent"], "input": "survivors",
     "eliminates": "在真实工具负载下降智的配置",
     "why": "85k 工具定义让单题从 2.8s 变 48.9s;真实体验与裸测可能不同"},
    {"id": "3f", "name": "Agent 精测", "cost": COST["fine_agent"], "input": "top-2",
     "eliminates": "最终排序",
     "why": "最贵的一步,只给最后的候选"},
]


def estimate_plan(plan, stages=None, defaults=None):
    """Wall-clock for the funnel.

    Each stage only processes the combos whose VARIED variable belongs to that
    stage's class. Running the whole 16-combo list through the cross stage would
    pay 7 minutes each to re-measure temperature, which the coarse stage already
    answered for 56 seconds.
    """
    import sweep as _sw
    st = stages or STAGES
    defaults = defaults or {}
    cls_of = {"1a": "speed", "1b": "quality", "2": "cross"}

    def belongs(combo, stage_id):
        cls = cls_of.get(stage_id)
        if cls is None:
            return True
        varied = combo.get("_varies")
        spec = _sw.PARAMS.get(varied, {})
        return spec.get("affects") == cls

    rows, total = [], 0.0
    for s in st:
        if s["id"] == "0":
            n = len(plan)
        elif s["id"] == "3":
            n = max(3, len([c for c in plan
                            if _sw.PARAMS.get(c.get("_varies"), {}).get("affects")
                            in ("cross", "quality")]) // 3)
        elif s["id"] == "3f":
            n = 2
        else:
            n = len([c for c in plan if belongs(c, s["id"])])
            if s["id"] == "1a":
                n = max(n, 1)
        cost = s["cost"] * n
        total += cost
        rows.append({"stage": s["id"], "name": s["name"], "n": n,
                     "per_combo_s": s["cost"], "total_s": round(cost),
                     "total_h": round(cost / 3600, 2)})
    return {"rows": rows, "total_s": round(total), "total_h": round(total / 3600, 2)}


def llamarig_baseline(cfg):
    """Convert a Llama-Rig config into the sweep's BASELINE (role -> value).

    Llama-Rig configs are REFERENCE material, not the measurement itself. They tell
    the sweep what the user actually runs, so the sweep can vary one flag around it
    and re-measure - rather than trusting a comment that says "equivalent quality".
    The conclusions in those comments have not been through a paired test.
    """
    return dict(cfg.get("roles") or {})


def commands_from_llamarig(cfgs, stage_id, endpoint="local", out_dir=None):
    """Emit commands that re-measure each Llama-Rig config as a baseline.

    Each config seeds its own OFAT sweep, so the final artefact is a measured run
    per config plus the variants around it - not a list of commands that assume the
    config's own notes are correct.
    """
    cmds = []
    for c in cfgs:
        label = re.sub(r"[^A-Za-z0-9._+-]", "", c["name"]).lstrip("-") or "config"
        if stage_id == "1a":
            cmd = "python runner/speed_probe.py --endpoint %s --tag %s" % (endpoint, label)
        else:
            cmd = "python runner/run.py --endpoint %s --suite %s --tag %s" % (
                endpoint, "coarse" if stage_id == "1b" else "canary", label)
        cmds.append({"label": label, "engine": c.get("engine"),
                     "from_config": c.get("path"), "cmd": cmd,
                     "out": os.path.join(out_dir or "", label + ".json")})
    return cmds


def plan_from_reference(cfg, engine=None):
    """Build an OFAT sweep that uses a reference config as its baseline.

    The reference tells the sweep WHAT the user runs; it does not certify the result.
    So the plan is still OFAT around it, and every variant (including the baseline
    itself) gets measured before any conclusion is drawn.
    """
    import sweep as _sw
    eng = engine or cfg.get("engine") or "llama.cpp"
    return _sw.build_plan(base=dict(cfg.get("roles") or {})), eng


def profile_for_engine(profile, engine):
    """Adapt a launch profile to an engine's vocabulary.

    A profile imported from a llama.cpp launcher carries --ctx-size / -ctk / -fa.
    Handing those to ninfer produces a command the server ignores, and the run is
    then recorded under a configuration that never took effect. Translation happens
    once, here, and unsupported roles are reported.
    """
    import engine_profiles as ep
    values = {}
    table = ep.resolve(engine)
    src_vars = (profile or {}).get("vars") or {}
    # map the profile's own flags onto roles by matching the engine tables
    for eng_name in ("llama.cpp", "kvmem", "ninfer"):
        etable = ep.resolve(eng_name)
        for role, spec in etable.items():
            fl = spec.get("flag")
            if isinstance(fl, list):
                continue
            if fl and fl in src_vars:
                values.setdefault(role, src_vars[fl])
    if profile.get("model"):
        values["model"] = profile["model"]
    bad = ep.unsupported(engine, list(values))
    for b in bad:
        values.pop(b, None)
    return values, bad


def stage_commands(plan, sweep, stage_id, endpoint="local", out_dir=None, engine="llama.cpp"):
    """Emit the concrete commands for one stage. Never runs them.

    speed  -> runner/speed_probe.py (throughput only)
    others -> runner/run.py with --suite probe/coarse/fine
    """
    out_dir = out_dir or os.path.join(ROOT, "results", "sweep", stage_id)
    cmds = []
    # Only propose combos this engine can actually express. Without this filter the
    # ninfer plan still contained ctk/ctv variants, which ninfer has no flag for -
    # the commands would run and quietly measure a configuration the engine ignored.
    try:
        import sweep as _sw
        allowed, _dropped = _sw.params_for_engine(engine)
        allowed_keys = set(allowed)
    except Exception:
        allowed_keys = None
    for c in plan:
        if allowed_keys is not None and c.get("_varies") not in allowed_keys:
            continue
        # Label from the VARIED variable plus its value, not from describe(): the
        # latter only lists quality-class flags, so every speed-class combo produced
        # the same label and the outputs overwrote each other.
        varied = c.get("_varies") or "base"
        val = c.get(varied)
        if isinstance(val, bool) or val is None:
            val = "on" if val else "off"
        raw = "%s_%s" % (varied, val)
        # strip leading dashes: a tag starting with "--" is parsed as an option
        label = re.sub(r"[^A-Za-z0-9._+-]", "", raw).lstrip("-").replace("--", "-")
        if not label:
            label = "baseline"
        if stage_id == "1a":
            cmds.append({"combo": c, "label": label, "engine": engine,
                         "cmd": "python runner/speed_probe.py --endpoint %s --tag %s"
                                % (endpoint, label),
                         "out": os.path.join(out_dir, label + ".json")})
        elif stage_id == "1b":
            cmds.append({"combo": c, "label": label, "engine": engine,
                         "cmd": "python runner/run.py --endpoint %s --suite coarse --tag %s"
                                % (endpoint, label),
                         "out": os.path.join(out_dir, "LEVEL_coarse_%s.json" % label)})
        elif stage_id == "2":
            cmds.append({"combo": c, "label": label,
                         "cmd": ("python runner/speed_probe.py --endpoint %s --tag %s "
                                 "&& python runner/run.py --endpoint %s --suite canary --tag %s"
                                 % (endpoint, label, endpoint, label)),
                         "out": os.path.join(out_dir, label + "+canary.json")})
        elif stage_id == "3":
            cmds.append({"combo": c, "label": label,
                         "cmd": "python runner/run.py --endpoint %s --suite agent-probe --tag %s"
                                % (endpoint, label),
                         "out": os.path.join(out_dir, "agent_%s.json" % label)})
    return cmds


def ordering_rationale():
    """Human-readable justification for the stage order, so it can be reviewed
    rather than trusted."""
    out = []
    out.append("排序原则:先用便宜测试砍掉选项,贵的只测幸存者。")
    out.append("")
    for s in STAGES:
        per = s["cost"]
        unit = ("%.0f 秒" % per) if per < 60 else ("%.1f 分" % (per / 60))
        out.append("  %-4s %-16s 单组 %-8s -> %s"
                   % (s["id"], s["name"], unit, s["eliminates"]))
        out.append("       %s" % s["why"])
    return NL.join(out)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", action="store_true", help="show the funnel and its costs")
    ap.add_argument("--commands", default="", help="emit commands for a stage id")
    ap.add_argument("--survivors", type=int, default=16)
    ap.add_argument("--json", default="")
    ap.add_argument("--from-llamarig", action="store_true",
                    help="把 Llama-Rig 配置作为「参考基线」,仍然按 OFAT 逻辑重测一轮")
    ap.add_argument("--reference", default="",
                    help="以某个 Llama-Rig 配置名作基线(如 L2-Ornith-ncmoe36),生成围绕它的 OFAT 扫描")
    ap.add_argument("--engine", default="llama.cpp",
                    help="目标引擎:llama.cpp | kvmem | ninfer | ninfer+kvmem")
    args = ap.parse_args()

    import sys
    sys.path.insert(0, HERE)
    import sweep

    # A reference config seeds the baseline. The sweep still runs OFAT around it and
    # re-measures, because the config's own notes are not a paired test.
    ref_cfg = None
    if args.reference:
        import llamarig as lr
        inst = lr.find_install()
        if not inst:
            print("未找到 Llama-Rig,无法按名称取参考配置")
            return 2
        cfgs = [lr.load_config(p) for p in lr.list_configs(inst[0])]
        hit = [c for c in cfgs if c["name"] == args.reference]
        if not hit:
            print("无此参考配置: %s" % args.reference)
            print("可用(前 8): %s" % ", ".join(c["name"] for c in cfgs[:8]))
            return 2
        ref_cfg = hit[0]
        print("参考基线: %s (引擎 %s)" % (ref_cfg["name"], ref_cfg["engine"]))
        if args.engine == "llama.cpp" and ref_cfg["engine"] != "llama.cpp":
            args.engine = ref_cfg["engine"]
            print("  已按参考配置切换引擎: %s" % args.engine)

    plan = sweep.build_plan(base=dict(ref_cfg["roles"]) if ref_cfg else None,
                            model_path=(ref_cfg.get("model") if ref_cfg else None),
                            mmproj_path=(ref_cfg.get("mmproj") if ref_cfg else None))
    combos = plan["plan"]
    est = estimate_plan(combos)

    if args.stages or not args.commands:
        print("=" * 70)
        print("  参数优化漏斗 · 调度计划")
        print("=" * 70)
        print(ordering_rationale())
        print()
        print("  按当前方案(%d 组)的耗时(每阶段只处理属于它的变量):" % len(combos))
        print("  %-6s %-18s %5s %10s %10s" % ("阶段", "名称", "组数", "单组", "小计"))
        print("  " + "-" * 58)
        for r in est["rows"]:
            per = ("%.0fs" % r["per_combo_s"]) if r["per_combo_s"] < 60 \
                else ("%.1f分" % (r["per_combo_s"] / 60))
            tot = ("%.0fs" % r["total_s"]) if r["total_s"] < 60 \
                else ("%.1f分" % (r["total_s"] / 60)) if r["total_s"] < 3600 \
                else ("%.1fh" % r["total_h"])
            print("  %-6s %-18s %5d %10s %10s"
                  % (r["stage"], r["name"], r["n"], per, tot))
        print("  " + "-" * 58)
        print("  合计 ≈ %.1f 小时" % est["total_h"])
        print()
        print("  注意:前 4 个阶段共约 %.1f 小时,先拿到速度与质量的结论。"
              % sum(r["total_h"] for r in est["rows"] if r["stage"] in ("1a", "1b", "2")))
        print("       Agent 模式验证最贵,只给幸存者。")

    if args.commands:
        if args.from_llamarig:
            import llamarig as lr
            inst = lr.find_install()
            if not inst:
                print("未找到 Llama-Rig")
                return
            cfgs = [lr.load_config(p) for p in lr.list_configs(inst[0])]
            cmds = commands_from_llamarig(cfgs, args.commands, out_dir="results/sweep")
            print()
            print("=" * 70)
            print("  参考基线 = Llama-Rig 配置(%d 个),仍然重测" % len(cmds))
            print("=" * 70)
            for c in cmds[:20]:
                print("  [%s] %s" % (c["label"][:34], c["engine"]))
                print("      %s" % c["cmd"])
            if len(cmds) > 20:
                print("  ... 另有 %d 条" % (len(cmds) - 20))
            print()
            print("  参考配置只作为基线种子:它说明你在用什么,不代表结论已被验证。")
            print("  实际仍按 OFAT 逻辑扫描 + 配对检验,重测一轮后才出结论。")
            return
        cmds = stage_commands(combos, sweep, args.commands, engine=args.engine)
        print()
        print("=" * 70)
        print("  阶段 %s 的待执行命令(%d 条,未执行)" % (args.commands, len(cmds)))
        print("=" * 70)
        for c in cmds:
            print("  [%s]" % c["label"])
            print("      %s" % c["cmd"])
        print()
        print("  这些命令刻意不自动执行:开发阶段不跑真实评测。")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump([{k: v for k, v in c.items() if k != "combo"} for c in cmds],
                          f, ensure_ascii=False, indent=1)
            print("  -> %s" % args.json)


if __name__ == "__main__":
    main()
