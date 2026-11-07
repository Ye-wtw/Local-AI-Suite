# -*- coding: utf-8 -*-
"""harvest_sessions.py - mine REAL user tasks out of local DSH session logs.

This is the most valuable question source we have: private, grounded in actual work,
and impossible for any model to have memorized. It is the opposite of a public
benchmark (which is likely already in the training data).

Reads <DSH_HOME>/sessions/**/*.jsonl.zstd, pulls user/message events, filters to
substantive requests, dedupes, and writes a candidate file for curation.

Privacy: outputs stay local under datasets/_samples/. Nothing is uploaded.

Usage:
  python scripts/harvest_sessions.py --list-sessions
  python scripts/harvest_sessions.py --min-chars 40 --limit 400
"""
import argparse, collections, glob, json, os, re, sys, zstandard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DSH_HOME = os.path.expanduser("~/.dsh")
OUT = os.path.join(ROOT, "datasets", "_samples", "D9_harvest_candidates.jsonl")
NL = chr(10)

# tool-result noise and shell-ish one-liners are not evaluation-worthy tasks
_Q = chr(34) + chr(39) + chr(96)
NOISE = re.compile("^[" + chr(92) + "s" + _Q + "]*$" "|^[/" + chr(92) + chr(92) + "]" "|^cd |^ls |^dir |^git |^npm |^pnpm |^python -c|^pip ")
CMDWORDS = ("继续", "go on", "yes", "ok", "好的", "嗯", "对", "1", "2", "3", "4", "c",
            "继续吧", "跑", "修", "做吧")


# system-generated noise that is not a user task
SYS_NOISE = ("Background subagent", "This is an automatically generated checkpoint",
             "automatically generated checkpoint", "subagent_fork", "Tool call ended",
             "编译完成", "AgentTeams captain protocol")
PLACEHOLDER = ("{这里", "粘贴上面", "<原题结果>", "<探针结果>", "TODO")
URL_ONLY = re.compile(r"^(https?://\S+\s*)+$")


def sessions(only=None):
    pats = "%s/sessions/**/*.jsonl.zstd" % DSH_HOME
    out = sorted(glob.glob(pats, recursive=True))
    if only:
        out = [p for p in out if only in p]
    return out


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text") or "")
            elif isinstance(c, str):
                parts.append(c)
        return NL.join(parts)
    return ""


def harvest(min_chars, limit, only):
    seen = set()
    got = []
    for p in sessions(only):
        try:
            raw = zstandard.ZstdDecompressor().stream_reader(open(p, "rb")).read().decode("utf-8", "ignore")
        except Exception:
            continue
        session_id = os.path.basename(os.path.dirname(p))
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") != "user/message":
                continue
            d = o.get("data") or {}
            # user/message stores content directly on data: {content:[{type:text,text:...}]}
            txt = text_of(d.get("content") or (d.get("message") or {}).get("content")).strip()
            if len(txt) < min_chars:
                continue
            if NOISE.match(txt) or txt.strip().lower() in CMDWORDS:
                continue
            if any(txt.startswith(x) or x in txt[:80] for x in SYS_NOISE):
                continue
            if URL_ONLY.match(txt.strip()):
                continue
            if sum(1 for x in PLACEHOLDER if x in txt) >= 1 and len(txt) < 200:
                continue
            if txt.count("http") >= 3 and len(txt) < 120:
                continue
            key = re.sub(r"\s+", "", txt)[:160]
            if key in seen:
                continue
            seen.add(key)
            got.append({"text": txt, "session": session_id,
                        "time": o.get("time"), "chars": len(txt)})
    got.sort(key=lambda x: x["time"] or "")
    return got[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--only", default="")
    ap.add_argument("--list-sessions", action="store_true")
    args = ap.parse_args()

    ss = sessions(args.only or None)
    if args.list_sessions:
        by = collections.Counter(os.path.basename(os.path.dirname(p)) for p in ss)
        print("会话文件 %d 个,目录 %d 个" % (len(ss), len(by)))
        for k, v in by.most_common(10):
            print("   %-46s %d" % (k, v))
        return

    got = harvest(args.min_chars, args.limit, args.only or None)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for _i, g in enumerate(got):
            f.write(json.dumps({"id": "d9_cand_%04d" % _i, "dim": "D9",
                                "source": "user-dsh-sessions",
                                "subcat": "harvested", "user": g["text"],
                                "session": g["session"], "notes": "真实会话任务,待人工策展"},
                               ensure_ascii=False) + NL)
    print("扫描会话 %d 个 -> 候选任务 %d 条" % (len(ss), len(got)))
    print("-> %s" % os.path.relpath(OUT, ROOT))
    print()
    print("样例(前 8 条):")
    for g in got[:8]:
        print("   [%5d 字] %s" % (g["chars"], g["text"][:88].replace(NL, " ")))
    print()
    print("下一步:人工策展 —— 挑出可自动判分的任务,补上 expect.must_cover / must_not,")
    print("        然后并入 datasets/_samples/D9_realworld_full.jsonl 并重跑 d9_baseline.py")


if __name__ == "__main__":
    main()
