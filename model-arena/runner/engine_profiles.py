# -*- coding: utf-8 -*-
"""engine_profiles.py - one launch-profile model that covers several engines.

The four engines this project targets do NOT share a flag vocabulary:

    llama.cpp   --model x.gguf --ctx-size 32768 --n-gpu-layers 99 -ctk q8_0
                --spec-type draft-mtp --spec-draft-n-max 2

    kvmem       same base, PLUS a KV-memory family llama.cpp does not have:
                --kv-dtype  --kvmem-method  --kvmem-budget  --kvmem-cpu-gb
                --kvmem-block-tokens  --kvmem-gen-reserve
                (kvmem is a llama.cpp fork focused on KV cache residency)

    ninfer      a different model format and a different vocabulary:
                ninfer-serve models\\x.ninfer --model-id qwen3.6-27b
                --max-context 150000 --spec mtp --draft-tokens 3

    ninfer+kvmem  both vocabularies in one command line

Hard-coding llama.cpp flag names (the original design) silently produces a profile
that is wrong for three of the four: a sweep would set "--ctx-size" on ninfer, which
does not accept it, and the server would either refuse to start or ignore it - and
the run would be attributed to the wrong configuration.

So flags are described by ROLE, and each engine supplies its own spelling:

    role "context"  -> llama.cpp: --ctx-size | ninfer: --max-context
    role "kv_k"     -> llama.cpp: -ctk      | kvmem: --kv-dtype
    role "spec"     -> llama.cpp: --spec-type draft-mtp | ninfer: --spec mtp

Usage:
  python runner/engine_profiles.py --list
  python runner/engine_profiles.py --map --engine ninfer
  python runner/engine_profiles.py --translate --engine ninfer --set context 32768
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)

# ---------------------------------------------------------------------------
# Roles: what a flag DOES, independent of how an engine spells it.
# A sweep edits roles; the engine decides the spelling.
# ---------------------------------------------------------------------------
ROLES = {
    "model":        "模型文件",
    "mmproj":       "多模态投影",
    "context":      "上下文长度",
    "gpu_layers":   "GPU 卸载层数",
    "cpu_moe":      "MoE 专家层放 CPU(开关)",
    "cpu_moe_layers": "MoE 专家层放 CPU 的层数(数值,--n-cpu-moe)",
    "min_p":        "min-p 相对概率下限",
    "top_p":        "top-p 核采样",
    "reasoning_effort": "思考力度",
    "kv_k":         "KV cache K 精度",
    "kv_v":         "KV cache V 精度",
    "kv_dtype":     "KV 数据类型(kvmem 专用,统一控制 K/V)",
    "kv_mem_method": "KV 内存方法(kvmem)",
    "kv_mem_budget": "KV 内存预算(kvmem)",
    "kv_cpu_gb":    "KV 放 CPU 的容量(kvmem)",
    "kv_block":     "KV 分块 token 数(kvmem)",
    "kv_gen_reserve": "生成预留(kvmem)",
    "flash_attn":   "Flash Attention",
    "spec":         "投机解码开关",
    "spec_draft_n": "每次草拟 token 数",
    "rope_scaling": "RoPE 外推策略",
    "threads":      "CPU 线程",
    "threads_batch": "批处理线程",
    "batch":        "批大小",
    "ubatch":       "微批大小",
    "temp":         "温度",
    "top_k":        "top-k",
    "top_p":        "top-p",
    "min_p":        "min-p",
    "repeat_penalty": "重复惩罚",
    "host":         "监听地址",
    "port":         "端口",
    "reasoning":    "思考模式",
    "alias":        "模型别名",
    "metrics":      "开启 /metrics",
    "split_mode":   "切分模式",
    "max_tokens":   "最大生成 token",
}

# ---------------------------------------------------------------------------
# Per-engine spelling. A role maps to a flag plus how it is written:
#   {"flag": "--ctx-size", "style": "value"}   -> "--ctx-size 32768"
#   {"flag": "--cpu-moe",  "style": "switch"}  -> "--cpu-moe"
#   {"flag": ["--spec-type", "draft-mtp"], "style": "pair"}  -> "--spec-type draft-mtp"
# ---------------------------------------------------------------------------
ENGINE_FLAGS = {
    "llama.cpp": {
        "model":          {"flag": "--model", "style": "value"},
        "mmproj":         {"flag": "--mmproj", "style": "value"},
        "context":        {"flag": "--ctx-size", "style": "value"},
        "gpu_layers":     {"flag": "--n-gpu-layers", "style": "value"},
        "cpu_moe":        {"flag": "--cpu-moe", "style": "switch"},
        "cpu_moe_layers": {"flag": "--n-cpu-moe", "style": "value"},
        "kv_k":           {"flag": "-ctk", "style": "value"},
        "kv_v":           {"flag": "-ctv", "style": "value"},
        "flash_attn":     {"flag": "-fa", "style": "value"},
        "spec":           {"flag": "--spec-type", "style": "value"},
        "spec_draft_n":   {"flag": "--spec-draft-n-max", "style": "value"},
        "rope_scaling":   {"flag": "--rope-scaling", "style": "value"},
        "threads":        {"flag": "--threads", "style": "value"},
        "threads_batch":  {"flag": "--threads-batch", "style": "value"},
        "batch":          {"flag": "--batch-size", "style": "value"},
        "ubatch":         {"flag": "--ubatch-size", "style": "value"},
        "temp":           {"flag": "--temp", "style": "value"},
        "top_k":          {"flag": "--top-k", "style": "value"},
        "top_p":          {"flag": "--top-p", "style": "value"},
        "min_p":          {"flag": "--min-p", "style": "value"},
        "repeat_penalty": {"flag": "--repeat-penalty", "style": "value"},
        "host":           {"flag": "--host", "style": "value"},
        "port":           {"flag": "--port", "style": "value"},
        "reasoning":      {"flag": "--reasoning", "style": "value"},
        "alias":          {"flag": "--alias", "style": "value"},
        "metrics":        {"flag": "--metrics", "style": "switch"},
        "split_mode":     {"flag": "--split-mode", "style": "value"},
        "max_tokens":     {"flag": "--n-predict", "style": "value"},
    },
    # kvmem is a llama.cpp fork: same base vocabulary, plus the KV-memory family.
    "kvmem": {
        "_inherits": "llama.cpp",
        # kvmem replaces the separate -ctk/-ctv pair with one --kv-dtype. Aliasing
        # the roles (rather than dropping them) means a sweep written for llama.cpp
        # still expresses "vary KV precision" and gets translated, instead of
        # silently losing that dimension on this engine.
        "kv_k":           {"flag": "--kv-dtype", "style": "value", "alias_of": "kv_dtype"},
        "kv_v":           {"flag": "--kv-dtype", "style": "value", "alias_of": "kv_dtype"},
        "kv_dtype":       {"flag": "--kv-dtype", "style": "value"},
        "kv_mem_method":  {"flag": "--kvmem-method", "style": "value"},
        "kv_mem_budget":  {"flag": "--kvmem-budget", "style": "value"},
        "kv_cpu_gb":      {"flag": "--kvmem-cpu-gb", "style": "value"},
        "kv_block":       {"flag": "--kvmem-block-tokens", "style": "value"},
        "kv_gen_reserve": {"flag": "--kvmem-gen-reserve", "style": "value"},
    },
    # ninfer: different model format, different vocabulary, no mmap/ngl split.
    "ninfer": {
        "model":          {"flag": "POSITIONAL", "style": "positional"},
        # ninfer's own vocabulary; add roles only where the engine documents a flag.
        # Guessing here would be worse than reporting "unsupported".
        "context":        {"flag": "--max-context", "style": "value"},
        "max_tokens":     {"flag": "--default-max-tokens", "style": "value"},
        "spec":           {"flag": "--spec", "style": "value"},
        "spec_draft_n":   {"flag": "--draft-tokens", "style": "value"},
        "host":           {"flag": "--host", "style": "value"},
        "port":           {"flag": "--port", "style": "value"},
        "alias":          {"flag": "--model-id", "style": "value"},
        "reasoning":      {"flag": "--preserve-thinking", "style": "switch"},
    },
    # ninfer with kvmem KV handling: both vocabularies on one command line.
    "ninfer+kvmem": {
        "_inherits": "ninfer",
        "kv_dtype":       {"flag": "--kv-dtype", "style": "value"},
        "kv_mem_method":  {"flag": "--kvmem-method", "style": "value"},
        "kv_mem_budget":  {"flag": "--kvmem-budget", "style": "value"},
        "kv_cpu_gb":      {"flag": "--kvmem-cpu-gb", "style": "value"},
        "kv_block":       {"flag": "--kvmem-block-tokens", "style": "value"},
        "kv_gen_reserve": {"flag": "--kvmem-gen-reserve", "style": "value"},
    },
    "ollama": {
        "model":  {"flag": "POSITIONAL", "style": "positional"},
        "host":   {"flag": "--host", "style": "value"},
        "port":   {"flag": "--port", "style": "value"},
    },
}

# Which roles a sweep may vary, per engine. Editing a role an engine does not have
# must fail loudly rather than produce a command the server ignores.
SWEEPABLE = {
    "llama.cpp": ["context", "gpu_layers", "cpu_moe", "cpu_moe_layers", "kv_k", "kv_v", "flash_attn",
                  "spec", "spec_draft_n", "threads", "threads_batch", "batch",
                  "ubatch", "temp", "top_k", "top_p", "min_p", "repeat_penalty",
                  "reasoning", "rope_scaling"],
    "kvmem": ["context", "gpu_layers", "cpu_moe", "rope_scaling", "kv_k", "kv_v", "kv_dtype", "kv_mem_method",
              "kv_mem_budget", "kv_cpu_gb", "kv_block", "kv_gen_reserve",
              "flash_attn", "spec", "spec_draft_n", "threads", "batch", "temp",
              "top_k", "top_p", "min_p"],
    "ninfer": ["context", "max_tokens", "spec", "spec_draft_n", "reasoning"],
    "ninfer+kvmem": ["context", "max_tokens", "spec", "spec_draft_n", "reasoning",
                     "kv_dtype", "kv_mem_method", "kv_mem_budget", "kv_cpu_gb",
                     "kv_block", "kv_gen_reserve"],
}


def resolve(engine):
    """Flatten an engine's flag table, following _inherits."""
    spec = ENGINE_FLAGS.get(engine)
    if not spec:
        return {}
    base = spec.get("_inherits")
    table = dict(resolve(base)) if base else {}
    for role, v in spec.items():
        if role.startswith("_"):
            continue
        table[role] = v
    return table


