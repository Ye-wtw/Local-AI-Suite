# -*- coding: utf-8 -*-
"""sampler.py - background machine-state sampler for model evaluations.

environment.py answers "is the machine clean RIGHT NOW". This answers a
different question: "what did the machine actually do DURING the run", sampled
at a fixed interval, without blocking the evaluator and without inventing
numbers when the machine cannot be read.

Three rules, ordered by how easily they turn into a lie:

  1. NEVER FABRICATE. A metric with no data is reported as missing (n=0), and
     p95 stays None below 3 samples. No interpolation, no forward-fill, no
     substituting 0 for "unknown" - a 0 MiB VRAM reading and a failed
     nvidia-smi call are not the same measurement.
  2. NEVER BLOCK. Every probe runs on its own daemon thread with a bounded
     join, so one slow powershell call (measured up to 30 s) cannot stall the
     evaluation thread, and a slow cycle cannot smear into the next tick.
  3. NEVER LEAK. The loop thread is a daemon, stop() always joins it, and the
     class is usable as a context manager so an exception in the caller still
     leaves nothing running.

Standard library only (project iron rule).
"""
import ctypes
import math
import os
import subprocess
import sys
import threading
import time

# Metrics summarise() knows about by name. Extra numeric keys found in the
# samples are summarised too, so a test fixture can invent a metric.
METRIC_KEYS = (
    "gpu_mem_used_mib", "gpu_util_pct", "gpu_temp_c", "gpu_power_w",
    "gpu_mem_total_mib", "gpu_other_procs_n", "gpu_other_mem_mib",
    "proc_rss_mib", "proc_cpu_s", "proc_cpu_pct",
    "mem_free_gib", "mem_total_gib",
)

# Bookkeeping, not measurements: never summarised as metrics.
_BOOKKEEPING = frozenset((
    "t", "ts", "probe_ms", "proc_pid", "gpu_ok", "proc_ok", "notes",
    "mem_source", "computed_at",
))

P95_MIN_N = 3

# Highest timeout environment.py itself passes to any of its subprocess calls
# is 30 s (cpu_top); a cycle never waits longer than this for one probe.
PROBE_TIMEOUT_S = 30.0


