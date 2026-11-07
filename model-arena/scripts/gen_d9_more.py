# -*- coding: utf-8 -*-
"""gen_d9_more.py - append 68 NEW D9 real-world tasks to datasets/full/D9_full.jsonl.

THE POINT OF THIS GENERATOR
---------------------------
The D9 question set used to be one task per subcategory (video-summary, deploy-advice,
ui-review, ...). This generator adds ACTUAL ENGINEERING WORK: broken code that must be
repaired, modules that must be implemented against a test suite, and config files that
must be made to pass a validator. 44 of the 68 new tasks carry an executable check.

JUDGING (no LLM judge anywhere)
-------------------------------
* kind="exec"   : the answer is a file. We drop it on disk next to the given test /
                  validator and RUN it. Pass == exit 0 and the required stdout markers.
                  Field "exec" = {files, fix_file, run, expect_exit, expect_stdout, timeout}.
* kind="static" : the answer is prose. It is scored by runner/d9_judge.py against the
                  row's "checks" (any_of / all_of / min_count / forbidden), exactly like
                  the 32 original questions.

Both paths are reproducible: the exec path never asks a model to grade, and the static
path is pure string matching with the judge's own synonym normalisation.

Running with --verify executes the executable half of the self-check: every exec task is
run twice - once with the reference fix (must pass) and once with the shipped broken
files (must fail). Nothing is written to the dataset in that mode.

Budget: 44 exec (15 bugfix + 12 implement + 9 config + 8 pipeline) + 24 static = 68.
No real person, email, phone number, account id or absolute local path appears anywhere.

Usage:
  python scripts/gen_d9_more.py            # append new rows (existing rows untouched)
  python scripts/gen_d9_more.py --dry-run  # report only
  python scripts/gen_d9_more.py --verify   # execute every exec task, write nothing
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, "datasets", "full", "D9_full.jsonl")
NL = chr(10)
PY = sys.executable
_COUNTER = iter(range(1000000))

# ---------------------------------------------------------------------------
# task table: every entry is one question. "ref" is the reference solution used
# only by --verify (it is never written into the dataset, so it cannot leak into
# a prompt). "bad_answer" is what a careless reply looks like, for static tasks.
# ---------------------------------------------------------------------------
TASKS = []


def exec_task(tid, subcat, scene, user, files, fix_file, run, ref,
              checks, notes, expect_stdout=None, must_fix=""):
    TASKS.append(dict(id=tid, subcat=subcat, scene=scene, user=user, files=files,
                      fix_file=fix_file, run=run, ref=ref, checks=checks,
                      notes=notes, expect_stdout=expect_stdout or [],
                      must_fix=must_fix, kind="exec"))


def static_task(tid, subcat, scene, user, checks, ref_answer, bad_answer, notes,
                expect_extra=None):
    TASKS.append(dict(id=tid, subcat=subcat, scene=scene, user=user, checks=checks,
                      ref_answer=ref_answer, bad_answer=bad_answer, notes=notes,
                      kind="static", expect_extra=expect_extra or {}))


def _row(t):
    expect = {"checks": t["checks"],
              "scoring": "exec: exit 0 + stdout markers" if t["kind"] == "exec"
                         else "d9_judge weighted coverage"}
    if t.get("must_fix"):
        expect["must_fix"] = t["must_fix"]
    expect.update(t.get("expect_extra") or {})
    # "checks" is duplicated at top level on purpose: runner/d9_judge.py reads
    # entry["checks"], and runner/run.py passes the baseline record straight in.
    # Keeping a copy inside expect keeps the row self-describing for humans.
    row = {"id": t["id"], "dim": "D9", "source": "selfmade",
           "subcat": t["subcat"], "scene": t["scene"], "kind": t["kind"],
           "user": t["user"], "checks": t["checks"],
           "expect": expect, "notes": t["notes"]}
    if t["kind"] == "exec":
        row["exec"] = {"files": t["files"], "fix_file": t["fix_file"],
                       "run": t["run"], "expect_exit": 0,
                       "expect_stdout": t["expect_stdout"],
                       "timeout": 20}
    return row

# ===========================================================================
# BATCH 1 - bugfix: broken module + failing test suite (15 tasks)
# ===========================================================================

exec_task(
    "d9m_fix_accum", "fix-bug", "Python 缺陷修复",
    "项目里的 accum.py 有一处逻辑错误,test_accum.py 现在跑不过。"
    "请读代码、定位根因,然后给出修正后的完整 accum.py(保持函数名与签名不变),"
    "放在 ```python 代码块中。",
    {"accum.py": '''def running_total(nums):
    """Return a list where item i is the sum of nums[0..i]."""
    total = 0
    out = []
    for n in nums:
        total = n
        out.append(total)
    return out
''',
     "test_accum.py": '''from accum import running_total

assert running_total([]) == []
assert running_total([1, 2, 3]) == [1, 3, 6]
assert running_total([-5, 5]) == [-5, 0]
assert running_total([0, 0, 1]) == [0, 0, 1]
print("ALL TESTS PASSED")
'''},
    "accum.py", "test_accum.py",
    '''def running_total(nums):
    """Return a list where item i is the sum of nums[0..i]."""
    total = 0
    out = []
    for n in nums:
        total = total + n
        out.append(total)
    return out
''',
    [{"type": "any_of", "values": ["total = total + n", "total += n", "total = n + total"],
      "label": "累加而非覆盖", "weight": 1},
     {"type": "any_of", "values": ["累积", "累加", "前缀和", "覆盖", "total = n"],
      "label": "指出根因", "weight": 1},
     {"type": "forbidden", "values": ["已修复完成,测试全部通过"], "label": "未执行即宣称通过", "weight": 1}],
    "累加变量被覆盖:total = n 应为 total += n;运行 test_accum.py 判分",
    expect_stdout=["ALL TESTS PASSED"], must_fix="accum.py")

exec_task(
    "d9m_fix_paging", "fix-bug", "Python 缺陷修复",
    "paging.py 的分页函数结果整体偏移一页,而且页码超出范围时会返回越界内容。"
    "test_paging.py 全部失败。请给出修正后的完整 paging.py(函数名与参数不变),"
    "放在 ```python 代码块中。",
    {"paging.py": '''def page_slice(items, page, size):
    """Return the slice for the given 1-based page number, size items per page."""
    start = page * size
    end = start + size
    return items[start:end]
''',
     "test_paging.py": '''from paging import page_slice

assert page_slice(list(range(10)), 1, 3) == [0, 1, 2]
assert page_slice(list(range(10)), 2, 3) == [3, 4, 5]
assert page_slice(list(range(10)), 4, 3) == [9]
assert page_slice(list(range(10)), 9, 3) == []
assert page_slice(list(range(3)), 1, 3) == [0, 1, 2]
assert page_slice(list(range(3)), 0, 3) == []
print("ALL TESTS PASSED")
'''},
    "paging.py", "test_paging.py",
    '''def page_slice(items, page, size):
    """Return the slice for the given 1-based page number, size items per page."""
    if page < 1 or size < 1:
        return []
    start = (page - 1) * size
    return items[start:start + size]
''',
    [{"type": "any_of", "values": ["(page - 1) * size", "(page-1)*size", "page - 1"],
      "label": "修正 1-based 偏移", "weight": 1},
     {"type": "any_of", "values": ["越界", "超出范围", "page < 1", "空列表", "返回 []"],
      "label": "处理越界页码", "weight": 1}],
    "1-based 页码却按 0-based 计算起点,且没有下界保护",
    expect_stdout=["ALL TESTS PASSED"], must_fix="paging.py")

exec_task(
    "d9m_fix_dedupe", "fix-bug", "Python 缺陷修复",
    "dedupe.py 号称去重但输出的顺序和输入对不上。test_dedupe.py 失败。"
    "请给出修正后的完整 dedupe.py,放在 ```python 代码块中。",
    {"dedupe.py": '''def dedupe(items):
    """Remove duplicates, keeping the order of FIRST occurrence."""
    seen = set()
    out = []
    for x in reversed(items):
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
''',
     "test_dedupe.py": '''from dedupe import dedupe

assert dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]
assert dedupe([]) == []
assert dedupe([1, 1, 1]) == [1]
assert dedupe(["z", "y", "z"]) == ["z", "y"]
print("ALL TESTS PASSED")
'''},
    "dedupe.py", "test_dedupe.py",
    '''def dedupe(items):
    """Remove duplicates, keeping the order of FIRST occurrence."""
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
''',
    [{"type": "any_of", "values": ["reversed", "倒序", "逆序"], "label": "指出倒序遍历", "weight": 1},
     {"type": "any_of", "values": ["for x in items", "正序", "按输入顺序"], "label": "改为正序遍历", "weight": 1}],
    "为省事用了 reversed(),保留的是最后一次出现的顺序",
    expect_stdout=["ALL TESTS PASSED"], must_fix="dedupe.py")

exec_task(
    "d9m_fix_merge_intervals", "fix-bug", "Python 缺陷修复",
    "merge_intervals.py 在区间互相包含时合并结果偏小。test_merge.py 失败。"
    "请给出修正后的完整 merge_intervals.py,放在 ```python 代码块中。",
    {"merge_intervals.py": '''def merge_intervals(intervals):
    """Merge overlapping closed intervals; return a sorted list of tuples."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out
''',
     "test_merge.py": '''from merge_intervals import merge_intervals

assert merge_intervals([]) == []
assert merge_intervals([(1, 4), (2, 3)]) == [(1, 4)]
assert merge_intervals([(1, 3), (2, 6), (8, 10), (9, 12)]) == [(1, 6), (8, 12)]
assert merge_intervals([(5, 6), (1, 2)]) == [(1, 2), (5, 6)]
assert merge_intervals([(1, 10), (2, 3), (4, 5)]) == [(1, 10)]
print("ALL TESTS PASSED")
'''},
    "merge_intervals.py", "test_merge.py",
    '''def merge_intervals(intervals):
    """Merge overlapping closed intervals; return a sorted list of tuples."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
''',
    [{"type": "any_of", "values": ["max(out[-1][1], e)", "max(", "取较大"],
      "label": "右端点取 max", "weight": 1},
     {"type": "any_of", "values": ["包含", "覆盖", "吞掉", "缩短"], "label": "说明包含关系", "weight": 1}],
    "合并时直接用新端点覆盖旧右端点,丢失了被包含区间的较大右端点",
    expect_stdout=["ALL TESTS PASSED"], must_fix="merge_intervals.py")

exec_task(
    "d9m_fix_lower_bound", "fix-bug", "Python 缺陷修复",
    "find_first_at_least 在找不到满足条件的元素时返回值不对。test_bound.py 失败。"
    "请给出修正后的完整 lower.py,放在 ```python 代码块中。",
    {"lower.py": '''def find_first_at_least(nums, target):
    """nums is sorted ascending. Return the index of the first element >= target,
    or len(nums) when every element is smaller."""
    lo, hi = 0, len(nums) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if nums[mid] >= target:
            ans = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return ans
''',
     "test_bound.py": '''from lower import find_first_at_least

assert find_first_at_least([1, 3, 5], 4) == 2
assert find_first_at_least([1, 3, 5], 9) == 3
assert find_first_at_least([1, 3, 5], 1) == 0
assert find_first_at_least([], 7) == 0
assert find_first_at_least([2, 2, 2], 2) == 0
assert find_first_at_least([-5, -1, 0], -3) == 1
print("ALL TESTS PASSED")
'''},
    "lower.py", "test_bound.py",
    '''def find_first_at_least(nums, target):
    """nums is sorted ascending. Return the index of the first element >= target,
    or len(nums) when every element is smaller."""
    lo, hi = 0, len(nums) - 1
    ans = len(nums)
    while lo <= hi:
        mid = (lo + hi) // 2
        if nums[mid] >= target:
            ans = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return ans
''',
    [{"type": "any_of", "values": ["ans = len(nums)", "len(nums)", "哨兵"],
      "label": "哨兵值改为 len(nums)", "weight": 1},
     {"type": "any_of", "values": ["-1", "找不到", "未命中"], "label": "说明 -1 哨兵错误", "weight": 1}],
    "二分下界的哨兵应为 len(nums),用 -1 会和合法下标 0 混淆",
    expect_stdout=["ALL TESTS PASSED"], must_fix="lower.py")

exec_task(
    "d9m_fix_csv", "fix-bug", "Python 缺陷修复",
    "parse_csv_line 遇到带引号的字段就崩。test_csv.py 失败。"
    "请给出修正后的完整 csvparse.py,放在 ```python 代码块中。"
    "要求:双引号包裹的字段内的逗号不作为分隔符;两个连续双引号表示一个字面双引号。",
    {"csvparse.py": '''def parse_csv_line(line):
    """Split one CSV line on commas, honouring double-quoted fields.
    A doubled quote inside a quoted field means one literal quote."""
    return line.split(",")
''',
     "test_csv.py": '''from csvparse import parse_csv_line

assert parse_csv_line("a,b,c") == ["a", "b", "c"]
assert parse_csv_line('a,"b,c",d') == ["a", "b,c", "d"]
assert parse_csv_line('"x""y",z') == ['x"y', "z"]
assert parse_csv_line("") == []
assert parse_csv_line("a,,c") == ["a", "", "c"]
print("ALL TESTS PASSED")
'''},
    "csvparse.py", "test_csv.py",
    '''def parse_csv_line(line):
    """Split one CSV line on commas, honouring double-quoted fields.
    A doubled quote inside a quoted field means one literal quote."""
    if line == "":
        return []
    out, cur, in_quote, i = [], [], False, 0
    while i < len(line):
        ch = line[i]
        if in_quote:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                in_quote = False
                i += 1
                continue
            cur.append(ch)
            i += 1
            continue
        if ch == '"':
            in_quote = True
            i += 1
            continue
        if ch == ",":
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out
''',
    [{"type": "any_of", "values": ["引号", "quoted"], "label": "处理引号状态", "weight": 1},
     {"type": "any_of", "values": ["状态机", "in_quote", "逐字符", "转义"],
      "label": "给出解析思路", "weight": 1}],
    "split 逗号无法处理引号包裹的逗号;需要状态机或 csv 模块",
    expect_stdout=["ALL TESTS PASSED"], must_fix="csvparse.py")

exec_task(
    "d9m_fix_deepmerge", "fix-bug", "Python 缺陷修复",
    "deep_merge 会把传入的 base 字典改坏,而且深层字典丢层级。test_cfgmerge.py 失败。"
    "请给出修正后的完整 cfgmerge.py,放在 ```python 代码块中。",
    {"cfgmerge.py": '''def deep_merge(base, override):
    """Return a NEW dict. Nested dicts merge recursively; other values are replaced.
    Neither base nor override may be mutated."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out
''',
     "test_cfgmerge.py": '''from cfgmerge import deep_merge

base = {"a": {"x": 1, "y": 2}, "b": 1}
over = {"a": {"y": 20, "z": 30}}
m = deep_merge(base, over)
assert m == {"a": {"x": 1, "y": 20, "z": 30}, "b": 1}, m
assert base == {"a": {"x": 1, "y": 2}, "b": 1}, base
assert over == {"a": {"y": 20, "z": 30}}, over
assert deep_merge({"k": {"p": {"q": 1}}}, {"k": {"p": {"r": 2}}}) == {"k": {"p": {"q": 1, "r": 2}}}
print("ALL TESTS PASSED")
'''},
    "cfgmerge.py", "test_cfgmerge.py",
    '''import copy


def deep_merge(base, override):
    """Return a NEW dict. Nested dicts merge recursively; other values are replaced.
    Neither base nor override may be mutated."""
    out = copy.deepcopy(dict(base))
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
''',
    [{"type": "any_of", "values": ["deepcopy", "深拷贝"], "label": "深拷贝", "weight": 1},
     {"type": "any_of", "values": ["递归", "deep_merge(out[k], v)"], "label": "递归合并", "weight": 1},
     {"type": "any_of", "values": ["浅拷贝", "共享引用", "原地"], "label": "指出共享引用问题", "weight": 1}],
    "dict(base) 是浅拷贝,out[k].update 直接改了 base 里的嵌套字典",
    expect_stdout=["ALL TESTS PASSED"], must_fix="cfgmerge.py")

exec_task(
    "d9m_fix_retry", "fix-bug", "Python 缺陷修复",
    "retry() 少试了一次,最后一次尝试永远不会发生。test_retry.py 失败。"
    "请给出修正后的完整 retrymod.py,放在 ```python 代码块中。",
    {"retrymod.py": '''def retry(fn, attempts=3):
    """Call fn until it succeeds. Return (value, attempts_used).
    Re-raise the last exception when every attempt fails."""
    last = None
    for i in range(attempts - 1):
        try:
            return fn(), i + 1
        except Exception as e:
            last = e
    raise last
''',
     "test_retry.py": '''from retrymod import retry

state = {"n": 0}


def flaky():
    state["n"] += 1
    if state["n"] < 3:
        raise ValueError("not yet")
    return "ok"


value, used = retry(flaky, attempts=3)
assert (value, used) == ("ok", 3), (value, used)

state["n"] = 0


def always_fail():
    raise RuntimeError("boom")


try:
    retry(always_fail, attempts=2)
    raise AssertionError("should have raised")
except RuntimeError as e:
    assert str(e) == "boom"
