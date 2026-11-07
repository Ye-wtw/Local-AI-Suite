# -*- coding: utf-8 -*-
"""sweep_report.py - turn sweep results into a decision, not a table of numbers.

The question this answers is "which launch flags should I actually use", so the
output is a recommendation with its cost stated, not a scoreboard:

    KV  q8_0 -> q4_0   saves 1.5 GB VRAM, costs 2.9 pp accuracy, p=0.31
                      -> keep q8_0 unless you need the VRAM

Three rules keep the report honest:

  1. A difference is only reported as real when the paired test agrees. Two runs
     of the same config differ by a few points all the time; calling that an effect
     is how noise becomes advice.
  2. Every quality claim is paired with its speed claim, because the interesting
     flags trade one for the other. "Same accuracy" is only half an answer when
     the alternative is 40% faster.
  3. When a comparison is underpowered the report says so rather than implying a
     verdict. With 20 questions, a 5 pp gap is inside the noise.

Usage:
  python runner/sweep_report.py --results results/sweep --out reports/sweep.md
  python runner/sweep_report.py --baseline results/sweep/1a/baseline.json
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
# Below this gap the coarse screen simply cannot tell the configs apart.
COARSE_NOISE_PP = 5.0


def load_jsonl_records(path):
    d = json.load(open(path, encoding="utf-8"))
    s = d.get("summary") or d.get("level") or {}
    recs = {r["id"]: bool(r.get("correct")) for r in (d.get("records") or [])
            if not r.get("error")}
    return s, recs


def collect(root):
    """Gather every sweep artefact: speed probes and quality runs."""
    speed, quality = {}, {}
    for p in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        name = os.path.basename(p)
        if name.startswith("_") :
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("ok") is not None and "client" in d:
            label = (d.get("tag") or os.path.splitext(name)[0])
            speed[label] = {"path": p, "data": d}
        elif "records" in d:
            label = (d.get("summary") or {}).get("tag") or os.path.splitext(name)[0]
            s, recs = load_jsonl_records(p)
            quality[label] = {"path": p, "summary": s, "records": recs}
    return speed, quality


def paired_test(recs_a, recs_b):
    """McNemar on the shared question ids. Local import keeps stats optional."""
    common = sorted(set(recs_a) & set(recs_b))
    if not common:
        return None
    pairs = [(recs_a[q], recs_b[q]) for q in common]
    try:
        from stats import mcnemar, wilson_ci
        mc = mcnemar(pairs)
        ka = sum(1 for a, _ in pairs if a)
        kb = sum(1 for _, b in pairs if b)
        return {"n": len(pairs), "acc_a": ka / len(pairs), "acc_b": kb / len(pairs),
                "delta_pp": (kb - ka) / len(pairs) * 100, "mcnemar": mc}
    except Exception:
        ka = sum(1 for a, _ in pairs if a)
        kb = sum(1 for _, b in pairs if b)
        return {"n": len(pairs), "acc_a": ka / len(pairs), "acc_b": kb / len(pairs),
                "delta_pp": (kb - ka) / len(pairs) * 100, "mcnemar": None}


def verdict(delta_pp, mc, n, threshold=COARSE_NOISE_PP):
    """Classify a difference.

    The paired test decides, not the question count. An earlier version rejected
    everything under 30 questions, which mislabelled a 30 pp gap with p=0.031 as
    "insufficient evidence" - the test already knew it was real. Question count
    only weakens the CLAIM when the test itself cannot separate the two.
    """
    if not mc:
        return ("证据不足", "无法做配对检验(题集可能不同)")
    p = mc.get("p", 1.0)
    discordant = mc.get("n01", 0) + mc.get("n10", 0)
    if p < 0.05:
        if abs(delta_pp) < threshold:
            return ("影响轻微",
                    "%.1fpp 且统计显著(p=%.3f),但低于 %gpp 的实用阈值"
                    % (abs(delta_pp), p, threshold))
        return (("明确提升" if delta_pp > 0 else "明确损失"),
                "%.1fpp(配对 %d 题,%d 道分歧,p=%.3f)"
                % (abs(delta_pp), n, discordant, p))
    # not significant: say whether that is because the effect is absent or because
    # the sample is too small to see it
    if n < 30:
        return ("证据不足",
                "只有 %d 题配对(%d 道分歧,p=%.3f);%.1fpp 的差距测不出 —— "
                "需精测(137 题)才能判定"
                % (n, discordant, p, abs(delta_pp)))
    return ("无显著差异", "配对 %d 题,p=%.3f,差距不可区分于随机波动" % (n, p))


def speed_delta(a, b):
    """Compare two speed probes on the authoritative server view, falling back to
    the client view when /metrics was unavailable."""
    def get(d):
        s = (d.get("server") or {})
        c = (d.get("client") or {})
        dec = s.get("decode_tps") or (c.get("output_tps") or {}).get("p50")
        pre = s.get("prefill_tps")
        return dec, pre
    da, pa = get(a)
    db, pb = get(b)
    out = {}
    if da and db:
        out["decode_a"], out["decode_b"] = da, db
        out["decode_delta_pct"] = (db - da) / da * 100
    if pa and pb:
        out["prefill_a"], out["prefill_b"] = pa, pb
        out["prefill_delta_pct"] = (pb - pa) / pa * 100
    return out


def render(root, baseline_label=None):
    speed, quality = collect(root)
    lines = []
    lines.append("# 参数优化报告")
    lines.append("")
    if not speed and not quality:
        lines.append("尚未找到扫描结果。运行阶段 1a/1b 后再生成报告:")
        lines.append("")
        lines.append("```bash")
        lines.append("python runner/speed_probe.py --endpoint local --tag baseline")
        lines.append("python runner/sweep.py --stage 0")
        lines.append("```")
        return NL.join(lines)

    # ---- speed section ----
    lines.append("## 一、速度对比(阶段 1a)")
    lines.append("")
    if not speed:
        lines.append("_尚无速度探测结果。_")
    else:
        labels = sorted(speed)
        base = baseline_label if baseline_label in speed else labels[0]
        lines.append("基准: **%s**" % base)
        lines.append("")
        lines.append("| 配置 | decode tok/s | prefill tok/s | TTFT s | 相对基准 |")
        lines.append("|---|---:|---:|---:|---:|")
        bd = (speed[base]["data"].get("server") or {}).get("decode_tps") \
            or (((speed[base]["data"].get("client") or {}).get("output_tps") or {}).get("p50"))
        for lb in labels:
            d = speed[lb]["data"]
            srv = d.get("server") or {}
            cli = d.get("client") or {}
            dec = srv.get("decode_tps") or ((cli.get("output_tps") or {}).get("p50"))
            pre = srv.get("prefill_tps")
            ttft = (cli.get("ttft_s") or {}).get("p50")
            rel = ""
            if dec and bd:
                pct = (dec - bd) / bd * 100
                rel = "%+.1f%%" % pct
            lines.append("| %s | %s | %s | %s | %s |"
                         % (lb,
                            "%.1f" % dec if dec else "—",
                            "%.1f" % pre if pre else "—",
                            "%.2f" % ttft if ttft else "—",
                            rel or "基准"))
        # MTP acceptance, when present
        for lb in labels:
            srv = speed[lb]["data"].get("server") or {}
            if srv.get("mtp_accept_rate") is not None:
                lines.append("")
                lines.append("_MTP 投机接受率(%s):%.1f%%,即草稿被采纳的比例。_"
                             % (lb, srv["mtp_accept_rate"] * 100))
                break

    # ---- quality section ----
    lines.append("")
    lines.append("## 二、质量对比(阶段 1b / 2)")
    lines.append("")
    if not quality:
        lines.append("_尚无质量结果。_")
    else:
        labels = sorted(quality)
        base = baseline_label if baseline_label in quality else labels[0]
        base_recs = quality[base]["records"]
        lines.append("基准: **%s**(%d 题)" % (base, len(base_recs)))
        lines.append("")
        lines.append("| 配置 | 准确率 | 与基准差 | 配对判定 | 说明 |")
        lines.append("|---|---:|---:|---|---|")
        for lb in labels:
            q = quality[lb]
            acc = (q["summary"].get("accuracy") or 0) * 100
            if lb == base:
                lines.append("| %s | %.1f%% | 基准 | — | — |" % (lb, acc))
                continue
            r = paired_test(base_recs, q["records"])
            if not r:
                lines.append("| %s | %.1f%% | — | 无法配对 | 题集不同 |" % (lb, acc))
                continue
            v, why = verdict(r["delta_pp"], r["mcnemar"], r["n"])
            lines.append("| %s | %.1f%% | %+.1fpp | %s | %s |"
                         % (lb, acc, r["delta_pp"], v, why))

    # ---- the actual recommendation ----
    lines.append("")
    lines.append("## 三、结论")
    lines.append("")
    if quality:
        labels = sorted(quality)
        base = baseline_label if baseline_label in quality else labels[0]
        effects = []
        for lb in labels:
            if lb == base:
                continue
            r = paired_test(quality[base]["records"], quality[lb]["records"])
            if not r:
                continue
            v, why = verdict(r["delta_pp"], r["mcnemar"], r["n"])
            sp = speed_delta(speed[base]["data"], speed[lb]["data"]) if (base in speed and lb in speed) else {}
            effects.append((lb, v, r["delta_pp"], why, sp))
        # A variant can be worth adopting on SPEED ALONE. Treating "quality verdict is
        # insufficient evidence" as "nothing to report" hid the best case: identical
        # accuracy with 30% more throughput, which needs no quality significance at all.
        real = []
        for e in effects:
            lb, v, dp, why, sp = e
            spd = sp.get("decode_delta_pct")
            strong_speed = (spd is not None and abs(spd) >= 5)
            if v in ("明确提升", "明确损失", "影响轻微"):
                real.append(e)
            elif v == "证据不足" and strong_speed and abs(dp) < COARSE_NOISE_PP:
                real.append((lb, "速度优势", dp, why + ";质量无差异但速度显著", sp))
        if not real:
            lines.append("**没有发现需要改变配置的理由。** 所有变体都在噪声范围内,"
                         "保持当前配置即可。")
            lines.append("")
            lines.append("若想得到更确定的结论,把有疑问的变体送进阶段 2(137 题精测)。")
        else:
            lines.append("有实际影响的变体:")
            lines.append("")
            for lb, v, dp, why, sp in real:
                bits = ["**%s**:%s,%.1fpp" % (lb, v, dp)]
                if sp.get("decode_delta_pct") is not None:
                    bits.append("速度 %+.1f%%" % sp["decode_delta_pct"])
                lines.append("- " + ",".join(bits) + "(%s)" % why)
                spd = sp.get("decode_delta_pct")
                if v == "速度优势":
                    lines.append("  - **质量无差异,速度快 %+.1f%%** -> **建议采纳**" % spd)
                elif dp <= -COARSE_NOISE_PP:
                    lines.append("  - **质量明显下降** -> 除非有别的理由,否则不采纳")
                    if spd and spd > 5:
                        lines.append("    - 代价换来的是速度 %+.1f%% -> 显存或速度紧张时才值得考虑" % spd)
                elif dp >= COARSE_NOISE_PP:
                    lines.append("  - **质量明显提升** -> 建议采纳")
                elif spd is not None and abs(spd) >= 5:
                    lines.append("  - 质量基本不变而速度 %+.1f%% -> **建议采纳**" % spd)
    else:
        lines.append("_需要质量数据才能给出结论。_")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 判读说明")
    lines.append("")
    lines.append("- **证据不足** = 题量太少,差距落在噪声内。这不是\"无影响\","
                 "而是\"这个测试测不出\"。要判定需送阶段 2。")
    lines.append("- **无显著差异** = 配对检验 p ≥ 0.05,差距不可区分于随机。")
    lines.append("- 每一项质量差异都配了速度差异:只报质量而不报速度,会漏掉"
                 "\"准确率一样但快 40%%\"这种最值得采纳的情况。")
    return NL.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(ROOT, "results", "sweep"))
    ap.add_argument("--baseline", default="")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports", "sweep.md"))
    args = ap.parse_args()

    base_label = ""
    if args.baseline:
        base_label = (json.load(open(args.baseline, encoding="utf-8")).get("tag")
                      or os.path.splitext(os.path.basename(args.baseline))[0])

    text = render(args.results, base_label or None)
    print(text)
    if args.out and os.path.dirname(args.out):
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + NL)
        print(NL + "-> %s" % os.path.relpath(args.out, ROOT))


if __name__ == "__main__":
    main()
