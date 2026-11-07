# -*- coding: utf-8 -*-
"""perf.py - 每维度耗时/速度与硬件占用(不进指数分)。

为什么单独一块:
  正确率回答"准不准",耗时回答"用起来顺不顺"。两者是不同的问题,
  混进一个分数里会让"慢但准"的模型和"快但错"的模型看起来一样好。
  所以这块数据只呈现,不打分 —— 由用户按自己的场景取舍。

数据来源(全部实测,不估算):
  * 每题 latency_s  -> 中位数 / P95 / 秒每题
  * 队列采样的 GPU  -> 显存峰 / 利用率均 / 温度峰 / 功耗均
  * 进程 RSS        -> 内存峰

刻意**不**给 tok/s:结果文件里没有存 token 数(只有文本),
按字数反推会给出一个看起来精确、实际不可比的假数字。
"""
import glob
import json
import os


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _p95(xs):
    """P95,不做插值 —— 样本少时插值会造出不存在的值。"""
    if not xs:
        return None
    s = sorted(xs)
    idx = int(round(0.95 * (len(s) - 1)))
    return s[idx]


# 样本少于这个数就不出统计:3 个点的"中位数"没有意义,
# 而且会让人误以为这个维度跑得特别快(实测 D6 曾因只有 1 个样本显示 0.01 s/题)。
MIN_LAT_SAMPLES = 10


def latency_stats(records):
    """从 records 算每题耗时的分布。"""
    lat = [r.get("latency_s") for r in records or [] if r.get("latency_s")]
    if len(lat) < MIN_LAT_SAMPLES:
        return None
    return {
        "n": len(lat),
        "median_s": round(_median(lat), 2),
        "p95_s": round(_p95(lat), 2),
        "min_s": round(min(lat), 2),
        "max_s": round(max(lat), 2),
        "per_q_s": round(sum(lat) / len(lat), 2),
    }


