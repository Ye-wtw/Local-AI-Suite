# -*- coding: utf-8 -*-
"""speed_probe.py - stage 1a of the parameter funnel: throughput only, ~30 s per combo.

Why this is a separate probe from the question suites:
  * a combo's throughput costs 30 seconds to measure; its quality costs 6-112
    minutes. Paying the latter to learn a thread count is waste.
  * timing is far more fragile than accuracy. Judge work, disk writes and a browser
    in the background all distort tok/s but barely move an accuracy number, so the
    speed probe checks the environment FIRST and records what it saw.

Two independent sources of truth are recorded, deliberately:
  * client-side timing (TTFT from the stream, output tok/s from chunk timestamps)
  * server-side counters from /metrics  (prompt_tokens_seconds, predicted_tokens_seconds,
    spec_decode acceptance, prompt cache hit rate)

They should agree; when they disagree the run is marked. The client view includes
network and HTTP overhead, the server view does not, so a gap is informative rather
than an error.

Usage:
  python runner/speed_probe.py --endpoint local --tag baseline
  python runner/speed_probe.py --endpoint local --tag kvq4 --json results/sweep/1a/kvq4.json
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from endpoint import load_endpoints, EndpointError   # noqa: E402
import environment as envmod                          # noqa: E402
import launch_params as launchmod                     # noqa: E402

NL = chr(10)

# One fixed workload. The probe measures the CONFIG, not the task, so the prompt is
# deliberately boring and identical across combos.
PROBE = {
    "prompt_tokens": 1200,
    "output_tokens": 200,
    "repeats": 3,
}

FILLER = ("季度运营回顾覆盖全部业务单元，指标处于预期波动区间。"
          "The quarterly operations review continues across all business units. ")


def build_probe_prompt(target_tokens):
    """A prompt of roughly target_tokens. ~1.86 chars/token measured for this tokenizer."""
    chars = int(target_tokens * 1.86)
    body = (FILLER * (chars // len(FILLER) + 1))[:chars]
    return body + NL + NL + "请用一句话概括上文主题。"


def server_metrics(base_url, caps=None):
    """Read server-side counters when the engine provides them.

    Only llama.cpp (and vLLM, under different names) expose prometheus metrics.
    Probing /metrics on Ollama or LM Studio returns 404, and the old code turned that
    into an empty dict with no explanation - which reads as "a run with no server
    data" rather than "this engine has no such endpoint". The caller can now tell the
    two apart via the returned reason.
    """
    caps = caps or {}
    if caps.get("metrics") is False:
        return {}, "该引擎未提供 /metrics(能力探测结果为否)"
    try:
        raw = urllib.request.urlopen(base_url.rstrip("/") + "/metrics", timeout=8).read().decode()
    except urllib.error.HTTPError as e:
        return {}, "引擎返回 HTTP %s(可能不支持 /metrics)" % e.code
    except Exception as e:
        return {}, "读取失败: %s" % str(e)[:60]
    out = {}
    for line in raw.splitlines():
        if line.startswith("llamacpp:"):
            try:
                k, v = line.rsplit(" ", 1)
                out[k.split(":", 1)[1]] = float(v)
            except ValueError:
                continue
    if not out:
        return {}, "响应中未找到 llamacpp:* 指标(vLLM 等引擎字段名不同)"
    return out, None


def server_slots(base_url):
    """Context fill and processing state, if the server exposes /slots."""
    try:
        raw = urllib.request.urlopen(base_url.rstrip("/") + "/slots", timeout=8).read().decode()
        sl = json.loads(raw)
        if isinstance(sl, list) and sl:
            s = sl[0]
            return {"n_ctx": s.get("n_ctx"), "is_processing": s.get("is_processing")}
    except Exception:
        pass
    return {}


def percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (k - lo)) + s[hi] * (k - lo)


def probe_once(ep, prompt, max_tokens):
    """One timed generation. Returns client-side metrics plus the raw stream result."""
    t0 = time.perf_counter()
    res = ep.chat_stream_text([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    wall = time.perf_counter() - t0
    m = dict(res.get("metrics") or {})
    m["wall_s"] = wall
    m["text_len"] = len(res.get("text") or "")
    return m


def run_probe(ep, probe=None, verbose=True, skip_env_check=False):
    cfg = dict(probe or PROBE)
    prompt = build_probe_prompt(cfg["prompt_tokens"])

    # --- environment first: a contaminated timing is worse than no timing ---
    env_before = envmod.assess() if not skip_env_check else {}
    if env_before and env_before.get("trust_level") != "high" and verbose:
        print("  [env] trust=%s  %s"
              % (env_before.get("trust_level"),
                 "; ".join(env_before.get("warnings") or [])[:100]))

    # capability detection first: a probe must not assume llama.cpp
    try:
        import engines as engmod
        caps, engine_name = engmod.caps_for({}, base)
        if verbose:
            print("  [engine] %s | 能力: %s"
                  % (engine_name or "未识别",
                     ", ".join(c for c, ok in caps.items() if ok) or "仅 OpenAI 兼容"))
    except Exception:
        caps, engine_name = {}, None
    launch = launchmod.capture()
    base = getattr(ep, "base_url", None) or "http://127.0.0.1:8080"
    m_before, metrics_reason = server_metrics(base, caps)

    runs, errors = [], []
    for i in range(cfg["repeats"]):
        try:
            m = probe_once(ep, prompt, cfg["output_tokens"])
            runs.append(m)
            if verbose:
                print("    run %d: ttft=%.2fs  out=%.1f tok/s  (%.1fs wall)"
                      % (i + 1, m.get("ttft_s", 0), m.get("output_tps", 0), m.get("wall_s", 0)))
        except EndpointError as e:
            errors.append("run %d: %s" % (i + 1, str(e)[:120]))
            if verbose:
                print("    run %d FAILED: %s" % (i + 1, str(e)[:90]))

    m_after, metrics_reason2 = server_metrics(base, caps)
    slots = server_slots(base)
    env_after = envmod.assess() if not skip_env_check else {}

    if not runs:
        return {"ok": False, "errors": errors, "probe": cfg}

    # --- client-side view ---
    ttfts = [r["ttft_s"] for r in runs if r.get("ttft_s") is not None]
    tpss = [r["output_tps"] for r in runs if r.get("output_tps") is not None]
    client = {
        "ttft_s": {"p50": percentile(ttfts, 50), "p95": percentile(ttfts, 95),
                   "mean": statistics.fmean(ttfts) if ttfts else None},
        "output_tps": {"p50": percentile(tpss, 50), "p95": percentile(tpss, 95),
                       "mean": statistics.fmean(tpss) if tpss else None},
        "wall_s": {"p50": percentile([r["wall_s"] for r in runs], 50)},
    }

    # --- server-side view: authoritative counters, deltas over this probe ---
    def delta(key):
        if key not in m_after:
            return None
        return m_after.get(key, 0) - m_before.get(key, 0)

    dpt = delta("prompt_tokens_total")
    dps = delta("prompt_seconds_total")
    dot = delta("tokens_predicted_total")
    dos = delta("tokens_predicted_seconds_total")
    dct = delta("prompt_tokens_cached_total")
    ddraft = delta("spec_decode_num_draft_tokens_total")
    dacc = delta("spec_decode_num_accepted_tokens_total")

    server = {
        "prefill_tps": (dpt / dps) if (dpt and dps) else None,
        "decode_tps": (dot / dos) if (dot and dos) else None,
        "prompt_tokens": dpt,
        "output_tokens": dot,
        "prompt_cached": dct,
        # hit rate is cached / (new + cached); computing cached/new (an earlier bug)
        # gives nonsense like 10325%
        "cache_hit_rate": (dct / (dpt + dct)) if (dct is not None and (dpt or 0) + (dct or 0) > 0) else None,
        "mtp_draft": ddraft,
        "mtp_accepted": dacc,
        "mtp_accept_rate": (dacc / ddraft) if (ddraft and dacc is not None) else None,
    }

    # --- do the two views agree? A large gap means overhead or contention ---
    agree = None
    if client["output_tps"]["p50"] and server["decode_tps"]:
        ratio = client["output_tps"]["p50"] / server["decode_tps"]
        agree = {"ratio": ratio,
                 "note": "客户端/服务端比值;显著偏离 1 说明 HTTP 或负载有开销"}
        if ratio < 0.8:
            agree["flag"] = "客户端明显慢于服务端,可能有网络或负载开销"

    return {
        "ok": True,
        "probe": cfg,
        "repeats_ok": len(runs),
        "errors": errors,
        "client": client,
        "server": server,
        "agreement": agree,
        "engine": engine_name,
        "server_metrics_reason": metrics_reason or metrics_reason2,
        "launch": launch,
        "slots": slots,
        "environment": {"before": env_before.get("trust_level"),
                        "after": env_after.get("trust_level"),
                        "warnings": (env_before.get("warnings") or [])[:3],
                        "drift": envmod.diff_before_after(env_before, env_after)
                        if env_before and env_after else {}},
        "raw_runs": runs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="local")
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "endpoints.json"))
    ap.add_argument("--tag", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--repeats", type=int, default=0)
    ap.add_argument("--no-env-check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    eps, _ = load_endpoints(args.config)
    if args.endpoint not in eps:
        print("unknown endpoint %r; available: %s" % (args.endpoint, ", ".join(eps)))
        return
    ep = eps[args.endpoint]
    probe = dict(PROBE)
    if args.repeats:
        probe["repeats"] = args.repeats

    print("=" * 62)
    print("  速度探测 · 阶段 1a")
    print("=" * 62)
    print("  endpoint=%s model=%s" % (ep.name, ep.model))
    print("  探测负载: %d 输入 tok / %d 输出 tok × %d 次"
          % (probe["prompt_tokens"], probe["output_tokens"], probe["repeats"]))
    print()

    res = run_probe(ep, probe, verbose=not args.quiet,
                    skip_env_check=args.no_env_check)

    print()
    if not res.get("ok"):
        print("  探测失败:", "; ".join(res.get("errors") or [])[:160])
    else:
        c, s = res["client"], res["server"]
        print("  客户端视角:")
        print("    TTFT     p50 %.2fs  p95 %.2fs" % (c["ttft_s"]["p50"], c["ttft_s"]["p95"]))
        print("    输出速度 p50 %.1f tok/s" % c["output_tps"]["p50"])
        print("  服务端视角:")
        if s["prefill_tps"]:
            print("    prefill  %.1f tok/s" % s["prefill_tps"])
        if s["decode_tps"]:
            print("    decode   %.1f tok/s" % s["decode_tps"])
        if s["cache_hit_rate"] is not None:
            print("    缓存命中 %.1f%%" % (s["cache_hit_rate"] * 100))
        if s["mtp_accept_rate"] is not None:
            print("    MTP 接受 %.1f%% (%d/%d)"
                  % (s["mtp_accept_rate"] * 100, s["mtp_accepted"], s["mtp_draft"]))
        if res.get("agreement"):
            print("  两视角比值: %.2f" % res["agreement"]["ratio"])
            if res["agreement"].get("flag"):
                print("    ⚠ %s" % res["agreement"]["flag"])
        env = res["environment"]
        print("  环境: trust %s -> %s" % (env["before"], env["after"]))
        if res["errors"]:
            print("  ⚠ %d 次失败(未计入):" % len(res["errors"]))
            for e in res["errors"][:3]:
                print("      %s" % e)

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        res["tag"] = args.tag
        res["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(NL + "  -> %s" % args.json)
    print(NL + "  说明:这是配置探测,不跑题本。质量由阶段 1b 负责。")


if __name__ == "__main__":
    main()
