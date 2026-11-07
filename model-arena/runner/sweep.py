# -*- coding: utf-8 -*-
"""sweep.py - parameter-sweep engine: find the launch flags that best fit THIS machine.

The project has two jobs:
  1. measure how capable a model is, on this machine, as configured
  2. find the launch configuration that fits this machine best

Job 2 is an optimisation problem, not a benchmarking one. The full cross-product of
the flags that plausibly affect quality is 768 combinations; at ~25 min per run that
is 13 days. So this module implements a four-stage funnel instead of a grid:

  stage 0  CONSTRAINT  768 -> ~40   pure arithmetic on VRAM/RAM, runs the model zero times
  stage 1  COARSE       ~40 -> ~10   20 high-sensitivity questions, ~3 min each
  stage 2  FINE         ~10 -> 3-5   137 questions + paired significance test
  stage 3  INTERACT    3-5 -> 1      combinations of the survivors only

Total ~9.5 h instead of ~320 h. The engine is part of the project, not a one-off
script: any future flag set is described declaratively and swept the same way.

Usage:
  python runner/sweep.py --plan                 show the generated plan, run nothing
  python runner/sweep.py --stage 0              resolve constraints only
  python runner/sweep.py --stage 1 --dry        show coarse commands
"""
import argparse
import itertools
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NL = chr(10)

# ---------------------------------------------------------------------------
# Machine budget. Values are for the user's laptop and are deliberately explicit:
# a sweep result is only meaningful against a stated budget.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Machine and model facts come from config/machine.json and config/models.json,
# written by `python runner/detect.py`. They used to be constants here, copied from
# the developer's laptop - which is not a default, it is a wrong answer for every
# other user: a 7B model would be sized as if it had 48 layers, and stage 0 would
# silently discard configurations that run perfectly well.
#
# The fallbacks are deliberately CONSERVATIVE so a missing config yields a plan that
# is too cautious rather than one promising more than the machine can do.
# ---------------------------------------------------------------------------
_FALLBACK_MACHINE = {
    "vram_mib": 8192,
    "ram_gib": 16.0,
    "usable_vram_ratio": 0.90,
    "usable_ram_ratio": 0.70,
}

_FALLBACK_MODEL = {
    "layers": 32,
    "kv_heads": 8,
    "head_dim": 128,
    "weights_mib": 8000,
    "mmproj_mib": 0,
    "_fallback": True,
}


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_machine():
    """Machine budget from config, falling back to a conservative guess."""
    cfg = _load_json(os.path.join(ROOT, "config", "machine.json"), {})
    m = dict(_FALLBACK_MACHINE)
    for k in ("vram_mib", "ram_gib", "usable_vram_ratio", "usable_ram_ratio"):
        if cfg.get(k):
            m[k] = cfg[k]
    m["_source"] = "config/machine.json" if cfg else "fallback(保守默认)"
    if cfg.get("gpus"):
        m["gpus"] = cfg["gpus"]
    return m


def load_model(name=None):
    """Model geometry from config. Picks the first entry when no name is given."""
    cfg = _load_json(os.path.join(ROOT, "config", "models.json"), {})
    if not cfg:
        g = dict(_FALLBACK_MODEL)
        g["_source"] = "fallback(保守默认) — 请运行 python runner/detect.py --write"
        return g
    g = dict(cfg[name]) if (name and name in cfg) else dict(next(iter(cfg.values())))
    for k, v in _FALLBACK_MODEL.items():
        if not g.get(k) and k != "_fallback":
            g[k] = v
            g.setdefault("_missing", []).append(k)
    g["_source"] = "config/models.json"
    return g


MACHINE = load_machine()
MODEL_GEO = load_model()

