# -*- coding: utf-8 -*-
"""detect.py - discover the user's machine and model instead of asking them to
hand-write numbers.

The first version of the sweep hard-coded this developer's laptop (8151 MiB VRAM,
48 layers, 8 KV heads) and a KV table measured on it. That is not a configurable
default, it is a wrong answer for everyone else: a 7B model would be sized as if it
had 48 layers, and stage 0 would silently drop combinations that run fine.

So everything specific is detected:

  GPU / RAM      from nvidia-smi and the OS
  llama.cpp      from PATH, a saved profile, or common install locations
  model geometry read from the GGUF header (block_count, head_count_kv, file size)

Detection is best-effort and reports what it could NOT find, because a wrong number
that looks authoritative is worse than a missing one.

Usage:
  python runner/detect.py                 # show what was detected
  python runner/detect.py --write         # write config/machine.json + models.json
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    from console import make_stdout_safe
    make_stdout_safe()
except Exception:
    pass

NL = chr(10)
MACHINE_CFG = os.path.join(ROOT, "config", "machine.json")
MODELS_CFG = os.path.join(ROOT, "config", "models.json")


def _ps(cmd, timeout=25):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, errors="ignore", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _run(argv, timeout=20):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="ignore", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def detect_gpu():
    """Every GPU with its VRAM. Multi-GPU is common enough to not assume one."""
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                gpus.append({"name": parts[0], "vram_mib": float(parts[1]),
                             "driver": parts[2] if len(parts) > 2 else None})
            except ValueError:
                continue
    if not gpus:
        # no nvidia-smi: still worth reporting, since AMD/Apple users exist
        return {"gpus": [], "total_vram_mib": 0, "vendor": None,
                "note": "未检测到 nvidia-smi;若使用 AMD/Apple 请手动填写 config/machine.json"}
    return {"gpus": gpus, "total_vram_mib": sum(g["vram_mib"] for g in gpus),
            "vendor": "nvidia"}


def detect_ram():
    ps = ("$os = Get-CimInstance Win32_OperatingSystem; "
          "[math]::Round($os.TotalVisibleMemorySize/1024/1024,2)")
    out = _ps(ps)
    try:
        return {"ram_gib": float(out)}
    except ValueError:
        pass
    # POSIX fallback
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return {"ram_gib": round(pages * size / 1024 ** 3, 2)}
    except Exception:
        return {"ram_gib": None}


def detect_llama():
    """Locate llama-server. Order: saved profile, PATH, then common installs."""
    found = []
    saved = os.path.join(ROOT, "config", "profiles.json")
    if os.path.exists(saved):
        try:
            store = json.load(open(saved, encoding="utf-8"))
            for name, p in (store.get("profiles") or {}).items():
                exe = p.get("exe")
                if exe and os.path.exists(exe):
                    found.append({"path": exe, "source": "profile:%s" % name})
        except Exception:
            pass

    which = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if which:
        found.append({"path": which, "source": "PATH"})

    candidates = [
        os.path.join(os.path.expanduser("~"), "llama.cpp", "build", "bin"),
        os.path.join(os.path.expanduser("~"), "llama.cpp", "bin"),
        "/usr/local/bin", "/opt/llama.cpp/build/bin",
        os.path.join(os.path.expanduser("~"), ".local", "bin"),
    ]
    for d in candidates:
        for exe in ("llama-server", "llama-server.exe"):
            p = os.path.join(d, exe)
            if os.path.exists(p) and not any(f["path"] == p for f in found):
                found.append({"path": p, "source": "common:%s" % d})

    # a sweep of the parent workspace is a last resort, clearly labelled
    parent = os.path.dirname(ROOT)
    for depth in (2, 3):
        pat = os.path.join(parent, *(["*"] * depth), "llama-server.exe")
        import glob as _g
        for p in _g.glob(pat)[:3]:
            if not any(f["path"] == p for f in found):
                found.append({"path": p, "source": "scan"})
    return found


# ---------------------------------------------------------------- GGUF
GGUF_MAGIC = b"GGUF"
# metadata value types -> (struct format, size)
_T = {0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2), 4: ("I", 4), 5: ("i", 4),
      6: ("f", 4), 7: ("?", 1), 10: ("Q", 8), 11: ("q", 8), 12: ("d", 8)}


def _read_str(f):
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", "ignore")


def _skip_value(f, vtype):
    """Advance past one metadata value without decoding it."""
    if vtype in _T:
        fmt, size = _T[vtype]
        f.read(size)
    elif vtype == 8:                       # string
        _read_str(f)
    elif vtype == 9:                       # array
        itype = struct.unpack("<I", f.read(4))[0]
        count = struct.unpack("<Q", f.read(8))[0]
        for _ in range(count):
            _skip_value(f, itype)
    else:
        raise ValueError("unknown gguf type %s" % vtype)


def _read_value(f, vtype):
    if vtype in _T:
        fmt, size = _T[vtype]
        return struct.unpack("<" + fmt, f.read(size))[0]
    if vtype == 8:
        return _read_str(f)
    raise ValueError("not a scalar")


def read_gguf_meta(path, limit_keys=None):
    """Parse a GGUF header into a dict of metadata.

    Only the header is read (the tensor data is skipped), so this stays fast even on
    a 17 GB file.
    """
    want = {
        "general.architecture": "arch",
        "general.name": "name",
        "general.file_type": "file_type",
    }
    meta = {}
    with open(path, "rb") as f:
        if f.read(4) != GGUF_MAGIC:
            return {}
        version = struct.unpack("<I", f.read(4))[0]
        n_tensors = struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]
        meta["_version"] = version
        meta["_n_tensors"] = n_tensors
        for _ in range(n_kv):
            key = _read_str(f)
            vtype = struct.unpack("<I", f.read(4))[0]
            if key in want:
                try:
                    meta[want[key]] = _read_value(f, vtype)
                except Exception:
                    _skip_value(f, vtype)
                continue
            # architecture-specific counts we care about
            # Match on the FINAL component only. An earlier pattern listed
            # "head_count" and "head_count_kv" separately but anchored with $, so
            # "attention.head_count" matched nothing and head_dim fell back to a
            # guess. Namespaced keys (qwen35.attention.head_count) are the norm.
            if re.search(r"\.(block_count|head_count_kv|head_count|embedding_length|"
                         r"context_length|attention\.head_count_kv|"
                         r"attention\.head_count|attention\.key_length|"
                         r"attention\.value_length)$", key):
                try:
                    meta[key] = _read_value(f, vtype)
                except Exception:
                    _skip_value(f, vtype)
                continue
            _skip_value(f, vtype)
    return meta


def model_geometry(gguf_path):
    """Turn GGUF metadata into the numbers the VRAM estimate needs."""
    meta = read_gguf_meta(gguf_path)
    if not meta:
        return None
    arch = meta.get("arch") or ""
    def pick(suffix):
        for k, v in meta.items():
            if k.endswith("." + suffix) and isinstance(v, (int, float)):
                return v
        return None
    layers = pick("block_count")
    kv_heads = pick("head_count_kv") or pick("attention.head_count_kv")
    heads = pick("head_count") or pick("attention.head_count")
    emb = pick("embedding_length")
    # key_length is authoritative when present; otherwise derive from embedding/heads
    head_dim = pick("attention.key_length") or pick("attention.value_length")
    if not head_dim and emb and heads:
        try:
            head_dim = int(emb) // int(heads)
        except ZeroDivisionError:
            head_dim = None
    # a head_dim that is not an integer means the derivation was wrong, not that the
    # model is exotic - KV sizing would be off by that fraction on every estimate
    if head_dim and float(head_dim) != int(head_dim):
        head_dim = None
    size_mib = os.path.getsize(gguf_path) / 1048576
    return {
        "path": gguf_path,
        "arch": arch,
        "name": meta.get("name"),
        "layers": layers,
        "kv_heads": kv_heads,
        "head_dim": head_dim,
        "weights_mib": round(size_mib),
        "_source": "gguf-header",
        "_missing": [k for k, v in (("layers", layers), ("kv_heads", kv_heads),
                                    ("head_dim", head_dim)) if not v],
    }


def find_ggufs(roots=None, limit=8):
    """Find model files near the workspace, so the user does not have to paste paths."""
    import glob as _g
    roots = roots or [os.path.dirname(ROOT)]
    out = []
    for r in roots:
        for depth in (3, 4):
            pat = os.path.join(r, *(["*"] * depth), "*.gguf")
            for p in _g.glob(pat):
                if "mmproj" in os.path.basename(p).lower():
                    continue
                try:
                    if os.path.getsize(p) < 100 * 1024 * 1024:
                        continue           # skip tiny test files
                except OSError:
                    continue
                out.append(p)
                if len(out) >= limit:
                    return sorted(set(out))
    return sorted(set(out))


def build_config(write=False, model_paths=None):
    gpu = detect_gpu()
    ram = detect_ram()
    llama = detect_llama()
    models = {}
    for p in (model_paths or []):
        try:
            geo = model_geometry(p)
        except Exception as e:
            geo = {"path": p, "_error": str(e)[:120]}
        if geo:
            models[os.path.basename(os.path.dirname(p)) or os.path.basename(p)] = geo

    machine = {
        "vram_mib": gpu["total_vram_mib"] or None,
        "ram_gib": ram.get("ram_gib"),
        "gpus": gpu["gpus"],
        "vendor": gpu["vendor"],
        "usable_vram_ratio": 0.95,
        "usable_ram_ratio": 0.75,
        "llama_server": llama[0]["path"] if llama else None,
        "llama_candidates": llama,
        "_notes": [
            "usable_vram_ratio 是可用比例;桌面/浏览器会占一部分显存。",
            "若你的配置实测能用到更高比例,可上调(见 runner/calibrate.py)。",
        ],
    }
    if write:
        os.makedirs(os.path.dirname(MACHINE_CFG), exist_ok=True)
        with open(MACHINE_CFG, "w", encoding="utf-8") as f:
            json.dump(machine, f, ensure_ascii=False, indent=1)
        with open(MODELS_CFG, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=1)
    return machine, models, gpu


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写入 config/machine.json")
    ap.add_argument("--model", action="append", default=[], help="额外指定 GGUF 路径")
    ap.add_argument("--scan", action="store_true", help="自动搜索附近的 GGUF")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = list(args.model)
    if args.scan or not paths:
        found = find_ggufs()
        if found:
            print("发现模型文件:")
            for p in found:
                print("  %.1f GiB  %s" % (os.path.getsize(p) / 1024 ** 3, p))
            print()
            paths = paths or found[:3]

    machine, models, gpu = build_config(write=args.write, model_paths=paths)

    if args.json:
        print(json.dumps({"machine": machine, "models": models}, ensure_ascii=False, indent=1))
        return

    print("=" * 62)
    print("  环境探测")
    print("=" * 62)
    print()
    print("显卡:")
    if gpu["gpus"]:
        for g in gpu["gpus"]:
            print("  %-34s %6.0f MiB  驱动 %s" % (g["name"], g["vram_mib"], g.get("driver") or "?"))
    else:
        print("  ⚠ 未检测到 NVIDIA GPU")
        print("    %s" % gpu.get("note", ""))
    print("内存: %s GiB" % (machine["ram_gib"] if machine["ram_gib"] else "未检测到"))
    print()
    print("llama.cpp:")
    if machine["llama_candidates"]:
        for c in machine["llama_candidates"][:4]:
            print("  %-58s (%s)" % (c["path"][:58], c["source"]))
    else:
        print("  ⚠ 未找到 llama-server,请在 config/machine.json 里填 llama_server")
    print()
    if models:
        print("模型几何(从 GGUF 头读取):")
        for name, g in models.items():
            if g.get("_error"):
                print("  %-28s 读取失败: %s" % (name[:28], g["_error"]))
                continue
            miss = g.get("_missing") or []
            print("  %-28s %s 层 / %s KV heads / head_dim %s / %.1f GiB"
                  % (name[:28], g.get("layers"), g.get("kv_heads"), g.get("head_dim"),
                     (g.get("weights_mib") or 0) / 1024))
            if miss:
                print("      ⚠ 未能从 GGUF 读到: %s —— 显存估算会退化为保守值" % ", ".join(miss))
    else:
        print("未指定模型。用 --scan 自动搜索,或 --model <路径.gguf>")

    if args.write:
        print()
        print("已写入:")
        print("  %s" % os.path.relpath(MACHINE_CFG, ROOT))
        print("  %s" % os.path.relpath(MODELS_CFG, ROOT))
    else:
        print()
        print("加 --write 保存为配置。")


if __name__ == "__main__":
    main()
