# -*- coding: utf-8 -*-
"""abtest.py - A/B 对照实验引擎(竖切)。

一条命令跑完 A/B 两套配置 × 105 题 × 四维度,产出可以做配对检验的结果文件。

为什么不是再写一个 run.py:
  run.py 已经拥有题库装载、按维度构造请求、按维度判分、以及"接口错误不计入分母"
  这一整套逻辑。复制一份必然与它漂移 —— 项目里那个"每个 suite 都重犯一次:接口
  失败被记成答错"的 bug 就是这么来的。所以这里 import run,直接复用它的
  _request_for / _judge_for / _run_tool_loop / mark_error,不重写任何判分代码。

参数同理:配置里写的是角色名(context / kv_k / kv_v / temp ...),由
engine_profiles 翻译成具体引擎的命令行。引擎没有的角色必须停下来 —— 静默丢弃
会让一次运行被标注成一个它从未使用过的配置。

用法:
  python runner/abtest.py --plan config/abtest_plan.json --dry-run
  python runner/abtest.py --plan config/abtest_plan.json --endpoint mock --yes
  python runner/abtest.py --plan config/abtest_plan.json --endpoint mock --yes --tag v1

退出码:
  0  成功
  2  计划文件有问题
  5  引擎不支持某个角色(消息里带角色名与引擎名)
  6  用户在命令确认处取消
"""
import argparse
import datetime
import json
import os
import random
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import engine_profiles as profiles    # noqa: E402
import run as runmod                  # noqa: E402
import speed_probe as speedmod        # noqa: E402
from checkpoint import Checkpoint     # noqa: E402
from endpoint import Endpoint, EndpointError, load_endpoints   # noqa: E402

RESULTS = os.path.join(ROOT, "results")
NL = chr(10)
MOCK_MODEL = "mock-model-80pct"


class PlanError(Exception):
    """计划文件本身有问题(缺段、引擎未知)。"""


class UnsupportedRole(PlanError):
    """配置里用了一个该引擎没有的角色。必须报错,不能静默丢弃。"""

    def __init__(self, engine, roles):
        self.engine = engine
        self.roles = list(roles)
        lines = ["引擎 %s 不支持这些角色: %s" % (engine, ", ".join(self.roles))]
        for r in self.roles:
            others = [e for e in profiles.ENGINE_FLAGS if r in profiles.resolve(e)]
            if others:
                lines.append("  角色 %r 只在 %s 上有定义,%s 没有对应开关。"
                             % (r, ", ".join(others), engine))
            else:
                lines.append("  角色 %r 在 engine_profiles 里没有任何引擎定义它。" % r)
        lines.append("  继续会生成一条服务器会忽略的命令,这次运行就会被标注成一个它从未使用的配置,")
        lines.append("  所以这里停下来。")
        PlanError.__init__(self, NL.join(lines))


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def ts():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe(s):
    """把任意名字变成可以用作文件名的片段(arm key 里带冒号,Windows 不允许)。"""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(s))


def load_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_plan(path):
    if not os.path.isfile(path):
        raise PlanError("计划文件不存在: %s" % path)
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    for key in ("blocks", "configs"):
        if key not in plan:
            raise PlanError("计划文件缺少 %r 段: %s" % (key, path))
    return plan


def rel(path):
    """相对 ROOT 显示路径;跨盘符时原样返回(os.path.relpath 会抛 ValueError)。"""
    try:
        return os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:
        return os.path.abspath(path)


def render_argv(argv):
    out = []
    for a in argv:
        a = str(a)
        out.append('"%s"' % a if (" " in a or "\t" in a) else a)
    return " ".join(out)


# ---------------------------------------------------------------------------
# 题库:按块抽样,块缺料时用 full 题库兜底,并把真实题数带回去
# ---------------------------------------------------------------------------
def _ctx_ok(row, block):
    """块上的上下文档位过滤。有档位的块不接受档位未知的题。"""
    lo = block.get("min_ctx_gt")
    hi = block.get("min_ctx_lte")
    if lo is None and hi is None:
        return True
    mc = row.get("min_ctx")
    if mc is None:
        return False
    if lo is not None and mc <= lo:
        return False
    if hi is not None and mc > hi:
        return False
    return True