# ---------------------------------------------------------------------------
# Parameter space. "affects" classifies WHY a flag is in the sweep:
#   quality  - can change what the model outputs (the intelligence question)
#   speed    - changes only throughput
#   capacity - changes what fits, not how well it computes
# ---------------------------------------------------------------------------
PARAMS = {
    "-ctk/-ctv": {
        "flag": ["-ctk", "-ctv"],
        "role": "kv_k",
        "values": ["f16", "q8_0", "q4_0"],
        "affects": "cross",
        "dual": True,
        "why": "KV cache 量化:改数值精度的同时省显存,影响可用上下文",
        "kv_bits": {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5},
        "priority": 1,
    },
    "--temp": {
        "flag": ["--temp"],
        "role": "temp",
        "values": ["0.2", "0.6", "1.0", "1.4"],
        "affects": "quality",
        "why": "采样温度:改变输出分布,影响稳定性",
        "priority": 2,
    },
    "--spec-type": {
        "flag": ["--spec-type"],
        "role": "spec",
        "values": ["draft-mtp", "none"],
        "affects": "cross",
        "dual": True,
        "why": "MTP 投机解码:显著提速,但用草稿近似可能损质量",
        "priority": 3,
    },
    "-fa": {
        "flag": ["-fa"],
        "role": "flash_attn",
        "values": ["on", "off"],
        "affects": "cross",
        "dual": True,
        "why": "Flash Attention:主要提速,同时改变注意力数值路径",
        "priority": 4,
    },
    "--rope-scaling": {
        "flag": ["--rope-scaling"],
        "role": "rope_scaling",
        "values": ["default", "yarn"],
        "affects": "quality",
        "why": "长上下文外推策略:外推会降智",
        "only_if_ctx_gt": 32768,
        "priority": 5,
    },
    "--reasoning": {
        "flag": ["--reasoning"],
        "role": "reasoning",
        "values": ["auto", "off"],
        "affects": "quality",
        "why": "思考模式:影响推理类题目",
        "priority": 6,
    },
    "--ctx-size": {
        "flag": ["--ctx-size"],
        "role": "context",
        "values": ["32768", "65536", "131072", "256000"],
        "affects": "cross",
        "dual": True,
        "why": "上下文长度:决定容量,同时改变 RoPE 外推与 prefill 耗时",
        "priority": 7,
    },
    "--top-k": {
        "flag": ["--top-k"],
        "role": "top_k",
        "values": ["20", "40"],
        "affects": "quality",
        "why": "次要采样参数",
        "priority": 8,
    },
    # ---- speed-only flags: never enter the quality sweep, they are measured with
    # a 30-second throughput probe instead of a 25-minute question run ----
    "--threads": {
        "flag": ["--threads", "--threads-batch"],
        "role": "threads",
        "values": ["8", "12", "16"],
        "affects": "speed",
        "why": "CPU 线程数:只影响速度",
        "priority": 11,
    },
    "--batch-size": {
        "flag": ["--batch-size", "--ubatch-size"],
        "role": "batch",
        "values": ["1024", "2048", "4096"],
        "affects": "speed",
        "why": "批大小:影响吞吐与显存峰值",
        "priority": 12,
    },
    "-ngl": {
        "flag": ["-ngl"],
        "role": "gpu_layers",
        "values": ["99", "999"],
        "affects": "speed",
        "why": "GPU 卸载层数:影响速度与显存",
        "priority": 13,
    },
    # ---- roles that only appeared once Llama-Rig configs were used as baselines.
    # Without these, four of the values in the user's own reference config were
    # silently dropped, including cpu_moe_layers - which IS the variable their
    # L2-Ornith-ncmoe30/36/42 configs were built to compare. ----
    "-ncmoe": {
        "flag": ["--n-cpu-moe"],
        "role": "cpu_moe_layers",
        "values": ["24", "30", "36", "42"],
        "affects": "cross",
        "dual": True,
        "why": "MoE 专家层放 CPU 的层数:省显存,同时改变计算路径",
        "priority": 5,
    },
    "--min-p": {
        "flag": ["--min-p"],
        "role": "min_p",
        "values": ["0", "0.05", "0.1"],
        "affects": "quality",
        "why": "相对概率下限:截断低概率 token",
        "priority": 9,
    },
    "--top-p": {
        "flag": ["--top-p"],
        "role": "top_p",
        "values": ["0.9", "0.95", "1.0"],
        "affects": "quality",
        "why": "核采样阈值",
        "priority": 10,
    },
    "--reasoning-effort": {
        "flag": ["--reasoning-effort"],
        "role": "reasoning_effort",
        "values": ["none", "default"],
        "affects": "quality",
        "why": "思考力度:影响推理类题目的输出长度与深度",
        "priority": 6,
    },
}

