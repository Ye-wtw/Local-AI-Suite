# -*- coding: utf-8 -*-
"""overnight_watch.py - 整夜跑测的只读旁路记录器。

只读检查点与 nvidia-smi,永不修改队列状态、不停任何进程。
为什么需要它:日志是块缓冲,看不到实时进度;曾经因此把"正常跑着"误判成
"卡死",停掉了一个健康的 552 题任务。进度必须从检查点读,而且必须有
一条独立的时间线,才能回答"那 6 小时里发生了什么"。
"""
import io
import json
import os
import subprocess
import sys
import time
import datetime
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CKDIR = os.path.join(ROOT, "results", ".ckpt")
OUT = os.path.join(ROOT, "results", ".queue", "overnight_progress.log")


def gpu():
    try:
        r = subprocess.run(["nvidia-smi",
                            "--query-gpu=memory.used,utilization.gpu,temperature.gpu,power.draw",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=12)
        p = [x.strip() for x in (r.stdout or "").strip().split(",")]
        if len(p) >= 4:
            return "%sMiB %s%% %sC %sW" % tuple(p[:4])
    except Exception:
        pass
    return "n/a"


def progress():
    total, parts = 0, []
    for f in glob.glob(os.path.join(CKDIR, "*.jsonl")):
        n = 0
        try:
            with io.open(f, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        n += 1
        except Exception:
            continue
        total += n
        parts.append("%s:%d" % (os.path.basename(f).split("__")[0], n))
    return total, parts


def main():
    every = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 96
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    first = not os.path.isfile(OUT)
    for i in range(rounds):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        done, parts = progress()
        line = "%s | done=%d | %s | %s" % (ts, done, gpu(), " ".join(parts) or "starting")
        try:
            with io.open(OUT, "a", encoding="utf-8") as fh:
                fh.write(line + chr(10))
        except Exception:
            pass
        print(line, flush=True)
        if i < rounds - 1:
            time.sleep(every)


if __name__ == "__main__":
    main()
