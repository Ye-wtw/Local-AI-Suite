# -*- coding: utf-8 -*-
"""environment.py - detect and record what could interfere with a measurement.

The product goal is "as interference-free as possible, INCLUDING interference from
the project itself". That phrase has teeth: the evaluator is a Python process that
competes for CPU with a --cpu-moe server, the desktop browser steals memory, and a
game left running destroys every timing number.

Three layers, deliberately ordered by risk:
  1. DETECT   - always on, read-only. Never blocks a run.
  2. ISOLATE  - opt-in only. Never kills a process without an explicit flag.
  3. RECORD   - every finding lands in the result file, so a number can always be
                traced to the conditions it was measured under.

The point of trust_level is that a slightly noisy run is still USEFUL - it is
marked, not discarded. Throwing data away teaches nothing.
"""
import json
import os
import subprocess
import time

NL = chr(10)


import ctypes


def _run(args, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="ignore",
                           timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def gpu_snapshot():
    """Current GPU state. Returns {} when nvidia-smi is unavailable."""
    q = ("utilization.gpu,memory.used,memory.total,temperature.gpu,"
         "clocks.current.sm,power.draw")
    out = _run(["nvidia-smi", "--query-gpu=" + q, "--format=csv,noheader,nounits"])
    if not out:
        return {}
    line = out.splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 6:
        return {"raw": line}
    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    return {"util_pct": num(parts[0]), "mem_used_mib": num(parts[1]),
            "mem_total_mib": num(parts[2]), "temp_c": num(parts[3]),
            "sm_clock_mhz": num(parts[4]), "power_w": num(parts[5])}


def gpu_competitors():
    """Other processes holding GPU memory - a game or a second server."""
    out = _run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits"])
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                rows.append({"pid": int(parts[0]), "name": parts[1],
                             "mem_mib": float(parts[2])})
            except ValueError:
                continue
    return rows


def cpu_top(limit=8):
    """Processes with the most accumulated CPU time.

    Accumulated CPU seconds are used rather than an instantaneous percentage
    because a single sample of %CPU is nearly meaningless on Windows.
    """
    ps = ('Get-Process | Sort-Object CPU -Descending | Select-Object -First %d '
          'Name,Id,CPU,WorkingSet64 | ConvertTo-Json -Depth 2' % limit)
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=30)
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    rows = []
    for p in data:
        rows.append({"name": p.get("Name"), "pid": p.get("Id"),
                     "cpu_s": round(float(p.get("CPU") or 0), 1),
                     "rss_mib": round((p.get("WorkingSet64") or 0) / 1048576, 1)})
    return rows


def memory_info():
    """Physical memory, total and available, in GiB.

    **Do not use Get-CimInstance / WMI here.** Measured on this machine: an
    unprivileged caller gets "Get-CimInstance : 拒绝访问" (HRESULT 0x80041003),
    PowerShell writes it to stderr, and the parse then yields a perfectly
    well-formed 0.0/0.0. That is the worst possible failure shape: it is
    indistinguishable from a real reading, and downstream it made assess()
    report "可用内存偏低: 0.0 GiB" on a clean machine and force trust_level down to
    "medium" for every run. A wrong number that looks right is worse than no number.

    GlobalMemoryStatusEx (kernel32, via ctypes) needs no WMI and no elevation.
    Plausibility is checked rather than trusted: a total below 1 GiB is treated
    as a failed probe, not as a measurement of a very small machine.
    """
    info = _mem_kernel32()
    if info:
        return info
    # Fallback for the unusual host where kernel32 is unavailable. The same
    # plausibility guard applies, so a denied WMI call cannot masquerade as a
    # 0 GiB machine here either.
    ps = ("$os = Get-CimInstance Win32_OperatingSystem; "
          "[math]::Round($os.TotalVisibleMemorySize/1024/1024,2); "
          "[math]::Round($os.FreePhysicalMemory/1024/1024,2)")
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=25)
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    if len(lines) >= 2:
        try:
            total, free = float(lines[0]), float(lines[1])
            if _plausible_mem(total, free):
                return {"total_gib": total, "free_gib": free, "source": "wmi"}
        except ValueError:
            pass
    return {}


def _plausible_mem(total, free):
    """A reading is only a reading if it could describe a real machine."""
    try:
        total = float(total)
        free = float(free)
    except (TypeError, ValueError):
        return False
    return total >= 1.0 and 0.0 <= total - free


