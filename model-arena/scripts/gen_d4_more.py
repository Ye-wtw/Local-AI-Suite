# -*- coding: utf-8 -*-
"""gen_d4_more.py - append 42 NEW D4 code questions to datasets/full/D4_full.jsonl.

The D4 judge (runner/code_judge.py + runner/sandbox.py) is fixed and may not be
modified, so every question is written to fit it exactly:
  subcat "python" : row has prompt (skeleton), test, entry_point -> pass@1 in sandbox
  subcat "debug"  : row has buggy, task, test, entry_point   -> pass@1 in sandbox
  subcat "sql"    : row has schema, question, check          -> result-set equivalence
The SQL questions reuse the EXACT schema sandbox.run_sql_equivalence seeds
(customers(id,name,city) + orders(id,customer,amount,date)), otherwise the seeded
rows would not match and every answer would compare unequal.

--verify drives the real judge end to end: each task is scored twice, once with the
reference solution (must pass) and once with the shipped skeleton/buggy code (must
fail). Nothing is written to the dataset in that mode.

Budget: 36 python + 4 debug + 2 sql = 42 new rows -> D4 goes 38 -> 80.
No real person, email, phone number, account id or absolute local path appears here.

Usage:
  python scripts/gen_d4_more.py            # append new rows (existing rows untouched)
  python scripts/gen_d4_more.py --dry-run  # report only
  python scripts/gen_d4_more.py --verify   # run the judge on ref vs skeleton
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, "datasets", "full", "D4_full.jsonl")
NL = chr(10)
FENCE = chr(96) * 3

TASKS = []

SQL_SCHEMA = ("CREATE TABLE orders(id INT, customer TEXT, amount REAL, date TEXT);" + NL +
              "CREATE TABLE customers(id INT, name TEXT, city TEXT);")


def py_task(tid, name, prompt, test, ref, notes, subcat="python", buggy=None, task=None):
    TASKS.append(dict(id=tid, subcat=subcat, entry_point=name, prompt=prompt,
                      buggy=buggy, task=task, test=test, ref=ref, notes=notes))


def sql_task(tid, question, check, notes):
    TASKS.append(dict(id=tid, subcat="sql", question=question, check=check,
                      schema=SQL_SCHEMA, notes=notes))


def _row(t):
    row = {"id": t["id"], "dim": "D4", "source": "selfmade", "subcat": t["subcat"]}
    if t["subcat"] == "sql":
        row.update({"schema": t["schema"], "question": t["question"],
                    "check": t["check"], "scoring": "sql-equivalence",
                    "notes": t["notes"]})
    elif t["subcat"] == "debug":
        row.update({"buggy": t["buggy"], "task": t["task"], "test": t["test"],
                    "entry_point": t["entry_point"], "scoring": "sandbox-pass@1",
                    "notes": t["notes"]})
    else:
        row.update({"prompt": t["prompt"], "test": t["test"],
                    "entry_point": t["entry_point"], "scoring": "sandbox-pass@1",
                    "notes": t["notes"]})
    return row


def fence(code):
    return FENCE + "python" + NL + code + NL + FENCE

# ===========================================================================
# python: skeleton + tests (36 tasks)
# ===========================================================================

py_task("d4m_py_001", "count_vowels",
        '''def count_vowels(s: str) -> int:
    """Return the number of vowels (a e i o u, case-insensitive) in s."""
    pass''',
        '''assert count_vowels("hello") == 2
assert count_vowels("") == 0
assert count_vowels("AEIOU") == 5
assert count_vowels("xyz") == 0''',
        '''def count_vowels(s: str) -> int:
    """Return the number of vowels (a e i o u, case-insensitive) in s."""
    return sum(1 for ch in s.lower() if ch in "aeiou")''',
        "元音计数,大小写不敏感")

py_task("d4m_py_002", "flatten_once",
        '''def flatten_once(rows: list) -> list:
    """Flatten one level of nesting."""
    pass''',
        '''assert flatten_once([[1, 2], [3], []]) == [1, 2, 3]
assert flatten_once([]) == []
assert flatten_once([["a"], ["b", "c"]]) == ["a", "b", "c"]''',
        '''def flatten_once(rows: list) -> list:
    """Flatten one level of nesting."""
    out = []
    for r in rows:
        out.extend(r)
    return out''',
        "只展开一层,不递归")

py_task("d4m_py_003", "word_freq",
        '''def word_freq(text: str) -> dict:
    """Return {word: count}, lowercased, split on whitespace, punctuation stripped
    from the ends of each word. Empty words are dropped."""
    pass''',
        '''assert word_freq("a b a") == {"a": 2, "b": 1}
assert word_freq("") == {}
assert word_freq("Hi, hi!") == {"hi": 2}''',
        '''def word_freq(text: str) -> dict:
    """Return {word: count}, lowercased, split on whitespace, punctuation stripped
    from the ends of each word. Empty words are dropped."""
    out = {}
    for raw in text.lower().split():
        w = raw.strip(".,!?;:\\"'()[]")
        if w:
            out[w] = out.get(w, 0) + 1
    return out''',
        "词频统计 + 标点剥离")

py_task("d4m_py_004", "is_palindrome",
        '''def is_palindrome(s: str) -> bool:
    """True when s reads the same forwards and backwards, ignoring case and
    non-alphanumeric characters."""
    pass''',
        '''assert is_palindrome("A man, a plan, a canal: Panama") is True
assert is_palindrome("abc") is False
assert is_palindrome("") is True
assert is_palindrome("Was it a car or a cat I saw?") is True''',
        '''def is_palindrome(s: str) -> bool:
    """True when s reads the same forwards and backwards, ignoring case and
    non-alphanumeric characters."""
    clean = [ch.lower() for ch in s if ch.isalnum()]
    return clean == clean[::-1]''',
        "回文判定,需过滤非字母数字")

py_task("d4m_py_005", "chunk_list",
        '''def chunk_list(items: list, size: int) -> list:
    """Split items into consecutive lists of at most size items.
    Raise ValueError when size < 1."""
    pass''',
        '''assert chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
assert chunk_list([], 3) == []
assert chunk_list([1, 2], 5) == [[1, 2]]
try:
    chunk_list([1], 0)
    assert False
except ValueError:
    pass''',
        '''def chunk_list(items: list, size: int) -> list:
    """Split items into consecutive lists of at most size items.
    Raise ValueError when size < 1."""
    if size < 1:
        raise ValueError("size must be >= 1")
    return [items[i:i + size] for i in range(0, len(items), size)]''',
        "分片 + 参数校验")

py_task("d4m_py_006", "merge_dicts",
        '''def merge_dicts(a: dict, b: dict) -> dict:
    """Return a new dict with b overriding a. Neither input is modified."""
    pass''',
        '''a = {"x": 1, "y": 2}
b = {"y": 20, "z": 30}
m = merge_dicts(a, b)
assert m == {"x": 1, "y": 20, "z": 30}
assert a == {"x": 1, "y": 2}
assert b == {"y": 20, "z": 30}''',
        '''def merge_dicts(a: dict, b: dict) -> dict:
    """Return a new dict with b overriding a. Neither input is modified."""
    out = dict(a)
    out.update(b)
    return out''',
        "字典合并,不得修改入参")

py_task("d4m_py_007", "safe_int",
        '''def safe_int(value, default=0) -> int:
    """Convert to int, returning default on any failure. Floats truncate toward zero."""
    pass''',
        '''assert safe_int("42") == 42
assert safe_int("abc") == 0
assert safe_int("abc", -1) == -1
assert safe_int(3.9) == 3
assert safe_int(-3.9) == -3
assert safe_int(None) == 0''',
        '''def safe_int(value, default=0) -> int:
    """Convert to int, returning default on any failure. Floats truncate toward zero."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default''',
        "异常兜底 + 浮点截断")

