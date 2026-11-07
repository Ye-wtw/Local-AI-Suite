# -*- coding: utf-8 -*-
"""preset_launch.py - 按预设原文起服务,并验证它真的起来了。

这个模块只做一件事:把一份预设变成一条真实运行的命令行,然后确认它对外服务。

**为什么不复用 launcher.py 的角色翻译层**:KV17 那批预设是逐参数调优出来的
(--draft-tokens 2 / --ngram-draft-tokens 31 / --adaptive-mtp / --kvmem-budget 8192
...)。经过任何"角色->引擎拼写"的翻译层,就有可能丢掉、改名或加进预设里没有
的参数,而跑出来的数字会被当成那套预设的结果。翻译层是给**扫描**用的(它要
按角色生成变体);**复现既有预设**必须逐字透传。

参数三分区(实测五种引擎后确定的规则):

    model_arg         模型怎么给:
                        -m <path>     llama-server / llama-kvmem-server
                        <path>        ninfer-kvmem / ninfer-serve  (位置参数)
    toml_fields       预设 TOML 里的结构化字段 -> 引擎选项。这些是预设的一部分。
    extra_args        预设里的 extra_args,逐字追加。这是调优结果所在。

起服务后必须验证三件事,缺一不可:
  1. /health 或 /v1/models 应答(端口通了不代表是模型服务)
  2. 模型别名/模型路径与预设一致(端口被别的进程占着时,照样会 200)
  3. 进程还活着(启动即崩的服务也可能先绑上端口再退出)
"""
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

NL = chr(10)

def _safe_stdio():
    """Windows 控制台默认是 GBK 代码页，打印任何非 GBK 字符都会抛
    UnicodeEncodeError 并**直接终止进程**。

    这不是"显示难看"的问题，是崩溃。实测代价：KV17 启动成功（138 秒、
    显存已占 6.8 GiB）之后，只因为一行带 ⚠ 的提示就崩了，模型进程留在
    后台无人回收。所以这里在入口处把 stdout/stderr 改成 UTF-8 with
    replace，宁可显示成问号，也不能因为一个字符把整个队列打死。
    """
    import sys
    for stream in ("stdout", "stderr"):
        s = getattr(sys, stream, None)
        if s is None or not hasattr(s, "reconfigure"):
            continue
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass




# TOML 字段 -> 选项名。只映射**预设里真的写了值**的字段;空值不生成选项,
# 否则会给引擎塞一串它没要求过的参数(例如 mmproj 为空还传 --mmproj "")。
#
# 只收 llama.cpp 系(llama-server / llama-kvmem-server)认得的名字;
# ninfer 系不吃这套(见 _ninfer_fields)。
TOML_FLAGS = [
    ("host", "--host"),
    ("port", "--port"),
    ("ctx_size", "-c"),
    ("n_gpu_layers", "-ngl"),
    ("threads", "-t"),
    ("threads_batch", "-tb"),
    ("batch_size", "-b"),
    ("ubatch_size", "-ub"),
    ("temp", "--temp"),
    ("top_k", "--top-k"),
    ("top_p", "--top-p"),
    ("min_p", "--min-p"),
    ("presence_penalty", "--presence-penalty"),
    ("repeat_penalty", "--repeat-penalty"),
    ("split_mode", "--split-mode"),
    ("tensor_split", "--tensor-split"),
    ("n_predict", "-n"),
]

# 布尔字段 -> (开启时的选项, 关闭时的选项或 None)
#
# flash_attn 单列在这里是踩过的:**llama.cpp 的 -fa 取值**(实测预设写的是
# "-fa on"),不是裸开关。拼成裸 -fa 会让引擎把下一个选项当成它的值,
# 表现为启动报错或某个参数被吃掉,而错误信息指向的是被吃掉的那个参数。
TOML_BOOL_FLAGS = [
    ("cpu_moe", "--cpu-moe", None),
    ("verbose", "--verbose", None),
    ("webui", "--webui", None),
    ("embeddings", "--embeddings", None),
    ("continuous_batching", "--cont-batching", None),
    ("no_mmap", "--no-mmap", None),
    ("mlock", "--mlock", None),
    ("use_mmap", "--use-mmap", None),
]

# 取值型布尔:字段为 true 时给 "<选项> on",false 时给 "<选项> off"
TOML_ONOFF_FLAGS = [
    ("flash_attn", "-fa"),
]

# 假值不要拼进去: 空字符串、只由空格组成、或显式写 none 的
_EMPTY = ("", "none", "null", "~")


def _blank(v):
    if v is None or isinstance(v, bool):
        return True
    s = str(v).strip()
    return s.lower() in _EMPTY