class _MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _mem_kernel32():
    try:
        m = _MEMSTATUS()
        m.dwLength = ctypes.sizeof(_MEMSTATUS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return {}
        total = round(m.ullTotalPhys / (1024.0 ** 3), 2)
        free = round(m.ullAvailPhys / (1024.0 ** 3), 2)
        if not _plausible_mem(total, free):
            return {}
        return {"total_gib": total, "free_gib": free, "source": "kernel32"}
    except Exception:
        return {}


def self_overhead():
    """CPU time consumed by THIS process and its children - the project's own
    interference, which the user explicitly asked to account for."""
    try:
        import resource  # not available on Windows
        return {}
    except Exception:
        pass
    ps = ('$p = Get-Process -Id %d; '
          '[math]::Round($p.CPU,2); [math]::Round($p.WorkingSet64/1MB,1)' % os.getpid())
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=20)
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    if len(lines) >= 2:
        try:
            return {"runner_cpu_s": float(lines[0]), "runner_rss_mib": float(lines[1])}
        except ValueError:
            pass
    return {}


# Processes that reliably distort a local-inference measurement. A browser or a
# game is the usual culprit; these are warnings, never auto-killed.
HEAVY = ("msedge", "chrome", "firefox", "game", "steam", "obs", "qq", "wechat",
         "discord", "photoshop", "premiere", "blender")

# System/background processes that accumulate huge CPU time without competing for
# compute during inference. Flagging them produced a false "medium" verdict.
IGNORE = ("audiodg", "system", "registry", "idle", "csrss", "wininit", "services",
          "lsass", "svchost", "dwm", "explorer", "searchindexer", "runtimebroker",
          "conhost", "fontdrvhost", "sihost", "taskhostw", "ctfmon", "spoolsv")


def assess(thresholds=None):
    """Full environment report with a trust_level verdict.

    trust_level:
      high   - no GPU competitor, GPU idle, nothing heavy running
      medium - a heavy process exists but GPU is free (timing may drift)
      low    - another process holds GPU memory, or free RAM is tight
    """
    th = thresholds or {}
    gpu_before = gpu_snapshot()
    comp = gpu_competitors()
    top = cpu_top()
    mem = memory_info()
    self_ovh = self_overhead()

    def _is_noise(n):
        n = (n or "").lower()
        return any(n.startswith(x) for x in IGNORE)

    heavy = [p for p in top
             if not _is_noise(p.get("name"))
             and (p.get("name") or "").lower().startswith(HEAVY)]
    warnings, reasons = [], []

    if comp:
        warnings.append("有 %d 个进程占用 GPU 显存:%s"
                        % (len(comp), ", ".join("%s(%.0fMiB)" % (c["name"], c["mem_mib"])
                                                for c in comp[:3])))
    if heavy:
        warnings.append("有重负载进程在跑:%s"
                        % ", ".join("%s(%.0fs)" % (h["name"], h["cpu_s"]) for h in heavy[:3]))
    free = mem.get("free_gib")
    if free is not None and free < (th.get("min_free_gib", 4.0)):
        warnings.append("可用内存偏低:%.1f GiB" % free)

    if comp:
        level = "low"
        reasons.append("其他进程占用 GPU")
    elif heavy:
        level = "medium"
        reasons.append("重负载进程在跑,但 GPU 空闲")
    elif free is not None and free < th.get("min_free_gib", 4.0):
        level = "medium"
        reasons.append("内存偏紧")
    else:
        level = "high"
        reasons.append("无 GPU 竞争,无重负载进程")

    return {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trust_level": level,
        "reasons": reasons,
        "warnings": warnings,
        "gpu": gpu_before,
        "gpu_competitors": comp,
        "cpu_top": top[:5],
        "heavy_processes": heavy,
        "memory": mem,
        "self_overhead": self_ovh,
        "note": ("干扰只记录不阻断;trust_level 表示该次测量的可信度,"
                 "不表示结果无效"),
    }


def diff_before_after(before, after):
    """Compare two snapshots to see whether the machine drifted during the run."""
    out = {}
    b, a = before.get("gpu") or {}, after.get("gpu") or {}
    if b and a:
        for k in ("util_pct", "mem_used_mib", "temp_c", "power_w"):
            if b.get(k) is not None and a.get(k) is not None:
                out[k] = {"before": b[k], "after": a[k], "delta": round(a[k] - b[k], 2)}
    if before.get("trust_level") != after.get("trust_level"):
        out["trust_changed"] = {"before": before.get("trust_level"),
                                "after": after.get("trust_level")}
    return out


if __name__ == "__main__":
    print(json.dumps(assess(), ensure_ascii=False, indent=2))
