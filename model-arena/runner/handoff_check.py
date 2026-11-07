# -*- coding: utf-8 -*-
"""handoff_check.py - 交接检查清单，一条命令验证项目状态。

新会话接手时先跑这个。它只做只读检查，不改任何东西。
任何一项失败，说明环境或代码有问题，先修再改代码。

Usage:
  python runner/handoff_check.py
"""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OK = "  OK   "
BAD = "  FAIL "
WARN = "  WARN "


def _run(args):
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return -1, str(e)


def main():
    fails = []
    warns = []

    print("=" * 62)
    print("  交接检查 · model-arena")
    print("=" * 62)
    print()

    # 1. 编译
    print("[1] 模块编译")
    py = sorted(glob.glob(os.path.join(HERE, "*.py")) +
                glob.glob(os.path.join(ROOT, "scripts", "*.py")))
    bad = []
    for f in py:
        rc, out = _run([sys.executable, "-m", "py_compile", f])
        if rc != 0:
            bad.append(os.path.basename(f))
    if bad:
        print(BAD + "%d 个文件编译失败: %s" % (len(bad), ", ".join(bad[:5])))
        fails.append("compile")
    else:
        print(OK + "%d 个文件全部通过" % len(py))

    # 2. 题库自检
    print()
    print("[2] 题库结构")
    rc, out = _run([sys.executable, os.path.join(HERE, "selfcheck.py"), "--strict"])
    if rc != 0:
        print(BAD + "自检发现问题（跑 selfcheck.py 看详情）")
        fails.append("selfcheck")
    else:
        import re
        m = re.search(r"findings:\s*(\d+)", out)
        print(OK + ("findings=%s" % m.group(1) if m else "通过"))

    # 3. 文档命令
    print()
    print("[3] 文档命令核对")
    rc, out = _run([sys.executable, os.path.join(HERE, "doccheck.py"), "--strict"])
    if rc != 0:
        print(BAD + "文档里有失效命令")
        fails.append("doccheck")
    else:
        print(OK + "no broken commands")

    # 4. 判分器版本
    print()
    print("[4] 判分器版本")
    try:
        import judge_version as JV
        for k, v in JV.all_versions().items():
            print("        %-8s %s" % (k, v))
        print(OK + "版本可读")
    except Exception as e:
        print(BAD + "版本读取失败: %s" % str(e)[:60])
        fails.append("judge_version")

    # 5. 题库与套件
    print()
    print("[5] 题库与套件")
    try:
        total = 0
        for f in glob.glob(os.path.join(ROOT, "datasets", "full", "*.jsonl")):
            total += sum(1 for line in open(f, encoding="utf-8") if line.strip())
        pub = os.path.join(ROOT, "datasets", "subsets", "public_balanced.jsonl")
        pubn = sum(1 for line in open(pub, encoding="utf-8") if line.strip()) if os.path.exists(pub) else 0
        print("        自建 %d + 公开 %d = %d" % (total, pubn, total + pubn))
        subs = glob.glob(os.path.join(ROOT, "datasets", "subsets", "*.jsonl"))
        print("        套件 %d 个" % len(subs))
        if total == 0 or pubn == 0:
            print(BAD + "题库为空")
            fails.append("datasets")
        else:
            print(OK + "题库就绪")
    except Exception as e:
        print(BAD + "题库读取失败: %s" % str(e)[:60])
        fails.append("datasets")

    # 6. 配置文件
    print()
    print("[6] 配置")
    for name in ("endpoints.json", "targets.json"):
        p = os.path.join(ROOT, "config", name)
        if not os.path.exists(p):
            print(WARN + "config/%s 不存在" % name)
            warns.append(name)
            continue
        try:
            json.load(open(p, encoding="utf-8"))
            print(OK + "config/%s 可解析" % name)
        except Exception as e:
            print(BAD + "config/%s 解析失败: %s" % (name, str(e)[:50]))
            fails.append(name)

    # 7. 密钥不落盘
    print()
    print("[7] 密钥安全")
    # Match a REAL key shape, not the substring "sk-". Every one of these files
    # legitimately contains sk-: the docs show sk-xxxx as a placeholder, two
    # modules default to sk-local for local servers, and anonymize.py carries the
    # redaction regex itself. Flagging those trains people to ignore the check.
    import re as _re
    KEY_RE = _re.compile(r"sk-[A-Za-z0-9]{20,}")
    leaked = []
    for pat in ("config/*.json", "*.md", "*.html", "runner/*.py"):
        for f in glob.glob(os.path.join(ROOT, pat)):
            try:
                s = open(f, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for m in KEY_RE.finditer(s):
                tok = m.group(0)
                # a documented placeholder is not a secret
                if tok.lower().startswith(("sk-xxxx", "sk-test", "sk-your")):
                    continue
                leaked.append("%s(%s...)" % (os.path.basename(f), tok[:8]))
    if leaked:
        print(BAD + "疑似明文密钥: %s" % ", ".join(leaked[:5]))
        fails.append("secret")
    else:
        print(OK + "未发现明文密钥")

    # 8. 成本
    print()
    print("[8] 成本估算")
    try:
        import cost as C
        s = C.summary()
        print("        全量 %d 题  %.2f 元" % (s["total_n"], s["total_cost_cny"]))
        print("        不含 D7    %.2f 元" % s["without_d7_cost_cny"])
        print("        D7        %.2f 元" % s["d7_cost_cny"])
        print(OK + "成本可估算")
    except Exception as e:
        print(WARN + "成本估算失败: %s" % str(e)[:50])
        warns.append("cost")

    # 汇总
    print()
    print("=" * 62)
    if fails:
        print("  结论：%d 项失败 -> %s" % (len(fails), ", ".join(fails)))
        print("  先修这些，再改代码。")
        return 1
    if warns:
        print("  结论：通过，%d 项警告 -> %s" % (len(warns), ", ".join(warns)))
        return 0
    print("  结论：全部通过，可以开始开发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())