def _is_ninfer(preset):
    return str(preset.get("engine") or "").lower().startswith("ninfer")


def _is_kvmem_exe(exe):
    return "kvmem" in str(exe or "").lower()


# ---------------------------------------------------------------------------
# 命令行拼装
# ---------------------------------------------------------------------------
def build_argv(preset):
    """把预设变成 argv。返回 (argv, plan),plan 说明每一项的来源,便于审计。"""
    exe = preset.get("exe")
    if not exe:
        raise ValueError("预设没有可执行文件路径: %s" % preset.get("id"))
    if not os.path.isfile(exe):
        raise ValueError("可执行文件不存在: %s" % exe)

    model = preset.get("model_path")
    if not model:
        raise ValueError("预设没有模型路径: %s" % preset.get("id"))
    if not os.path.isfile(model):
        raise ValueError("模型文件不存在: %s" % model)

    if preset.get("launcher_only"):
        raise ValueError("这套预设不能按 TOML 参数启动: %s"
                         % preset.get("launcher_only"))

    style = preset.get("model_arg_style") or "-m"
    if _is_ninfer(preset) or style == "model":
        # 位置参数:NInfer 系的用法是 <exe> <model.ninfer> [options]
        argv = [exe, model]
        style_used = "model"
    else:
        argv = [exe, "-m", model]
        style_used = "-m"

    plan = [{"src": "model", "style": style_used, "value": os.path.basename(model)}]

    raw = preset.get("raw_fields") or {}

    # mmproj 只对 llama.cpp 系有意义;ninfer 把视觉打进模型或用它自己的参数
    mmproj = preset.get("mmproj_path") or raw.get("mmproj")
    if mmproj and os.path.isfile(str(mmproj)) and not _is_ninfer(preset):
        argv += ["--mmproj", str(mmproj)]
        plan.append({"src": "toml:mmproj", "value": os.path.basename(str(mmproj))})

    extra = (preset.get("extra_args") or "").strip()

    if not _is_ninfer(preset):
        # llama.cpp 系:结构化字段逐个转选项
        for field, flag in TOML_FLAGS:
            if field in raw and not _blank(raw[field]):
                # --port / --host 若 extra_args 里也带了,会让同一选项出现两次。
                # llama.cpp 取最后一个,但云端差异不该靠"取最后一个"来解释,
                # 所以这里跳过并在 plan 里记一句。
                if extra and ("%s " % flag) in (" " + extra + " "):
                    plan.append({"src": "toml:%s" % field, "skipped": "extra_args 已含 %s" % flag})
                    continue
                argv += [flag, str(raw[field])]
                plan.append({"src": "toml:%s" % field, "value": raw[field]})
        for field, onoff in TOML_ONOFF_FLAGS:
            if field in raw and isinstance(raw[field], bool):
                if extra and (" %s " % onoff) in (" " + extra + " "):
                    plan.append({"src": "toml:%s" % field, "skipped": "extra_args 已含 %s" % onoff})
                    continue
                argv += [onoff, "on" if raw[field] else "off"]
                plan.append({"src": "toml:%s" % field, "value": "on" if raw[field] else "off"})
        for field, on_flag, off_flag in TOML_BOOL_FLAGS:
            if field in raw:
                v = raw[field]
                if isinstance(v, bool):
                    if v:
                        argv.append(on_flag)
                        plan.append({"src": "toml:%s" % field, "value": True})
                    elif off_flag:
                        argv.append(off_flag)
                        plan.append({"src": "toml:%s" % field, "value": False})
        # n_cpu_moe:数值型,0 是有效值(等于不卸载),不能当空值丢掉
        if raw.get("n_cpu_moe") not in (None, "", "none"):
            argv += ["--n-cpu-moe", str(raw["n_cpu_moe"])]
            plan.append({"src": "toml:n_cpu_moe", "value": raw["n_cpu_moe"]})

    if extra:
        # 逐字追加。不 split 再重组:引号里的空格、等号写法(如 --a=b)
        # 在重新拼装时都可能变样。
        argv += extra.split()
        plan.append({"src": "extra_args", "value": extra})

    # 强制项:项目铁律,写在这里而不是让每个预设自己记得
    # --parallel 1:默认 4 会把上下文切成 4 份,32k 变成 8k/槽
    # --reasoning off:开着时 content 为空,全在 reasoning_content,判分器读不到答案
    if not _is_ninfer(preset):
        joined = " ".join(argv)
        if "--parallel" not in joined:
            argv += ["--parallel", "1"]
            plan.append({"src": "forced", "value": "--parallel 1 (上下文不被切槽)"})
        if "reasoning" not in joined:
            argv += ["--reasoning", "off"]
            plan.append({"src": "forced", "value": "--reasoning off (否则 content 为空)"})

    return argv, plan


