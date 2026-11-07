# -*- coding: utf-8 -*-
"""engines.py - describe each local inference engine and what it actually supports.

Every engine speaks an OpenAI-compatible /v1/chat/completions, but they differ in
everything else, and the probes currently assume llama.cpp:

    llama.cpp     /health /props /metrics /slots   -- rich, and the only one with
                                                      KV/MTP counters
    LM Studio     /v1/models, /api/v0/models       -- has its own REST API, no
                                                      prometheus metrics
    Ollama        /api/tags /api/ps /api/show      -- native API; the OpenAI shim
                                                      exposes /v1/models only
    NInfer        ?                                -- a custom engine; capabilities
                                                      must be detected, not assumed

Assuming llama.cpp endpoints against Ollama does not fail loudly - it returns 404
and the probe silently reports "no server metrics", which looks like a working run
with missing data. So capability detection is explicit: each engine declares what it
has, and the probe degrades with a stated reason.

Usage:
  python runner/engines.py --detect                    # probe whatever is running
  python runner/engines.py --detect --port 11434
  python runner/engines.py --list                      # the known engine table
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)

# Each engine: how to identify it, and which optional endpoints exist.
# "caps" lists the EXTRA capabilities beyond /v1/chat/completions.
ENGINES = {
    "llama.cpp": {
        "probe": ["/props", "/health"],
        "caps": ["props", "metrics", "slots", "health"],
        "endpoints": {"props": "/props", "metrics": "/metrics", "slots": "/slots",
                      "health": "/health", "models": "/v1/models"},
        "notes": "唯一提供 KV/MTP 计数的引擎;速度探测的双视角依赖它",
        "launch_style": "argv",
    },
    "lmstudio": {
        "probe": ["/api/v0/models", "/v1/models"],
        "caps": ["models", "native_rest"],
        "endpoints": {"models": "/v1/models", "native_models": "/api/v0/models"},
        "notes": "有独立 REST API;无 prometheus 指标,速度只能靠客户端计时",
        "launch_style": "external",
    },
    "ollama": {
        "probe": ["/api/tags"],
        "caps": ["models", "native_rest", "ps"],
        "endpoints": {"models": "/v1/models", "tags": "/api/tags", "ps": "/api/ps",
                      "show": "/api/show"},
        "notes": "原生 API 提供模型列表与运行状态;OpenAI 兼容层只暴露 /v1/*",
        "launch_style": "external",
    },
    "ninfer": {
        "probe": ["/health", "/v1/models"],
        "caps": ["models"],
        "endpoints": {"models": "/v1/models", "health": "/health"},
        "notes": "独立引擎:.ninfer artifact 格式,参数名与 llama.cpp 不同"
                 "(--max-context / --spec mtp),能力需实测",
        "launch_style": "argv",
    },
    "kvmem": {
        "probe": ["/props", "/health"],
        "caps": ["props", "metrics", "slots", "health", "models"],
        "endpoints": {"props": "/props", "metrics": "/metrics", "slots": "/slots",
                      "health": "/health", "models": "/v1/models"},
        "notes": "llama.cpp 分支,专注 KV cache 驻留;额外提供 --kv-dtype /"
                 " --kvmem-method / --kvmem-budget / --kvmem-cpu-gb 等 KV 内存控制",
        "launch_style": "argv",
    },
    "ninfer+kvmem": {
        "probe": ["/health", "/v1/models"],
        "caps": ["models"],
        "endpoints": {"models": "/v1/models", "health": "/health"},
        "notes": "ninfer 引擎 + kvmem 的 KV 内存控制;两套参数名并存",
        "launch_style": "argv",
    },
    "vllm": {
        "probe": ["/v1/models", "/health"],
        "caps": ["models", "metrics", "health"],
        "endpoints": {"models": "/v1/models", "health": "/health", "metrics": "/metrics"},
        "notes": "有 prometheus 指标,但字段名与 llama.cpp 不同",
        "launch_style": "argv",
    },
    "openai-compatible": {
        "probe": ["/v1/models"],
        "caps": ["models"],
        "endpoints": {"models": "/v1/models"},
        "notes": "兜底:任何只实现 /v1/chat/completions 与 /v1/models 的服务",
        "launch_style": "external",
    },
}

# Extra capability names -> why the probe needs them
CAP_WHY = {
    "props": "读取模型别名、n_ctx、多模态能力",
    "metrics": "服务端权威速度(prompt/predicted tokens/s)、缓存命中、MTP 接受率",
    "slots": "上下文占用(n_past/n_ctx)",
    "health": "就绪检测(比轮询 /v1/models 更轻)",
    "models": "模型列表",
    "native_rest": "引擎原生 API(模型详情、运行状态)",
    "ps": "当前已加载模型",
}


# /props for a real model is far larger than it looks: the generation params block
# alone runs to several KB before model_path appears. Reading a fixed 4 KB truncated
# the JSON, parsing failed, and llama.cpp was misidentified as a generic
# openai-compatible server - which then reported "no metrics" on a server that has
# them. Read a generous cap instead, and treat the cap as a safety net, not a size.
_MAX_BODY = 4 * 1024 * 1024


def _get(base, path, timeout=4):
    try:
        r = urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout)
        return r.status, r.read(_MAX_BODY)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return None, b""


def detect(base_url, verbose=False):
    """Identify the engine behind a base URL and report which capabilities work.

    Detection is by probing, not by guessing from the port: users put engines on
    whatever port they like, and a wrong guess silently produces a probe that
    reports no metrics.
    """
    result = {"base_url": base_url, "engine": None, "caps": {}, "raw": {}}

    # llama.cpp answers /props with a JSON object containing model_path.
    # Detection accepts either signature key, and records why it failed when it does,
    # so a misidentification can be diagnosed instead of silently degrading.
    code, body = _get(base_url, "/props")
    if code == 200 and body:
        try:
            d = json.loads(body.decode("utf-8", "ignore"))
            if "model_path" in d or "default_generation_settings" in d:
                result["engine"] = "llama.cpp"
                result["raw"]["props"] = {k: d.get(k) for k in
                                          ("model_alias", "model_path", "model_ftype",
                                           "build_info", "modalities")}
            else:
                result["raw"]["props_keys"] = sorted(d)[:8]
        except Exception as e:
            result["raw"]["props_error"] = "%s (body %d 字节)" % (type(e).__name__, len(body))

    # Ollama's native API
    if not result["engine"]:
        code, body = _get(base_url, "/api/tags")
        if code == 200 and body and b"models" in body:
            result["engine"] = "ollama"

    # LM Studio
    if not result["engine"]:
        code, body = _get(base_url, "/api/v0/models")
        if code == 200 and body and b"data" in body:
            result["engine"] = "lmstudio"

    # vLLM exposes /version with a distinctive payload
    if not result["engine"]:
        code, body = _get(base_url, "/version")
        if code == 200 and body and b"version" in body:
            result["engine"] = "vllm"

    # NInfer: a custom engine. Detect by the health/models combination plus the
    # absence of every known signature, and say so rather than pretending.
    if not result["engine"]:
        c1, _ = _get(base_url, "/health")
        c2, b2 = _get(base_url, "/v1/models")
        if c2 == 200 and b2:
            result["engine"] = "openai-compatible"
            if c1 == 200:
                result["raw"]["health"] = True

    spec = ENGINES.get(result["engine"] or "", ENGINES["openai-compatible"])
    for cap, path in spec["endpoints"].items():
        code, _ = _get(base_url, path)
        result["caps"][cap] = (code == 200)
    result["notes"] = spec["notes"]
    result["launch_style"] = spec["launch_style"]
    return result


def caps_for(endpoint_conf, base_url=None):
    """Capabilities for a configured endpoint, used by the probes to decide whether
    to attempt a server-side read at all."""
    eng = (endpoint_conf or {}).get("engine")
    if eng and eng in ENGINES:
        spec = ENGINES[eng]
        return {c: True for c in spec["caps"]}, eng
    if base_url:
        d = detect(base_url)
        return d["caps"], d["engine"]
    return {}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--base", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list or (not args.detect and not args.base):
        print("已知引擎及其能力")
        print("=" * 72)
        for name, spec in ENGINES.items():
            print()
            print("  %s" % name)
            print("      %s" % spec["notes"])
            print("      启动方式: %s" % ("本项目可代管(argv)" if spec["launch_style"] == "argv"
                                          else "外部进程,本项目只连接"))
            print("      能力: %s" % ", ".join(spec["caps"]))
        print()
        print("  说明:能力通过探测确定,不靠端口猜测 —— 猜错会静默产生「无服务端数据」的探测结果,")
        print("        看起来像一次成功的运行,实际缺了一半指标。")
        return

    base = args.base or ("http://127.0.0.1:%d" % (args.port or 8080))
    d = detect(base)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return

    print("=" * 66)
    print("  引擎探测")
    print("=" * 66)
    print()
    print("  地址: %s" % base)
    print("  引擎: %s" % (d["engine"] or "未识别"))
    if d.get("raw", {}).get("props"):
        p = d["raw"]["props"]
        print("  模型: %s" % (p.get("model_alias") or p.get("model_path")))
        print("  量化: %s | 构建: %s" % (p.get("model_ftype"), p.get("build_info")))
        m = p.get("modalities") or {}
        if m:
            print("  多模态: %s" % ", ".join(k for k, v in m.items() if v) or "无")
    print()
    print("  能力探测:")
    for cap, ok in sorted(d["caps"].items()):
        why = CAP_WHY.get(cap, "")
        print("    %-14s %s  %s" % (cap, "✓" if ok else "✗", why))
    print()
    print("  %s" % d["notes"])
    missing = [c for c, ok in d["caps"].items() if not ok and c in ("metrics", "props")]
    if missing:
        print()
        print("  ⚠ 缺少 %s —— 速度探测将退化为「仅客户端计时」," % ", ".join(missing))
        print("    服务端权威值(缓存命中、MTP 接受率、prefill/decode 分解)不可得。")


if __name__ == "__main__":
    main()
