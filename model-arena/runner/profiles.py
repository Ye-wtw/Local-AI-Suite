# -*- coding: utf-8 -*-
"""profiles.py - launch profiles: the user's real preset, plus derived variants.

A "profile" is one complete way to start a local model, captured as structured data
rather than a .bat file:

    profile = {
        exe, cwd, base_flags[],          # everything that does NOT vary
        model, mmproj,                    # the two paths that matter most
        vars: {"--ctx-size": "32768", ...}  # the flags a sweep may override
    }

Why structure it: a sweep has to start the server ~21 times with one flag changed
each time. Editing a .bat by hand that many times is how a config gets mistyped and
an hour of GPU time is spent measuring the wrong thing. Importing the preset once
means every variant is derived, not retyped.

Import is intentionally narrow: it reads the command line of the REAL preset and
keeps it verbatim, so the baseline is the configuration the user actually runs -
not a cleaned-up approximation of it.

Usage:
    python runner/profiles.py --import-bat <path.bat> --name apex-256k
    python runner/profiles.py --list
    python runner/profiles.py --show apex-256k
    python runner/profiles.py --variants apex-256k          # what a sweep would try
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

STORE = os.path.join(ROOT, "config", "profiles.json")
NL = chr(10)

# Flags that must never be overwritten by a sweep: the model identity and the
# vision tower define WHAT is being measured, not HOW it is configured.
PROTECTED = {"--model", "-m", "--mmproj", "--host", "--port"}


def _bat_cwd(path):
    """Read the `cd /d <dir>` a launcher performs before invoking the server.
    The exe lives in the bin directory, not next to the model, so without this a
    sweep cannot find llama-server.exe and stops dead."""
    rx = chr(92) + "s*cd" + chr(92) + "s+/d" + chr(92) + "s+" + chr(34) + "?([^" + chr(34) + chr(92) + "r" + chr(92) + "n]+)" + chr(34) + "?"
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(rx, line, re.I)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return None


def _strip_bat(path):
    """Pull one logical command line out of a .bat file.

    Handles the caret continuation style the user's launchers use, and ignores
    comments and echo lines.
    """
    lines = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for raw in f:
            s = raw.rstrip(NL)
            if s.strip().startswith("REM") or s.strip().startswith("::"):
                continue
            if s.strip().lower().startswith("echo"):
                continue
            lines.append(s)
    text = NL.join(lines)
    text = text.replace("^" + NL, " ")
    # find the invocation
    m = re.search(r"(llama-server(?:\.exe)?[^\r\n]*)", text, re.I)
    if not m:
        return None
    cmd = m.group(1)
    cmd = re.sub(r"\s+", " ", cmd).strip()
    cmd = cmd.replace("%ERRORLEVEL%", "").strip()
    return cmd


def parse_invocation(cmd):
    """Split a launch command into (exe, flags, model, mmproj, vars)."""
    toks = re.findall(r'"([^"]*)"|(\S+)', cmd or "")
    argv = [a or b for a, b in toks]
    exe = argv[0] if argv else "llama-server.exe"
    flags, i = [], 1
    model = mmproj = None
    while i < len(argv):
        t = argv[i]
        if t in ("--model", "-m") and i + 1 < len(argv):
            model = argv[i + 1]; i += 2; continue
        if t == "--mmproj" and i + 1 < len(argv):
            mmproj = argv[i + 1]; i += 2; continue
        flags.append(t)
        i += 1
    return exe, flags, model, mmproj


def resolve_exe(exe, cwd=None):
    """Find the actual executable. A launcher .bat does `cd /d <bin>` first, so the
    exe name alone is not enough - and a sweep that cannot find the binary stops
    dead rather than measuring the wrong thing."""
    import shutil as _sh
    if os.path.isabs(exe) and os.path.exists(exe):
        return exe
    if cwd:
        cand = os.path.join(cwd, exe)
        if os.path.exists(cand):
            return cand
    found = _sh.which(exe)
    if found:
        return found
    # search the known llama.cpp install used by this workspace
    base = os.path.join(os.path.dirname(ROOT), "00_models_Base", "llama.cpp", "bin")
    cand = os.path.join(base, exe)
    if os.path.exists(cand):
        return cand
    return exe


def build_profile(name, cmd, cwd=None, note=""):
    exe, flags, model, mmproj = parse_invocation(cmd)
    vars_ = {}

    def is_flag(tok):
        """A negative number is a VALUE, not a flag: --n-predict -1 is a common and
        correct llama.cpp argument, and treating -1 as a switch silently dropped it."""
        if not tok.startswith("-"):
            return False
        try:
            float(tok)
            return False
        except ValueError:
            return True

    i = 0
    while i < len(flags):
        t = flags[i]
        if is_flag(t) and i + 1 < len(flags) and not is_flag(flags[i + 1]):
            vars_[t] = flags[i + 1]
            i += 2
        elif is_flag(t):
            vars_[t] = True          # bare switch
            i += 1
        else:
            i += 1
    cwd = cwd or os.path.dirname(model or "") or "."
    return {
        "name": name,
        "exe": resolve_exe(exe, cwd),
        "exe_name": exe,
        "cwd": cwd,
        "model": model,
        "mmproj": mmproj,
        "flags": flags,             # verbatim, minus --model/--mmproj
        "vars": vars_,              # parsed view, what a sweep edits
        "raw": cmd,
        "note": note or "从启动器导入的完整预设",
    }


def load_store():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE, encoding="utf-8"))
        except Exception:
            pass
    return {"profiles": {}, "updated": None}


def save_store(store):
    import datetime
    store["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


def command_for(profile, overrides=None):
    """Build the argv for a profile with overrides applied.

    Overrides are applied to the PARSED vars and then rebuilt in place, so a flag's
    position in the original command line is preserved (llama.cpp does not care, but
    a human comparing two commands does).
    """
    overrides = overrides or {}
    argv = [profile["exe"], "--model", profile["model"]]
    if profile.get("mmproj"):
        argv += ["--mmproj", profile["mmproj"]]
    flags = list(profile.get("flags") or [])
    def set_flag(f, v):
        def _val_like(tok):
            if not tok.startswith("-"):
                return True
            try:
                float(tok); return True     # -1 is a value
            except ValueError:
                return False
        for idx, tok in enumerate(flags):
            if tok == f:
                if idx + 1 < len(flags) and _val_like(flags[idx + 1]):
                    flags[idx + 1] = str(v); return
                flags.insert(idx + 1, str(v)); return
            if tok.startswith(f + "="):
                flags[idx] = "%s=%s" % (f, v); return
        flags.extend([f, str(v)])
    for f, v in overrides.items():
        if f in PROTECTED:
            continue
        if v is False or v is None:
            # remove the flag entirely (and its value, if it took one)
            out, skip = [], False
            for idx, tok in enumerate(flags):
                if skip:
                    skip = False; continue
                if tok == f:
                    if idx + 1 < len(flags) and not flags[idx + 1].startswith("-"):
                        skip = True
                    continue
                out.append(tok)
            flags = out
            continue
        if v is True:
            if f not in flags:
                flags.append(f)
            continue
        set_flag(f, v)
    return argv + flags


def to_argv_tokens(profile, overrides=None):
    """Render as a list where only tokens containing spaces get quoted."""
    out = []
    for t in command_for(profile, overrides):
        s = str(t)
        out.append('"%s"' % s if (" " in s and not s.startswith('"')) else s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--import-bat", default="")
    ap.add_argument("--import-cmd", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", default="")
    ap.add_argument("--variants", default="")
    ap.add_argument("--argv", default="")
    args = ap.parse_args()

    store = load_store()

    if args.import_bat or args.import_cmd:
        if args.import_bat:
            cmd = _strip_bat(args.import_bat)
            if not cmd:
                print("未在 %s 中找到 llama-server 调用" % args.import_bat)
                return
            name = args.name or os.path.splitext(os.path.basename(args.import_bat))[0]
        else:
            cmd = args.import_cmd
            name = args.name or "imported"
        cwd = _bat_cwd(args.import_bat) if args.import_bat else None
        prof = build_profile(name, cmd, cwd=cwd, note=args.note)
        store["profiles"][name] = prof
        save_store(store)
        print("已导入配置档: %s" % name)
        print("  模型: %s" % os.path.basename(prof["model"] or "?"))
        print("  视觉: %s" % ("有" if prof["mmproj"] else "无"))
        print("  可扫描变量(%d 个):" % len([k for k in prof["vars"] if not k.startswith("--host")]))
        for k, v in sorted(prof["vars"].items()):
            print("    %-20s %s" % (k, v))
        return

    if args.list or (not args.show and not args.variants and not args.argv):
        print("已保存的配置档: %d 个" % len(store["profiles"]))
        for n, p in sorted(store["profiles"].items()):
            ctx = (p.get("vars") or {}).get("--ctx-size", "?")
            print("  %-24s ctx=%-8s %s" % (n, ctx, os.path.basename(p.get("model") or "")))
        if not store["profiles"]:
            print()
            print("导入你的启动器:")
            print("  python runner/profiles.py --import-bat <你的启动器.bat> --name my-config")
            print()
            print("  启动器里通常有一行 `cd /d <llama.cpp bin>` 和一行 llama-server 调用,")
            print("  本工具会从里面读出模型路径、exe 位置和全部参数。")
        return

    if args.show:
        p = store["profiles"].get(args.show)
        if not p:
            print("无此配置档:", args.show); return
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return

    if args.argv:
        p = store["profiles"].get(args.argv)
        if not p:
            print("无此配置档:", args.argv); return
        print(" ".join(to_argv_tokens(p)))
        return

    if args.variants:
        p = store["profiles"].get(args.variants)
        if not p:
            print("无此配置档:", args.variants); return
        import sweep as sw
        print("配置档 %s 的可扫描变体(模拟单变量变化):" % args.variants)
        print()
        n = 0
        for key, spec in sorted(sw.PARAMS.items(), key=lambda x: x[1]["priority"]):
            flag = spec["flag"][0]
            for val in spec["values"]:
                ov = {f: val for f in spec["flag"]}
                n += 1
                print("  %2d. %-14s=%-12s [%s]" % (n, flag, val, spec.get("affects")))
        print()
        print("  共 %d 个变体。用 --argv 加 overrides 可预览具体命令。" % n)


if __name__ == "__main__":
    main()
