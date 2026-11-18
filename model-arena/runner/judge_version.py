# -*- coding: utf-8 -*-
"""judge_version.py - stamp results with the judge version that produced them.

Why this exists: judges change, and a changed judge silently invalidates every
number computed before it. This session alone rewrote five judges (hallucination
length rule, refusal delivery rule, _has_any CJK matching, SQL reference answers,
D9 exec scoring). Without a version marker the leaderboard merges old and new
scores by timestamp, and the newest FILE wins even when it was scored by an older
judge - so the ranking reports numbers no current judge would produce.

TWO LEVELS, because one global hash over-reports the damage.

All judges used to share one hash, so adding lcb_judge.py (which only the `lcb`
suite can reach) changed the version seen by D2, D3, D7 - results that never
touch LCB. That flagged 6,671 already-valid questions as "must re-run" and would
have cost hours plus real API money to re-measure unchanged numbers. A version
that cries wolf is worse than no version: people learn to ignore it.

  CORE_FILES  every dimension depends on these. Editing one invalidates the
              whole leaderboard.
  SUITE_FILES a judge reachable by one suite only. Editing it invalidates that
              suite alone.

judge_version()          the core hash (unchanged meaning for D2..D10)
judge_version("lcb")     the hash a specific suite must be compared under
"""
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Files whose content defines what a score means, for EVERY dimension.
#
# checkers.py belongs here. It was missing, so the empty-checkers pass through
# run_checkers() changed D5 verdicts for 300 rows while judge_version() stayed
# byte-identical - the one control meant to catch a silent re-scoring could not
# see it. A judge file that is not hashed is a judge change that ships unflagged.
CORE_FILES = [
    "judge.py", "checkers.py", "reliability_judge.py", "code_judge.py",
    "d9_judge.py", "chess_judge.py", "longctx_judge.py", "d9_exec.py",
    "agent_judge.py",
    # compression.py judged D12 and was missing here: adding the project-code
    # requirement to judge_retention() changed D12 verdicts while the fingerprint
    # stayed byte-identical. Same class of blind spot as checkers.py.
    "compression.py",
]

# Files reachable by one suite only. Editing one of these must not invalidate
# results from a suite that cannot call it.
#
# d13_prose.py goes here rather than CORE_FILES on purpose: it is only reachable
# from suite_writing (D13), and D13 carries no weight in the index. Putting it in
# CORE_FILES would invalidate every D2..D12 result every time the prose word lists
# are tuned - which happens far more often than the core judges change.
# Third time this blind spot appeared (checkers.py, compression.py, now this):
# a judge file that is not hashed is a judge change that ships unflagged.
SUITE_FILES = {
    "lcb": ["lcb_judge.py"],
    "public": ["public_judge.py", "ifeval_judge.py", "lcb_judge.py", "aliases.py"],
    "writing": ["d13_prose.py"],
}

# 题库文件：**改题也是改版本**。
#
# 2026-11-19 实测的盲点：D13 的题从 31 道扩到 50 道（chat 7→20、长篇 6→12），
# 判分器一行没改，judge_version("writing") 前后都是同一个哈希。
# 于是「31 题的成绩」和「50 题的成绩」在版本机制看来完全可比 —— 但它们不可比：
# 分母不同、覆盖的场景不同，直接并列就是把两把不同的尺子画进同一张表。
#
# 只登记在 suite 级、不进 CORE_FILES：题库是按维度建的，改 D13 的题不该让
# D2..D12 的成绩失效。这与上面 SUITE_FILES 的设计意图一致 ——
# 一个动不动就报警的版本号比没有更糟。
SUITE_DATASETS = {
    # 相对 runner/ 的路径
    "writing": ["../datasets/full/D13_full.jsonl"],
}


def _hash(names):
    h = hashlib.sha256()
    for name in names:
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        h.update(name.encode("utf-8"))
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:12]


def judge_version(suite=None):
    """Core hash, or the hash a specific suite must be compared under.

    With no argument this returns the CORE hash only, so adding a suite-specific
    judge leaves the D2..D10 version untouched - which is the point.
    """
    if suite in SUITE_FILES:
        # 题库也算版本的一部分：改题和改判分器一样会让历史成绩不可比。
        files = CORE_FILES + SUITE_FILES[suite] + SUITE_DATASETS.get(suite, [])
        return _hash(files)
    return _hash(CORE_FILES)


def all_versions():
    out = {"core": judge_version()}
    for s in SUITE_FILES:
        out[s] = judge_version(s)
    return out