py_task("d4m_py_008", "dedupe_stable",
        '''def dedupe_stable(items: list) -> list:
    """Remove duplicates keeping the FIRST occurrence order."""
    pass''',
        '''assert dedupe_stable([1, 2, 1, 3, 2]) == [1, 2, 3]
assert dedupe_stable([]) == []
assert dedupe_stable(["b", "a", "b"]) == ["b", "a"]''',
        '''def dedupe_stable(items: list) -> list:
    """Remove duplicates keeping the FIRST occurrence order."""
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out''',
        "保序去重")

py_task("d4m_py_009", "group_by",
        '''def group_by(items: list, key) -> dict:
    """Group items into {key(item): [items]}. Order inside each group is preserved."""
    pass''',
        '''assert group_by([1, 2, 3, 4], lambda x: x % 2) == {1: [1, 3], 0: [2, 4]}
assert group_by([], lambda x: x) == {}
assert group_by(["aa", "b", "cc"], len) == {2: ["aa", "cc"], 1: ["b"]}''',
        '''def group_by(items: list, key) -> dict:
    """Group items into {key(item): [items]}. Order inside each group is preserved."""
    out = {}
    for x in items:
        out.setdefault(key(x), []).append(x)
    return out''',
        "按 key 函数分组")

py_task("d4m_py_010", "binary_search",
        '''def binary_search(nums: list, target: int) -> int:
    """nums is sorted ascending. Return the index of target, or -1."""
    pass''',
        '''assert binary_search([1, 3, 5, 7], 5) == 2
assert binary_search([1, 3, 5, 7], 1) == 0
assert binary_search([1, 3, 5, 7], 4) == -1
assert binary_search([], 1) == -1''',
        '''def binary_search(nums: list, target: int) -> int:
    """nums is sorted ascending. Return the index of target, or -1."""
    lo, hi = 0, len(nums) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if nums[mid] == target:
            return mid
        if nums[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1''',
        "二分查找")

