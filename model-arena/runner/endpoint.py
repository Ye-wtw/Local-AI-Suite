# -*- coding: utf-8 -*-
"""endpoint.py - OpenAI-compatible endpoint client (stdlib only, no pip install needed).
Supports llama.cpp llama-server, Ollama (/v1), and cloud APIs (DeepSeek/OpenRouter/...).
Streaming support is required for TTFT / output tok/s measurement.
"""
import json, os, time, urllib.request, urllib.error

_UNSET = object()   # distinguishes "argument omitted" from an explicit None


class EndpointError(Exception):
    pass

class Endpoint:
    def __init__(self, name, base_url, model, api_key=None, timeout=300, extra=None,
                 cost=None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or "sk-local"
        self.timeout = timeout
        self.extra = extra or {}
        # "paid" / "free", or None to infer from the host shape. Declared here so a
        # paid gateway on a loopback tunnel or a LAN IP still triggers the cost
        # confirmation - and so nothing has to guess from the URL.
        self.cost = cost

    @property
    def url(self):
        if self.base_url.endswith("/v1"):
            return self.base_url + "/chat/completions"
        return self.base_url + "/v1/chat/completions"

    def _headers(self):
        return {"Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.api_key}

    def _payload(self, messages, stream, **kw):
        p = {"model": self.model, "messages": messages, "stream": stream}
        p.update(self.extra)
        p.update(kw)
        return p

    # ---------- non-streaming ----------
    @staticmethod
    def _guard_tools(kw):
        """Last line of defence: whatever code path built the request, tool specs must
        be in OpenAI shape with a valid JSON Schema before anything is sent.

        A bare {name, description, parameters} (BFCL's native shape) or a
        "type": "dict" makes llama-server answer HTTP 500, which silently zeroes an
        entire question set. Three separate code paths had that bypass, so the check
        lives at the transport layer where no caller can skip it.
        """
        tools = kw.get("tools")
        if not tools:
            return
        for t in tools:
            if not isinstance(t, dict) or t.get("type") != "function" or "function" not in t:
                raise EndpointError(
                    "tool spec is not wrapped for the OpenAI API: %s" % str(t)[:150])
            fn = t.get("function") or {}
            if not fn.get("name"):
                raise EndpointError("tool spec has no name: %s" % str(t)[:120])
            pt = (fn.get("parameters") or {}).get("type")
            if pt is not None and pt not in (
                    "object", "array", "string", "integer", "number", "boolean", "null"):
                raise EndpointError(
                    "invalid JSON-Schema type %r in tool %r" % (pt, fn.get("name")))

    # Per-question hard stop. A model that starts looping does not fail, it just
    # keeps generating until the context is full: one logic question here ran for
    # 13+ minutes and was still going, heading for ~1-2 hours at 131k. urlopen's
    # timeout only bounds a single read, so it never fires on a steady stream.
    # Measured on this box: median output 429 tokens, max 11126 (a GPQA question
    # that took 133s). DEFAULT_MAX_TOKENS sits well above that, so it never cuts a
    # legitimate answer - it only stops an unbounded loop.
    DEFAULT_TIMEOUT_S = 300
    DEFAULT_MAX_TOKENS = 16384

    def chat(self, messages, retries=2, deadline_s=_UNSET, max_tokens=_UNSET, **kw):
        """Return dict: {text, usage, latency_s, raw, timed_out}

        deadline_s is a WALL-CLOCK cap for the whole call (default 300s): the
        request is abandoned when it is exceeded, and the caller records a
        timeout instead of a wrong answer. max_tokens (default 16384) is the
        far cheaper guard against a runaway loop - set either to None to
        disable that particular limit.
        """
        self._guard_tools(kw)
        # _UNSET means "caller did not ask": apply the guard. An explicit None
        # means "caller wants it off": leave the server default alone.
        if deadline_s is _UNSET:
            deadline_s = getattr(self, "deadline_s", self.DEFAULT_TIMEOUT_S)
        if max_tokens is _UNSET:
            max_tokens = getattr(self, "max_tokens", self.DEFAULT_MAX_TOKENS)
        if max_tokens:
            kw = dict(kw)
            kw["max_tokens"] = max_tokens
        body = json.dumps(self._payload(messages, False, **kw)).encode("utf-8")
        last_err = None
        started = time.perf_counter()
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            # what is left of the wall-clock budget, so retries cannot stack past it
            left = None
            if deadline_s:
                left = deadline_s - (time.perf_counter() - started)
                if left <= 1:
                    raise EndpointError("deadline %.0fs exceeded before attempt %d"
                                        % (deadline_s, attempt + 1))
            req_timeout = self.timeout if left is None else min(self.timeout, left)
            try:
                req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
                with urllib.request.urlopen(req, timeout=req_timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                dt = time.perf_counter() - t0
                text = ""
                tool_calls = []
                if "choices" in data and data["choices"]:
                    msg = data["choices"][0].get("message", {})
                    text = msg.get("content") or msg.get("reasoning_content") or ""
                    tool_calls = msg.get("tool_calls") or []
                return {"text": text, "tool_calls": tool_calls,
                        "usage": data.get("usage", {}), "latency_s": dt, "raw": data}
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise EndpointError("chat failed after %d attempts: %s" % (retries + 1, last_err))

    def chat_vision(self, prompt, image_path, retries=1, deadline_s=_UNSET, max_tokens=_UNSET, **kw):
        """Send one image + a text prompt, in the OpenAI multimodal shape.

        llama.cpp accepts a base64 data URL in an image_url content part. The
        image is read and inlined here rather than referenced by path, because
        the server has no access to this machine's filesystem.
        """
        import base64
        if deadline_s is _UNSET:
            deadline_s = getattr(self, "deadline_s", self.DEFAULT_TIMEOUT_S)
        if max_tokens is _UNSET:
            max_tokens = getattr(self, "max_tokens", self.DEFAULT_MAX_TOKENS)
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
        if ext == "jpg":
            ext = "jpeg"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/%s;base64,%s" % (ext, b64)}},
        ]}]
        kw = dict(kw)
        if max_tokens:
            kw["max_tokens"] = max_tokens
        body = json.dumps(self._payload(messages, False, **kw)).encode("utf-8")
        started = time.perf_counter()
        last_err = None
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            left = None
            if deadline_s:
                left = deadline_s - (time.perf_counter() - started)
                if left <= 1:
                    raise EndpointError("deadline %.0fs exceeded (vision)" % deadline_s)
            req_timeout = self.timeout if left is None else min(self.timeout, left)
            try:
                req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
                with urllib.request.urlopen(req, timeout=req_timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                dt = time.perf_counter() - t0
                text = ""
                if "choices" in data and data["choices"]:
                    msg = data["choices"][0].get("message", {})
                    text = msg.get("content") or msg.get("reasoning_content") or ""
                return {"text": text, "usage": data.get("usage", {}),
                        "latency_s": dt, "raw": data}
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise EndpointError("vision chat failed after %d attempts: %s" % (retries + 1, last_err))

    # ---------- streaming (for speed metrics) ----------
    def chat_stream(self, messages, max_tokens=None, **kw):
        """Yield events. Final event is {'done': True, 'usage':..., 'metrics': {...}}.
        Each token event: {'text': delta, 't': perf_counter}
        """
        kw2 = dict(kw)
        if max_tokens:
            kw2["max_tokens"] = max_tokens
        # 请求服务在流末尾回报 usage(OpenAI 兼容):
        #   不请求它,很多引擎(含 ninfer)就不会给 completion_tokens,
        #   于是速度只能按字符算,得出的 tok/s 是假的。
        kw2.setdefault("stream_options", {"include_usage": True})
        body = json.dumps(self._payload(messages, True, **kw2)).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        t_request = time.perf_counter()
        t_first = None
        t_last = None
        first_chunk_chars = 0
        total_chars = 0
        usage = {}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                for raw_line in r:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for ch in chunk.get("choices", []):
                        delta = ch.get("delta", {}) or {}
                        # reasoning models stream their thinking in
                        # delta.reasoning_content and leave delta.content empty
                        # until the answer starts - and with a tight max_tokens
                        # the answer may never start. The blocking paths above
                        # already fall back to reasoning_content; this stream did
                        # not, so every reasoning-on model produced text_len=0
                        # with metrics=None while usage reported a full
                        # completion_tokens. Measured: D1 speed 'short' failed
                        # 3/3 with "no metrics" on a Qwen3.5 model that had
                        # generated 1000 tokens.
                        piece = delta.get("content") or ""
                        if not piece:
                            piece = delta.get("reasoning_content") or ""
                        if not piece:
                            continue
                        now = time.perf_counter()
                        if t_first is None:
                            t_first = now
                            first_chunk_chars = len(piece)
                        t_last = now
                        total_chars += len(piece)
                        yield {"text": piece, "t": now}
        except Exception as e:
            raise EndpointError("stream failed: %s" % e)
        if t_first is None:
            yield {"done": True, "usage": usage, "metrics": None, "error": "no content received"}
            return
        total_tokens = usage.get("completion_tokens") or 0
        # AA formula pieces
        ttft = t_first - t_request
        span = max(t_last - t_first, 1e-9)
        # 速度只在服务回报了 completion_tokens 时才算 tok/s。
        #
        # 原来这里有个静默回退：拿不到 token 数时用**字符数**当 token 数,
        # 算出一个"tok/s"。这个数是假的 —— 中英混排下 1 token 可能是 1~4 个字符,
        # 结果会虚高好几倍(实测 KV17 因此报出 439 tok/s,而它不可能这么高)。
        # 现在改成:拿不到 token 数就不给 tok/s,改报 chars/s,并且打标记,
        # 让上层能区分"真的 tok/s"和"只是字符吞吐"。
        if total_tokens and total_tokens > 1:
            out_tps = (total_tokens - 1) / span
            unit = "tok/s"
            exact = True
        else:
            out_tps = (total_chars - first_chunk_chars) / span if span > 0 else 0.0
            unit = "chars/s"
            exact = False
        metrics = {
            "ttft_s": ttft,
            "output_tps": out_tps,
            "output_unit": unit,
            "output_exact": exact,
            "e2e_500_s": ttft + (500.0 / out_tps if out_tps > 0 else float("inf")),
            "total_tokens": total_tokens,
            "stream_span_s": span,
            "total_s": time.perf_counter() - t_request,
        }
        yield {"done": True, "usage": usage, "metrics": metrics}

    def chat_stream_text(self, messages, **kw):
        """Convenience: collect streamed text + metrics."""
        parts = []
        metrics = None
        usage = {}
        for ev in self.chat_stream(messages, **kw):
            if ev.get("done"):
                metrics = ev.get("metrics")
                usage = ev.get("usage") or {}
            else:
                parts.append(ev["text"])
        return {"text": "".join(parts), "metrics": metrics, "usage": usage}

    def health(self):
        """Quick reachability probe. Returns (ok, detail)."""
        try:
            url = self.base_url + "/v1/models"
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            ids = [m.get("id") for m in data.get("data", [])]
            return True, "models: %s" % (", ".join(ids[:5]) or "none listed")
        except Exception as e:
            return False, str(e)


def load_endpoints(path):
    """Load config/endpoints.json -> {name: Endpoint}"""
    import os
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    out = {}
    for name, c in cfg.get("endpoints", {}).items():
        # API key resolution order: api_key_env > api_key.
        # Use api_key_env so the secret never lands in the config file.
        key = c.get("api_key")
        env_name = c.get("api_key_env")
        if env_name:
            key = os.environ.get(env_name) or key
        out[name] = Endpoint(name=name,
                             base_url=c["base_url"],
                             model=c.get("model", "local-model"),
                             api_key=key,
                             timeout=c.get("timeout", 300),
                             extra=c.get("extra"),
                             # "paid" / "free" / None. Passed through so the cost
                             # gate can trust the profile instead of guessing from
                             # the URL - a paid gateway on 127.0.0.1 looks local.
                             cost=c.get("cost"))
    return out, cfg