def _sample(pool, want, seed, key):
    """确定性抽样:同一 plan 的同一块,每次跑都选同一批题。"""
    rows = sorted(pool, key=lambda r: str(r.get("id")))
    if len(rows) <= want:
        return rows
    rng = random.Random("%s|%s" % (seed, key))
    return sorted(rng.sample(rows, want), key=lambda r: str(r.get("id")))


def select_questions(plan):
    subset = plan.get("subset") or "full"
    seed = plan.get("seed", 20261010)
    subset_path = os.path.join(ROOT, "datasets", "subsets", "%s.jsonl" % subset)
    subset_rows = load_jsonl(subset_path)
    blocks = []
    for b in plan["blocks"]:
        dim = b["dim"]
        want = int(b["n"])
        pool = [r for r in subset_rows if r.get("dim") == dim and _ctx_ok(r, b)]
        sources = []
        if pool:
            sources.append("subsets/%s.jsonl" % subset)
        have = set(r.get("id") for r in pool)
        if len(pool) < want:
            full = load_jsonl(os.path.join(ROOT, "datasets", "full", "%s_full.jsonl" % dim))
            extra = [r for r in full
                     if r.get("dim") == dim and _ctx_ok(r, b) and r.get("id") not in have]
            if extra:
                pool = pool + extra
                sources.append("full/%s_full.jsonl(兜底)" % dim)
        picked = _sample(pool, want, seed, b["id"])
        note = ""
        if len(picked) < want:
            note = ("题库里该档只有 %d 题,少于计划的 %d 题;覆盖率 %.0f%%"
                    % (len(picked), want, 100.0 * len(picked) / want if want else 0))
        blocks.append({
            "id": b["id"],
            "label": b.get("label") or b["id"],
            "dimension": b.get("dimension") or dim,
            "dim": dim,
            "planned": want,
            "actual": len(picked),
            "available": len(pool),
            "coverage": (len(picked) / float(want)) if want else 1.0,
            "sources": sources or ["(无可用题库)"],
            "note": note,
            "questions": picked,
        })
    return blocks


# ---------------------------------------------------------------------------
# 启动命令:角色名 -> 具体引擎的命令行
# ---------------------------------------------------------------------------
def config_argv(cfg):
    engine = cfg.get("engine") or ""
    roles = cfg.get("roles") or {}
    if engine not in profiles.ENGINE_FLAGS:
        raise PlanError("未知引擎 %r;engine_profiles 已知: %s"
                        % (engine, ", ".join(sorted(profiles.ENGINE_FLAGS))))
    bad = profiles.unsupported(engine, list(roles))
    if bad:
        raise UnsupportedRole(engine, bad)
    return profiles.build_argv(engine, roles)


def mock_argv(port, acc):
    return [sys.executable, os.path.join(HERE, "mock_server.py"),
            "--port", str(port), "--acc", str(acc)]


