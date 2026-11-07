# -*- coding: utf-8 -*-
"""launcher.py - start the engine ourselves, so the launch parameters are known.

Why this exists next to attach mode: a score measured against a server somebody
else started cannot be reproduced, because the flags are invisible. launch mode
composes the command line here, therefore every result can carry the exact
--ctx-size / --n-gpu-layers / KV quant that produced it.

Two constraints learned the hard way in this project (see AGENTS.md):
  * --parallel 1 is mandatory. The default 4 splits the context window four
    ways, so a 32k run silently becomes 8k per slot and long-context scores drop
    for a reason that has nothing to do with the model.
  * --reasoning off is mandatory for the same class of reason: with reasoning on,
    content comes back empty and everything lands in reasoning_content, which
    every judge here reads as an empty answer.

Nothing here starts a process by itself: build() returns the argv, and start()
is only called on explicit request.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LLAMA_BASE = [
    "--host", "127.0.0.1",
    "--parallel", "1",
    "-fa", "on",
    "-b", "512",
    "-ub", "512",
    "--reasoning", "off",
]


def _find_llama_server():
    """Locate llama-server.exe without assuming any particular machine layout.

    Resolution order (first hit wins):
      1. $LLAMA_SERVER            - explicit override, always respected
      2. next to this repo / in PATH - the normal case for a plain install
      3. locations from config/machine.json ("llama_server_dirs")

    Step 3 exists because engine binaries are often kept outside the repo.
    List YOUR directories there; nothing in the source should name a path
    specific to one machine (it would simply not exist for anyone else).
    """
    import shutil
    env = os.environ.get("LLAMA_SERVER")
    if env and os.path.isfile(env):
        return env

    cands = [shutil.which("llama-server") or ""]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub in ("bin", "llama.cpp/bin", "llama.cpp", "engine/bin", "tools/llama.cpp"):
        cands.append(os.path.join(here, sub, "llama-server.exe"))
        cands.append(os.path.join(here, sub, "llama-server"))

    # optional, per-user: paths recorded by runner/detect.py in config/machine.json
    #   "llama_server"     - the one you actually use (checked first)
    #   "llama_candidates" - every copy a scan found ({"path":...,"source":...})
    try:
        import json
        mf = os.path.join(here, "config", "machine.json")
        if os.path.isfile(mf):
            with open(mf, encoding="utf-8") as f:
                m = json.load(f)
            if m.get("llama_server"):
                cands.append(m["llama_server"])
            for c in (m.get("llama_candidates") or []):
                cands.append(c.get("path") if isinstance(c, dict) else c)
    except Exception:
        pass          # a broken config must never stop the launcher from probing

    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def build(model_path, port=8080, ctx=32768, ngl=99, cpu_moe=None,
          kv_type="q4_0", mmproj=None, exe=None, extra=None):
    """Compose the llama-server command line for one model."""
    if not model_path or not os.path.exists(model_path):
        raise ValueError("model file not found: %s" % model_path)
    exe = exe or _find_llama_server()
    if not exe:
        raise ValueError("llama-server not found; pass exe= or install llama.cpp")

    argv = [exe, "-m", model_path,
            "--ctx-size", str(int(ctx)),
            "--n-gpu-layers", str(int(ngl)),
            "--port", str(int(port))]
    if cpu_moe:
        argv += ["--n-cpu-moe", str(int(cpu_moe))]
    if kv_type:
        argv += ["-ctk", kv_type, "-ctv", kv_type]
    if mmproj:
        argv += ["--mmproj", mmproj]
    argv += LLAMA_BASE
    if extra:
        argv += list(extra)

    params = {"engine": "llama.cpp", "ctx": int(ctx), "ngl": int(ngl),
              "cpu_moe": int(cpu_moe) if cpu_moe else None, "kv_type": kv_type,
              "mmproj": bool(mmproj), "parallel": 1, "reasoning": "off",
              "flash_attn": True, "port": int(port),
              "model_file": os.path.basename(model_path)}
    return {"argv": argv, "exe": exe, "params": params}


def start(model_path, port=8080, wait_s=180, **kw):
    """Start llama-server in the background and wait until /health is 200."""
    built = build(model_path, port=port, **kw)
    t0 = time.time()
    log_dir = os.path.join(ROOT, "results", ".engine")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "llama_%d_%s.log"
                            % (port, time.strftime("%Y%m%d-%H%M%S")))
    fh = open(log_path, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(built["argv"],
                                cwd=os.path.dirname(built["exe"]) or None,
                                stdout=fh, stderr=subprocess.STDOUT)
    except Exception as e:
        return {"ok": False, "pid": None, "argv": built["argv"],
                "params": built["params"], "seconds": 0,
                "error": "spawn failed: %s" % str(e)[:120]}

    import urllib.request
    deadline = time.time() + wait_s
    last = None
    while time.time() < deadline:
        if proc.poll() is not None:
            return {"ok": False, "pid": proc.pid, "argv": built["argv"],
                    "params": built["params"], "seconds": time.time() - t0,
                    "error": "exited early (code %s), see %s"
                             % (proc.returncode,
                                os.path.relpath(log_path, ROOT))}
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/health" % port, timeout=2) as r:
                if r.status == 200:
                    return {"ok": True, "pid": proc.pid, "argv": built["argv"],
                            "params": built["params"],
                            "seconds": round(time.time() - t0, 1),
                            "log": os.path.relpath(log_path, ROOT), "error": None}
        except Exception as e:
            last = str(e)[:60]
        time.sleep(1.5)
    return {"ok": False, "pid": proc.pid, "argv": built["argv"],
            "params": built["params"], "seconds": time.time() - t0,
            "error": "not healthy after %ds (last: %s)" % (wait_s, last)}


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ctx", type=int, default=32768)
    ap.add_argument("--ngl", type=int, default=99)
    ap.add_argument("--cpu-moe", type=int, default=None)
    ap.add_argument("--kv-type", default="q4_0")
    a = ap.parse_args()
    b = build(a.model, port=a.port, ctx=a.ctx, ngl=a.ngl,
              cpu_moe=a.cpu_moe, kv_type=a.kv_type)
    print(" ".join(b["argv"]))
    print()
    print(json.dumps(b["params"], ensure_ascii=False, indent=2))