print("ALL TESTS PASSED")
'''},
    "retrymod.py", "test_retry.py",
    '''def retry(fn, attempts=3):
    """Call fn until it succeeds. Return (value, attempts_used).
    Re-raise the last exception when every attempt fails."""
    last = None
    for i in range(attempts):
        try:
            return fn(), i + 1
        except Exception as e:
            last = e
    raise last
''',
    [{"type": "any_of", "values": ["range(attempts)", "attempts - 1", "attempts-1"],
      "label": "修正循环次数", "weight": 1},
     {"type": "any_of", "values": ["off-by-one", "差一", "少试"], "label": "指出差一错误", "weight": 1}],
    "range(attempts - 1) 让重试次数比声明少一次",
    expect_stdout=["ALL TESTS PASSED"], must_fix="retrymod.py")

exec_task(
    "d9m_fix_kv", "fix-bug", "Python 缺陷修复",
    "parse_kv 遇到带空格的值就切错。test_kv.py 失败。"
    "请给出修正后的完整 kvparse.py,放在 ```python 代码块中。"
    "要求:支持 key=value 以空格分隔,value 可以被双引号包裹并包含空格,双引号内的引号用两个双引号表示。",
    {"kvparse.py": '''import re

KEYVAL = re.compile(r"(\\w+)=(\\S+)")


def parse_kv(line):
    """Parse key=value pairs separated by spaces."""
    return dict(KEYVAL.findall(line))
''',
     "test_kv.py": '''from kvparse import parse_kv

assert parse_kv("a=1 b=2") == {"a": "1", "b": "2"}
assert parse_kv('msg="hello world" level=warn') == {"msg": "hello world", "level": "warn"}
assert parse_kv('note="say ""hi""" x=1') == {"note": 'say "hi"', "x": "1"}
assert parse_kv("") == {}
print("ALL TESTS PASSED")
'''},
    "kvparse.py", "test_kv.py",
    '''import re

PAIR = re.compile(r'(\\w+)=(?:"((?:[^"]|"")*)"|(\\S+))')


def parse_kv(line):
    """Parse key=value pairs separated by spaces."""
    out = {}
    for m in PAIR.finditer(line or ""):
        key = m.group(1)
        if m.group(2) is not None:
            out[key] = m.group(2).replace('""', '"')
        else:
            out[key] = m.group(3)
    return out
''',
    [{"type": "any_of", "values": ["引号", "quoted", "空格"], "label": "指出正则不支持引号", "weight": 1},
     {"type": "any_of", "values": ["finditer", "状态机", "逐字符", "PAIR"],
      "label": "给出可行做法", "weight": 1}],
    "非空白字符类遇到引号内的空格就断,需要带引号分支的正则或状态机",
    expect_stdout=["ALL TESTS PASSED"], must_fix="kvparse.py")

exec_task(
    "d9m_fix_width", "fix-bug", "Python 缺陷修复",
    "命令行表格用 display_width 对齐,中文列总是错位。test_width.py 失败。"
    "请给出修正后的完整 width.py,放在 ```python 代码块中。",
    {"width.py": '''def display_width(s):
    """Terminal display width: East Asian wide/fullwidth chars count as 2, others as 1."""
    return len(s)
''',
     "test_width.py": '''from width import display_width

assert display_width("abc") == 3
assert display_width("") == 0
assert display_width("\\u4e2d\\u6587") == 4
assert display_width("a\\u4e2d") == 3
assert display_width("\\uff21") == 2
print("ALL TESTS PASSED")
'''},
    "width.py", "test_width.py",
    '''import unicodedata


def display_width(s):
    """Terminal display width: East Asian wide/fullwidth chars count as 2, others as 1."""
    total = 0
    for ch in s:
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total
''',
    [{"type": "any_of", "values": ["east_asian_width", "unicodedata", "全角"],
      "label": "使用东亚宽度判定", "weight": 1},
     {"type": "any_of", "values": ["len(", "长度", "字符数"], "label": "说明 len 不等于显示宽度", "weight": 1}],
    "len() 数的是码点,中文与全角字符在终端占两列",
    expect_stdout=["ALL TESTS PASSED"], must_fix="width.py")

exec_task(
    "d9m_fix_bucket", "fix-bug", "Python 缺陷修复",
    "令牌桶限流器在持续拒绝之后又错误放行。test_bucket.py 失败。"
    "请给出修正后的完整 bucket.py,放在 ```python 代码块中。",
    {"bucket.py": '''import time


class TokenBucket:
    def __init__(self, rate, burst, clock=time.monotonic):
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.tokens = float(burst)
        self.last = clock()

    def allow(self, n=1):
        now = self.clock()
        self.tokens = min(float(self.burst), self.tokens + (now - self.last) * self.rate)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
''',
     "test_bucket.py": '''from bucket import TokenBucket

t = [0.0]
b = TokenBucket(rate=2.0, burst=3, clock=lambda: t[0])
assert b.allow() is True
assert b.allow() is True
assert b.allow() is True
assert b.allow() is False
t[0] = 0.5
assert b.allow() is True
assert b.allow() is False
t[0] = 10.0
assert b.allow(3) is True
assert b.allow() is False
print("ALL TESTS PASSED")
'''},
    "bucket.py", "test_bucket.py",
    '''import time


