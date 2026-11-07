# -*- coding: utf-8 -*-
"""arena_desktop.py - the desktop entry point: one process that serves the UI.

Two forms of the same product, sharing one codebase:

    arena_desktop.py --gui      open a native window (pywebview if available,
                                otherwise the default browser)
    arena_desktop.py --serve    headless: just run the kernel API on a port
                                (this is what the DSH plugin talks to)

Why one process instead of "UI fetches a separate API": the API here is read-only
over files on disk, so running it in-process costs nothing and removes a moving
part. The evaluation itself is NEVER run in this process - that stays in
`runner/run.py` so a measurement cannot be contaminated by the UI's own CPU use.

Usage:
    python runner/arena_desktop.py --gui
    python runner/arena_desktop.py --serve --port 8770
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
DEFAULT_PORT = 8770


def build_ui_if_needed(port):
    """Regenerate the API-mode UI so it points at this process's port."""
    ui = os.path.join(ROOT, "ui-preview.html")
    try:
        import make_ui
        # make_ui writes the api URL from a fixed constant; regenerate with the
        # runtime port so a non-default --port still works
        src = open(os.path.join(HERE, "make_ui.py"), encoding="utf-8").read()
        src = src.replace('"http://127.0.0.1:8770"', '"http://127.0.0.1:%d"' % port)
        ns = {}
        exec(compile(src, "make_ui", "exec"), ns)
        ns["build"]("api")
    except Exception as e:
        if not os.path.exists(ui):
            print("  [ui] 生成失败:", str(e)[:80])
    return ui


def start_api(port, verbose=False):
    """Run the kernel API in a background thread. Returns the server object."""
    import arena_api
    from http.server import HTTPServer

    srv = HTTPServer(("127.0.0.1", port), arena_api.Handler)
    srv.verbose = verbose
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def wait_ready(port, timeout=8):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    ap = argparse.ArgumentParser(description="Model Arena 桌面端")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--gui", action="store_true", help="打开窗口(默认)")
    ap.add_argument("--serve", action="store_true", help="只跑内核 API,不开窗口")
    ap.add_argument("--browser", action="store_true", help="强制用浏览器而非原生窗口")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("=" * 58)
    print("  Model Arena")
    print("=" * 58)

    # refuse to double-bind rather than silently serving a stale UI
    import socket
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", args.port))
        probe.close()
    except OSError:
        print("  端口 %d 已被占用。" % args.port)
        print("  若已有实例在跑,直接打开 http://127.0.0.1:%d/ui" % args.port)
        print("  否则用 --port 换一个端口。")
        return 2

    ui = build_ui_if_needed(args.port)
    srv = start_api(args.port, verbose=args.verbose)
    if not wait_ready(args.port):
        print("  内核 API 启动失败")
        return 3

    url = "http://127.0.0.1:%d/ui" % args.port
    runs = 0
    try:
        import glob
        runs = len(glob.glob(os.path.join(ROOT, "results", "LEVEL_*.json")))
    except Exception:
        pass
    print("  内核 API: http://127.0.0.1:%d" % args.port)
    print("  界面:     %s" % url)
    print("  结果库:   %d 次运行" % runs)

    if args.serve:
        print(NL + "  仅服务模式,按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(NL + "  已停止")
        return 0

    # native window when possible, browser otherwise
    if not args.browser:
        try:
            import webview
            print(NL + "  打开原生窗口(pywebview)…")
            webview.create_window("Model Arena", url, width=1500, height=950)
            webview.start()
            srv.shutdown()
            return 0
        except ImportError:
            print(NL + "  pywebview 未安装,改用浏览器")
        except Exception as e:
            print(NL + "  窗口打开失败(%s),改用浏览器" % str(e)[:60])

    print(NL + "  在浏览器中打开…")
    webbrowser.open(url)
    print("  按 Ctrl+C 退出服务")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(NL + "  已停止")
    finally:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
