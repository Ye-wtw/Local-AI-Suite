# -*- coding: utf-8 -*-
"""build_lazy_package.py - 造一个"打开即用"的 Windows 整合包。

包里有什么：
  - 全部代码、题库、文档（去掉可再生成的大件）
  - 一份独立的 Python 3.12 运行时（用户不用装 Python）
  - 一个「启动.bat」：双击 → 自检 → 打开界面

包里没有什么：
  - results/ cache/ reference/（作者私有，且可再生成）
  - datasets/public/（fetch_bench.py 可下）
  - datasets/fixtures/longctx/long/（gen_d7_long.py 可生成）
  - 任何 .gguf 模型（太大，用户自备）

用法：
  python scripts/build_lazy_package.py [--runtime <内置 python 目录>] [--out <输出目录>]
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG_NAME = "model-arena-v0.1"

EXCLUDE_TOP = {"results", "cache", "reference", "_build_rt", ".git", "node_modules",
               # 输出目录也在项目里：不排除的话第二次构建会把上一次的包打进去
               "_pkg"}
EXCLUDE_REL = {
    os.path.join("datasets", "public"),
    os.path.join("datasets", "fixtures", "longctx", "long"),
    os.path.join("datasets", "_mined"),
    os.path.join("config", "machine.json"),
    os.path.join("config", "models.json"),
    os.path.join("config", "profiles.json"),
}
EXCLUDE_GLOB = ("__pycache__", ".pyc", ".log", ".bak", "_srv_", "tmpui")

LAUNCHER = r"""@echo off
chcp 936 > nul
cd /d "%~dp0"
title Model Arena v0.1
echo ==================================================
echo   Model Arena v0.1
echo ==================================================
echo.
set PY=%~dp0runtime\python.exe
if not exist "%PY%" (
  echo   [!] 找不到内置 Python: %PY%
  echo       请重新解压完整压缩包。
  pause
  exit /b 1
)
echo   [1/2] 环境自检...
"%PY%" runner\setup.py
if errorlevel 1 goto fail
echo.
echo   [2/2] 打开界面（浏览器会自动打开）...
echo         关闭这个窗口即停止服务。
echo.
"%PY%" runner\arena_api.py --port 0 --open-ui
goto end
:fail
echo.
echo   启动失败，见上面的提示。
:end
pause
"""


def keep(rel):
    parts = rel.split(os.sep)
    if parts[0] in EXCLUDE_TOP:
        return False
    if rel in EXCLUDE_REL:
        return False
    for p in EXCLUDE_REL:
        if rel.startswith(p + os.sep):
            return False
    for g in EXCLUDE_GLOB:
        if g in rel:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=os.path.join(ROOT, "_build_rt",
                    "cpython-3.12.14-windows-x86_64-none"))
    ap.add_argument("--out", default=os.path.join(ROOT, "_pkg"))
    a = ap.parse_args()

    if not os.path.isfile(os.path.join(a.runtime, "python.exe")):
        sys.exit("内置 Python 不存在: %s\n先跑: uv python install 3.12 --install-dir <dir>" % a.runtime)

    dest = os.path.join(a.out, PKG_NAME)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    print("target:", dest)

    n = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_TOP]
        for fn in filenames:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, ROOT)
            if not keep(rel):
                continue
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    print("copied %d files" % n)

    # bundled runtime
    rt_dst = os.path.join(dest, "runtime")
    print("copying runtime...")
    shutil.copytree(a.runtime, rt_dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test", "tests",
                                                  "site-packages", "idlelib", "tkinter",
                                                  "turtledemo", "ensurepip"))
    # an embedded runtime ships python312._pth on some builds; keep it (it pins sys.path)
    print("runtime copied")

    # Launcher: cmd.exe reads .bat with the OEM codepage, and the file does
    # "chcp 936", so the bytes must be GBK. Writing UTF-8 here produced mojibake
    # in the very first screen the user sees.
    def write_bat(path, text):
        try:
            data = text.encode("gbk")
        except UnicodeEncodeError:
            # any character GBK cannot hold would make the whole file unreadable,
            # so fall back to a plain-ASCII launcher rather than mojibake
            data = text.encode("ascii", "replace")
        with open(path, "wb") as f:
            f.write(data.replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n"))

    write_bat(os.path.join(dest, "启动.bat"), LAUNCHER)
    # an ASCII-named twin, for consoles that mangle CJK filenames
    write_bat(os.path.join(dest, "START.bat"), LAUNCHER.replace("启动", "START"))

    total = 0
    for dirpath, _, filenames in os.walk(dest):
        for fn in filenames:
            total += os.path.getsize(os.path.join(dirpath, fn))
    print("package: %.1f MB" % (total / 1024 / 1024))
    print("launcher:", os.path.join(dest, "START.bat"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
