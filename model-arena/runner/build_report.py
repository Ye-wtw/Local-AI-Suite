# -*- coding: utf-8 -*-
"""build_report.py - consolidate results/*.json into one leaderboard dataset + Markdown.
Scans every result file, groups by (endpoint, model, run stamp), merges dimension
scores and speed metrics, computes the weighted Intelligence Index, and writes:
  results/leaderboard.json     structured data (also embedded into the HTML)
  reports/leaderboard.md       readable summary
Usage: python runner/build_report.py
"""
import glob, json, os, re, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")
REPORTS = os.path.join(ROOT, "reports")

DIM_LABEL = {"D1": "速度", "D2": "选择", "D3": "数学", "D4": "代码", "D5": "指令",
             "D6": "Agent", "D7": "长上下文", "D8": "可靠性", "D9": "实战", "D10": "棋类"}


# 一个维度要进榜单,最少需要多少题。
#
# 为什么不是 1:跑测链路时每个维度只跑 1-4 题验证"能跑通",那是冒烟数据,
# 不是能力数据。它会因为"新 stamp 覆盖旧 stamp"顶掉整批真实结果 ——
# 实测 KV17 的 D2 因此从 81.7%(552 题)显示成 100%(4 题)。
#
# 定 10:低于 10 题的维度连方向都谈不上(项目铁律是 <100 只说方向),
# 而 10 题已经足以把"冒烟"挡住,又不会误伤真正的小规模诊断维度。
MIN_SAMPLE_N = 10


def load_weights():
    return json.load(open(os.path.join(ROOT, "config", "weights.json"), encoding="utf-8"))


def parse_name(path):
    """Return (kind, endpoint, stamp) from a result filename."""
    base = os.path.basename(path)[:-5]
    # stamp 后面可能挂**多段**后缀,不只一段。
    # 实测 D6/D8 的文件名是 ..._20261105-060459-kv17-261105-0202,
    # 旧正则只允许一段 (?:-[A-Za-z0-9]+)?,于是整个文件名解析失败 →
    # 这两个维度**从来没进过榜单**(KV17 因此只有 5 个维度而不是 8 个)。
    # stamp 后面可能挂**多段**后缀,还可能带 _merged/_rescored 这类标记。
    # 实测两个失败案例:
    #   ..._20261105-060459-kv17-261105-0202  (多段,旧正则只允许一段)
    #   ..._20261105-020441-kv17-261105-0202_merged.json  (带 _merged)
    # 解析失败的后果很严重:文件被整份跳过。D6/D8 因此从未进榜单;
    # 而 D2 因为丢掉 _merged(80.98%,补跑后)只剩未合并的 45.29%
    # (257 题连接失败被算成答错)—— 正是铁律 6 禁止的口径。
    m = re.match(r"^(.*?)_([A-Za-z0-9\-]+)_(\d{8}-\d{6}(?:-[A-Za-z0-9]+)*)(?:_\w+)?$", base)
    if not m:
        return None, None, None
    return m.group(1), m.group(2), m.group(3)


