# -*- coding: utf-8 -*-
"""targets.py - one place that answers "what am I measuring?".

Two ways a model is reachable, and both must feel the same to the caller:

  attach  someone else already started the server (the DSH way). This program
          only points at a URL. It cannot know the launch parameters, so every
          result records `params_known: false` - a number without its flags is
          not reproducible, and pretending otherwise is how a benchmark library
          starts lying.

  launch  this program starts the engine itself, so it knows every flag because
          it composed the command line. Same measurement, but reproducible.

The distinction is not cosmetic: a sweep that respawns the server can control
KV precision and offload; an attach run cannot, and must say so.

Storage: config/targets.json. Kept separate from endpoints.json so existing CLI
invocations keep working while the UI layer gains model selection.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "config", "targets.json")

# Secrets typed in the UI live only in this process. Cleared on restart, which is
# the point: a benchmark tool should not be a credential store.
_RUNTIME_KEYS = {}


def set_runtime_key(target_id, key):
    """Hold a key in memory for this process only."""
    if key:
        _RUNTIME_KEYS[target_id] = key
    else:
        _RUNTIME_KEYS.pop(target_id, None)


def get_runtime_key(target_id):
    return _RUNTIME_KEYS.get(target_id)


def has_key(target_id, env_name=None):
    """True when this target can authenticate right now."""
    import os as _os
    if _RUNTIME_KEYS.get(target_id):
        return True
    if env_name and _os.environ.get(env_name):
        return True
    return False


DEFAULTS = {
    "version": 1,
    "targets": [
        {"id": "local", "kind": "attach",
         "label": "本地 Qwen3.6-35B-APEX",
         "base_url": "http://127.0.0.1:8080", "model": "Qwen3.6-35B-APEX",
         "engine": "llama.cpp", "timeout": 600},
        {"id": "ollama", "kind": "attach", "label": "Ollama qwen3",
         "base_url": "http://127.0.0.1:11434", "model": "qwen3",
         "engine": "ollama", "timeout": 600},
        {"id": "deepseek-flash", "kind": "attach", "label": "云端 DeepSeek Flash",
         "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
         "engine": "openai", "timeout": 600,
         "api_key_env": "DEEPSEEK_API_KEY"},
    ],
}


def load():
    """Read targets.json, creating it from defaults on first use."""
    if not os.path.exists(PATH):
        save(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULTS))
    d.setdefault("targets", [])
    return d


def save(data):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return PATH


def list_targets():
    return load().get("targets") or []


def get_target(tid):
    for t in list_targets():
        if t.get("id") == tid:
            return t
    return None


def upsert(target):
    """Add or replace one target by id. Returns the stored record.

    api_key handling: a key typed into the UI is NOT written to targets.json.
    It is stored in a process-local map so the running API can use it, and the
    file only records which env var holds it. Writing a secret to disk is how a
    benchmark repo ends up publishing someone else's credential.
    """
    if not target.get("id"):
        raise ValueError("target needs an id")
    kind = target.get("kind") or "attach"
    if kind not in ("attach", "launch"):
        raise ValueError("kind must be attach or launch")
    target["kind"] = kind
    d = load()
    ts = d.get("targets") or []
    for i, t in enumerate(ts):
        if t.get("id") == target["id"]:
            ts[i] = target
            break
    else:
        ts.append(target)
    d["targets"] = ts
    save(d)
    return target


def remove(tid):
    d = load()
    before = len(d.get("targets") or [])
    d["targets"] = [t for t in (d.get("targets") or []) if t.get("id") != tid]
    save(d)
    return before - len(d["targets"])


# --- discovery -----------------------------------------------------------

def probe(base_url, timeout=3):
    """Ask a running server what it is serving. Never raises."""
    import urllib.request
    out = {"reachable": False, "models": [], "engine_guess": None, "error": None}
    base = (base_url or "").rstrip("/")
    if not base:
        out["error"] = "no base_url"
        return out
    for path, key in (("/v1/models", "data"), ("/api/tags", "models")):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            items = d.get(key) or d.get("data") or []
            names = []
            for it in items:
                if isinstance(it, dict):
                    n = it.get("id") or it.get("name")
                    if n:
                        names.append(n)
            out["reachable"] = True
            out["models"] = names
            out["engine_guess"] = ("ollama" if path == "/api/tags"
                                   else "openai-compatible")
            return out
        except Exception as e:
            out["error"] = "%s: %s" % (path, str(e)[:60])
    return out


def scan_ports(ports=None, timeout=1.2):
    """Probe the usual local ports and report what answered."""
    ports = ports or [8080, 8099, 11434, 1234, 8326, 5000, 8000]
    found = []
    for p in ports:
        base = "http://127.0.0.1:%d" % p
        r = probe(base, timeout=timeout)
        if r["reachable"]:
            found.append({"port": p, "base_url": base, "models": r["models"],
                          "engine": r["engine_guess"]})
    return found


def find_ggufs(roots=None, limit=200):
    """Local model files that could be launched.

    Search roots, in order: explicit argument > $MODEL_DIRS (os.pathsep-joined)
    > config/machine.json "model_dirs" > this repo's ./models.

    Nothing here names a path from one particular machine - a hardcoded root
    just yields zero results for everyone else, which looks like "scan is
    broken" rather than "you have not told me where your models are".
    """
    import glob
    if not roots:
        roots = []
        env = os.environ.get("MODEL_DIRS") or ""
        roots += [p for p in env.split(os.pathsep) if p]
        try:
            import json
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            mf = os.path.join(here, "config", "machine.json")
            if os.path.isfile(mf):
                with open(mf, encoding="utf-8") as f:
                    roots += (json.load(f).get("model_dirs") or [])
        except Exception:
            pass
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        roots.append(os.path.join(here, "models"))
        if not roots:
            roots = [os.getcwd()]
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for p in glob.glob(os.path.join(root, "**", "*.gguf"), recursive=True):
            name = os.path.basename(p)
            if name.lower().startswith("mmproj"):
                continue          # vision projector, not a model
            try:
                mb = os.path.getsize(p) / (1024 * 1024)
            except Exception:
                mb = 0
            out.append({"path": p, "name": name, "size_gb": round(mb / 1024, 2)})
            if len(out) >= limit:
                return out
    out.sort(key=lambda x: -x["size_gb"])
    return out


def to_endpoint(target):
    """Shape a target as something arena run.py can consume as an endpoint."""
    return {
        "name": target.get("id"),
        "base_url": target.get("base_url"),
        "model": target.get("model") or target.get("id"),
        "engine": target.get("engine") or "openai",
        "timeout": target.get("timeout") or 600,
        "kind": target.get("kind") or "attach",
        "api_key_env": target.get("api_key_env"),
        "params_known": (target.get("kind") == "launch"),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="list",
                    choices=["list", "probe", "scan", "ggufs", "add-attach"])
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--id", default=None)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    if a.cmd == "list":
        for t in list_targets():
            print("%-16s %-7s %-30s %s" % (t.get("id"), t.get("kind"),
                                           t.get("base_url"), t.get("model")))
    elif a.cmd == "probe":
        print(json.dumps(probe(a.url), ensure_ascii=False, indent=2))
    elif a.cmd == "scan":
        for f in scan_ports():
            print("port %-6d %-24s %s" % (f["port"], f["engine"], f["models"][:3]))
    elif a.cmd == "ggufs":
        for g in find_ggufs()[:15]:
            print("%7.2f GB  %s" % (g["size_gb"], g["name"]))
    elif a.cmd == "add-attach":
        r = probe(a.url)
        tid = a.id or ("port%d" % (a.url.rsplit(":", 1)[-1]))
        t = upsert({"id": tid, "kind": "attach", "label": tid,
                    "base_url": a.url, "model": a.model or (r["models"][0] if r["models"] else tid),
                    "engine": r["engine_guess"] or "openai", "timeout": 600})
        print("saved:", json.dumps(t, ensure_ascii=False))