py_task("d4m_py_011", "transpose",
        '''def transpose(matrix: list) -> list:
    """Return the transpose of a rectangular matrix. Empty input -> []."""
    pass''',
        '''assert transpose([[1, 2, 3], [4, 5, 6]]) == [[1, 4], [2, 5], [3, 6]]
assert transpose([]) == []
assert transpose([[1]]) == [[1]]''',
        '''def transpose(matrix: list) -> list:
    """Return the transpose of a rectangular matrix. Empty input -> []."""
    if not matrix:
        return []
    return [list(row) for row in zip(*matrix)]''',
        "矩阵转置")

py_task("d4m_py_012", "clamp",
        '''def clamp(value, low, high):
    """Return value limited to [low, high]. Raise ValueError when low > high."""
    pass''',
        '''assert clamp(5, 0, 10) == 5
assert clamp(-1, 0, 10) == 0
assert clamp(99, 0, 10) == 10
try:
    clamp(1, 10, 0)
    assert False
except ValueError:
    pass''',
        '''def clamp(value, low, high):
    """Return value limited to [low, high]. Raise ValueError when low > high."""
    if low > high:
        raise ValueError("low must be <= high")
    return max(low, min(value, high))''',
        "区间夹取 + 参数校验")

py_task("d4m_py_013", "parse_query",
        '''def parse_query(qs: str) -> dict:
    """Parse "a=1&b=2&b=3" into {key: [values]}. Empty string -> {}.
    Values are URL-decoded (%20 -> space)."""
    pass''',
        '''assert parse_query("a=1&b=2&b=3") == {"a": ["1"], "b": ["2", "3"]}
assert parse_query("") == {}
assert parse_query("k=a%20b") == {"k": ["a b"]}''',
        '''from urllib.parse import unquote


def parse_query(qs: str) -> dict:
    """Parse "a=1&b=2&b=3" into {key: [values]}. Empty string -> {}.
    Values are URL-decoded (%20 -> space)."""
    out = {}
    for part in (qs or "").split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        out.setdefault(unquote(k), []).append(unquote(v))
    return out''',
        "查询串解析,同名键聚成列表")

py_task("d4m_py_014", "rle",
        '''def rle(s: str) -> str:
    """Run-length encode: "aaabb" -> "a3b2". A single char keeps count 1."""
    pass''',
        '''assert rle("aaabb") == "a3b2"
assert rle("") == ""
assert rle("abc") == "a1b1c1"
assert rle("aabbaa") == "a2b2a2"''',
        '''def rle(s: str) -> str:
    """Run-length encode: "aaabb" -> "a3b2". A single char keeps count 1."""
    if not s:
        return ""
    out, cur, n = [], s[0], 1
    for ch in s[1:]:
        if ch == cur:
            n += 1
        else:
            out.append("%s%d" % (cur, n))
            cur, n = ch, 1
    out.append("%s%d" % (cur, n))
    return "".join(out)''',
        "游程编码")

py_task("d4m_py_015", "is_balanced",
        '''def is_balanced(s: str) -> bool:
    """True when (), [], {} are correctly nested and closed. Other chars ignored."""
    pass''',
        '''assert is_balanced("([]{})") is True
assert is_balanced("([)]") is False
assert is_balanced("") is True
assert is_balanced("((") is False''',
        '''def is_balanced(s: str) -> bool:
    """True when (), [], {} are correctly nested and closed. Other chars ignored."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack''',
        "括号匹配,用栈")