# Two separate sweeps, because they cost three orders of magnitude apart:
#   quality -- ~25 min per combo (137 questions + judging)
#   speed   -- ~30 s  per combo (one fixed workload, throughput only)
# Mixing them means either paying 25 minutes to learn a thread count, or letting
# judge/disk work contaminate the timing.
# Three-way split of the parameter space, because the three kinds cost wildly
# different amounts to measure and must be ordered cheapest-first:
#   speed    only throughput changes      -> 30 s probe, never touches the question set
#   quality  only the output distribution -> question set, insensitive to load
#   cross    BOTH, in opposite directions -> needs a dual measurement, and only the
#            survivors of the cheap stages earn one
SWEEP_CLASSES = {
    "speed": {
        "per_combo_s": 30,
        "method": "throughput_probe",
        "note": "只测吞吐/TTFT;必须先做环境检查 —— 负载会直接让数字失真",
        "stage": "1a",
    },
    "quality": {
        "per_combo_s": 56,          # 20 题 × 2.8s(实测裸模式)
        "method": "question_set_bare",
        "note": "跑题本 + 判分;对机器负载不敏感,可与速度测交错",
        "stage": "1b",
    },
    "cross": {
        "per_combo_s": 414,         # 30s 速度 + 384s(137 题裸)
        "method": "dual_measure",
        "note": "速度与质量都要测:省了显存是否降智、提速是否损质",
        "stage": "2",
    },
}

# Measured on this machine (see the sweep notes): a single question costs 2.8 s bare
# but 48.9 s once 159 tool definitions are in the prompt, because prefill dominates.
# That 17x gap is why agent-mode validation runs LAST and only on survivors.
AGENT_MODES = {
    "bare":         {"tools": 0,   "prompt_tokens": 0,     "per_q_s": 2.8},
    "agent-small":  {"tools": 10,  "prompt_tokens": 5000,  "per_q_s": 8.0},
    "agent-mid":    {"tools": 50,  "prompt_tokens": 25000, "per_q_s": 25.0},
    "agent-real":   {"tools": 159, "prompt_tokens": 85357, "per_q_s": 48.9},
}

# The textbook KV formula overestimates by 2x on this model. Measured: 64k with
# q8_0 KV used 5239 MiB total, which after fixed overhead implies ~2951 MiB of KV
# against a predicted 6144 MiB. A wrong estimate does not merely mislead - it makes
# stage 0 drop combinations that actually run, so the estimate is calibrated against
# the machine instead of trusted.
# Measured on the real machine with the user's actual preset. The textbook formula
# is wrong by a factor that DRIFTS with context length, so a single calibration
# constant is not enough:
#    64k  predicted 6144 MiB, measured ~2951 MiB  (factor 0.48)
#   256k  predicted 24000 MiB, measured ~5595 MiB  (factor 0.23)
# The drift means part of the cache is not resident in VRAM at long contexts. A
# wrong estimate is not cosmetic: it makes stage 0 discard combinations that run,
# and it made 256k look impossible when the user runs it every day. So the table
# below is measured data, and interpolation fills the gaps.
# Optional per-machine KV measurements, produced by `python runner/calibrate.py`.
# The textbook formula overestimates (2x at 64k, 4x at 256k on the developer's
# machine, because part of the cache is not resident), so a calibrated table is
# better - but it is only valid for the machine that produced it. Without one the
# formula is used, which errs toward "this will not fit".
KV_MEASURED = {}
_kvm = _load_json(os.path.join(ROOT, "config", "kv_measured.json"), {})
if _kvm:
    try:
        KV_MEASURED = {int(k): v for k, v in _kvm.items() if not str(k).startswith("_")}
    except Exception:
        KV_MEASURED = {}


