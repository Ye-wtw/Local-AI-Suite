# -*- coding: utf-8 -*-
"""presets.py - 本机预设清单:从哪儿来、参数是什么、现在哪个在跑。

一个"预设"是一份完整的启动配置(Llama-Rig 的 *.config.toml 或本项目
config/profiles.json)。本模块做三件事,每件都必须可追溯:

  识别   Llama-Rig 的 20 份配置 + 本项目的 profiles.json,统一成同一形状。
         不靠猜:引擎由可执行文件名判断,参数由 TOML/argv 解析。

  实测   **两条独立的实测序列,不合并、不插值**:
           gui-20261006  config/preset_speed.json   (2026-10-06 GUI 实测,有首字)
           catalog-1015  Llama-Rig model-catalog.json 的 verified (2026-10-15)
         同一个预设在两份里可以有不同的 t/s(N1: 45.0 vs 47.1),那是两次
         测量,不是错误。合并成一个数字等于伪造一次没做过的测量。

  在跑   读 netstat 找出本机监听端口,再逐个探 /props 问它是谁。
         不猜端口:用户把引擎放在哪个端口都行。也不信"端口像"——
         实测本机有 4 个非模型服务也在 /props 回 JSON,所以只认带
         model_path / default_generation_settings 的响应。

为什么不能只看进程名:本会话实测 Get-CimInstance(读命令行)与 tasklist
都被拒绝访问,拿不到启动参数。所以"在跑的是哪套预设"必须靠服务自己回答
(别名、模型路径、n_ctx),再和预设清单比对。

Usage:
  python runner/presets.py            # 列表 + 在跑标记
  python runner/presets.py --json     # 完整目录
  python runner/presets.py --running  # 只看现在在跑什么
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
CONFIG = os.path.join(ROOT, "config")
SPEED_JSON = os.path.join(CONFIG, "preset_speed.json")
PROFILES_JSON = os.path.join(CONFIG, "profiles.json")

# 引擎由可执行文件名判断:同一个 llama.cpp 分支有不同的 server 名字,
# 而 ninfer 是完全不同的产物格式(.ninfer),参数名都不一样。
#
# 元组第三项是该引擎的**模型传参方式**,这是启动时唯一真正会出错的地方:
#   -m     模型是选项值,如 llama-server -m x.gguf
#   model  模型是位置参数,如 ninfer-kvmem.exe x.ninfer --port ...
# 实测(--help 逐个读过):ninfer-kvmem / ninfer-serve 用位置参数,
# llama-server / llama-kvmem-server 用 -m。搞错这一个字符,服务就起不来。
EXE_ENGINES = [
    ("ninfer-kvmem", "ninfer", "model"),
    ("ninfer-serve", "ninfer", "model"),
    ("llama-kvmem-server", "kvmem", "-m"),
    ("llama-server", "llama.cpp", "-m"),
]

# 不是 llama.cpp 系服务端、参数表与本模块不通用、且不能按预设直接起 HTTP 服务的
# 可执行文件。strata.exe 是 cyber-on-strata 分支:CLI 用法是
#   strata generate --pack DIR --tokens "1,2,3"
# 它走的是预设里 launcher(如 run-optimized.ps1)那条链,不吃本模块的 TOML 参数。
#
# 判据不是"名字里有没有 strata",而是**它会不会按本模块拼的 argv 起来**。
# 实测把 C1 这份标成 llama.cpp + -m,等于承诺一个它做不到的启动方式;
# 预设注释原话是「strata.exe,非 llama-server,不走 TOML 参数表」。
LAUNCHER_ONLY = [
    ("strata", "strata.exe 走预设自带的 launcher(如 run-optimized.ps1),"
               "不吃本模块拼的 TOML 参数"),
]

# 参数 -> 中文标签。放在这里而不是 UI:命令行和界面必须读到同一份说明。
PARAM_LABELS = [
    ("host", "监听"),
    ("port", "端口"),
    ("ctx", "上下文"),
    ("ngl", "GPU 层"),
    ("kv", "KV 精度"),
    ("kv_mem", "KV 内存"),
    ("kv_budget", "KV 预算 B"),
    ("kv_gen", "生成预留 R"),
    ("kv_host", "Host 预算 H"),
    ("draft_tokens", "草稿 token"),
    ("ngram", "ngram 草稿"),
    ("adaptive", "自适应 MTP"),
    ("full_draft", "全输出头"),
    ("prefill", "prefill 块"),
    ("batch", "batch / ubatch"),
    ("moe", "MoE 卸载"),
    ("spec", "投机解码"),
    ("sampling", "采样"),
    ("threads", "线程"),
    ("alias", "别名"),
]


def engine_from_exe(path):
    """可执行文件名 -> 引擎名。找不到就返回 None(而不是猜一个)。"""
    low = str(path or "").lower()
    for needle, eng, _ in EXE_ENGINES:
        if needle in low:
            return eng
    return None


def model_arg_style(path):
    """该引擎把模型当选项值(-m)还是位置参数(model)。默认 -m:
    多认一个位置参数的引擎,服务起不来;反过来只是启动失败得晚一点。"""
    low = str(path or "").lower()
    for needle, _, style in EXE_ENGINES:
        if needle in low:
            return style
    return "-m"


def launcher_only_reason(path):
    """该引擎不能由本模块按 TOML 参数启动。返回原因,否则 None。"""
    low = str(path or "").lower()
    for needle, why in LAUNCHER_ONLY:
        if needle in low:
            return why
    return None


def _norm_name(name):
    """把 'L1-main-Qwen3.6-35B-APEX' 与 'L1-Qwen3.6-35B-APEX' 看成同一个。
    Llama-Rig 给主力预设加了 main- 段,实测文档里写的是短名。"""
    s = str(name or "").lower().replace("-main-", "-").replace("-main", "")
    return s


import re

# 注释掉的赋值行,例如 "# n_cpu_moe = 15"。它们记录的是"这里可以改什么",
# 不是"这套配置测出了什么",显示在结论位会误导。
_TOML_LINE = re.compile(r"^[A-Za-z0-9_.-]+\s*=\s*[^\s].*")


def _normalise_note(text):
    return re.sub(r"\s{2,}", " ", str(text or "")).strip()


def note_corpus(install):
    """统计每行注释出现在多少个配置文件里。

    这些 TOML 是从同一份模板复制的,所以【多个文件共有的行】几乎一定是模板说明
    (路径解释、界面提示、"必须显式写"之类),而不是某一套配置的实测结论。
    用出现次数判断,而不是维护一长串黑名单:模板以后改了也不会漏。
    """
    import llamarig as R
    counts = {}
    for path in R.list_configs(install):
        try:
            cfg = R.load_config(path)
        except Exception:
            continue
        for n in cfg.get("notes") or []:
            s = _normalise_note(n)
            if len(s) >= 12:
                counts[s] = counts.get(s, 0) + 1
    return counts


def _clean_notes(notes, corpus=None, limit=3, shared_at=2):
    """只留这套配置独有、且含实际内容的注释行。"""
    import re as _re
    out = []
    for c in (notes or []):
        s = _normalise_note(c)
        if len(s) < 12 or not _re.search(r"[\u4e00-\u9fff]|[A-Za-z]", s):
            continue
        if _TOML_LINE.match(s):
            continue          # 被注释掉的赋值行(key = value),不是结论
        if corpus is not None and corpus.get(s, 0) >= shared_at:
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# 来源一:Llama-Rig 的 *.config.toml
# ---------------------------------------------------------------------------
def load_from_rig():
    import llamarig as R
    installs = R.find_install()
    if not installs:
        return [], None
    install = installs[0]
    corpus = note_corpus(install)
    out = []
    for path in R.list_configs(install):
        cfg = R.load_config(path)
        raw = cfg.get("raw_fields") or {}
        roles = cfg.get("roles") or {}
        p = {
            "id": cfg["name"],
            "source": "llamarig",
            "source_path": path,
            "engine": cfg.get("engine"),
            "exe": cfg.get("exe"),
            "model_arg_style": model_arg_style(cfg.get("exe") or cfg.get("exe_name")),
            "model": os.path.basename(str(cfg.get("model") or "")) or None,
            "model_path": cfg.get("model"),
            "mmproj": os.path.basename(str(cfg.get("mmproj") or "")) or None,
            "notes": _clean_notes(cfg.get("notes"), corpus),
            "extra_args": cfg.get("extra_args") or "",
            "raw_fields": raw,
        }
        p["params"] = _params_from_rig(raw, roles)
        # 没有模型的配置是模板(没有可启动的东西),不是一份预设。
        # 它的注释里全是"某一节有什么字段"的说明,当结论显示会误导。
        p["placeholder"] = not p["model"]
        if p["placeholder"]:
            p["notes"] = []
        out.append(p)
    return out, install


def _params_from_rig(raw, roles):
    """把 Llama-Rig 的字段压成"标签 -> 值",按 PARAM_LABELS 的顺序。
    空值不写:缺字段是事实(这套配置没设),写成 0 就是编造。"""
    p = {}
    p["host"] = raw.get("host")
    p["port"] = raw.get("port")
    p["ctx"] = raw.get("ctx_size")
    p["ngl"] = raw.get("n_gpu_layers")
    # KV 精度有三个来源:TOML 的 type_k/type_v 字段、extra_args 里的 -ctk/-ctv、
    # 以及 kvmem 引擎的 --kv-dtype。只读第一个字段会漏掉整套 Bonsai 配置
    # (它们的 KV 精度只写在 extra_args 里),漏掉就会把"q4_0 的配置"和
    # "q8_0 的实测"当成同一套,进而报出一个它没跑出过的速度。
    tk, tv = raw.get("type_k"), raw.get("type_v")
    rk, rv = roles.get("kv_k"), roles.get("kv_v")
    kvd = roles.get("kv_dtype")
    if tk or tv:
        p["kv"] = "%s / %s" % (tk or "-", tv or "-")
    elif kvd:
        p["kv"] = str(kvd)
    elif rk or rv:
        p["kv"] = "%s / %s" % (rk or "-", rv or "-")
    km = roles.get("kv_mem_method")
    if km:
        p["kv_mem"] = "%s%s" % (km, (" block=" + str(roles["kv_block"]))
                                if roles.get("kv_block") else "")
    if raw.get("batch_size") or raw.get("ubatch_size"):
        p["batch"] = "%s / %s" % (raw.get("batch_size") or "-", raw.get("ubatch_size") or "-")
    if raw.get("cpu_moe") is not None:
        p["moe"] = "--cpu-moe(全部专家在 CPU)" if raw.get("cpu_moe") else "关闭"
    if raw.get("n_cpu_moe") is not None:
        p["moe"] = "n_cpu_moe=%s" % raw.get("n_cpu_moe")
    spec = roles.get("spec")
    if spec:
        p["spec"] = "%s%s" % (spec, (" n-max=" + str(roles["spec_draft_n"]))
                              if roles.get("spec_draft_n") else "")
    t, tp, tk2 = raw.get("temp"), raw.get("top_p"), raw.get("top_k")
    if t is not None or tp is not None:
        bits = []
        if t is not None:
            bits.append("temp=" + str(t))
        if tp is not None:
            bits.append("top_p=" + str(tp))
        if tk2 is not None:
            bits.append("top_k=" + str(tk2))
        if raw.get("min_p") is not None:
            bits.append("min_p=" + str(raw.get("min_p")))
        p["sampling"] = " ".join(bits)
    if raw.get("threads") is not None:
        p["threads"] = str(raw.get("threads")) + (
            "/" + str(raw.get("threads_batch")) if raw.get("threads_batch") else "")
    if roles.get("alias"):
        p["alias"] = roles["alias"]
    return {k: v for k, v in p.items() if v not in (None, "", "None")}


# ---------------------------------------------------------------------------
# 来源二:Llama.cpp Desktop 的 *.config.toml
# ---------------------------------------------------------------------------
# 这两处配置都叫 *.config.toml、字段名也几乎一样,但**不是同一份东西**:
# Llama-Rig 那份是通用的 llama.cpp 预设;llama-cpp-desktop 这份是用户手工
# 调优过的当前主力(KV17 系列),参数全在 extra_args 里,且引擎可以是
# ninfer-kvmem(位置参数模型 + 完全不同的参数名)。
#
# 两处必须分开扫、分开标来源:同名预设出现在两边时,谁是谁要能看出来。
DESKTOP_DIRS = [
    os.path.join(os.path.dirname(ROOT), "03_tools", "llama-cpp-desktop", "configs"),
]


def find_desktop_dirs():
    return [d for d in DESKTOP_DIRS if os.path.isdir(d)]


def load_from_desktop():
    """扫 Llama.cpp Desktop 的配置。用 llamarig.load_config 解析:
    格式一致(都是那套 TOML 字段),但引擎与参数语义由本模块重新判定。"""
    import glob
    import llamarig as R
    out, dirs = [], find_desktop_dirs()
    for d in dirs:
        paths = sorted(glob.glob(os.path.join(d, "*.config.toml")))
        if not paths:
            continue
        corpus = {}
        loaded = []
        for path in paths:
            try:
                cfg = R.load_config(path)
            except Exception:
                continue
            loaded.append(cfg)
            for n in cfg.get("notes") or []:
                s = _normalise_note(n)
                if len(s) >= 12:
                    corpus[s] = corpus.get(s, 0) + 1
        for cfg in loaded:
            raw = cfg.get("raw_fields") or {}
            roles = cfg.get("roles") or {}
            exe = str(raw.get("llama_server_path") or cfg.get("exe") or "")
            p = {
                "id": cfg["name"],
                "source": "desktop",
                "source_path": cfg.get("path"),
                # 引擎由可执行文件名判断,不信配置里没写的字段:
                # 这套 TOML 没有 engine 键,靠 llama_server_path 才看得出是 ninfer。
                "engine": engine_from_exe(exe) or "unknown",
                "exe": exe or None,
                "model_arg_style": model_arg_style(exe),
                "model": os.path.basename(str(cfg.get("model") or "")) or None,
                "model_path": cfg.get("model"),
                "mmproj": os.path.basename(str(cfg.get("mmproj") or "")) or None,
                "mmproj_path": cfg.get("mmproj") or None,
                "notes": _clean_notes(cfg.get("notes"), corpus),
                # extra_args 逐字保留:KV17 的调优结果(dt=2 / ngram=31 /
                # adaptive-mtp ...)全在这串里,拆开重拼就等于改掉实验条件。
                "extra_args": cfg.get("extra_args") or "",
                "raw_fields": raw,
                "launcher_only": launcher_only_reason(exe),
                "launcher": raw.get("launcher") or None,
            }
            p["params"] = _params_from_desktop(raw, roles)
            p["placeholder"] = not p["model"]
            if p["placeholder"]:
                p["notes"] = []
            out.append(p)
    return out, dirs[0] if dirs else None


def _params_from_desktop(raw, roles):
    """Desktop 配置的参数。机型差异:这套 TOML 的 KV 精度、投机解码等
    多数只写在 extra_args 里,所以先按字段取,缺了再从 roles 补。"""
    p = _params_from_rig(raw, roles)
    extra = str(raw.get("extra_args") or "")
    # extra_args 里常见的几项,只用于**显示**,不参与启动命令的拼装
    for key, role, label in (
        ("draft-tokens", "draft_tokens", "草稿 token"),
        ("ngram-draft-tokens", "ngram", "ngram 草稿"),
        ("prefill-chunk", "prefill", "prefill 块"),
        ("kvmem-budget", "kv_budget", "KV 预算 B"),
        ("kvmem-gen-reserve", "kv_gen", "生成预留 R"),
        ("kvmem-host-mib", "kv_host", "Host 预算 H"),
    ):
        m = re.search(r"--%s\s+(\S+)" % re.escape(key), extra)
        if m:
            p[role] = m.group(1)
    if "--adaptive-mtp" in extra:
        p["adaptive"] = "on"
    if "--full-draft-head" in extra:
        p["full_draft"] = "on"
    return p


# ---------------------------------------------------------------------------
# 来源三:本项目 config/profiles.json
# ---------------------------------------------------------------------------
def load_from_project():
    try:
        with open(PROFILES_JSON, encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        return []
    out = []
    for name, prof in (store.get("profiles") or {}).items():
        vars_ = prof.get("vars") or {}
        p = {
            "id": name,
            "source": "profiles.json",
            "source_path": PROFILES_JSON,
            "engine": engine_from_exe(prof.get("exe")) or prof.get("engine") or "llama.cpp",
            "exe": prof.get("exe"),
            "model": os.path.basename(str(prof.get("model") or "")) or None,
            "model_path": prof.get("model"),
            "mmproj": os.path.basename(str(prof.get("mmproj") or "")) or None,
            "notes": [prof.get("note") or ""],
            "extra_args": "",
            "raw_fields": vars_,
            "params": _params_from_vars(vars_),
        }
        out.append(p)
    return out


def _params_from_vars(vars_):
    p = {}
    p["host"] = vars_.get("--host")
    p["port"] = vars_.get("--port")
    p["ctx"] = vars_.get("--ctx-size")
    p["ngl"] = vars_.get("--n-gpu-layers")
    tk, tv = vars_.get("-ctk"), vars_.get("-ctv")
    if tk or tv:
        p["kv"] = "%s / %s" % (tk or "-", tv or "-")
    if vars_.get("--batch-size") or vars_.get("--ubatch-size"):
        p["batch"] = "%s / %s" % (vars_.get("--batch-size") or "-", vars_.get("--ubatch-size") or "-")
    if vars_.get("--cpu-moe"):
        p["moe"] = "--cpu-moe(全部专家在 CPU)"
    if vars_.get("--n-cpu-moe") is not None:
        p["moe"] = "n_cpu_moe=%s" % vars_.get("--n-cpu-moe")
    if vars_.get("--spec-type"):
        p["spec"] = "%s%s" % (vars_["--spec-type"],
                              (" n-max=" + str(vars_["--spec-draft-n-max"]))
                              if vars_.get("--spec-draft-n-max") else "")
    if vars_.get("--temp") is not None:
        p["sampling"] = "temp=%s top_p=%s top_k=%s" % (vars_.get("--temp"),
                                                      vars_.get("--top-p"), vars_.get("--top-k"))
    if vars_.get("--threads") is not None:
        p["threads"] = str(vars_["--threads"])
    return {k: v for k, v in p.items() if v not in (None, "", "None")}


# ---------------------------------------------------------------------------
# 实测序列:两条,互不覆盖
# ---------------------------------------------------------------------------
def series_gui():
    """系列 A:2026-10-06 的 GUI 实测(有首字延迟与启动耗时)。"""
    if not os.path.isfile(SPEED_JSON):
        return None
    try:
        with open(SPEED_JSON, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    by_name, by_model = {}, {}
    for rec in d.get("presets") or []:
        item = {
            "p1_ttft_ms": rec.get("p1_ttft_ms"), "p1_tps": rec.get("p1_tps"),
            "p2_ttft_ms": rec.get("p2_ttft_ms"), "p2_tps": rec.get("p2_tps"),
            "load_s": rec.get("load_s"), "weights_gb": rec.get("weights_gb"),
            "verdict": rec.get("verdict"), "note": rec.get("note"),
            "port": rec.get("port"),
        }
        by_name[_norm_name(rec.get("preset"))] = item
        by_model[str(rec.get("model") or "").lower()] = item
    return {"id": "gui-20261006", "label": "GUI 实测", "date": d.get("_measured"),
            "machine": d.get("_machine"), "method": d.get("_method"),
            "source": d.get("_source"), "by_name": by_name, "by_model": by_model}


def series_catalog(install=None):
    """系列 B:Llama-Rig model-catalog.json 的 verified(2026-10-15)。
    这份没有首字延迟,只有 p1/p2 吞吐与体积、量化、专家类型。"""
    if not install:
        import llamarig as R
        found = R.find_install()
        install = found[0] if found else None
    if not install:
        return None
    path = os.path.join(install, "configs", "model-catalog.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    by_model = {}
    for m in d.get("models") or []:
        v = m.get("verified") or {}
        if not v:
            continue
        by_model[str(m.get("file") or "").lower()] = {
            "p1_tps": v.get("p1_tps"), "p2_tps": v.get("p2_tps"),
            "load_s": v.get("load_s"), "weights_gb": m.get("size_gb"),
            "ctx": v.get("ctx"), "kv": v.get("kv_dtype"),
            "ncmoe": v.get("ncmoe"), "cpu_moe": v.get("cpu_moe"),
            "date": v.get("date"), "source": v.get("source"),
            "quant": m.get("quant"), "params": m.get("params"),
            "roles": m.get("roles"), "note": m.get("note"),
            "strata_compatible": m.get("strata_compatible"),
        }
    return {"id": "catalog-20261015", "label": "模型目录实测",
            "date": "2026-10-15", "source": os.path.relpath(path, ROOT),
            "machine": d.get("machine"), "by_model": by_model}


def attach_speed(presets, series):
    """把每条实测挂到预设上,并记下是怎么挂上的(via)。

    名字精确对上的才算"这套配置的实测"。只有模型文件对上的是"这个模型的实测,
    配置可能不同"——例如 Bonsai 五套配置共用同一个 gguf,但 catalog 里那条
    22.7 t/s 是在 ctx=65536 / kv=q8_0 下测的,不是 q4_0 的 Balanced 测的。
    两者混在一起显示,读者会以为 Balanced 跑出过 22.7 t/s。

    挂不上就留空:空着是"这套预设没测过",填 0 才是造假。
    """
    for p in presets:
        p["speed"] = {}
        p["speed_via"] = {}
    for s in series:
        if not s:
            continue
        for p in presets:
            rec = (s.get("by_name") or {}).get(_norm_name(p["id"]))
            via = "name"
            if rec is None and p.get("model"):
                rec = (s.get("by_model") or {}).get(str(p["model"]).lower())
                via = "model"
            if rec is not None and via == "model":
                # 同一模型文件还不够:配置不同,速度就不是这套配置的数字。
                # catalog 里带 ctx / kv 时可以比;比不上的只能标"未能核对"。
                same = _same_config(rec, p.get("params") or {})
                via = "config" if same is True else ("model" if same is False else "model_unknown")
            if rec is not None:
                p["speed"][s["id"]] = rec
                p["speed_via"][s["id"]] = via
    return presets


def _same_config(rec, params):
    """catalog 记录的 ctx / kv 与预设参数是否一致。

    返回三态而不是布尔:True 相同、False 不同、None 无法判断。
    缺一侧数据时返回 None —— "无法确认"和"确认不同"是两件事,合并成一个
    False 会让"没能核对"看起来像"核对过且不符"。
    """
    rctx, rkv = rec.get("ctx"), rec.get("kv")
    pctx, pkv = params.get("ctx"), params.get("kv")
    if rctx is None or pctx is None:
        return None
    try:
        if int(str(rctx).strip()) != int(str(pctx).strip()):
            return False
    except (TypeError, ValueError):
        return None
    if rkv and pkv:
        return str(rkv).strip().lower().replace(" ", "") in \
            str(pkv).strip().lower().replace(" ", "")
    return None if (rkv is None or pkv is None) else True


# ---------------------------------------------------------------------------
# 在跑探测
# ---------------------------------------------------------------------------
def listening_ports(lo=1024, hi=49151):
    """本机回环监听端口,连同 PID。用 netstat:实测 Get-NetTCPConnection 在本机
    返回空(权限受限),tasklist / Get-CimInstance 直接被拒绝访问。"""
    try:
        import subprocess
        out = subprocess.run(["cmd", "/c", "netstat -ano"], capture_output=True,
                             text=True, errors="ignore", timeout=30).stdout or ""
    except Exception:
        return []
    rows = {}
    for line in out.splitlines():
        tok = line.split()
        if len(tok) < 5 or tok[0].upper() != "TCP" or tok[3].upper() != "LISTENING":
            continue
        try:
            port = int(tok[1].rsplit(":", 1)[1])
            pid = int(tok[4])
        except (IndexError, ValueError):
            continue
        if port < lo or port > hi:
            continue
        addr = tok[1].rsplit(":", 1)[0]
        if addr not in ("127.0.0.1", "0.0.0.0", "::1", "::"):
            continue
        rows.setdefault(port, pid)
    return sorted(rows.items())


def _get_json(port, path, timeout):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            raw = r.read(400000)
            if not raw:
                return None
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None


def probe(port, pid=None, timeout=0.35):
    """问一个端口"你是谁"。只认能证明自己是模型服务的响应。

    实测本机 2743 / 9210 / 13344 / 28317 四个端口也回 /props 的 200 与 JSON,
    内容是 errcode / base64 / "Not implemented"。所以判据是字段,不是状态码:
    必须有 model_path 或 default_generation_settings。
    """
    d = _get_json(port, "/props", timeout)
    out = {"port": port, "pid": pid, "engine": None, "model_path": None,
           "alias": None, "n_ctx": None, "via": None}
    if isinstance(d, dict) and ("model_path" in d or "default_generation_settings" in d):
        out["engine"] = "llama.cpp"
        out["via"] = "/props"
        out["model_path"] = d.get("model_path")
        out["alias"] = d.get("model_alias")
        gen = d.get("default_generation_settings") or {}
        out["n_ctx"] = gen.get("n_ctx") if isinstance(gen, dict) else None
        return out
    m = _get_json(port, "/v1/models", timeout)
    items = (m or {}).get("data") if isinstance(m, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        out["engine"] = "openai-compatible"
        out["via"] = "/v1/models"
        out["alias"] = items[0].get("id")
        return out
    return None


def scan_running(ports=None, timeout=0.35, workers=16):
    """并发探全部回环端口。实测 51 个端口串行要 3.3s,并发后约 1s。"""
    ports = ports if ports is not None else listening_ports()
    if isinstance(ports, list) and ports and isinstance(ports[0], tuple):
        targets = ports
    else:
        targets = [(p, None) for p in (ports or [])]
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda t: probe(t[0], t[1], timeout), targets))
    return [r for r in results if r]


def match_preset(presets, srv):
    """判断某台在跑的服务对应哪套预设。分级,并把理由写出来:
    3 = 模型文件 + n_ctx 都对上(最强)
    2 = 模型文件对上,上下文不同(可能是改了 ctx 的同一套)
    1 = 只有别名对上
    0 = 对不上
    并列最高分时不算命中:两套相同的预设本来就无法从服务端区分。"""
    base = os.path.basename(str(srv.get("model_path") or "")).lower()
    alias = _norm_name(srv.get("alias") or "")
    ctx = srv.get("n_ctx")
    scored = []
    for p in presets:
        score, why = 0, None
        pmodel = str(p.get("model") or "").lower()
        try:
            pctx = int(p.get("params", {}).get("ctx"))
        except (TypeError, ValueError):
            pctx = None
        if base and pmodel and base == pmodel:
            if ctx and pctx and int(ctx) == pctx:
                score, why = 3, "模型文件与上下文长度都一致"
            else:
                score, why = 2, "模型文件一致,上下文 %s != %s" % (ctx, pctx)
        elif alias and alias == _norm_name(p.get("params", {}).get("alias") or ""):
            score, why = 1, "只有别名一致"
        if score:
            scored.append((score, why, p["id"]))
    if not scored:
        return {"preset": None, "confidence": 0, "why": "没有匹配的预设"}
    best = max(s for s, _, _ in scored)
    top = [x for x in scored if x[0] == best]
    if len(top) > 1:
        # 并列不是"认不出来",而是"这几套共用同一个模型文件"——Bonsai 五档、
        # 多个 Qwen3.x 配置都是这种情况。服务只报了模型路径,能确定的就是
        # "它在跑这个模型",再往细分就是猜测。所以给出候选并说清差在哪。
        cands = [x[2] for x in top]
        pctxs = []
        for p in presets:
            if p["id"] not in cands:
                continue
            try:
                pctxs.append(str(int(p.get("params", {}).get("ctx"))))
            except (TypeError, ValueError):
                continue
        if ctx and pctxs:
            # ctx 能区分时就只剩一组候选了,所以走到这里说明 ctx 区分不了:
            # 要么与某些候选相同,要么全都不相同。这两种说法必须分开写,
            # 否则会出现"服务 ctx=65536 都不等于它们的 65536"这种自相矛盾的话。
            if str(ctx) in pctxs:
                why = ("同一模型有 %d 套预设的 ctx 都是 %s，仅凭服务自述无法区分"
                       % (len(top), ctx))
            else:
                why = ("同一模型有 %d 套预设，服务 ctx=%s 都不是它们的 %s"
                       % (len(top), ctx, "/".join(sorted(set(pctxs)))))
        else:
            why = "同一模型有 %d 套预设，服务未报告可区分的信息" % len(top)
        return {"preset": None, "confidence": best, "ambiguous": True,
                "why": why, "candidates": cands}
    return {"preset": top[0][2], "confidence": best, "why": top[0][1]}


def _running_record(srv, m):
    return {"port": srv["port"], "pid": srv.get("pid"),
            "engine": srv.get("engine"), "alias": srv.get("alias"),
            "n_ctx": srv.get("n_ctx"), "via": srv.get("via"),
            "confidence": m["confidence"], "why": m["why"],
            "candidates": m.get("candidates")}


def mark_running(presets, servers):
    """把在跑状态写到预设上,并返回没被任何预设认领的服务。

    并列命中的服务不丢弃、也不硬挑一个:它带着候选列表落到那几套预设上,
    状态是 "ambiguous"。丢掉它等于假装本机没东西在跑,硬挑一个等于编造
    "就是这套"。"""
    for p in presets:
        p["running"] = None
        p["running_ambiguous"] = False
        p["running_partial"] = False
    orphans = []
    by_id = {p["id"]: p for p in presets}
    for srv in servers:
        m = match_preset(presets, srv)
        rec = _running_record(srv, m)
        if m["preset"]:
            target = by_id.get(m["preset"])
            if target is not None:
                target["running"] = rec
            continue
        if m.get("ambiguous"):
            for cid in m.get("candidates") or []:
                cand = by_id.get(cid)
                if cand is None:
                    continue
                cand["running"] = rec
                cand["running_ambiguous"] = True
                # 上下文不同的那一批,置信度是 2 而不是 3:它确实在跑这个模型,
                # 但跑的不是这套配置的参数。
                cand["running_partial"] = (rec["confidence"] < 3)
            continue
        orphans.append(rec)
    return orphans


def shared_ports(presets):
    """同一端口上有多套预设 = 换配置必须停旧进程。这是真实风险,要显示。"""
    m = {}
    for p in presets:
        port = p.get("params", {}).get("port")
        if port is None:
            continue
        # 端口在 TOML 里是 int、在 profiles.json 里是 str;统一成字符串再分组,
        # 否则同一条 TCP 端口会被当成两个。
        m.setdefault(str(port).strip(), []).append(p["id"])
    return {k: v for k, v in sorted(m.items()) if len(v) > 1}


_CACHE = {"t": 0.0, "running": None, "orphans": None}


def catalog(force_scan=False, ttl=20.0):
    """完整目录。在跑信息带缓存:UI 每次切 Tab 都全端口扫描没有意义,
    但结果会比机器状态旧,所以 TTL 是秒级,并且 --running / 扫描按钮能强制刷新。"""
    import time
    rig_presets, install = load_from_rig()
    desktop_presets, desktop_dir = load_from_desktop()
    presets = disambiguate_ids(rig_presets + desktop_presets + load_from_project())
    series = [s for s in (series_gui(), series_catalog(install)) if s]
    attach_speed(presets, series)

    now = time.time()
    if force_scan or _CACHE["running"] is None or (now - _CACHE["t"]) > ttl:
        servers = scan_running()
        orphans = mark_running(presets, servers)
        _CACHE.update({"t": now, "running": servers, "orphans": orphans})
    else:
        # 复用缓存的在跑集合,但每次都用新的 presets 列表重新挂一遍:
        # 目录可能刚被重新解析过,直接把上次的对象返回会带着旧的 speed 字段。
        orphans = mark_running(presets, _CACHE["running"])

    for p in presets:
        if p.get("running") is None:
            p["state"] = "idle"
        elif p["running_ambiguous"]:
            # 候选之一,不是它自己:既不标"运行中",也不算空闲。
            p["state"] = "ambiguous" if not p["running_partial"] else "maybe"
        elif p["running"]["confidence"] >= 2:
            p["state"] = "running"
        else:
            p["state"] = "maybe"
    # 在跑的排最前面:这个页面的用处是"现在哪个能用",不是按字母查目录。
    # 用 sorted 而不是 sort,保持同一份目录的原始顺序作为次级键(sort 是稳定的)。
    order = {"running": 0, "ambiguous": 1, "maybe": 2, "idle": 3}
    presets = sorted(presets, key=lambda p: order.get(p["state"], 3))
    return {
        "ok": True,
        "sources": {"rig": install, "desktop": desktop_dir,
                    "profiles": os.path.relpath(PROFILES_JSON, ROOT)
                    if os.path.isfile(PROFILES_JSON) else None},
        "series": [{"id": s["id"], "label": s["label"], "date": s.get("date"),
                    "machine": s.get("machine"), "method": s.get("method"),
                    "source": s.get("source")} for s in series],
        "labels": [{"key": k, "label": v} for k, v in PARAM_LABELS],
        "presets": presets,
        "running": _CACHE["running"] or [],
        "orphans": orphans,
        "shared_ports": shared_ports(presets),
        "measured_only": measured_only(presets, series),
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_CACHE["t"] or now)),
    }


def disambiguate_ids(presets):
    """两个源撞名时给后来的加来源后缀。

    两个配置源都用 L1-*/N1-* 这种编号习惯,撞名是常态。若不处理,队列按 id
    选预设会把两条不同配置当成同一条 —— 表现是"排了 3 套,只跑了 2 套",
    而且没有任何报错。撞名要能看见,不能靠运气。
    """
    seen = {}
    for p in presets:
        pid = p["id"]
        if pid not in seen:
            seen[pid] = p.get("source")
            continue
        src = p.get("source") or "?"
        first = seen[pid]
        # 先来的那个也改名,否则它仍是裸 id,分不清哪个是哪个
        for q in presets:
            if q["id"] == pid and q is not p and q.get("_dup_renamed") is None:
                q["_dup_renamed"] = True
                q["id"] = "%s@%s" % (pid, first)
                q["id_dup_of"] = pid
                break
        p["id"] = "%s@%s" % (pid, src)
        p["id_dup_of"] = pid
    for p in presets:
        p.pop("_dup_renamed", None)
    return presets


def disambiguate_ids(presets):
    """两个源撞名时给双方都加来源后缀。

    两个配置源都用 L1-*/N1-* 这种编号习惯,撞名是常态。不处理的话,队列按 id
    选预设会把两条不同配置当成同一条 —— 表现是"排了 3 套只跑了 2 套",
    而且没有任何报错。撞名要能看见。
    """
    seen = {}
    for p in presets:
        pid = p["id"]
        if pid in seen:
            continue
        seen[pid] = p.get("source") or "?"
    counted = {}
    for p in presets:
        counted.setdefault(p["id"], []).append(p)
    for pid, items in counted.items():
        if len(items) < 2:
            continue
        for p in items:
            p["id_dup_of"] = pid
            p["id"] = "%s@%s" % (pid, p.get("source") or "?")
    return presets


def measured_only(presets, series):
    """有实测数据但找不到对应预设配置的条目。
    L2-Ornith-ncmoe30/42 就是这种情况:Llama-Rig 里只有 ncmoe36。
    照实列出来,不能因为它没有配置文件就当没测过。"""
    known = set()
    for p in presets:
        for sid in p.get("speed") or {}:
            known.add((sid, _norm_name(p["id"])))
    out = []
    for s in series:
        for name in (s.get("by_name") or {}):
            if (s["id"], name) not in known:
                rec = s["by_name"][name]
                out.append({"series": s["id"], "name": name, "speed": rec})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--running", action="store_true")
    ap.add_argument("--no-scan", action="store_true")
    args = ap.parse_args()

    doc = catalog(force_scan=not args.no_scan)
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0

    if args.running:
        if not doc["running"]:
            print("本机没有在跑模型服务。")
            return 0
        print("在跑的服务 %d 个" % len(doc["running"]))
        for s in doc["running"]:
            print("  端口 %-6s pid %-7s %-18s alias=%s n_ctx=%s"
                  % (s["port"], s.get("pid"), s.get("engine"), s.get("alias"), s.get("n_ctx")))
        if doc["orphans"]:
            print(NL + "  未被任何预设认领:")
            for o in doc["orphans"]:
                print("    端口 %-6s %s" % (o["port"], o["why"]))
        return 0

    print("预设 %d 个" % len(doc["presets"]))
    for key in ("rig", "desktop", "profiles"):
        if doc["sources"].get(key):
            print("  来源 %-9s %s" % (key, doc["sources"][key]))
    for s in doc["series"]:
        print("  实测序列 %-18s %s" % (s["id"], s["date"]))
    print()
    print("  %-32s %-10s %-9s %-8s %-18s %s"
          % ("预设", "引擎", "状态", "端口", "在跑", "模型"))
    print("  " + "-" * 104)
    for p in doc["presets"]:
        st = {"running": "运行中", "ambiguous": "候选之一",
              "maybe": "疑似", "idle": "未运行"}[p["state"]]
        run = ""
        if p.get("running"):
            run = "port %s n_ctx=%s" % (p["running"]["port"], p["running"].get("n_ctx"))
        print("  %-32s %-10s %-9s %-8s %-18s %s"
              % (p["id"][:32], p.get("engine"), st,
                 p.get("params", {}).get("port") or "-", run,
                 (p.get("model") or "-")[:34]))
    print()
    if doc["shared_ports"]:
        print("  端口共享(换配置必须先停旧进程):")
        for port, names in doc["shared_ports"].items():
            print("    %-6s %s" % (port, ", ".join(names)))
    if doc["measured_only"]:
        print(NL + "  只有实测、无对应预设配置:")
        for it in doc["measured_only"]:
            print("    %-24s %s" % (it["name"], it["series"]))
    print(NL + "  --json 完整目录 | --running 只看在跑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