def role_to_argv(engine, role, value):
    """Render one role as argv tokens for an engine. Returns [] if unsupported."""
    table = resolve(engine)
    spec = table.get(role)
    if not spec:
        return []
    style = spec.get("style")
    flag = spec.get("flag")
    if style == "positional":
        return [str(value)]
    if style == "switch":
        return [flag] if value else []
    if isinstance(flag, list):                  # pair, e.g. ["--spec-type", "draft-mtp"]
        return list(flag) if value else []
    if value is True:
        return [flag]
    if value in (False, None):
        return []
    return [flag, str(value)]


def build_argv(engine, values, exe=None):
    """Render a full command line from role -> value.

    Aliased roles collapse: kvmem has one --kv-dtype where llama.cpp has a -ctk/-ctv
    pair, so setting both kv_k and kv_v produced the flag twice. A repeated flag is
    not harmless - the server's behaviour depends on which one it keeps, and the
    profile no longer says what was actually applied.
    """
    argv = [exe or default_exe(engine)]
    table = resolve(engine)
    # positionals first: ninfer and ollama want the model path before the flags
    for role, spec in table.items():
        if spec.get("style") == "positional" and values.get(role):
            argv += role_to_argv(engine, role, values[role])

    emitted = {}          # real flag -> the value already written for it
    for role, val in values.items():
        spec = table.get(role)
        if not spec or spec.get("style") == "positional":
            continue
        flag = spec.get("flag")
        key = flag if isinstance(flag, str) else tuple(flag or ())
        if key in emitted:
            # keep the first, note the collision so it is visible rather than silent
            if emitted[key] != val:
                argv.append("# 注: %s 与 %s 共用同一开关,已取 %s=%s"
                            % (emitted.get("_role_" + str(key), "?"), role,
                               emitted[key], emitted[key]))
            continue
        toks = role_to_argv(engine, role, val)
        if not toks:
            continue
        argv += toks
        emitted[key] = val
        emitted["_role_" + str(key)] = role
    return [a for a in argv if not str(a).startswith("#")]


