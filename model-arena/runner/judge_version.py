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
SUITE_FILES = {
    "lcb": ["lcb_judge.py"],
    "public": ["public_judge.py", "ifeval_judge.py", "lcb_judge.py", "aliases.py"],
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
        return _hash(CORE_FILES + SUITE_FILES[suite])
    return _hash(CORE_FILES)


def all_versions():
    out = {"core": judge_version()}
    for s in SUITE_FILES:
        out[s] = judge_version(s)
    return out