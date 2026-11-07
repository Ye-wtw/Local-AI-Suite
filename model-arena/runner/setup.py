# -*- coding: utf-8 -*-
"""setup.py - first-run wizard. Run this once after cloning.

A stranger cloning this repository has none of the things the code assumes: no
machine.json, no models.json, no launch profile, no idea whether their GPU can run
the sweep. Rather than let them hit a confusing failure three commands later, this
checks everything up front and writes the config files it can.

It is also honest about what it cannot do: it will not download a 20 GB model, and
it will not invent a llama.cpp install.

Usage:
  python runner/setup.py            # check and configure
  python runner/setup.py --check    # report only, change nothing
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
OK, WARN, BAD = "✓", "!", "✗"


def check_python():
    v = sys.version_info
    good = v >= (3, 8)
    return good, "%s Python %d.%d.%d" % (OK if good else BAD, v.major, v.minor, v.micro), \
        "需要 3.8+(本项目只用标准库,无第三方依赖)" if good else "Python 版本过低"


def check_stdlib_only():
    """The zero-dependency claim is worth verifying, not asserting."""
    missing = []
    for mod in ("json", "urllib.request", "http.server", "sqlite3", "subprocess"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return (not missing), \
        "%s 标准库依赖" % (OK if not missing else BAD), \
        ("全部可用" if not missing else "缺失: " + ", ".join(missing))


def check_gpu():
    out = ""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, errors="ignore", timeout=15)
        out = (r.stdout or "").strip()
    except Exception:
        pass
    if out:
        first = out.splitlines()[0]
        return True, "%s GPU: %s" % (OK, first), ""
    return False, "%s 未检测到 NVIDIA GPU" % WARN, \
        "AMD/Apple/核显用户可手动填写 config/machine.json 继续使用(速度测量会受限)"


def check_llama():
    try:
        from detect import detect_llama
        found = detect_llama()
    except Exception as e:
        return False, "%s llama.cpp 探测失败" % BAD, str(e)[:80]
    if found:
        return True, "%s llama-server: %s" % (OK, found[0]["path"]), \
            "来源: %s" % found[0]["source"]
    return False, "%s 未找到 llama-server" % WARN, \
        "纯云端评测不需要它;本地模型评测需要,请安装 llama.cpp 或填 config/machine.json"


def check_models():
    try:
        from detect import find_ggufs
        found = find_ggufs(limit=3)
    except Exception:
        found = []
    if found:
        return True, "%s 发现 %d 个 GGUF 模型" % (OK, len(found)), found[0]
    return False, "%s 未发现 GGUF 模型" % WARN, \
        "本地评测需要模型文件;可用 --model 指定,或先跑云端对比"


def check_datasets():
    full = os.path.join(ROOT, "datasets", "full")
    n = 0
    if os.path.isdir(full):
        import glob
        for p in glob.glob(os.path.join(full, "D*_full.jsonl")):
            with open(p, encoding="utf-8") as f:
                n += sum(1 for line in f if line.strip())
    if n:
        return True, "%s 题库: %d 题" % (OK, n), ""
    return False, "%s 题库为空" % WARN, \
        "运行 python scripts/gen_datasets.py && python scripts/build_manifest.py 生成"


def check_public():
    pub = os.path.join(ROOT, "datasets", "public")
    have = 0
    if os.path.isdir(pub):
        import glob
        have = len(glob.glob(os.path.join(pub, "*.jsonl")))
    if have:
        return True, "%s 公开基准: %d 个来源" % (OK, have), ""
    return False, "%s 公开基准未下载" % WARN, \
        "运行 python scripts/fetch_public.py 下载(需要网络,约 160 MB)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告,不修改")
    ap.add_argument("--yes", action="store_true", help="自动确认")
    args = ap.parse_args()

    print("=" * 66)
    print("  Model Arena · 首次运行检查")
    print("=" * 66)
    print()

    checks = [
        ("Python", check_python),
        ("依赖", check_stdlib_only),
        ("GPU", check_gpu),
        ("llama.cpp", check_llama),
        ("模型文件", check_models),
        ("题库", check_datasets),
        ("公开基准", check_public),
    ]
    results = {}
    for name, fn in checks:
        try:
            ok, msg, hint = fn()
        except Exception as e:
            ok, msg, hint = False, "%s %s 检查异常" % (BAD, name), str(e)[:80]
        results[name] = ok
        print("  %-58s" % msg)
        if hint and not ok:
            print("      → %s" % hint)

    print()
    print("-" * 66)

    # ---- write what can be written ----
    wrote = []
    if not args.check:
        cfg_machine = os.path.join(ROOT, "config", "machine.json")
        cfg_models = os.path.join(ROOT, "config", "models.json")
        if not os.path.exists(cfg_machine) or not os.path.exists(cfg_models):
            try:
                from detect import build_config, find_ggufs
                paths = find_ggufs(limit=3)
                build_config(write=True, model_paths=paths)
                wrote.append("config/machine.json")
                wrote.append("config/models.json")
            except Exception as e:
                print("  自动写配置失败: %s" % str(e)[:80])
                print("  可手动复制 config/machine.example.json")

    if wrote:
        print("  已生成配置:")
        for w in wrote:
            print("    %s" % w)
        print()

    # ---- what to do next ----
    critical_ok = results.get("Python") and results.get("依赖") and results.get("题库")
    print("  下一步:")
    if not results.get("题库"):
        print("    1. 生成题库:  python scripts/gen_datasets.py && python scripts/build_manifest.py")
    if not results.get("公开基准"):
        print("    %d. 下载公开基准:  python scripts/fetch_public.py" % (2 if not results.get("题库") else 1))
    if not results.get("llama.cpp") or not results.get("模型文件"):
        print("    · 本地模型评测需要 llama.cpp + GGUF 模型")
        print("      只做云端对比的话可以跳过")
    print("    · 查看可用命令:  python runner/arena_cli.py --help")
    print("    · 打开界面:      python runner/arena_desktop.py --gui")
    print()

    if not critical_ok:
        print("  ⚠ 有阻塞项未满足,先处理上面的提示。")
        return 1
    print("  ✓ 基础环境就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
