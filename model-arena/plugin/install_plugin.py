# -*- coding: utf-8 -*-
"""install_plugin.py - wire the Model Arena plugin into a DSH profile.

Deliberately conservative: DSH config is what makes the whole harness boot, so this
script

  * never edits an existing cordis.patch.yml in place without a timestamped backup,
  * is idempotent (running it twice does not add the plugin twice),
  * prints exactly what it will change before changing it, and
  * supports --dry-run and --uninstall.

A broken profile means DSH will not start at all, which is far worse than not having
the plugin, so every mutation is reversible and reported.

Usage:
  python plugin/install_plugin.py --dry-run
  python plugin/install_plugin.py --profile web
  python plugin/install_plugin.py --profile web --uninstall
"""
import argparse
import datetime
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NL = chr(10)

DSH_HOME = os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")
PLUGIN_SRC = os.path.join(HERE, "model-arena.mjs")
MARKER = "model-arena"


def profile_dir(profile):
    return os.path.join(DSH_HOME, "profiles", profile)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def backup(path):
    ts = datetime.datetime.now().strftime("%Y%m%dT%H-%M-%S")
    dst = "%s.bak-modelarena-%s" % (path, ts)
    shutil.copy2(path, dst)
    return dst


def already_installed(text):
    return text is not None and MARKER in text


def build_entry():
    """A cordis patch entry pointing at the .mjs file by absolute path.

    Absolute rather than a package name: this plugin is not published to npm, and a
    relative path would resolve against the profile directory, not the project.
    """
    return (
        "# --- Model Arena (added by plugin/install_plugin.py) ---" + NL +
        "plugins:" + NL +
        "  - name: model-arena" + NL +
        "    module: " + json.dumps(PLUGIN_SRC.replace("\\", "/")) + NL +
        "    config:" + NL +
        "      projectRoot: " + json.dumps(ROOT.replace("\\", "/")) + NL
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="web")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    pd = profile_dir(args.profile)
    if not os.path.isdir(pd):
        print("找不到 profile 目录: %s" % pd)
        print("可用的 profile:", ", ".join(
            d for d in (os.listdir(os.path.join(DSH_HOME, "profiles"))
                        if os.path.isdir(os.path.join(DSH_HOME, "profiles")) else [])
            if os.path.isdir(os.path.join(DSH_HOME, "profiles", d))))
        return 2

    target = os.path.join(pd, "cordis.patch.yml")
    text = read(target)
    print("profile:  %s" % pd)
    print("目标文件: %s" % target)
    print("现状:     %s" % ("文件不存在" if text is None else "%d 字节" % len(text)))
    print("已安装:   %s" % ("是" if already_installed(text) else "否"))
    print()

    if not os.path.exists(PLUGIN_SRC):
        print("插件源文件缺失: %s" % PLUGIN_SRC)
        return 3

    if args.uninstall:
        if not already_installed(text):
            print("未安装,无需卸载。")
            return 0
        lines = text.split(NL)
        out, skipping = [], False
        for line in lines:
            if line.startswith("# --- Model Arena"):
                skipping = True
                continue
            if skipping:
                # the block ends when indentation returns to column 0 on a new key
                if line and not line.startswith(" ") and not line.startswith("-"):
                    skipping = False
                else:
                    continue
            out.append(line)
        new_text = NL.join(out)
        print("将移除 Model Arena 块(%d 字节 -> %d 字节)" % (len(text), len(new_text)))
        if args.dry_run:
            print(NL + "(dry-run:未写入)")
            return 0
        b = backup(target)
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_text)
        print("已备份: %s" % b)
        print("已卸载。重启 DSH 生效。")
        return 0

    if already_installed(text):
        print("已安装,跳过(幂等)。")
        return 0

    entry = build_entry()
    print("将追加以下内容:")
    print("-" * 56)
    print(entry.rstrip())
    print("-" * 56)

    if args.dry_run:
        print(NL + "(dry-run:未写入)")
        return 0

    if text is None:
        new_text = entry
    else:
        sep = "" if text.endswith(NL) else NL
        new_text = text + sep + NL + entry
        b = backup(target)
        print(NL + "已备份: %s" % b)

    with open(target, "w", encoding="utf-8") as f:
        f.write(new_text)
    print("已写入 %d 字节" % len(new_text))
    print()
    print("下一步:")
    print("  1. 重启 DSH")
    print("  2. 在对话里应能看到 arena_status / arena_compare / arena_launch /")
    print("     arena_sweep / arena_run 五个工具")
    print()
    print("若 DSH 无法启动,恢复方式:")
    print("  把 cordis.patch.yml.bak-modelarena-* 复制回 cordis.patch.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