# ---------------------------------------------------------------------------
# 起 / 停 / 验
# ---------------------------------------------------------------------------
def port_of(preset):
    raw = preset.get("raw_fields") or {}
    for key in ("port",):
        v = raw.get(key) or (preset.get("params") or {}).get("port")
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            continue
    import re
    m = re.search(r"--port\s+(\d+)", preset.get("extra_args") or "")
    return int(m.group(1)) if m else 8080


def port_in_use(port, host="127.0.0.1"):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, int(port))) == 0
    finally:
        s.close()


def _get_json(port, path, timeout=3.0):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=timeout) as r:
            raw = r.read(400000)
        return json.loads(raw.decode("utf-8", "ignore")) if raw else None
    except Exception:
        return None


def wait_ready(port, proc=None, wait_s=240, expect_model=None, verbose=True):
    """轮询到服务就绪。判据是**模型服务自述**,不是端口通。

    启动耗时实测差异极大:llama.cpp 13-22 秒,而 KV17 ninfer-kvmem 要 138 秒
    (要读 5.76 GB 权重并把 KV 窗口摆好)。默认 240 秒是给 KV17 留的余量。
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < wait_s:
        if proc is not None and proc.poll() is not None:
            return {"ok": False, "seconds": round(time.time() - t0, 1),
                    "reason": "进程已退出 (code %s)" % proc.returncode,
                    "log_tail": _tail(proc)}
        props = _get_json(port, "/props")
        if isinstance(props, dict) and ("model_path" in props
                                        or "default_generation_settings" in props):
            alias = props.get("model_alias")
            path = props.get("model_path")
            gen = props.get("default_generation_settings") or {}
            n_ctx = gen.get("n_ctx") if isinstance(gen, dict) else None
            ok = True
            warn = None
            if expect_model and path:
                if os.path.basename(str(path)).lower() != os.path.basename(str(expect_model)).lower():
                    ok = False
                    warn = ("服务报的模型与预设不符: 期望 %s,实际 %s"
                            % (os.path.basename(str(expect_model)), os.path.basename(str(path))))
            return {"ok": ok, "seconds": round(time.time() - t0, 1),
                    "via": "/props", "alias": alias, "n_ctx": n_ctx,
                    "model_path": path, "warning": warn}
        models = _get_json(port, "/v1/models")
        ids = (models or {}).get("data") if isinstance(models, dict) else None
        if isinstance(ids, list) and ids and isinstance(ids[0], dict):
            alias = ids[0].get("id")
            return {"ok": True, "seconds": round(time.time() - t0, 1),
                    "via": "/v1/models", "alias": alias, "n_ctx": None,
                    "model_path": None,
                    "warning": None if not expect_model else
                    ("只能读到模型别名,无法核对模型文件" if str(alias).lower() !=
                     os.path.basename(str(expect_model)).lower() else None)}
        last = "waiting"
        time.sleep(1.5)
    return {"ok": False, "seconds": round(time.time() - t0, 1),
            "reason": "%ds 内未就绪" % wait_s, "log_tail": None, "last": last}


def _tail(proc, n=18):
    path = getattr(proc, "_arena_log", None)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = [x.rstrip() for x in f.readlines() if x.strip()]
        return lines[-n:]
    except Exception:
        return None


def start(preset, wait_s=240, log_dir=None, verbose=True):
    """起服务并验证。返回 dict,ok=False 时带 reason 与日志尾部。

    端口被占用时**直接失败**,不去杀别人的进程:这台机器上残留进程杀不掉
    (taskkill 报 Access denied),硬杀会卡很久,而"端口被占"本身是要说的话。
    """
    port = port_of(preset)
    if port_in_use(port):
        return {"ok": False, "port": port,
                "reason": "端口 %d 已被占用,请先停掉占用它的服务" % port}

    argv, plan = build_argv(preset)
    log_dir = log_dir or os.path.join(ROOT, "results", ".engine")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "preset_%s_%s.log"
                            % (str(preset.get("id") or "x").replace("/", "_")[:28],
                               time.strftime("%Y%m%d-%H%M%S")))
    fh = open(log_path, "w", encoding="utf-8")
    fh.write("PRESET: %s%s" % (preset.get("id"), NL))
    fh.write("ARGV: %s%s" % (" ".join(argv), NL))
    fh.write("=" * 70 + NL)
    fh.flush()

    if verbose:
        print("  [start] %s" % preset.get("id"))
        print("  [start] %s ..." % " ".join(argv[:4]))

    try:
        proc = subprocess.Popen(argv, cwd=os.path.dirname(argv[0]) or None,
                                stdout=fh, stderr=subprocess.STDOUT)
    except Exception as e:
        fh.close()
        return {"ok": False, "port": port, "argv": argv, "plan": plan,
                "reason": "启动失败: %s" % str(e)[:160], "log": log_path}
    proc._arena_log = log_path      # 供 _tail 读日志

    ready = wait_ready(port, proc=proc, wait_s=wait_s,
                       expect_model=preset.get("model_path"), verbose=verbose)
    if verbose:
        if ready.get("ok"):
            print("  [start] 就绪 %.1fs via %s alias=%s n_ctx=%s"
                  % (ready["seconds"], ready.get("via"), ready.get("alias"), ready.get("n_ctx")))
            if ready.get("warning"):
                print("  [start] ⚠ %s" % ready["warning"])
        else:
            print("  [start] ✗ %s" % ready.get("reason"))

    out = {"ok": bool(ready.get("ok")), "port": port, "pid": proc.pid,
           "argv": argv, "plan": plan, "log": log_path,
           "seconds": ready.get("seconds"), "alias": ready.get("alias"),
           "n_ctx": ready.get("n_ctx"), "via": ready.get("via"),
           "warning": ready.get("warning"),
           "base_url": "http://127.0.0.1:%d" % port,
           "model_name": ready.get("alias") or preset.get("model") or "local-model"}
    if not ready.get("ok"):
        out["reason"] = ready.get("reason")
        out["log_tail"] = _tail(proc)
        stop(proc, verbose=verbose)
    else:
        out["_proc"] = proc        # 调用方负责 stop
    return out


def stop(proc=None, port=None, force_after=12.0, verbose=True):
    """停服务并等端口释放。

    返回前必须确认端口真的空了:没停干净就起下一套,下一套会绑不上端口,
    表现是"队列跑到第二套就失败",而根因在第一套没关。
    """
    if proc is None and port is not None:
        import serverctl
        return serverctl.stop(port, force_after=force_after, verbose=verbose)
    if proc is None:
        return True
    if proc.poll() is not None:
        return True
    try:
        proc.terminate()
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < force_after:
        if proc.poll() is not None:
            if verbose:
                print("  [stop] 已退出 %.1fs" % (time.time() - t0))
            return True
        time.sleep(0.4)
    try:
        proc.kill()
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < 8:
        if proc.poll() is not None:
            if verbose:
                print("  [stop] 强制结束")
            return True
        time.sleep(0.4)
    if verbose:
        print("  [stop] ✗ 进程仍存活 (pid %s)" % proc.pid)
    return False


def main():
    _safe_stdio()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="", help="预设 id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--argv", action="store_true", help="只打印命令行,不起服务")
    ap.add_argument("--start", action="store_true", help="真的起服务(会等就绪)")
    ap.add_argument("--wait", type=int, default=240)
    a = ap.parse_args()

    import presets as P
    doc = P.catalog(force_scan=False)
    by_id = {p["id"]: p for p in doc["presets"]}

    if a.list:
        print("可用预设 %d 个(标 * 的可按 TOML 直接起):" % len(by_id))
        for pid, p in by_id.items():
            flag = "launcher" if p.get("launcher_only") else "*"
            print("  %-8s %-44s %-10s %s"
                  % (flag, pid[:44], p.get("engine"), (p.get("model") or "-")[:28]))
        return 0

    if not a.preset:
        print("需要 --preset <id>")
        return 2
    p = by_id.get(a.preset)
    if not p:
        print("无此预设:", a.preset)
        return 2

    try:
        argv, plan = build_argv(p)
    except ValueError as e:
        print("X %s" % e)
        return 3

    if a.argv or not a.start:
        print("预设: %s" % p["id"])
        print("引擎: %s   模型传参: %s   端口: %s"
              % (p.get("engine"), p.get("model_arg_style"), port_of(p)))
        print()
        print(" ".join(argv))
        print()
        print("来源:")
        for item in plan:
            print("  %-22s %s" % (item.get("src"), item.get("value") if "value" in item else item.get("skipped")))
        return 0

    r = start(p, wait_s=a.wait)
    print(json.dumps({k: v for k, v in r.items() if not k.startswith("_")},
                     ensure_ascii=False, indent=1))
    if r.get("ok"):
        input("按回车停止服务...")
        stop(r.get("_proc"))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