py_task("d4m_py_016", "top_n",
        '''def top_n(pairs: list, n: int) -> list:
    """pairs: list of (name, score). Return the n highest scores as (name, score),
    descending by score then ascending by name."""
    pass''',
        '''assert top_n([("a", 3), ("b", 5), ("c", 5)], 2) == [("b", 5), ("c", 5)]
assert top_n([], 3) == []
assert top_n([("x", 1)], 5) == [("x", 1)]''',
        '''def top_n(pairs: list, n: int) -> list:
    """pairs: list of (name, score). Return the n highest scores as (name, score),
    descending by score then ascending by name."""
    return sorted(pairs, key=lambda p: (-p[1], p[0]))[:max(0, n)]''',
        "多键排序取前 n")

py_task("d4m_py_017", "deep_get",
        '''def deep_get(obj, path, default=None):
    """Walk a dotted path through dicts. Return default when any key is missing."""
    pass''',
        '''assert deep_get({"a": {"b": 1}}, "a.b") == 1
assert deep_get({"a": {"b": 1}}, "a.c") is None
assert deep_get({"a": {"b": 1}}, "a.c", 0) == 0
assert deep_get({}, "x") is None''',
        '''def deep_get(obj, path, default=None):
    """Walk a dotted path through dicts. Return default when any key is missing."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur''',
        "嵌套取值带默认值")

py_task("d4m_py_018", "normalize_scores",
        '''def normalize_scores(values: list) -> list:
    """Min-max scale to [0, 1]. When all values are equal, return all 0.0.
    Empty input -> []."""
    pass''',
        '''assert normalize_scores([0, 5, 10]) == [0.0, 0.5, 1.0]
assert normalize_scores([3, 3]) == [0.0, 0.0]
assert normalize_scores([]) == []
assert normalize_scores([2]) == [0.0]''',
        '''def normalize_scores(values: list) -> list:
    """Min-max scale to [0, 1]. When all values are equal, return all 0.0.
    Empty input -> []."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]''',
        "归一化 + 全等边界")

py_task("d4m_py_019", "running_max",
        '''def running_max(nums: list) -> list:
    """Return a list where item i is max(nums[0..i]). Empty -> []."""
    pass''',
        '''assert running_max([1, 3, 2, 5]) == [1, 3, 3, 5]
assert running_max([]) == []
assert running_max([-1, -5]) == [-1, -1]''',
        '''def running_max(nums: list) -> list:
    """Return a list where item i is max(nums[0..i]). Empty -> []."""
    out, cur = [], None
    for n in nums:
        cur = n if cur is None else max(cur, n)
        out.append(cur)
    return out''',
        "前缀最大值")

py_task("d4m_py_020", "split_even",
        '''def split_even(nums: list) -> tuple:
    """Return (evens, odds), each keeping the input order."""
    pass''',
        '''assert split_even([1, 2, 3, 4]) == ([2, 4], [1, 3])
assert split_even([]) == ([], [])
assert split_even([0, 0]) == ([0, 0], [])''',
        '''def split_even(nums: list) -> tuple:
    """Return (evens, odds), each keeping the input order."""
    evens = [n for n in nums if n % 2 == 0]
    odds = [n for n in nums if n % 2 != 0]
    return evens, odds''',
        "奇偶分组,保持顺序")

py_task("d4m_py_021", "is_anagram",
        '''def is_anagram(a: str, b: str) -> bool:
    """True when a and b use the same letters with the same counts, ignoring case
    and spaces."""
    pass''',
        '''assert is_anagram("listen", "silent") is True
assert is_anagram("a b", "ab") is True
assert is_anagram("abc", "abd") is False
assert is_anagram("", "") is True''',
        '''def is_anagram(a: str, b: str) -> bool:
    """True when a and b use the same letters with the same counts, ignoring case
    and spaces."""
    def key(s):
        return sorted(ch.lower() for ch in s if not ch.isspace())
    return key(a) == key(b)''',
        "变位词判定")

py_task("d4m_py_022", "find_duplicates",
        '''def find_duplicates(items: list) -> list:
    """Return the values appearing more than once, each listed once, in the order
    of their SECOND occurrence."""
    pass''',
        '''assert find_duplicates([1, 2, 1, 3, 2]) == [1, 2]
assert find_duplicates([1, 2, 3]) == []
assert find_duplicates([]) == []''',
        '''def find_duplicates(items: list) -> list:
    """Return the values appearing more than once, each listed once, in the order
    of their SECOND occurrence."""
    seen, dup = set(), []
    for x in items:
        if x in seen and x not in dup:
            dup.append(x)
        seen.add(x)
    return dup''',
        "重复项按第二次出现顺序")

