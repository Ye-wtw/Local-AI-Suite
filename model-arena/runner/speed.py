# -*- coding: utf-8 -*-
"""speed.py - D1 speed scan following Artificial Analysis formulas.
output_speed = (total_tokens - first_chunk_tokens) / (t_last_chunk - t_first_chunk)
ttft         = t_first_token - t_request_sent
e2e_500      = ttft + 500 / output_speed
Reports P50/P95 across repeats.
"""
import json, os, statistics, time

FILLER_EN = ("The quarterly operations review continues across all business units. "
             "Metrics remain within the expected variance band for this period. ")
FILLER_CN = ("本季度的运营回顾覆盖了全部业务单元,各项指标均处于本期的预期波动区间之内。")

def build_prompt(input_tokens, lang="en"):
    """Approximate a prompt of ~input_tokens tokens."""
    filler = FILLER_EN if lang == "en" else FILLER_CN
    per_tok = 4.0 if lang == "en" else 1.5   # chars per token (rough)
    target_chars = int(input_tokens * per_tok)
    reps = max(1, target_chars // len(filler))
    body = filler * reps
    return body + "\n\nBased on the material above, summarize the main operational themes in detail."

def percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac

def run_workload(ep, workload, repeats=3, max_tokens=None, lang="en", verbose=True):
    """Run one speed workload. Returns metrics dict."""
    prompt = build_prompt(workload.get("input_tokens", 1000), lang=lang)
    name = workload.get("name", "unnamed")
    recs = []
    errors = []
    for i in range(repeats):
        try:
            res = ep.chat_stream_text(
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens or workload.get("min_output_tokens", 500),
                temperature=workload.get("temperature", 0),
            )
            m = res.get("metrics")
            if m:
                recs.append(m)
                if verbose:
                    print("    [%s] run %d: ttft=%.2fs out=%.1f tok/s e2e500=%.2fs"
                          % (name, i + 1, m["ttft_s"], m["output_tps"], m["e2e_500_s"]))
            else:
                errors.append("run %d: no metrics" % (i + 1))
        except Exception as e:
            errors.append("run %d: %s" % (i + 1, e))
            if verbose:
                print("    [%s] run %d FAILED: %s" % (name, i + 1, e))
    if not recs:
        return {"workload": name, "error": "; ".join(errors) or "all runs failed", "runs": 0}
    ttfts = [r["ttft_s"] for r in recs]
    tpss = [r["output_tps"] for r in recs]
    e2es = [r["e2e_500_s"] for r in recs]
    return {
        "workload": name,
        "input_tokens_target": workload.get("input_tokens"),
        "runs": len(recs),
        "errors": errors,
        "ttft_s": {"mean": statistics.fmean(ttfts), "p50": percentile(ttfts, 50), "p95": percentile(ttfts, 95)},
        "output_tps": {"mean": statistics.fmean(tpss), "p50": percentile(tpss, 50), "p95": percentile(tpss, 95)},
        # 单位与是否精确必须一起往上传:服务没回报 completion_tokens 时,
        # 上面的 output_tps 实际是**字符/秒**,冒充 tok/s 会虚高好几倍
        # (实测 KV17 报出 439 tok/s,而它不可能这么高)。
        "output_unit": recs[0].get("output_unit", "tok/s") if recs else "tok/s",
        "output_exact": all(r.get("output_exact", True) for r in recs),
        "e2e_500_s": {"mean": statistics.fmean(e2es), "p50": percentile(e2es, 50), "p95": percentile(e2es, 95)},
        "output_tokens_mean": statistics.fmean([r.get("total_tokens", 0) for r in recs]),
        "raw_runs": recs,
    }

def run_speed_suite(ep, config_path, repeats=3, only=None, verbose=True):
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    workloads = cfg.get("workloads", [])
    if only:
        workloads = [w for w in workloads if w.get("name") in only]
    print("  endpoint=%s model=%s" % (ep.name, ep.model))
    results = []
    for w in workloads:
        if w.get("name") == "vision":
            if verbose:
                print("  [skip] vision workload needs an image payload (not yet wired)")
            continue
        if verbose:
            print("  workload=%s (target input ~%s tokens)" % (w.get("name"), w.get("input_tokens")))
        results.append(run_workload(ep, w, repeats=repeats, verbose=verbose))
    return {"endpoint": ep.name, "model": ep.model, "repeats": repeats, "results": results}
