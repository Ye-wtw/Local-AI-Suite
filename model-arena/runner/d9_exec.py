# -*- coding: utf-8 -*-
"""d9_exec.py - run a D9 task that carries an `exec` block.

A D9 exec question ships a tiny broken repo (files), a command to run (run), and
the exit code / stdout marker that means "fixed". The model's answer is code, so
scoring it by regex is meaningless: the only honest check is to write the
candidate files out and actually run the command.

Why this module exists separately from sandbox.py: sandbox.py uses
tempfile.mkdtemp(), which creates a 0o700 directory that the DSH sandbox cannot
write into (reproduced on this host as PermissionError). Work dirs here are
created with makedirs under a caller-supplied root instead.

Nothing here calls a model. Callers pass in the already-extracted file contents.
"""
import os
import shutil
import subprocess

DEFAULT_TIMEOUT = 60


def extract_code_blocks(text):
    """Pull fenced code blocks out of a model reply.

    Returns a list of (lang, body). Models fence python as python/py/python3 and
    sometimes with no language at all, so all three are accepted.
    """
    blocks = []
    lines = (text or "").split(chr(10))
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("```"):
            lang = s[3:].strip().lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append((lang, chr(10).join(body)))
        i += 1
    return blocks


def pick_file_content(response_text, fix_file):
    """Choose which code block is the fix.

    Preference order:
      1. A block whose language matches the target file's extension
      2. The largest block (a fix is bigger than a quoted fragment)
      3. The whole reply, if it contains no fences at all
    """
    ext = os.path.splitext(fix_file or "")[1].lower()
    want = {".py": ("python", "py", "python3", ""),
            ".sh": ("bash", "sh", "shell", ""),
            ".json": ("json", ""),
            ".toml": ("toml", ""),
            ".ini": ("ini", ""),
            ".sql": ("sql", ""),
            ".yaml": ("yaml", "yml", ""),
            ".yml": ("yaml", "yml", "")}.get(ext, ("",))
    blocks = extract_code_blocks(response_text)
    if not blocks:
        return (response_text or "").strip()
    matching = [b for lang, b in blocks if lang in want]
    if matching:
        return max(matching, key=len)
    return max((b for _, b in blocks), key=len)


def run_exec_task(task, response_text, work_root):
    """Materialise the task, apply the model's fix, run it. Returns a verdict dict.

    Never raises on a failed run: a crashed candidate is a failed answer, not a
    harness error. Only a broken work root is an error.
    """
    e = task.get("exec") or {}
    files = e.get("files") or {}
    fix_file = e.get("fix_file")
    cmd = e.get("run")
    expect_exit = int(e.get("expect_exit") or 0)
    expect_stdout = e.get("expect_stdout") or []
    if isinstance(expect_stdout, str):
        expect_stdout = [expect_stdout]
    timeout = int(e.get("timeout") or DEFAULT_TIMEOUT)

    if not files or not cmd:
        return {"correct": False, "reason": "exec block incomplete",
                "extracted": None, "exit_code": None, "stdout": ""}

    work = os.path.join(work_root, "d9_" + str(task.get("id") or "task"))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    try:
        for name, content in files.items():
            path = os.path.join(work, name.replace("/", os.sep))
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        applied = None
        if fix_file:
            applied = pick_file_content(response_text, fix_file)
            target = os.path.join(work, fix_file.replace("/", os.sep))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(applied)

        try:
            pr = subprocess.run(cmd, cwd=work, shell=True, capture_output=True,
                                text=True, timeout=timeout)
            code, out, err = pr.returncode, pr.stdout or "", pr.stderr or ""
        except subprocess.TimeoutExpired:
            return {"correct": False, "reason": "timeout after %ds" % timeout,
                    "extracted": applied, "exit_code": "timeout", "stdout": "",
                    "timeout": True}

        missing = [m for m in expect_stdout if m not in out]
        ok = (code == expect_exit) and not missing
        if ok:
            reason = "exit=%d, marker ok" % code
        elif code != expect_exit:
            # 120 chars cut every traceback off at "File ...", so the model got
            # the same uninformative feedback on every round and could not
            # converge. Give it the tail of stderr, where the actual assertion
            # lives, and fall back to stdout for a silent failure.
            detail = (err or out or "").strip()
            if len(detail) > 800:
                detail = "..." + detail[-800:]
            reason = "exit=%d (want %d); %s" % (code, expect_exit, detail)
        else:
            reason = "marker missing: %s" % missing
        return {"correct": ok, "reason": reason, "extracted": applied,
                "exit_code": code, "stdout": out[:2000], "stderr": err[:1000],
                "timeout": False}
    finally:
        shutil.rmtree(work, ignore_errors=True)