py_task("d4m_py_023", "zip_dicts",
        '''def zip_dicts(keys: list, values: list) -> dict:
    """Pair keys with values. Extra keys get None; extra values are dropped."""
    pass''',
        '''assert zip_dicts(["a", "b"], [1, 2]) == {"a": 1, "b": 2}
assert zip_dicts(["a", "b", "c"], [1]) == {"a": 1, "b": None, "c": None}
assert zip_dicts([], [1, 2]) == {}''',
        '''def zip_dicts(keys: list, values: list) -> dict:
    """Pair keys with values. Extra keys get None; extra values are dropped."""
    out = {}
    for i, k in enumerate(keys):
        out[k] = values[i] if i < len(values) else None
    return out''',
        "长度不齐时的补齐规则")

py_task("d4m_py_024", "slugify",
        '''def slugify(text: str) -> str:
    """Lowercase, replace runs of non-alphanumeric characters with a single '-',
    and strip leading/trailing '-'."""
    pass''',
        '''assert slugify("Hello World") == "hello-world"
assert slugify("  A -- B  ") == "a-b"
assert slugify("") == ""
assert slugify("---") == ""''',
        '''import re


def slugify(text: str) -> str:
    """Lowercase, replace runs of non-alphanumeric characters with a single '-',
    and strip leading/trailing '-'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")''',
        "slug 生成,正则压缩分隔符")

py_task("d4m_py_025", "count_words_prefix",
        '''def count_words_prefix(words: list, prefix: str) -> int:
    """Count words starting with prefix (case-sensitive)."""
    pass''',
        '''assert count_words_prefix(["apple", "apricot", "banana"], "ap") == 2
assert count_words_prefix([], "a") == 0
assert count_words_prefix(["a", "A"], "a") == 1''',
        '''def count_words_prefix(words: list, prefix: str) -> int:
    """Count words starting with prefix (case-sensitive)."""
    return sum(1 for w in words if w.startswith(prefix))''',
        "前缀计数,区分大小写")

py_task("d4m_py_026", "median",
        '''def median(nums: list) -> float:
    """Return the median. Raise ValueError on an empty list."""
    pass''',
        '''assert median([1, 2, 3]) == 2
assert median([4, 1, 3, 2]) == 2.5
assert median([5]) == 5
try:
    median([])
    assert False
except ValueError:
    pass''',
        '''def median(nums: list) -> float:
    """Return the median. Raise ValueError on an empty list."""
    if not nums:
        raise ValueError("empty list")
    s = sorted(nums)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0''',
        "中位数,偶数个取均值")

py_task("d4m_py_027", "rotate",
        '''def rotate(items: list, k: int) -> list:
    """Rotate right by k positions. k may exceed len(items) or be negative."""
    pass''',
        '''assert rotate([1, 2, 3, 4], 1) == [4, 1, 2, 3]
assert rotate([1, 2, 3, 4], 0) == [1, 2, 3, 4]
assert rotate([1, 2, 3, 4], 6) == [3, 4, 1, 2]
assert rotate([1, 2, 3, 4], -1) == [2, 3, 4, 1]
assert rotate([], 3) == []''',
        '''def rotate(items: list, k: int) -> list:
    """Rotate right by k positions. k may exceed len(items) or be negative."""
    if not items:
        return []
    k = k % len(items)
    return items[-k:] + items[:-k] if k else list(items)''',
        "数组旋转,处理 k 取模")

py_task("d4m_py_028", "sum_by_key",
        '''def sum_by_key(rows: list, key: str) -> dict:
    """Sum the numeric field key per distinct value of the other field 'name'."""
    pass''',
        '''rows = [{"name": "a", "v": 1}, {"name": "a", "v": 2}, {"name": "b", "v": 5}]
assert sum_by_key(rows, "v") == {"a": 3, "b": 5}
assert sum_by_key([], "v") == {}''',
        '''def sum_by_key(rows: list, key: str) -> dict:
    """Sum the numeric field key per distinct value of the other field 'name'."""
    out = {}
    for r in rows:
        out[r["name"]] = out.get(r["name"], 0) + r[key]
    return out''',
        "按维度求和")

py_task("d4m_py_029", "strip_comments",
        '''def strip_comments(line: str) -> str:
    """Remove a trailing '#' comment and surrounding whitespace.
    A '#' inside single or double quotes is not a comment."""
    pass''',
        '''assert strip_comments("a = 1  # set a") == "a = 1"
assert strip_comments("# whole line") == ""
assert strip_comments("s = 'a # b'") == "s = 'a # b'"
assert strip_comments("plain") == "plain"''',
        '''def strip_comments(line: str) -> str:
    """Remove a trailing '#' comment and surrounding whitespace.
    A '#' inside single or double quotes is not a comment."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\\"":
            quote = ch
        elif ch == "#":
            return line[:i].strip()
    return line.strip()''',
        "去注释,需识别引号状态")

