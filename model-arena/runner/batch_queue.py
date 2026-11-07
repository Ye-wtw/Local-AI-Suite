# -*- coding: utf-8 -*-
"""batch_queue.py - 按顺序把多套预设跑完,每套起服务→评测→停服务→记性能。

编排的难点不在"循环调用",而在四件事:

1. **上一套必须真的停下**,否则下一套绑不上端口。表现是"第二套起不来",
   根因在第一套没关干净。所以 stop 之后要验证端口真的空了。
2. **失败了要能继续**。实测有预设会因为显存不够而启动失败
   (KV17 文档原话:"--draft-tokens 6 启动失败(显存不够)")。这类失败本身
   就是结论,不该中断整个队列。
3. **每套都要带证据**:起服务的真实 argv、就绪耗时、性能采样、评测结果。
   没有 argv 的分数不可复现,没有采样的报告答不了"能不能同时开别的程序"。
4. **长任务要能续**。一套 4 小时,四套就是一整夜;中途崩了不能全部重来。

队列状态写在 results/.queue/<run_id>.json:每完成一项就落盘,所以进程被杀
之后能看出跑到哪,并用 --resume 接着跑。
"""
import datetime
import glob
import json
import os
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



QUEUE_DIR = os.path.join(ROOT, "results", ".queue")

# 套件 -> 会跑哪些维度。用于"这套跑完了吗"的判断,也用于成本/时间预估。
SUITE_DIMS = {
    "canary": ["D2", "D3", "D4", "D6"],
    "smoke": ["D2", "D3"],
    "full": ["D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10"],
    "public": ["public"],
    "agent": ["D6"],
    "reliability": ["D8"],
    "realworld": ["D9"],
    "longctx": ["D7"],
    "lcb": ["lcb"],
    "mcq": ["D2"],
    "math": ["D3"],
    "code": ["D4"],
    "instruction": ["D5"],
}


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def state_path(run_id):
    return os.path.join(QUEUE_DIR, "%s.json" % run_id)