def kv_cache_mib(ctx, bits, geo=None, calibrated=True):
    """KV cache size, from measurement where available and the textbook formula
    (scaled by the nearest measured point) elsewhere."""
    g = geo or MODEL_GEO
    name = {2.0: "f16", 1.0: "q8_0", 0.5: "q4_0"}.get(float(bits))
    if calibrated and name:
        pts = [(c, v[name]) for c, v in sorted(KV_MEASURED.items())
               if v.get(name) is not None]
        if pts:
            if ctx <= pts[0][0]:
                return float(pts[0][1]) * ctx / pts[0][0]
            if ctx >= pts[-1][0]:
                return float(pts[-1][1]) * ctx / pts[-1][0]
            for (c1, v1), (c2, v2) in zip(pts, pts[1:]):
                if c1 <= ctx <= c2:
                    t = (ctx - c1) / float(c2 - c1)
                    return v1 + (v2 - v1) * t
    bytes_per = {2.0: 2, 1.0: 1, 0.5: 0.5}.get(float(bits), float(bits))
    raw = 2 * g["layers"] * g["kv_heads"] * g["head_dim"] * ctx * bytes_per / 1048576
    return raw * 0.48


def feasibility(combo, machine=None, geo=None):
    """Can this combination actually run here? Returns (ok, reasons, estimate).

    With --cpu-moe the expert weights live in RAM, so VRAM only has to hold the
    attention/KV part; both budgets are checked separately.
    """
    m = machine or MACHINE
    g = geo or MODEL_GEO
    ctx = int(combo.get("--ctx-size", 32768))
    kvname = combo.get("-ctk/-ctv", "q8_0")
    bits = PARAMS["-ctk/-ctv"]["kv_bits"].get(kvname, 1.0)
    kv = kv_cache_mib(ctx, bits, g)

    vram_cap = m["vram_mib"] * m["usable_vram_ratio"]
    ram_cap = m["ram_gib"] * 1024 * m["usable_ram_ratio"]

    cpu_moe = combo.get("--cpu-moe", True)
    reasons = []

    if cpu_moe:
        # attention weights + KV + vision tower must fit VRAM
        attn_share = 0.12                       # rough attention/embedding share
        vram_need = g["weights_mib"] * attn_share + kv + g["mmproj_mib"] * 0.35
        ram_need = g["weights_mib"] * (1 - attn_share)
    else:
        vram_need = g["weights_mib"] + kv + g["mmproj_mib"]
        ram_need = 2048

    if vram_need > vram_cap:
        reasons.append("显存不足:需 %.0f MiB,可用 %.0f MiB" % (vram_need, vram_cap))
    if ram_need > ram_cap:
        reasons.append("内存不足:需 %.0f MiB,可用 %.0f MiB" % (ram_need, ram_cap))

    return (not reasons), reasons, {"vram_need_mib": round(vram_need),
                                    "ram_need_mib": round(ram_need),
                                    "kv_mib": round(kv)}


def space_size(params=None):
    p = params or PARAMS
    n = 1
    for k, v in p.items():
        n *= len(v["values"])
    return n


def params_for_engine(engine, params=None):
    """Filter the parameter space down to what an engine actually accepts.

    The sweep used to be written against llama.cpp flag names, which is wrong for
    three of the four target engines: ninfer takes --max-context, not --ctx-size,
    and knows nothing about -ctk. Setting an unsupported flag does not error - the
    server ignores it - so the run gets recorded under a configuration that was
    never applied. Filtering here means the plan only ever proposes flags the engine
    has, and the dropped ones are reported rather than silently omitted.
    """
    import engine_profiles as ep
    p = params or PARAMS
    supported = set(ep.sweep_roles(engine))
    keep, dropped = {}, []
    for k, spec in p.items():
        role = spec.get("role")
        if role in supported:
            keep[k] = spec
        else:
            dropped.append({"param": k, "role": role,
                            "why": "引擎 %s 无此角色" % engine})
    return keep, dropped