def build_arms(plan, args):
    mode = "mock" if args.endpoint == "mock" else "launch"
    endpoints, raw_cfg = load_endpoints(args.config)
    confs = raw_cfg.get("endpoints") or {}
    arms = []

    for key in sorted(plan.get("configs") or {}):
        cfg = plan["configs"][key]
        arm = {
            "key": key,
            "label": cfg.get("label") or key,
            "kind": "local",
            "engine": cfg.get("engine"),
            "model": cfg.get("model") or key,
            "endpoint_name": cfg.get("model") or key,
            "argv": config_argv(cfg),
            "mock": False,
            "base_url": cfg.get("base_url") or "http://127.0.0.1:8080",
            "port": int((cfg.get("roles") or {}).get("port") or 0) or None,
            "timeout": int(cfg.get("timeout") or 600),
            "roles": cfg.get("roles") or {},
        }
        if mode == "mock":
            m = cfg.get("mock") or {}
            port = int(m.get("port") or 8099)
            arm["mock"] = True
            arm["argv"] = mock_argv(port, m.get("acc", 0.8))
            arm["base_url"] = "http://127.0.0.1:%d" % port
            arm["model"] = MOCK_MODEL
            arm["endpoint_name"] = MOCK_MODEL
            arm["port"] = port
            arm["timeout"] = 120
        arms.append(arm)

    if args.skip_cloud:
        return arms, mode

    for i, c in enumerate(plan.get("cloud") or []):
        name = c.get("endpoint")
        conf = confs.get(name) or {}
        arm = {
            "key": "cloud:" + str(name),
            "label": c.get("label") or name,
            "kind": "cloud",
            "engine": conf.get("engine") or "cloud",
            "model": conf.get("model") or name,
            "endpoint_name": name,
            "argv": [],
            "mock": False,
            "base_url": conf.get("base_url") or "",
            "real_base_url": conf.get("base_url") or "",
            "port": None,
            "timeout": int(conf.get("timeout") or 600),
            "api_key_env": conf.get("api_key_env") or "DEEPSEEK_API_KEY",
            "roles": {},
        }
        if mode == "mock":
            m = c.get("mock") or {}
            port = int(m.get("port") or (8101 + i))
            arm["mock"] = True
            arm["argv"] = mock_argv(port, m.get("acc", 0.8))
            arm["base_url"] = "http://127.0.0.1:%d" % port
            arm["model"] = MOCK_MODEL
            arm["endpoint_name"] = MOCK_MODEL
            arm["port"] = port
            arm["timeout"] = 120
            arm["mock_note"] = ("mock 模式下这一臂由本地 mock 服务承载,用于验证链路;端点定义仍取自 "
                                "config/endpoints.json(model=%s, base_url=%s),没有发出任何真实云端请求。"
                                % (conf.get("model"), conf.get("base_url")))
        else:
            if not conf:
                arm["skipped"] = "config/endpoints.json 里没有端点 %r" % name
            elif not os.environ.get(arm["api_key_env"]):
                arm["skipped"] = ("环境变量 %s 未设置,不发起云端请求(API key 只从环境变量读,绝不写盘)"
                                  % arm["api_key_env"])
        arms.append(arm)
    return arms, mode


def endpoint_for(arm):
    api_key = "sk-local"
    if arm.get("kind") == "cloud" and not arm.get("mock"):
        api_key = os.environ.get(arm.get("api_key_env") or "DEEPSEEK_API_KEY") or "sk-local"
    return Endpoint(name=arm.get("endpoint_name") or arm["key"],
                    base_url=arm["base_url"],
                    model=arm.get("model") or arm["key"],
                    api_key=api_key,
                    timeout=int(arm.get("timeout") or 600),
                    extra={"temperature": 0})


# ---------------------------------------------------------------------------
# 预览与确认
# ---------------------------------------------------------------------------
def print_preview(plan, blocks, arms, mode, args):
    total_planned = sum(b["planned"] for b in blocks)
    total_actual = sum(b["actual"] for b in blocks)
    print("=" * 78)
    print("  A/B 对照实验 · 计划预览")
    print("=" * 78)
    print("  计划文件 : %s" % rel(args.plan))
    print("  计划名称 : %s (version=%s)" % (plan.get("name"), plan.get("version")))
    print("  运行模式 : %s" % ("mock 端点(不启动真引擎、不联网)"
                               if mode == "mock" else "真跑(会启动下面列出的引擎)"))
    print("  题库来源 : datasets/subsets/%s.jsonl,缺料时用 datasets/full/<dim>_full.jsonl 兜底"
          % (plan.get("subset") or "full"))
    print()
    for arm in arms:
        print("  [%s] %s" % (arm["key"], arm["label"]))
        print("      引擎      : %s" % arm.get("engine"))
        print("      服务地址  : %s" % arm.get("base_url"))
        print("      模型      : %s" % arm.get("model"))
        if arm.get("skipped"):
            print("      状态      : 跳过 —— %s" % arm["skipped"])
            print()
            continue
        if arm.get("roles"):
            print("      角色参数  : %s"
                  % ", ".join("%s=%s" % (k, arm["roles"][k]) for k in sorted(arm["roles"])))
        print("      启动命令  :")
        print("        %s" % render_argv(arm["argv"]))
        if arm.get("mock_note"):
            print("      说明      : %s" % arm["mock_note"])
        print()
    print("  题库块分配(计划 %d 题,实际 %d 题):" % (total_planned, total_actual))
    print("    %-20s %-18s %-5s %7s %7s %8s  %s"
          % ("block", "dimension", "dim", "planned", "actual", "coverage", "source"))
    print("    " + "-" * 92)
    for b in blocks:
        print("    %-20s %-18s %-5s %7d %7d %7.0f%%  %s"
              % (b["id"], b["dimension"], b["dim"], b["planned"], b["actual"],
                 b["coverage"] * 100, ", ".join(b["sources"])))
        if b["note"]:
            print("      ! %s" % b["note"])
    print("    " + "-" * 92)
    print("    %-45s %7d %7d %7.0f%%"
          % ("合计", total_planned, total_actual,
             100.0 * total_actual / total_planned if total_planned else 0))
    print()


