# -*- coding: utf-8 -*-
"""gen_preset_speed.py - 从实测文档生成预设速度数据。

数据来源（唯一权威）：
  04_文档/02_方案与工程/2026-10_三路线实测/预设对照与GUI实测速度.md
  测试日期 2026-10-06，RTX 5060 Laptop 8GB sm_120a / 31.2 GiB RAM
  全部通过 Llama Rig 图形界面真实启动（CDP 真实鼠标事件驱动）

为什么不手写进 UI：数字必须能追溯到实测记录。改数字 = 改这份文档 + 重跑生成器。

Usage:
  python runner/gen_preset_speed.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "config", "preset_speed.json")

# 逐字抄自实测文档第二节"GUI 实测结果"表。
# ttft_ms / tps 分别对应 P1 短问答 与 P2 长输出。
MEASURED = [
    {
        "preset": "N1-ninfer-PTQ1-8G",
        "engine": "ninfer",
        "model": "bonsai2_27b_ternary_ptq1_native_mtp.ninfer",
        "weights_gb": 5.96,
        "port": 8095,
        "load_s": 3,
        "p1_ttft_ms": 162, "p1_tps": 45.0,
        "p2_ttft_ms": 274, "p2_tps": 37.3,
        "verdict": "最优",
        "note": "8GB 可用，启动最快、首字最快",
    },
    {
        "preset": "L4-light-Qwen3.5-9B",
        "engine": "llama.cpp",
        "model": "Qwen3.5-9B-UD-Q4_K_XL",
        "weights_gb": 5.71,
        "port": 8084,
        "load_s": 18,
        "p1_ttft_ms": 258, "p1_tps": 55.2,
        "p2_ttft_ms": 362, "p2_tps": 54.1,
        "verdict": "最快",
        "note": "解码吞吐最高，dense 9B",
    },
    {
        "preset": "L2-Ornith-ncmoe36",
        "engine": "llama.cpp",
        "model": "Ornith-1.5-35B-A3B",
        "weights_gb": 16.14,
        "port": 8082,
        "load_s": 9,
        "p1_ttft_ms": 1366, "p1_tps": 19.1,
        "p2_ttft_ms": 805, "p2_tps": 30.6,
        "verdict": "平衡",
        "note": "MoE 35B，n_cpu_moe=36",
    },
    {
        "preset": "L2-Ornith-ncmoe30",
        "engine": "llama.cpp",
        "model": "Ornith-1.5-35B-A3B",
        "weights_gb": 16.14,
        "port": 8082,
        "load_s": 17,
        "p1_ttft_ms": 1469, "p1_tps": 19.8,
        "p2_ttft_ms": 694, "p2_tps": 32.9,
        "verdict": "可用",
        "note": "MoE 35B，n_cpu_moe=30",
    },
    {
        "preset": "L2-Ornith-ncmoe42",
        "engine": "llama.cpp",
        "model": "Ornith-1.5-35B-A3B",
        "weights_gb": 16.14,
        "port": 8082,
        "load_s": 9,
        "p1_ttft_ms": 1378, "p1_tps": 16.7,
        "p2_ttft_ms": 897, "p2_tps": 27.8,
        "verdict": "可用",
        "note": "MoE 35B，n_cpu_moe=42",
    },
    {
        "preset": "L1-Qwen3.6-35B-APEX",
        "engine": "llama.cpp",
        "model": "Qwen3.6-35B-A3B-APEX",
        "weights_gb": 15.85,
        "port": 8081,
        "load_s": 13,
        "p1_ttft_ms": 2331, "p1_tps": 15.0,
        "p2_ttft_ms": 1159, "p2_tps": 26.7,
        "verdict": "可用",
        "note": "--cpu-moe 全部专家，ctx 262144",
    },
    {
        "preset": "K2-Bonsai2-KVMem-fast",
        "engine": "kvmem",
        "model": "Ternary-Bonsai-2-27B-PQ2_0",
        "weights_gb": 7.3,
        "port": 18202,
        "load_s": 3,
        "p1_ttft_ms": 865, "p1_tps": 10.0,
        "p2_ttft_ms": 703, "p2_tps": 9.9,
        "verdict": "省显存",
        "note": "KVMem 卸载 KV 到 CPU，吞吐受限",
    },
]

# 参数详情不再写在生成器里。
#
# 这里曾有一张手抄的 PROFILE_PARAMS（只有 apex-256k 一条），用一次实测就要手改
# 两处，迟早有一处是旧的。现在 params 由 presets.py 从 Llama-Rig 的 TOML 与
# profiles.json 的 vars 实时解析，21 套配置参数齐全且不会滞后。


def main():
    doc = {
        "_source": "04_文档/02_方案与工程/2026-10_三路线实测/预设对照与GUI实测速度.md",
        "_measured": "2026-10-06",
        "_machine": "RTX 5060 Laptop 8GB sm_120a / 31.2 GiB RAM",
        "_method": "Llama Rig 图形界面真实启动（CDP 真实鼠标事件）",
        "presets": MEASURED,
    }
    with open(OUT, "w", encoding="utf-8", newline=chr(10)) as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write(chr(10))
    print("写出 %s" % OUT)
    print("  预设 %d 个" % len(MEASURED))
    best = max(MEASURED, key=lambda x: x["p1_tps"])
    fast = min(MEASURED, key=lambda x: x["p1_ttft_ms"])
    print("  吞吐最高: %s %.1f t/s" % (best["preset"], best["p1_tps"]))
    print("  首字最快: %s %d ms" % (fast["preset"], fast["p1_ttft_ms"]))


if __name__ == "__main__":
    main()