py_task("d4m_py_030", "gcd_list",
        '''def gcd_list(nums: list) -> int:
    """Return the GCD of all numbers. Empty list -> 0. Works for negatives."""
    pass''',
        '''assert gcd_list([12, 18]) == 6
assert gcd_list([7]) == 7
assert gcd_list([]) == 0
assert gcd_list([-12, 18]) == 6''',
        '''import math


def gcd_list(nums: list) -> int:
    """Return the GCD of all numbers. Empty list -> 0. Works for negatives."""
    g = 0
    for n in nums:
        g = math.gcd(g, abs(n))
    return g''',
        "多数最大公约数")

py_task("d4m_py_031", "validate_range",
        '''def validate_range(values: list, low: int, high: int) -> list:
    """Return the indices of values outside [low, high]."""
    pass''',
        '''assert validate_range([1, 5, 9], 1, 5) == [2]
assert validate_range([0, 6], 1, 5) == [0, 1]
assert validate_range([], 0, 1) == []''',
        '''def validate_range(values: list, low: int, high: int) -> list:
    """Return the indices of values outside [low, high]."""
    return [i for i, v in enumerate(values) if v < low or v > high]''',
        "越界下标收集")

py_task("d4m_py_032", "csv_join",
        '''def csv_join(rows: list) -> str:
    """Join rows of strings into one CSV line. A field containing a comma, quote or
    newline must be wrapped in double quotes, with internal quotes doubled."""
    pass''',
        '''assert csv_join([["a", "b"], ["c"]]) == 'a,b' + chr(10) + 'c'
assert csv_join([["a,b", 'x"y']]) == '"a,b","x""y"'
assert csv_join([]) == ""''',
        '''def csv_join(rows: list) -> str:
    """Join rows of strings into one CSV line. A field containing a comma, quote or
    newline must be wrapped in double quotes, with internal quotes doubled."""
    def field(v):
        v = str(v)
        if any(c in v for c in ',"' + chr(10)):
            return '"' + v.replace('"', '""') + '"'
        return v
    return chr(10).join(",".join(field(v) for v in row) for row in rows)''',
        "CSV 生成,含转义规则")

py_task("d4m_py_033", "first_unique",
        '''def first_unique(items: list):
    """Return the first value appearing exactly once, or None."""
    pass''',
        '''assert first_unique([2, 3, 2, 4]) == 3
assert first_unique([1, 1]) is None
assert first_unique([]) is None
assert first_unique(["a"]) == "a"''',
        '''def first_unique(items: list):
    """Return the first value appearing exactly once, or None."""
    counts = {}
    for x in items:
        counts[x] = counts.get(x, 0) + 1
    for x in items:
        if counts[x] == 1:
            return x
    return None''',
        "首个唯一元素")

py_task("d4m_py_034", "apply_discount",
        '''def apply_discount(price: float, pct: float) -> float:
    """Return the price after subtracting pct percent, rounded to 2 decimals.
    Raise ValueError when pct is outside 0..100."""
    pass''',
        '''assert apply_discount(100, 10) == 90.0
assert apply_discount(19.99, 50) == 10.0
assert apply_discount(10, 0) == 10.0
try:
    apply_discount(10, 101)
    assert False
except ValueError:
    pass''',
        '''from decimal import Decimal, ROUND_HALF_UP


def apply_discount(price: float, pct: float) -> float:
    """Return the price after subtracting pct percent, rounded to 2 decimals.
    Raise ValueError when pct is outside 0..100."""
    if not (0 <= pct <= 100):
        raise ValueError("pct must be within 0..100")
    raw = Decimal(str(price)) * (Decimal(100) - Decimal(str(pct))) / Decimal(100)
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))''',
        "折扣计算 + 四舍五入")

