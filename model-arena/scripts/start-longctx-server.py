# -*- coding: utf-8 -*-
"""start-longctx-server.py - 用 -nkvo 起一个能服务 128K/256K 的本地服务。

为什么单独给一个脚本：
D7 的 120 题里有 64 题需要 128k 以上上下文。默认的 llama-server 会把
KV cache 放进显存，8GB 卡在 128K 就会 OOM —— 于是这些题全部被判"跳过"，
看起来像长上下文能力为零，实际是**服务根本没开到位**。

llama.cpp 的 -nkvo（--no-kv-offload）把 KV 放到主机内存，权重仍在显存，
8GB 卡因此可以服务 256K：实测权重 ~7.3GB + KV 在内存，200 能正常返回。
代价是慢：256K 上下文生成约 13 t/s（短上下文是 53 t/s）。

用法：
    python start-longctx-server.py --model <path.gguf> [--port 8080]
    python start-longctx-server.py --model <path.gguf> --ctx 131072
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXE = r"E:\AI_workspace\00_models_Base\llama.cpp\bin\llama-server.exe"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="path to the .gguf")
    ap.add_argument("--exe", default=os.environ.get("LLAMA_SERVER", DEFAULT_EXE))
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ctx", type=int, default=263168,
                    help="context size; 263168 covers every D7 question")
    ap.add_argument("--ngl", type=int, default=99, help="layers to offload to GPU")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--alias", default="local")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.exe):
        sys.exit("llama-server not found: %s (set --exe or LLAMA_SERVER)" % a.exe)
    if not os.path.isfile(a.model):
        sys.exit("model not found: %s" % a.model)

    cmd = [a.exe, "--model", a.model, "--host", "127.0.0.1", "--port", str(a.port),
           "--ctx-size", str(a.ctx), "--n-gpu-layers", str(a.ngl),
           "--threads", str(a.threads), "--alias", a.alias,
           "--temp", "0", "--parallel", "1",
           # q8_0 KV halves the cache vs f16, which is what makes 256K fit at all
           "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
           # the whole point: KV in host RAM, not VRAM
           "-nkvo"]
    print(" ".join(cmd))
    if a.print_only:
        return 0
    print()
    print("提示：256K 上下文时生成速度约 13 t/s，D7 全量需要 1-3 小时。")
    print("      显存占用约 7.3GB（权重），KV 在内存。Ctrl+C 停止。")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