def confirm_or_abort(assume_yes):
    if assume_yes:
        print("  --yes 已给出,跳过确认。" + NL)
        return True
    sys.stdout.write("  确认按以上命令启动引擎?输入 y 继续,其他任意输入取消: ")
    sys.stdout.flush()
    try:
        line = sys.stdin.readline().strip().lower()
    except Exception:
        line = ""
    return line == "y"


# ---------------------------------------------------------------------------
# 起停
# ---------------------------------------------------------------------------
def _wait_http(url, timeout_s=60):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def start_arm(arm):
    os.makedirs(RESULTS, exist_ok=True)
    if arm.get("mock"):
        log_path = os.path.join(RESULTS, "abtest_mock_%s.log" % _safe(arm["key"]))
        fh = open(log_path, "a", encoding="utf-8")
        fh.write(NL + "=" * 60 + NL + time.strftime("%Y-%m-%d %H:%M:%S") + NL)
        fh.write(render_argv(arm["argv"]) + NL)
        fh.flush()
        try:
            proc = subprocess.Popen(arm["argv"], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
        except Exception as e:
            return None, "mock 服务启动失败: %s" % str(e)[:160]
        ok = _wait_http(arm["base_url"] + "/v1/models", 60)
        if not ok:
            proc.terminate()
            return None, "mock 服务在 60s 内未就绪,见 %s" % rel(log_path)
        return {"proc": proc, "log": log_path}, None
    # 真跑:交给 serverctl,它会在返回前确认端口真的在服务
    import serverctl
    port = int(arm.get("port") or 8080)
    res = serverctl.start(arm["argv"], port=port)
    if not res.get("ok"):
        return None, "引擎启动失败: %s" % res.get("reason")
    return {"serverctl": True, "port": port, "detail": res}, None


def stop_arm(arm, handle):
    if not handle:
        return
    if handle.get("proc") is not None:
        try:
            handle["proc"].terminate()
            handle["proc"].wait(timeout=10)
        except Exception:
            try:
                handle["proc"].kill()
            except Exception:
                pass
        return
    if handle.get("serverctl"):
        try:
            import serverctl
            serverctl.stop(handle["port"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 单题执行:判分完全复用 run.py
# ---------------------------------------------------------------------------
def _evidence(detail):
    """把判分器的返回压成一行"判分依据",方便人看和事后复核。"""
    if not isinstance(detail, dict):
        return str(detail)[:300]
    bits = []
    for k in ("reason", "extracted", "expected", "method", "checker_results",
              "missing", "fabricated", "partial_score", "verdict", "recall"):
        if k in detail and detail[k] not in (None, "", [], {}):
            v = detail[k]
            if isinstance(v, (list, dict)):
                bits.append("%s=%s" % (k, json.dumps(v, ensure_ascii=False)[:200]))
            else:
                bits.append("%s=%s" % (k, str(v)[:200]))
    return "; ".join(bits)[:600]


def run_one(ep, row):
    dim = row.get("dim")
    base = {"id": row.get("id"), "dim": dim, "subcat": row.get("subcat"),
            "min_ctx": row.get("min_ctx")}
    try:
        msgs, kwargs = runmod._request_for(row)
        if kwargs.get("tools"):
            calls, final_text, first_text = runmod._run_tool_loop(ep, msgs, kwargs["tools"])
            res = {"text": final_text or first_text, "tool_calls": calls,
                   "usage": {}, "latency_s": None}
        else:
            res = ep.chat(msgs, **kwargs)
        ok, detail = runmod._judge_for(row, res.get("text") or "", res)
    except EndpointError as e:
        rec = runmod.mark_error(dict(base), e)
        rec["detail"] = {"reason": "endpoint error: " + str(e)[:160]}
        rec["evidence"] = "接口错误,不计入分母"
        rec["response"] = ""
        return rec
    u = res.get("usage") or {}
    rec = dict(base)
    rec.update({
        "correct": bool(ok),
        "detail": detail,
        "evidence": _evidence(detail),
        "response": (res.get("text") or "")[:8000],
        "latency_s": res.get("latency_s"),
        "prompt_tokens": u.get("prompt_tokens"),
        "completion_tokens": u.get("completion_tokens"),
    })
    return rec


def probe_arm(ep, plan, quiet):
    probe = dict(plan.get("speed_probe") or
                 {"prompt_tokens": 1200, "output_tokens": 200, "repeats": 3})
    try:
        res = speedmod.run_probe(ep, probe, verbose=not quiet, skip_env_check=True)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:180])}
    client = res.get("client") or {}
    return {
        "ok": bool(res.get("ok")),
        "probe": probe,
        "output_tps_p50": (client.get("output_tps") or {}).get("p50"),
        "ttft_s_p50": (client.get("ttft_s") or {}).get("p50"),
        "repeats_ok": res.get("repeats_ok"),
        "errors": res.get("errors") or [],
    }


