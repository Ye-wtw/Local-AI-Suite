# -*- coding: utf-8 -*-
"""agent_env.py - inject a realistic tool environment into evaluation prompts.

Why this matters, measured rather than assumed:

    the user's real DSH session carries 159 tool definitions = 158,765 chars
    ~= 85,357 tokens of prompt BEFORE the question is even read.

On this machine a single question costs 2.8 s bare but 48.9 s once those
definitions are in the prompt - a 17x difference, because prefill dominates. And
the definitions are not free for accuracy either: 85k tokens of tool schemas
compete for attention with the actual question.

So "how good is this model" has two different answers depending on whether it is
asked bare or through an agent harness, and only the second one matches daily use.
This module makes that switchable instead of implicit.

Four sizes, so the effect of SCALE can be separated from the effect of TOOLS
existing at all:

    bare          0 tools      0 tok        pure capability
    agent-small   10 tools   ~5k tok        a light harness
    agent-mid     50 tools  ~25k tok        a normal harness
    agent-real   159 tools  ~85k tok        this user's actual environment

Usage:
  python runner/agent_env.py --build                    # regenerate the size tiers
  python runner/agent_env.py --show agent-mid           # what is in a tier
  python runner/agent_env.py --prefix agent-mid         # the literal prompt prefix
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORE = os.path.join(ROOT, "datasets", "agent_env")
REAL = os.path.join(STORE, "tools_real.json")
TIERS = os.path.join(STORE, "tiers.json")
NL = chr(10)
SEED = 20261010

SIZES = {
    "bare":        {"n": 0,   "target_tokens": 0},
    "agent-small": {"n": 10,  "target_tokens": 5000},
    "agent-mid":   {"n": 50,  "target_tokens": 25000},
    "agent-real":  {"n": 159, "target_tokens": 85357},
}


def load_real():
    if not os.path.exists(REAL):
        return []
    try:
        return json.load(open(REAL, encoding="utf-8"))
    except Exception:
        return []


def build_tiers(verbose=True):
    """Pick which tools go in each tier.

    Selection is by CATEGORY ROUND-ROBIN, not by taking the first N. The real set is
    dominated by mcp_* and browser_* (69 of 159); slicing the head would give a tier
    that is all browser tools, which is not a smaller harness, it is a different one.
    """
    tools = load_real()
    if not tools:
        return {}
    rng = random.Random(SEED)

    # group by name prefix so every tier spans the same kinds of capability
    groups = {}
    for t in tools:
        key = (t.get("name") or "?").split("_")[0]
        groups.setdefault(key, []).append(t)
    for g in groups.values():
        g.sort(key=lambda x: x.get("name") or "")
        rng.shuffle(g)

    order, keys = [], sorted(groups)
    idx = {k: 0 for k in keys}
    while len(order) < len(tools):
        progressed = False
        for k in keys:
            if idx[k] < len(groups[k]):
                order.append(groups[k][idx[k]])
                idx[k] += 1
                progressed = True
        if not progressed:
            break

    tiers = {}
    for name, spec in SIZES.items():
        if spec["n"] <= 0:
            tiers[name] = []
            continue
        picked = order[:spec["n"]]
        tiers[name] = picked
        if verbose:
            # report the RENDERED prefix, not the raw JSON: the rendered form is what
            # actually reaches the model, and mixing the two measures made the build
            # output and the stats table disagree by 3x.
            rendered = build_prefix_from(picked, SIZES[name]["target_tokens"])
            print("  %-12s %3d 工具  注入 %7d 字符  ≈ %6d tok  (%d 类)"
                  % (name, len(picked), len(rendered), len(rendered) / 1.86,
                     len(set((t.get("name") or "?").split("_")[0] for t in picked))))
    return tiers


def save_tiers(tiers):
    os.makedirs(STORE, exist_ok=True)
    with open(TIERS, "w", encoding="utf-8") as f:
        json.dump(tiers, f, ensure_ascii=False)
    return TIERS


def load_tiers():
    if os.path.exists(TIERS):
        try:
            return json.load(open(TIERS, encoding="utf-8"))
        except Exception:
            pass
    return {}


def build_prefix_from(tools, target_tokens=None):
    """Render a tool list into the prompt prefix.

    The description budget adapts instead of being a fixed truncation. A hard 200-char
    cap was applied to 69% of the real definitions and cut the injected prompt from
    85k to 19k tokens - a "real environment" that was 4.4x smaller than the real one,
    which would have understated the very effect being measured. When target_tokens is
    given the budget is solved to land near it; otherwise the full text is used.
    """
    if not tools:
        return ""
    CHARS_PER_TOKEN = 1.86

    def render(budget):
        lines = ["你可以使用以下工具。若任务需要,请说明你要调用哪个工具及其参数。", ""]
        for t in tools:
            name = t.get("name") or "?"
            desc = (t.get("description") or "").strip().replace(NL, " ")
            if budget and len(desc) > budget:
                desc = desc[:budget] + "…"
            params = t.get("parameters") or {}
            props = params.get("properties") or {}
            req = set(params.get("required") or [])
            bits = []
            for k in list(props)[:8]:
                bits.append(k + ("*" if k in req else ""))
            argnames = ", ".join(bits)
            lines.append("- %s(%s): %s" % (name, argnames, desc))
        lines.append("")
        return NL.join(lines)

    if not target_tokens:
        return render(None)

    target_chars = target_tokens * CHARS_PER_TOKEN
    # binary search the description budget that lands closest to the target
    lo, hi, best = 60, 6000, render(None)
    for _ in range(14):
        mid = (lo + hi) // 2
        text = render(mid)
        if len(text) > target_chars:
            hi = mid - 1
        else:
            lo = mid + 1
        if abs(len(text) - target_chars) < abs(len(best) - target_chars):
            best = text
    return best


def build_prefix(mode, tiers=None):
    """The literal text injected before a question.

    It is written as a system-style preamble rather than as an API `tools` field,
    because that is what the comparison needs: the same prompt reaching the model
    whether the harness supports native tool calling or not. Native tool schemas are
    a different experiment (D6 covers them).
    """
    if mode == "bare":
        return ""
    tiers = tiers or load_tiers()
    return build_prefix_from(tiers.get(mode) or [],
                             (SIZES.get(mode) or {}).get("target_tokens"))


def apply_prefix(prompt, mode, tiers=None):
    p = build_prefix(mode, tiers)
    return (p + NL + prompt) if p else prompt


def estimate_tokens(mode, tiers=None):
    p = build_prefix(mode, tiers)
    return int(len(p) / 1.86) if p else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show", default="")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.build:
        print("从真实工具集构建分档:")
        print("  源: %s (%d 个工具)" % (os.path.relpath(REAL, ROOT), len(load_real())))
        tiers = build_tiers()
        path = save_tiers(tiers)
        print(NL + "  -> %s" % os.path.relpath(path, ROOT))
        return

    if args.show:
        tiers = load_tiers()
        tools = tiers.get(args.show)
        if tools is None:
            print("无此档:", args.show, "| 可用:", ", ".join(tiers) or "(未构建)")
            return
        print("%s: %d 个工具" % (args.show, len(tools)))
        for t in tools[:20]:
            print("  %-34s %s" % (t.get("name"), (t.get("description") or "")[:52]))
        if len(tools) > 20:
            print("  ... 共 %d 个" % len(tools))
        return

    if args.prefix:
        print(build_prefix(args.prefix))
        return

    tiers = load_tiers()
    if not tiers:
        print("尚未构建分档。先运行: python runner/agent_env.py --build")
        return
    print("Agent 环境分档:")
    print("  %-12s %5s %10s %10s %8s" % ("档位", "工具", "字符", "≈tokens", "单题耗时"))
    print("  " + "-" * 54)
    per_q = {"bare": 2.8, "agent-small": 8.0, "agent-mid": 25.0, "agent-real": 48.9}
    for name in ("bare", "agent-small", "agent-mid", "agent-real"):
        tools = tiers.get(name) or []
        rendered = build_prefix_from(tools, SIZES[name]["target_tokens"])
        print("  %-12s %5d %10d %10d %7.1fs"
              % (name, len(tools), len(rendered), len(rendered) / 1.86,
                 per_q.get(name, 0)))
    print()
    print("  用途:同一批题分别在这四档下跑,得到「真实 agent 环境下的降智幅度」:")
    print("        降智 = (bare 准确率 − agent 准确率) / bare 准确率")


if __name__ == "__main__":
    main()
