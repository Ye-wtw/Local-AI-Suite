# -*- coding: utf-8 -*-
import subprocess, sys, os, time, json, io, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
suites = ["chess", "reliability", "agent", "realworld"]
stamp = time.strftime("%Y%m%d-%H%M%S")
log = os.path.join(ROOT, "results", ".queue", "sevendims_%s.log" % stamp)
fh = io.open(log, "w", encoding="utf-8")
def w(s):
    fh.write(s + chr(10)); fh.flush()

for s in suites:
    w("[%s] start %s" % (time.strftime("%H:%M:%S"), s))
    cmd = [sys.executable, "-u", os.path.join(ROOT, "runner", "run.py"),
           "--endpoint", "queue", "--suite", s, "--tag", "d6d8d9d10",
           "--yes", "--skip-vram-check",
           "--preset-id", "KV17-Bonsai2T5-200K"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="ignore")
    w("[%s] %s exit=%s" % (time.strftime("%H:%M:%S"), s, r.returncode))
    for line in (r.stdout or "").splitlines()[-6:]:
        w("    " + line)
    fh.flush()
w("[done] %s" % time.strftime("%H:%M:%S"))
fh.close()
print("ALL DONE")
