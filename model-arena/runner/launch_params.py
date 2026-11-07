# -*- coding: utf-8 -*-
"""launch_params.py - capture the FULL launch command line of a local model server
and parse it into structured parameters.

Why the raw command line matters: "the config the model actually ran with" is the
whole point of comparable numbers. A score without --ctx-size / --n-gpu-layers /
KV quant is not reproducible, and a laptop run at ngl=99 is not the same
measurement as one at ngl=20 even for the same weights.

The parser needs a parameter TYPE TABLE, because llama.cpp mixes two kinds:
    --ctx-size 32768      takes a value
    --cpu-moe             is a FLAG with no value
Without the table, a naive "take the next token" parser reads "--cpu-moe" and
consumes "--threads" as its value - which is exactly the bug seen while testing.

Used by runner/run.py; the structured result goes into every result file under
"launch", so a leaderboard row can always be traced back to a real command line.
"""
import json
import os
import re
import subprocess
import time

NL = chr(10)

# ---------------------------------------------------------------- type table
# Flags that take NO value. Everything else that starts with "-" is assumed to
# take one, unless the next token also looks like a flag.
FLAGS = {
    "--cpu-moe", "--no-cpu-moe", "--flash-attn", "--no-flash-attn", "--mlock",
    "--no-mlock", "--mmap", "--no-mmap", "--cont-batching", "--no-cont-batching",
    "--metrics", "--no-metrics", "--verbose", "--log-disable", "--embedding",
    "--reranking", "--no-warmup", "--warmup", "--jinja", "--no-jinja",
    "--reasoning", "--no-reasoning", "--slots", "--no-slots", "--webui",
    "--no-webui", "--swa", "--no-swa", "--numa", "--no-numa", "--alias-of",
}

# Flags whose value is OPTIONAL (may be a bare switch). e.g. --flash-attn on
OPTIONAL_VALUE = {"--flash-attn", "--reasoning", "--numa", "--swa", "--verbose"}

# Parameters worth surfacing separately for comparison.
HIGHLIGHT = {
    "--model": "模型文件",
    "-m": "模型文件",
    "--ctx-size": "上下文长度",
    "-c": "上下文长度",
    "--n-gpu-layers": "GPU 卸载层数",
    "-ngl": "GPU 卸载层数",
    "--cpu-moe": "MoE 专家层放 CPU",
    "--threads": "线程数",
    "-t": "线程数",
    "--threads-batch": "批处理线程",
    "--batch-size": "批大小",
    "-b": "批大小",
    "--ubatch-size": "微批大小",
    "-ub": "微批大小",
    "-ctk": "KV cache K 量化",
    "--cache-type-k": "KV cache K 量化",
    "-ctv": "KV cache V 量化",
    "--cache-type-v": "KV cache V 量化",
    "--temp": "温度",
    "--top-p": "top-p",
    "--top-k": "top-k",
    "--min-p": "min-p",
    "--alias": "模型别名",
    "-a": "模型别名",
    "--host": "监听地址",
    "--port": "端口",
    "--split-mode": "切分模式",
    "--n-predict": "最大生成",
    "-n": "最大生成",
    "--rope-scaling": "RoPE 缩放",
    "--rope-freq-base": "RoPE 基频",
    "--tensor-split": "张量切分",
    "--seed": "随机种子",
}


def _looks_like_flag(tok):
    return bool(tok) and tok.startswith("-") and not re.fullmatch(r"-?\d+(\.\d+)?", tok)


def parse_command_line(cmd):
    """Split a command line into (argv, params dict, flags set).

    Quoted values are preserved as single tokens, so a model path containing
    spaces does not split into pieces.
    """
    # tokenize honouring double quotes (Windows paths with spaces are common)
    tokens = re.findall(r'"([^"]*)"|(\S+)', cmd or "")
    argv = [a or b for a, b in tokens]
    params, flags, positionals = {}, [], []
    i = 1 if argv else 0
    while i < len(argv):
        tok = argv[i]
        if _looks_like_flag(tok):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if tok in FLAGS and tok not in OPTIONAL_VALUE:
                flags.append(tok)
                i += 1
                continue
            if nxt is not None and not _looks_like_flag(nxt):
                params[tok] = nxt
                i += 2
                continue
            # bare switch, or an optional-value flag with no value
            flags.append(tok)
            i += 1
            continue
        positionals.append(tok)
        i += 1
    return argv, params, flags


