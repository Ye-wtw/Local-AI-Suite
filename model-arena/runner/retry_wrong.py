# -*- coding: utf-8 -*-
"""retry_wrong.py - 错题重做(独立指标,不污染标准分)。

为什么单独做成一个工具而不是混进 run.py:
  * 标准分是 **pass@1**:模型第一次作答就是对错。这是能与云端、与其他模型对比的口径。
  * 错题重做是 **best-of-N / pass@k_k**:同一题再来一次,答对就算。这个数字一定更高,
    但它回答的是另一个问题——"给模型第二次机会,它能救回多少"。
  * 两个数字混在一起,得到的是既不是 pass@1 也不是 pass@k 的东西,谁也解释不清。

用途(真实存在的两个):
  1. **判分器嫌疑排查**:一堆题被判错,想知道是模型真不会,还是判分器误伤。
     重做一遍若大量翻对,说明判分器或题面有问题(见 AGENTS.md 第3条)。
  2. **稳定性测量**:同一题两次作答不一致,说明模型对该题不稳定(采样噪声/长推理漂移),
     这在温度=0 下本不该发生,出现即值得记一笔。

输出:preset 带 -retry 后缀,records 带 retry_of / attempt 字段。
标准报告不会把它并进 pass@1 —— 这是刻意的。
"""
import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))

from endpoint import load_endpoints  # noqa: E402
from judge import judge_math  # noqa: E402
from checkers import judge_mcq, run_checkers  # noqa: E402
from chess_judge import judge_chess  # noqa: E402
from code_judge import judge_code  # noqa: E402
from agent_judge import judge_agent  # noqa: E402
from reliability_judge import judge_reliability  # noqa: E402
from d9_judge import judge_d9  # noqa: E402

DATASET = {"D2": "D2_full.jsonl", "D3": "D3_full.jsonl", "D4": "D4_full.jsonl",
           "D5": "D5_full.jsonl", "D6": "D6_full.jsonl", "D8": "D8_full.jsonl",
           "D10": "D10_full.jsonl"}


def load_rows(dim):
    p = os.path.join(ROOT, "datasets", "full", DATASET[dim])
    rows = {}
    with io.open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                x = json.loads(line)
                rows[x.get("id")] = x
    return rows


def build_prompt(dim, r):
    """按各 suite 真实使用的题面构造。不能凭记忆写 —— 题面不一致就不是同一道题。"""
    import run as R
    if dim == "D2":
        opts = r.get("options") or {}
        if isinstance(opts, list):
            opts = {p.split(".", 1)[0].strip(): p.split(".", 1)[1].strip()
                    for p in opts if "." in p}
        opt_txt = chr(10).join("%s. %s" % (k, v) for k, v in sorted(opts.items()))
        return (r["question"] + chr(10) + chr(10) + opt_txt + chr(10) + chr(10) +
                "Reply with the single best option letter only, on a final line: answer: <letter>")
    if dim == "D3":
        return r.get("question") or r.get("user") or ""
    if dim == "D4":
        return R._code_prompt(r)
    if dim == "D5":
        return r.get("user") or r.get("question") or ""
    if dim == "D6":
        return r.get("user") or r.get("question") or ""
    if dim == "D8":
        return r.get("question") or r.get("user") or ""
    if dim == "D10":
        return r.get("question") or ""
    return r.get("question") or r.get("user") or ""


