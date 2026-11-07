# -*- coding: utf-8 -*-
"""arena_api.py - read-only HTTP API over the results directory (the "kernel" side).

Why a separate process: the product goal is a measurement that is as free of
interference as possible, INCLUDING interference from the project itself. A UI that
loads 7000 lines of Python into the same process as the evaluator would compete for
CPU with a --cpu-moe server. So the evaluator stays independent and the UI talks to
it over localhost HTTP, read-only.

Endpoints (all GET, all JSON):
  /api/health            liveness + which result dir it is serving
  /api/runs              every result file, summarised (model, level, accuracy, trust)
  /api/run/<name>        one run in full
  /api/leaderboard       the aggregated leaderboard.json payload
  /api/paired            per-question correctness maps, for the compare view
  /api/launch/<name>     the launch-params snapshot of one run
  /api/environment       CURRENT machine interference report
  /api/datasets          question counts per dimension
  /api/reports/<file>    a markdown report from reports/

Usage: python runner/arena_api.py [--port 8770] [--host 127.0.0.1]
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)
RESULTS = os.path.join(ROOT, "results")
REPORTS = os.path.join(ROOT, "reports")


def _json_default(o):
    return str(o)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summarize_run(path):
    """Small listing shape: never sends whole record arrays to the UI."""
    try:
        d = read_json(path)
    except Exception as e:
        return {"file": os.path.basename(path), "error": str(e)[:120]}
    s = d.get("summary") or d.get("level") or {}
    env = s.get("environment") or {}
    launch = (s.get("launch") or {}).get("processes") or [{}]
    return {
        "file": os.path.basename(path),
        "mtime": datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
        # 预设身份:队列跑出来的结果带这个字段。老结果没有,显示为空,
        # 但报告层会退回按 endpoint/model 归并 —— 有更强的身份就用更强的。
        "preset": d.get("preset"),
        "judge_version": d.get("judge_version"),
        "model": s.get("model"),
        "endpoint": s.get("endpoint"),
        "level": s.get("level"),
        "n": s.get("n"),
        "correct": s.get("correct"),
        "accuracy": s.get("accuracy"),
        "n_errors": s.get("n_errors"),
        "elapsed_s": s.get("elapsed_s"),
        "trust_level": env.get("trust_level"),
        "verified": launch[0].get("raw") is not None,
        "derived": launch[0].get("derived") or {},
        "by_dim": s.get("by_dim") or {},
        "by_sub": s.get("by_sub") or {},
    }


def list_runs():
    out = []
    for pat in ("LEVEL_*.json", "D*_*.json", "run_*.json"):
        for p in glob.glob(os.path.join(RESULTS, pat)):
            out.append(summarize_run(p))
    out.sort(key=lambda r: r.get("mtime") or "", reverse=True)
    return out


def dataset_counts():
    counts = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "datasets", "full", "D*_full.jsonl"))):
        dim = os.path.basename(p).split("_")[0]
        n = 0
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
        counts[dim] = n
    counts["_total"] = sum(v for k, v in counts.items() if k.startswith("D"))
    return counts


# --- control plane -------------------------------------------------------
# One global handle: this process supervises at most one evaluation at a time.
# Running two would halve each one's throughput and make the tok/s numbers
# meaningless, so the second request is refused rather than queued.
_RUN = {"proc": None, "suite": None, "endpoint": None, "started": None, "log": None}


def _start_run(body):
    if _RUN["proc"] is not None and _RUN["proc"].poll() is None:
        return {"ok": False, "error": "a run is already in progress",
                "suite": _RUN["suite"], "endpoint": _RUN["endpoint"]}
    suite = str(body.get("suite") or "canary")
    endpoint = str(body.get("endpoint") or "local")
    limit = body.get("limit")
    tag = str(body.get("tag") or "api")

    # The API runs run.py as a CHILD process, so it can never answer the
    # interactive cost prompt. Passing --yes here is not skipping the guard: the
    # UI already showed the suite and the operator pressed start on it. Without
    # this flag every cloud run from the web UI died with exit 5 before measuring
    # anything, which looked like a broken endpoint rather than a missing flag.
    cmd = [sys.executable, os.path.join(HERE, "run.py"),
           "--endpoint", endpoint, "--suite", suite, "--tag", tag, "--yes"]
    if limit:
        cmd += ["--limit", str(int(limit))]
    if endpoint != "local":
        cmd.append("--skip-vram-check")

    import subprocess
    log_dir = os.path.join(RESULTS, ".api")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "run_%s.log" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    _RUN.update({"proc": proc, "suite": suite, "endpoint": endpoint,
                 "started": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "log": log_path})
    return {"ok": True, "suite": suite, "endpoint": endpoint, "pid": proc.pid,
            "started": _RUN["started"], "log": os.path.relpath(log_path, ROOT)}


def _stop_run(body):
    proc = _RUN.get("proc")
    if proc is None or proc.poll() is not None:
        return {"ok": False, "error": "nothing is running"}
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    return {"ok": True, "stopped": True}


def _progress(body):
    """Live state for the UI: process alive, per-suite counts, last question."""
    proc = _RUN.get("proc")
    alive = proc is not None and proc.poll() is None
    out = {"running": alive, "suite": _RUN.get("suite"),
           "endpoint": _RUN.get("endpoint"), "started": _RUN.get("started"),
           "exit_code": (proc.poll() if proc is not None else None),
           "suites": [], "judge_version": None}
    try:
        import judge_version as JV
        out["judge_version"] = JV.judge_version()
    except Exception:
        pass
    ck = os.path.join(RESULTS, ".ckpt")
    if os.path.isdir(ck):
        for p in sorted(glob.glob(os.path.join(ck, "*.jsonl"))):
            base = os.path.basename(p)[:-6]
            parts = base.split("__")
            n, last = 0, None
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            n += 1
                            try:
                                last = json.loads(line)
                            except Exception:
                                pass
            except Exception:
                continue
            ok = None
            if last is not None:
                ok = bool(last.get("correct"))
            out["suites"].append({
                "suite": parts[0] if parts else base,
                "endpoint": parts[1] if len(parts) > 1 else "?",
                "done": n,
                "last_id": (last or {}).get("id"),
                "last_correct": ok,
                "age_s": round(time.time() - os.path.getmtime(p), 1),
            })
    return out


# --- batch queue -----------------------------------------------------------
# The queue runs ONE batch at a time, in a child process. Same reason as the
# single-run control plane: the web layer must not compete with the measured
# engine for CPU, and a queue that lives in this process would die with a
# restart in the middle of a six-hour batch.
_Q = {"proc": None, "run_id": None, "presets": None, "suites": None,
      "started": None, "log": None}


def _queue_start(body):
    if _Q["proc"] is not None and _Q["proc"].poll() is None:
        return {"ok": False, "error": "a batch is already running",
                "run_id": _Q["run_id"]}
    presets = [str(x) for x in (body.get("presets") or []) if str(x).strip()]
    suites = [str(x) for x in (body.get("suites") or []) if str(x).strip()]
    if not presets:
        return {"ok": False, "error": "需要至少一套预设"}
    if not suites:
        return {"ok": False, "error": "需要至少一个套件"}

    # 顺序就是用户排的顺序:先跑哪个是有意义的(显存最紧的排前面,失败时省时间)。
    # 所以这里不做任何重排,原样传给子进程。
    cmd = [sys.executable, os.path.join(HERE, "batch_queue.py"),
           "--presets", ",".join(presets),
           "--suites", ",".join(suites),
           "--tag", str(body.get("tag") or "ui")]
    if body.get("limit"):
        cmd += ["--limit", str(int(body["limit"]))]
    if body.get("wait_s"):
        cmd += ["--wait-s", str(int(body["wait_s"]))]
    if body.get("resume"):
        cmd += ["--resume", str(body["resume"])]

    log_dir = os.path.join(RESULTS, ".queue")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "batch_%s.log"
                            % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    fh = open(log_path, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    except Exception as e:
        fh.close()
        return {"ok": False, "error": "启动失败: %s" % str(e)[:140]}
    _Q.update({"proc": proc, "run_id": None, "presets": presets,
               "suites": suites, "log": log_path,
               "started": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return {"ok": True, "pid": proc.pid, "presets": presets, "suites": suites,
            "started": _Q["started"], "log": os.path.relpath(log_path, ROOT)}


def _queue_stop(body):
    proc = _Q.get("proc")
    if proc is None or proc.poll() is not None:
        return {"ok": False, "error": "没有批量任务在跑"}
    # Terminate the child; it writes state after every item, so a kill loses at
    # most the item in flight - not the whole night's work.
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    return {"ok": True, "stopped": True}


def _suite_progress():
    """每个套件"跑过多少题、花了多少秒/题、多少分"。给队列 UI 用。

    为什么需要:用户会重复跑同一套件(实测踩过整批重跑),也会对工时估错 ——
    552 题的 D2 和 80 题的 D4 单题耗时差 14 倍,任何统一估算都会差一个数量级。
    这里直接读历史结果里的真实 n 与 elapsed_s,让 UI 显示实测而不是常数。
    """
    import glob
    SUITE_DIM = {"smoke": "L0", "canary": "L1", "mcq": "D2", "math": "D3", "code": "D4",
                 "instruction": "D5", "agent": "D6", "longctx": "D7", "reliability": "D8",
                 "realworld": "D9", "chess": "D10", "lcb": "lcb", "public": "public"}
    # 同一套件可能有多份结果(补跑/重算),取"题数最多"的那次作为能力基线
    best = {}
    for p in glob.glob(os.path.join(RESULTS, "*.json")):
        name = os.path.basename(p)
        if not (name.startswith("LEVEL_") or name.startswith("D")):
            continue
        if "refill" in name or "retry" in name:      # 补跑/重做不是独立能力指标
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        s = d.get("summary") or d.get("level") or {}
        n = s.get("n_valid") or s.get("n")
        el = s.get("elapsed_s")
        if not n or not el or n < 10:
            continue
        dim = str(s.get("dim") or "").strip()
        suite = None
        for k, v in SUITE_DIM.items():
            if dim and dim == v:
                suite = k; break
        if not suite:
            lv = str(s.get("level") or "").lower()
            for k in SUITE_DIM:
                if k in lv:
                    suite = k; break
        if not suite:
            continue
        acc = round((s.get("accuracy") or 0) * 100, 1)
        cur = best.get(suite)
        if cur is None or n >= cur["n"]:
            best[suite] = {"n": n, "acc": acc, "sec_per_q": round(el / n, 2)}
    return best


def _resume_state():
    """断点续跑的可见状态:每个检查点跑了多少、属于哪套件。

    为什么需要:续跑能力后端一直有,但 UI 里完全看不见,用户无法判断
    "这个套件是跑完了还是中途断了"。检查点文件是唯一的实时进度来源
    (日志是块缓冲,不可靠 —— 曾因此误判卡死而停掉正常任务)。
    """
    import glob
    out = []
    ckdir = os.path.join(RESULTS, ".ckpt")
    for p in glob.glob(os.path.join(ckdir, "*.jsonl")):
        key = os.path.basename(p)[:-6]
        parts = key.split("__")
        suite = parts[0] if parts else "?"
        n = 0
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        n += 1
        except Exception:
            continue
        # 只有 1-2 题的检查点是"验证一次能不能跑"留下的,不是真正中断的任务。
        # 显示它会让用户以为有个跑了 1 题的任务等着续 —— 实测踩过。
        if n < 3:
            continue
        out.append({"suite": suite, "key": key, "done": n,
                    "path": os.path.relpath(p, ROOT),
                    "mtime": datetime.datetime.fromtimestamp(
                        os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _retry_state():
    """错题重做(caveat 必须是给人看的那种)。"""
    import glob
    out = []
    for p in glob.glob(os.path.join(RESULTS, "*retry*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        s = d.get("summary") or {}
        out.append({"file": os.path.basename(p), "dim": s.get("dim"),
                    "n": s.get("n_wrong"), "recovered": s.get("recovered"),
                    "recover_rate": round((s.get("recover_rate") or 0) * 100, 1),
                    "caveat": d.get("caveat")})
    return out


def _perf_section():
    """每维度耗时/硬件占用:只呈现,不计入指数。"""
    try:
        import perf as P
        got = P.collect(os.path.join(ROOT, "results"))
    except Exception as e:
        return {"ok": False, "error": str(e)[:120], "rows": []}
    rows = []
    for (preset, suite), v in sorted(got.items()):
        L = v.get("latency") or {}
        h = v.get("hardware") or {}
        rows.append({"preset": preset, "suite": suite,
                     # 没有 preset 字段的老结果只有 endpoint/model,
                     # 归并出来的"预设名"其实是 model 名。UI 必须能区分,
                     # 否则同一模型的两批数据看起来像两个不同型号。
                     # 注意:has_preset 为 True 表示**有** preset 字段(是新结果)。
                     # 直接传 has_preset,别在 API 层做 not 反转 ——
                     # 之前写成 "legacy": not has_preset,UI 又把 legacy 当"是否历史",
                     # 两处反转叠在一起,新结果被显示成历史结果。
                     "has_preset": bool(v.get("has_preset")),
                     "cloud": bool(v.get("cloud")),
                     "n": L.get("n"),
                     "median_s": L.get("median_s"), "p95_s": L.get("p95_s"),
                     "max_s": L.get("max_s"), "per_q_s": L.get("per_q_s"),
                     "gpu_mem_peak_mib": h.get("gpu_mem_peak_mib"),
                     "gpu_util_mean_pct": h.get("gpu_util_mean_pct"),
                     "gpu_temp_peak_c": h.get("gpu_temp_peak_c"),
                     "gpu_power_mean_w": h.get("gpu_power_mean_w"),
                     "tps": (v.get("speed") or {}).get("p2_tps"),
                     "ttft_ms": (v.get("speed") or {}).get("ttft_ms")})
    return {"ok": True, "rows": rows,
            "note": "耗时不计入指数;tok/s 未记录 token 数,不估算"}


def _queue_status(body):
    import batch_queue as BQ
    proc = _Q.get("proc")
    alive = proc is not None and proc.poll() is None
    states = BQ.list_states()
    newest = states[-1] if states else None
    out = {"ok": True, "running": alive,
           "exit_code": (proc.poll() if proc is not None else None),
           "started": _Q.get("started"),
           "presets": _Q.get("presets"), "suites": _Q.get("suites"),
           "log": os.path.relpath(_Q["log"], ROOT) if _Q.get("log") else None,
           "queue": _trim_state(newest) if newest else None,
           "history": [_trim_state(s) for s in states[-8:]]}
    # 套件进度:UI 用它显示"已有 N 题 · X%"和按实测速率算的工时
    out["suite_progress"] = _suite_progress()
    out["resume"] = _resume_state()
    out["retry"] = _retry_state()
    out["perf"] = _perf_section()
    return out


def _latest_score(preset, suite, since_stamp=None):
    """找某套预设某套件最近一次结果的正确率。

    队列进度表原来只有机器指标、没有分数 —— 跑完 15 分钟看不到"对了多少题",
    等于白等。结果文件里已经有这个数(和 preset 字段),读出来即可,不重算。
    """
    import glob
    best = None
    for p in glob.glob(os.path.join(RESULTS, "*.json")):
        name = os.path.basename(p)
        if not (name.startswith("LEVEL_") or name.startswith("D")):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("preset") != preset:
            continue
        s = d.get("summary") or d.get("level") or {}
        if suite and suite not in str(s.get("level") or "") and suite not in name:
            continue
        if not s.get("n"):
            continue
        mtime = os.path.getmtime(p)
        if best is None or mtime > best["mtime"]:
            best = {"mtime": mtime, "file": name, "n": s.get("n"),
                    "correct": s.get("correct"), "accuracy": s.get("accuracy"),
                    "n_errors": s.get("n_errors"),
                    "judge_version": d.get("judge_version")}
    return best


def _build_report(body):
    """跑 batch_report.py,给界面上的「出报告」按钮用。

    报告生成只读结果文件、不跑评测,所以放在本进程的子进程里也没关系;
    但仍然用子进程:它可能扫几百个 JSON,不该让 HTTP 请求等在那里。
    """
    out = str(body.get("out") or "results/batch_report.md")
    max_lines = int(body.get("max_lines") or 600)
    # 只允许写到 results/ 下面,防止把报告写到任意路径
    safe = out.replace("\\", "/")
    if not safe.startswith("results/"):
        safe = "results/" + os.path.basename(safe)
    cmd = [sys.executable, os.path.join(HERE, "batch_report.py"),
           "--out", safe, "--max-lines", str(max_lines)]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           errors="ignore", timeout=300)
    except Exception as e:
        return {"ok": False, "error": "生成失败: %s" % str(e)[:140]}
    tail = (r.stdout or "").strip().splitlines()[-6:]
    return {"ok": r.returncode == 0, "path": safe,
            "exit": r.returncode,
            "url": "/api/reports/" + os.path.basename(safe),
            "log": tail,
            "error": None if r.returncode == 0 else (r.stderr or "")[:200]}


def _trim_state(st):
    """Queue state, minus per-sample arrays: a 4-hour run's sampler data is
    megabytes, and the UI only draws the summary."""
    if not st:
        return None
    items = []
    for it in st.get("items") or []:
        perf = it.get("performance") or {}
        items.append({
            "preset": it.get("preset"), "suite": it.get("suite"),
            "status": it.get("status"), "error": it.get("error"),
            "launch_seconds": it.get("launch_seconds"),
            "run_seconds": it.get("run_seconds"),
            "n_samples": perf.get("n_samples"),
            "gpu_mem_max": ((perf.get("stats") or {}).get("gpu_mem_used_mib") or {}).get("max"),
            "gpu_util_mean": ((perf.get("stats") or {}).get("gpu_util_pct") or {}).get("mean"),
            "proc_rss_max": ((perf.get("stats") or {}).get("proc_rss_mib") or {}).get("max"),
            "gpu_temp_max": ((perf.get("stats") or {}).get("gpu_temp_c") or {}).get("max"),
            "gpu_power_mean": ((perf.get("stats") or {}).get("gpu_power_w") or {}).get("mean"),
            "steps": [s.get("step") for s in it.get("steps") or []],
            "score": _latest_score(it.get("preset"), it.get("suite")),
        })
    return {"run_id": st.get("run_id"), "created": st.get("created"),
            "updated": st.get("updated"), "finished": st.get("finished"),
            "presets": st.get("presets"), "suites": st.get("suites"),
            "items": items}


class Handler(BaseHTTPRequestHandler):
    server_version = "arena-core/1.0"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("  %s" % (fmt % args) + NL)

    def _send(self, obj, code=200, ctype="application/json; charset=utf-8"):
        if isinstance(obj, (dict, list)):
            body = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
        else:
            body = (obj or "").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Allow the UI to be opened straight from disk (file://) as well as served
        # here. Without this the browser blocks the fetch and the page looks blank.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _safe(self, rel):
        """Block path traversal: only files directly inside results/ or reports/."""
        rel = unquote(rel or "").replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            return None
        return rel

    # ------------------------------------------------------------------
    # Control plane: POST only. Three endpoints, no more.
    #
    # The evaluation itself is NEVER run in this process. It is spawned as a
    # child so the API stays responsive and, more importantly, so nothing in the
    # web layer competes with the measured server for CPU.
    # ------------------------------------------------------------------
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            return self._send({"error": "bad json: %s" % str(e)[:80]}, 400)

        try:
            if path == "/api/start":
                return self._send(_start_run(body))
            if path == "/api/stop":
                return self._send(_stop_run(body))
            if path == "/api/progress":
                return self._send(_progress(body))
            if path == "/api/presets/scan":
                # Explicit rescan: the catalog caches the running set for a few
                # seconds, and after starting a server the user should not have to
                # wait for the cache to expire to see it.
                import presets as PS
                return self._send(PS.catalog(force_scan=True))
            if path == "/api/queue/start":
                return self._send(_queue_start(body))
            if path == "/api/queue/stop":
                return self._send(_queue_stop(body))
            if path == "/api/queue/status":
                return self._send(_queue_status(body))
            if path == "/api/report":
                return self._send(_build_report(body))
            if path in ("/api/targets", "/api/discover", "/api/launch", "/api/attach"):
                return self._targets_routes(path, dict(body, _method="POST"))
            return self._send({"error": "no such endpoint", "path": path}, 404)
        except Exception as e:
            return self._send({"error": str(e)[:160]}, 500)

    # ------------------------------------------------------------------
    # Model targets: what am I measuring, and how is it reachable.
    #
    # GET  /api/targets    list configured targets (attach and launch)
    # POST /api/targets    add or replace one target
    # POST /api/discover   probe local ports + list local gguf files
    # POST /api/launch     start the engine for a launch-kind target
    # POST /api/attach     probe a url and save it as an attach target
    # ------------------------------------------------------------------
    def _targets_routes(self, path, body):
        import targets as T
        if path == "/api/targets" and body.get("_method") == "POST":
            try:
                tgt = dict(body["target"])
                # A key typed in the UI stays in this process. It is deliberately
                # stripped before the record reaches targets.json, so the file
                # only names the env var, never the secret.
                typed_key = tgt.pop("api_key", None)
                saved = T.upsert(tgt)
                if typed_key:
                    T.set_runtime_key(saved["id"], typed_key)
                return self._send({"ok": True, "target": saved,
                                   "key_in_memory": bool(typed_key)})
            except Exception as e:
                return self._send({"ok": False, "error": str(e)[:120]}, 400)
        if path == "/api/discover":
            return self._send({
                "ok": True,
                "ports": T.scan_ports(body.get("ports")),
                "ggufs": T.find_ggufs(body.get("roots"))[:40],
            })
        if path == "/api/launch":
            import launcher as LZ
            tid = body.get("target") or body.get("id")
            t = T.get_target(tid) if tid else None
            model = body.get("model") or (t or {}).get("model_path")
            if not model:
                return self._send({"ok": False, "error": "need model_path"}, 400)
            try:
                r = LZ.start(
                    model,
                    port=int(body.get("port") or (t or {}).get("port") or 8080),
                    ctx=int(body.get("ctx") or (t or {}).get("ctx") or 32768),
                    ngl=int(body.get("ngl") or (t or {}).get("ngl") or 99),
                    cpu_moe=body.get("cpu_moe") or (t or {}).get("cpu_moe"),
                    kv_type=body.get("kv_type") or (t or {}).get("kv_type") or "q4_0",
                    mmproj=body.get("mmproj") or (t or {}).get("mmproj"),
                )
            except Exception as e:
                return self._send({"ok": False, "error": str(e)[:160]}, 400)
            return self._send(r)
        if path == "/api/attach":
            url = body.get("base_url") or "http://127.0.0.1:8080"
            p = T.probe(url)
            tid = body.get("id") or ("port" + url.rsplit(":", 1)[-1])
            if not p.get("reachable"):
                return self._send({"ok": False, "error": "not reachable: %s"
                                   % (p.get("error") or ""), "probe": p}, 400)
            t = T.upsert({"id": tid, "kind": "attach",
                          "label": body.get("label") or tid,
                          "base_url": url,
                          "model": body.get("model") or (p["models"][0] if p["models"] else tid),
                          "engine": p.get("engine_guess") or "openai",
                          "timeout": 600})
            return self._send({"ok": True, "target": t, "probe": p})
        return self._send({"error": "no such endpoint", "path": path}, 404)

    def do_DELETE(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        try:
            if path.startswith("/api/target/"):
                import targets as T
                tid = path[len("/api/target/"):]
                n = T.remove(tid)
                T.set_runtime_key(tid, None)
                return self._send({"ok": bool(n), "removed": n})
            return self._send({"error": "no such endpoint", "path": path}, 404)
        except Exception as e:
            return self._send({"error": str(e)[:120]}, 500)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        try:
            if path in ("/ui", "/ui.html", "/index.html"):
                # Two pages, two purposes:
                #   ui.html         live, talks to this API (default)
                #   ui-preview.html offline snapshot with data baked in
                # Prefer the live one; fall back so an existing deployment that
                # only built the static page still serves something.
                for name in ("ui.html", "ui-preview.html"):
                    ui = os.path.join(ROOT, name)
                    if os.path.isfile(ui):
                        with open(ui, encoding="utf-8") as f:
                            return self._send(f.read(),
                                             ctype="text/html; charset=utf-8")
                return self._send({"error": "no ui.html"}, 404)

            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/ui")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return

            if path in ("/api", "/api/health"):
                return self._send({
                    "ok": True, "service": "arena-core",
                    "results_dir": os.path.relpath(RESULTS, ROOT),
                    "runs": len(list_runs()),
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })

            if path == "/api/presets":
                # Everything the preset tab shows: which configs exist on this
                # machine (Llama-Rig + config/profiles.json), their launch params,
                # every measured speed series, and which one is serving right
                # now. presets.py owns that logic; this route only carries it.
                import presets as PS
                return self._send(PS.catalog(force_scan=(u.query == "scan=1")))

            if path == "/api/targets":
                import targets as T
                import os as _os
                ts = T.list_targets()
                for _t in ts:
                    # Report whether the credential this endpoint needs is
                    # actually present in THIS process. Without it the run dies
                    # with HTTP 401 and the UI can only say "unreachable", which
                    # sends the user looking at the network instead of the key.
                    env = _t.get("api_key_env")
                    if env or T.get_runtime_key(_t.get("id")):
                        _t["key_ok"] = T.has_key(_t.get("id"), env)
                        _t["key_env"] = env
                        _t["key_source"] = ("memory" if T.get_runtime_key(_t.get("id"))
                                            else "env")
                    else:
                        _t["key_ok"] = None      # not needed / not declared
                return self._send({"ok": True, "targets": ts})
            if path.startswith("/api/target/"):
                import targets as T
                tid = path[len("/api/target/"):]
                t = T.get_target(tid)
                if not t:
                    return self._send({"error": "no such target"}, 404)
                return self._send({"ok": True, "target": t})
            if path == "/api/runs":
                return self._send(list_runs())

            if path.startswith("/api/run/"):
                rel = self._safe(path[len("/api/run/"):])
                p = os.path.join(RESULTS, rel) if rel else None
                if not p or not os.path.isfile(p):
                    return self._send({"error": "not found"}, 404)
                return self._send(read_json(p))

            if path.startswith("/api/launch/"):
                rel = self._safe(path[len("/api/launch/"):])
                p = os.path.join(RESULTS, rel) if rel else None
                if not p or not os.path.isfile(p):
                    return self._send({"error": "not found"}, 404)
                d = read_json(p)
                s = d.get("summary") or d.get("level") or {}
                return self._send(s.get("launch") or {})

            if path.startswith("/api/environment"):
                p = os.path.join(RESULTS, "environment_latest.json")
                if u.query == "live=1" or not os.path.isfile(p):
                    import environment as envmod
                    snap = envmod.assess()
                    try:
                        with open(p, "w", encoding="utf-8") as f:
                            json.dump(snap, f, ensure_ascii=False, indent=1)
                    except OSError:
                        pass
                    return self._send(snap)
                return self._send(read_json(p))

            if path == "/api/leaderboard":
                p = os.path.join(RESULTS, "leaderboard.json")
                if not os.path.isfile(p):
                    return self._send({"error": "run build_report.py first"}, 404)
                return self._send(read_json(p))

            if path == "/api/paired":
                p = os.path.join(RESULTS, "leaderboard.json")
                if not os.path.isfile(p):
                    return self._send({"error": "run build_report.py first"}, 404)
                return self._send(read_json(p).get("paired") or {})

            if path == "/api/datasets":
                return self._send(dataset_counts())

            if path.startswith("/api/reports/"):
                rel = self._safe(path[len("/api/reports/"):])
                p = os.path.join(REPORTS, rel) if rel else None
                if not p or not os.path.isfile(p):
                    return self._send({"error": "not found"}, 404)
                with open(p, encoding="utf-8") as f:
                    return self._send(f.read(), ctype="text/markdown; charset=utf-8")

            return self._send({"error": "unknown endpoint", "path": path}, 404)
        except Exception as e:
            return self._send({"error": type(e).__name__, "detail": str(e)[:200]}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770,
                    help="0 = pick the first free port in 8770-8869")
    ap.add_argument("--open-ui", action="store_true",
                    help="open a browser once the socket is bound")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    # Port selection: a stale process from an earlier session can hold the port
    # without appearing in any process list this sandbox can read, and then every
    # request is answered by OLD code - the page appears not to update no matter
    # what was edited. That happened repeatedly here, so --port 0 now means "take
    # the first free one" and the chosen port is printed for the caller to parse.
    if args.port == 0:
        import socket as _sock
        for _p in range(8770, 8870):
            _s = _sock.socket()
            try:
                _s.bind((args.host, _p))
                _s.close()
                args.port = _p
                break
            except OSError:
                _s.close()
                continue
        else:
            print("no free port in 8770-8869", flush=True)
            raise SystemExit(6)
    srv = HTTPServer((args.host, args.port), Handler)
    print("ARENA_PORT=%d" % args.port, flush=True)
    print("UI: http://127.0.0.1:%d/ui" % args.port, flush=True)
    print("API: http://127.0.0.1:%d/api/health" % args.port, flush=True)
    print("", flush=True)
    # --open-ui: the launcher cannot know the port before this process picks it,
    # so the process opens the browser itself once the socket is bound. Opening
    # it from the .bat would need the port parsed back out of stdout, which is
    # racy and breaks whenever the output format changes.
    if getattr(args, "open_ui", False):
        try:
            import threading, webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(
                "http://127.0.0.1:%d/ui" % args.port)).start()
        except Exception:
            pass
    srv.verbose = args.verbose
    print("arena-core listening on http://%s:%d" % (args.host, args.port))
    print("  results: %s (%d runs)" % (os.path.relpath(RESULTS, ROOT), len(list_runs())))
    print("  endpoints: /api/health /api/runs /api/run/<f> /api/launch/<f> "
          "/api/environment /api/leaderboard /api/paired /api/datasets /api/reports/<f>")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(NL + "stopped")


if __name__ == "__main__":
    main()