def default_exe(engine):
    return {
        "llama.cpp": "llama-server",
        "kvmem": "llama-kvmem-server",
        "ninfer": "ninfer-serve",
        "ninfer+kvmem": "ninfer-serve",
        "ollama": "ollama",
    }.get(engine, "llama-server")


def sweep_roles(engine):
    """The roles a parameter sweep may touch on this engine."""
    return SWEEPABLE.get(engine, [])


def unsupported(engine, roles):
    """Roles the caller asked for that this engine has no flag for.

    Returning these instead of dropping them is the whole point: silently ignoring
    "--ctx-size" on ninfer would produce a run labelled with a context length the
    server never used.
    """
    have = resolve(engine)
    return [r for r in roles if r not in have]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--map", default="")
    ap.add_argument("--engine", default="")
    ap.add_argument("--translate", action="store_true")
    ap.add_argument("--set", nargs=2, action="append", default=[], metavar=("ROLE", "VALUE"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list or (not args.map and not args.translate):
        print("引擎参数对照")
        print("=" * 76)
        print()
        print("  %-14s %s" % ("角色", "说明"))
        print("  " + "-" * 70)
        for role, why in ROLES.items():
            print("  %-14s %s" % (role, why))
        print()
        print("  各引擎的写法:")
        for eng in ENGINE_FLAGS:
            roles = sweep_roles(eng)
            print()
            print("  %s  (可扫描 %d 个角色)" % (eng, len(roles)))
            table = resolve(eng)
            for role in roles[:8]:
                spec = table.get(role) or {}
                fl = spec.get("flag")
                shown = " ".join(fl) if isinstance(fl, list) else fl
                print("      %-16s %s" % (role, shown))
            if len(roles) > 8:
                print("      ... 另有 %d 个" % (len(roles) - 8))
        print()
        print("  为什么要按角色而不是按参数名:")
        print("    硬编码 llama.cpp 的参数名,在 ninfer 上会静默失效 ——")
        print("    ninfer 不认 --ctx-size(它用 --max-context),但服务器可能只是忽略它,")
        print("    于是这次运行被标注成一个它从未使用的上下文长度。")
        return

    eng = args.map or args.engine
    if not eng or eng not in ENGINE_FLAGS:
        print("未知引擎: %r" % eng)
        print("可用: %s" % ", ".join(ENGINE_FLAGS))
        return 2

    if args.json:
        print(json.dumps({"engine": eng, "flags": resolve(eng),
                          "sweepable": sweep_roles(eng)},
                         ensure_ascii=False, indent=1))
        return

    if args.map:
        print("引擎: %s" % eng)
        print("可执行文件: %s" % default_exe(eng))
        print()
        table = resolve(eng)
        for role in ROLES:
            spec = table.get(role)
            if not spec:
                continue
            fl = spec.get("flag")
            shown = " ".join(fl) if isinstance(fl, list) else fl
            print("  %-16s %-12s %s" % (role, spec.get("style"), shown))
        return

    if args.translate:
        values = {k: v for k, v in (args.set or [])}
        bad = unsupported(eng, list(values))
        if bad:
            print("⚠ 引擎 %s 不支持这些角色: %s" % (eng, ", ".join(bad)))
            print("  继续会生成一条服务器会忽略的命令,因此这里停下来。")
            return 3
        argv = build_argv(eng, values)
        print(" ".join('"%s"' % a if " " in a else a for a in argv))
        return 0


if __name__ == "__main__":
    sys.exit(main())