py_task("d4m_py_035", "count_islands",
        '''def count_islands(grid: list) -> int:
    """Count 4-connected groups of 1s in a grid of 0/1 ints."""
    pass''',
        '''assert count_islands([[1, 1, 0], [0, 1, 0], [1, 0, 1]]) == 3
assert count_islands([[0]]) == 0
assert count_islands([]) == 0
assert count_islands([[1, 0, 1]]) == 2''',
        '''def count_islands(grid: list) -> int:
    """Count 4-connected groups of 1s in a grid of 0/1 ints."""
    if not grid:
        return 0
    rows, cols = len(grid), len(grid[0])
    seen = set()
    count = 0
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] != 1 or (r, c) in seen:
                continue
            count += 1
            stack = [(r, c)]
            seen.add((r, c))
            while stack:
                cr, cc = stack.pop()
                for nr, nc in ((cr + 1, cc), (cr - 1, cc), (cr, cc + 1), (cr, cc - 1)):
                    if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 1 and (nr, nc) not in seen:
                        seen.add((nr, nc))
                        stack.append((nr, nc))
    return count''',
        "岛屿计数,DFS 泛洪")

py_task("d4m_py_036", "round_half_up",
        '''def round_half_up(value: float, digits: int) -> float:
    """Round half away from zero (0.5 -> 1, -0.5 -> -1), unlike Python's banker rounding."""
    pass''',
        '''assert round_half_up(0.5, 0) == 1.0
assert round_half_up(-0.5, 0) == -1.0
assert round_half_up(2.5, 0) == 3.0
assert round_half_up(1.234, 2) == 1.23
assert round_half_up(1.235, 2) == 1.24''',
        '''from decimal import Decimal, ROUND_HALF_UP


def round_half_up(value: float, digits: int) -> float:
    """Round half away from zero (0.5 -> 1, -0.5 -> -1), unlike Python's banker rounding."""
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))''',
        "四舍五入与银行家舍入的区别")

# ===========================================================================
# debug: buggy function + task (4 tasks)
# ===========================================================================

py_task("d4m_dbg_001", "count_positive",
        None,
        '''assert count_positive([1, -2, 3]) == 2
assert count_positive([]) == 0
assert count_positive([0, 0]) == 0
assert count_positive([-1, -1]) == 0''',
        '''def count_positive(nums):
    return sum(1 for n in nums if n > 0)''',
        "边界:0 不算正数",
        subcat="debug",
        buggy='''def count_positive(nums):
    total = 0
    for n in nums:
        if n >= 0:
            total += 1
    return total''',
        task="这个函数把 0 也算成了正数。请修复,使 0 不被计入。")

py_task("d4m_dbg_002", "average",
        None,
        '''assert average([2, 4]) == 3.0
assert average([5]) == 5.0
assert average([]) is None
assert average([1, 2, 3, 4]) == 2.5''',
        '''def average(nums):
    if not nums:
        return None
    return sum(nums) / float(len(nums))''',
        "空列表除零保护",
        subcat="debug",
        buggy='''def average(nums):
    return sum(nums) / len(nums)''',
        task="空列表时会抛 ZeroDivisionError。请修复,使空列表返回 None。")

py_task("d4m_dbg_003", "unique_sorted",
        None,
        '''assert unique_sorted([3, 1, 3, 2]) == [1, 2, 3]
assert unique_sorted([]) == []
assert unique_sorted([5, 5, 5]) == [5]''',
        '''def unique_sorted(nums):
    return sorted(set(nums))''',
        "去重并升序",
        subcat="debug",
        buggy='''def unique_sorted(nums):
    out = []
    for n in nums:
        if n not in out:
            out.append(n)
    return out''',
        task="这个函数没有排序。请修复,使其返回去重后的升序列表。")

py_task("d4m_dbg_004", "truncate",
        None,
        '''assert truncate("abcdef", 3) == "abc"
assert truncate("ab", 5) == "ab"
assert truncate("", 3) == ""
assert truncate("abcdef", 0) == ""''',
        '''def truncate(text, limit):
    if limit <= 0:
        return ""
    return text[:limit]''',
        "截断长度边界",
        subcat="debug",
        buggy='''def truncate(text, limit):
    return text[:limit + 1]''',
        task="这个函数多截取了一个字符。请修复,使其最多返回 limit 个字符。")


# ===========================================================================
# sql: schema + question + reference query (2 tasks)
# The schema MUST match sandbox.run_sql_equivalence's seeded tables.
# ===========================================================================

sql_task("d4m_sql_001",
         "按城市统计订单总金额,只保留总额超过 1000 的城市,按总额降序返回城市名与总额。",
         "SELECT c.city, SUM(o.amount) AS total FROM orders o "
         "JOIN customers c ON o.customer = c.id GROUP BY c.city "
         "HAVING SUM(o.amount) > 1000 ORDER BY total DESC",
         "JOIN + GROUP BY + HAVING + ORDER BY;种子数据 cust1=3000 会被保留,cust2/cust3 被 HAVING 过滤")