def run_arm(arm, questions, args, tag):
    ep = endpoint_for(arm)
    ids = [q.get("id") for q in questions]
    ck = Checkpoint(suite=_safe(tag), endpoint=_safe(arm["key"]), ids=ids)
    done = {} if args.no_resume else ck.load()
    if done and not args.quiet:
        print("    [resume] 检查点已有 %d/%d 题,续跑剩余 %d 题"
              % (len(done), len(questions), len(questions) - len(done)))
    records = []
    t0 = time.perf_counter()
    for i, q in enumerate(questions, 1):
        prev = done.get(q.get("id"))
        if prev is not None:
            records.append(prev)
            continue
        rec = run_one(ep, q)
        ck.add(rec)
        records.append(rec)
        if not args.quiet and (i % 10 == 0 or i == len(questions)):
            ok = sum(1 for x in records if x.get("correct"))
            err = sum(1 for x in records if x.get("error"))
            valid = len(records) - err
            print("    %3d/%3d  acc=%.1f%%%s"
                  % (i, len(questions), (100.0 * ok / valid) if valid else 0.0,
                     ("  [%d 接口错误未计入]" % err) if err else ""))
    ck.close()
    arm["elapsed_s"] = round(time.perf_counter() - t0, 1)
    # 检查点先留着:结果文件还没落盘,此刻清掉的话,进程在写盘前被杀就等于全丢。
    # 由 main() 在结果文件写成功之后统一清理(run.py 也是"落盘后才清")。
    return records, ck


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def annotate(records, blocks):
    qmap = {}
    for b in blocks:
        for q in b["questions"]:
            qmap[q.get("id")] = {"block": b["id"], "block_label": b["label"],
                                 "dimension": b["dimension"]}
    for r in records:
        info = qmap.get(r.get("id")) or {}
        r["block"] = info.get("block")
        r["block_label"] = info.get("block_label")
        r["dimension"] = info.get("dimension") or r.get("dim")
    return records


def summarize(records, blocks):
    order = []
    for b in blocks:
        if b["dimension"] not in order:
            order.append(b["dimension"])
    by_dim = []
    for dim in order:
        planned = sum(b["planned"] for b in blocks if b["dimension"] == dim)
        actual = sum(b["actual"] for b in blocks if b["dimension"] == dim)
        rs = [r for r in records if r.get("dimension") == dim]
        valid = [r for r in rs if not r.get("error")]
        ok = sum(1 for r in valid if r.get("correct"))
        by_dim.append({
            "dimension": dim,
            "planned": planned,
            "actual": actual,
            "answered": len(valid),
            "errors": len(rs) - len(valid),
            "correct": ok,
            "accuracy": (ok / float(len(valid))) if valid else 0.0,
            "coverage": (len(valid) / float(planned)) if planned else 0.0,
        })
    valid_all = [r for r in records if not r.get("error")]
    ok_all = sum(1 for r in valid_all if r.get("correct"))
    planned_all = sum(b["planned"] for b in blocks)
    return {
        "n": len(records),
        "answered": len(valid_all),
        "errors": len(records) - len(valid_all),
        "correct": ok_all,
        "accuracy": (ok_all / float(len(valid_all))) if valid_all else 0.0,
        "planned": planned_all,
        "coverage": (len(valid_all) / float(planned_all)) if planned_all else 0.0,
        "by_dimension": by_dim,
    }