def perf_from_queue_state(path):
    """队列状态文件 -> 每套件的硬件占用。没有采样就返回 {}。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    out = {}
    for it in d.get("items") or []:
        p = it.get("performance") or {}
        st = p.get("stats") or {}
        suite = it.get("suite")
        if not suite or not st:
            continue
        def mx(k):
            v = st.get(k) or {}
            return v.get("max")
        def mean(k):
            v = st.get(k) or {}
            return v.get("mean")
        # 采样太少的套件不能算硬件占用:实测 D9 那次只采到 1 个点
        # (服务刚起就结束),报出 69℃/11W,和同机其它套件的 87-90℃/90W 完全矛盾。
        # 宁可显示 "—" 也不给一个会误导决策的数字。
        n = p.get("n_samples") or 0
        if n < 3:
            continue
        out[suite] = {
            "launch_s": it.get("launch_seconds"),
            "samples": n,
            "gpu_mem_peak_mib": mx("gpu_mem_used_mib"),
            "gpu_util_mean_pct": mean("gpu_util_pct"),
            "gpu_temp_peak_c": mx("gpu_temp_c"),
            "gpu_power_mean_w": mean("gpu_power_w"),
            "proc_rss_peak_mib": mx("proc_rss_mib"),
            "mem_free_min_gib": (st.get("mem_free_gib") or {}).get("min"),
        }
    return out


def preset_speeds(config_path=None):
    """预设的实测 tok/s,来自 config/preset_speed.json。

    为什么要单独读这个文件:结果文件里没有记录 token 数,无法从 latency
    反推速度。这份数据是 Llama Rig 图形界面实测记录的
    (p1=短提示、p2=长提示),是唯一可用的真实速度来源。
    没有登记的预设写「—」,不估算 —— 编出来的 tok/s 不可比。
    """
    if not config_path:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(here, "config", "preset_speed.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    out = {}
    for p in d.get("presets") or []:
        name = p.get("preset")
        if not name:
            continue
        info = {"p1_tps": p.get("p1_tps"), "p2_tps": p.get("p2_tps"),
                "ttft_ms": p.get("p1_ttft_ms"), "load_s": p.get("load_s"),
                "verdict": p.get("verdict")}
        # 两份命名不一致时也要能对上。实测遇到的三种写法:
        #   catalog 里叫  L1-Qwen3.6-35B-APEX
        #   预设里叫      L1-main-Qwen3.6-35B-APEX
        #   结果文件里叫  Qwen3.6-35B-APEX
        # 只剥一段前缀(旧实现)只能得到 main-Qwen3.6-35B-APEX,仍然对不上,
        # 于是"明明测过速却显示未测"。
        #
        # 现在把每一段前缀都剥一遍,登记所有后缀;key 已存在就不覆盖,
        # 保证长名(信息更全的)不被短名顶掉。
        out[name] = info
        parts = name.split("-")
        for i in range(1, len(parts)):
            suffix = "-".join(parts[i:])
            if suffix and suffix not in out:
                out[suffix] = info
    return _PrefixTolerant(out)


class _PrefixTolerant(dict):
    """查不到就逐段剥前缀再查。

    为什么需要两个方向:登记时剥的是「测速表里的名字」,
    但实际查询用的可能是**更长**的名字(预设 L1-main-Qwen3.6-35B-APEX
    vs catalog 里的 L1-Qwen3.6-35B-APEX)。登记端剥不出来,只能在查询端剥。

    两侧都剥,任意一种"同一型号、不同前缀"的命名都能对上。
    查不到就返回 None(调用方按"未测"处理),不猜。
    """

    def get(self, key, default=None):
        if key in self:
            return dict.get(self, key)
        parts = str(key).split("-")
        for i in range(1, len(parts)):
            suffix = "-".join(parts[i:])
            if suffix in self:
                return dict.get(self, suffix)
        return default


def collect(results_dir, queue_dir=None):
    """汇总:每套件 {耗时分布, 硬件占用}。同套件有多份结果时取题数最多的。"""
    best = {}
    for p in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        name = os.path.basename(p)
        if not (name.startswith("LEVEL_") or name.startswith("D")):
            continue
        # 排除不是"真实测出来的"结果:
        #   refill/retry 不是独立能力指标;mock 是假端点(延迟 0.00s),
        #   混进来会让性能表出现"某维度 0.01 s/题"这种不可能的数字。
        if "refill" in name or "retry" in name or "_mock_" in name or "mock-" in name:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        s = d.get("summary") or d.get("level") or {}
        suite = s.get("suite") or ""
        dim = s.get("dim") or ""
        if not suite and not dim:
            continue
        # 必须按 (预设, 套件) 分组,不能只按套件 ——
        # 实测踩过:只按套件分组时,云端 deepseek-flash 和本地
        # Qwen3.6-35B 的耗时被合进同一个中位数,算出来的数谁也不代表。
        # 不同模型的速度差异正是这张表的重点,合起来就把信号抹掉了。
        preset = d.get("preset") or s.get("model") or "?"
        key = (preset, suite or dim)
        stats = latency_stats(d.get("records"))
        if not stats:
            # 记录里没存耗时(或有效样本太少):这个维度的性能无从得知。
            # 旧结果普遍缺这个字段,所以这里必须能安静跳过,不能编一个数出来。
            continue
        # 选中规则:题数多的优先;题数相同时**有 preset 字段的优先**。
        # 实测踩过:三份 D2 结果都是 552 题,按文件名字母序先读到老结果
        # (preset=None),合并后的新结果因为 n 不比它大而落选,
        # 于是明明是新数据却被标成"历史结果"。
        cur = best.get(key)
        better = (cur is None
                  or stats["n"] > cur["latency"]["n"]
                  or (stats["n"] == cur["latency"]["n"]
                      and bool(d.get("preset")) and not cur.get("has_preset")))
        if better:
            best[key] = {"preset": preset, "dim": dim, "suite": suite or dim,
                         "model": s.get("model"),
                         # 老结果没有 preset 字段 —— 归并出来的名字只是 model 名。
                         # 云端端点本来就没有本地测速记录,不该显示"未测"。
                         "has_preset": bool(d.get("preset")),
                         "cloud": (s.get("endpoint") or "").startswith("deepseek")
                                  or "https://" in (d.get("base_url") or ""),
                         "latency": stats, "elapsed_s": s.get("elapsed_s"),
                         "lat_coverage": "%d/%d" % (stats["n"],
                                                    s.get("n") or stats["n"])}

    hw = {}
    for p in sorted(glob.glob(os.path.join(queue_dir or os.path.join(results_dir, ".queue"),
                                          "q_*.json"))):
        got = perf_from_queue_state(p)
        for suite, v in got.items():
            cur = hw.get(suite)
            if cur is None or (v.get("samples") or 0) >= (cur.get("samples") or 0):
                hw[suite] = v
    for key in best:
        if key[1] in hw:
            best[key]["hardware"] = hw[key[1]]

    # 挂上预设在测速记录里的真实 tok/s(没有登记就留 None,报告写「—」)
    sp = preset_speeds()
    for key in best:
        info = sp.get(key[0])
        if info is None:
            # 老结果没有 preset 字段,只有 model 名(如 kv17-bonsai2t5),
            # 而测速表登记的是预设名。model 名与某个预设登记项相同就认作同一套。
            # 只在 preset 匹配失败时才试 model 名,避免把不同预设误并。
            info = sp.get(best[key].get("model") or "")
        if info is not None:
            best[key]["speed"] = info
    return best


if __name__ == "__main__":
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    got = collect(os.path.join(root, "results"))
    for (preset, suite), v in sorted(got.items()):
        lat, hwv = v["latency"], v.get("hardware") or {}
        sp = v.get("speed") or {}
        print("%-26s %-12s n=%-4d 典型 %.2fs 最慢 %.2fs %.2f s/题 | tok/s %s | 显存 %s 温度 %s" % (
            preset[:26], suite, lat["n"], lat["median_s"], lat["max_s"], lat["per_q_s"],
            sp.get("p2_tps") or "-",
            hwv.get("gpu_mem_peak_mib") or "-", hwv.get("gpu_temp_peak_c") or "-"))
    sys.exit(0)
