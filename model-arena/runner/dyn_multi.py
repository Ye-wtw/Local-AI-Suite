# -*- coding: utf-8 -*-
"""dyn_multi.py - multi-round dynamic axis.

Why this exists: every other suite asks one question and reads one answer. That
measures a single shot, but a coding agent works in rounds - it edits, runs the
tests, reads the failure, and tries again. A model that fixes a bug on attempt 3
is a different tool from one that fixes it on attempt 1, and a model that never
fixes it is different again. One-shot scoring cannot tell those apart.

Shape: reuse the D9 exec questions unchanged (they already ship a small broken
repo, a command to run, and the marker that means "fixed"). Each round the model
gets the task, its own previous patch, and the real test output from last time.
Scoring is the same honest check as d9_exec: write the files, run the command.

Reported per task, all four because they answer different questions:
  solved            did the tests ever go green
  rounds_used       how many attempts it took (1 is best)
  time_to_green_s   wall clock until the first green (a timeout is recorded,
                    not scored as zero - a slow-but-correct local model is a
                    legitimate outcome, see PLAN-v2 addendum 2)
  timed_out         the cap was hit before solving
"""
import os
import time

import d9_exec as d9exec

DEFAULT_ROUNDS = 3
DEFAULT_TIMEOUT = 300


def run_multiround(ep, task, work_root, max_rounds=DEFAULT_ROUNDS,
                   timeout_s=DEFAULT_TIMEOUT, verbose=False):
    """Drive up to max_rounds attempts at one exec task. Returns a record dict."""
    e = task.get("exec") or {}
    question = task.get("user") or task.get("task") or ""
    convo = [{"role": "user", "content": question}]
    history = []
    t0 = time.perf_counter()
    solved = False
    rounds_used = 0
    time_to_green = None
    timed_out = False
    last = None

    for rnd in range(1, max_rounds + 1):
        if time.perf_counter() - t0 > timeout_s:
            timed_out = True
            break
        rounds_used = rnd
        try:
            res = ep.chat(convo)
        except Exception as exc:                      # endpoint failure is not a wrong answer
            history.append({"round": rnd, "error": str(exc)[:200]})
            break
        text = res.get("text") or ""
        v = d9exec.run_exec_task(task, text, work_root)
        last = v
        history.append({"round": rnd, "correct": bool(v.get("correct")),
                        "exit_code": v.get("exit_code"), "reason": v.get("reason"),
                        "latency_s": res.get("latency_s")})
        if v.get("correct"):
            solved = True
            time_to_green = time.perf_counter() - t0
            break
        # feed the real failure back, exactly as a developer would see it
        feedback = ("你的修改没有通过测试。\n退出码: %s\n输出:\n%s\n%s\n请修正后重新提交完整文件。"
                    % (v.get("exit_code"), (v.get("stdout") or "")[:1500],
                       (v.get("stderr") or "")[:800]))
        convo.append({"role": "assistant", "content": text})
        convo.append({"role": "user", "content": feedback})

    if not solved and not timed_out and time.perf_counter() - t0 > timeout_s:
        timed_out = True
    return {
        "id": task.get("id"),
        "subcat": task.get("subcat"),
        "solved": solved,
        "rounds_used": rounds_used,
        "time_to_green_s": round(time_to_green, 2) if time_to_green else None,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "timed_out": timed_out,
        "final_reason": (last or {}).get("reason"),
        "history": history,
    }


def suite_dynamic(ep, limit=None, max_rounds=DEFAULT_ROUNDS, timeout_s=DEFAULT_TIMEOUT,
                  verbose=True):
    """Run the dynamic axis over the D9 exec questions."""
    import run as runmod
    qs, base = runmod._load_d9()
    tasks = [q for q in qs if (base.get(q["id"]) or {}).get("exec")]
    if limit:
        tasks = tasks[:limit]
    root = os.path.join(runmod.ROOT, "results", ".dynwork")
    records = []
    t0 = time.perf_counter()
    if verbose:
        print()
        print("[D10 动态多轮] %d 题, 最多 %d 轮, 单题上限 %ds, endpoint=%s model=%s"
              % (len(tasks), max_rounds, timeout_s, ep.name, ep.model))
    for i, q in enumerate(tasks, 1):
        rec = run_multiround(ep, q, root, max_rounds=max_rounds,
                             timeout_s=timeout_s, verbose=verbose)
        records.append(rec)
        if verbose:
            print("  %2d/%2d %-24s %-6s 轮数=%d 用时=%5.1fs%s"
                  % (i, len(tasks), rec["id"], "PASS" if rec["solved"] else "fail",
                     rec["rounds_used"], rec["elapsed_s"],
                     "  [超时]" if rec["timed_out"] else ""))
    dt = time.perf_counter() - t0
    solved = sum(1 for r in records if r["solved"])
    timeouts = sum(1 for r in records if r["timed_out"])
    greens = [r["time_to_green_s"] for r in records if r["time_to_green_s"]]
    return ({"dim": "DYN", "suite": "dynamic", "endpoint": ep.name, "model": ep.model,
             "n": len(records), "solved": solved,
             "solve_rate": solved / len(records) if records else 0.0,
             "timeout_rate": timeouts / len(records) if records else 0.0,
             "mean_rounds": (sum(r["rounds_used"] for r in records) / len(records)) if records else 0.0,
             "median_time_to_green_s": (sorted(greens)[len(greens) // 2] if greens else None),
             "max_rounds": max_rounds, "timeout_s": timeout_s,
             "elapsed_s": dt}, records)
