# -*- coding: utf-8 -*-
"""export_prompts.py - EXPORT a test level's prompts to JSONL so an external model
(including an assistant working through tool calls) can answer them offline.

This is how a "cloud run" happens without an HTTP API key:
  1) python runner/export_prompts.py --level smoke --out cloud_smoke_prompts.jsonl
  2) the external model writes cloud_smoke_responses.jsonl  {id, text, tool_calls?}
  3) python runner/import_responses.py --level smoke --responses cloud_smoke_responses.jsonl

Usage: python runner/export_prompts.py --level smoke [--limit N] [--out FILE]
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
NL = chr(10)


def load_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="smoke")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--exclude", default="D9",
                    help="comma list of dims to exclude (D9 checks are derived from the "
                         "cloud baseline, so scoring the same model on them is circular)")
    args = ap.parse_args()

    path = os.path.join(ROOT, "datasets", "subsets", "%s.jsonl" % args.level)
    if not os.path.exists(path):
        print("no such level:", args.level)
        return
    rows = load_jsonl(path)
    excl = set(x.strip() for x in args.exclude.split(",") if x.strip())
    rows = [r for r in rows if r.get("dim") not in excl]
    if args.limit:
        rows = rows[:args.limit]

    out = args.out or os.path.join(ROOT, "cloud_%s_prompts.jsonl" % args.level)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            rec = {"id": r.get("id"), "dim": r.get("dim"), "subcat": r.get("subcat")}
            d = r.get("dim")
            if d == "D6":
                sub = r.get("subcat")
                if sub == "multi-turn-state":
                    rec["messages"] = [{"role": m["role"], "content": m["content"]}
                                       for m in r.get("dialogue", [])
                                       if m.get("content") and m.get("role") in ("user", "assistant")]
                else:
                    rec["messages"] = [{"role": "user", "content": r.get("user", "")}]
                    from run import tool_specs
                    rec["tools"] = tool_specs(r.get("tools") or [])
            elif d == "D7":
                parts = []
                if r.get("context_file"):
                    parts.append(open(os.path.join(ROOT, "datasets", r["context_file"]),
                                      encoding="utf-8").read())
                for cf in r.get("context_files", []) or []:
                    parts.append(open(os.path.join(ROOT, "datasets", cf), encoding="utf-8").read())
                rec["messages"] = [{"role": "user",
                                    "content": NL.join(parts) + NL + NL + r["question"]}]
            elif d == "D4":
                if r.get("subcat") == "sql":
                    rec["messages"] = [{"role": "user", "content":
                        "数据库表结构:" + NL + (r.get("schema") or "") + NL + NL +
                        (r.get("question") or "") + NL + "请只输出一条 SQL,放在 ```sql 代码块中。"}]
                elif r.get("subcat") == "debug":
                    rec["messages"] = [{"role": "user", "content":
                        "下面这段 Python 代码有问题:" + NL + "```python" + NL + (r.get("buggy") or "") +
                        NL + "```" + NL + (r.get("task") or "") +
                        NL + "请给出修复后的完整函数,放在 ```python 代码块中。"}]
                else:
                    rec["messages"] = [{"role": "user", "content":
                        "请补全下面的 Python 函数实现:" + NL + "```python" + NL + (r.get("prompt") or "") +
                        NL + "```" + NL + "请只输出完整实现,放在 ```python 代码块中。"}]
            else:
                txt = r.get("user") or r.get("question") or ""
                if d == "D8" and r.get("external_content"):
                    txt = (r.get("user") or "") + NL + NL + "--- 待处理内容 ---" + NL + r["external_content"]
                if d == "D2" and r.get("options"):
                    # Same two shapes as _prompt_for: dict (normal) and a legacy
                    # list. .items() on the list raised and killed the export.
                    _o = r["options"]
                    if isinstance(_o, dict):
                        opt = NL.join("%s. %s" % (k, v) for k, v in sorted(_o.items()))
                    elif isinstance(_o, list):
                        opt = NL.join(str(x) for x in _o)
                    else:
                        opt = ""
                    txt = (r.get("question") or "") + NL + NL + opt + NL + NL + \
                          "Reply with the single best option letter only, on a final line: answer: <letter>"
                if d == "D3":
                    txt = (r.get("question") or "") + NL + NL + "Please end with a line: answer: <value>"
                rec["messages"] = [{"role": "user", "content": txt}]
            f.write(json.dumps(rec, ensure_ascii=False) + NL)
    from collections import Counter
    print("exported %d prompts -> %s" % (len(rows), os.path.relpath(out, ROOT)))
    print("  ", dict(sorted(Counter(r["dim"] for r in rows).items())))
    print(NL + "next: have the model write responses as {id, text, tool_calls?} per line, then")
    print("      python runner/import_responses.py --level %s --responses <file>" % args.level)


if __name__ == "__main__":
    main()
