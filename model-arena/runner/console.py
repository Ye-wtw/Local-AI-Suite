# -*- coding: utf-8 -*-
"""console.py - 让任何脚本在 GBK 控制台下都不会崩在 print 上。

为什么需要：
中文 Windows 控制台默认是 GBK（cp936），编不出 ✓ ✗ ⚠ → ——。项目里若干
进度/警告行用了这些符号，于是"打开即用"的第一个屏幕直接抛
UnicodeEncodeError，看起来像项目坏了。

实测（2026-11-10，随包 Python 3.12 + GBK 控制台）：
    runner/setup.py -> UnicodeEncodeError: 'gbk' codec can't encode '\u2713'

做法：在入口最早处调用 make_stdout_safe()，把 sys.stdout 换成
errors="replace" 的版本。这样符号会显示成 "?"，但**不会崩**，
信息主体（中文、路径、数字）完好。比逐处删符号可靠：新增的符号自动被兜住。
"""
import io
import sys


def make_stdout_safe(encoding=None):
    """Re-wrap stdout/stderr so unencodable characters degrade instead of raising.

    Returns True when a re-wrap happened. Safe to call twice; a second call is a
    no-op because sys.stdout is already an ErrorsSafeTextIOWrapper.
    """
    done = False
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        if getattr(stream, "_arena_safe", False):
            continue
        enc = encoding or getattr(stream, "encoding", None) or "utf-8"
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        try:
            wrapped = io.TextIOWrapper(buf, encoding=enc, errors="replace",
                                       line_buffering=True)
            setattr(wrapped, "_arena_safe", True)
            setattr(sys, name, wrapped)
            done = True
        except Exception:
            continue
    return done
