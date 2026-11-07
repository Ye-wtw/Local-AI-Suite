# -*- coding: utf-8 -*-
"""mine_sessions.py - pull REAL user tasks out of DSH session archives.

Why: the D9 "real world" dimension is supposed to test what the user actually
asks a model to do. 70 of its 102 questions are hand-written imitations; only 32
came from real sessions. Everything else in the bank measures ability on
questions someone invented, which is exactly what the pelican video argues is
not the same thing as daily usefulness.

This miner reads the session store, keeps only first-turn user messages that look
like an actual task (long enough, imperative, not a greeting/pasted error), and
writes a candidate list. It does NOT grade anything - scoring needs a checkable
deliverable, which a human or a second pass must supply.

Usage:
  python scripts/mine_sessions.py --scan            # how many candidates exist
  python scripts/mine_sessions.py --write --limit 60
"""
import argparse
import glob
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "datasets", "_mined")
SESSIONS = os.path.join(os.path.expanduser("~"), ".dsh", "sessions")

# Tasks worth testing are instructions to DO something, not chatter.
TASK_VERBS = ["帮我", "请", "写", "改", "修", "检查", "分析", "生成", "整理", "提取",
              "总结", "实现", "优化", "排查", "翻译", "对比", "评估", "设计", "把",
              "看一下", "看看", "跑", "测试", "补", "加上", "去掉", "重写"]
CHATTER = ["你好", "在吗", "谢谢", "好的", "嗯", "继续", "test", "hi", "hello"]
# pasted noise: stack traces, huge blobs
NOISE_MARK = ["Traceback (most recent call last)", "at Object.", "错误堆栈"]


def text_of(data):
    parts = []
    for c in (data.get("content") or []):
        if isinstance(c, dict):
            if c.get("type") == "text" and c.get("text"):
                parts.append(c["text"])
    return chr(10).join(parts).strip()


def iter_sessions():
    dctx = None
    try:
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
    except ImportError:
        return
    for f in sorted(glob.glob(os.path.join(SESSIONS, "**", "*.zstd"), recursive=True)):
        try:
            with open(f, "rb") as fh:
                raw = dctx.stream_reader(fh).read()
        except Exception:
            continue
        objs = []
        for line in raw.decode("utf-8", "replace").split(chr(10)):
            if not line.strip():
                continue
            try:
                objs.append(json.loads(line))
            except Exception:
                continue
        meta = next((o for o in objs if o.get("type") == "session"), {})
        users = [o for o in objs if o.get("type") == "user/message"]
        yield f, meta, objs, users


def looks_like_task(t):
    if not t or len(t) < 15 or len(t) > 1200:
        return False
    low = t.lower()
    for c in CHATTER:
        if low.strip() == c or (len(t) < 8 and c in low):
            return False
    for n in NOISE_MARK:
        if n.lower() in low:
            return False
    # must read as an instruction
    return any(v in t for v in TASK_VERBS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()

    rows = []
    n_sessions = n_users = 0
    for f, meta, objs, users in iter_sessions():
        n_sessions += 1
        sid = meta.get("id") or os.path.basename(os.path.dirname(f))
        cwd = meta.get("cwd") or ""
        # only the first user turn: later turns depend on context we cannot replay
        first = None
        for u in users:
            t = text_of(u.get("data") or {})
            if t:
                first = t
                break
        n_users += len(users)
        if first and looks_like_task(first):
            rows.append({"session_id": sid, "cwd": cwd, "text": first,
                         "chars": len(first), "archive": f})

    rows.sort(key=lambda r: -r["chars"])
    print("会话 %d 个, user/message %d 条" % (n_sessions, n_users))
    print("符合「真实任务」特征: %d" % len(rows))
    if rows:
        lens = sorted(r["chars"] for r in rows)
        print("长度: min=%d 中位=%d max=%d" % (lens[0], lens[len(lens)//2], lens[-1]))
        from collections import Counter
        cw = Counter(os.path.basename(r["cwd"]) if r["cwd"] else "?" for r in rows)
        print("工作目录分布 top5:", cw.most_common(5))
        print()
        for r in rows[:5]:
            print("  [%4d 字] %s" % (r["chars"], r["text"][:100].replace(chr(10), " ")))
    if a.write:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, "session_tasks.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows[:a.limit]:
                fh.write(json.dumps(r, ensure_ascii=False) + chr(10))
        print()
        print("写入 %d 条 -> %s" % (min(len(rows), a.limit), p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
