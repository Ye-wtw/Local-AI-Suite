#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_report.py - one Markdown report over a BATCH of result files.

Why this exists: results/ holds hundreds of files from different presets,
different dimensions and different judge versions. A human comparing them by
hand eventually "averages away" the difference between files that are not
comparable at all. This report refuses to do that:

  1. Every cell carries its trust mark. A number produced by a judge that has
     since changed is still printed (it is data), but it is labelled, never
     silently merged with a current-judge number.
  2. A dimension with no data is "—", never 0 and never an empty cell. An empty
     cell reads as "0" to a tired human and as "fine" to a script.
  3. No value is estimated. A metric that was never sampled says 未采集.

Usage:
  python runner/batch_report.py --results "results/*kvmem*.json" --out results/batch_report_test.md
  python runner/batch_report.py --results results/*.json --out report.md --max-lines 600
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
BT = chr(96)                       # markdown backtick
# 低于这个题数就在矩阵格子里标出 n:小样本的正确率看着精确,实际波动极大。
SMALL_N = 30

MISSING = "—"                      # 缺数据; NOT 0, NOT empty
NO_SAMPLE = "未采集"

DIM_LABEL = {
    "D1": "速度", "D2": "选择", "D3": "数学", "D4": "代码", "D5": "指令",
    "D6": "Agent", "D7": "长上下文", "D8": "可靠性", "D9": "实战",
    "D10": "棋类", "D11": "压缩", "D12": "多轮", "DYN": "动态",
    "PUBLIC": "公开基准", "LCB": "LCB",
}

MARK_OK = "✅"
MARK_WARN = "⚠️"

try:
    import judge_version as _jvmod
except Exception:                                    # pragma: no cover
    _jvmod = None


def _current_versions():
    """{suite-or-empty: expected hash} - tolerant of judge_version.py being absent."""
    if _jvmod is None:
        return {}
    try:
        out = {"": _jvmod.judge_version()}
        for s in getattr(_jvmod, "SUITE_FILES", {}):
            out[s] = _jvmod.judge_version(s)
        return out
    except Exception:
        return {}


CURRENT_VERSIONS = _current_versions()
CURRENT_CORE = CURRENT_VERSIONS.get("", "")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp_of(source):
    """Run stamp embedded in a result filename, e.g. 20261103-223138."""
    m = re.search(r"(\d{8}-\d{6})", os.path.basename(source or ""))
    return m.group(1) if m else ""


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _pct(v, digits=1):
    f = _num(v)
    if f is None:
        return MISSING
    return "%.*f%%" % (digits, f * 100.0)


def _int(v, digits=0):
    f = _num(v)
    if f is None:
        return MISSING
    return ("%." + str(digits) + "f") % f


def _safe_name(s):
    out = re.sub(r"[^0-9A-Za-z_.-]+", "_", s or "set")
    return out.strip("_") or "set"


def _dim_sort_key(d):
    m = re.match(r"^([A-Za-z]+)(\d+)$", d or "")
    if m:
        return (0, m.group(1), int(m.group(2)))
    return (1, d or "?", 0)


def _records_of(d):
    recs = d.get("records")
    return recs if isinstance(recs, list) else []


def _bucket_from_records(recs):
    """Per-dimension counts straight from records (errors excluded, never scored)."""
    out = {}
    for r in recs:
        if not isinstance(r, dict) or r.get("error"):
            continue
        dim = r.get("dim") or "?"
        b = out.setdefault(dim, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if r.get("correct") else 0
    return out


# --------------------------------------------------------------- normalisation
def normalize(d, source=""):
    """One result file -> a flat set of facts. Tolerant: never raises on shape."""
    if not isinstance(d, dict):
        return None
    summary = d.get("summary") if isinstance(d.get("summary"), dict) else {}
    recs = _records_of(d)

    def pick(key):
        for holder in (summary, d):
            if isinstance(holder, dict) and holder.get(key) is not None:
                return holder.get(key)
        return None

    endpoint = pick("endpoint") or ""
    model = pick("model") or ""
    suite = pick("suite")
    preset = pick("preset")
    dim = pick("dim")
    level = pick("level")
    launch = pick("launch")
    env = pick("environment")
    jv = pick("judge_version")

    n = _num(pick("n"))
    if n is None and recs:
        n = float(len(recs))
    correct = _num(pick("correct"))
    n_valid = _num(pick("n_valid"))
    n_err = _num(pick("n_errors"))
    if n_err is None:
        n_err = float(sum(1 for r in recs if isinstance(r, dict) and r.get("error")))
    accuracy = _num(pick("accuracy"))
    if accuracy is None and correct is not None:
        denom = n_valid if n_valid is not None else n
        if denom:
            accuracy = correct / denom

    dims = {}
    by_dim = pick("by_dim")
    by_subcat = pick("by_subcat")
    if dim and dim != "?":
        dims[str(dim)] = {
            "n": n, "correct": correct, "n_valid": n_valid, "n_errors": n_err,
            "accuracy": accuracy, "by_subcat": by_subcat if isinstance(by_subcat, dict) else {},
            "records": recs, "elapsed_s": _num(pick("elapsed_s")),
        }
    elif isinstance(by_dim, dict) and by_dim:
        for k, v in by_dim.items():
            if not isinstance(v, dict):
                continue
            cnt = _num(v.get("n"))
            cor = _num(v.get("correct"))
            dims[str(k)] = {
                "n": cnt, "correct": cor, "n_valid": cnt, "n_errors": 0.0,
                "accuracy": (cor / cnt) if (cnt and cor is not None) else None,
                "by_subcat": {}, "records": [], "elapsed_s": None,
            }
    elif recs:
        for k, v in _bucket_from_records(recs).items():
            dims[k] = {
                "n": float(v["n"]), "correct": float(v["correct"]),
                "n_valid": float(v["n"]), "n_errors": 0.0,
                "accuracy": (v["correct"] / v["n"]) if v["n"] else None,
                "by_subcat": {}, "records": [], "elapsed_s": None,
            }

    if not dims and not n:
        return None                                  # a launch snapshot, not a measurement

    expected = CURRENT_VERSIONS.get(suite) if suite in CURRENT_VERSIONS else CURRENT_VERSIONS.get("")
    if not jv:
        trust = "none"                               # 无法追溯
    elif CURRENT_CORE and str(jv) == CURRENT_CORE:
        trust = "ok"
    elif expected and str(jv) == expected:
        trust = "ok"
    elif not expected:
        trust = "unknown"
    else:
        trust = "drift"                              # 版本已变

    return {
        "source": source, "stamp": _stamp_of(source),
        # 预设身份由 run.py --preset-id 写入。老结果没有这个字段(None),
        # 此时退回按 endpoint/model 归并 —— 有更强的身份就用更强的。
        "preset": preset or None,
        "endpoint": endpoint, "model": model, "suite": suite,
        "level": level, "judge_version": jv, "expected": expected,
        "trust": trust, "launch": launch if isinstance(launch, dict) else None,
        "environment": env if isinstance(env, dict) else None,
        "n": n, "correct": correct, "n_valid": n_valid, "n_errors": n_err,
        "accuracy": accuracy, "elapsed_s": _num(pick("elapsed_s")),
        "by_subcat": by_subcat if isinstance(by_subcat, dict) else {},
        "notes": pick("notes") if isinstance(pick("notes"), list) else [],
        "dims": dims,
    }


def merge_sets(facts, warnings):
    """Group by preset when the result knows its preset, else by (endpoint, model).

    为什么必须有这两种:几套预设可以共用同一个模型别名(实测 mock-a 与 mock-b
    的 model 完全相同),只按 endpoint/model 归并会把它们合并成一行,于是"每套
    预设各有一份报告"就无法做到。反过来,老结果里没有 preset 字段,只按 preset
    归并会把它们全归到同一个 None 行下面。所以优先 preset、缺失时退回旧口径,
    并在报告里说清哪些行是按旧口径归并的。
    """
    sets = {}
    for f in facts:
        if f.get("preset"):
            key = ("preset", f["preset"])
            label = "%s" % f["preset"]
        else:
            key = (f["endpoint"] or "?", f["model"] or "?")
            label = "%s / %s" % key
        s = sets.setdefault(key, {
            "key": key, "label": label,
            "endpoint": f["endpoint"] or "?",
            "model": f["model"] or "?",
            "preset": f.get("preset"),
            "by_preset": bool(f.get("preset")),
            "dims": {}, "files": [], "levels": [], "n_errors": 0.0,
            "elapsed_s": None, "notes": [],
        })
        if f.get("preset") and s["endpoint"] == "?":
            s["endpoint"] = f["endpoint"] or "?"
        if f.get("preset") and s["model"] == "?":
            s["model"] = f["model"] or "?"
        s["files"].append(f)
        if f["level"] and f["level"] not in s["levels"]:
            s["levels"].append(f["level"])
        if f["n_errors"]:
            s["n_errors"] += f["n_errors"]
        if f["elapsed_s"]:
            s["elapsed_s"] = (s["elapsed_s"] or 0.0) + f["elapsed_s"]
        for note in f["notes"]:
            s["notes"].append(note)
        for dim, b in f["dims"].items():
            prev = s["dims"].get(dim)
            rank = (f["stamp"] or "", f["source"])
            if prev is not None:
                if prev["_rank"] >= rank:
                    warnings.append("重复维度 %s（%s）：保留 %s，忽略 %s"
                                    % (dim, s["label"], prev["_source"], f["source"]))
                    continue
                warnings.append("重复维度 %s（%s）：用 %s 覆盖 %s"
                                % (dim, s["label"], f["source"], prev["_source"]))
            nb = dict(b)
            nb["_rank"] = rank
            nb["_source"] = f["source"]
            nb["_trust"] = f["trust"]
            nb["_jv"] = f["judge_version"]
            nb["_launch"] = f["launch"]
            nb["_environment"] = f["environment"]
            nb["_level"] = f["level"]
            nb["_suite"] = f["suite"]
            s["dims"][dim] = nb
    return sets


# --------------------------------------------------------------------- metrics
def summarize_samples(samples):
    """Raw sample dicts -> peak/mean of the five metrics. Key names are tolerant."""
    fields = {
        "gpu_mem": ("gpu_mem_mib", "mem_used_mib", "gpu_mem_used_mib", "vram_mib"),
        "gpu_util": ("gpu_util_pct", "util_pct", "gpu_utilization_pct"),
        "temp": ("temp_c", "gpu_temp_c", "temperature_c"),
        "power": ("power_w", "gpu_power_w"),
        "sys_mem": ("sys_mem_gib", "memory_gib", "ram_used_gib"),
    }
    out = {}
    for name, alts in fields.items():
        vals = []
        for s in samples:
            if not isinstance(s, dict):
                continue
            for a in alts:
                v = _num(s.get(a))
                if v is not None:
                    vals.append(v)
                    break
        if vals:
            out[name] = {"n": len(vals), "mean": sum(vals) / len(vals),
                         "peak": max(vals), "min": min(vals)}
    return out


def stats_for(stats, s):
    """Find one set's sampling stats: label, endpoint/model, model, endpoint."""
    if not isinstance(stats, dict) or not stats:
        return None
    candidates = [s["label"], "%s/%s" % s["key"], s["model"], s["endpoint"]]
    for cand in candidates:
        if cand and cand in stats:
            v = stats[cand]
            if isinstance(v, list):
                return summarize_samples(v)
            if isinstance(v, dict):
                if isinstance(v.get("samples"), list):
                    merged = summarize_samples(v["samples"])
                    for k, val in v.items():
                        if k != "samples" and k not in merged:
                            merged[k] = val
                    return merged
                return v
    return None


def _stat(blk, name, mode, unit, digits=0):
    """peak or mean of one metric, or 未采集 when it was never sampled."""
    if not blk:
        return NO_SAMPLE
    v = blk.get(name)
    if isinstance(v, dict):
        val = _num(v.get(mode))
    elif v is None:
        val = None
    else:
        val = _num(v)
    if val is None:
        return NO_SAMPLE
    txt = _int(val, digits)
    return MISSING if txt == MISSING else txt + unit


def _env_line(env):
    """Single-point environment snapshot - NOT a peak/mean, and it says so."""
    if not isinstance(env, dict):
        return None
    gpu = env.get("gpu") if isinstance(env.get("gpu"), dict) else {}
    mem = env.get("memory") if isinstance(env.get("memory"), dict) else {}
    bits = []
    if _num(gpu.get("mem_used_mib")) is not None:
        bits.append("显存 %s MiB" % _int(gpu.get("mem_used_mib")))
    if _num(gpu.get("util_pct")) is not None:
        bits.append("利用率 %s%%" % _int(gpu.get("util_pct")))
    if _num(gpu.get("temp_c")) is not None:
        bits.append("温度 %s°C" % _int(gpu.get("temp_c")))
    if _num(gpu.get("power_w")) is not None:
        bits.append("功耗 %sW" % _int(gpu.get("power_w"), 1))
    if _num(mem.get("total_gib")):
        bits.append("系统内存 %s GiB" % _int(mem.get("total_gib"), 1))
    elif mem:
        bits.append("系统内存探测失败(记录 0.0 GiB, 非真实读数)")
    if not bits:
        return None
    return "环境快照(trust=%s, %s, 单点非峰值): %s" % (
        env.get("trust_level") or "?", env.get("captured_at") or "?", " · ".join(bits))


def _launch_lines(launch):
    """Parameter snapshot from launch.processes[], including the raw command line."""
    if not isinstance(launch, dict) or not launch:
        return ["- " + MARK_WARN + " 缺 launch 字段（启动参数未记录，成绩不可复现）"]
    lines = ["- 采集时间: %s · 来源: %s"
             % (launch.get("captured_at") or "?", launch.get("source") or "?")]
    procs = launch.get("processes")
    if not isinstance(procs, list) or not procs:
        lines.append("- " + MARK_WARN + " 无进程记录")
        return lines
    for p in procs:
        if not isinstance(p, dict):
            continue
        if p.get("raw"):
            lines.append("- 命令行: " + BT + str(p["raw"])[:300] + BT)
        params = p.get("params") if isinstance(p.get("params"), dict) else {}
        if params:
            keys = sorted(params.keys())
            rendered = " · ".join("%s=%s" % (k, params[k]) for k in keys[:14])
            if len(keys) > 14:
                rendered += " …共%d项" % len(keys)
            lines.append("- 参数: " + rendered)
        flags = p.get("flags")
        if isinstance(flags, list) and flags:
            lines.append("- 开关: " + " ".join(str(f) for f in flags[:14]))
        derived = p.get("derived") if isinstance(p.get("derived"), dict) else {}
        if derived:
            lines.append("- 推导: " + " · ".join("%s=%s" % (k, derived[k])
                                                for k in sorted(derived)[:6]))
        if p.get("note"):
            lines.append("- 备注: " + str(p["note"]))
        if not p.get("raw") and not params:
            lines.append("- " + MARK_WARN + " 未捕获命令行（云端端点或未预先启动）")
    return lines


def subcat_table(bucket):
    """Subcategory distribution: explicit by_subcat, else derived from records."""
    sub = bucket.get("by_subcat") if isinstance(bucket.get("by_subcat"), dict) else {}
    if not sub:
        derived = {}
        for r in bucket.get("records") or []:
            if not isinstance(r, dict) or r.get("error"):
                continue
            k = r.get("subcat") or "?"
            d = derived.setdefault(k, {"n": 0, "correct": 0})
            d["n"] += 1
            d["correct"] += 1 if r.get("correct") else 0
        sub = derived
    if not sub:
        return []
    lines = ["| 子类 | 题数 | 正确率 |", "|---|---|---|"]
    for k in sorted(sub):
        v = sub[k]
        if isinstance(v, dict):
            cnt = _num(v.get("n"))
            cor = _num(v.get("correct"))
            acc = (cor / cnt) if (cnt and cor is not None) else _num(v.get("accuracy"))
            lines.append("| %s | %s | %s |" % (k, _int(cnt), _pct(acc)))
        else:
            lines.append("| %s | %s | %s |" % (k, _int(_num(v)), MISSING))
    return lines


def failure_note(f):
    """A run that produced no usable answer is a failure, not a low score."""
    if f.get("n_errors") and f["n_errors"] > 0:
        return "API 失败 %d 题（已从正确率分母剔除）" % int(f["n_errors"])
    n = _num(f.get("n"))
    correct = _num(f.get("correct"))
    if n and correct is not None and correct == 0:
        return "全部判错（n=%d, correct=0）" % int(n)
    if not n:
        return "无样本（n 空）"
    return None


# ---------------------------------------------------------------------- report
def _load_results(items):
    """Accept file paths, globs, or already-parsed dicts."""
    files, parsed = [], []
    for it in items or []:
        if isinstance(it, dict):
            parsed.append(it)
            continue
        if not isinstance(it, str):
            continue
        expanded = sorted(glob.glob(it)) if (os.sep in it or "/" in it or "*" in it) else []
        if expanded:
            files.extend(expanded)
        elif os.path.exists(it):
            files.append(it)
        else:
            expanded2 = sorted(glob.glob(os.path.join(ROOT, it)))
            files.extend(expanded2)
    seen = set()
    uniq = []
    for f in files:
        key = os.path.abspath(f)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    out = list(parsed)
    for path in uniq:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if isinstance(data, dict):
            out.append({"__source__": path, "__data__": data})
    return out


def build_report(results, stats, out_path, max_lines=600):
    """Build the Markdown batch report. Returns {ok, path, split_paths, n_sets, warnings}."""
    warnings = []
    facts = []
    n_files = 0
    for it in results or []:
        if isinstance(it, dict) and "__data__" in it:
            src, data = it.get("__source__") or "", it["__data__"]
            n_files += 1
        elif isinstance(it, dict):
            src, data = it.get("source") or it.get("__source__") or "", it
            n_files += 1
        else:
            warnings.append("忽略无法识别的输入: %r" % (type(it).__name__,))
            continue
        f = normalize(data, src)
        if f is None:
            warnings.append("跳过 %s（没有评测记录，多为 run 元数据或环境快照）"
                            % os.path.basename(src or "?"))
            continue
        facts.append(f)

    if not facts:
        return {"ok": False, "path": None, "split_paths": [], "n_sets": 0,
                "warnings": warnings + ["没有任何可用的评测结果"]}

    sets = merge_sets(facts, warnings)
    all_dims = sorted({d for s in sets.values() for d in s["dims"]}, key=_dim_sort_key)

    def trust_mark(t):
        return MARK_OK if t == "ok" else MARK_WARN

    head_lines = []        # title + trust legend + matrix (never dropped)
    perf_lines = []        # performance table + environment snapshots (droppable)
    H = head_lines.append
    A = perf_lines.append
    H("# 模型评测批量报告")
    H("")
    H("- 生成时间: " + _now())
    n_by_preset = sum(1 for s in sets.values() if s.get("by_preset"))
    H("- 数据来源: %d 个结果文件 → %d 套预设" % (n_files, len(sets)))
    H("- 归并口径: 优先结果里的 preset 字段（%d 套）；老结果没有 preset，"
      "退回按 endpoint/model 归并（%d 套）" % (n_by_preset, len(sets) - n_by_preset))
    H("- 维度列: 本次归并 %d 列（维度 + 套件）" % len(all_dims))
    H("- 生成器: runner/batch_report.py · 纯标准库 · 不跑评测、不估算任何数字")
    H("")
    H("**可信度标记**: " + MARK_OK + " = judge_version 与当前一致（当前 core "
      + (CURRENT_CORE or "unknown") + "）；" + MARK_WARN + " = 缺 judge_version（无法追溯）"
      " 或版本已变（需重跑）。标记只说明判分口径，不表示分数高低。")
    H("")
    H("**缺数据格**: " + MISSING + " = 无该维度数据（不是 0，也不是失败）；"
      + NO_SAMPLE + " = 该指标从未采样。")
    H("")
    # 测试产物与能力评价必须分开。实测踩过:报告里混着 mock/fakecloud/paidloop
    # 这些假端点的数字,它们和真模型并排出现在同一张矩阵里,读者无法分辨 ——
    # 而且这些数据本身是"验证判分器能不能跑通"的产物,不代表任何模型能力。
    H("**标 ⚠️ 且模型名含 mock / fake / paidloop 的行是测试夹具。**"
      "它们是假端点，用于验证判分链路能否跑通，**不参与模型能力评价**；"
      "历史遗留的旧题目集结果也按同样口径对待 —— 后续一律用当前题库重跑。"
      "看排名请只看不带这些标记的行。")
    H("")

    # ---- 1. overview matrix
    H("## 总览矩阵")
    H("")
    H("行=预设/模型，列=维度或套件，格=正确率。n 是参与判分的题数（API 失败已剔除）。")
    H("")
    header = "| 预设 / 模型 | " + " | ".join(DIM_LABEL.get(d, d) + " " + d for d in all_dims) + " |"
    H(header)
    H("|" + "---|" * (len(all_dims) + 1))
    for _, s in sorted(sets.items()):
        cells = []
        for d in all_dims:
            b = s["dims"].get(d)
            if b is None:
                cells.append(MISSING)                # never 0, never empty
            elif _num(b.get("accuracy")) is None:
                cells.append(MISSING)                # present but unscored: still not a number
            else:
                # 小样本必须标出来。实测 smoke 每维只有 3-4 题:3 题对 2 题
                # 就是 66.7%,看起来像正式分数,其实是抽样。项目铁律是
                # "样本 <100 只能说方向,不能量化差距",矩阵上不标 n 就等于
                # 邀请别人把 66.7% 当成结论。
                n = _num(b.get("n"))
                small = (n is not None and n < SMALL_N)
                mark = (" ·n=%s" % _int(n)) if small else ""
                cells.append("%s%s %s" % (_pct(b.get("accuracy")), mark,
                                         trust_mark(b.get("_trust"))))
        H("| %s | %s |" % (s["label"], " | ".join(cells)))
    H("")

    fixtures = sorted({s["endpoint"] for s in sets.values()
                       if s["endpoint"] in ("mock", "fakecloud", "paidloop", "freepub")})
    if fixtures:
        H("> " + MARK_WARN + " " + ", ".join(fixtures)
          + " 是测试夹具/假端点，行内数字用于验证判分器与链路，**不代表任何模型能力**。")
        H("")
    H("### 矩阵题数(n)")
    H("")
    H("| 预设 / 模型 | " + " | ".join(d for d in all_dims) + " |")
    H("|" + "---|" * (len(all_dims) + 1))
    for _, s in sorted(sets.items()):
        cells = [_int(s["dims"][d].get("n")) if d in s["dims"] else MISSING for d in all_dims]
        H("| %s | %s |" % (s["label"], " | ".join(cells)))
    H("")

    # ---- 2. performance table
    A("## 性能对比")
    A("")
    A("来自采样统计(stats)：显存/利用率/温度/功耗为**峰值(均值)**，系统内存为峰值。"
      "没有采样数据的写「" + NO_SAMPLE + "」，不回退成别的数字。")
    A("")
    A("| 预设 / 模型 | GPU 显存峰值(均值) MiB | GPU 利用率峰值(均值) % | 温度峰值(均值) °C "
      "| 功耗峰值(均值) W | 系统内存峰值 GiB | 采样点 | 来源 |")
    A("|---|---|---|---|---|---|---|---|")
    sampled = False
    for _, s in sorted(sets.items()):
        blk = stats_for(stats, s)
        if blk:
            sampled = True
            row = [
                "%s (%s)" % (_stat(blk, "gpu_mem", "peak", ""), _stat(blk, "gpu_mem", "mean", "")),
                "%s (%s)" % (_stat(blk, "gpu_util", "peak", ""), _stat(blk, "gpu_util", "mean", "")),
                "%s (%s)" % (_stat(blk, "temp", "peak", ""), _stat(blk, "temp", "mean", "")),
                "%s (%s)" % (_stat(blk, "power", "peak", "W", 1), _stat(blk, "power", "mean", "W", 1)),
                _stat(blk, "sys_mem", "peak", "", 1),
            ]
            cnt = blk.get("n") if _num(blk.get("n")) is not None else _num(
                (blk.get("gpu_mem") or {}).get("n") if isinstance(blk.get("gpu_mem"), dict) else None)
            row.append(_int(cnt))
            row.append("stats")
            A("| %s | %s |" % (s["label"], " | ".join(row)))
        else:
            A("| %s | %s | %s | %s | %s | %s | %s | %s |"
              % (s["label"], NO_SAMPLE, NO_SAMPLE, NO_SAMPLE, NO_SAMPLE, NO_SAMPLE,
                 NO_SAMPLE, "无 stats"))
    A("")
    if not sampled:
        A("> " + MARK_WARN + " 本次未收到任何采样统计(stats)，性能列全部为「"
          + NO_SAMPLE + "」。不估算、不用环境单点代替峰值。")
        A("")

    # ---- 每维度耗时(不计入指数) ----
    # 正确率回答"准不准",耗时回答"用起来顺不顺"。两者是不同的问题,
    # 混进一个分数会让"慢但准"和"快但错"看起来一样好。所以这块只呈现、不打分。
    try:
        import perf as _perf
        _lat = _perf.collect(os.path.join(ROOT, "results"))
    except Exception:
        _lat = {}
    A("### 每维度耗时（不计入指数）")
    A("")
    A("读法:**典型** = 一半的题比它快(中位数);**最慢** = 这组里最慢的一道;"
      "**秒/题** = 平均(总时间 ÷ 题数)。三个数差得越大,说明这维度越不稳定。")
    A("")
    A("**tok/s** 来自 config/preset_speed.json(Llama Rig 图形界面实测记录),"
      "不是从 latency 反推 —— 结果文件没存 token 数,反推会得到"
      "看起来精确、实际不可比的假数字。没测过速的预设写「—」。")
    A("")
    A("| 预设 / 模型 | 维度 | 题数 | 典型 s | 最慢 s | 秒/题 | tok/s |")
    A("|---|---|---|---|---|---|---|")
    if _lat:
        # 按预设聚合:同名预设的维度排在一起。原来按 (预设,维度) 字典序,
        # 同一预设的不同维度散落在表里,想比较"这套预设各维度快慢"要来回跳行。
        _order = sorted(_lat.items(), key=lambda kv: (kv[0][0], kv[1]["latency"]["per_q_s"]))
        for (preset, suite), v in _order:
            L = v["latency"]
            sp = v.get("speed") or {}
            tps = sp.get("p2_tps")
            A("| %s | %s | %d | %.2f | %.2f | %.2f | %s |" % (
                preset, suite, L["n"], L["median_s"], L["max_s"], L["per_q_s"],
                ("%.1f" % tps) if tps else "—"))
    else:
        A("| — | — | — | — | — | — | — |")
    A("")
    A("> 补测速:python runner/run.py --endpoint <端点> --suite speed --preset-id <预设名>"
      " ,然后把 p2_tps 记进 config/preset_speed.json。")
    A("")
    snap_lines = []
    for _, s in sorted(sets.items()):
        for d, b in sorted(s["dims"].items(), key=lambda kv: _dim_sort_key(kv[0])):
            envl = _env_line(b.get("_environment"))
            if envl:
                snap_lines.append("%s · %s: %s" % (s["label"], d, envl))
                break
    if snap_lines:
        A("**环境单点快照**（运行结束时刻采一次，是单点，**不是**峰值/均值，"
          "只用于说明当时的干扰条件；每套预设的完整快照见其明细小节）：")
        A("")
        for l in snap_lines:
            A("- " + l)
        A("")

    # ---- 3. per-set sections (built once, reused by the split files)
    body = []
    for _, s in sorted(sets.items()):
        body.extend(_set_section(s, trust_mark, _trust_text, subcat_table))

    # ---- 4. failure / untrusted list (cap depends on how tight the budget is)
    full = head_lines + perf_lines + body + _failure_lines(facts, 40)
    if warnings:
        full.extend(["## 生成过程中的提示", ""] + ["- " + w for w in warnings[:20]])
        if len(warnings) > 20:
            full.append("- …其余 %d 条" % (len(warnings) - 20))
        full.append("")

    out_path = out_path or os.path.join(ROOT, "results", "batch_report.md")
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    split_paths = []
    if len(full) > max_lines:
        # Detail moves to per-preset files; the main report must then fit the
        # budget, and the matrix is the one block that always survives.
        base_name = os.path.splitext(os.path.basename(out_path))[0]
        for _, s in sorted(sets.items()):
            path = os.path.join(out_dir, "%s_%s.md" % (base_name, _safe_name(s["label"])))
            det = ["# 预设明细: %s" % s["label"], "",
                   "- 由 runner/batch_report.py 拆分生成于 %s" % _now(),
                   "- 主报告: %s" % os.path.basename(out_path), ""]
            det.extend(_set_section(s, trust_mark, _trust_text, subcat_table))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(NL.join(det) + NL)
            split_paths.append(path)

        summary = ["## 每套预设摘要（明细已拆分）", "",
                   "完整内容共 %d 行，超过 max_lines=%d；逐维度/子类明细已拆到独立文件："
                   % (len(full), max_lines), "",
                   "| 预设 / 模型 | 题数 | 正确率 | 耗时 s | 明细文件 |",
                   "|---|---|---|---|---|"]
        for _, s in sorted(sets.items()):
            tot_n = sum((_num(b.get("n")) or 0.0) for b in s["dims"].values())
            tot_c = sum((_num(b.get("correct")) or 0.0) for b in s["dims"].values())
            nm = "%s_%s.md" % (base_name, _safe_name(s["label"]))
            summary.append("| %s | %s | %s | %s | [%s](%s) |"
                           % (s["label"], _int(tot_n), _pct((tot_c / tot_n) if tot_n else None),
                              _int(s["elapsed_s"], 1), nm, nm))
        summary.append("")

        warn_block = []
        if warnings:
            warn_block = ["## 生成过程中的提示", ""]
            warn_block.extend(["- " + w for w in warnings[:3]])
            if len(warnings) > 3:
                warn_block.append("- …其余 %d 条" % (len(warnings) - 3))
            warn_block.append("")

        # Fit by choosing which blocks to include - never by truncating the matrix.
        final = list(head_lines) + summary + _failure_lines(facts, 0)
        if len(final) + 1 <= max_lines and perf_lines:
            cand = list(head_lines) + perf_lines + summary + _failure_lines(facts, 2) + warn_block
            if len(cand) <= max_lines:
                final = cand
        if len(final) < max_lines:
            for cap in (1, 2, 4, 6, 8):
                cand = list(head_lines) + summary + _failure_lines(facts, cap) + warn_block
                if len(cand) <= max_lines:
                    final = cand
        final = final[:max_lines]
    else:
        final = full

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(NL.join(final) + NL)

    return {
        "ok": True, "path": os.path.abspath(out_path), "split_paths": split_paths,
        "n_sets": len(sets),
        "warnings": warnings + ([] if not split_paths else ["总行数 %d 超过 max_lines=%d，已拆分 %d 个明细文件"
                                                            % (len(full), max_lines, len(split_paths))]),
    }


def _failure_lines(facts, cap):
    """失败与不可信清单. cap bounds how many bullets each list prints (0 = counts only)."""
    out = ["## 失败与不可信清单", ""]
    groups = [
        ("缺 judge_version（无法追溯判分口径）",
         [f for f in facts if f["trust"] == "none"],
         lambda f: "%s（%s / %s，n=%s）" % (os.path.basename(f["source"]), f["endpoint"] or "?",
                                          f["model"] or "?", _int(f["n"]))),
        ("judge_version 已变（需重跑）",
         [f for f in facts if f["trust"] == "drift"],
         lambda f: "%s（记录 %s，当前 %s）" % (os.path.basename(f["source"]), f["judge_version"],
                                              f["expected"] or CURRENT_CORE)),
        ("缺 launch（启动参数未记录，不可复现）",
         [f for f in facts if not f["launch"]],
         lambda f: "%s（%s / %s，%s，n=%s）" % (os.path.basename(f["source"]), f["endpoint"] or "?",
                                               f["model"] or "?", f["suite"] or "?", _int(f["n"]))),
        ("缺 environment（性能快照未记录）",
         [f for f in facts if not f["environment"]],
         lambda f: os.path.basename(f["source"])),
    ]
    for title, items, render in groups:
        out.append("### " + MARK_WARN + " " + title + ": %d 个文件" % len(items))
        out.append("")
        for f in items[:cap]:
            out.append("- " + render(f))
        if len(items) > cap:
            out.append("- …其余 %d 个同类（明细见各预设文件）" % (len(items) - cap))
        out.append("")
    failed = [(f, failure_note(f)) for f in facts if failure_note(f)]
    out.append("### 失败运行: %d 个文件" % len(failed))
    out.append("")
    if failed:
        for f, why in failed[:cap]:
            out.append("- %s：%s" % (os.path.basename(f["source"]), why))
        if len(failed) > cap:
            out.append("- …其余 %d 个同类" % (len(failed) - cap))
    else:
        out.append("- 无（所有文件的题都返回了可判分回答）")
    out.append("")
    return out


def _trust_text(t):
    if t == "ok":
        return "一致"
    if t == "none":
        return "无法追溯"
    if t == "drift":
        return "版本已变"
    return "未知版本"


def _set_section(s, trust_mark, trust_text, subcat_table):
    """One preset's detail block, shared by the full report and the split files."""
    out = ["## 预设: %s" % s["label"], ""]
    tot_n = sum((_num(b.get("n")) or 0.0) for b in s["dims"].values())
    tot_c = sum((_num(b.get("correct")) or 0.0) for b in s["dims"].values())
    out.append("- 结果文件: %d 个" % len(s["files"]))
    out.append("- 题数合计: %s（正确 %s）" % (_int(tot_n), _int(tot_c)))
    out.append("- 正确率(合计): %s" % _pct((tot_c / tot_n) if tot_n else None))
    out.append("- 耗时合计: %s s" % _int(s["elapsed_s"], 1))
    out.append("- 每题耗时: %s s/题"
               % (_int(s["elapsed_s"] / tot_n, 2) if (s["elapsed_s"] and tot_n) else MISSING))
    if s["levels"]:
        out.append("- 覆盖档位: " + ", ".join(str(x) for x in s["levels"]))
    out.append("")
    out.append("### 参数快照")
    out.append("")
    first = None
    first_env = None
    for d, b in sorted(s["dims"].items(), key=lambda kv: _dim_sort_key(kv[0])):
        if first is None and b.get("_launch") is not None:
            first = b["_launch"]
        if first_env is None and b.get("_environment") is not None:
            first_env = b["_environment"]
        if first is not None and first_env is not None:
            break
    out.extend(_launch_lines(first))
    out.append("")
    envl = _env_line(first_env)
    if envl:
        out.append("- " + envl)
        out.append("")
    out.append("### 按维度")
    out.append("")
    out.append("| 维度 | 套件 | 题数 | 正确 | 正确率 | 耗时 s | s/题 | 可信度 | 来源文件 |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for d, b in sorted(s["dims"].items(), key=lambda kv: _dim_sort_key(kv[0])):
        n = _num(b.get("n"))
        el = _num(b.get("elapsed_s"))
        out.append("| %s %s | %s | %s | %s | %s | %s | %s | %s %s | %s |"
                   % (d, DIM_LABEL.get(d, ""), b.get("_suite") or MISSING, _int(n),
                      _int(b.get("correct")) if b.get("correct") is not None else MISSING,
                      _pct(b.get("accuracy")) + ("" if _num(b.get("accuracy")) is not None
                                                 else " (未判分)"), _int(el, 1),
                      _int(el / n, 2) if (el and n) else MISSING,
                      trust_mark(b.get("_trust")), trust_text(b.get("_trust")),
                      os.path.basename(b.get("_source") or "?")))
    out.append("")
    for d, b in sorted(s["dims"].items(), key=lambda kv: _dim_sort_key(kv[0])):
        st = subcat_table(b)
        if st:
            out.append("**%s 子类分布**" % d)
            out.append("")
            out.extend(st)
            out.append("")
            break
    for note in s["notes"][:4]:
        out.append("- 备注: " + str(note))
    out.append("")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="读一批评测结果 + 采样统计，生成 Markdown 批量报告（纯标准库）")
    ap.add_argument("--results", nargs="+", default=[os.path.join(ROOT, "results", "*.json")],
                    help="结果文件 glob 或文件列表（也接受逗号分隔）")
    ap.add_argument("--stats", default="",
                    help="采样统计 JSON 文件（可选）")
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "batch_report.md"))
    ap.add_argument("--max-lines", type=int, default=600)
    args = ap.parse_args(argv)

    items = []
    for r in args.results:
        items.extend(x for x in r.split(",") if x.strip())
    stats = {}
    if args.stats:
        try:
            with open(args.stats, encoding="utf-8") as fh:
                stats = json.load(fh)
        except Exception as exc:
            print("[warn] 采样统计读取失败: %s" % exc)

    res = build_report(_load_results(items), stats, args.out, max_lines=args.max_lines)
    if not res["ok"]:
        print("[fail] " + "; ".join(res["warnings"][:3]))
        return 2
    print("报告: %s" % res["path"])
    print("预设: %d 套" % res["n_sets"])
    if res["split_paths"]:
        print("明细拆分: %d 个" % len(res["split_paths"]))
        for p in res["split_paths"]:
            print("  " + p)
    for w in res["warnings"][:5]:
        print("[warn] " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
