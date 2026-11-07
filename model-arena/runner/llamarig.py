# -*- coding: utf-8 -*-
"""llamarig.py - read Llama-Rig configuration files.

Llama-Rig is an Electron launcher the user already maintains, with 29 named configs
covering the exact experiments this project was rebuilding by hand:

    Bonsai2-27B-PQ2-Balanced / Fast / LongCtx / MaxCtx   one model, four context/quality settings
    K1/K2/K3/K4  KVMem-balanced/fast/longctx/maxctx      KVMem parameter variants
    K6  block32                                          KV block-size experiment
    L2-Ornith-ncmoe30/36/42                              n-cpu-moe at three values
    N1/N2                                                ninfer configs

That is a parameter sweep that has already been run, with the conclusions written
into the file comments. Re-deriving it from scratch would duplicate work and,
worse, disagree with it.

What a Llama-Rig config gives us that a .bat does not:

    llama_server_path   which ENGINE the config targets (llama.cpp vs kvmem vs ninfer)
    extra_args          flags the GUI has no field for, passed through verbatim
    typed fields        ctx_size / n_gpu_layers / temp ... already parsed
    comments            measured conclusions, e.g. "q4_0 KV 实测与 q8_0 质量逐字同形"

So this module treats Llama-Rig as a first-class source of launch profiles, and
keeps the comments - they are the experiment log.

Usage:
  python runner/llamarig.py --find                      locate a Llama-Rig install
  python runner/llamarig.py --list                      list its configs
  python runner/llamarig.py --show K6-Bonsai2-KVMem-block32
  python runner/llamarig.py --import-all                write them into config/profiles.json
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)

# Fields Llama-Rig stores as typed TOML keys, and the role each one plays.
FIELD_ROLES = {
    "model": "model",
    "mmproj": "mmproj",
    "host": "host",
    "port": "port",
    "ctx_size": "context",
    "n_predict": "max_tokens",
    "n_gpu_layers": "gpu_layers",
    "temp": "temp",
    "top_k": "top_k",
    "top_p": "top_p",
    "min_p": "min_p",
    "presence_penalty": "presence_penalty",
    "threads": "threads",
    "threads_batch": "threads_batch",
    "batch_size": "batch",
    "ubatch_size": "ubatch",
    # n_cpu_moe is a first-class field and a real sweep variable: the user has
    # L2-Ornith-ncmoe30/36/42, i.e. the same model at three MoE-offload depths.
    "n_cpu_moe": "cpu_moe_layers",
    "flash_attn": "flash_attn",
    "reasoning": "reasoning",
    "repeat_penalty": "repeat_penalty",
    "seed": "seed",
    "split_mode": "split_mode",
}

# extra_args is free text; recognise the roles we know so a sweep can vary them.
EXTRA_ROLES = [
    (r"-ctk\s+(\S+)", "kv_k"),
    (r"-ctv\s+(\S+)", "kv_v"),
    (r"--kv-dtype\s+(\S+)", "kv_dtype"),
    (r"--kvmem-method\s+(\S+)", "kv_mem_method"),
    (r"--kvmem-budget\s+(\S+)", "kv_mem_budget"),
    (r"--kvmem-cpu-gb\s+(\S+)", "kv_cpu_gb"),
    (r"--kvmem-block-tokens\s+(\S+)", "kv_block"),
    (r"--kvmem-sink-tokens\s+(\S+)", "kv_sink"),
    (r"--kvmem-trace", "kv_trace"),
    (r"--jinja", "jinja"),
    (r"--reasoning-effort\s+(\S+)", "reasoning_effort"),
    (r"--reasoning-budget\s+(\S+)", "reasoning_budget"),
    (r"--n-cpu-moe\s+(\S+)", "cpu_moe_layers"),
    (r"-ngl\s+(\S+)", "gpu_layers"),
    (r"--kvmem-gen-reserve\s+(\S+)", "kv_gen_reserve"),
    (r"--spec-type\s+(\S+)", "spec"),
    (r"--spec-draft-n-max\s+(\S+)", "spec_draft_n"),
    (r"--rope-scaling\s+(\S+)", "rope_scaling"),
    (r"--reasoning\s+(\S+)", "reasoning"),
    (r"-fa\s+(\S+)", "flash_attn"),
    (r"--alias\s+(\S+)", "alias"),
]

# llama_server_path tells us which engine a config targets.
EXE_ENGINE = [
    ("llama-kvmem-server", "kvmem"),
    ("ninfer-serve", "ninfer"),
    ("llama-server", "llama.cpp"),
]


def find_install(roots=None):
    """Locate a Llama-Rig installation (a directory holding configs/*.toml)."""
    roots = roots or [os.path.join(os.path.dirname(ROOT), "00_models_Base"),
                      os.path.dirname(ROOT), ROOT]
    found = []
    for r in roots:
        for depth in (1, 2, 3):
            pat = os.path.join(r, *(["*"] * depth), "Llama-Rig*")
            for d in glob.glob(pat):
                if os.path.isdir(os.path.join(d, "configs")):
                    found.append(d)
    return sorted(set(found))


def parse_toml_lite(text):
    """Parse the subset of TOML these files use: key = value, with # comments.

    A full TOML parser is not worth a dependency here: the files are flat, one
    assignment per line, and anything unexpected is skipped rather than guessed at.
    """
    out, comments = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            c = line.lstrip("#").strip()
            if c:
                comments.append(c)
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.split(" #")[0].strip() if " #" in v else v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif v.lower() in ("true", "false"):
            v = v.lower() == "true"
        else:
            try:
                v = int(v)
            except ValueError:
                try:
                    v = float(v)
                except ValueError:
                    pass
        out[k] = v
    return out, comments


def engine_of(cfg):
    path = str(cfg.get("llama_server_path") or "")
    low = path.lower()
    for needle, eng in EXE_ENGINE:
        if needle in low:
            return eng, path
    return "llama.cpp", path


def load_config(path):
    """Read one Llama-Rig config into a structured profile."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    cfg, comments = parse_toml_lite(text)
    engine, exe = engine_of(cfg)

    roles = {}
    for field, role in FIELD_ROLES.items():
        if field in cfg and cfg[field] not in ("", None):
            roles[role] = cfg[field]

    extra = str(cfg.get("extra_args") or "")
    for pat, role in EXTRA_ROLES:
        m = re.search(pat, extra)
        if m:
            # a pattern with no capture group is a bare switch
            roles.setdefault(role, m.group(1) if m.groups() else True)

    # the comments carry the measured conclusions - keep them
    notes = [c for c in comments if len(c) > 12][:12]

    return {
        "name": os.path.basename(path).replace(".config.toml", ""),
        "source": "llamarig",
        "path": path,
        "engine": engine,
        "exe": exe,
        "launch_mode": cfg.get("launch_mode"),
        "model": cfg.get("model"),
        "mmproj": cfg.get("mmproj") or None,
        "port": cfg.get("port"),
        "host": cfg.get("host"),
        "roles": roles,              # role -> value, engine-agnostic
        "raw_fields": cfg,
        "extra_args": extra,
        "notes": notes,
    }


def list_configs(install):
    return sorted(glob.glob(os.path.join(install, "configs", "*.config.toml")))


def summary_row(cfg):
    r = cfg["roles"]
    return {
        "name": cfg["name"],
        "engine": cfg["engine"],
        "model": os.path.basename(str(cfg.get("model") or "?"))[:34],
        "ctx": r.get("context"),
        "ngl": r.get("gpu_layers"),
        "kv": r.get("kv_dtype") or (("%s/%s" % (r.get("kv_k"), r.get("kv_v")))
                                    if r.get("kv_k") else None),
        "spec": r.get("spec"),
        "block": r.get("kv_block"),
        "ncmoe": r.get("cpu_moe_layers"),
    }


def to_profile(cfg):
    """Convert to the profile shape the rest of the project uses."""
    return {
        "name": cfg["name"],
        "exe": cfg["exe"],
        "cwd": os.path.dirname(cfg["exe"]) if cfg["exe"] else ".",
        "model": cfg["model"],
        "mmproj": cfg["mmproj"],
        "engine": cfg["engine"],
        "vars": dict(cfg["roles"]),
        "raw": cfg.get("extra_args") or "",
        "note": "从 Llama-Rig 导入" + ((" | " + cfg["notes"][0][:70]) if cfg["notes"] else ""),
        "_source": "llamarig",
        "_source_path": cfg["path"],
    }


def _norm(v):
    """Normalise a value for comparison: True/on are the same switch state."""
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v).strip().lower()


# Roles that are labels rather than experimental variables. Comparing configs that
# differ only in these is not an experiment.
#
# "model" and "mmproj" are NOT excluded, even though they look like labels: two
# configs for different models are not a controlled comparison at all. Excluding the
# model made L4-light-Qwen3.5-9B and Qwen3.6-35B-APEX group together and "vary
# gpu_layers", which would have been reported as an experiment that does not exist.
NON_EXPERIMENTAL = {"alias", "port", "host"}


def detect_sweeps(cfgs, max_group=6):
    """Find groups of configs that differ in exactly ONE experimental role.

    The first version grouped by model alone and reported every role that varied
    anywhere in the group. On 29 configs that produced noise: "alias" varies by
    definition, and flash_attn appeared to vary only because one file writes `on`
    and another writes `true`.

    A controlled comparison requires holding everything else FIXED, so the grouping
    is done by the signature of all non-varying roles first, and only then is the
    single differing role identified. Configs that differ in two things at once are
    reported separately as "not a clean comparison", because treating them as one
    would attribute a combined effect to a single flag.
    """
    # signature = every role except the label-ish ones, normalised
    sigs = {}
    for c in cfgs:
        roles = {k: _norm(v) for k, v in c["roles"].items()
                 if k not in NON_EXPERIMENTAL}
        # compare models by filename: the same weights reached through two paths are
        # the same model, while two different files never are
        roles["model"] = os.path.basename(str(c.get("model") or "?"))
        roles["mmproj"] = os.path.basename(str(c.get("mmproj") or ""))
        key = json.dumps(roles, sort_keys=True, ensure_ascii=False)
        sigs.setdefault(key, []).append(c)

    # group configs whose signatures differ in exactly one role
    keys = list(sigs)
    groups, used = [], set()
    for i, k1 in enumerate(keys):
        r1 = json.loads(k1)
        for k2 in keys[i + 1:]:
            if (k1, k2) in used:
                continue
            r2 = json.loads(k2)
            diff = [r for r in set(r1) | set(r2)
                    if _norm(r1.get(r)) != _norm(r2.get(r))]
            # "model" is in the signature so that different models never group, but a
            # difference in the model itself is not an experiment - it is a different
            # subject. Only a real configuration role counts.
            if len(diff) != 1 or diff[0] == "model":
                continue
            role = diff[0]
            members = sigs[k1] + sigs[k2]
            if len(members) > max_group:
                continue
            used.add((k1, k2))
            groups.append({
                "role": role,
                "model": os.path.basename(str(members[0].get("model") or "?")),
                "engine": members[0]["engine"],
                "members": [{"name": m["name"],
                             "value": m["roles"].get(role),
                             "engine": m["engine"]} for m in members],
            })

    # merge groups sharing the same role + model + engine (a 3+ value sweep shows up
    # as several pairs)
    merged = {}
    for g in groups:
        key = (g["role"], g["model"], g["engine"])
        m = merged.setdefault(key, {"role": g["role"], "model": g["model"],
                                    "engine": g["engine"], "members": {}})
        for mem in g["members"]:
            m["members"][mem["name"]] = mem["value"]
    out = []
    for m in merged.values():
        vals = {}
        for name, val in m["members"].items():
            vals.setdefault(_norm(val), []).append(name)
        if len(vals) >= 2:
            out.append({"role": m["role"], "model": m["model"], "engine": m["engine"],
                        "values": {k: v for k, v in sorted(vals.items())},
                        "n_configs": sum(len(v) for v in vals.values())})
    out.sort(key=lambda g: (-len(g["values"]), g["role"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find", action="store_true")
    ap.add_argument("--install", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", default="")
    ap.add_argument("--import-all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sweeps", action="store_true",
                    help="找出只差一个变量的配置组(已做过的扫描实验)")
    args = ap.parse_args()

    install = args.install
    if not install:
        found = find_install()
        if not found:
            print("未找到 Llama-Rig 安装(需要包含 configs/*.toml 的目录)")
            return 2
        install = found[0]
        if args.find or not (args.list or args.show or args.import_all):
            print("找到 Llama-Rig: %s" % install)
            if len(found) > 1:
                print("  (另有 %d 个:%s)" % (len(found) - 1, ", ".join(found[1:])))

    paths = list_configs(install)
    if not paths:
        print("该目录下没有配置: %s" % install)
        return 2

    cfgs = [load_config(p) for p in paths]

    if args.sweeps:
        groups = detect_sweeps(cfgs)
        print("Llama-Rig 中已有的扫描实验")
        print("=" * 70)
        print()
        if not groups:
            print("  未发现只差一个变量的配置组。")
            return 0
        seen = set()
        for g in groups:
            key = (g["model"], g["role"])
            if key in seen:
                continue
            seen.add(key)
            print("  模型: %s" % g["model"][:56])
            print("  变量: %s" % g["role"])
            for val, names in g["values"].items():
                shown = os.path.basename(val) if ("\\" in val or "/" in val) else val
                print("      %-24s <- %s" % (shown[:24], ", ".join(n[:30] for n in names)))
            print()
        print("  这些是已经做过的单变量对比。本项目的扫描引擎可以直接复用它,")
        print("  不必从零重跑 —— 但要注意:配置里已有的结论尚未经过配对检验。")
        return 0

    if args.find and not (args.list or args.show or args.import_all):
        return 0

    if args.show:
        hit = [c for c in cfgs if c["name"] == args.show]
        if not hit:
            print("无此配置: %s" % args.show)
            print("可用: %s" % ", ".join(c["name"] for c in cfgs[:8]))
            return 2
        c = hit[0]
        if args.json:
            print(json.dumps(c, ensure_ascii=False, indent=1))
            return 0
        print("配置: %s" % c["name"])
        print("  引擎: %s" % c["engine"])
        print("  exe:  %s" % c["exe"])
        print("  模型: %s" % os.path.basename(str(c["model"] or "?")))
        print("  视觉: %s" % (os.path.basename(str(c["mmproj"])) if c["mmproj"] else "无"))
        print()
        print("  角色化参数:")
        for role, val in sorted(c["roles"].items()):
            print("    %-16s %s" % (role, val))
        if c["extra_args"]:
            print()
            print("  extra_args(界面无字段,原样直传):")
            print("    %s" % c["extra_args"])
        if c["notes"]:
            print()
            print("  配置注释(实测结论):")
            for n in c["notes"][:8]:
                print("    · %s" % n)
        return 0

    if args.import_all:
        store_path = os.path.join(ROOT, "config", "profiles.json")
        try:
            store = json.load(open(store_path, encoding="utf-8"))
        except Exception:
            store = {"profiles": {}}
        store.setdefault("profiles", {})
        n = 0
        for c in cfgs:
            store["profiles"][c["name"]] = to_profile(c)
            n += 1
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
        print("已导入 %d 个配置 -> config/profiles.json" % n)
        print("  (该文件在 .gitignore 中,不会提交)")
        return 0

    # default: list
    print("Llama-Rig: %s" % install)
    print("配置 %d 个" % len(cfgs))
    print()
    print("  %-34s %-10s %-24s %-8s %-8s %s"
          % ("名称", "引擎", "模型", "ctx", "KV", "其他"))
    print("  " + "-" * 96)
    for c in cfgs:
        r = summary_row(c)
        extra = []
        if r.get("block"):
            extra.append("block=%s" % r["block"])
        if r.get("spec"):
            extra.append("spec=%s" % r["spec"])
        print("  %-34s %-10s %-24s %-8s %-8s %s"
              % (r["name"][:34], r["engine"], r["model"][:24],
                 r["ctx"] or "-", r["kv"] or "-", " ".join(extra)))
    print()
    print("  --show <名称>   看某个配置的完整参数与注释")
    print("  --import-all    全部导入 config/profiles.json")


if __name__ == "__main__":
    sys.exit(main())