def _load_environment():
    """Import runner/environment.py under whichever import style the caller
    uses: flat (run.py / speed_probe.py put runner/ on sys.path), package
    (from runner import ...), or by file path beside this module."""
    try:
        import environment as mod
        if hasattr(mod, "gpu_snapshot"):
            return mod
    except Exception:
        pass
    try:
        from runner import environment as mod
        if hasattr(mod, "gpu_snapshot"):
            return mod
    except Exception:
        pass
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "_arena_environment", os.path.join(here, "environment.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


ENV = _load_environment()


def _run(args, timeout):
    """Local copy of environment._run, used only when environment.py could
    not be imported at all. Kept identical in behaviour: any failure is ""."""
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           errors="ignore", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _ps(command, timeout):
    """Run one powershell command, preferring environment.py's own runner."""
    if ENV is not None and hasattr(ENV, "_run"):
        return ENV._run(["powershell", "-NoProfile", "-Command", command],
                        timeout=timeout)
    return _run(["powershell", "-NoProfile", "-Command", command], timeout)


def _num(v):
    """float or None - never a made-up 0 for an unreadable field."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _probe_gpu():
    """nvidia-smi via environment.gpu_snapshot(); {} means unavailable."""
    if ENV is None:
        return {}
    return ENV.gpu_snapshot() or {}


def _probe_gpu_other():
    """Other processes holding GPU memory - 'was it exclusive?' is not
    optional in a report (AGENTS.md)."""
    if ENV is None:
        return []
    return ENV.gpu_competitors() or []


def _probe_proc(pid, timeout=20):
    """RSS MiB and cumulative CPU seconds for one pid.

    Deliberately targeted rather than environment.cpu_top(), which sorts by
    CPU time and truncates: the process under test can fall off that list
    between ticks, which would silently drop the metric.
    """
    ps = ("$p = Get-Process -Id %d -ErrorAction SilentlyContinue; "
          "if($p){ '{0}|{1}' -f $(if($p.CPU -ne $null)"
          "{[math]::Round($p.CPU,2)}else{'NA'}), "
          "[math]::Round($p.WorkingSet64/1MB,1) } else { 'GONE' }" % int(pid))
    out = _ps(ps, timeout)
    if not out:
        return {}
    head = out.splitlines()[0].strip()
    if head == "GONE":
        return {"gone": True}
    parts = head.split("|")
    if len(parts) < 2:
        return {}
    return {"cpu_s": _num(parts[0]), "rss_mib": _num(parts[1])}


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _mem_kernel32():
    """Physically available RAM straight from the kernel: microseconds, no
    subprocess. Returns {} where that call is unavailable."""
    if not hasattr(ctypes, "windll"):
        return {}
    try:
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return {}
        gib = 1024.0 ** 3
        return {"total_gib": round(st.ullTotalPhys / gib, 2),
                "free_gib": round(st.ullAvailPhys / gib, 2)}
    except Exception:
        return {}


def _probe_mem():
    """System memory, preferring environment.memory_info().

    That call is not trustworthy everywhere: on this machine Get-CimInstance
    is denied for an unprivileged caller and it returns a perfectly well
    formed 0.0 / 0.0. Recording that as "0 GiB free" would be worse than
    recording nothing, so an implausible answer is rejected and the reading
    falls back to the kernel.
    """
    raw = {}
    if ENV is not None:
        try:
            raw = ENV.memory_info() or {}
        except Exception:
            raw = {}
    total, free = _num(raw.get("total_gib")), _num(raw.get("free_gib"))
    if total and total > 0 and free is not None and 0 <= free <= total * 1.05:
        return {"total_gib": total, "free_gib": free,
                "source": "environment.memory_info"}
    fb = _mem_kernel32()
    if fb:
        fb["source"] = "kernel32"
        return fb
    return {}


def _run_probes(probes, timeout):
    """Run probes concurrently on daemon threads and collect what finished.

    Daemon threads (not a ThreadPoolExecutor) because a worker stuck inside an
    unkillable call must never delay interpreter exit; the bounded join is
    what keeps a cycle finite.
    """
    out, lock = {}, threading.Lock()

    def worker(key, fn):
        try:
            val, err = fn(), None
        except Exception as exc:                     # a probe never kills the loop
            val, err = None, "%s: %r" % (type(exc).__name__, exc)
        with lock:
            out[key] = (val, err)

    threads = []
    for key, fn in probes:
        th = threading.Thread(target=worker, args=(key, fn),
                              name="sampler-probe-%s" % key, daemon=True)
        th.start()
        threads.append((key, th))
    deadline = time.monotonic() + timeout
    for key, th in threads:
        th.join(max(0.0, deadline - time.monotonic()))
    res = {}
    for key, _th in threads:
        res[key] = out.get(key, (None, "timeout after %.0fs" % timeout))
    return res


def gather(pid=None, probe_timeout_s=PROBE_TIMEOUT_S):
    """Take one raw sample synchronously.

    Also usable on its own: a short run or a unit test can read the machine
    once without starting the background thread. Never raises - an unreadable
    subsystem becomes ok=False plus a note, because during a live evaluation
    a sampler that throws is worse than one that admits it saw nothing.
    """
    target = os.getpid() if pid is None else pid
    try:
        target = int(target)
    except (TypeError, ValueError):
        target = os.getpid()
    notes = []
    t0 = time.monotonic()
    data = _run_probes((
        ("gpu", _probe_gpu),
        ("gpu_other", _probe_gpu_other),
        ("proc", lambda: _probe_proc(target)),
        ("mem", _probe_mem),
    ), probe_timeout_s)
    out = {"proc_pid": target, "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    gpu, err = data["gpu"]
    if err:
        notes.append("GPU 采样不可用(%s)" % err)
    gpu = gpu or {}
    out["gpu_ok"] = gpu.get("mem_used_mib") is not None
    for src, dst in (("mem_used_mib", "gpu_mem_used_mib"),
                     ("mem_total_mib", "gpu_mem_total_mib"),
                     ("util_pct", "gpu_util_pct"),
                     ("temp_c", "gpu_temp_c"),
                     ("power_w", "gpu_power_w")):
        out[dst] = _num(gpu.get(src))
    if not out["gpu_ok"]:
        notes.append("GPU 采样不可用")
    elif out["gpu_power_w"] is None:
        notes.append("GPU 功耗不可用(驱动未上报 power.draw)")

    other, err = data["gpu_other"]
    if err:
        notes.append("GPU 竞争进程采样不可用(%s)" % err)
    other = other or []
    out["gpu_other_procs_n"] = len(other)
    out["gpu_other_mem_mib"] = round(
        sum(_num(c.get("mem_mib")) or 0.0 for c in other), 1)

    proc, err = data["proc"]
    if err:
        notes.append("进程采样不可用(%s)" % err)
        proc = None
    if proc and proc.get("gone"):
        notes.append("进程 pid=%d 已退出" % target)
        proc = {}
    proc = proc or {}
    out["proc_ok"] = proc.get("rss_mib") is not None
    out["proc_rss_mib"] = _num(proc.get("rss_mib"))
    out["proc_cpu_s"] = _num(proc.get("cpu_s"))
    out["proc_cpu_pct"] = None

    mem, err = data["mem"]
    if err:
        notes.append("内存采样不可用(%s)" % err)
    mem = mem or {}
    out["mem_free_gib"] = _num(mem.get("free_gib"))
    out["mem_total_gib"] = _num(mem.get("total_gib"))
    out["mem_source"] = mem.get("source")
    if out["mem_free_gib"] is None:
        notes.append("内存采样不可用")

    out["probe_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
    out["notes"] = notes
    return out


def _stat(values):
    """mean / max / p95 / min / n for one metric.

    p95 is nearest-rank and stays None below P95_MIN_N samples: with two
    points a percentile is arithmetic theatre, and interpolating one would
    report a value that was never measured.
    """
    n = len(values)
    if n == 0:
        return {"mean": None, "max": None, "p95": None, "min": None, "n": 0}
    ordered = sorted(values)
    p95 = None
    if n >= P95_MIN_N:
        rank = int(math.ceil(0.95 * n)) - 1
        p95 = ordered[max(0, min(n - 1, rank))]
    return {"mean": round(sum(values) / n, 4), "max": ordered[-1], "p95": p95,
            "min": ordered[0], "n": n}


def summarise(samples):
    """Pure function: raw samples in, per-metric statistics out.

    Numeric keys beyond METRIC_KEYS are picked up too. None, NaN and bool
    values are treated as "not measured" rather than as data points.
    """
    rows = [s for s in (samples or []) if isinstance(s, dict)]
    keys = list(METRIC_KEYS)
    for s in rows:
        for k, v in s.items():
            if k in _BOOKKEEPING or k in keys or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                keys.append(k)
    stats = {}
    for k in keys:
        vals = []
        for s in rows:
            v = s.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            f = float(v)
            if math.isfinite(f):
                vals.append(f)
        stats[k] = _stat(vals)
    return {
        "n_samples": len(rows),
        "stats": stats,
        "metrics_missing": [k for k in keys if stats[k]["n"] == 0],
        "p95_min_n": P95_MIN_N,
        "note": ("p95 为 nearest-rank 实测值,样本数 < %d 时为 None;"
                 "n=0 表示该指标全程未采到,不是 0" % P95_MIN_N),
    }


class Sampler(object):
    """Interval sampler for one evaluation run.

        sampler = Sampler(interval_s=5.0, pid=server_pid)
        sampler.start()
        ...
        report = sampler.stop()      # {'samples', 'stats', 'ok', ...}

    Also a context manager: __exit__ stops the thread, so an exception on the
    evaluation path cannot leave the sampler running.
    """

    def __init__(self, interval_s=5.0, pid=None):
        try:
            interval = float(interval_s)
        except (TypeError, ValueError):
            interval = 5.0
        self.interval_s = interval if interval > 0 else 5.0
        self.pid = None
        if pid is not None:
            try:
                self.pid = int(pid)
            except (TypeError, ValueError):
                self.pid = None
        self.samples = []
        self._lock = threading.Lock()
        self._stop_ev = threading.Event()
        self._thread = None
        self._t0 = None
        self._t_end = None
        self._error = None
        self._notes = []
        self._skipped_ticks = 0
        self._last_cpu = None
        self._summary = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Start the background thread. Idempotent while running."""
        if self._thread is not None and self._thread.is_alive():
            return self
        with self._lock:
            self.samples = []
            self._notes = []
        self._error = None
        self._skipped_ticks = 0
        self._last_cpu = None
        self._summary = None
        self._t_end = None
        self._stop_ev.clear()
        self._t0 = time.monotonic()
        th = threading.Thread(target=self._loop, name="sampler-loop", daemon=True)
        self._thread = th
        th.start()
        return self

    def stop(self):
        """Stop sampling and return the summary. Safe to call twice."""
        if self._summary is not None:
            return self._summary
        self._stop_ev.set()
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=PROBE_TIMEOUT_S + 1.0)
            if th.is_alive():
                self._record_error("采样线程未在 %.0fs 内退出" % (PROBE_TIMEOUT_S + 1.0))
        duration = None
        if self._t0 is not None:
            end = self._t_end if self._t_end is not None else time.monotonic()
            duration = round(end - self._t0, 2)
        with self._lock:
            samples = list(self.samples)
            notes = list(self._notes)
        stats = summarise(samples)
        note_set = []
        for n in notes:
            if n not in note_set:
                note_set.append(n)
        ok = self._error is None and len(samples) > 0
        self._summary = {
            "ok": ok,
            "error": self._error,
            "interval_s": self.interval_s,
            "duration_s": duration,
            "n_samples": len(samples),
            "samples": samples,
            "stats": stats["stats"],
            "metrics_missing": stats["metrics_missing"],
            "p95_min_n": P95_MIN_N,
            "gpu_available": any(bool(s.get("gpu_ok")) for s in samples),
            "proc_available": any(bool(s.get("proc_ok")) for s in samples),
            "pid": self.pid,
            "skipped_ticks": self._skipped_ticks,
            "notes": note_set,
        }
        return self._summary

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False                      # never swallow the caller's exception

    # -- internals ---------------------------------------------------------
    def _record_error(self, msg):
        if self._error is None:
            self._error = msg

    def _loop(self):
        deadline = self._t0
        while not self._stop_ev.is_set():
            try:
                sample = self._sample()
            except Exception as exc:
                self._record_error("采样失败: %r" % (exc,))
                sample = None
            if sample is not None:
                with self._lock:
                    self.samples.append(sample)
                    for note in sample.get("notes") or []:
                        self._notes.append(note)
            deadline += self.interval_s
            now = time.monotonic()
            if deadline < now - self.interval_s:
                # A cycle overran an entire interval. Resync instead of firing
                # a burst of catch-up samples, but say so: a report must not
                # imply a tick happened when none did.
                self._skipped_ticks += int((now - deadline) // self.interval_s) + 1
                deadline = now
            wait = deadline - now
            if wait > 0 and self._stop_ev.wait(wait):
                break
        self._t_end = time.monotonic()

    def _sample(self):
        raw = gather(self.pid, PROBE_TIMEOUT_S)
        raw["t"] = round(time.monotonic() - self._t0, 3)
        raw["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        cpu = raw.get("proc_cpu_s")
        if cpu is not None:
            if self._last_cpu is not None:
                dt = raw["t"] - self._last_cpu[0]
                delta = cpu - self._last_cpu[1]
                # A counter that went backwards means a recycled pid, not a
                # negative CPU consumption; leave cpu_pct unmeasured.
                if dt > 0 and delta > 0:
                    raw["proc_cpu_pct"] = round(delta / dt * 100.0, 2)
            self._last_cpu = (raw["t"], cpu)
        return raw


if __name__ == "__main__":
    import json
    secs = 12.0
    try:
        secs = float(sys.argv[1]) if len(sys.argv) > 1 else secs
    except ValueError:
        pass
    s = Sampler(interval_s=5.0, pid=None)
    s.start()
    time.sleep(secs)
    r = s.stop()
    r.pop("samples", None)
    print(json.dumps(r, ensure_ascii=False, indent=2))