def engine_argv(engine, values, exe=None):
    """Render a role->value dict into an engine-specific command line."""
    import engine_profiles as ep
    bad = ep.unsupported(engine, list(values))
    if bad:
        raise ValueError("引擎 %s 不支持: %s" % (engine, ", ".join(bad)))
    return ep.build_argv(engine, values, exe=exe)


def build_plan(params=None, machine=None, geo=None, base=None, use_ofat=True,
               model_path=None, mmproj_path=None):
    """Stage 0: build the sweep plan.

    Default is OFAT (one-factor-at-a-time), NOT the full cross-product. The full
    grid for these flags is 768 combinations = 13 days of runs, and almost all of
    that is wasted: temperature and KV quant do not interact, so 4 temperatures
    crossed with 3 KV types costs 12 runs to learn 7 facts. OFAT learns each
    variable's effect in len(values) runs and reserves the cross-product for the
    few survivors that actually showed an effect (stage 3).

    The grid is still enumerated so the saving is measurable rather than asserted.
    """
    # Work on a COPY. build_plan appends reference values to a parameter's value list
    # so the in-use configuration is always measured; doing that on the module-level
    # PARAMS made the global grow on every call, so a second build_plan saw a polluted
    # ladder and returned a different (smaller) plan.
    p = {k: dict(v, values=list(v["values"])) for k, v in (params or PARAMS).items()}
    keys = [k for k in p if not p[k].get("only_if_ctx_gt")]

    # Model geometry decides what is feasible, so it MUST match the model under test.
    # It used to come from config/models.json's first entry regardless of which model
    # a reference config named; with a 6.7 GiB entry as the default, planning for a
    # 20.6 GiB Ornith rejected 36 combinations as "insufficient VRAM" - including
    # every temperature variant. Reading the actual file prevents that.
    if model_path and not geo:
        try:
            import detect as _det
            g = _det.model_geometry(model_path)
            if g and not g.get("_missing"):
                # model_geometry parses the GGUF header, which does not describe the
                # separate vision projector. The reference config knows whether one is
                # loaded, so take the size from there instead of assuming zero - a
                # missing mmproj understates VRAM and could approve an impossible plan.
                mm = 0
                if mmproj_path:
                    try:
                        mm = round(os.path.getsize(mmproj_path) / 1048576)
                    except OSError:
                        mm = 0
                g["mmproj_mib"] = mm
                geo = g
        except Exception:
            pass

    # ---- OFAT: vary one variable, hold the rest at the baseline ----
    # Defaults stand in for "the user's own preset" only until one is supplied.
    # build_plan accepted a `base` argument it never read, so a reference config
    # silently changed nothing. Now the baseline is what is actually injected.
    baseline = {k: p[k]["values"][0] for k in keys}
    baseline["-ctk/-ctv"] = "q8_0"
    baseline["--temp"] = "1.0"
    baseline["--spec-type"] = "draft-mtp"
    baseline["-fa"] = "on"
    baseline["--ctx-size"] = "32768"
    baseline["--reasoning"] = "auto"
    baseline["--top-k"] = "20"
    if base:
        # Accept BOTH spellings for the baseline. params_for_engine() and the engine
        # layer speak ROLES (cpu_moe_layers, context, kv_k), while PARAMS is keyed by
        # FLAG NAME (--ctx-size, -ctk/-ctv). Filtering `base` with `if k in p` dropped
        # every role-named key, so injecting a Llama-Rig config appeared to work and
        # silently changed nothing.
        role_to_param = {spec.get("role"): k for k, spec in p.items()
                        if spec.get("role")}
        for k, v in base.items():
            if k in p:                          # already a flag name
                baseline[k] = v
            elif k in role_to_param:            # a role name -> its flag
                baseline[role_to_param[k]] = v

    # A reference value outside the planned ladder must still be measured: it IS the
    # configuration in use, and dropping it would compare variants against a
    # baseline nobody runs. The loop above already resolved role names into flag-name
    # keys, so appending from `baseline` alone is enough - a second pass over `base`
    # added nothing and crashed when `base` was not a mapping.
    for k, v in list(baseline.items()):
        if k in p and v not in p[k]["values"]:
            p[k] = dict(p[k])
            p[k]["values"] = list(p[k]["values"]) + [v]

    total = len(list(itertools.product(*[p[k]["values"] for k in keys])))

    combos, dropped = [], []
    seen = set()
    for k in sorted(p, key=lambda x: p[x]["priority"]):
        spec = p[k]
        if spec.get("only_if_ctx_gt"):
            continue
        # Speed-only flags still get generated: they are measured by the throughput
        # probe instead of the question set. Skipping them entirely (an earlier bug)
        # meant the speed sweep had nothing to run.
        for val in spec["values"]:
            c = dict(baseline)
            c[k] = val
            if spec.get("only_if_ctx_gt") and int(c["--ctx-size"]) <= spec["only_if_ctx_gt"]:
                continue
            sig = json.dumps({a: b for a, b in sorted(c.items())}, ensure_ascii=False)
            if sig in seen:
                continue
            seen.add(sig)
            ok, why, est = feasibility(c, machine, geo)
            c["_estimate"] = est
            c["_varies"] = k
            c["_class"] = spec.get("affects", "quality")
            if ok:
                combos.append(c)
            else:
                dropped.append({"combo": c, "reasons": why})

    # context size interacts with rope-scaling: add the paired variant
    for ctx in p["--ctx-size"]["values"]:
        if int(ctx) > 32768:
            c = dict(baseline)
            c["--ctx-size"] = ctx
            c["--rope-scaling"] = "yarn"
            ok, why, est = feasibility(c, machine, geo)
            c["_estimate"] = est
            c["_varies"] = "--rope-scaling"
            c["_class"] = p["--rope-scaling"].get("affects", "quality")
            if ok and json.dumps({a: b for a, b in sorted(c.items())},
                                 ensure_ascii=False) not in seen:
                combos.append(c)
            elif not ok:
                dropped.append({"combo": c, "reasons": why})

    def prio(c):
        return p.get(c.get("_varies"), {}).get("priority", 99)
    combos.sort(key=prio)

    return {"total_combinations": total,
            "ofat_combinations": len(combos) + len(dropped),
            "survivors": len(combos),
            "dropped": len(dropped),
            "dropped_detail": dropped,
            "plan": combos,
            "baseline": baseline,
            "machine": machine or MACHINE,
            "model": geo or MODEL_GEO,
            "stage_estimate_h": stage_estimate(len(combos))}


