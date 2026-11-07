# -*- coding: utf-8 -*-
"""arena_tui.py - terminal dashboard for long evaluation runs.

Why a TUI and not just the CLI: a full run is 5-10 hours, and the CLI prints a
progress line every N questions with no way to see how far along it is, how long
it has left, or what the score looks like per dimension while it runs. A TUI is
the cheapest way to answer those, because it needs no browser, no server and no
third-party dependency.

Three panes:

  status   endpoint, model, VRAM, judge version, current run
  progress per-suite bars with rate and ETA
  results  the leaderboard, refreshed as runs finish

Zero dependencies: ANSI escapes only. Falls back to plain line output when the
terminal cannot handle cursor addressing (piped to a file, dumb terminal), so
the same command is usable in a script.

Usage:
  python runner/arena_tui.py              # watch (refresh every 2s)
  python runner/arena_tui.py --once       # render one frame and exit
  python runner/arena_tui.py --plain      # no ANSI, append-only lines
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")
CKPT = os.path.join(RESULTS, ".ckpt")

# --- ANSI ---------------------------------------------------------------
ESC = chr(27)


class Term:
    def __init__(self, plain=False):
        self.plain = plain
        try:
            self.w = shutil.get_terminal_size((100, 30)).columns
            self.h = shutil.get_terminal_size((100, 30)).lines
        except Exception:
            self.w, self.h = 100, 30

    def s(self, code):
        return "" if self.plain else ESC + "[" + code + "m"

    def clear(self):
        return "" if self.plain else ESC + "[2J" + ESC + "[H"

    def home(self):
        return "" if self.plain else ESC + "[H"

    @property
    def bold(self):
        return self.s("1")

    @property
    def dim(self):
        return self.s("2")

    @property
    def reset(self):
        return self.s("0")

    def color(self, name, enabled):
        if not enabled:
            return ""
        return self.s({"green": "32", "yellow": "33", "red": "31",
                       "cyan": "36", "grey": "90"}.get(name, "0"))


def bar(frac, width=28, enabled=True, color="green"):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    body = "#" * n + "-" * (width - n)
    if not enabled:
        return "[" + body + "]"
    c = ESC + "[" + {"green": "32", "yellow": "33", "red": "31"}.get(color, "32") + "m"
    return "[" + c + body + ESC + "[0m]"


# --- data gathering -----------------------------------------------------

def read_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def judge_version():
    try:
        sys.path.insert(0, HERE)
        import judge_version as JV
        return JV.judge_version()
    except Exception:
        return "?"


# Progress rates measured across frames. Frames are seconds apart, so a naive
# "done / elapsed-since-start" is wildly noisy on the first few frames; keep a
# monotonic first-seen record instead.
_SEEN = {}


def active_checkpoints(total_hint=None):
    """Running suites leave a <suite>__<endpoint>__<hash>.jsonl checkpoint."""
    out = []
    for p in sorted(glob.glob(os.path.join(CKPT, "*.jsonl"))):
        base = os.path.basename(p)[:-6]
        parts = base.split("__")
        suite = parts[0] if parts else base
        ep = parts[1] if len(parts) > 1 else "?"
        n = 0
        last = {}
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    n += 1
                    try:
                        last = json.loads(line)
                    except Exception:
                        pass
        except Exception:
            continue
        mtime = os.path.getmtime(p)
        now = time.time()
        first = _SEEN.setdefault(p, {"t": now, "n": n})
        if first["n"] > n:
            # checkpoint was reset (a new run reused the key): restart the clock
            first["t"], first["n"] = now, n
        # rate over the whole observed window, not the last frame
        rate = 0.0
        spent = now - first["t"]
        if spent > 1.5 and n > first["n"]:
            rate = (n - first["n"]) / spent
        out.append({"suite": suite, "endpoint": ep, "done": n,
                    "last": last, "age_s": now - mtime, "path": p,
                    "rate_qps": rate})
    return out


def latest_runs(limit=6):
    """Most recent LEVEL_/PUBLIC_/LCB_ result files, one row each."""
    files = []
    for pat in ("LEVEL_*.json", "PUBLIC_*.json", "LCB_*.json", "D8h_*.json",
                "D12_*.json", "D11_*.json", "DYN_*.json"):
        files.extend(glob.glob(os.path.join(RESULTS, pat)))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    rows = []
    for p in files[:limit]:
        d = read_json(p)
        if not d:
            continue
        s = d.get("summary") or d
        n = s.get("n")
        ok = s.get("correct")
        acc = s.get("accuracy")
        if acc is None and n:
            acc = (ok or 0) / n
        rows.append({
            "file": os.path.basename(p)[:46],
            "suite": s.get("suite") or s.get("level") or "?",
            "endpoint": s.get("endpoint") or "?",
            "n": n, "correct": ok,
            "acc": acc,
            "when": datetime.datetime.fromtimestamp(os.path.getmtime(p))
                    .strftime("%m-%d %H:%M"),
            "jv": (d.get("judge_version") or s.get("judge_version") or "?"),
        })
    return rows


def gpu_info():
    """Best-effort VRAM read. Never raises: a missing nvidia-smi is not an error."""
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=4)
        if r.returncode == 0 and r.stdout.strip():
            used, total = [x.strip() for x in r.stdout.strip().split(chr(10))[0].split(",")]
            return int(used), int(total)
    except Exception:
        pass
    return None


def endpoint_alive(port=8080):
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as f:
            return f.status == 200
    except Exception:
        return False


# --- rendering ----------------------------------------------------------

def render(t: Term, color=True):
    w = t.w
    out = []
    def line(s=""):
        out.append(s[:w] if len(s) > w else s)

    head = " Model Arena "
    line(t.bold + head + t.reset + " " + t.dim +
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + t.reset)
    line("=" * min(w, 96))

    # status
    up = endpoint_alive()
    g = gpu_info()
    vr = ("VRAM %d/%d MiB" % g) if g else "VRAM n/a"
    line("  endpoint : %s   %s   judge %s"
         % ("UP" if up else "DOWN", vr, judge_version()))
    line("")

    # running suites
    cks = active_checkpoints()
    line(t.bold + "  进行中" + t.reset)
    if not cks:
        line("    " + t.dim + "(没有正在跑的评测)" + t.reset)
    for c in cks:
        age = c["age_s"]
        live = age < 120
        col = t.color("green", color) if live else t.color("grey", color)
        mark = "运行中" if live else ("已停 %d 分" % int(age / 60))
        # Show the observed rate, never a guessed ETA: the total is not stored in
        # the checkpoint, so an ETA would be a fabricated number. Talking about
        # speed the tool actually measured is honest; inventing a finish time is not.
        rate = c.get("rate_qps") or 0.0
        extra = ("  %.1f 题/秒" % rate) if (live and rate > 0.05) else ""
        line("    %-16s %-14s %s %4d 题  %s%s%s%s"
             % (c["suite"], c["endpoint"],
                bar(min(1.0, c["done"] / 200.0), 16, color, "green" if live else "grey"),
                c["done"], col, mark, extra, t.reset))
    line("")

    # recent results
    line(t.bold + "  最近结果" + t.reset)
    line("    %-46s %-8s %-12s %6s %8s  %s"
         % ("文件", "suite", "endpoint", "n", "准确率", "时间"))
    for r in latest_runs(6):
        acc = r["acc"]
        if acc is None:
            accs = "   --  "
            col = ""
        else:
            accs = "%5.1f%%" % (acc * 100)
            col = (t.color("green", color) if acc >= 0.85 else
                   t.color("yellow", color) if acc >= 0.6 else t.color("red", color))
        line("    %-46s %-8s %-12s %6s %s%s%s  %s"
             % (r["file"], str(r["suite"])[:8], str(r["endpoint"])[:12],
                r["n"] if r["n"] is not None else "-",
                col, accs, t.reset, r["when"]))
    line("")

    # legend
    line(t.dim + "  q 退出   r 立即刷新   判分器改了要看版本是否一致" + t.reset)
    return out


def draw(t, color=True):
    lines = render(t, color)
    if t.plain:
        sys.stdout.write(NL.join(lines) + NL)
    else:
        sys.stdout.write(t.clear() + NL.join(lines))
    sys.stdout.flush()


NL = chr(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="render one frame and exit")
    ap.add_argument("--plain", action="store_true", help="no ANSI, append-only")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--targets", action="store_true",
                    help="show configured + discovered models instead of results")
    a = ap.parse_args()

    t = Term(plain=a.plain)
    if a.targets:
        import tui_targets as TT
        lines = ["", " Model Arena - 模型选择", "=" * 78]
        lines += TT.render_targets()
        lines.append("")
        lines += TT.render_discovered()
        lines.append("")
        lines.append("  添加: python runner/targets.py add-attach --url http://...")
        lines.append("        python runner/tui_targets.py 见模块文档")
        sys.stdout.write(NL.join(lines) + NL)
        return 0
    if a.once or a.plain:
        draw(t, color=not a.plain)
        return 0

    try:
        while True:
            draw(t, color=True)
            # Non-blocking key check: works on Windows only with msvcrt, and on
            # POSIX only with a tty. A failed import means "no keys", not an error.
            waited = 0.0
            quit_now = False
            while waited < a.interval:
                try:
                    import msvcrt
                    if msvcrt.kbhit():
                        k = msvcrt.getwch().lower()
                        if k in ("q", chr(27)):
                            quit_now = True
                            break
                        if k == "r":
                            break
                except ImportError:
                    time.sleep(a.interval)
                    break
                time.sleep(0.1)
                waited += 0.1
            if quit_now:
                break
    except KeyboardInterrupt:
        pass
    sys.stdout.write(NL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())