def main():
    weights = load_weights()
    scored = [d for d, v in weights["dims"].items() if v.get("scored")]
    runs = {}

    def slot(endpoint, stamp):
        return runs.setdefault((endpoint, stamp), {"endpoint": endpoint, "stamp": stamp,
                                                   "model": None, "preset": None, "dims": {},
                                                   "speed": {}, "level": None, "errors": {},
                                                   "elapsed_s": None})

    for path in sorted(glob.glob(os.path.join(RESULTS, "*.json"))):
        if os.path.basename(path).startswith("leaderboard"):
            continue
        kind, ep, stamp = parse_name(path)
        if not kind:
            continue
        # 排除不是"一次完整评测"的文件。四类都会被误当成正式数据:
        #
        #   run_*     : --suite all 的汇总壳。实测 141 个,多数是
        #               mocktest / fixcheck / ui 这种跑几道题验证链路的冒烟,
        #               它的 by_dim 会把整批正式结果顶掉。
        #   *refill*  : 只补跑连接失败的题。按铁律它是"补"不是独立成绩,
        #               与原盘合并后的 _merged 文件才代表最终结果。
        #   *retry*   : 错题重做,属于 best-of-N,必须单独成栏,绝不进 pass@1。
        #   *selftest*: 判分器自测,根本不是模型输出。
        bn = os.path.basename(path)
        if (bn.startswith("run_") or "refill" in bn
                or "retry" in bn or "selftest" in bn):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        r = slot(ep, stamp)
        # 预设名是比 endpoint 更稳的身份:同一个模型可能昨天连 18260(kvmem)、
        # 今天走队列(queue),两次评测其实是同一套配置。只按 endpoint 归并,
        # 会让同一模型在榜单上出现两行 —— 实测 KV17 就这样被拆成
        # "kvmem(D2/D3/D4/D5)" 和 "queue(D9)" 两行。
        if isinstance(data, dict) and data.get("preset"):
            r["preset"] = data["preset"]

        # LEVEL_* files -> level summary with per-dim accuracy
        if kind.startswith("LEVEL_"):
            s = data.get("summary") or data
            r["level"] = s.get("level")
            # The version may sit on the wrapper (run.py save) or inside the
            # summary (rejudge.py write). Reading only one of them made every
            # rejudged file look unversioned even though it carried the field.
            r["judge_version"] = (data.get("judge_version") or s.get("judge_version"))
            if s.get("model"):
                r["model"] = s["model"]
            if s.get("elapsed_s"):
                r["elapsed_s"] = s["elapsed_s"]
            # LEVEL_/run_ 汇总里的 by_dim 是**跨维度一次跑的顺带产物**,
            # 跑测链路验证时每个维度只有 1-4 题(实测 run_..._-ui.json 就是
            # D2 n=4 / D4 n=3 这种冒烟数据)。它一旦进榜单,会因为后面
            # "新 stamp 覆盖旧 stamp"把 552 题的真实结果顶掉 ——
            # 实测 D2 因此从 81.7% 变成 100%(n=4)。
            # 项目铁律:样本 <100 只能说方向。这里设硬门槛,小样本根本不入库。
            for d, b in (s.get("by_dim") or {}).items():
                if b.get("n") and b["n"] >= MIN_SAMPLE_N:
                    r["dims"][d] = {"n": b["n"], "correct": b["correct"],
                                    "acc": b["correct"] / b["n"]}
            continue

        # per-dimension files -> summary + records
        s = data.get("summary") if isinstance(data, dict) else None
        if s and s.get("dim"):
            d = s["dim"]
            if s.get("model"):
                r["model"] = s["model"]
            if s.get("n"):
                r["dims"][d] = {"n": s["n"], "correct": s.get("correct", 0),
                                "acc": s.get("accuracy", 0.0),
                                "errors": s.get("errors", 0)}
            continue

        # run_* files -> aggregate per-suite summaries
        if isinstance(data, dict):
            if data.get("model"):
                r["model"] = data["model"]
            for key, val in data.items():
                if not isinstance(val, dict):
                    continue
                if key == "speed" or "results" in val and key == "speed":
                    pass
            if isinstance(data.get("speed"), dict):
                for w in data["speed"].get("results", []):
                    if w.get("runs"):
                        r["speed"][w["workload"]] = {
                            "ttft_p50": w["ttft_s"]["p50"], "ttft_p95": w["ttft_s"]["p95"],
                            "tps_p50": w["output_tps"]["p50"], "e2e500_p50": w["e2e_500_s"]["p50"]}
            for key, dimname in (("math", "D3"), ("mcq", "D2"), ("instruction", "D5"),
                                 ("chess", "D10"), ("longctx", "D7"), ("code", "D4"),
                                 ("agent", "D6"), ("reliability", "D8")):
                sec = data.get(key)
                if isinstance(sec, dict) and sec.get("n"):
                    r["dims"].setdefault(dimname, {"n": sec["n"], "correct": sec.get("correct", 0),
                                                   "acc": sec.get("accuracy", 0.0)})

    # speed files (standalone)
    for path in sorted(glob.glob(os.path.join(RESULTS, "D1_speed_*.json"))):
        kind, ep, stamp = parse_name(path)
        if not kind:
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        r = slot(ep, stamp)
        if data.get("model"):
            r["model"] = data["model"]
        for w in data.get("results", []):
            if w.get("runs"):
                r["speed"][w["workload"]] = {
                    "ttft_p50": w["ttft_s"]["p50"], "ttft_p95": w["ttft_s"]["p95"],
                    "tps_p50": w["output_tps"]["p50"], "e2e500_p50": w["e2e_500_s"]["p50"]}

    # ---- merge per (endpoint, model): latest measurement wins per dimension ----
    # A score is only comparable to another produced by the SAME judge. Judges get
    # fixed, and a fix silently invalidates earlier numbers - this session rewrote
    # five of them (hallucination length rule, refusal delivery rule, _has_any CJK
    # matching, SQL reference answers, D9 exec scoring). Blending old and new by
    # timestamp alone would publish a ranking no current judge would produce, so
    # each dimension carries the version it came from and mismatches are reported.
    try:
        import judge_version as JV
        CURRENT_JV = JV.judge_version()
        SUITE_JV = JV.all_versions()
    except Exception:
        CURRENT_JV = None
        SUITE_JV = {}

    def expected_version(dim):
        """Version this dimension must be compared under.

        Not every judge is reachable from every dimension. D2..D10 use the core
        judges only; the public-benchmark suite has its own. Comparing them all
        against one global hash reports a suite-local edit as damage to results
        that cannot reach that file - a false alarm that teaches readers to
        ignore the warning.
        """
        if dim in ("PUBLIC", "public"):
            return SUITE_JV.get("public") or CURRENT_JV
        if dim in ("LCB", "lcb"):
            return SUITE_JV.get("lcb") or CURRENT_JV
        return CURRENT_JV
    stale = []
    merged = {}
    for (ep, stamp), r in sorted(runs.items(), key=lambda kv: kv[0][1]):
        if not r["dims"] and not r["speed"]:
            continue
        # 归并键 = **模型名**,不用 (endpoint, model)。
        #
        # 为什么不用 endpoint:它只是"当时连哪个端口"(kvmem 18260 / queue 队列)。
        # 同一个模型昨天走 kvmem、今天走 queue,其实还是同一套配置 ——
        # 按 endpoint 归并会把它拆成两行,实测 KV17 就成了
        # "kvmem(D2/D3/D4/D5) + queue(D9)" 两行,选项点开还以为是两个型号。
        #
        # 为什么也不用 preset:67 个老结果文件没有 preset 字段,只有 model 名。
        # 按 preset 归并则这些老结果永远对不上新结果。实测 10 个模型里
        # 只有 kv17 同时存在 (none) 和新 preset,即同一型号的新旧两批。
        #
        # 代价:同一 model 名若真有两套参数配置,会被并成一行。当前数据里
        # 不存在这种情况;将来出现时要靠 preset 拆分,届时以 preset 为准。
        pre = r.get("preset") or ""
        key = ("model", r["model"] or "?")
        m = merged.setdefault(key, {"endpoint": ep, "model": r["model"] or "?",
                                    "preset": pre or None,
                                    "dims": {}, "speed": {}, "levels": [], "last_stamp": stamp,
                                    "runs": 0})
        m["runs"] += 1
        m["last_stamp"] = stamp
        for d, v in r["dims"].items():
            # Keep only dimensions that carry weight. A diagnostic dimension (D10)
            # has weight 0 and would otherwise inflate the coverage ratio above
            # 100% - 9 measured of 8 scored.
            if d in scored:
                jv = r.get("judge_version")
                # A missing version is NOT "fine" - it means the file predates
                # versioning, so nobody can say which judge produced it. Treat it
                # as unknown and report it, otherwise the oldest and least
                # trustworthy numbers are the ones that silently survive.
                want = expected_version(d)
                if want and jv != want:
                    stale.append({"endpoint": ep, "model": r.get("model"),
                                  "dim": d, "file_version": jv or "unknown",
                                  "current": want, "stamp": stamp})
                v = dict(v)
                v["judge_version"] = jv or "unknown"
                # 合并规则:**样本多的优先**,同样多才让新的覆盖旧的。
                #
                # 原来这里是"后写入的无条件覆盖"(latest wins),后果是
                # 一次 3 题的冒烟测试能把 552 题的正式结果顶掉,榜单显示 100%。
                # 题多的那份才是更可信的估计,这比"时间新"重要得多。
                prev = m["dims"].get(d)
                if prev is None or (v.get("n") or 0) > (prev.get("n") or 0):
                    m["dims"][d] = v
                elif (v.get("n") or 0) == (prev.get("n") or 0):
                    m["dims"][d] = v     # 同样本量,新的替换旧的
            else:
                m.setdefault("diagnostic", {})[d] = v
        for w, v in r["speed"].items():
            m["speed"][w] = v
        if r.get("elapsed_s"):
            m["elapsed_s"] = r["elapsed_s"]
        if r.get("level"):
            m["levels"].append(r["level"])

    # ---- weighted index ----
    out = []
    total_w = sum(weights["dims"][d]["weight"] for d in scored)
    for key, m in merged.items():
        num, den = 0.0, 0.0
        for d in scored:
            if d in m["dims"]:
                w = weights["dims"][d]["weight"]
                num += w * m["dims"][d]["acc"]
                den += w
        m["index"] = (num / den) if den > 0 else None
        m["coverage"] = den / total_w
        m["measured"] = sorted(m["dims"])
        out.append(m)
    out.sort(key=lambda x: (x["index"] is None, -(x["index"] or 0)))

    # per-question correctness for LEVEL runs -> enables PAIRED comparison in the UI
    paired = {}
    for path in sorted(glob.glob(os.path.join(RESULTS, "LEVEL_*.json"))):
        kind, ep, stamp = parse_name(path)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        s2 = d.get("summary") or {}
        model = s2.get("model") or "?"
        key = "%s|%s|%s" % (model, s2.get("level") or "?", stamp)
        recs = d.get("records") or []
        paired[key] = {"model": model, "level": s2.get("level"), "stamp": stamp,
                       "n": len(recs),
                       "map": {r["id"]: (1 if r.get("correct") else 0) for r in recs},
                       "dim": {r["id"]: r.get("dim") for r in recs}}

    payload = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "index_name": weights["index_name"],
               "weights": weights, "runs": out,
               "paired": paired,
               "dim_label": DIM_LABEL}
    os.makedirs(REPORTS, exist_ok=True)
    json.dump(payload, open(os.path.join(RESULTS, "leaderboard.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # Markdown
    lines = ["# model-arena 评测榜单", "",
             "> 生成时间 %s ｜ 权重比例参考 Artificial Analysis 官方四大类;" % payload["generated"],
             "> **维度→基准的映射为本项目自设计**,非官方榜单。", ""]
    dims_present = sorted({d for r in out for d in r["dims"]},
                          key=lambda d: (len(d), d))
    head = ("| 模型 | endpoint | 智力量表 | 覆盖 | "
            + " | ".join(DIM_LABEL.get(d, d) for d in dims_present))
    lines.append(head)
    lines.append("|---" * (4 + len(dims_present)) + "|")
    for r in out:
        cells = []
        for d in dims_present:
            v = r["dims"].get(d)
            cells.append("%.1f%%" % (v["acc"] * 100) if v else "—")
        idx = "%.1f" % (r["index"] * 100) if r["index"] is not None else "—"
        lines.append("| %s | %s | **%s** | %.0f%% | %s |"
                     % (r["model"], r["endpoint"], idx, r["coverage"] * 100, " | ".join(cells)))
    lines += ["", "## 速度", ""]
    lines.append("| 模型 | 负载 | TTFT P50 | TTFT P95 | 输出 tok/s P50 | E2E-500 P50 |")
    lines.append("|---|---|---|---|---|---|")
    for r in out:
        for w, m in sorted(r["speed"].items()):
            lines.append("| %s | %s | %.2fs | %.2fs | %.1f | %.2fs |"
                         % (r["model"] or "?", w, m["ttft_p50"], m["ttft_p95"],
                            m["tps_p50"], m["e2e500_p50"]))
    lines += ["", "## 权重来源", "",
              "| 维度 | 权重 | 计分 |", "|---|---:|---|"]
    for d, v in weights["dims"].items():
        lines.append("| %s %s | %.2f | %s |" % (d, v["name"], v["weight"],
                                                "是" if v.get("scored") else "否(仅诊断)"))
    open(os.path.join(REPORTS, "leaderboard.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print("runs aggregated: %d" % len(out))
    for r in out:
        idx = "%.1f" % (r["index"] * 100) if r["index"] is not None else "n/a"
        print("  %-22s %-8s index=%-6s dims=%s" % (r["model"] or "?", r["endpoint"], idx,
                                                   ",".join(sorted(r["dims"]))))
    if stale:
        unknown = [s for s in stale if s["file_version"] == "unknown"]
        old = [s for s in stale if s["file_version"] != "unknown"]
        print()
        print("  !! 判分器版本核对（当前 %s）" % CURRENT_JV)
        if old:
            print("     由更早版本的判分器产生，数字不可比:")
            seen = set()
            for s in old:
                k = (s["model"], s["dim"], s["file_version"])
                if k in seen:
                    continue
                seen.add(k)
                print("       %-20s %-3s 旧=%s" % (str(s["model"])[:20], s["dim"],
                                                    s["file_version"]))
        if unknown:
            models = sorted(set(str(s["model"])[:20] for s in unknown))
            print("     %d 个维度没有版本标记（早于版本机制引入）:" % len(unknown))
            print("       %s" % ", ".join(models[:6]))
        print("     重跑后自动带上版本号: python runner/run.py --endpoint <ep> --suite <suite>")
    print("-> results/leaderboard.json")
    print("-> reports/leaderboard.md")


if __name__ == "__main__":
    main()
