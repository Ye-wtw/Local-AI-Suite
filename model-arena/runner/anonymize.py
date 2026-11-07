# -*- coding: utf-8 -*-
"""anonymize.py - de-identify the sample questions without gutting their realism.

The D9 dimension exists because public benchmarks do not measure "can this model do
my actual work". Those questions were mined from real sessions, and their value IS
the concrete detail: a real 8 GB laptop, a real failing .bat file, a real plugin
conflict. Blanking that out would leave generic questions that measure nothing.

So the transformation is narrow. It replaces IDENTIFIERS (paths, hosts, account
names, links) and personal possessive framing, and leaves every technical fact that
makes the question answerable:

    我的笔记本是 RTX 5060 Laptop 8GB 显存 + 32GB 内存
        -> 用户的笔记本是 RTX 5060 Laptop 8GB 显存 + 32GB 内存

Nothing technical changed; the question still has a definite answer, and it no
longer reads as somebody's private diary.

Usage:
  python runner/anonymize.py --check            # report what would change
  python runner/anonymize.py --apply            # rewrite the file (keeps a .bak)
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, "datasets", "_samples", "D9_realworld_full.jsonl")
NL = chr(10)

# --- identifier patterns: these could point at a specific person's machine ---
IDENTIFIERS = [
    (r"https?://127\.0\.0\.1:\d+", "http://<本地服务>:<端口>"),
    (r"https?://localhost:\d+", "http://<本地服务>:<端口>"),
    (r"https?://[\w.-]*bilibili\.com/\S*", "<视频链接>"),
    (r"BV[0-9A-Za-z]{10}", "<视频ID>"),
    (r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+", "<用户目录>"),
    (r"C:\\\\Users\\\\[^\\\\\s]+", "<用户目录>"),
    (r"[\w.+-]+@[\w-]+\.[\w.]{2,}", "<邮箱>"),
    (r"1[3-9]\d{9}", "<手机号>"),
    (r"sk-[A-Za-z0-9]{16,}", "<API密钥>"),
]

# --- possessive framing ---------------------------------------------------
# Rewrite to SECOND person, not third.
#
# The first attempt turned "我的笔记本" into "用户的笔记本", which reads as a case
# study rather than a request: "请根据用户的硬件评估可行性" is something a report says,
# not something a user asks. These questions are prompts TO a model, so the natural
# de-identified form keeps the direct address and drops only the ownership:
#
#     我的笔记本是 RTX 5060 8GB     ->  这台笔记本是 RTX 5060 8GB
#     请根据我的硬件评估            ->  请根据这台机器的配置评估
#
# Nothing technical is lost and the prompt still sounds like a person talking.
POSSESSIVE = [
    # longer forms first: a short rule would otherwise consume part of a long one
    ("我的笔记本是", "这台笔记本是"),
    ("我这台机器", "这台机器"),
    ("我的笔记本", "这台笔记本"),
    ("我的机器", "这台机器"),
    ("我的工作区", "当前工作区"),
    ("我的桌面软件", "一个桌面软件"),
    ("我的训练目标", "训练目标"),
    ("我的 AI 应用方向", "几个 AI 应用方向"),
    ("我的 DSH", "本机的 DSH"),
    ("我的环境", "运行环境"),
    ("根据我的硬件", "根据这台机器的配置"),
    ("我有 8GB", "这台机器有 8GB"),
    ("我有一段", "有一段"),
    ("我在找一个", "想找一个"),
    ("我在想", "在考虑"),
    ("我记不清", "记不清"),
    ("我打算", "打算"),
    ("我准备", "准备"),
    ("我装了", "装了"),
    ("我的", "这台机器的"),
    ("我在", "在"),
]

# --- product names that are fine to keep, but should not look like a shill ---
# (left in place deliberately: removing vendor names would make hardware questions
#  unanswerable, and naming a GPU is not private information)


def anonymize_text(t):
    if not t:
        return t
    out = t
    for pat, rep in IDENTIFIERS:
        out = re.sub(pat, rep, out)
    for src, dst in POSSESSIVE:
        out = out.replace(src, dst)
    # repair awkward artefacts the ordered replacements can still produce
    fixes = [
        (r"这台机器的这台机器的", "这台机器的"),
        (r"根据这台机器的配置的硬件", "根据这台机器的配置"),
        (r"请根据这台机器的配置评估", "请根据这台机器的配置评估"),
        (r"这台机器的笔记本", "这台笔记本"),
        (r"本机的本机的", "本机的"),
        (r"在在这", "在这"),
    ]
    for pat, rep in fixes:
        out = re.sub(pat, rep, out)
    return out


def scan(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    changes = []
    for r in rows:
        before = r.get("user") or ""
        after = anonymize_text(before)
        notes = []
        for pat, _ in IDENTIFIERS:
            if re.search(pat, before):
                notes.append("标识符:%s" % pat[:24])
        for src, _ in POSSESSIVE:
            if src in before:
                notes.append("人称:%s" % src)
                break
        if after != before:
            changes.append({"id": r.get("id"), "subcat": r.get("subcat"),
                            "notes": notes, "before": before, "after": after})
    return rows, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--file", default=TARGET)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print("找不到:", args.file)
        return 2

    rows, changes = scan(args.file)
    print("=" * 66)
    print("  D9 脱敏检查")
    print("=" * 66)
    print()
    print("  题目总数: %d" % len(rows))
    print("  需要改动: %d" % len(changes))
    print()

    if not changes:
        print("  没有需要改动的内容。")
        return 0

    for c in changes[:6]:
        print("  [%s] %s" % (c["id"], c["subcat"]))
        print("     原: %s" % c["before"][:96].replace(NL, " "))
        print("     新: %s" % c["after"][:96].replace(NL, " "))
        print("     原因: %s" % ", ".join(sorted(set(c["notes"]))[:3]))
        print()
    if len(changes) > 6:
        print("  ... 另有 %d 题" % (len(changes) - 6))
        print()

    if not args.apply:
        print("  加 --apply 执行改写(会先备份为 .bak)")
        return 0

    bak = args.file + ".bak"
    shutil.copy2(args.file, bak)
    n = 0
    with open(args.file, "w", encoding="utf-8") as f:
        for r in rows:
            if r.get("user"):
                new = anonymize_text(r["user"])
                if new != r["user"]:
                    r["user"] = new
                    n += 1
            # notes may also carry identifiers
            if r.get("notes"):
                r["notes"] = anonymize_text(r["notes"])
            f.write(json.dumps(r, ensure_ascii=False) + NL)
    print("  已改写 %d 题" % n)
    print("  备份: %s" % os.path.relpath(bak, ROOT))
    print()
    print("  提示:改写后请重新生成题库并重跑 D9 基准自检:")
    print("    python scripts/build_manifest.py")
    print("    python tools/d9_baseline.py --selfcheck")
    return 0


if __name__ == "__main__":
    sys.exit(main())