def save_state(state):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    tmp = state_path(state["run_id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
        f.write(NL)
    # 原子替换:进程被杀时留在半截文件里的状态,比没有状态更糟 ——
    # 读出来会像"跑到一半"而实际什么都没跑完。
    os.replace(tmp, state_path(state["run_id"]))
    return state_path(state["run_id"])


def load_state(run_id):
    p = state_path(run_id)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_states():
    out = []
    for p in sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        out.append(d)
    return out


def new_state(presets, suites, tag="", limit=None):
    return {
        "run_id": "q_%s%s" % (_stamp(), ("_" + tag) if tag else ""),
        "created": _now(),
        "updated": _now(),
        "presets": list(presets),
        "suites": list(suites),
        "limit": limit,
        "items": [],
        "finished": False,
    }


def _run_body(preset, suite, item, proc, port, base_url, limit=None, tag="queue"):
    """评测 + 采样 + 停服务。由 _run_one 用 try 包住调用,保证异常时进程被清理。"""
    import preset_launch as L
    import sampler as S

    # --- 2. 评测(同时采性能) ---
    #
    # 端点在这里写:服务已经就绪,started 里带着它自述的 alias —— 那才是
    # /v1/chat/completions 认的 model 名。实测:
    #   model="kv17-bonsai2t5"              -> 200 OK
    #   model="Bonsai2-T5-MTP-r3-Q4.ninfer" -> 404 model_not_found(文件名,服务不认)
    # 预设里的 model 是给人看的文件名,不能直接当 API 的 model 用。
    if base_url:
        alias = (item.get("launch") or {}).get("alias")
        ensure_endpoint("queue", base_url,
                        alias or _endpoint_model_name(preset))
    # pid 必须是**模型服务的进程**,不是本进程:采 runner 自己得到 17 MiB 的
    # 数字毫无意义。这一条是实测踩出来的 —— 不传 pid 时 stats 里全是 runner 的
    # 内存,看起来有数据,读起来是错的。
    smp = S.Sampler(interval_s=5.0, pid=(proc.pid if proc is not None else None))
    smp.start()
    item["steps"].append({"step": "sample_start", "ok": True, "at": _now()})
    try:
        # -u 是必须的:run.py 的 stdout 被重定向到文件时是块缓冲,
        # 不刷盘就看不到任何进度,表现和"卡死"完全一样 —— 实测因此误停过
        # 一个正在正常生成的任务。加 -u 后每行立即落盘。
        cmd = [sys.executable, "-u", os.path.join(HERE, "run.py"),
               "--endpoint", "queue",  # 由 endpoints 注入,见 ensure_endpoint
               "--suite", suite, "--tag", tag, "--yes"]
        # 预设身份必须传给子进程:多套预设共用同一个别名时,结果文件里的
        # model 字段完全相同,只有这个字段能区分谁是谁。
        if preset.get("id"):
            cmd += ["--preset-id", str(preset["id"])]
        if limit:
            cmd += ["--limit", str(int(limit))]
        if base_url and ("127.0.0.1" in base_url or "localhost" in base_url):
            cmd.append("--skip-vram-check")

        log_dir = os.path.join(ROOT, "results", ".queue")
        os.makedirs(log_dir, exist_ok=True)
        safe_id = str(preset.get("id") or "x").replace("/", "_").replace("\\", "_")[:40]
        log_path = os.path.join(log_dir, "%s_%s.log" % (safe_id, suite))
        item["run_log"] = log_path
        item["cmd"] = cmd

        t1 = time.time()
        # encoding 必须是 utf-8:子进程的输出里有中文与特殊字符,
        # 用默认编码写入会让整个队列在评测中途崩掉。
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("PRESET %s%s" % (preset.get("id"), NL))
            fh.write("SUITE  %s%s" % (suite, NL))
            fh.write("BASE   %s%s" % (base_url, NL))
            fh.write("=" * 70 + NL)
            fh.flush()
            proc_run = subprocess.Popen(cmd, cwd=ROOT, stdout=fh,
                                        stderr=subprocess.STDOUT)
            code = proc_run.wait()
        item["run_seconds"] = round(time.time() - t1, 1)
        item["run_exit"] = code
        item["steps"].append({"step": "run", "ok": code == 0,
                              "at": _now(), "detail": "exit %s" % code})
        if code != 0:
            item["status"] = "run_failed"
            item["error"] = "run.py exit %s(端点:%s)" % (code, base_url)
    except Exception as e:
        item["status"] = "run_failed"
        item["error"] = "评测异常: %s" % str(e)[:160]
    finally:
        stats = smp.stop()
        item["performance"] = stats
        item["steps"].append({"step": "sample_stop", "ok": bool(stats.get("ok")),
                              "at": _now(),
                              "detail": None if stats.get("ok") else stats.get("error")})

    # --- 3. 停服务并确认端口释放 ---
    if preset.get("external"):
        # 外部服务(测试注入的 mock、或用户自己起的):不启动也不停止。
        # 停别人的服务是越界,而且用户的服务下次还要用。
        item["steps"].append({"step": "stop", "ok": True, "at": _now(),
                              "detail": "外部服务,未停止"})
        if item["status"] == "running":
            item["status"] = "done" if item.get("run_exit") == 0 else "run_failed"
        item["finished"] = _now()
        return item
    L.stop(proc, verbose=True)
    still = L.port_in_use(port) if port else False
    item["steps"].append({"step": "stop", "ok": not still, "at": _now(),
                          "detail": None if not still else "端口 %s 仍被占用" % port})
    if still:
        item["stop_warning"] = "端口 %s 仍被占用,下一套可能起不来" % port
        try:
            import serverctl
            serverctl.stop(port, verbose=True)
        except Exception:
            pass

    if item["status"] == "running":
        item["status"] = "done" if item.get("run_exit") == 0 else "run_failed"
    item["finished"] = _now()
    return item


def _run_one(preset, suite, limit=None, wait_s=240, tag="queue", on_log=None):
    """跑一套预设的一个套件。返回 dict(含每步证据)。

    每次调用都自带起停:不起服务就没有"这套预设"的语境;共用服务则一旦
    端口上的东西被换掉,后面的结果就归错了。
    """
    import preset_launch as L
    import sampler as S

    item = {"preset": preset.get("id"), "suite": suite, "started": _now(),
            "status": "running", "steps": [], "error": None}

    # --- 1. 起服务 ---
    t0 = time.time()
    if preset.get("external"):
        # 已经在跑的服务(测试注入的 mock):不启动,也不接管进程。
        started = {"ok": True, "port": None, "pid": preset.get("pid"),
                   "base_url": preset.get("base_url"),
                   "alias": preset.get("model"), "via": "external",
                   "seconds": 0.0, "warning": None, "_proc": None}
    else:
        started = L.start(preset, wait_s=wait_s, verbose=True)
    item["launch"] = {k: v for k, v in started.items()
                      if k not in ("_proc", "plan", "argv") and not k.startswith("_")}
    item["launch"]["argv"] = started.get("argv")
    item["launch"]["plan"] = started.get("plan")
    item["launch_seconds"] = round(time.time() - t0, 1)
    item["steps"].append({"step": "launch", "ok": bool(started.get("ok")),
                          "at": _now(), "detail": started.get("reason")})
    if not started.get("ok"):
        item["status"] = "launch_failed"
        item["error"] = started.get("reason")
        item["log_tail"] = started.get("log_tail")
        return item

    proc = started.get("_proc")
    port = started.get("port")
    # base_url 以"起服务返回的"为准:那是真正在服务的地址。
    # 预设里写的可能是给别的用途看的,与实际监听不一致时,应以实际为准。
    base_url = started.get("base_url") or preset.get("base_url") or ""

    # 从这里往下握住进程:后面任何一处抛异常(包括一行 print 编码失败),
    # 都必须先把模型进程杀掉再往外抛。
    #
    # 实测事故:KV17 启动成功(138 秒、显存已占 6.8 GiB)之后,只因为打印提示
    # 里的一个字符编码失败就崩了,进程留在后台继续占着显卡 —— 用户看到的是
    # "跑了一下,显卡还是满的,但队列里什么都没有"。清理必须无条件执行。
    try:
        return _run_body(preset, suite, item, proc, port, base_url,
                         limit=None if limit is None else limit, tag=tag)
    except BaseException as exc:
        item["status"] = "exception"
        item["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        item["steps"].append({"step": "exception", "ok": False, "at": _now(),
                              "detail": item["error"]})
        try:
            L.stop(proc, verbose=False)
        except Exception:
            pass
        # 端口仍被占(进程树没杀干净)时再补一刀
        try:
            if port and L.port_in_use(port):
                import serverctl
                serverctl.stop(port, verbose=False)
        except Exception:
            pass
        item["orphan_cleaned"] = True
        item["finished"] = _now()
        raise

    return item   # 正常路径由 _run_body 返回


def _endpoint_model_name(preset):
    """服务端认的模型 id。

    注意这不是"模型文件叫什么":llama-server 认文件名(llama.cpp 的 alias 默认
    是路径),而 ninfer 认 --model-id 注册的名字。两者不同,写错就整批 404。
    """
    import re
    # 1) 预设显式声明
    for key in ("model_id", "alias"):
        v = (preset.get("params") or {}).get(key) or (preset.get("raw_fields") or {}).get(key)
        if v and str(v).strip():
            return str(v).strip()
    # 2) extra_args 里的 --model-id / --alias
    extra = preset.get("extra_args") or ""
    for flag in ("--model-id", "--alias"):
        m = re.search(r"%s\s+(\S+)" % re.escape(flag), extra)
        if m:
            return m.group(1).strip().strip('"')
    # 3) 起服务后服务自述的 alias(由调用方在 launch 之后补写,见 run())
    return preset.get("model") or "local-model"


def ensure_endpoint(name, base_url, model):
    """把一个临时端点写进 config/endpoints.json,供 run.py --endpoint 使用。

    run.py 只认配置文件里的端点名,没有"临时 base_url"入口。与其改 run.py
    的加载逻辑(那会影响所有已有调用),不如在这里维护一个专门的端点条目
    "queue":每次跑之前覆盖它。副作用是 endpoints.json 会被改写 —— 所以
    先备份,并在原文件里保留 _queue_note 说明它是被自动维护的。
    """
    path = os.path.join(ROOT, "config", "endpoints.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("endpoints", {})
    cfg["endpoints"][name] = {
        "base_url": base_url,
        "model": model,
        "timeout": 1800,
        "extra": {"temperature": 0},
        # 本地服务:明确标 free,别让成本门禁把它当付费端点
        "cost": "free",
        "engine": "openai-compatible",
        "_auto": "由 runner/batch_queue.py 在批量评测时自动维护,不要手改",
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write(NL)
    os.replace(tmp, path)


def run(presets, suites, limit=None, tag="queue", resume=None, wait_s=240,
        on_item=None, stop_event=None, verbose=True, preset_specs=None):
    """跑一整个队列。返回 (state, report_paths)。

    顺序即 presets 列表顺序 × suites 列表顺序。用户手动排的顺序就是执行顺序:
    "先跑哪个"是有意义的(显存最紧的放最前面,失败时省时间)。

    preset_specs 用于注入预设定义(验证调度用):给了它就不去扫真实目录。
    验证编排逻辑和验证模型能力是两件事,前者不该占用 GPU。
    """
    import presets as P
    if preset_specs is not None:
        by_id = dict(preset_specs)
    else:
        doc = P.catalog(force_scan=True)
        by_id = {p["id"]: p for p in doc["presets"]}

    missing = [x for x in presets if x not in by_id]
    if missing:
        # 静默跳过会让"排了 4 套只跑了 3 套"变成谜。直接拒绝并列出。
        raise ValueError("这些预设不存在: %s" % ", ".join(missing))

    state = None
    if resume:
        state = load_state(resume)
        if not state:
            raise ValueError("找不到队列状态: %s" % resume)
        if verbose:
            done = [i for i in state["items"] if i.get("status") in ("done", "run_failed", "launch_failed")]
            print("[resume] %s 已有 %d 项记录,跳过已完成的" % (resume, len(done)))
    if state is None:
        state = new_state(presets, suites, tag=tag, limit=limit)
        save_state(state)

    done_keys = {(i["preset"], i["suite"]) for i in state["items"]
                 if i.get("status") in ("done", "run_failed", "launch_failed")}

    total = len(presets) * len(suites)
    n = 0
    for pid in presets:
        preset = by_id[pid]
        for suite in suites:
            n += 1
            if (pid, suite) in done_keys:
                if verbose:
                    print("[%d/%d] 跳过(已完成) %s @ %s" % (n, total, pid, suite))
                continue
            if stop_event is not None and stop_event.is_set():
                state["stopped"] = "用户停止"
                state["updated"] = _now()
                save_state(state)
                if verbose:
                    print("[stop] 用户停止,已完成 %d 项" % len(state["items"]))
                return state, []

            if verbose:
                print(NL + "[%d/%d] %s @ %s" % (n, total, pid, suite))
            # 端点必须在评测前写好:run.py 只认配置文件里的端点名。
            #
            # 已自带 base_url 的(测试注入的 mock、或用户自己起的服务)优先用它;
            # 否则按预设端口推。**只看预设端口是不够的**:外部服务的端口不在
            # TOML 里,推出来的会是默认 8080,于是 run.py 去连一个空端口并报
            # "UNREACHABLE" —— 看起来像队列坏了,其实是端点写错了。
            base_url = preset.get("base_url") or ""
            if not base_url:
                port = 0
                try:
                    import preset_launch as L
                    port = L.port_of(preset)
                except Exception:
                    pass
                if port:
                    base_url = "http://127.0.0.1:%d" % port
            # 端点**不在这里写**:此时服务还没起来,更没读到它的 /v1/models,
            # 写进去的 model 名可能不是服务认的那个(实测过 404 model_not_found)。
            # 改到 _run_body 里服务就绪之后、评测开始之前写。

            item = _run_one(preset, suite, limit=limit, wait_s=wait_s,
                            tag=tag); 
            state["items"].append(item)
            state["updated"] = _now()
            save_state(state)
            if on_item:
                try:
                    on_item(item)
                except Exception:
                    pass
            if verbose:
                print("  -> %s%s" % (item["status"],
                                     (" | %s" % item.get("error")) if item.get("error") else ""))

    state["finished"] = True
    state["updated"] = _now()
    save_state(state)
    return state, []


def summary_table(state):
    """队列结果的一行一套(套件内合并)。给人看的摘要,不是报告。"""
    rows = {}
    for it in state.get("items") or []:
        key = it.get("preset") or "?"
        r = rows.setdefault(key, {"suites": [], "status": it.get("status"),
                                  "launch_s": it.get("launch_seconds")})
        r["suites"].append((it.get("suite"), it.get("status"),
                            (it.get("launch") or {}).get("alias")))
    return rows


def main():
    _safe_stdio()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default="", help="逗号分隔的预设 id,顺序即执行顺序")
    ap.add_argument("--suites", default="canary", help="逗号分隔的套件")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default="queue")
    ap.add_argument("--wait-s", type=int, default=240, help="单套起服务等待上限")
    ap.add_argument("--resume", default="")
    ap.add_argument("--status", default="", help="查看某个队列状态")
    ap.add_argument("--list-presets", action="store_true")
    a = ap.parse_args()

    if a.list_presets:
        import presets as P
        doc = P.catalog(force_scan=False)
        print("可选预设 %d 个:" % len(doc["presets"]))
        for p in doc["presets"]:
            flag = "launcher" if p.get("launcher_only") else "ok"
            print("  %-8s %-44s %-10s %s"
                  % (flag, p["id"][:44], p.get("engine"),
                     (p.get("model") or "-")[:26]))
        return 0

    if a.status:
        st = load_state(a.status)
        if not st:
            print("无此队列:", a.status)
            return 2
        print("队列 %s  创建 %s  更新 %s" % (st["run_id"], st["created"], st["updated"]))
        print("预设: %s" % ", ".join(st["presets"]))
        print("套件: %s" % ", ".join(st["suites"]))
        print()
        for it in st["items"]:
            perf = it.get("performance") or {}
            st_ = perf.get("stats") or {}
            gpu = (st_.get("gpu_mem_used_mib") or {}).get("max")
            util = (st_.get("gpu_util_pct") or {}).get("mean")
            rss = (st_.get("proc_rss_mib") or {}).get("max")
            n = perf.get("n_samples")
            print("  %-34s %-10s %-14s 启动 %-6ss 样本 %-4s 显存峰 %-7s GPU均值 %-6s RSS峰 %s"
                  % (str(it.get("preset"))[:34], str(it.get("suite")),
                     it.get("status"), it.get("launch_seconds"), n if n else 0,
                     ("%sMiB" % gpu) if gpu is not None else "-",
                     ("%s%%" % round(util, 1)) if util is not None else "-",
                     ("%sMiB" % rss) if rss is not None else "-"))
        return 0

    if not a.presets:
        print("需要 --presets。用 --list-presets 看有哪些。")
        return 2
    presets = [x.strip() for x in a.presets.split(",") if x.strip()]
    suites = [x.strip() for x in a.suites.split(",") if x.strip()]
    state, _ = run(presets, suites, limit=a.limit, tag=a.tag,
                   resume=a.resume or None, wait_s=a.wait_s)
    print(NL + "队列结束: %s" % state["run_id"])
    print("状态文件: %s" % os.path.relpath(state_path(state["run_id"]), ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