def _derive(params, flags):
    """Human-facing derived values so two runs can be compared at a glance."""
    d = {}
    ctx = params.get("--ctx-size") or params.get("-c")
    if ctx:
        try:
            n = int(ctx)
            d["context"] = ("%dk" % (n // 1024)) if n >= 1024 else str(n)
            d["context_tokens"] = n
        except ValueError:
            d["context"] = ctx
    ngl = params.get("--n-gpu-layers") or params.get("-ngl")
    if ngl:
        d["gpu_offload"] = ("全部层" if str(ngl).isdigit() and int(ngl) >= 99
                            else "%s 层" % ngl)
    if "--cpu-moe" in flags:
        d["moe"] = "专家层在 CPU(--cpu-moe)"
    ctk = params.get("-ctk") or params.get("--cache-type-k")
    ctv = params.get("-ctv") or params.get("--cache-type-v")
    if ctk or ctv:
        d["kv_cache"] = "%s / %s" % (ctk or "f16", ctv or "f16")
    th = params.get("--threads") or params.get("-t")
    if th:
        d["threads"] = th
    temp = params.get("--temp")
    if temp:
        d["temperature"] = temp
    if "--reasoning" in params:
        d["reasoning"] = params["--reasoning"]
    elif "--reasoning" in flags:
        d["reasoning"] = "on"
    model = params.get("--model") or params.get("-m")
    if model:
        d["model_file"] = os.path.basename(model)
    return d


def find_server_process(names=None):
    """Locate a running local model server and return its full command line.

    names: executable names to look for (default: common llama.cpp / ollama ones).
    Returns a list of dicts; empty when nothing is running.
    """
    names = names or ["llama-server.exe", "llama-server", "ollama.exe",
                      "koboldcpp.exe", "lmstudio.exe", "vllm"]
    found = []
    for exe in names:
        ps = ('Get-CimInstance Win32_Process -Filter "Name=\'%s\'" | '
              'Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Depth 3'
              % exe)
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, errors="ignore", timeout=25)
        except Exception:
            continue
        out = (r.stdout or "").strip()
        if not out:
            continue
        try:
            data = json.loads(out)
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        for p in data:
            cl = p.get("CommandLine") or ""
            if not cl:
                continue
            found.append({"exe": exe, "pid": p.get("ProcessId"),
                          "started": p.get("CreationDate"),
                          "command_line": cl})
    return found


def _fmt_started(v):
    """CIM returns /Date(1791817146466)/ - a .NET JSON date. Render it as local time
    so the UI shows when the server was launched, not a raw epoch blob."""
    if not v:
        return None
    m = re.search(r"/Date\((\d+)\)/", str(v))
    if not m:
        return str(v)[:19]
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(int(m.group(1)) / 1000.0))
    except Exception:
        return str(v)[:19]


def capture(host=None, port=None, names=None):
    """Full launch snapshot: raw command line + parsed params + derived values.

    Falls back to an explicit (host, port) record when no process is found, so a
    cloud/remote endpoint still gets a provenance block instead of nothing.
    """
    procs = find_server_process(names)
    if host or port:
        procs = [p for p in procs
                 if not port or ("--port %s" % port) in (p.get("command_line") or "")] or procs

    snap = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "process" if procs else "config",
            "processes": []}
    for p in procs:
        argv, params, flags = parse_command_line(p["command_line"])
        snap["processes"].append({
            "exe": p["exe"], "pid": p["pid"], "started": _fmt_started(p.get("started")),
            "raw": p["command_line"],
            "params": params, "flags": flags,
            "derived": _derive(params, flags),
            "highlight": {k: v for k, v in params.items() if k in HIGHLIGHT},
            "highlight_flags": [f for f in flags if f in HIGHLIGHT],
        })
    if not procs and (host or port):
        snap["processes"].append({"exe": None, "pid": None, "raw": None,
                                  "params": {}, "flags": [],
                                  "derived": {"endpoint": "%s:%s" % (host or "?", port or "?")},
                                  "note": "未发现本地服务进程(云端端点或未按预期命名)"})
    return snap


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False, indent=2))