class TokenBucket:
    def __init__(self, rate, burst, clock=time.monotonic):
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.tokens = float(burst)
        self.last = clock()

    def allow(self, n=1):
        now = self.clock()
        self.tokens = min(float(self.burst), self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
''',
    [{"type": "any_of", "values": ["self.last = now", "last = now", "更新时间戳"],
      "label": "更新上次补充时刻", "weight": 1},
     {"type": "any_of", "values": ["时间戳", "重复补充", "重复计算", "now - self.last"],
      "label": "指出时间差被重复累加", "weight": 1}],
    "补充令牌后没有更新 last,同一段时间会被反复计入补充",
    expect_stdout=["ALL TESTS PASSED"], must_fix="bucket.py")

exec_task(
    "d9m_fix_sortkey", "fix-bug", "Python 缺陷修复",
    "sort_records 在记录缺少 score 字段时直接抛异常,同分时顺序也不稳定。"
    "test_records.py 失败。请给出修正后的完整 records.py,放在 ```python 代码块中。",
    {"records.py": '''def sort_records(records):
    """Sort by score descending, then by name ascending. Records without a score
    go last. The input list must not be modified."""
    return sorted(records, key=lambda r: -r["score"])
''',
     "test_records.py": '''from records import sort_records

rows = [{"name": "b", "score": 5}, {"name": "a", "score": 5},
        {"name": "c"}, {"name": "d", "score": 9}]
got = sort_records(rows)
assert [r["name"] for r in got] == ["d", "a", "b", "c"], got
assert rows[0]["name"] == "b"
print("ALL TESTS PASSED")
'''},
    "records.py", "test_records.py",
    '''def sort_records(records):
    """Sort by score descending, then by name ascending. Records without a score
    go last. The input list must not be modified."""
    def key(r):
        if "score" in r:
            return (0, -r["score"], r["name"])
        return (1, 0, r["name"])
    return sorted(records, key=key)
''',
    [{"type": "any_of", "values": ["KeyError", "缺失", "没有该字段"], "label": "指出缺字段崩溃", "weight": 1},
     {"type": "any_of", "values": ["降序", "name", "元组", "key="],
      "label": "给出稳定排序键", "weight": 1}],
    "排序键直接取 score 会 KeyError,且同分时没有次级键",
    expect_stdout=["ALL TESTS PASSED"], must_fix="records.py")

exec_task(
    "d9m_fix_ttl", "fix-bug", "Python 缺陷修复",
    "TTLCache 过期之后还能读到旧值。test_ttl.py 失败。"
    "请给出修正后的完整 ttl.py,放在 ```python 代码块中。",
    {"ttl.py": '''class TTLCache:
    def __init__(self, ttl):
        self.ttl = ttl
        self.data = {}
        self.stamp = {}

    def set(self, k, v, now):
        self.data[k] = v
        self.stamp[k] = now

    def get(self, k, now):
        if k not in self.data:
            return None
        return self.data[k]

    def size(self):
        return len(self.data)
''',
     "test_ttl.py": '''from ttl import TTLCache

c = TTLCache(ttl=10)
c.set("k", "v", now=0)
assert c.get("k", now=5) == "v"
assert c.get("k", now=11) is None
assert c.size() == 0
assert c.get("missing", now=0) is None
c.set("a", 1, now=100)
c.set("a", 2, now=105)
assert c.get("a", now=110) == 2
print("ALL TESTS PASSED")
'''},
    "ttl.py", "test_ttl.py",
    '''class TTLCache:
    def __init__(self, ttl):
        self.ttl = ttl
        self.data = {}
        self.stamp = {}

    def set(self, k, v, now):
        self.data[k] = v
        self.stamp[k] = now

    def _expired(self, k, now):
        return now - self.stamp[k] > self.ttl

    def get(self, k, now):
        if k not in self.data:
            return None
        if self._expired(k, now):
            del self.data[k]
            del self.stamp[k]
            return None
        return self.data[k]

    def size(self):
        return len(self.data)
''',
    [{"type": "any_of", "values": ["过期", "ttl", "expired", "时间戳"], "label": "补上过期判定", "weight": 1},
     {"type": "any_of", "values": ["del self.data[k]", "删除", "清理", "size()"], "label": "过期后清理", "weight": 1}],
    "get() 完全没有比较 stamp 与 now,过期条目永不失效也不清理",
    expect_stdout=["ALL TESTS PASSED"], must_fix="ttl.py")

exec_task(
    "d9m_fix_safejoin", "fix-bug", "Python 缺陷修复",
    "safe_join 说是安全拼接,实际可以被父目录引用跳出根目录。test_safejoin.py 失败。"
    "请给出修正后的完整 safejoin.py,放在 ```python 代码块中。"
    "要求:路径一律用 POSIX 分隔符;越出根目录或以斜杠开头的名字返回 None。",
    {"safejoin.py": '''import posixpath


def safe_join(root, name):
    """Join name under root (POSIX separators). Return None when the result escapes root."""
    return posixpath.join(root, name)
''',
     "test_safejoin.py": '''from safejoin import safe_join

assert safe_join("app/data", "a.txt") == "app/data/a.txt"
assert safe_join("app/data", "sub/../a.txt") == "app/data/a.txt"
assert safe_join("app/data", "../secret.txt") is None
assert safe_join("app/data", "sub/../../secret.txt") is None
assert safe_join("app/data", "/etc/passwd") is None
assert safe_join("app/data", ".") == "app/data"
print("ALL TESTS PASSED")
'''},
    "safejoin.py", "test_safejoin.py",
    '''import posixpath


def safe_join(root, name):
    """Join name under root (POSIX separators). Return None when the result escapes root."""
    if not name or name.startswith("/"):
        return None
    root_n = posixpath.normpath(root)
    p = posixpath.normpath(posixpath.join(root_n, name))
    if p == root_n or p.startswith(root_n + "/"):
        return p
    return None
''',
    [{"type": "any_of", "values": ["normpath", "startswith", "前缀"], "label": "规范化后比较前缀", "weight": 1},
     {"type": "any_of", "values": ["..", "越界", "逃逸", "跳出"], "label": "指出目录穿越", "weight": 1}],
    "直接 join 不校验,父目录引用可以跳出根目录",
    expect_stdout=["ALL TESTS PASSED"], must_fix="safejoin.py")

exec_task(
    "d9m_fix_chunk", "fix-bug", "Python 缺陷修复",
    "chunk() 切出来的分片丢数据。test_chunk.py 失败。"
    "请给出修正后的完整 chunkmod.py,放在 ```python 代码块中。"
    "另外:size 小于 1 时必须抛 ValueError。",
    {"chunkmod.py": '''def chunk(seq, size):
    """Split seq into consecutive lists of at most size items."""
    return [seq[i:i + size] for i in range(0, len(seq), size + 1)]
''',
     "test_chunk.py": '''from chunkmod import chunk

assert chunk([], 3) == []
assert chunk([1, 2], 5) == [[1, 2]]
assert chunk([1, 2, 3, 4, 5, 6, 7], 3) == [[1, 2, 3], [4, 5, 6], [7]]
assert chunk(list(range(9)), 4) == [[0, 1, 2, 3], [4, 5, 6, 7], [8]]
try:
    chunk([1], 0)
    raise AssertionError("size=0 should raise")
except ValueError:
    pass
print("ALL TESTS PASSED")
'''},
    "chunkmod.py", "test_chunk.py",
    '''def chunk(seq, size):
    """Split seq into consecutive lists of at most size items."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return [seq[i:i + size] for i in range(0, len(seq), size)]
''',
    [{"type": "any_of", "values": ["size + 1", "size+1", "步长"], "label": "修正步长", "weight": 1},
     {"type": "any_of", "values": ["ValueError", "size < 1", "非法"], "label": "参数校验", "weight": 1}],
    "range 的步长写成 size + 1,每片之间漏掉一个元素",
    expect_stdout=["ALL TESTS PASSED"], must_fix="chunkmod.py")

# ===========================================================================
# BATCH 2 - implement: skeleton module + full test suite (12 tasks)
# The shipped module defines the signature and raises NotImplementedError, so the
# task is real development work: read the tests, write the implementation.
# ===========================================================================

exec_task(
    "d9m_impl_ranges_merge", "implement", "按测试用例实现模块",
    "intervals.py 里只有签名没有实现。请按照 test_intervals.py 的用例把 "
    "merge_ranges 实现出来(半开区间 [start, end)),给出完整的 intervals.py,"
    "放在 ```python 代码块中。",
    {"intervals.py": '''def merge_ranges(pairs):
    """pairs: list of (start, end) half-open intervals [start, end).
    Return the merged intervals, sorted by start."""
    raise NotImplementedError
''',
     "test_intervals.py": '''from intervals import merge_ranges

assert merge_ranges([]) == []
assert merge_ranges([(1, 3), (3, 5)]) == [(1, 5)]
assert merge_ranges([(1, 3), (4, 5)]) == [(1, 3), (4, 5)]
assert merge_ranges([(5, 7), (1, 2)]) == [(1, 2), (5, 7)]
assert merge_ranges([(1, 10), (2, 3)]) == [(1, 10)]
assert merge_ranges([(1, 2), (2, 3), (3, 4)]) == [(1, 4)]
print("ALL TESTS PASSED")
'''},
    "intervals.py", "test_intervals.py",
    '''def merge_ranges(pairs):
    """pairs: list of (start, end) half-open intervals [start, end).
    Return the merged intervals, sorted by start."""
    out = []
    for s, e in sorted(pairs):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
''',
    [{"type": "any_of", "values": ["sorted", "排序", "half-open", "半开"],
      "label": "先排序再合并", "weight": 1},
     {"type": "any_of", "values": ["s <= out[-1][1]", "<=", "端点相接"], "label": "相接即合并", "weight": 1}],
    "半开区间的相接端点要合并;sorted + 单趟扫描",
    expect_stdout=["ALL TESTS PASSED"], must_fix="intervals.py")

exec_task(
    "d9m_impl_lru", "implement", "按测试用例实现模块",
    "lru.py 只有一个空壳。请按照 test_lru.py 实现 LRUCache(get / put / keys),"
    "给出完整的 lru.py,放在 ```python 代码块中。",
    {"lru.py": '''class LRUCache:
    def __init__(self, capacity):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def put(self, key, value):
        raise NotImplementedError

    def keys(self):
        """Keys ordered from least recently used to most recently used."""
        raise NotImplementedError
''',
     "test_lru.py": '''from lru import LRUCache

c = LRUCache(2)
assert c.get(1) is None
c.put(1, "a")
c.put(2, "b")
assert c.get(1) == "a"
c.put(3, "c")
assert c.get(2) is None
assert c.get(1) == "a"
assert c.keys() == [3, 1]
c.put(1, "z")
assert c.get(1) == "z"
assert c.keys() == [3, 1]
print("ALL TESTS PASSED")
'''},
    "lru.py", "test_lru.py",
    '''from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        if key not in self.data:
            return None
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        self.data[key] = value
        while len(self.data) > self.capacity:
            self.data.popitem(last=False)

    def keys(self):
        """Keys ordered from least recently used to most recently used."""
        return list(self.data.keys())
''',
    [{"type": "any_of", "values": ["OrderedDict", "move_to_end", "双向链表"],
      "label": "选用合适的数据结构", "weight": 1},
     {"type": "any_of", "values": ["popitem", "淘汰", "超出容量"], "label": "容量淘汰", "weight": 1}],
    "get 也要刷新访问顺序;OrderedDict 或自建双向链表",
    expect_stdout=["ALL TESTS PASSED"], must_fix="lru.py")

exec_task(
    "d9m_impl_tokenize", "implement", "按测试用例实现模块",
    "tokenize.py 还没有实现。请按照 test_tokenize.py 实现分词函数,"
    "给出完整的 tokenize.py,放在 ```python 代码块中。",
    {"tokenize.py": '''STOPWORDS = {"the", "a", "an", "of", "and"}


def tokenize(text):
    """Lowercase, keep only alphanumeric runs, drop stopwords, keep original order."""
    raise NotImplementedError
''',
     "test_tokenize.py": '''from tokenize import tokenize

assert tokenize("The quick, a BROWN fox!") == ["quick", "brown", "fox"]
assert tokenize("") == []
assert tokenize("A a an the") == []
assert tokenize("llama.cpp  vs  Llama") == ["llama", "cpp", "vs", "llama"]
print("ALL TESTS PASSED")
'''},
    "tokenize.py", "test_tokenize.py",
    '''import re

STOPWORDS = {"the", "a", "an", "of", "and"}


def tokenize(text):
    """Lowercase, keep only alphanumeric runs, drop stopwords, keep original order."""
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in STOPWORDS]
''',
    [{"type": "any_of", "values": ["lower", "小写", "re.findall"], "label": "统一小写并切词", "weight": 1},
     {"type": "any_of", "values": ["stopword", "停用词"], "label": "过滤停用词", "weight": 1}],
    "正则切词 + 小写 + 停用词过滤",
    expect_stdout=["ALL TESTS PASSED"], must_fix="tokenize.py")

exec_task(
    "d9m_impl_semver", "implement", "按测试用例实现模块",
    "semver.py 需要实现语义化版本解析与比较。请按照 test_semver.py 给出完整的 "
    "semver.py,放在 ```python 代码块中。",
    {"semver.py": '''def parse(version):
    """Parse "MAJOR.MINOR.PATCH" into a 3-tuple of ints. Raise ValueError otherwise."""
    raise NotImplementedError


def compare(a, b):
    """Return -1, 0 or 1 comparing two version strings numerically per component."""
    raise NotImplementedError
''',
     "test_semver.py": '''from semver import parse, compare

assert parse("1.2.3") == (1, 2, 3)
assert parse("0.0.0") == (0, 0, 0)
for bad in ["1.2", "1.2.3.4", "v1.2.3", "1.x.3", ""]:
    try:
        parse(bad)
        raise AssertionError("should reject %r" % bad)
    except ValueError:
        pass
assert compare("1.2.3", "1.2.3") == 0
assert compare("1.10.0", "1.9.9") == 1
assert compare("1.0.0", "2.0.0") == -1
assert compare("2.0.1", "2.1.0") == -1
print("ALL TESTS PASSED")
'''},
    "semver.py", "test_semver.py",
    '''import re

_PATTERN = re.compile(r"^(\\d+)\\.(\\d+)\\.(\\d+)$")


def parse(version):
    """Parse "MAJOR.MINOR.PATCH" into a 3-tuple of ints. Raise ValueError otherwise."""
    m = _PATTERN.match(version or "")
    if not m:
        raise ValueError("bad version: %r" % (version,))
    return tuple(int(x) for x in m.groups())


def compare(a, b):
    """Return -1, 0 or 1 comparing two version strings numerically per component."""
    pa, pb = parse(a), parse(b)
    if pa == pb:
        return 0
    return 1 if pa > pb else -1
''',
    [{"type": "any_of", "values": ["元组", "tuple", "按数值", "int("], "label": "逐段按数值比较", "weight": 1},
     {"type": "any_of", "values": ["ValueError", "非法", "格式"], "label": "非法版本抛错", "weight": 1}],
    "不能按字符串比较(1.10 会排在 1.9 前面);必须逐段转 int",
    expect_stdout=["ALL TESTS PASSED"], must_fix="semver.py")

exec_task(
    "d9m_impl_glob", "implement", "按测试用例实现模块",
    "globmod.py 要实现一个简化通配符匹配器。请按照 test_globmod.py 给出完整的 "
    "globmod.py,放在 ```python 代码块中。",
    {"globmod.py": '''def match(pattern, name):
    """Wildcard match: star matches any run of characters except the separator,
    question mark matches exactly one character except the separator.
    The whole name must match."""
    raise NotImplementedError
''',
     "test_globmod.py": '''from globmod import match

assert match("*.py", "a.py") is True
assert match("*.py", "a.txt") is False
assert match("a?.txt", "ab.txt") is True
assert match("a?.txt", "abc.txt") is False
assert match("dir/*", "dir/x") is True
assert match("dir/*", "other/x") is False
assert match("*", "a/b") is False
assert match("a*b*c", "axxbyyc") is True
assert match("", "") is True
print("ALL TESTS PASSED")
'''},
    "globmod.py", "test_globmod.py",
    '''def match(pattern, name):
    """Wildcard match: star matches any run of characters except the separator,
    question mark matches exactly one character except the separator.
    The whole name must match."""
    if not pattern:
        return not name
    head = pattern[0]
    if head == "*":
        if match(pattern[1:], name):
            return True
        i = 0
        while i < len(name) and name[i] != "/":
            if match(pattern[1:], name[i + 1:]):
                return True
            i += 1
        return False
    if head == "?":
        return bool(name) and name[0] != "/" and match(pattern[1:], name[1:])
    return bool(name) and name[0] == head and match(pattern[1:], name[1:])
''',
    [{"type": "any_of", "values": ["递归", "recursion", "回溯"], "label": "递归/回溯实现", "weight": 1},
     {"type": "any_of", "values": ["/", "不跨目录", "分隔符"], "label": "星号不跨目录", "weight": 1}],
    "星号不能跨目录分隔符匹配;递归或双指针回溯",
    expect_stdout=["ALL TESTS PASSED"], must_fix="globmod.py")

exec_task(
    "d9m_impl_parse_ranges", "implement", "按测试用例实现模块",
    "ranges.py 要把 1-3,7,9-11 这样的区间串展开成整数列表。请按照 "
    "test_ranges.py 给出完整的 ranges.py,放在 ```python 代码块中。",
    {"ranges.py": '''def parse_ranges(text):
    """Expand "1-3,7,9-11" into a sorted list of ints. Empty text -> [].
    Raise ValueError for a reversed or non-numeric range."""
    raise NotImplementedError
''',
     "test_ranges.py": '''from ranges import parse_ranges

assert parse_ranges("1-3,7") == [1, 2, 3, 7]
assert parse_ranges("5") == [5]
assert parse_ranges("") == []
assert parse_ranges("9-11,1-2") == [1, 2, 9, 10, 11]
assert parse_ranges(" 4 , 6 ") == [4, 6]
for bad in ["3-1", "a-b", "1-"]:
    try:
        parse_ranges(bad)
        raise AssertionError("should reject %r" % bad)
    except ValueError:
        pass
print("ALL TESTS PASSED")
'''},
    "ranges.py", "test_ranges.py",
    '''def parse_ranges(text):
    """Expand "1-3,7,9-11" into a sorted list of ints. Empty text -> [].
    Raise ValueError for a reversed or non-numeric range."""
    if not (text or "").strip():
        return []
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bits = part.split("-")
            if len(bits) != 2:
                raise ValueError("bad range: %r" % part)
            lo, hi = int(bits[0]), int(bits[1])
            if lo > hi:
                raise ValueError("reversed range: %r" % part)
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    return sorted(out)
''',
    [{"type": "any_of", "values": ["split", "逗号", "range("], "label": "拆段展开", "weight": 1},
     {"type": "any_of", "values": ["ValueError", "逆序", "非法"], "label": "非法区间报错", "weight": 1}],
    "int() 会替你挡住非数字;逆序区间要显式检查",
    expect_stdout=["ALL TESTS PASSED"], must_fix="ranges.py")

exec_task(
    "d9m_impl_pick", "implement", "按测试用例实现模块",
    "jsonpath.py 要支持 a.b[0].c 这样的取值路径,取不到返回 None。请按照 "
    "test_pick.py 给出完整的 jsonpath.py,放在 ```python 代码块中。",
    {"jsonpath.py": '''def pick(obj, path):
    """Walk a dotted path with optional [index] segments. Return None when any
    segment is missing or has the wrong container type."""
    raise NotImplementedError
''',
     "test_pick.py": '''from jsonpath import pick

data = {"a": {"b": [{"c": 1}, {"c": 2}]}, "n": 5, "lst": [10, 20]}
assert pick(data, "a.b[0].c") == 1
assert pick(data, "a.b[1].c") == 2
assert pick(data, "n") == 5
assert pick(data, "lst[1]") == 20
assert pick(data, "a.b[5].c") is None
assert pick(data, "a.missing") is None
assert pick({}, "x") is None
assert pick(data, "n.deeper") is None
print("ALL TESTS PASSED")
'''},
    "jsonpath.py", "test_pick.py",
    '''import re

_SEG = re.compile(r"([^.\\[\\]]+)|\\[(\\d+)\\]")


def pick(obj, path):
    """Walk a dotted path with optional [index] segments. Return None when any
    segment is missing or has the wrong container type."""
    if not path:
        return None
    cur = obj
    for m in _SEG.finditer(path):
        key, idx = m.group(1), m.group(2)
        if idx is not None:
            if not isinstance(cur, list):
                return None
            i = int(idx)
            if i >= len(cur):
                return None
            cur = cur[i]
        else:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
    return cur
''',
    [{"type": "any_of", "values": ["isinstance", "类型", "list", "dict"], "label": "按容器类型逐段下降", "weight": 1},
     {"type": "any_of", "values": ["None", "缺省", "取不到"], "label": "取不到返回 None", "weight": 1}],
    "路径既有点号也有下标;每段都要判容器类型",
    expect_stdout=["ALL TESTS PASSED"], must_fix="jsonpath.py")

exec_task(
    "d9m_impl_sliding", "implement", "按测试用例实现模块",
    "sliding.py 要实现滑动窗口限流器。请按照 test_sliding.py 给出完整的 "
    "sliding.py,放在 ```python 代码块中。",
    {"sliding.py": '''class SlidingWindow:
    def __init__(self, limit, window):
        raise NotImplementedError

    def allow(self, now):
        """Return True when fewer than limit calls happened inside the window
        ending at now."""
        raise NotImplementedError
''',
     "test_sliding.py": '''from sliding import SlidingWindow

w = SlidingWindow(limit=2, window=10)
assert w.allow(0) is True
assert w.allow(1) is True
assert w.allow(2) is False
assert w.allow(11) is True
assert w.allow(12) is True
assert w.allow(13) is False

w2 = SlidingWindow(limit=1, window=5)
assert w2.allow(100) is True
assert w2.allow(105) is True
print("ALL TESTS PASSED")
'''},
    "sliding.py", "test_sliding.py",
    '''class SlidingWindow:
    def __init__(self, limit, window):
        if limit < 1 or window < 1:
            raise ValueError("limit and window must be >= 1")
        self.limit = limit
        self.window = window
        self.hits = []

    def allow(self, now):
        """Return True when fewer than limit calls happened inside the window
        ending at now."""
        edge = now - self.window
        self.hits = [t for t in self.hits if t > edge]
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True
''',
    [{"type": "any_of", "values": ["now - window", "窗口", "裁剪"], "label": "先清理过期记录", "weight": 1},
     {"type": "any_of", "values": ["> edge", ">=", "边界"], "label": "处理边界开闭", "weight": 1}],
    "窗口左开右闭;先裁剪再判断,且被拒绝的请求不记账",
    expect_stdout=["ALL TESTS PASSED"], must_fix="sliding.py")

exec_task(
    "d9m_impl_schedule", "implement", "按测试用例实现模块",
    "schedule.py 要求最多的不重叠会议数。请按照 test_schedule.py 给出完整的 "
    "schedule.py,放在 ```python 代码块中。",
    {"schedule.py": '''def max_meetings(intervals):
    """intervals: list of (start, end). Return the max number of non-overlapping
    meetings (a meeting ending at t does not conflict with one starting at t)."""
    raise NotImplementedError
''',
     "test_schedule.py": '''from schedule import max_meetings

assert max_meetings([]) == 0
assert max_meetings([(1, 3), (2, 4), (3, 5)]) == 2
assert max_meetings([(1, 2), (2, 3), (3, 4)]) == 3
assert max_meetings([(1, 10)]) == 1
assert max_meetings([(4, 5), (1, 2), (2, 3)]) == 3
print("ALL TESTS PASSED")
'''},
    "schedule.py", "test_schedule.py",
    '''def max_meetings(intervals):
    """intervals: list of (start, end). Return the max number of non-overlapping
    meetings (a meeting ending at t does not conflict with one starting at t)."""
    count, last_end = 0, None
    for s, e in sorted(intervals, key=lambda p: p[1]):
        if last_end is None or s >= last_end:
            count += 1
            last_end = e
    return count
''',
    [{"type": "any_of", "values": ["按结束时间", "key=lambda", "p[1]", "贪心"], "label": "按结束时间贪心", "weight": 1},
     {"type": "any_of", "values": ["s >= last_end", "不相交", "不重叠"], "label": "相接不算冲突", "weight": 1}],
    "活动选择问题:按结束时间排序后贪心",
    expect_stdout=["ALL TESTS PASSED"], must_fix="schedule.py")

exec_task(
    "d9m_impl_diff", "implement", "按测试用例实现模块",
    "diffmod.py 要统计两份文本的行级差异。请按照 test_diffmod.py 给出完整的 "
    "diffmod.py,放在 ```python 代码块中。",
    {"diffmod.py": '''def diff_summary(old, new):
    """Return (added, removed) line counts between two texts, based on the longest
    common subsequence of lines. Trailing empty lines are ignored."""
    raise NotImplementedError
''',
     "test_diffmod.py": '''from diffmod import diff_summary

assert diff_summary("a\\nb\\nc", "a\\nc") == (0, 1)
assert diff_summary("", "x") == (1, 0)
assert diff_summary("a", "a") == (0, 0)
assert diff_summary("a\\nb", "b\\na") == (1, 1)
assert diff_summary("a\\nb\\nc", "a\\nx\\nc") == (1, 1)
assert diff_summary("a\\nb", "a\\nb") == (0, 0)
print("ALL TESTS PASSED")
'''},
    "diffmod.py", "test_diffmod.py",
    '''def _lines(text):
    parts = (text or "").split("\\n")
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def diff_summary(old, new):
    """Return (added, removed) line counts between two texts, based on the longest
    common subsequence of lines. Trailing empty lines are ignored."""
    a, b = _lines(old), _lines(new)
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    lcs = dp[0][0]
    return (m - lcs, n - lcs)
''',
    [{"type": "any_of", "values": ["LCS", "最长公共子序列", "动态规划", "dp"],
      "label": "基于 LCS 计数", "weight": 1},
     {"type": "any_of", "values": ["added", "removed", "增删"], "label": "分别给出增删行数", "weight": 1}],
    "增删行数由 LCS 反推:新增行数 = 新文本行数 - LCS",
    expect_stdout=["ALL TESTS PASSED"], must_fix="diffmod.py")

exec_task(
    "d9m_impl_schema", "implement", "按测试用例实现模块",
    "schema.py 要实现一个极简的字段类型校验器。请按照 test_schema.py 给出完整的 "
    "schema.py,放在 ```python 代码块中。",
    {"schema.py": '''TYPES = {"str": str, "int": int, "list": list, "dict": dict, "float": float}


def validate(obj, schema):
    """Return a list of error strings. Missing required field -> "missing:<name>",
    wrong type -> "type:<name>". A schema value of None means any type is accepted."""
    raise NotImplementedError
''',
     "test_schema.py": '''from schema import validate

S = {"name": "str", "age": "int", "tags": "list"}
assert validate({"name": "x", "age": 1, "tags": []}, S) == []
assert validate({"name": "x", "age": 1}, S) == ["missing:tags"]
assert validate({"name": 1, "age": 1, "tags": []}, S) == ["type:name"]
assert validate({"name": "x", "age": 1, "tags": [], "extra": 9}, S) == []
assert validate({}, {"n": None}) == ["missing:n"]
assert validate({"n": "anything"}, {"n": None}) == []
bad = validate({"age": "old"}, S)
assert "missing:name" in bad and "type:age" in bad and "missing:tags" in bad, bad
print("ALL TESTS PASSED")
'''},
    "schema.py", "test_schema.py",
    '''TYPES = {"str": str, "int": int, "list": list, "dict": dict, "float": float}


def validate(obj, schema):
    """Return a list of error strings. Missing required field -> "missing:<name>",
    wrong type -> "type:<name>". A schema value of None means any type is accepted."""
    errors = []
    for name, want in schema.items():
        if name not in obj:
            errors.append("missing:" + name)
            continue
        if want is None:
            continue
        expect = TYPES.get(want)
        if expect is None:
            errors.append("type:" + name)
            continue
        if isinstance(obj[name], bool) and expect is int:
            errors.append("type:" + name)
            continue
        if not isinstance(obj[name], expect):
            errors.append("type:" + name)
    return errors
''',
    [{"type": "any_of", "values": ["isinstance", "类型"], "label": "用 isinstance 判型", "weight": 1},
     {"type": "any_of", "values": ["missing", "type:", "错误列表"], "label": "按约定返回错误串", "weight": 1}],
    "注意 bool 是 int 的子类;返回值是错误串列表而不是布尔",
    expect_stdout=["ALL TESTS PASSED"], must_fix="schema.py")

exec_task(
    "d9m_impl_backoff", "implement", "按测试用例实现模块",
    "backoff.py 要生成指数退避的等待时长序列。请按照 test_backoff.py 给出完整的 "
    "backoff.py,放在 ```python 代码块中。",
    {"backoff.py": '''def backoff_delays(base, factor, cap, n):
    """Return n delays: base, base*factor, base*factor^2, ... each capped at cap.
    n < 1 -> empty list. base < 1 or factor < 1 or cap < base -> ValueError."""
    raise NotImplementedError
''',
     "test_backoff.py": '''from backoff import backoff_delays

assert backoff_delays(1, 2, 10, 4) == [1, 2, 4, 8]
assert backoff_delays(1, 3, 10, 4) == [1, 3, 9, 10]
assert backoff_delays(2, 2, 100, 1) == [2]
assert backoff_delays(1, 2, 10, 0) == []
assert backoff_delays(5, 2, 100, 3) == [5, 10, 20]
for args in [(0, 2, 10, 3), (1, 0, 10, 3), (5, 2, 4, 3)]:
    try:
        backoff_delays(*args)
        raise AssertionError("should reject %r" % (args,))
    except ValueError:
        pass
print("ALL TESTS PASSED")
'''},
    "backoff.py", "test_backoff.py",
    '''def backoff_delays(base, factor, cap, n):
    """Return n delays: base, base*factor, base*factor^2, ... each capped at cap.
    n < 1 -> empty list. base < 1 or factor < 1 or cap < base -> ValueError."""
    if base < 1 or factor < 1 or cap < base:
        raise ValueError("invalid backoff parameters")
    out, cur = [], base
    for _ in range(max(0, n)):
        out.append(min(cur, cap))
        cur = cur * factor
    return out
''',
    [{"type": "any_of", "values": ["min(", "cap", "封顶"], "label": "逐项封顶", "weight": 1},
     {"type": "any_of", "values": ["ValueError", "参数校验", "非法"], "label": "参数校验", "weight": 1}],
    "每项先取 min(cur, cap);注意 cap 小于 base 时直接拒绝",
    expect_stdout=["ALL TESTS PASSED"], must_fix="backoff.py")

# ===========================================================================
# BATCH 3 - config: a broken config file + a validator that must exit 0 (9 tasks)
# These are the "配置文件要能通过校验" tasks: the shipped file is invalid, the
# validator is the spec, and the answer is a corrected config.
# ===========================================================================

exec_task(
    "d9m_cfg_ini", "config-fix", "配置文件修复",
    "app.ini 现在被 check_ini.py 判为不合法。请读懂校验脚本的要求,"
    "给出修正后的完整 app.ini(原样输出文件内容,不要改字段名)。",
    {"app.ini": '''[server]
host = 0.0.0.0
port = 70000
workers=4
timeout = 30

[log]
level = verbose
path = ./logs/app.log
''',
     "check_ini.py": '''import configparser

cfg = configparser.ConfigParser()
cfg.read("app.ini", encoding="utf-8")

assert cfg.has_section("server"), "missing [server]"
assert cfg.has_section("log"), "missing [log]"
port = cfg.getint("server", "port")
assert 1 <= port <= 65535, "port out of range: %s" % port
workers = cfg.getint("server", "workers")
assert 1 <= workers <= 64, "workers out of range: %s" % workers
assert cfg.get("server", "host") != "", "host empty"
level = cfg.get("log", "level").lower()
assert level in ("debug", "info", "warn", "error"), "bad level: %s" % level
assert cfg.get("log", "path").startswith("./"), "log path must be relative"
print("CONFIG OK")
'''},
    "app.ini", "check_ini.py",
    '''[server]
host = 0.0.0.0
port = 8080
workers = 4
timeout = 30

[log]
level = info
path = ./logs/app.log
''',
    [{"type": "any_of", "values": ["port", "端口", "70000"], "label": "指出端口越界", "weight": 1},
     {"type": "any_of", "values": ["level", "日志级别", "verbose"], "label": "指出非法日志级别", "weight": 1}],
    "端口 70000 越界;level=verbose 不在允许集合;workers 缺空格虽是合法 INI 但需保持可读",
    expect_stdout=["CONFIG OK"], must_fix="app.ini")

exec_task(
    "d9m_cfg_toml", "config-fix", "配置文件修复",
    "service.toml 解析不过。请读懂 check_toml.py 的要求,给出修正后的完整 "
    "service.toml。",
    {"service.toml": '''[service]
name = "ingest"
replicas = 0
image = "registry.local/ingest:1.4"
env = "dev"
timeout_ms = "30000"
''',
     "check_toml.py": '''import tomllib

with open("service.toml", "rb") as f:
    cfg = tomllib.load(f)

svc = cfg["service"]
assert isinstance(svc["name"], str) and svc["name"].strip(), "name must be a non-empty string"
assert isinstance(svc["replicas"], int) and 1 <= svc["replicas"] <= 10, "replicas out of range"
assert isinstance(svc["timeout_ms"], int) and svc["timeout_ms"] > 0, "timeout_ms must be a positive int"
assert svc["env"] in ("dev", "staging", "prod"), "bad env"
assert ":" in svc["image"], "image must carry a tag"
print("CONFIG OK")
'''},
    "service.toml", "check_toml.py",
    '''[service]
name = "ingest"
replicas = 2
image = "registry.local/ingest:1.4"
env = "dev"
timeout_ms = 30000
''',
    [{"type": "any_of", "values": ["replicas", "副本"], "label": "指出 replicas 越界", "weight": 1},
     {"type": "any_of", "values": ["timeout_ms", "字符串", "int", "类型"],
      "label": "指出类型错误", "weight": 1}],
    "replicas=0 越界;timeout_ms 被写成了字符串,TOML 里类型必须一致",
    expect_stdout=["CONFIG OK"], must_fix="service.toml")

exec_task(
    "d9m_cfg_json", "config-fix", "配置文件修复",
    "policy.json 通不过 check_json.py。请读懂校验脚本,给出修正后的完整 policy.json。",
    {"policy.json": '''{
  "version": 1,
  "allow": ["read", "write", "delete"],
  "deny": [],
  "limits": {"rps": 0, "burst": -1},
  "owner": ""
}
''',
     "check_json.py": '''import json

with open("policy.json", encoding="utf-8") as f:
    p = json.load(f)

assert p["version"] == 2, "version must be 2"
allow = set(p["allow"])
deny = set(p["deny"])
assert allow and deny, "allow and deny must both be non-empty"
assert not (allow & deny), "a verb cannot be both allowed and denied: %s" % (allow & deny)
assert "delete" in deny, "delete must be denied"
assert 1 <= p["limits"]["rps"] <= 1000, "rps out of range"
assert 1 <= p["limits"]["burst"] <= p["limits"]["rps"], "burst must be within rps"
assert isinstance(p["owner"], str) and p["owner"].strip(), "owner must be set"
print("CONFIG OK")
'''},
    "policy.json", "check_json.py",
    '''{
  "version": 2,
  "allow": ["read", "write"],
  "deny": ["delete"],
  "limits": {"rps": 50, "burst": 10},
  "owner": "platform-team"
}
''',
    [{"type": "any_of", "values": ["version", "版本"], "label": "指出版本不符", "weight": 1},
     {"type": "any_of", "values": ["deny", "delete", "rps", "burst"], "label": "指出权限与限流问题", "weight": 1}],
    "version 应为 2;delete 必须进 deny 且不能同时在 allow;rps=0/burst=-1 越界;owner 不能为空",
    expect_stdout=["CONFIG OK"], must_fix="policy.json")

exec_task(
    "d9m_cfg_yaml", "config-fix", "配置文件修复",
    "deploy.yaml 被 check_yaml.py 判为不合法。环境里只有标准库,"
    "所以校验脚本用极简的逐行解析。请给出修正后的完整 deploy.yaml。",
    {"deploy.yaml": '''app: demo
replicas: many
image: demo
ports:
  - 8080
resources:
  cpu: 500m
  memory: 128Mi
''',
     "check_yaml.py": '''import re

text = open("deploy.yaml", encoding="utf-8").read()
lines = [ln.rstrip() for ln in text.splitlines()]

# top-level keys must be "key: value" or "key:" followed by an indented block
top = {}
for ln in lines:
    if not ln.strip() or ln.lstrip().startswith("#"):
        continue
    if not ln.startswith(" "):
        m = re.match(r"^([a-z_]+):(?: (.*))?$", ln)
        assert m, "bad top-level line: %r" % ln
        top[m.group(1)] = (m.group(2) or "").strip()

assert set(top) == {"app", "replicas", "image", "ports", "resources"}, sorted(top)
assert top["app"], "app must not be empty"
assert top["image"].endswith(":1.0"), "image must be tagged :1.0"
assert top["replicas"].isdigit() and 1 <= int(top["replicas"]) <= 5, "replicas must be 1..5"
assert top["ports"] == "", "ports must be a list block"
assert top["resources"] == "", "resources must be a mapping block"

body = "\\n".join(lines)
assert re.search(r"^  - 8080$", body, re.M), "ports must list 8080"
assert re.search(r"^  cpu: ", body, re.M), "cpu missing"
assert re.search(r"^  memory: ", body, re.M), "memory missing"
print("CONFIG OK")
'''},
    "deploy.yaml", "check_yaml.py",
    '''app: demo
replicas: 2
image: demo:1.0
ports:
  - 8080
resources:
  cpu: 500m
  memory: 128Mi
''',
    [{"type": "any_of", "values": ["replicas", "many", "整数"], "label": "指出 replicas 非整数", "weight": 1},
     {"type": "any_of", "values": ["image", "tag", "标签"], "label": "指出镜像缺标签", "weight": 1}],
    "replicas 写成 many 不是数字;镜像没有 :1.0 标签",
    expect_stdout=["CONFIG OK"], must_fix="deploy.yaml")

exec_task(
    "d9m_cfg_env", "config-fix", "配置文件修复",
    ".env 文件被 check_env.py 判为不合法。请给出修正后的完整 .env。",
    {"envfile": '''APP_HOST=localhost
APP_PORT=abc
APP_DEBUG=maybe
APP_SECRET=
APP_LOG_LEVEL=INFO
''',
     "check_env.py": '''import re

vals = {}
for ln in open("envfile", encoding="utf-8").read().splitlines():
    if not ln.strip() or ln.lstrip().startswith("#"):
        continue
    assert "=" in ln, "line without =: %r" % ln
    k, v = ln.split("=", 1)
    assert re.fullmatch(r"[A-Z][A-Z0-9_]*", k), "bad key name: %r" % k
    assert k not in vals, "duplicate key: %s" % k
    vals[k] = v

assert vals.get("APP_HOST"), "APP_HOST required"
assert vals.get("APP_PORT", "").isdigit() and 1 <= int(vals["APP_PORT"]) <= 65535, "bad APP_PORT"
assert vals.get("APP_DEBUG") in ("0", "1"), "APP_DEBUG must be 0 or 1"
assert len(vals.get("APP_SECRET", "")) >= 16, "APP_SECRET must be >= 16 chars"
assert vals.get("APP_LOG_LEVEL") in ("debug", "info", "warn", "error"), "bad APP_LOG_LEVEL"
print("CONFIG OK")
'''},
    "envfile", "check_env.py",
    '''APP_HOST=localhost
APP_PORT=8080
APP_DEBUG=0
APP_SECRET=change-me-please-32chars
APP_LOG_LEVEL=info
''',
    [{"type": "any_of", "values": ["APP_PORT", "端口", "abc"], "label": "指出端口非数字", "weight": 1},
     {"type": "any_of", "values": ["APP_DEBUG", "APP_SECRET", "APP_LOG_LEVEL", "空", "大写"],
      "label": "指出其余不合规项", "weight": 1}],
    "端口不是数字;DEBUG 只能是 0/1;SECRET 为空;日志级别须小写",
    expect_stdout=["CONFIG OK"], must_fix="envfile")

exec_task(
    "d9m_cfg_sql", "config-fix", "配置文件修复",
    "schema.sql 建表时被 check_sql.py 拒绝。请给出修正后的完整 schema.sql。",
    {"schema.sql": '''CREATE TABLE users (
    id INTEGER,
    name TEXT,
    email TEXT,
    age INTEGER
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    amount REAL
);
''',
     "check_sql.py": '''import sqlite3

conn = sqlite3.connect(":memory:")
conn.executescript(open("schema.sql", encoding="utf-8").read())

def cols(t):
    return {r[1]: r for r in conn.execute("PRAGMA table_info(%s)" % t)}

u = cols("users")
assert "id" in u and u["id"][5] == 1, "users.id must be PRIMARY KEY"
assert u["name"][3] == 1, "users.name must be NOT NULL"
assert u["email"][3] == 1, "users.email must be NOT NULL"
assert u["age"][3] == 0, "users.age must be nullable"
assert u["age"][2].upper() == "INTEGER", "users.age must be INTEGER"

idx = {r[1] for r in conn.execute("PRAGMA index_list(users)")}
assert any("email" in str(r) for r in conn.execute("PRAGMA index_info(sqlite_autoindex_users_1)")), "users.email needs UNIQUE"

o = cols("orders")
assert o["user_id"][3] == 1, "orders.user_id must be NOT NULL"

fk = list(conn.execute("PRAGMA foreign_key_list(orders)"))
assert fk and fk[0][2] == "users", "orders.user_id must reference users"
print("SCHEMA OK")
'''},
    "schema.sql", "check_sql.py",
    '''CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    age INTEGER
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    amount REAL
);
''',
    [{"type": "any_of", "values": ["PRIMARY KEY", "主键"], "label": "补主键", "weight": 1},
     {"type": "any_of", "values": ["NOT NULL", "非空", "UNIQUE", "外键", "REFERENCES"],
      "label": "补约束", "weight": 1}],
    "缺主键、非空、唯一与外键约束;sqlite 里 UNIQUE 会建自动索引",
    expect_stdout=["SCHEMA OK"], must_fix="schema.sql")

exec_task(
    "d9m_cfg_csv", "config-fix", "配置文件修复",
    "records.csv 被 check_csv.py 判为不合法。请给出修正后的完整 records.csv。",
    {"records.csv": '''id,name,score,active
1,alice,88,true
2,,95,true
3,bob,abc,false
4,carol,120,true
1,dave,70,false
''',
     "check_csv.py": '''import csv

rows = list(csv.DictReader(open("records.csv", encoding="utf-8", newline="")))
assert rows, "no rows"
assert list(rows[0].keys()) == ["id", "name", "score", "active"], list(rows[0].keys())

ids = [r["id"] for r in rows]
assert len(ids) == len(set(ids)), "duplicate id: %s" % ids
for r in rows:
    assert r["name"].strip(), "empty name in row %s" % r["id"]
    assert r["score"].isdigit() and 0 <= int(r["score"]) <= 100, "bad score: %r" % r["score"]
    assert r["active"] in ("true", "false"), "bad active: %r" % r["active"]
assert sum(1 for r in rows if r["active"] == "true") >= 2, "need at least two active rows"
print("CSV OK")
'''},
    "records.csv", "check_csv.py",
    '''id,name,score,active
1,alice,88,true
2,erin,95,true
3,bob,64,false
4,carol,72,true
5,dave,70,false
''',
    [{"type": "any_of", "values": ["重复", "duplicate", "id"], "label": "指出重复主键", "weight": 1},
     {"type": "any_of", "values": ["score", "120", "abc", "分数"], "label": "指出分数非法", "weight": 1}],
    "id 1 重复;name 有空值;score 出现 abc 与 120;active 需保持 true/false 小写",
    expect_stdout=["CSV OK"], must_fix="records.csv")

exec_task(
    "d9m_cfg_nginx", "config-fix", "配置文件修复",
    "site.conf 被 check_nginx.py 判为不合法(校验脚本按 nginx 语法做括号与指令检查)。"
    "请给出修正后的完整 site.conf。",
    {"site.conf": '''server {
    listen 80
    server_name example.local;

    location / {
        root /var/www/html;
        index index.html
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8080;
    }
''',
     "check_nginx.py": '''import re

text = open("site.conf", encoding="utf-8").read()
depth = 0
for ln in text.splitlines():
    s = ln.split("#", 1)[0]
    depth += s.count("{") - s.count("}")
    assert depth >= 0, "unbalanced closing brace"
assert depth == 0, "unbalanced braces: depth=%d" % depth

# every non-block directive line must end with ';'
for ln in text.splitlines():
    s = ln.split("#", 1)[0].strip()
    if not s or s in ("}", "{") or s.endswith("{"):
        continue
    assert s.endswith(";"), "missing semicolon: %r" % s

assert re.search(r"^\\s*listen\\s+\\d+;", text, re.M), "listen directive missing"
assert re.search(r"^\\s*server_name\\s+\\S+;", text, re.M), "server_name missing"
assert text.count("location /api/") == 1, "api location missing"
assert "proxy_pass http://127.0.0.1:8080;" in text, "proxy_pass must be set"
assert re.search(r"^\\s*root\\s+\\S+;", text, re.M), "root missing"
assert re.search(r"^\\s*index\\s+\\S+;", text, re.M), "index missing"
print("CONFIG OK")
'''},
    "site.conf", "check_nginx.py",
    '''server {
    listen 80;
    server_name example.local;

    location / {
        root /var/www/html;
        index index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8080;
    }
}
''',
    [{"type": "any_of", "values": ["分号", ";", "semicolon"], "label": "指出缺分号", "weight": 1},
     {"type": "any_of", "values": ["括号", "}", "{", "闭合"], "label": "指出括号不配对", "weight": 1}],
    "三处指令缺分号,server 块的右括号缺失",
    expect_stdout=["CONFIG OK"], must_fix="site.conf")

exec_task(
    "d9m_cfg_shell", "config-fix", "配置文件修复",
    "run.sh 是一份部署脚本,但语法检查失败且行为不对。请给出修正后的完整 run.sh。"
    "要求:目录不存在时自动创建;必须以 0 退出并打印 READY。",
    {"run.sh": '''#!/bin/sh
set -e
TARGET="$1"
if [ -z "$TARGET" ]
then
  echo "usage: run.sh <target>" >&2
  exit 2
fi
mkdir -p "$TARGET"
cd "$TARGET
echo "READY"
''',
     "check_sh.py": '''import os, shutil, subprocess

here = os.path.dirname(os.path.abspath(__file__))
script = os.path.join(here, "run.sh")
work = os.path.join(here, "_sh_work")
text = open(script, encoding="utf-8").read()


def structural_check(text):
    """Static rules that hold even when no POSIX shell is installed."""
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.lstrip().startswith("#!"):
            continue
        code = ln.split("#", 1)[0]
        assert code.count(chr(34)) % 2 == 0, "unbalanced double quote on line %d: %r" % (i, ln)
    assert "mkdir -p" in text, "the script must create the target directory"
    assert "READY" in text, "the script must print READY"
    assert "$1" in text, "the script must read its first argument"


shell = shutil.which("sh") or shutil.which("bash")
if not shell:
    structural_check(text)
    print("SCRIPT OK (structural check: no POSIX shell on PATH)")
    raise SystemExit(0)

# no target -> must exit non-zero with a usage message
p = subprocess.run([shell, script], capture_output=True, text=True)
assert p.returncode != 0, "missing argument must exit non-zero"

# with a target -> must create it and print READY, exit 0
shutil.rmtree(work, ignore_errors=True)
target = os.path.join(work, "out", "dir")
p = subprocess.run([shell, script, target], capture_output=True, text=True)
assert p.returncode == 0, "exit %s: %s" % (p.returncode, p.stderr.strip()[-200:])
assert "READY" in p.stdout, "stdout must contain READY, got %r" % p.stdout
assert os.path.isdir(target), "target directory was not created"
shutil.rmtree(work, ignore_errors=True)
print("SCRIPT OK")'''},
    "run.sh", "check_sh.py",
    '''#!/bin/sh
set -e
TARGET="$1"
if [ -z "$TARGET" ]
then
  echo "usage: run.sh <target>" >&2
  exit 2
fi
mkdir -p "$TARGET"
cd "$TARGET"
echo "READY"
''',
    [{"type": "any_of", "values": ["引号", "双引号", "未闭合"], "label": "指出引号未闭合", "weight": 1},
     {"type": "any_of", "values": ["mkdir -p", "创建目录"], "label": "创建目录", "weight": 1}],
    "cd 那一行的双引号少了一个,整段脚本语法错误;目录需要 mkdir -p 创建",
    expect_stdout=["SCRIPT OK"], must_fix="run.sh")

# ===========================================================================
# BATCH 4 - pipeline: multi-file data/log pipelines that must produce output (8 tasks)
# These need several modules to work together; the runner prints fixed markers.
# ===========================================================================

exec_task(
    "d9m_pipe_logparse", "pipeline", "日志分析管线",
    "pipeline/ 里的日志统计脚本跑不出结果:parse.py 的入口函数没写完,"
    "stats.py 的分组也漏了字段。请给出修正后的完整 stats.py(可同时修改 parse.py,"
    "两个文件都要按 ```python 代码块分别给出,并标注文件名)。"
    "运行方式见 run.py,通过后必须打印 SUMMARY 行。",
    {"parse.py": '''LEVELS = ("INFO", "WARN", "ERROR")


def parse_line(line):
    """Return a dict with keys: ts, level, code. Return None when the line is not a
    valid record. A record looks like: 2026-03-01T10:00:01 INFO code=E4021"""
    parts = line.split()
    if len(parts) < 3:
        return None
    return {"ts": parts[0], "level": parts[1], "code": parts[2]}
''',
     "stats.py": '''from parse import parse_line


def summarize(lines):
    """Return {level: count} for every valid record."""
    counts = {}
    for ln in lines:
        rec = parse_line(ln)
        if not rec:
            continue
        counts[rec["level"]] = counts.get(rec["level"], 0) + 1
    return counts
''',
     "run.py": '''from stats import summarize

LINES = [
    "2026-03-01T10:00:01 INFO code=E4021",
    "2026-03-01T10:00:02 WARN code=E1000",
    "2026-03-01T10:00:03 ERROR code=E5012",
    "2026-03-01T10:00:04 INFO code=E4021",
    "garbage line",
    "2026-03-01T10:00:05 BOGUS code=E1",
    "2026-03-01T10:00:06 ERROR code=E5012",
]

got = summarize(LINES)
want = {"INFO": 2, "WARN": 1, "ERROR": 2}
assert got == want, "got %r want %r" % (got, want)
print("SUMMARY %s" % sorted(got.items()))
'''},
    "stats.py", "run.py",
    '''from parse import LEVELS, parse_line


def summarize(lines):
    """Return {level: count} for every valid record."""
    counts = {}
    for ln in lines:
        rec = parse_line(ln)
        if not rec or rec["level"] not in LEVELS:
            continue
        counts[rec["level"]] = counts.get(rec["level"], 0) + 1
    return counts
''',
    [{"type": "any_of", "values": ["level", "级别"], "label": "按级别分组", "weight": 1},
     {"type": "any_of", "values": ["None", "无效", "跳过"], "label": "跳过非法行", "weight": 1}],
    "parse_line 要校验级别与字段格式,summarize 按 level 计数",
    expect_stdout=["SUMMARY"], must_fix="stats.py")

exec_task(
    "d9m_pipe_csvreport", "pipeline", "数据汇总管线",
    "report.py 要把 CSV 汇总成按渠道的报表,现在总数和去重数都不对。"
    "请给出修正后的完整 report.py,运行 report_check.py 必须通过并打印 TOTAL 行。",
    {"report.py": '''import csv


def build(rows):
    """Return {channel: {"orders": n, "revenue": sum, "users": distinct user count}}."""
    out = {}
    for r in rows:
        ch = r["channel"]
        cur = out.setdefault(ch, {"orders": 0, "revenue": 0.0, "users": 0})
        cur["orders"] += 1
        cur["revenue"] += float(r["amount"])
        cur["users"] += 1
    return out
''',
     "report_check.py": '''import csv, io
from report import build

CSV = """order_id,channel,amount,user
1,web,10.5,u1
2,web,20.0,u2
3,web,5.0,u1
4,store,7.5,u3
5,store,2.5,u3
"""

rows = list(csv.DictReader(io.StringIO(CSV)))
got = build(rows)
assert got["web"]["orders"] == 3, got["web"]
assert abs(got["web"]["revenue"] - 35.5) < 1e-6, got["web"]
assert got["web"]["users"] == 2, got["web"]
assert got["store"]["orders"] == 2, got["store"]
assert got["store"]["users"] == 1, got["store"]
total = sum(v["orders"] for v in got.values())
print("TOTAL %d" % total)
'''},
    "report.py", "report_check.py",
    '''import csv


def build(rows):
    """Return {channel: {"orders": n, "revenue": sum, "users": distinct user count}}."""
    out = {}
    users = {}
    for r in rows:
        ch = r["channel"]
        cur = out.setdefault(ch, {"orders": 0, "revenue": 0.0, "users": 0})
        cur["orders"] += 1
        cur["revenue"] += float(r["amount"])
        users.setdefault(ch, set()).add(r["user"])
    for ch, seen in users.items():
        out[ch]["users"] = len(seen)
    return out
''',
    [{"type": "any_of", "values": ["set(", "去重", "distinct"], "label": "用户数要去重", "weight": 1},
     {"type": "any_of", "values": ["float(", "revenue", "金额"], "label": "金额转数值累加", "weight": 1}],
    "users 要按 set 去重;amount 必须转 float 再累加",
    expect_stdout=["TOTAL 5"], must_fix="report.py")

exec_task(
    "d9m_pipe_etl", "pipeline", "ETL 清洗管线",
    "etl.py 的清洗函数把好数据也过滤掉了,而且日期格式没统一。"
    "请给出修正后的完整 etl.py,运行 etl_check.py 必须通过并打印 CLEAN 行。",
    {"etl.py": '''def clean(rows):
    """Keep rows with a positive int qty and a parseable date, normalising the date
    to YYYY-MM-DD. Drop duplicates by (sku, date) keeping the first."""
    out = []
    for r in rows:
        if int(r["qty"]) > 0:
            out.append({"sku": r["sku"], "date": r["date"], "qty": int(r["qty"])})
    return out
''',
     "etl_check.py": '''from etl import clean

ROWS = [
    {"sku": "A", "date": "2026/03/01", "qty": "3"},
    {"sku": "A", "date": "2026-03-01", "qty": "3"},
    {"sku": "B", "date": "2026-13-01", "qty": "1"},
    {"sku": "C", "date": "2026-03-02", "qty": "0"},
    {"sku": "D", "date": "2026-03-03", "qty": "-2"},
    {"sku": "E", "date": "2026-03-04", "qty": "5"},
]

got = clean(ROWS)
assert [r["sku"] for r in got] == ["A", "E"], got
assert got[0]["date"] == "2026-03-01", got[0]
assert got[0]["qty"] == 3 and isinstance(got[0]["qty"], int)
print("CLEAN %d" % len(got))
'''},
    "etl.py", "etl_check.py",
    '''import datetime


def _norm_date(s):
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def clean(rows):
    """Keep rows with a positive int qty and a parseable date, normalising the date
    to YYYY-MM-DD. Drop duplicates by (sku, date) keeping the first."""
    out, seen = [], set()
    for r in rows:
        try:
            qty = int(r["qty"])
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        d = _norm_date(str(r["date"]))
        if d is None:
            continue
        key = (r["sku"], d)
        if key in seen:
            continue
        seen.add(key)
        out.append({"sku": r["sku"], "date": d, "qty": qty})
    return out
''',
    [{"type": "any_of", "values": ["strptime", "日期", "解析"], "label": "解析并统一日期格式", "weight": 1},
     {"type": "any_of", "values": ["seen", "去重", "重复"], "label": "按业务键去重", "weight": 1}],
    "日期要兼容两种格式并统一输出;非法日期要丢弃;按 (sku, date) 去重",
    expect_stdout=["CLEAN 2"], must_fix="etl.py")

exec_task(
    "d9m_pipe_ratelimit", "pipeline", "多模块协作",
    "limiter.py 与 app.py 配合时会把正常请求也限掉。请给出修正后的完整 limiter.py,"
    "运行 app_check.py 必须通过并打印 DONE 行。",
    {"limiter.py": '''class Limiter:
    def __init__(self, budget):
        self.budget = budget
        self.left = budget

    def take(self, n=1):
        self.left -= n
        return self.left >= 0
''',
     "app_check.py": '''from limiter import Limiter

lim = Limiter(3)
assert lim.take() is True
assert lim.take() is True
assert lim.take() is True
assert lim.take() is False
assert lim.take() is False
lim.reset()
assert lim.take() is True
assert lim.used() == 1
print("DONE")
'''},
    "limiter.py", "app_check.py",
    '''class Limiter:
    def __init__(self, budget):
        if budget < 0:
            raise ValueError("budget must be >= 0")
        self.budget = budget
        self.left = budget

    def take(self, n=1):
        if n > self.left:
            return False
        self.left -= n
        return True

    def used(self):
        return self.budget - self.left

    def reset(self):
        self.left = self.budget
''',
    [{"type": "any_of", "values": ["n > self.left", "先判断", "不足则不扣"], "label": "额度不足时不扣减", "weight": 1},
     {"type": "any_of", "values": ["reset", "used", "重置"], "label": "补齐 reset/used", "weight": 1}],
    "被拒绝的请求不应扣额度;还需要 reset() 与 used()",
    expect_stdout=["DONE"], must_fix="limiter.py")

exec_task(
    "d9m_pipe_inventory", "pipeline", "库存扣减管线",
    "inventory.py 在库存不足时会扣成负数。请给出修正后的完整 inventory.py,"
    "运行 inv_check.py 必须通过并打印 STOCK 行。",
    {"inventory.py": '''class Stock:
    def __init__(self, items):
        self.items = dict(items)

    def take(self, sku, qty):
        self.items[sku] = self.items.get(sku, 0) - qty
        return True
''',
     "inv_check.py": '''from inventory import Stock

s = Stock({"a": 5, "b": 0})
assert s.take("a", 3) is True
assert s.items["a"] == 2
assert s.take("a", 5) is False
assert s.items["a"] == 2
assert s.take("missing", 1) is False
assert s.take("b", 1) is False
assert s.take("a", 0) is True
assert s.available("a") == 2
print("STOCK %r" % sorted(s.items.items()))
'''},
    "inventory.py", "inv_check.py",
    '''class Stock:
    def __init__(self, items):
        self.items = dict(items)

    def take(self, sku, qty):
        if qty < 0:
            raise ValueError("qty must be >= 0")
        have = self.items.get(sku, 0)
        if qty > have:
            return False
        self.items[sku] = have - qty
        return True

    def available(self, sku):
        return self.items.get(sku, 0)
''',
    [{"type": "any_of", "values": ["qty > have", "不足", "先判断"], "label": "库存不足不扣减", "weight": 1},
     {"type": "any_of", "values": ["available", "get(sku", "缺省"], "label": "补齐查询接口", "weight": 1}],
    "未知 sku 视为 0 库存;扣减前必须比较",
    expect_stdout=["STOCK"], must_fix="inventory.py")

exec_task(
    "d9m_pipe_migrate", "pipeline", "数据迁移管线",
    "migrate.py 把用户数据从 v1 迁到 v2 时丢了字段也丢了顺序。请给出修正后的完整 "
    "migrate.py,运行 migrate_check.py 必须通过并打印 MIGRATED 行。",
    {"migrate.py": '''def migrate_v1_to_v2(records):
    """v1: {"uid", "mail", "tags": "a,b"} -> v2: {"id", "email", "tags": [..], "schema": 2}.
    Records without a uid are skipped. Output keeps the input order."""
    out = []
    for r in records:
        out.append({"email": r["mail"], "schema": 2})
    return out
''',
     "migrate_check.py": '''from migrate import migrate_v1_to_v2

V1 = [
    {"uid": "u1", "mail": "a@example.test", "tags": "x,y"},
    {"uid": "", "mail": "skip@example.test", "tags": ""},
    {"uid": "u2", "mail": "b@example.test", "tags": ""},
]

got = migrate_v1_to_v2(V1)
assert [r["id"] for r in got] == ["u1", "u2"], got
assert got[0] == {"id": "u1", "email": "a@example.test", "tags": ["x", "y"], "schema": 2}, got[0]
assert got[1]["tags"] == []
assert all(r["schema"] == 2 for r in got)
print("MIGRATED %d" % len(got))
'''},
    "migrate.py", "migrate_check.py",
    '''def migrate_v1_to_v2(records):
    """v1: {"uid", "mail", "tags": "a,b"} -> v2: {"id", "email", "tags": [..], "schema": 2}.
    Records without a uid are skipped. Output keeps the input order."""
    out = []
    for r in records:
        uid = (r.get("uid") or "").strip()
        if not uid:
            continue
        raw = r.get("tags") or ""
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        out.append({"id": uid, "email": r.get("mail", ""), "tags": tags, "schema": 2})
    return out
''',
    [{"type": "any_of", "values": ["uid", "id", "跳过"], "label": "保留主键并跳过空主键", "weight": 1},
     {"type": "any_of", "values": ["split", "tags", "列表"], "label": "标签拆成列表", "weight": 1}],
    "v2 需要 id/email/tags 列表/schema;空 uid 必须跳过",
    expect_stdout=["MIGRATED 2"], must_fix="migrate.py")

exec_task(
    "d9m_pipe_timeseries", "pipeline", "时间序列聚合",
    "tsagg.py 的窗口聚合把不同序列混在一起,而且窗口边界算错。请给出修正后的完整 "
    "tsagg.py,运行 ts_check.py 必须通过并打印 WINDOWS 行。",
    {"tsagg.py": '''def window_sum(points, width):
    """points: list of (series, t, value). Return {(series, window_start): sum},
    window_start = t - t % width."""
    out = {}
    for s, t, v in points:
        out[(s, t)] = out.get((s, t), 0) + v
    return out
''',
     "ts_check.py": '''from tsagg import window_sum

PTS = [
    ("cpu", 0, 1.0), ("cpu", 4, 2.0), ("cpu", 10, 5.0),
    ("mem", 1, 3.0), ("mem", 9, 1.0),
]
got = window_sum(PTS, 10)
assert got[("cpu", 0)] == 3.0, got
assert got[("cpu", 10)] == 5.0, got
assert got[("mem", 0)] == 4.0, got
assert len(got) == 3, got
print("WINDOWS %d" % len(got))
'''},
    "tsagg.py", "ts_check.py",
    '''def window_sum(points, width):
    """points: list of (series, t, value). Return {(series, window_start): sum},
    window_start = t - t % width."""
    if width < 1:
        raise ValueError("width must be >= 1")
    out = {}
    for s, t, v in points:
        key = (s, t - t % width)
        out[key] = out.get(key, 0) + v
    return out
''',
    [{"type": "any_of", "values": ["t - t % width", "取整", "窗口起点"], "label": "按窗口起点归并", "weight": 1},
     {"type": "any_of", "values": ["series", "序列", "分组"], "label": "按序列分组", "weight": 1}],
    "键必须是 (序列, 窗口起点),起点 = t - t % width",
    expect_stdout=["WINDOWS 3"], must_fix="tsagg.py")

exec_task(
    "d9m_pipe_validate", "pipeline", "输入校验管线",
    "validate.py 的批量校验遇到第一条坏数据就整体抛错,应该逐条收集错误。"
    "请给出修正后的完整 validate.py,运行 validate_check.py 必须通过并打印 ERRORS 行。",
    {"validate.py": '''def validate_all(rows):
    """Return a list of (index, reason) for invalid rows.
    A row is valid when name is a non-empty str, age is an int in 0..150,
    and email contains '@'. Valid rows contribute nothing."""
    errors = []
    for i, r in enumerate(rows):
        if not r["name"]:
            errors.append((i, "name"))
        if not (0 <= r["age"] <= 150):
            errors.append((i, "age"))
        if "@" not in r["email"]:
            errors.append((i, "email"))
    return errors
''',
     "validate_check.py": '''from validate import validate_all

ROWS = [
    {"name": "ok", "age": 30, "email": "a@example.test"},
    {"name": "", "age": 30, "email": "b@example.test"},
    {"name": "two", "age": 200, "email": "c@example.test"},
    {"name": "three", "age": 40, "email": "no-at-sign"},
    {"name": None, "age": "x", "email": None},
]

got = validate_all(ROWS)
assert (1, "name") in got, got
assert (2, "age") in got, got
assert (3, "email") in got, got
bad4 = [r for r in got if r[0] == 4]
assert len(bad4) == 3, bad4
assert not any(r[0] == 0 for r in got), got
print("ERRORS %d" % len(got))
'''},
    "validate.py", "validate_check.py",
    '''def validate_all(rows):
    """Return a list of (index, reason) for invalid rows.
    A row is valid when name is a non-empty str, age is an int in 0..150,
    and email contains '@'. Valid rows contribute nothing."""
    errors = []
    for i, r in enumerate(rows):
        name = r.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append((i, "name"))
        age = r.get("age")
        if isinstance(age, bool) or not isinstance(age, int) or not (0 <= age <= 150):
            errors.append((i, "age"))
        email = r.get("email")
        if not isinstance(email, str) or "@" not in email:
            errors.append((i, "email"))
    return errors
''',
    [{"type": "any_of", "values": ["isinstance", "类型", "None"], "label": "先判类型再判值", "weight": 1},
     {"type": "any_of", "values": ["逐条", "收集", "errors.append"], "label": "逐条收集而非抛错", "weight": 1}],
    "None 与非整型会直接抛异常;必须逐条收集并返回 (下标, 原因)",
    expect_stdout=["ERRORS 6"], must_fix="validate.py")

# ===========================================================================
# BATCH 5 - static: prose tasks scored by runner/d9_judge.py (24 tasks)
# Every check list below was written to be answerable from the material in the
# prompt only, and every "forbidden" entry is something the material does not say.
# ===========================================================================

static_task(
    "d9m_root_log", "log-rootcause", "日志根因定位",
    "下面是一段线上服务日志,请定位根因并给出修复方向。" + NL + NL +
    "2026-03-01T10:00:01 INFO  worker-1 start pid=4411" + NL +
    "2026-03-01T10:00:03 WARN  db pool wait_ms=1200 active=20 max=20" + NL +
    "2026-03-01T10:00:04 WARN  db pool wait_ms=2400 active=20 max=20" + NL +
    "2026-03-01T10:00:06 ERROR request timeout path=/api/orders elapsed_ms=30000" + NL +
    "2026-03-01T10:00:07 ERROR request timeout path=/api/orders elapsed_ms=30000" + NL +
    "2026-03-01T10:00:08 WARN  db pool wait_ms=5100 active=20 max=20" + NL +
    "2026-03-01T10:00:09 ERROR OOMKilled container=api replicas=2" + NL +
    "2026-03-01T10:00:10 INFO  worker-1 health=degraded",
    [{"type": "any_of", "values": ["连接池", "pool", "数据库连接"], "label": "定位到连接池", "weight": 1},
     {"type": "any_of", "values": ["耗尽", "打满", "active=20", "max=20", "上限"],
      "label": "说明连接池被占满", "weight": 1},
     {"type": "any_of", "values": ["超时", "timeout", "30"], "label": "解释请求超时", "weight": 1},
     {"type": "any_of", "values": ["OOM", "内存"], "label": "关联内存溢出", "weight": 1},
     {"type": "any_of", "values": ["扩容", "调大", "泄漏", "超时时间", "连接复用", "池大小"],
      "label": "给出可落地修复方向", "weight": 1},
     {"type": "forbidden", "values": ["磁盘写满", "DNS 解析失败", "证书过期"],
      "label": "编造日志里没有的原因", "weight": 1}],
    "根因是数据库连接池耗尽(active=20/max=20,等待时间逐秒攀升),请求排队 30 秒后超时,"
    "堆积的请求又推高内存导致 OOMKilled。修复方向:排查连接泄漏、提高池上限或缩短慢查询、"
    "给请求设置更短超时与熔断、对容器内存限额做复核。",
    "根因是磁盘写满导致所有请求超时,建议清理日志目录。",
    "日志根因题:必须先看 active/max 与等待时间的单调上升")

static_task(
    "d9m_root_build", "log-rootcause", "构建失败定位",
    "同事发来这段构建输出,请定位失败根因并说明怎么改。" + NL + NL +
    "[1/4] Resolving dependencies" + NL +
    "[2/4] Building wheel for fastkv-1.2" + NL +
    "error: subprocess-exited-with-error" + NL +
    "  x Building wheel for fastkv (pyproject.toml) did not run successfully." + NL +
    "  | ModuleNotFoundError: No module named 'Cython'" + NL +
    "  | note: This error originates from a subprocess, and is likely not a problem with pip." + NL +
    "[3/4] skipped" + NL +
    "ERROR: Failed building wheel for fastkv",
    [{"type": "any_of", "values": ["Cython", "构建依赖", "build dependency", "编译"],
      "label": "指出缺少 Cython", "weight": 1},
     {"type": "any_of", "values": ["subprocess", "隔离环境", "build isolation", "PEP 517"],
      "label": "说明构建隔离", "weight": 1},
     {"type": "any_of", "values": ["安装 Cython", "pip install cython", "build-system", "requires"],
      "label": "给出修复动作", "weight": 1},
     {"type": "any_of", "values": ["wheel", "预编译", "binary"], "label": "提出替代方案", "weight": 1},
     {"type": "forbidden", "values": ["网络不通", "代理设置错误", "磁盘空间不足"],
      "label": "编造输出里没有的原因", "weight": 1}],
    "构建在隔离环境里跑,fastkv 的构建期需要 Cython 但 pyproject.toml 的 build-system.requires "
    "没有声明它,所以隔离环境里 import 失败。修复:先 pip install cython 再 --no-build-isolation 安装,"
    "或者给上游补 build-system.requires,或者改用带 wheel 的版本。",
    "是网络问题,重试几次就好了。",
    "构建失败题:区分运行期依赖与构建期依赖")

static_task(
    "d9m_root_slow", "log-rootcause", "性能问题定位",
    "接口 P99 从 200ms 涨到 3s,监控和日志如下。请定位根因并给出排查/修复方向。" + NL + NL +
    "指标:QPS 稳定在 800;CPU 使用率 35% 稳定;堆内存每 30 分钟上涨一次并在 GC 后回落。" + NL +
    "慢查询日志:SELECT * FROM orders WHERE user_id = ? 平均 2100ms,命中行数 3。" + NL +
    "表结构:orders(id PRIMARY KEY, user_id INTEGER, amount REAL, created_at TEXT)" + NL +
    "执行计划:SCAN TABLE orders",
    [{"type": "any_of", "values": ["全表扫描", "SCAN TABLE", "没有索引", "缺索引", "索引"],
      "label": "定位到全表扫描", "weight": 1},
     {"type": "any_of", "values": ["user_id", "索引"], "label": "指出 user_id 缺索引", "weight": 1},
     {"type": "any_of", "values": ["CPU", "不是 CPU", "排除"], "label": "排除 CPU 瓶颈", "weight": 1},
     {"type": "any_of", "values": ["CREATE INDEX", "建索引", "覆盖索引", "加索引"],
      "label": "给出建索引动作", "weight": 1},
     {"type": "forbidden", "values": ["内存泄漏导致", "CPU 打满", "磁盘 IO 打满"],
      "label": "与给定指标矛盾的结论", "weight": 1}],
    "慢查询是 orders 表按 user_id 过滤,但表上没有 user_id 索引,执行计划是全表扫描;"
    "CPU 只有 35% 说明不是算力瓶颈。修复:给 user_id 建索引,复查该查询是否只需要少数列,"
    "必要时用覆盖索引;堆内存的周期性上涨与 GC 后回落是正常分配节奏,不是根因。",
    "CPU 打满了,需要加机器;顺便怀疑是内存泄漏。",
    "性能题:执行计划是最硬的证据")

static_task(
    "d9m_root_dns", "log-rootcause", "网络故障定位",
    "服务间调用开始间歇性失败,日志如下。请定位根因并给出修复方向。" + NL + NL +
    "ERROR dial tcp: lookup api.internal on 10.0.0.2:53: no such host" + NL +
    "ERROR dial tcp: lookup api.internal on 10.0.0.2:53: no such host" + NL +
    "INFO  retry attempt=1 target=api.internal:8080" + NL +
    "ERROR dial tcp 10.1.2.3:8080: connect: connection refused" + NL +
    "INFO  config reload: upstream=api.internal ttl=30s",
    [{"type": "any_of", "values": ["DNS", "域名解析", "lookup", "解析"], "label": "定位到 DNS", "weight": 1},
     {"type": "any_of", "values": ["no such host", "解析失败", "记录不存在"],
      "label": "指出解析不到记录", "weight": 1},
     {"type": "any_of", "values": ["10.0.0.2", "53", "DNS 服务器"], "label": "指出 DNS 服务器地址", "weight": 1},
     {"type": "any_of", "values": ["connection refused", "端口", "未监听"],
      "label": "区分两类错误", "weight": 1},
     {"type": "any_of", "values": ["检查 DNS 记录", "解析配置", "resolv.conf", "服务发现", "TTL"],
      "label": "给出修复方向", "weight": 1},
     {"type": "forbidden", "values": ["证书过期", "带宽不足"], "label": "编造无关原因", "weight": 1}],
    "两类错误并存:先是 DNS 解析失败(no such host,查询发往 10.0.0.2:53),"
    "后来解析到了地址但连接被拒(connection refused,说明端口没在监听)。"
    "修复:先核对 DNS 记录与服务发现注册,再看目标实例是否真的在 8080 上监听;"
    "注意配置里 upstream 的 TTL 是 30 秒,解析结果会周期性刷新。",
    "是 TLS 证书过期,更新证书即可。",
    "网络题:解析失败与连接被拒是两件事")

static_task(
    "d9m_dev_review", "code-review", "代码评审",
    "下面这个函数要按用户 ID 查订单并返回 JSON,请指出问题并给出改进后的写法。" + NL + NL +
    "def get_orders(uid):" + NL +
    "    sql = \"SELECT * FROM orders WHERE user_id = \" + uid" + NL +
    "    rows = db.query(sql)" + NL +
    "    return rows",
    [{"type": "any_of", "values": ["SQL 注入", "注入", "参数化", "占位符", "绑定参数"],
      "label": "指出注入风险", "weight": 1},
     {"type": "any_of", "values": ["SELECT *", "只取需要的列", "显式列"], "label": "指出 SELECT *", "weight": 1},
     {"type": "any_of", "values": ["分页", "limit", "LIMIT"], "label": "缺少分页/上限", "weight": 1},
     {"type": "any_of", "values": ["JSON", "序列化", "返回类型"], "label": "返回类型与需求不符", "weight": 1},
     {"type": "any_of", "values": ["?", "%s", "占位"], "label": "给出参数化写法", "weight": 1}],
    "四个问题:①字符串拼接 uid 造成 SQL 注入,必须用参数化查询;②SELECT * 取回全部列,"
    "应显式列出需要的字段;③没有 LIMIT/分页,大用户会一次拉全量;④需求要 JSON 但直接返回了行对象。"
    "改进写法:用 db.query('SELECT id, amount, created_at FROM orders WHERE user_id = ? LIMIT ?', (uid, 100)) "
    "并把结果序列化成 JSON 返回。",
    "代码没问题,只是性能一般,加个缓存就好。",
    "评审题:安全、字段、分页、返回类型四类问题")

static_task(
    "d9m_dev_refactor", "code-review", "重构建议",
    "这段代码要批量处理上传的表格文件,请指出风险并给出重构方向。" + NL + NL +
    "def process(files):" + NL +
    "    results = []" + NL +
    "    for f in files:" + NL +
    "        data = open(f).read()" + NL +
    "        rows = data.split('\\n')" + NL +
    "        for r in rows:" + NL +
    "            results.append(r.split(','))" + NL +
    "    return results",
    [{"type": "any_of", "values": ["csv", "csv 模块", "引号", "转义"], "label": "指出手写切分不可靠", "weight": 1},
     {"type": "any_of", "values": ["with open", "关闭", "句柄", "资源"], "label": "文件未正确关闭", "weight": 1},
     {"type": "any_of", "values": ["编码", "encoding", "utf-8"], "label": "缺少编码指定", "weight": 1},
     {"type": "any_of", "values": ["内存", "一次性", "流式", "生成器", "逐行"],
      "label": "全量读入内存的风险", "weight": 1},
     {"type": "any_of", "values": ["空行", "异常处理", "校验"], "label": "缺少边界与异常处理", "weight": 1}],
    "五类问题:①自己 split(',') 处理不了带引号或转义的字段,应该用 csv 模块;"
    "②open(f).read() 没有 with,句柄可能泄漏;③没有指定 encoding,跨平台会乱;"
    "④一次性把整个文件读进内存,大文件会撑爆,应逐行流式处理;⑤没有跳过空行/坏行与异常处理。"
    "重构方向:用 csv.reader 逐行迭代,with open(f, encoding='utf-8', newline='') as fh,"
    "对每行做字段数校验,把结果用生成器或分批写入的方式交出。",
    "这段代码很好,只需要把变量名改得更清楚一点。",
    "重构题:正确性、资源、编码、内存、健壮性")

static_task(
    "d9m_dev_test", "test-design", "测试设计",
    "下面这个函数要按传入的尺寸把内容切成多页,请设计测试用例(说明每条用例测什么)。" + NL + NL +
    "def paginate(items, per_page):" + NL +
    "    \"\"\"Return a list of pages, each a list of at most per_page items.\"\"\"" + NL +
    "    if per_page <= 0:" + NL +
    "        raise ValueError('per_page must be positive')" + NL +
    "    return [items[i:i + per_page] for i in range(0, len(items), per_page)]",
    [{"type": "any_of", "values": ["空列表", "空输入", "[]"], "label": "空输入", "weight": 1},
     {"type": "any_of", "values": ["整除", "正好", "边界"], "label": "整除边界", "weight": 1},
     {"type": "any_of", "values": ["余数", "不整除", "余下"], "label": "不整除", "weight": 1},
     {"type": "any_of", "values": ["per_page", "0", "负", "ValueError"], "label": "非法参数", "weight": 1},
     {"type": "any_of", "values": ["大于", "超过长度", "per_page 大于"], "label": "每页大于总数", "weight": 1}],
    "至少五类用例:①空列表 -> [];②整除(6 个元素 per_page=3)-> 两页;"
    "③不整除(7 个元素 per_page=3)-> 最后一页只有 1 个;④per_page 为 0 或负数 -> 抛 ValueError;"
    "⑤per_page 大于列表长度 -> 只有一页且内容完整。补充:单元素、元素为可变对象时是否共享引用、"
    "以及返回的是否为新列表(不修改入参)。",
    "测一下正常情况就行,比如 paginate([1,2,3], 2) 返回 [[1,2],[3]]。",
    "测试设计题:边界值 + 异常路径 + 不变量")

static_task(
    "d9m_dev_perf", "code-review", "性能优化",
    "这个函数在 10 万条数据上要跑 40 秒,请指出瓶颈并给出优化方向。" + NL + NL +
    "def dedupe_and_count(records):" + NL +
    "    out = []" + NL +
    "    for r in records:" + NL +
    "        if r not in out:" + NL +
    "            out.append(r)" + NL +
    "    counts = {}" + NL +
    "    for r in out:" + NL +
    "        counts[r] = 0" + NL +
    "        for x in records:" + NL +
    "            if x == r:" + NL +
    "                counts[r] += 1" + NL +
    "    return counts",
    [{"type": "any_of", "values": ["O(n^2)", "平方", "嵌套循环", "两次遍历"], "label": "指出复杂度", "weight": 1},
     {"type": "any_of", "values": ["set", "哈希", "字典"], "label": "用集合/字典去重", "weight": 1},
     {"type": "any_of", "values": ["Counter", "collections", "计数器"], "label": "用现成计数器", "weight": 1},
     {"type": "any_of", "values": ["一次遍历", "单次遍历", "一遍"], "label": "改为单次遍历", "weight": 1}],
    "瓶颈是两处 O(n^2):①用列表 out 做成员判断 r not in out 是线性查找,应改成 set;"
    "②对每个去重后的元素再完整扫一遍 records 计数,也是平方级。"
    "优化:一次遍历用 dict 同时完成去重与计数,或者直接 collections.Counter(records);"
    "整体从 O(n^2) 降到 O(n)。",
    "把循环改成列表推导式就会快很多。",
    "性能题:先算复杂度再谈写法")

static_task(
    "d9m_dev_api", "api-design", "接口设计",
    "需要一个「批量查询订单状态」的接口给前端轮询,请给出接口设计并说明取舍。" + NL +
    "已知:订单量峰值约 5000 条/次查询;状态变化不频繁;前端每 3 秒轮询一次;" +
    "后端是单实例 Python 服务,数据库是 SQLite。",
    [{"type": "any_of", "values": ["POST", "批量", "body"], "label": "用 POST 传批量 ID", "weight": 1},
     {"type": "any_of", "values": ["分页", "分批", "上限", "limit"], "label": "分批与上限", "weight": 1},
     {"type": "any_of", "values": ["缓存", "ETag", "304", "增量"], "label": "缓存/增量", "weight": 1},
     {"type": "any_of", "values": ["5000", "太大", "单次"], "label": "质疑单次 5000 的规模", "weight": 1},
     {"type": "any_of", "values": ["SQLite", "单实例", "并发", "写锁"], "label": "考虑 SQLite 限制", "weight": 1}],
    "建议 POST /orders/status,请求体带 ids 数组(单次上限 500,超出由客户端分批);"
    "响应返回 {id, status, updated_at} 列表。取舍:GET 加超长 query 在 5000 个 ID 时会撞 URL 长度限制,"
    "所以用 POST;状态变化不频繁,可以带 ETag 或返回 since 参数做增量,"
    "把 3 秒轮询的无效负载压下去。还要注意 SQLite 单写锁:轮询是读多写少,"
    "但批量读也要用 IN 查询并限制返回列,避免长事务阻塞写入。",
    "用 GET /orders?ids=1,2,3...,把 5000 个 ID 都拼在 URL 里,简单直接。",
    "接口设计题:规模与存储引擎的限制必须被提到")

static_task(
    "d9m_dev_migration", "code-review", "数据迁移方案",
    "要把一张 2000 万行的订单表加上新列 status 并回填历史数据,请给出迁移方案与风险。" + NL +
    "现状:表 orders(id, user_id, amount, created_at),线上持续写入,数据库为 SQLite 单文件。",
    [{"type": "any_of", "values": ["ALTER TABLE", "加列", "ADD COLUMN"], "label": "加列方式", "weight": 1},
     {"type": "any_of", "values": ["分批", "batch", "批次"], "label": "分批回填", "weight": 1},
     {"type": "any_of", "values": ["锁", "写入阻塞", "停机"], "label": "评估锁与停机", "weight": 1},
     {"type": "any_of", "values": ["回滚", "备份", "校验"], "label": "回滚与校验", "weight": 1},
     {"type": "any_of", "values": ["默认值", "DEFAULT", "NOT NULL"], "label": "默认值与空值处理", "weight": 1}],
    "SQLite 的 ALTER TABLE ADD COLUMN 只支持加带默认值的列且很快,但回填 UPDATE 会长时间持有写锁,"
    "期间线上写入排队。方案:①先加列(带默认值,允许 NULL);②按主键区间分批 UPDATE,每批 5000-20000 行,"
    "批间让出锁并监控等待;③回填期间新写入由应用层直接写 status,避免新旧数据不一致;"
    "④迁移前做文件级备份,迁移后用行数与非空计数校验;⑤准备好回滚(保留旧文件,必要时切回)。"
    "风险:单文件 SQLite 没有在线 DDL,长时间写锁会直接表现为接口超时,必须放在低峰期并限制批次大小。",
    "直接一条 UPDATE orders SET status='done' 全表刷新,几秒钟就好。",
    "迁移题:必须提到写锁、分批与回滚")

static_task(
    "d9m_dev_config", "config-review", "配置评审",
    "下面是一份生产环境配置,请指出风险并说明改法。" + NL + NL +
    "debug = true" + NL +
    "log_level = \"trace\"" + NL +
    "db_url = \"sqlite:///./prod.db\"" + NL +
    "workers = 1" + NL +
    "request_timeout_s = 0" + NL +
    "cors_allow_origins = [\"*\"]",
    [{"type": "any_of", "values": ["debug", "调试"], "label": "生产开 debug", "weight": 1},
     {"type": "any_of", "values": ["日志", "log_level", "trace"], "label": "日志级别过高", "weight": 1},
     {"type": "any_of", "values": ["SQLite", "sqlite", "数据库"], "label": "数据库选型风险", "weight": 1},
     {"type": "any_of", "values": ["超时", "timeout", "0"], "label": "超时为 0", "weight": 1},
     {"type": "any_of", "values": ["CORS", "跨域", "来源"], "label": "CORS 全放开", "weight": 1}],
    "五项风险:①debug=true 在生产会暴露详细错误页与内部信息,必须关闭;"
    "②log_level=trace 会产生巨量日志并拖慢吞吐,生产用 info;"
    "③SQLite 单文件不适合多写并发的生产流量,应换成支持并发的数据库;"
    "④request_timeout_s=0 通常意味着永不超时,慢请求会占满 worker,应设成具体秒数;"
    "⑤CORS 允许任意来源等于对浏览器端放弃同源保护,应收敛到白名单。"
    "另外 workers=1 没有利用多核,但也可能是刻意的单写保护,需要结合数据库选型一起定。",
    "配置看起来都挺合理的,把 workers 调大就行。",
    "配置评审题:五处风险各对应一个检查项")

static_task(
    "d9m_dev_shell", "shell-script", "Shell 脚本修复",
    "这个脚本要清理 7 天前的临时文件,请指出问题并给出修正后的写法。" + NL + NL +
    "#!/bin/bash" + NL +
    "cd /tmp/work" + NL +
    "rm -rf *" + NL +
    "find . -name '*.log' -delete",
    [{"type": "any_of", "values": ["rm -rf", "通配符", "*", "危险"], "label": "指出危险删除", "weight": 1},
     {"type": "any_of", "values": ["mtime", "-mtime", "7"], "label": "缺少按时间过滤", "weight": 1},
     {"type": "any_of", "values": ["set -e", "set -u", "严格模式"], "label": "缺少严格模式", "weight": 1},
     {"type": "any_of", "values": ["引号", "变量", "cd 失败"], "label": "cd 失败未处理", "weight": 1}],
    "问题:①rm -rf * 会删掉目录里的一切,和「清理 7 天前临时文件」的需求完全不符,而且一旦 cd 失败"
    "就会在错误的目录里执行;②find 没有 -mtime 过滤,会把所有 .log 都删掉,不管新旧;"
    "③没有 set -euo pipefail,任何一步失败都静默继续;④cd 前没做存在性检查。"
    "修正方向:先 cd 到目标目录并检查返回值,用 find 加 -mtime +7 -type f 精确定位,"
    "先 dry-run 打印再执行,最后统一删除。",
    "脚本没问题,rm -rf * 就是清理临时文件的意思。",
    "脚本题:破坏性操作必须先定位范围")

static_task(
    "d9m_dev_sql", "sql-review", "SQL 评审",
    "这条查询在 500 万行的表上要跑 20 秒,请指出问题并给出改法。" + NL + NL +
    "SELECT * FROM orders" + NL +
    "WHERE strftime('%Y-%m', created_at) = '2026-03'" + NL +
    "ORDER BY created_at DESC" + NL +
    "LIMIT 20;",
    [{"type": "any_of", "values": ["函数", "索引失效", "strftime"], "label": "函数导致索引失效", "weight": 1},
     {"type": "any_of", "values": ["范围", "BETWEEN", ">=", "区间"], "label": "改为范围条件", "weight": 1},
     {"type": "any_of", "values": ["SELECT *", "覆盖索引", "需要的列"], "label": "避免 SELECT *", "weight": 1},
     {"type": "any_of", "values": ["created_at", "索引"], "label": "建 created_at 索引", "weight": 1}],
    "对 created_at 套 strftime 之后,列上即使有索引也用不上,优化器只能全表扫描再过滤。"
    "改法:把条件改写成范围比较,created_at >= '2026-03-01' AND created_at < '2026-04-01',"
    "并给 created_at 建索引;同时把 SELECT * 换成实际需要的列,让索引能覆盖排序与过滤。",
    "加个 LIMIT 20 就够快了,数据库会自动优化。",
    "SQL 题:函数包裹列是最常见的索引失效原因")

static_task(
    "d9m_dev_concurrency", "code-review", "并发问题",
    "这个函数在多线程下会丢计数,请指出原因并给出修正写法。" + NL + NL +
    "counter = 0" + NL + NL +
    "def inc():" + NL +
    "    global counter" + NL +
    "    value = counter" + NL +
    "    value = value + 1" + NL +
    "    counter = value",
    [{"type": "any_of", "values": ["竞态", "race", "原子"], "label": "指出竞态条件", "weight": 1},
     {"type": "any_of", "values": ["锁", "Lock", "互斥"], "label": "加锁", "weight": 1},
     {"type": "any_of", "values": ["读改写", "读-改-写", "三步"], "label": "说明读改写非原子", "weight": 1},
     {"type": "any_of", "values": ["GIL", "字节码", "不保证"], "label": "解释 GIL 不足以保护", "weight": 1}],
    "counter 的读取、加一、写回是三个独立步骤,线程切换可能发生在任意两步之间,"
    "两个线程会读到同一个旧值,后写的覆盖先写的,于是丢计数。GIL 只保证单条字节码不被打断,"
    "并不保证这三步整体原子。修正:用 threading.Lock 把读改写包起来,"
    "或者改成不依赖共享可变状态的设计(比如每线程本地计数最后合并,或使用队列)。",
    "Python 有 GIL,所以这段代码是线程安全的。",
    "并发题:GIL 不等于原子性")

static_task(
    "d9m_dev_error", "code-review", "异常处理评审",
    "这段重试代码在生产上会放大故障,请指出问题并给出改法。" + NL + NL +
    "for i in range(5):" + NL +
    "    try:" + NL +
    "        resp = call_api()" + NL +
    "        break" + NL +
    "    except Exception:" + NL +
    "        continue",
    [{"type": "any_of", "values": ["裸 except", "except Exception", "吞掉"], "label": "捕获过宽", "weight": 1},
     {"type": "any_of", "values": ["退避", "backoff", "等待", "sleep"], "label": "缺少退避", "weight": 1},
     {"type": "any_of", "values": ["重试次数", "上限", "5 次"], "label": "重试次数与策略", "weight": 1},
     {"type": "any_of", "values": ["幂等", "重复提交", "副作用"], "label": "重试的幂等风险", "weight": 1},
     {"type": "any_of", "values": ["日志", "记录", "可观测"], "label": "缺少日志", "weight": 1}],
    "四个问题:①except Exception 把编程错误也当成可重试故障,真正的问题被吞掉且没有任何记录;"
    "②五次重试之间没有退避,故障时会瞬间打出五倍流量,把小故障放大成雪崩;"
    "③重试是否安全取决于接口幂等性,非幂等写接口重试会重复下单;"
    "④完全没有日志,失败后无从排查。改法:只捕获明确的网络/限流异常,"
    "用指数退避加随机抖动,给总时长设上限,记录每次失败原因,并确认接口幂等(或带幂等键)。",
    "把重试次数从 5 次改成 10 次会更稳。",
    "异常处理题:重试必须配退避与幂等")

static_task(
    "d9m_dev_docker", "config-review", "容器配置评审",
    "下面是一个服务的容器配置,请指出风险并说明改法。" + NL + NL +
    "FROM python:3.12" + NL +
    "COPY . /app" + NL +
    "WORKDIR /app" + NL +
    "RUN pip install -r requirements.txt" + NL +
    "CMD python app.py",
    [{"type": "any_of", "values": ["缓存", "分层", "先拷贝"], "label": "依赖层缓存失效", "weight": 1},
     {"type": "any_of", "values": ["非 root", "USER", "root"], "label": "以 root 运行", "weight": 1},
     {"type": "any_of", "values": ["基础镜像", "固定版本", "tag", "latest"], "label": "基础镜像未固定", "weight": 1},
     {"type": "any_of", "values": ["体积", "slim", "多阶段"], "label": "镜像体积", "weight": 1},
     {"type": "any_of", "values": ["CMD", "exec", "信号"], "label": "CMD 形式与信号处理", "weight": 1}],
    "五个问题:①COPY . 放在 pip install 之前,任何源码改动都会让依赖层缓存失效,构建变慢,"
    "应先 COPY requirements.txt 再装依赖;②没有 USER,容器以 root 运行,逃逸后果更严重;"
    "③基础镜像只写 python:3.12 没有固定小版本与摘要,构建不可复现;"
    "④用的是完整镜像,可以换 slim 或多阶段构建减小体积;"
    "⑤CMD 用 shell 形式,python 会变成子进程,收不到 SIGTERM,优雅停机失效,应写成 exec 形式。",
    "这个 Dockerfile 很标准,没什么问题。",
    "容器题:缓存层顺序与信号处理是高频考点")

static_task(
    "d9m_dev_observe", "ops-runbook", "监控与排障",
    "线上接口错误率突然升到 8%,请你给出排查顺序和每一步要看什么。" + NL +
    "已知:服务有 Prometheus 指标、结构化日志、链路追踪。",
    [{"type": "any_of", "values": ["错误率", "指标", "Prometheus"], "label": "先看指标", "weight": 1},
     {"type": "any_of", "values": ["日志", "错误日志"], "label": "再看日志", "weight": 1},
     {"type": "any_of", "values": ["链路", "trace", "追踪"], "label": "用链路定位", "weight": 1},
     {"type": "any_of", "values": ["变更", "发布", "配置"], "label": "关联最近变更", "weight": 1},
     {"type": "any_of", "values": ["回滚", "止血", "降级"], "label": "止血优先", "weight": 1}],
    "顺序:①先用指标确认范围与时间点(错误率、延迟、QPS、依赖健康度,区分是全局还是单个接口/单个实例);"
    "②看错误日志聚合出错误类型与堆栈,确认是业务异常、依赖超时还是资源问题;"
    "③用链路追踪挑几条失败请求,定位到具体依赖或代码段;"
    "④对照最近的发布/配置变更时间线,确认是否同源;"
    "⑤止血优先(回滚或降级),再定位根因。每一步都要留记录,避免边查边忘。",
    "先重启服务试试,不行再看代码。",
    "运维题:范围 -> 日志 -> 链路 -> 变更 -> 止血")

static_task(
    "d9m_dev_a11y", "ui-review", "前端评审",
    "下面是一个搜索框的实现,请指出可用性问题并给出改法。" + NL + NL +
    "<div class=\"search\" onclick=\"doSearch()\">" + NL +
    "  <input placeholder=\"搜索\" />" + NL +
    "</div>" + NL + NL +
    "样式:输入框无 label,搜索按钮是一个 div,颜色 #ccc 文字 #ddd。",
    [{"type": "any_of", "values": ["label", "标签", "无障碍", "aria"], "label": "缺少可访问名称", "weight": 1},
     {"type": "any_of", "values": ["键盘", "tabindex", "focus", "焦点"], "label": "键盘不可用", "weight": 1},
     {"type": "any_of", "values": ["对比度", "#ccc", "颜色", "看不清"], "label": "对比度不足", "weight": 1},
     {"type": "any_of", "values": ["button", "语义", "role"], "label": "用 div 冒充按钮", "weight": 1},
     {"type": "any_of", "values": ["回车", "Enter", "提交"], "label": "缺少回车提交", "weight": 1}],
    "问题:①输入框没有 label 或 aria-label,读屏软件只会读出「编辑框」;"
    "②可点击的是 div,既不能 Tab 聚焦也没有键盘触发,必须换成 button 或补 role=button + tabindex + 键盘事件;"
    "③#ccc 底配 #ddd 字的对比度远低于无障碍标准,正文几乎看不清;"
    "④没有处理回车提交,用户按回车不会有反应。"
    "改法:用 form + label + button,补 aria-label,提高对比度到合规,监听 submit 处理回车。",
    "样式改成深色一点就行,其他没问题。",
    "前端评审题:可访问性四要素")

static_task(
    "d9m_dev_bench", "eval-design", "评测方案设计",
    "要比较两个本地模型在同一台 8GB 显存机器上的能力差异,请给出评测方案要点。" + NL +
    "已知:显存只有 8GB,两个模型量化不同(一个 IQ4_XS 约 13GB,一个 Q4_K_M 约 20GB)。",
    [{"type": "any_of", "values": ["量化", "量化差异", "不公平"], "label": "指出量化不可比", "weight": 1},
     {"type": "any_of", "values": ["参数", "温度", "采样"], "label": "固定采样参数", "weight": 1},
     {"type": "any_of", "values": ["上下文", "ctx"], "label": "固定上下文长度", "weight": 1},
     {"type": "any_of", "values": ["样本量", "题量", "重复"], "label": "样本量与重复", "weight": 1},
     {"type": "any_of", "values": ["速度", "t/s", "token"], "label": "同时记录速度", "weight": 1}],
    "要点:①两个模型量化不同、体积都超过 8GB 显存,必须部分卸载到内存,所以比的是「这套硬件上的可用性」,"
    "不能直接说成模型本身的能力差异,这一点要在结论里讲清楚;②采样参数(温度、top_p、种子)必须固定;"
    "③上下文长度要一致,并且分别记录不同上下文下的表现,因为卸载后长上下文会显著变慢;"
    "④题目要有足够样本量,同一题重复多次取稳定值,避免单次抖动;"
    "⑤除正确率外必须同时记录 t/s 与首 token 延迟,否则无法区分「更准」和「更慢」;"
    "⑥记录显存占用与 ngl 配置,保证两次测试的卸载比例是已知的。",
    "直接各跑一遍同样的题,谁分数高谁就强。",
    "评测设计题:可比性是第一位")

static_task(
    "d9m_dev_doc", "doc-write", "技术文档撰写",
    "请为一个「本地模型推理服务」写一段 README 的核心章节大纲,并说明每节要写什么。" + NL +
    "约束:读者是第一次接触这个服务的人;必须能在 10 分钟内跑起来。",
    [{"type": "any_of", "values": ["快速开始", "快速上手", "安装", "启动"], "label": "快速开始章节", "weight": 1},
     {"type": "any_of", "values": ["配置", "参数"], "label": "配置说明", "weight": 1},
     {"type": "any_of", "values": ["接口", "API", "请求"], "label": "接口示例", "weight": 1},
     {"type": "any_of", "values": ["排障", "常见问题", "FAQ", "错误"], "label": "排障章节", "weight": 1},
     {"type": "any_of", "values": ["前置", "依赖", "硬件", "显存"], "label": "前置条件", "weight": 1}],
    "大纲:①一句话说明这个服务做什么、不做什么;②前置条件(硬件显存、驱动、依赖版本);"
    "③快速开始(三条命令从零到跑通,给出可复制的命令与预期输出);④配置说明(启动参数表,"
    "标注默认值与影响);⑤接口示例(curl 请求与返回样例);⑥常见问题与排障(端口占用、显存不足、"
    "模型加载慢各自的现象与解法);⑦版本与变更记录。"
    "每节都要能回答「读者下一步该做什么」,快速开始必须在 10 分钟内可完成。",
    "写个简介、写个用法就行了,细节让用户自己看代码。",
    "文档题:快速开始与排障是新手最需要的两节")

static_task(
    "d9m_dev_dataqa", "data-analysis", "数据分析结论",
    "下面是一次 A/B 测试的结果,请给出结论并说明还需要什么信息。" + NL + NL +
    "对照组:访问 10000,转化 300(3.0%)" + NL +
    "实验组:访问 12000,转化 396(3.3%)" + NL +
    "测试时长 3 天,流量按用户 ID 哈希分配。",
    [{"type": "any_of", "values": ["显著", "显著性", "统计"], "label": "提出显著性检验", "weight": 1},
     {"type": "any_of", "values": ["样本", "样本量", "时长"], "label": "质疑样本量与时长", "weight": 1},
     {"type": "any_of", "values": ["3.0", "3.3", "提升"], "label": "给出提升幅度", "weight": 1},
     {"type": "any_of", "values": ["新奇效应", "周期", "工作日", "周末"], "label": "考虑时间效应", "weight": 1},
     {"type": "any_of", "values": ["置信区间", "p 值", "区间"], "label": "要求置信区间", "weight": 1}],
    "表面上看实验组转化率 3.3% 高于对照组 3.0%,相对提升约 10%,但绝对差异只有 0.3 个百分点,"
    "在这个样本量下需要做显著性检验并给出置信区间才能判断是否真实。"
    "还需要的信息:①测试只跑了 3 天,可能覆盖不到完整的工作日/周末周期;"
    "②流量按用户 ID 哈希分配,要确认两组在来源、设备、新老用户上是否均衡;"
    "③要检查是否存在新奇效应(用户第一次看到改动会短期提升行为);"
    "④需要明确这次测试的唯一主指标与停止规则,避免中途反复看结果导致假阳性。",
    "实验组转化率更高,直接全量上线。",
    "分析题:差异要谈显著性与置信区间")

static_task(
    "d9m_dev_plan", "roadmap-planning", "工程计划",
    "要把一个单文件脚本改造成可维护的服务,请给出分阶段计划与每阶段的验收标准。" + NL +
    "现状:一个 800 行的脚本,直接读写 SQLite,无测试,无日志,部署方式是手动 scp 到服务器再重启。",
    [{"type": "any_of", "values": ["测试", "用例", "回归"], "label": "先补测试", "weight": 1},
     {"type": "any_of", "values": ["拆分", "模块", "分层"], "label": "拆分模块", "weight": 1},
     {"type": "any_of", "values": ["日志", "监控", "可观测"], "label": "补可观测性", "weight": 1},
     {"type": "any_of", "values": ["部署", "自动化", "CI", "流水线"], "label": "部署自动化", "weight": 1},
     {"type": "any_of", "values": ["验收", "回滚", "灰度"], "label": "验收与回滚", "weight": 1}],
    "分阶段:①先给现有脚本补行为测试(哪怕只是把输入输出固化成用例),这是后续所有改动的安全网,"
    "验收标准是主要路径都有用例且能在本地一键跑;②按职责拆分模块(数据访问、业务逻辑、入口),"
    "每拆一步跑一次回归,验收标准是拆分前后输出一致;③补结构化日志与关键指标,"
    "验收标准是能回答「这次请求为什么慢/失败」;④把部署改成自动化流水线(构建、测试、发布),"
    "验收标准是一条命令完成发布且可回滚;⑤灰度发布并观察,验收标准是错误率与延迟在阈值内。"
    "每阶段都要能独立回滚。",
    "先重写成微服务,然后再补测试和监控。",
    "计划题:先有安全网再重构")

static_task(
    "d9m_dev_privacy", "data-analysis", "数据合规",
    "要做用户行为分析,需要把生产库数据导入分析库,请给出方案并指出合规风险。" + NL +
    "生产库里包含用户手机号、邮箱、收货地址与订单明细。",
    [{"type": "any_of", "values": ["脱敏", "匿名", "去标识"], "label": "脱敏处理", "weight": 1},
     {"type": "any_of", "values": ["最小", "只导需要的字段", "字段裁剪"], "label": "最小必要", "weight": 1},
     {"type": "any_of", "values": ["权限", "访问控制", "审计"], "label": "权限与审计", "weight": 1},
     {"type": "any_of", "values": ["留存", "删除", "生命周期"], "label": "留存与删除", "weight": 1},
     {"type": "any_of", "values": ["加密", "传输", "存储"], "label": "传输与存储安全", "weight": 1}],
    "方案要点:①先做脱敏:只导入分析真正需要的字段,手机号、邮箱、详细地址一律不进入分析库;"
    "确实需要用户维度时,用不可逆的哈希 ID 替代,并保留映射表在生产侧受控;"
    "②地址类信息可以降到城市级别再导出;③传输与存储都要加密,分析库的访问权限按角色最小化并留审计日志;"
    "④给分析数据设定留存期限与到期删除机制;⑤导出流程要有审批与记录,"
    "并明确分析结果不得反向识别到个人。风险主要是:字段过度导出、权限过宽、"
    "缺少审计导致无法追溯,以及留存失控。",
    "直接 mysqldump 全量导过去,反正是内网。",
    "合规题:最小必要与去标识化是核心")

static_task(
    "d9m_dev_model", "objective-compare", "模型选型",
    "要在 8GB 显存的笔记本上选一个本地模型做日常开发辅助,请给出选型思路与权衡。" + NL +
    "候选:一个 dense 27B 的 IQ4_XS(约 13GB),一个 MoE 35B-A3B 的 Q4_K_M(约 20GB)。",
    [{"type": "any_of", "values": ["MoE", "激活", "A3B"], "label": "MoE 激活参数少", "weight": 1},
     {"type": "any_of", "values": ["显存", "卸载", "ngl"], "label": "显存与卸载", "weight": 1},
     {"type": "any_of", "values": ["内存带宽", "带宽"], "label": "瓶颈在内存带宽", "weight": 1},
     {"type": "any_of", "values": ["上下文", "ctx"], "label": "上下文与 KV 缓存", "weight": 1},
     {"type": "any_of", "values": ["实测", "自己测", "基准"], "label": "建议实测", "weight": 1}],
    "思路:两个模型都超过 8GB 显存,必然要部分卸载到内存,所以速度上限主要由内存带宽决定而不是 GPU 算力。"
    "MoE 的 35B-A3B 每 token 只激活约 3B 参数,dense 27B 每 token 要过全部权重,"
    "所以在同样的卸载条件下 MoE 通常快得多;但 MoE 的总权重更大,加载更慢、占用内存更多。"
    "权衡:如果主要痛点是响应速度,倾向 MoE;如果更在意单次回答的稳定质量且能接受慢,"
    "dense 27B 更可预期。还要把上下文长度算进去:KV 缓存会额外吃显存,长上下文下能卸载的层数会变少。"
    "最终结论必须用自己机器上的实测数据来定,不要照搬别人的 t/s。",
    "选参数多的那个,参数越多越聪明,速度不重要。",
    "选型题:MoE 激活量与内存带宽是两个关键事实")

static_task(
    "d9m_dev_incident", "troubleshooting-guide", "故障复盘",
    "一次线上事故:发布后 10 分钟内错误率从 0.2% 升到 15%,回滚后恢复。请写出复盘要点。" + NL +
    "已知:变更内容是数据库连接池上限从 50 调到 10;监控告警在 8 分钟后才触发。",
    [{"type": "any_of", "values": ["连接池", "50", "10"], "label": "定位到连接池下调", "weight": 1},
     {"type": "any_of", "values": ["告警", "8 分钟", "延迟"], "label": "告警延迟问题", "weight": 1},
     {"type": "any_of", "values": ["回滚", "止血"], "label": "止血动作", "weight": 1},
     {"type": "any_of", "values": ["评审", "变更管理", "灰度"], "label": "变更管控", "weight": 1},
     {"type": "any_of", "values": ["根因", "为什么", "改进"], "label": "根因与改进项", "weight": 1}],
    "复盘要点:①时间线:发布 -> 错误率上升 -> 告警 -> 回滚 -> 恢复,每步给出时刻与判断依据;"
    "②根因:连接池上限从 50 降到 10,并发请求排队,等待超时转化为 5xx;"
    "③为什么没早发现:告警阈值与触发条件不合适,8 分钟才报,而且发布与告警之间没有联动;"
    "④为什么会被发出去:连接池这类容量参数缺少评审与灰度,直接全量;"
    "⑤改进项要可验收:容量类参数变更必须走评审、先在单实例灰度并观察连接等待指标、"
    "把错误率与连接池等待时间纳入发布期间的即时看板、把告警触发时间压到 1 分钟内。"
    "复盘不追责,只对事。",
    "事故原因是服务器不稳定,以后多发几次就没问题了。",
    "复盘题:时间线、根因、检测、预防四段都要有")

static_task(
    "d9m_dev_skill", "study-plan", "学习路径",
    "我想系统学会用本地大模型做应用开发,请给出一条可执行的学习路径。" + NL +
    "现状:会 Python,没接触过推理框架,机器是 8GB 显存的笔记本。",
    [{"type": "any_of", "values": ["推理", "llama.cpp", "引擎"], "label": "先跑通推理", "weight": 1},
     {"type": "any_of", "values": ["提示", "prompt"], "label": "提示工程", "weight": 1},
     {"type": "any_of", "values": ["接口", "API", "服务"], "label": "封装成服务", "weight": 1},
     {"type": "any_of", "values": ["评测", "评估", "基准"], "label": "评测方法", "weight": 1},
     {"type": "any_of", "values": ["量化", "显存", "卸载"], "label": "量化与显存约束", "weight": 1}],
    "路径:①第一周先把推理跑通:选一个能装进 8GB 显存的量化模型,用命令行跑起来,"
    "搞清 ngl、上下文长度、KV 缓存这几个参数各自影响什么;②第二周学提示与调用:"
    "把模型包成一个 HTTP 服务,写一个最小的客户端,理解流式输出与超时;"
    "③第三周做一个小应用(比如本地文档问答),重点是错误处理与降级,而不是模型本身;"
    "④第四周学评测:用固定题目集比较不同量化与参数下的正确率和速度,建立自己的基线;"
    "⑤之后按需深入:微调、RAG、多模型路由。每一步都要有可运行的产出物,不要只看文档。",
    "先看完所有论文,再动手。",
    "学习路径题:每阶段要有可运行产出")


# ---------------------------------------------------------------------------
# executable check: drop files, overwrite fix_file, run the command
# ---------------------------------------------------------------------------
def run_exec(row, code):
    """Return (passed, detail). code=None means 'leave the shipped files alone'."""
    spec = row["exec"]
    # the OS temp dir is outside the workspace sandbox, so work inside the project.
    # tempfile.mkdtemp() creates 0o700 dirs the sandbox cannot write into, so use
    # a plain unique directory name instead.
    scratch = os.path.join(ROOT, "results", "_d9m_scratch")
    tmp = os.path.join(scratch, "%s_%d" % (row["id"], next(_COUNTER)))
    os.makedirs(tmp, exist_ok=True)
    try:
        for name, body in spec["files"].items():
            fp = os.path.join(tmp, name)
            d = os.path.dirname(fp)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body)
        if code is not None:
            with open(os.path.join(tmp, spec["fix_file"]), "w", encoding="utf-8") as f:
                f.write(code)
        argv = [PY] + spec["run"].split()
        child_env = dict(os.environ)
        child_env["PYTHONIOENCODING"] = "utf-8"
        # Pipes created by a sandboxed parent hang on this host, so the child
        # writes to files in the scratch dir and we read them back.
        op = os.path.join(tmp, "_stdout.log")
        ep = os.path.join(tmp, "_stderr.log")
        with open(op, "w", encoding="utf-8") as fo, open(ep, "w", encoding="utf-8") as fe:
            try:
                rc = subprocess.run(argv, cwd=tmp, stdout=fo, stderr=fe,
                                    stdin=subprocess.DEVNULL,
                                    timeout=spec.get("timeout", 20),
                                    env=child_env).returncode
            except subprocess.TimeoutExpired:
                return False, "timeout after %ss" % spec.get("timeout", 20)
        out = open(op, encoding="utf-8", errors="replace").read()
        err = open(ep, encoding="utf-8", errors="replace").read()
        if rc != spec.get("expect_exit", 0):
            return False, "exit %s: %s" % (rc, (err or out).strip()[-260:])
        missing = [m for m in spec.get("expect_stdout", []) if m not in out]
        if missing:
            return False, "missing stdout markers %s (got %r)" % (missing, out.strip()[-200:])
        return True, (out.strip()[-160:] or "exit 0")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _load_judge():
    sys.path.insert(0, os.path.join(ROOT, "runner"))
    from d9_judge import judge_d9
    return judge_d9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--selfcheck", action="store_true",
                    help="sample 5 per dimension and score a right + a wrong answer")
    args = ap.parse_args()

    if not TASKS:
        print("no tasks defined")
        return
    rows = [_row(t) for t in TASKS]

    if args.verify:
        execs = [t for t in TASKS if t["kind"] == "exec"]
        rowmap = {r["id"]: r for r in rows}
        ok = bad = 0
        for t in execs:
            r = rowmap[t["id"]]
            good, gd = run_exec(r, t["ref"])
            neg, nd = run_exec(r, None)
            passed = good and not neg
            ok += 1 if passed else 0
            bad += 0 if passed else 1
            print("%s %-26s ref=%-5s (%s) | broken=%-5s (%s)"
                  % ("OK " if passed else "BAD", t["id"], good, gd[:70], neg, nd[:70]))
        print()
        print("exec tasks: %d  correct-behaviour=%d  wrong-behaviour=%d"
              % (len(execs), ok, bad))
        return

    if args.selfcheck:
        judge_d9 = _load_judge()
        statics = [t for t in TASKS if t["kind"] == "static"]
        print("%-24s %-6s %-6s" % ("task", "right", "wrong"))
        ok = bad = 0
        for t in statics:
            good = judge_d9({"checks": t["checks"]}, t["ref_answer"])
            neg = judge_d9({"checks": t["checks"]}, t["bad_answer"])
            passed = good["correct"] and not neg["correct"]
            ok += 1 if passed else 0
            bad += 0 if passed else 1
            print("%s %-24s %-6s %-6s  %s"
                  % ("OK " if passed else "BAD", t["id"], good["correct"],
                     neg["correct"], good.get("reason", "")[:40]))
        print()
        print("static tasks: %d  correct-behaviour=%d  wrong-behaviour=%d"
              % (len(statics), ok, bad))
        return

    existing = []
    if os.path.exists(TARGET):
        with open(TARGET, encoding="utf-8") as f:
            existing = [ln for ln in f if ln.strip()]
    have = set()
    for ln in existing:
        try:
            have.add(json.loads(ln)["id"])
        except Exception:
            pass
    new = [r for r in rows if r["id"] not in have]
    execs = [r for r in new if r["kind"] == "exec"]

    if args.dry_run:
        print("dry-run: %d new rows (%d exec), %d already present"
              % (len(new), len(execs), len(rows) - len(new)))
        return

    with open(TARGET, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + NL)

    from collections import Counter
    c = Counter(r["subcat"] for r in new)
    print("gen_d9_more: appended %d rows (skipped %d existing), %d of them executable"
          % (len(new), len(rows) - len(new), len(execs)))
    for k in sorted(c):
        print("   %-24s %d" % (k, c[k]))
    print("D9_full.jsonl now has %d rows" % (len(existing) + len(new)))


if __name__ == "__main__":
    main()
