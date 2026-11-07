# -*- coding: utf-8 -*-
"""checkpoint.py - crash-safe resume for long evaluation runs.

Why: a full run can take hours (DeepSeek on GPQA took 2.9h / ~¥15). If the machine
reboots, sleeps, or the API stalls, the old runner lost everything because it only
wrote the result file at the very end. A laptop GPU-mode switch alone was enough to
threaten a 3-hour run.

Design:
  * append-only JSONL, one flushed line per finished question, so an abrupt kill
    leaves a valid file up to the last completed question
  * keyed by (suite, endpoint, question-id-set hash), so a changed subset or a
    different model never silently mixes results
  * deleted only after the final result file is written

Usage from run.py:
    ck = Checkpoint(suite=level, endpoint=ep.name, ids=[r["id"] for r in rows])
    done = ck.load()                 # {id: record}
    ...
    if qid not in done:  ... call the model, ck.add(rec) ...
    ck.clear()                       # only after the result file is saved
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CKPT_DIR = os.path.join(ROOT, "results", ".ckpt")


def _key(suite, endpoint, ids):
    """Stable key. Includes a hash of the exact question set so a different subset
    (or a regenerated dataset) can never resume on top of stale records."""
    h = hashlib.sha1(("\n".join(str(i) for i in ids)).encode("utf-8")).hexdigest()[:10]
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(endpoint))
    return "%s__%s__%s" % (suite, safe, h)


class Checkpoint:
    def __init__(self, suite, endpoint, ids):
        os.makedirs(CKPT_DIR, exist_ok=True)
        self.key = _key(suite, endpoint, ids)
        self.path = os.path.join(CKPT_DIR, self.key + ".jsonl")
        self._fh = None

    # ---------- read ----------
    def load(self):
        """Return {id: record} for questions already finished. Tolerates a torn
        final line (a kill mid-write), which is the whole point of append-only."""
        done = {}
        if not os.path.exists(self.path):
            return done
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue          # torn tail line: ignore, it is simply not done
                if rec.get("id"):
                    done[rec["id"]] = rec
        return done

    def info(self):
        """(path, n_done) for logging."""
        n = len(self.load())
        return self.path, n

    # ---------- write ----------
    def add(self, rec):
        if self._fh is None:
            self._fh = open(self.path, "a", encoding="utf-8")
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())   # survive an OS-level kill, not just python
        except Exception:
            pass

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def clear(self):
        self.close()
        try:
            os.remove(self.path)
        except OSError:
            pass

    # ---------- housekeeping ----------
    @staticmethod
    def list_all():
        if not os.path.isdir(CKPT_DIR):
            return []
        out = []
        for fn in sorted(os.listdir(CKPT_DIR)):
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(CKPT_DIR, fn)
            n = 0
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            n += 1
            except OSError:
                continue
            out.append((fn[:-6], n, os.path.getmtime(p)))
        return out