def stage_estimate(n_survivors):
    """Rough wall-clock for the funnel, so the cost is visible before committing.

    Coarse runs the full OFAT list (all of it is affordable at ~3 min each); fine
    runs only the variables that showed a real gap.
    """
    coarse_n = n_survivors
    fine_n = min(max(4, n_survivors // 3), 12)
    inter_n = min(max(2, fine_n // 3), 5)
    return {"stage1_coarse_h": round(coarse_n * 3 / 60, 2),
            "stage2_fine_h": round(fine_n * 25 / 60, 2),
            "stage3_interact_h": round(inter_n * 25 / 60, 2),
            "total_h": round((coarse_n * 3 + (fine_n + inter_n) * 25) / 60, 2),
            "n_coarse": coarse_n, "n_fine": fine_n, "n_interact": inter_n}


# ---------------------------------------------------------------------------
# Command-line construction: a combo becomes an actual llama-server argv
# ---------------------------------------------------------------------------
def combo_to_flags(combo, base_flags=None):
    """Turn a sweep combo into launch flags, keeping everything not being swept.

    base_flags: the user's real preset (model, mmproj, cpu-moe, threads...).
    """
    out = list(base_flags or [])
    for key, val in combo.items():
        if key.startswith("_"):
            continue
        spec = PARAMS.get(key)
        if not spec:
            continue
        if val == "none":                       # explicit disable
            for f in spec["flag"]:
                out = [x for x in out if x != f]
                out = [x for i, x in enumerate(out)
                       if not (i > 0 and out[i - 1] in spec["flag"])]
            continue
        if val == "default":
            continue
        for f in spec["flag"]:
            out = _set_flag(out, f, val)
    return out


def _set_flag(argv, flag, value):
    """Replace a flag's value in place, or append it. Handles both -k v and -k=v."""
    for i, tok in enumerate(argv):
        if tok == flag:
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                argv[i + 1] = value
                return argv
            return argv[:i + 1] + [value] + argv[i + 1:]
        if tok.startswith(flag + "="):
            argv[i] = "%s=%s" % (flag, value)
            return argv
    return argv + [flag, value]


def describe(combo):
    bits = []
    for k in sorted(combo, key=lambda x: PARAMS.get(x, {}).get("priority", 99)):
        if k.startswith("_"):
            continue
        spec = PARAMS.get(k)
        if k == "--ctx-size":
            bits.append("ctx=%s" % combo[k])
            continue
        if spec and spec["affects"] == "quality" and combo[k] != "default":
            bits.append("%s=%s" % (k, combo[k]))
    return " ".join(bits) if bits else "(基准)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="show the generated plan")
    ap.add_argument("--stage", type=int, default=None, help="run a stage (0 = constraints only)")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    plan = build_plan()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)

    if args.stage == 0 or args.plan or args.stage is None:
        print("=" * 68)
        print("  参数扫描 · 阶段 0(约束筛除,零成本)")
        print("=" * 68)
        m = plan["machine"]
        print("  机器预算: 显存 %.0f MiB(可用 %.0f) | 内存 %.1f GiB(可用 %.1f)"
              % (m["vram_mib"], m["vram_mib"] * m["usable_vram_ratio"],
                 m["ram_gib"], m["ram_gib"] * m["usable_ram_ratio"]))
        print("  模型几何: %d 层 / %d KV heads / 权重 %.1f GiB"
              % (MODEL_GEO["layers"], MODEL_GEO["kv_heads"], MODEL_GEO["weights_mib"] / 1024))
        print()
        print("  全组合(笛卡尔积): %d" % plan["total_combinations"])
        print("  OFAT 方案:        %d 组(每个变量单独变化,其余保持基准)"
              % plan.get("ofat_combinations", plan["survivors"]))
        print("  物理不可行:       %d" % plan["dropped"])
        print("  实际要跑:         %d 组" % plan["survivors"])
        saved = plan["total_combinations"] - plan["survivors"]
        print("  相比穷举省下:     %d 组 ≈ %.0f 小时"
              % (saved, saved * 25 / 60))
        print()
        est = plan["stage_estimate_h"]
        print("  预估耗时(四阶段漏斗):")
        print("    阶段1 粗筛 %2d 组 × 3 min  = %.1f 小时" % (est["n_coarse"], est["stage1_coarse_h"]))
        print("    阶段2 精测 %2d 组 × 25 min = %.1f 小时" % (est["n_fine"], est["stage2_fine_h"]))
        print("    阶段3 组合 %2d 组 × 25 min = %.1f 小时" % (est["n_interact"], est["stage3_interact_h"]))
        print("    合计 ≈ %.1f 小时(穷举需 %.0f 小时)"
              % (est["total_h"], plan["total_combinations"] * 25 / 60))
        print()
        qc = [c for c in plan["plan"] if c.get("_sweep_class") != "speed"]
        print("  ⚠ 速度型参数(threads/batch/ngl)不进质量扫描:")
        print("     它们只影响吞吐,用 30 秒的探测测,而不是 25 分钟的题本。")
        print()
        print("  被排除的组合(前 8 条):")
        for d in plan["dropped_detail"][:8]:
            print("    %-40s %s" % (describe(d["combo"])[:40], "; ".join(d["reasons"])[:52]))
        if len(plan["dropped_detail"]) > 8:
            print("    ... 共 %d 条" % len(plan["dropped_detail"]))
        print()
        print("  变量优先级(粗筛按此顺序):")
        for k, spec in sorted(PARAMS.items(), key=lambda x: x[1]["priority"]):
            print("    %d. %-16s %-10s %s" % (spec["priority"], k, spec["affects"], spec["why"]))


if __name__ == "__main__":
    main()
