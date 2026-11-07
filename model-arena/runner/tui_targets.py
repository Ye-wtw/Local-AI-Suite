# -*- coding: utf-8 -*-
"""tui_targets.py - the model-selection pane for the TUI.

Written as its own module rather than grown inside arena_tui.py: the dashboard is
read-only, and picking a model means WRITING config. Keeping the write path in a
separate file makes it obvious which part of the terminal UI changes state.

Two kinds of target, matching what the API already models:
  attach  a server someone else started; this program only points at a URL
  launch  this program starts the engine, so the flags are known and recorded
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def render_targets(selected=None):
    """Lines describing every configured target, with the active one marked."""
    import targets as T
    out = ["  已配置的被测目标",
           "  %-3s %-16s %-8s %-34s %s"
           % ("", "id", "模式", "地址 / 文件", "模型或参数")]
    for t in T.list_targets():
        mark = "*" if t.get("id") == selected else " "
        kind = t.get("kind") or "attach"
        if kind == "launch":
            where = os.path.basename(t.get("model_path") or "?")
            extra = "ctx=%s ngl=%s moe=%s" % (t.get("ctx", "?"),
                                              t.get("ngl", "?"),
                                              t.get("cpu_moe", "-"))
        else:
            where = t.get("base_url") or "?"
            extra = t.get("model") or "?"
        out.append("  %-3s %-16s %-8s %-34s %s"
                   % (mark, t.get("id"), kind, where[:34], extra[:28]))
    if len(out) == 2:
        out.append("    (还没有配置任何目标，用 targets.py 添加)")
    return out


def render_discovered():
    """What is running locally and what could be launched - not yet saved."""
    import targets as T
    out = ["  扫描结果（未保存）"]
    ports = T.scan_ports()
    if ports:
        out.append("    在跑的服务:")
        for p in ports:
            out.append("      %-6d %-20s %s"
                       % (p["port"], p.get("engine") or "?",
                          ", ".join(p.get("models") or [])[:40]))
    else:
        out.append("    在跑的服务: 无")
    g = T.find_ggufs()
    if g:
        out.append("    本地可启动模型:")
        for x in g[:8]:
            out.append("      %6.2f GB  %s" % (x["size_gb"], x["name"][:52]))
        if len(g) > 8:
            out.append("      ... 共 %d 个" % len(g))
    return out


def add_attach(url, tid=None, model=None):
    """Save a URL as an attach target, probing it first."""
    import targets as T
    p = T.probe(url)
    if not p.get("reachable"):
        return {"ok": False, "error": "连不上: %s" % (p.get("error") or "")}
    tid = tid or ("port" + url.rsplit(":", 1)[-1])
    t = T.upsert({"id": tid, "kind": "attach", "label": tid,
                  "base_url": url,
                  "model": model or (p["models"][0] if p["models"] else tid),
                  "engine": p.get("engine_guess") or "openai",
                  "timeout": 600})
    return {"ok": True, "target": t}


def add_launch(model_path, tid, ctx=32768, ngl=99, cpu_moe=None,
               kv_type="q4_0", port=8080):
    """Save a launch target. Does NOT start anything: that stays explicit."""
    import targets as T
    if not os.path.exists(model_path):
        return {"ok": False, "error": "模型文件不存在"}
    t = T.upsert({"id": tid, "kind": "launch", "label": tid,
                  "model_path": model_path, "ctx": ctx, "ngl": ngl,
                  "cpu_moe": cpu_moe, "kv_type": kv_type, "port": port,
                  "engine": "llama.cpp"})
    return {"ok": True, "target": t}


def to_run_args(tid):
    """Translate a target into the arguments run.py understands."""
    import targets as T
    t = T.get_target(tid)
    if not t:
        return None
    if t.get("kind") == "launch":
        return {"needs_launch": True, "port": t.get("port") or 8080}
    return {"needs_launch": False, "endpoint": tid}