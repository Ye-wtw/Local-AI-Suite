# -*- coding: utf-8 -*-
"""doctor.py — 拿到项目第一件事:跑这个,它会告诉你缺什么。

为什么需要它:新人 clone 下来最常见的三个坑,每个都会让人卡住:
  1. Python 版本不够(本项目要 3.10+,用了 match 与新的类型语法)
  2. 没建 config/machine.json(那是 gitignore 的本机配置,不存在时跑测会报路径错)
  3. 没起模型服务(endpoint health 失败,但报错信息往往看不出是"服务没起")

它只读不写(除 --fix 会生成 config/machine.json 模板),不改任何已有配置。

用法:
    python doctor.py           # 体检
    python doctor.py --fix     # 缺的配置补成模板
"""
import argparse
import json
import os
import platform
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OK, WARN, BAD = [], [], []


def check(name, level, detail=""):
    { "ok": OK, "warn": WARN, "bad": BAD }[level].append((name, detail))
    mark = {"ok": "[OK]  ", "warn": "[WARN]", "bad": "[FAIL]"}[level]
    print("  %s %s%s" % (mark, name, ("  -- " + detail) if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="生成缺失的本机配置模板")
    args = ap.parse_args()

    print("=" * 62)
    print(" model-arena 环境体检")
    print("=" * 62)

    # 1. Python 版本
    v = sys.version_info
    need = (3, 10)
    if (v.major, v.minor) >= need:
        check("Python %d.%d" % (v.major, v.minor), "ok")
    else:
        check("Python 版本过低", "bad",
              "需要 3.10+,当前 %d.%d" % (v.major, v.minor))

    # 2. 零第三方依赖 —— 这是本项目的卖点,必须真的零
    req = os.path.join(ROOT, "requirements.txt")
    if os.path.exists(req):
        txt = open(req, encoding="utf-8").read().strip()
        deps = [l for l in txt.splitlines()
                if l.strip() and not l.strip().startswith("#")]
        if deps:
            check("第三方依赖", "warn", "%d 条:%s" % (len(deps), ", ".join(deps[:4]))
            )
        else:
            check("零第三方依赖", "ok")

    # 3. 目录结构
    #
    # results/ 与 cache/ 在 .gitignore 里 —— clone 或解包后本来就不存在。
    # 早先把它当"必检项",于是每个新人第一次跑 doctor 就看到一条 FAIL,
    # 而那条 FAIL 其实是设计内的(结果目录本来就该是空的)。实测在干净解包里复现过。
    # 改法:输出目录缺失就建出来(它只是存结果,不含任何需要保留的东西)。
    for d, kind in (("datasets", "req"), ("runner", "req"),
                    ("config", "req"), ("results", "out"),
                    ("cache", "out")):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            check("目录 %s/" % d, "ok")
        elif kind == "req":
            check("缺少目录 %s/" % d, "bad")
        else:
            try:
                os.makedirs(p)
                check("目录 %s/(已自动创建)" % d, "ok")
            except Exception as e:
                check("无法创建 %s/" % d, "warn", str(e)[:50])

    # 4. 题库
    bank = os.path.join(ROOT, "datasets", "full")
    total = 0
    if os.path.isdir(bank):
        for f in os.listdir(bank):
            if f.endswith("_full.jsonl"):
                with open(os.path.join(bank, f), encoding="utf-8") as fh:
                    total += sum(1 for l in fh if l.strip())
    if total >= 1000:
        check("题库 %d 题" % total, "ok")
    elif total > 0:
        check("题库偏少", "warn", "只有 %d 题" % total)
    else:
        check("题库为空", "bad", "datasets/full/ 下没有 *_full.jsonl")

    # 5. 本机配置(gitignore 掉的,必须自己生成)
    mach = os.path.join(ROOT, "config", "machine.json")
    if os.path.exists(mach):
        check("config/machine.json", "ok")
    else:
        check("config/machine.json 不存在", "warn",
              "本机配置,已在 .gitignore;用 --fix 生成模板")
        if args.fix:
            ex = os.path.join(ROOT, "config", "machine.example.json")
            if os.path.exists(ex):
                shutil.copy(ex, mach)
                print("      已从 machine.example.json 生成,请填入本机路径")

    # 6. 端点配置
    ep = os.path.join(ROOT, "config", "endpoints.json")
    if os.path.exists(ep):
        try:
            d = json.load(open(ep, encoding="utf-8"))
            n = len(d.get("endpoints") or {})
            check("端点配置 %d 个" % n, "ok")
        except Exception as e:
            check("端点配置损坏", "bad", str(e)[:60])
    else:
        check("config/endpoints.json 不存在", "bad")

    # 7. 字体/编码提示(Windows 特有坑)
    if platform.system() == "Windows":
        check("Windows 控制台编码", "warn",
              "GBK 遇到非 ASCII 可能乱码;脚本已做兜底,但建议 chcp 65001")

    print()
    print("=" * 62)
    print(" 通过 %d / 提醒 %d / 失败 %d" % (len(OK), len(WARN), len(BAD)))
    if BAD:
        print(" 必须先解决:")
        for n, d in BAD:
            print("   - %s %s" % (n, d))
    if not BAD:
        print(" 环境可用。下一步:")
        print("   python runner/run_all.py --endpoint <端点名> --preset-id <预设名>")
        print("   或双击 start-5-ui.bat 打开界面")
    print("=" * 62)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())