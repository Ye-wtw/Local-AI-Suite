# -*- coding: utf-8 -*-
import subprocess, sys, os, io, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
suites = ["realworld", "chess", "agent", "reliability"]
log = os.path.join(ROOT, "results", ".queue", "verify1.log")
fh = io.open(log, "w", encoding="utf-8")
for s in suites:
    cmd = [sys.executable, "-u", os.path.join(ROOT, "runner", "run.py"),
           "--endpoint", "queue", "--suite", s, "--limit", "1",
           "--tag", "verify1", "--yes", "--skip-vram-check",
           "--preset-id", "KV17-Bonsai2T5-200K"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="ignore")
    ok = "OK" if r.returncode == 0 else "FAIL exit=%s" % r.returncode
    fh.write("%-13s %s" % (s, ok))
    if r.returncode != 0:
        tail = [l for l in (r.stdout or "").splitlines() if "Error" in l or "error" in l][-2:]
        fh.write("  " + " | ".join(tail))
    fh.write(chr(10)); fh.flush()
fh.write("DONE")
fh.close()
print("verified")
