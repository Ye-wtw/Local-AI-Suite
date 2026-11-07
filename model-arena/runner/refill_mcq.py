# -*- coding: utf-8 -*-
"""refill_mcq.py - 补跑连接失败的题(只补"没答过"的,不补"答错了"的)。

区别很关键:
  * 连接失败 = 请求没送到,模型没机会作答 → 补跑是**修复数据**,不是改变成绩
  * 答错了   = 模型答了且答错 → 补跑就是 best-of-N,不再是 pass@1,不能混进同一栏

输出单独一个文件,里面的 preset 字段带 -refill 后缀,报告层能分开看。
"""
import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))

# 必须用 run.py 用的那个 judge_mcq(checkers.py)。
# 混用 judge.py 里的同名函数会让补跑结果和原始数据的判分口径不一致,
# 那样补出来的分数没法与已有结果合并。
from checkers import judge_mcq  # noqa: E402
from endpoint import load_endpoints  # noqa: E402


def _flush(path, out, src_path, ep, dt):
    valid = [x for x in out if not x.get("error")]
    cor = sum(1 for x in valid if x.get("correct"))
    payload = {"preset": "KV17-Bonsai2T5-200K-refill",
               "source_file": os.path.basename(src_path),
               "partial": True,
               "summary": {"dim": "D2", "suite": "mcq-refill", "endpoint": ep.name,
                           "model": ep.model, "n": len(out), "n_valid": len(valid),
                           "n_errors": len(out) - len(valid), "correct": cor,
                           "accuracy": (cor / len(valid)) if valid else 0.0,
                           "elapsed_s": dt},
               "records": out}
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="含 records 的原始结果文件")
    ap.add_argument("--out", default="", help="补跑结果(默认 results/D2_refill_<时间>.json)")
    a = ap.parse_args()

    with io.open(a.src, encoding="utf-8") as f:
        src = json.load(f)
    recs = src.get("records") or []
    need = [r.get("id") for r in recs if r.get("error")]
    if not need:
        print("没有需要补跑的题")
        return 0

    rows = {}
    for line in io.open(os.path.join(ROOT, "datasets", "full", "D2_full.jsonl"),
                        encoding="utf-8"):
        line = line.strip()
        if line:
            x = json.loads(line)
            rows[x.get("id")] = x

    eps, _ = load_endpoints(os.path.join(ROOT, "config", "endpoints.json"))
    ep = eps.get("queue")
    if not ep:
        print("config/endpoints.json 里没有 queue 端点")
        return 2
    ep.max_tokens = 16384
    ep.deadline_s = 300

    path_partial = os.path.join(ROOT, "results", "D2_refill_partial.json")
    out = []
    if os.path.isfile(path_partial):
        try:
            with io.open(path_partial, encoding="utf-8") as f:
                out = json.load(f).get("records") or []
            print("续跑:已有 %d 题" % len(out))
        except Exception:
            out = []
    done_ids = {x.get("id") for x in out if not x.get("error")}
    need = [q for q in need if q not in done_ids]
    print("待补跑 %d 题" % len(need))
    t0 = time.perf_counter()
    for i, qid in enumerate(need, 1):
        r = rows.get(qid)
        if not r:
            continue
        opts = r.get("options") or {}
        if isinstance(opts, list):
            opts = {p.split(".", 1)[0].strip(): p.split(".", 1)[1].strip()
                    for p in opts if "." in p}
        opt_txt = chr(10).join("%s. %s" % (k, v) for k, v in sorted(opts.items()))
        prompt = (r["question"] + chr(10) + chr(10) + opt_txt + chr(10) + chr(10) +
                  "Reply with the single best option letter only, on a final line: answer: <letter>")
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            v = judge_mcq(res["text"], r.get("answer_letter"))
            rec = {"id": qid, "subcat": r.get("subcat"), "question": r["question"],
                   "expected": r.get("answer_letter"), "response": res["text"],
                   "latency_s": res["latency_s"], "refilled": True, **v}
        except Exception as e:
            rec = {"id": qid, "subcat": r.get("subcat"), "question": r["question"],
                   "expected": r.get("answer_letter"), "response": "",
                   "correct": False, "error": True, "error_reason": str(e)[:150]}
        out.append(rec)
        if True:   # 每题都落盘:长题(单题几千 token)下,10 题的间隔能拖十几分钟
            ok = sum(1 for x in out if x.get("correct"))
            print("  %d/%d  correct %d (%.0f%%)" % (i, len(need), ok,
                                                     ok / max(1, len(out)) * 100), flush=True)
            # 每 10 题落一次盘:进程被杀也不丢已完成的题
            _flush(path_partial, out, a.src, ep, time.perf_counter() - t0)

    dt = time.perf_counter() - t0
    valid = [x for x in out if not x.get("error")]
    cor = sum(1 for x in valid if x.get("correct"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = a.out or os.path.join(ROOT, "results", "D2_refill_%s.json" % stamp)
    if os.path.isfile(path_partial):
        try:
            os.remove(path_partial)
        except OSError:
            pass
    payload = {
        "preset": "KV17-Bonsai2T5-200K-refill",
        "source_file": os.path.basename(a.src),
        "summary": {
            "dim": "D2", "suite": "mcq-refill", "endpoint": ep.name, "model": ep.model,
            "n": len(out), "n_valid": len(valid), "n_errors": len(out) - len(valid),
            "correct": cor, "accuracy": (cor / len(valid)) if valid else 0.0,
            "elapsed_s": dt, "by_subcat": {},
        },
        "records": out,
    }
    import judge_version
    payload["judge_version"] = judge_version.judge_version("mcq")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(chr(10) + "补跑完成: %d/%d 有效, 正确 %d (%.1f%%)" % (
        len(valid), len(out), cor, (cor / len(valid) * 100) if valid else 0))
    print("-> " + os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
