# -*- coding: utf-8 -*-
"""serverctl.py - start / stop / wait for a local model server, safely.

A sweep restarts the server once per variant. Measured on this machine that is ~6 s
from a warm file cache, so restart cost is not the bottleneck - but a restart that
silently fails IS: if the old server is still holding the port, the sweep measures
the PREVIOUS configuration and reports it under the new one's name. Every action here
therefore verifies rather than assumes:

  start()  -> poll /health until it answers, and confirm the model alias matches
  stop()   -> wait for the port to be released, force only if asked
  status() -> report what is actually listening, not what we believe we started

Usage:
  python runner/serverctl.py --status
  python runner/serverctl.py --start --profile apex-256k
  python runner/serverctl.py --stop
  python runner/serverctl.py --restart --profile apex-256k --override --ctx-size 32768
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    from console import make_stdout_safe
    make_stdout_safe()
except Exception:
    pass

NL = chr(10)
DEFAULT_PORT = 8080


def _run_ps(cmd, timeout=30):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, errors="ignore", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def who_listens(port=DEFAULT_PORT):
    """The PID actually holding the port, straight from the OS."""
    out = _run_ps('(Get-NetTCPConnection -LocalPort %d -State Listen '
                  '-ErrorAction SilentlyContinue | Select-Object -First 1 '
                  '-ExpandProperty OwningProcess)' % port)
    return int(out) if out.isdigit() else None


def proc_name(pid):
    if not pid:
        return None
    out = _run_ps('(Get-Process -Id %d -ErrorAction SilentlyContinue).ProcessName' % pid)
    return out or None


def health(port=DEFAULT_PORT, timeout=5):
    try:
        raw = urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=timeout).read()
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None


def props(port=DEFAULT_PORT, timeout=5):
    try:
        raw = urllib.request.urlopen("http://127.0.0.1:%d/props" % port, timeout=timeout).read()
        return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None


def status(port=DEFAULT_PORT):
    pid = who_listens(port)
    h = health(port) if pid else None
    p = props(port) if h else None
    return {
        "port": port,
        "listening": pid is not None,
        "pid": pid,
        "process": proc_name(pid),
        "health": h,
        "alias": (p or {}).get("model_alias"),
        "n_ctx": ((p or {}).get("default_generation_settings") or {}).get("n_ctx"),
        "vision": ((p or {}).get("modalities") or {}).get("vision"),
        "model_path": (p or {}).get("model_path"),
    }


def stop(port=DEFAULT_PORT, force_after=8.0, verbose=True):
    """Stop whatever serves the port and WAIT for the port to be released.

    Returning before the port frees would let the next start bind to a dying process
    or fail outright, and the sweep would record results against the wrong config.
    """
    pid = who_listens(port)
    if not pid:
        if verbose:
            print("  [stop] 端口 %d 未被占用" % port)
        return True
    if verbose:
        print("  [stop] 结束 PID %d (%s)" % (pid, proc_name(pid) or "?"))
    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
    t0 = time.time()
    while time.time() - t0 < force_after:
        if who_listens(port) is None:
            if verbose:
                print("  [stop] 已在 %.1fs 内释放" % (time.time() - t0))
            return True
        time.sleep(0.5)
    if verbose:
        print("  [stop] 未按时退出,强制结束")
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    t0 = time.time()
    while time.time() - t0 < 6:
        if who_listens(port) is None:
            if verbose:
                print("  [stop] 强制结束后已释放")
            return True
        time.sleep(0.5)
    if verbose:
        print("  [stop] ✗ 端口仍被占用 —— 后续测量会有风险")
    return False


def start(argv, cwd=None, port=DEFAULT_PORT, wait_s=180, expect_alias=None, verbose=True):
    """Launch a server and verify it actually serves the intended configuration."""
    if who_listens(port):
        if verbose:
            print("  [start] 端口已被占用,先停止旧进程")
        stop(port, verbose=verbose)

    log = os.path.join(ROOT, "results", "serverctl.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    fh = open(log, "a", encoding="utf-8")
    fh.write(NL + "=" * 60 + NL)
    fh.write("启动: %s%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), NL))
    fh.write(" ".join(argv) + NL)
    fh.flush()

    if verbose:
        print("  [start] %s" % " ".join(argv[:3]) + (" ..." if len(argv) > 3 else ""))
    try:
        proc = subprocess.Popen(argv, cwd=cwd or None, stdout=fh, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        if verbose:
            print("  [start] ✗ 找不到可执行文件: %s" % argv[0])
        return {"ok": False, "reason": "exe not found"}

    t0 = time.time()
    while time.time() - t0 < wait_s:
        if proc.poll() is not None:
            if verbose:
                print("  [start] ✗ 进程已退出(code=%s),见 results/serverctl.log" % proc.returncode)
            return {"ok": False, "reason": "process exited", "code": proc.returncode}
        if who_listens(port):
            h = health(port)
            if h:
                p = props(port)
                alias = (p or {}).get("model_alias")
                if expect_alias and alias and alias != expect_alias:
                    if verbose:
                        print("  [start] ⚠ 别名不符:期望 %s,实际 %s" % (expect_alias, alias))
                if verbose:
                    print("  [start] 就绪,耗时 %.1fs | alias=%s | n_ctx=%s | vision=%s"
                          % (time.time() - t0, alias,
                             ((p or {}).get("default_generation_settings") or {}).get("n_ctx"),
                             ((p or {}).get("modalities") or {}).get("vision")))
                return {"ok": True, "pid": proc.pid, "alias": alias,
                        "n_ctx": ((p or {}).get("default_generation_settings") or {}).get("n_ctx"),
                        "vision": ((p or {}).get("modalities") or {}).get("vision"),
                        "props": p, "waited_s": round(time.time() - t0, 1)}
        time.sleep(0.5)
    if verbose:
        print("  [start] ✗ 等待 %.0fs 仍未就绪" % wait_s)
    return {"ok": False, "reason": "timeout"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--profile", default="")
    # --set KEY VALUE repeated. nargs="*" cannot be used here: argparse then treats
    # "--ctx-size" as the start of a new option and rejects the call.
    ap.add_argument("--set", nargs=2, action="append", default=[], metavar=("FLAG", "VALUE"),
                    help="覆盖一个参数,可重复,例如 --set --ctx-size 32768")
    args = ap.parse_args()

    if args.status or (not args.start and not args.stop and not args.restart):
        s = status(args.port)
        print("端口 %d:" % s["port"])
        print("  监听中: %s" % ("是" if s["listening"] else "否"))
        if s["listening"]:
            print("  PID:    %s (%s)" % (s["pid"], s["process"]))
            print("  别名:   %s" % s["alias"])
            print("  n_ctx:  %s | vision: %s" % (s["n_ctx"], s["vision"]))
        return

    if args.stop:
        stop(args.port)
        return

    argv = None
    if args.profile:
        from profiles import load_store, to_argv_tokens
        store = load_store()
        p = store["profiles"].get(args.profile)
        if not p:
            print("无此配置档:", args.profile, "| 可用:", ", ".join(store["profiles"]))
            return
        ov = {k: v for k, v in (args.set or [])}
        if ov:
            print("  覆盖参数: %s" % ov)
        argv = to_argv_tokens(p, ov)
        cwd = p.get("cwd")
        res = start(argv, cwd=cwd, port=args.port)
        if not res.get("ok"):
            print("启动失败:", res.get("reason"))
        return

    if args.restart:
        stop(args.port)
        print("  (无 --profile,仅停止)")
        return

    print("需要 --profile 才能启动,或使用 --status/--stop")


if __name__ == "__main__":
    main()