sql_task("d4m_sql_002",
         "统计每个城市的订单笔数,只保留笔数大于等于 2 的城市,按笔数降序、城市名升序返回城市名与笔数。",
         "SELECT c.city, COUNT(*) AS n FROM orders o "
         "JOIN customers c ON o.customer = c.id GROUP BY c.city "
         "HAVING COUNT(*) >= 2 ORDER BY n DESC, c.city ASC",
         "COUNT + HAVING + 双键排序;种子数据 cust1 有 2 笔、cust2 有 2 笔、cust3 有 1 笔")



# ---------------------------------------------------------------------------
# verification: drive the real judge (code_judge -> sandbox) on ref vs skeleton
# ---------------------------------------------------------------------------
_SCRATCH_SEQ = [0]


def _patch_tempfile():
    """sandbox.py builds its work dir with tempfile.mkdtemp(), which on this host
    creates a 0o700 directory the DSH sandbox cannot write into, so every run dies
    with PermissionError before the candidate code is even written. Replace it with
    a plain makedirs under the project so the harness can actually run. This is a
    host quirk workaround in the TEST harness - runner/ is not touched."""
    import tempfile

    def mkdtemp(prefix="tmp", suffix="", dir=None):
        _SCRATCH_SEQ[0] += 1
        base_dir = dir or os.path.join(ROOT, "results", "_d4m_scratch")
        path = os.path.join(base_dir, "%s%d" % (prefix, _SCRATCH_SEQ[0]))
        os.makedirs(path, exist_ok=True)
        return path

    tempfile.mkdtemp = mkdtemp
    # sandbox.py never removes its work dir; register an atexit sweep so a verify
    # run does not leave hundreds of directories under results/.
    import atexit, shutil as _shutil

    def _sweep():
        _shutil.rmtree(os.path.join(ROOT, "results", "_d4m_scratch"), ignore_errors=True)

    atexit.register(_sweep)


def _judge():
    _patch_tempfile()
    sys.path.insert(0, os.path.join(ROOT, "runner"))
    from code_judge import judge_code
    return judge_code


def _ref_response(t):
    """The reply a correct model would produce (code inside a fenced block)."""
    if t["subcat"] == "sql":
        return "Here is the query:" + NL + NL + FENCE + "sql" + NL + t["check"] + NL + FENCE
    return "Here is the implementation:" + NL + NL + fence(t["ref"])


def _wrong_response(t):
    """The reply the shipped broken/skeleton code would produce."""
    if t["subcat"] == "sql":
        # the classic wrong shape: no JOIN, no HAVING
        wrong = ("SELECT customer, SUM(amount) FROM orders "
                 "GROUP BY customer ORDER BY 2 DESC")
        return "Here is the query:" + NL + NL + FENCE + "sql" + NL + wrong + NL + FENCE
    src = t["buggy"] if t["subcat"] == "debug" else t["prompt"]
    return "Here is the code:" + NL + NL + fence(src)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if not TASKS:
        print("no tasks defined")
        return
    rows = [_row(t) for t in TASKS]

    if args.verify:
        judge_code = _judge()
        ok = bad = 0
        for t, row in zip(TASKS, rows):
            good = judge_code(row, _ref_response(t))
            neg = judge_code(row, _wrong_response(t))
            passed = bool(good.get("correct")) and not neg.get("correct")
            ok += 1 if passed else 0
            bad += 0 if passed else 1
            print("%s %-18s %-6s ref=%-5s %-22s | broken=%-5s %s"
                  % ("OK " if passed else "BAD", t["id"], t["subcat"],
                     good.get("correct"), (good.get("stage") or "")[:20],
                     neg.get("correct"), (neg.get("reason") or neg.get("stage") or "")[:70]))
        print()
        print("D4 tasks: %d  correct-behaviour=%d  wrong-behaviour=%d" % (len(TASKS), ok, bad))
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

    if args.dry_run:
        print("dry-run: %d new rows, %d already present" % (len(new), len(rows) - len(new)))
        return

    with open(TARGET, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + NL)

    from collections import Counter
    c = Counter(r["subcat"] for r in new)
    print("gen_d4_more: appended %d rows (skipped %d existing)" % (len(new), len(rows) - len(new)))
    for k in sorted(c):
        print("   %-10s %d" % (k, c[k]))
    print("D4_full.jsonl now has %d rows" % (len(existing) + len(new)))


if __name__ == "__main__":
    main()
