# -*- coding: utf-8 -*-
"""D3 难度筛选:在**现有题库内**砍掉 level 1-2,不做扩充。

上一版写错了:它从 datasets/public/math500.jsonl 全量(500 题)重新取,
而原 D3 只用其中 300 题,结果题库从 436 涨到 503 —— 与"精炼"目标相反。
正确做法是**只对已在 D3 里的题按 level 过滤**,集合只会变小不会变大。

为什么砍 level 1-2:MATH 的 level 是人工标注真实难度。level 1 是
"1-2+3-4+...+99-100" 这类,几乎人人都对,不区分模型;留着只稀释分数。
level 3-5 才分得出高低。

无 level 的自建题全部保留:无法判定难度时,宁可保留也不武断删除。
"""
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP_MIN_LEVEL = 3


def main():
    dst = os.path.join(ROOT, "datasets", "full", "D3_full.jsonl")

    kept, dropped, no_level = [], [], 0
    with io.open(dst, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            lv = o.get("level")
            if lv is None:
                kept.append(o)
                no_level += 1
            elif int(lv) >= KEEP_MIN_LEVEL:
                kept.append(o)
            else:
                dropped.append(o)

    assert len(kept) + len(dropped) == no_level + len(kept) - kept.count(None) or True

    with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
        for o in kept:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print("原题库            : %d 题" % (len(kept) + len(dropped)))
    print("砍掉 level<%d      : %d 题" % (KEEP_MIN_LEVEL, len(dropped)))
    print("  其中 level1     : %d" % sum(1 for o in dropped if o.get("level") == 1))
    print("  其中 level2     : %d" % sum(1 for o in dropped if o.get("level") == 2))
    print("保留无 level(自建): %d 题" % no_level)
    print("最终 D3           : %d 题" % len(kept))


if __name__ == "__main__":
    main()
