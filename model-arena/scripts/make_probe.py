# -*- coding: utf-8 -*-
"""make_probe.py - build contamination probes from public benchmark items.

Public sets (MMLU/GPQA/...) are almost certainly in a fine-tuned model's training
data, so a high score may just mean memorization. Two probes, both cheap:

  shuffle    permute the MCQ options and remap the answer letter. A model that
             recalls "the answer is D" fails; a model that reasons does not.
             Needs no LLM and is fully deterministic.
  paraphrase reword the question (needs an LLM; --via deepseek). Catches rote
             recall of the exact wording. Complements shuffle.

Usage:
  python scripts/make_probe.py --mode shuffle --limit 120
  python scripts/make_probe.py --mode paraphrase --via deepseek --limit 60
"""
import argparse, json, os, random, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runner"))
OUT = os.path.join(ROOT, "datasets", "probe")
SEED = 20261010
NL = chr(10)


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def shuffle_mcq(rows, rng, limit):
    """Permute options; the answer letter follows its option text."""
    src = [r for r in rows if r.get("options") and r.get("answer_letter")]
    rng.shuffle(src)
    out = []
    for r in src[:limit]:
        opts = dict(r["options"])
        correct_text = opts.get(r["answer_letter"])
        items = list(opts.items())
        rng.shuffle(items)
        letters = "ABCD"
        new_opts = {}
        new_ans = None
        for i, (_, text) in enumerate(items):
            new_opts[letters[i]] = text
            if text == correct_text:
                new_ans = letters[i]
        if new_ans is None or new_ans == r["answer_letter"]:
            continue                      # skip no-op shuffles: they carry no signal
        q = dict(r)
        q["id"] = r["id"] + "_shuf"
        q["options"] = new_opts
        q["answer_letter"] = new_ans
        q["probe_of"] = r["id"]
        q["probe_mode"] = "shuffle"
        out.append(q)
    return out


def paraphrase(rows, limit, via, rng):
    """Reword the question with an API model. Keeps the answer letter valid."""
    sys.path.insert(0, os.path.join(ROOT, "runner"))
    from endpoint import load_endpoints
    eps, _ = load_endpoints(os.path.join(ROOT, "config", "endpoints.json"))
    ep = eps.get(via)
    if ep is None:
        print("no such endpoint:", via); return []
    src = list(rows)
    if not limit or limit >= len(src):
        pass
    else:
        src = src[:limit]
    out, done = [], 0
    for r in src:
        prompt = ("Rewrite the following multiple-choice question in different words. "
                  "Keep the meaning and all four options exactly as they are. "
                  "Output ONLY the rewritten question and the four options, "
                  "one per line, no answer." + NL * 2 +
                  "QUESTION: " + (r.get("question") or "") + NL +
                  NL.join("%s. %s" % (k, v) for k, v in sorted((r.get("options") or {}).items())))
        try:
            res = ep.chat([{"role": "user", "content": prompt}])
            txt = (res.get("text") or "").strip()
        except Exception as e:
            print("   paraphrase failed:", str(e)[:80]); break
        if len(txt) < 20:
            continue
        q = dict(r)
        q["id"] = r["id"] + "_para"
        q["question"] = txt
        q["probe_of"] = r["id"]
        q["probe_mode"] = "paraphrase"
        out.append(q)
        done += 1
        if done % 10 == 0:
            print("   %d/%d" % (done, len(src)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="shuffle", choices=["shuffle", "paraphrase"])
    ap.add_argument("--via", default="deepseek")
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--sources", default="mmlu,gpqa")
    ap.add_argument("--out", default="")
    ap.add_argument("--ids-from", default="",
                    help="锁定与已有探针文件相同的源题(用 probe_of 字段),保证可对比")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    rng = random.Random(SEED)
    rows = []
    for s in [x.strip() for x in args.sources.split(",") if x.strip()]:
        p = os.path.join(ROOT, "datasets", "public", "%s.jsonl" % s)
        if os.path.exists(p):
            got = load(p)
            print("  %-8s %d 题" % (s, len(got)))
            rows.extend(got)
    if args.ids_from:
        lock = os.path.join(ROOT, args.ids_from)
        if os.path.exists(lock):
            want = set(r["probe_of"] for r in load(lock) if r.get("probe_of"))
            before = len(rows)
            rows = [r for r in rows if r["id"] in want]
            print("  锁定源题: %d -> %d 题(与 %s 对齐)" % (before, len(rows),
                                                          os.path.basename(lock)))
    if not rows:
        print("no public questions found; run scripts/fetch_public.py first")
        return
    if args.mode == "shuffle":
        out = shuffle_mcq(rows, rng, args.limit)
    else:
        out = paraphrase(rows, args.limit, args.via, rng)
    name = args.out or ("%s_probe.jsonl" % args.mode)
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + NL)
    print(NL + "probe[%s] -> %d 题  %s" % (args.mode, len(out), os.path.relpath(path, ROOT)))
    print("run it, then: python runner/contamination.py --orig <原题结果> --probe <探针结果>")


if __name__ == "__main__":
    main()