def load_prices():
    path = os.path.join(ROOT, "config", "prices.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def price_for(arm, prices):
    """按臂的类型挑价目。名字精确命中优先,再按前缀命中(deepseek-flash -> deepseek),
    最后才退到同类兜底。不做这一步的话,云端臂会命中 "cloud": 0.0 而把成本报成 0。"""
    name = str(arm.get("endpoint_name") or arm.get("key") or "")
    if arm.get("mock"):
        cands = ["mock"]
    elif arm.get("kind") == "cloud":
        cands = [name] + [k for k in prices if k and not k.startswith("_")
                          and name.lower().startswith(str(k).lower())] + ["cloud"]
    else:
        cands = [name, "local"]
    for cand in cands:
        v = prices.get(cand)
        if isinstance(v, dict):
            return v, cand
    return {"in": 0.0, "out": 0.0, "currency": "CNY"}, "(未配置)"


def arm_cost(arm, records, prices):
    pin = sum((r.get("prompt_tokens") or 0) for r in records)
    pout = sum((r.get("completion_tokens") or 0) for r in records)
    pr, key = price_for(arm, prices)
    cost = pin / 1e6 * float(pr.get("in") or 0) + pout / 1e6 * float(pr.get("out") or 0)
    return {"prompt_tokens": pin, "completion_tokens": pout,
            "price_key": key, "in_per_m": pr.get("in"), "out_per_m": pr.get("out"),
            "currency": pr.get("currency", "CNY"), "est_cost": round(cost, 6)}


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="A/B 对照实验引擎(一条命令跑完两套配置并出差异表)")
    ap.add_argument("--plan", default=os.path.join(ROOT, "config", "abtest_plan.json"))
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "endpoints.json"))
    ap.add_argument("--endpoint", default="",
                    help="传 mock 表示全部臂都由本地 mock 服务承载(不启动真引擎、不联网)")
    ap.add_argument("--dry-run", action="store_true", help="只打印启动命令与块分配,不执行任何命令")
    ap.add_argument("--yes", action="store_true", help="跳过启动命令的确认")
    ap.add_argument("--tag", default="", help="结果文件名后缀")
    ap.add_argument("--no-resume", action="store_true", help="忽略检查点,从头跑")
    ap.add_argument("--no-speed", action="store_true", help="不跑速度探测")
    ap.add_argument("--skip-cloud", action="store_true", help="不跑云端对照臂")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        plan = load_plan(args.plan)
        blocks = select_questions(plan)
        arms, mode = build_arms(plan, args)
    except UnsupportedRole as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 5
    except PlanError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2

    print_preview(plan, blocks, arms, mode, args)

    if args.dry_run:
        print("  --dry-run:以上命令一条都没有执行。" + NL)
        return 0

    if not confirm_or_abort(args.yes):
        print(NL + "  已取消,未启动任何进程。" + NL)
        return 6

    questions = []
    seen = set()
    for b in blocks:
        for q in b["questions"]:
            qid = q.get("id")
            if qid in seen:
                continue
            seen.add(qid)
            questions.append(q)
    if not questions:
        print("ERROR: 计划没有选中任何题,检查 datasets/ 与计划里的 blocks", file=sys.stderr)
        return 2

    tag = args.tag or ("mock" if mode == "mock" else "run")
    prices = load_prices()
    checkpoints = []
    started = ts()
    out = {"tag": tag, "started": started, "mode": mode,
           "plan_path": rel(args.plan),
           "plan_name": plan.get("name"), "plan_version": plan.get("version"),
           "n_questions": len(questions),
           "blocks": [{k: v for k, v in b.items() if k != "questions"} for b in blocks],
           "question_ids": [q.get("id") for q in questions],
           "arms": [], "notes": []}

    for arm in arms:
        print()
        print("-" * 78)
        print("  [%s] %s" % (arm["key"], arm["label"]))
        print("-" * 78)
        if arm.get("skipped"):
            print("  跳过: %s" % arm["skipped"])
            out["arms"].append({"key": arm["key"], "label": arm["label"],
                                "kind": arm["kind"], "skipped": arm["skipped"],
                                "records": []})
            out["notes"].append("[%s] 跳过: %s" % (arm["key"], arm["skipped"]))
            continue

        handle, err = start_arm(arm)
        if err:
            print("  ERROR: %s" % err)
            out["arms"].append({"key": arm["key"], "label": arm["label"],
                                "kind": arm["kind"], "skipped": err, "records": []})
            out["notes"].append("[%s] 启动失败: %s" % (arm["key"], err))
            continue
        try:
            ep = endpoint_for(arm)
            ok, detail = ep.health()
            print("  健康检查: %s | %s" % ("OK" if ok else "UNREACHABLE", str(detail)[:100]))
            if not ok:
                raise EndpointError("endpoint unreachable: %s" % detail)

            speed = {}
            if not args.no_speed:
                print("  速度探测 ...")
                speed = probe_arm(ep, plan, args.quiet)
                if speed.get("ok"):
                    print("    输出速度 p50 = %.1f tok/s | TTFT p50 = %.2fs"
                          % (speed.get("output_tps_p50") or 0, speed.get("ttft_s_p50") or 0))
                else:
                    print("    速度探测失败: %s" % (speed.get("error") or speed.get("errors")))

            print("  题库 %d 题 ..." % len(questions))
            records, arm_ck = run_arm(arm, questions, args, tag)
            checkpoints.append(arm_ck)
            annotate(records, blocks)
            summary = summarize(records, blocks)
            cost = arm_cost(arm, records, prices)
            arm_out = {
                "key": arm["key"], "label": arm["label"], "kind": arm["kind"],
                "engine": arm.get("engine"), "model": arm.get("model"),
                "endpoint_name": arm.get("endpoint_name"),
                "base_url": arm.get("base_url"), "mock": arm.get("mock", False),
                "argv": arm.get("argv"), "roles": arm.get("roles"),
                "elapsed_s": arm.get("elapsed_s"), "speed": speed,
                "cost": cost, "summary": summary, "records": records,
            }
            if arm.get("mock_note"):
                arm_out["mock_note"] = arm["mock_note"]
            out["arms"].append(arm_out)
            print("  完成: %d/%d 正确,覆盖 %.0f%%,耗时 %.1fs"
                  % (summary["correct"], summary["answered"],
                     summary["coverage"] * 100, arm.get("elapsed_s") or 0))
        except EndpointError as e:
            print("  ERROR: %s" % str(e)[:200])
            out["arms"].append({"key": arm["key"], "label": arm["label"],
                                "kind": arm["kind"], "skipped": str(e)[:200],
                                "records": []})
            out["notes"].append("[%s] 中断: %s" % (arm["key"], str(e)[:200]))
        finally:
            stop_arm(arm, handle)

    for b in blocks:
        if b["note"]:
            out["notes"].append("[%s] %s" % (b["id"], b["note"]))
    if mode == "mock":
        out["notes"].append("本次为 mock 模式:所有回答来自本地 runner/mock_server.py,"
                            "不能当作任何真实模型的能力证据。")

    os.makedirs(RESULTS, exist_ok=True)
    name = "abtest_%s_%s.json" % (_safe(tag), started)
    path = os.path.join(RESULTS, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # 结果已落盘,检查点到此为止;留着会让下一次同 tag 的运行静默重放旧记录。
    for ck in checkpoints:
        ck.clear()

    print()
    print("=" * 78)
    print("  A/B 对照实验完成")
    print("=" * 78)
    for a in out["arms"]:
        if a.get("skipped"):
            print("  %-18s 跳过: %s" % (a["key"], a["skipped"][:60]))
            continue
        s = a["summary"]
        sp = a.get("speed") or {}
        print("  %-18s %3d/%3d  %5.1f%%  覆盖 %3.0f%%  速度 %s tok/s  成本 %.4f %s"
              % (a["key"], s["correct"], s["answered"], s["accuracy"] * 100,
                 s["coverage"] * 100,
                 ("%.1f" % sp["output_tps_p50"]) if sp.get("output_tps_p50") else "n/a",
                 a["cost"]["est_cost"], a["cost"]["currency"]))
    print("  -> %s" % os.path.relpath(path, ROOT))
    print("  下一步: python runner/abtest_report.py --in %s" % os.path.relpath(path, ROOT))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
