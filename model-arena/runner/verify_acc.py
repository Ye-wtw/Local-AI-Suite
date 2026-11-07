# -*- coding: utf-8 -*-
import subprocess, sys, os, io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
suites = ["math", "instruction", "chess", "longctx", "code", "agent",
          "reliability", "realworld", "vision", "hint"]
log = os.path.join(ROOT, "results", ".queue", "accverify.log")
fh = io.open(log, "w", encoding="utf-8")
fails = []
for s in suites:
    cmd = [sys.executable, "-u", os.path.join(ROOT, "runner", "run.py"),
           "--endpoint", "queue", "--suite", s, "--limit", "1",
           "--tag", "accv", "--yes", "--skip-vram-check",
           "--preset-id", "KV17-Bonsai2T5-200K"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="ignore")
    ok = r.returncode == 0
    fh.write("%-13s %s" % (s, "OK" if ok else "FAIL exit=%s" % r.returncode))
    if not ok:
        fails.append(s)
        bad = [l for l in (r.stdout or "").splitlines() if "Error" in l or "error" in l]
        fh.write("  " + " | ".join(bad[-2:])[:180])
    fh.write(chr(10)); fh.flush()
fh.write("FAILS=%s" % (fails or "none"))
fh.close()
print("done, fails:", fails)