def judge(dim, r, text, rec_old):
    if dim == "D2":
        return judge_mcq(text, r.get("answer_letter"))
    if dim == "D3":
        # judge_math 第三个参数是档位字符串(exact/numeric/...),不是数值容差
        return judge_math(text, r.get("answer"), r.get("tolerance", "exact"))
    if dim == "D4":
        return judge_code(r, text)
    if dim == "D5":
        passed, _res = run_checkers(r.get("expect", {}).get("checkers"), text)
        return {"correct": bool(passed)}
    if dim == "D6":
        return judge_agent(r, rec_old.get("result") or {}, text)
    if dim == "D8":
        return judge_reliability(r, text)
    if dim == "D10":
        return judge_chess(r, text)
    return {"correct": False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="原始结果文件")
    ap.add_argument("--out", default="")
    ap.add_argument("--max", type=int, default=0, help="最多重做多少题(0=全部)")
    a = ap.parse_args()

    with io.open(a.src, encoding="utf-8") as f:
        src = json.load(f)
    sum0 = src.get("summary") or {}
    dim = sum0.get("dim")
    recs = src.get("records") or []
    wrong = [r for r in recs if not r.get("correct") and not r.get("error")]
    if a.max:
        wrong = wrong[:a.max]
    if not wrong:
        print("没有错题可重做")
        return 0
    if dim not in DATASET:
        print("维度 %s 暂不支持重做" % dim)
        return 2

    rows = load_rows(dim)
    eps, _ = load_endpoints(os.path.join(ROOT, "config", "endpoints.json"))
    ep = eps.get("queue")
    if not ep:
        print("没有 queue 端点")
        return 2
    ep.max_tokens = 16384
    ep.deadline_s = 300

    partial = os.path.join(ROOT, "results", "retry_partial_%s.json" % dim)
    out = []
    if os.path.isfile(partial):
        try:
            with io.open(partial, encoding="utf-8") as f:
                out = json.load(f).get("records") or []
            print("续跑:已有 %d 题" % len(out))
        except Exception:
            out = []
    done = {x.get("retry_of") for x in out if not x.get("error")}
    wrong = [r for r in wrong if r.get("id") not in done]

    print("待重做 %d 题 (维度 %s)" % (len(wrong), dim))
    t0 = time.perf_counter()
    for i, old in enumerate(wrong, 1):
        r = rows.get(old.get("id"))
        if not r:
            continue
        prompt = build_prompt(dim, r)
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            v = judge(dim, r, res["text"], old)
            rec = {"id": old.get("id"), "retry_of": old.get("id"), "attempt": 2,
                   "subcat": r.get("subcat"), "response": res["text"],
                   "latency_s": res["latency_s"], "correct": bool(v.get("correct")),
                   "first_try_correct": False, **v}
        except Exception as e:
            rec = {"id": old.get("id"), "retry_of": old.get("id"), "attempt": 2,
                   "correct": False, "error": True, "error_reason": str(e)[:150]}
        out.append(rec)
        valid = [x for x in out if not x.get("error")]
        ok = sum(1 for x in valid if x.get("correct"))
        print("  %d/%d  救回 %d/%d (%.0f%%)" % (i, len(wrong), ok, len(valid),
                                               (ok / len(valid) * 100) if valid else 0),
              flush=True)
        _flush(partial, out, a.src, dim, ep, time.perf_counter() - t0)

    valid = [x for x in out if not x.get("error")]
    ok = sum(1 for x in valid if x.get("correct"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = a.out or os.path.join(ROOT, "results", "%s_retry_%s.json" % (dim, stamp))
    payload = {"preset": "retry-%s" % dim,
               "source_file": os.path.basename(a.src),
               "caveat": "错题重做(best-of-N)。与 pass@1 不可混合,不可与云端对比。",
               "summary": {"dim": dim, "suite": "retry", "model": ep.model,
                           "n_wrong": len(out), "n_valid": len(valid),
                           "n_errors": len(out) - len(valid),
                           "recovered": ok,
                           "recover_rate": (ok / len(valid)) if valid else 0.0,
                           "elapsed_s": time.perf_counter() - t0},
               "records": out}
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    if os.path.isfile(partial):
        try:
            os.remove(partial)
        except OSError:
            pass
    print(chr(10) + "重做完成: %d/%d 救回 (%.1f%%)" % (
        ok, len(valid), (ok / len(valid) * 100) if valid else 0))
    print("-> " + os.path.relpath(path, ROOT))
    return 0


def _flush(path, out, src_path, dim, ep, dt):
    valid = [x for x in out if not x.get("error")]
    ok = sum(1 for x in valid if x.get("correct"))
    payload = {"preset": "retry-%s" % dim, "source_file": os.path.basename(src_path),
               "partial": True,
               "summary": {"dim": dim, "suite": "retry", "model": ep.model,
                           "n_wrong": len(out), "n_valid": len(valid),
                           "n_errors": len(out) - len(valid), "recovered": ok,
                           "recover_rate": (ok / len(valid)) if valid else 0.0,
                           "elapsed_s": dt},
               "records": out}
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
