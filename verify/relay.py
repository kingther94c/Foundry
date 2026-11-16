"""A local model endpoint whose replies are written from outside: by an agent
that plays the model, seeing only what Foundry sent, or by a script.

    python verify/relay.py serve DIR PORT       the endpoint Foundry talks to
    python verify/relay.py next DIR [SECONDS]   wait for the next request; print what is new in it
    python verify/relay.py reply DIR N FILE     answer request N with the JSON in FILE

It speaks the three protocols Foundry has adapters for, streaming or not:
POST /v1/chat/completions (openai_compat), /v1/responses (responses) and
/v1/messages (anthropic_messages); GET /v1/models lists one model.

Every POST becomes DIR/<N>.request.json; the server waits until
DIR/<N>.response.json exists (sending SSE keepalive comments meanwhile) and
answers in the request's protocol. A reply file is
    {"content": "...", "tool_calls": [{"name": ..., "arguments": {...}}],
     "reasoning": "optional thinking summary"}
content or tool_calls may be empty, not both. A request repeated byte for byte
(a retry) waits on the same reply.

DIR/faults.json, written by the runner, injects failures by request number:
{"3": ["429", "drop"]} answers request 3's first attempt with a 429 and cuts
its second off mid-stream; the third is served normally. Kinds: 429, 500,
quota (429 insufficient_quota), overloaded (529 / overloaded_error), drop
(stream cut after part of the answer), stream_error (an error event part-way).

What the agent never sees but the runner reads: DIR/checks.jsonl, one line per
request, saying whether the opaque reasoning the relay emitted earlier came
back, and the request's settings.
"""

import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _requests(d):
    return sorted(int(p.name.split(".")[0]) for p in d.glob("*.request.json"))


def _protocol(path):
    if path.endswith("/chat/completions"):
        return "chat"
    if path.endswith("/responses"):
        return "responses"
    if path.endswith("/messages"):
        return "anthropic"
    return None


# --- normalising a request into one conversation view ----------------------

def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if not isinstance(part, dict):
                out.append(str(part))
            elif part.get("type") in ("text", "input_text", "output_text"):
                out.append(part.get("text", ""))
            elif part.get("type") in ("image", "image_url", "input_image"):
                out.append("[image]")
            elif "text" in part:
                out.append(str(part["text"]))
        return "\n".join(out)
    return "" if content is None else str(content)


def normalise(protocol, body):
    """[(kind, payload)], kinds: system, user, assistant, tool."""
    entries = []
    if protocol == "chat":
        for m in body.get("messages") or []:
            role = m.get("role")
            if role in ("system", "developer"):
                entries.append(["system", _text(m.get("content"))])
            elif role == "user":
                entries.append(["user", _text(m.get("content"))])
            elif role == "assistant":
                calls = [[(c.get("function") or {}).get("name"),
                          (c.get("function") or {}).get("arguments"), c.get("id")]
                         for c in m.get("tool_calls") or []]
                entries.append(["assistant", {"text": _text(m.get("content")), "calls": calls}])
            elif role == "tool":
                entries.append(["tool", {"id": m.get("tool_call_id"),
                                         "text": _text(m.get("content")), "error": None}])
    elif protocol == "responses":
        if body.get("instructions"):
            entries.append(["system", body["instructions"]])
        current = None
        for item in body.get("input") or []:
            kind = item.get("type")
            role = item.get("role")
            if kind in ("reasoning", "function_call") or (role == "assistant"):
                if current is None:
                    current = {"text": "", "calls": []}
                    entries.append(["assistant", current])
                if kind == "function_call":
                    current["calls"].append([item.get("name"), item.get("arguments"),
                                             item.get("call_id")])
                elif kind == "reasoning":
                    current.setdefault("reasoning_items", 0)
                    current["reasoning_items"] += 1
                else:
                    current["text"] += _text(item.get("content"))
                continue
            current = None
            if kind == "function_call_output":
                entries.append(["tool", {"id": item.get("call_id"),
                                         "text": _text(item.get("output")), "error": None}])
            elif role in ("system", "developer"):
                entries.append(["system", _text(item.get("content"))])
            elif role == "user":
                entries.append(["user", _text(item.get("content"))])
            else:
                entries.append(["user", json.dumps(item)[:2000]])
    elif protocol == "anthropic":
        system = body.get("system")
        if system:
            entries.append(["system", _text(system)])
        for m in body.get("messages") or []:
            content = m.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            if m.get("role") == "assistant":
                entry = {"text": "", "calls": []}
                for b in blocks:
                    if b.get("type") == "text":
                        entry["text"] += b.get("text", "")
                    elif b.get("type") == "tool_use":
                        entry["calls"].append([b.get("name"), json.dumps(b.get("input")),
                                               b.get("id")])
                    elif b.get("type") in ("thinking", "redacted_thinking"):
                        entry["reasoning_items"] = entry.get("reasoning_items", 0) + 1
                entries.append(["assistant", entry])
            else:
                texts = []
                for b in blocks:
                    if b.get("type") == "tool_result":
                        if texts:
                            entries.append(["user", "\n".join(texts)])
                            texts = []
                        entries.append(["tool", {"id": b.get("tool_use_id"),
                                                 "text": _text(b.get("content")),
                                                 "error": b.get("is_error")}])
                    else:
                        texts.append(_text([b]))
                if texts:
                    entries.append(["user", "\n".join(texts)])
    return entries


def tools_of(protocol, body):
    out = []
    for t in body.get("tools") or []:
        if protocol == "chat":
            f = t.get("function") or {}
            out.append((f.get("name"), f.get("description", ""), f.get("parameters")))
        elif protocol == "responses":
            out.append((t.get("name"), t.get("description", ""), t.get("parameters")))
        else:
            out.append((t.get("name"), t.get("description", ""), t.get("input_schema")))
    return out


_KNOWN = {"model", "messages", "input", "instructions", "system", "tools", "stream",
          "stream_options", "store", "include", "max_tokens", "metadata"}


def settings_of(body):
    return {k: v for k, v in body.items() if k not in _KNOWN}


# --- serving ---------------------------------------------------------------

def _pieces(text, n=3):
    if not text:
        return []
    size = max(1, len(text) // n + 1)
    return [text[i:i + size] for i in range(0, len(text), size)]


def _usage_numbers(raw, reply_bytes):
    return max(1, len(raw) // 4), max(1, reply_bytes // 4)


def _opaque_token(n, kind):
    return f"{kind}-{n}-" + hashlib.sha256(f"{kind}{n}".encode()).hexdigest()[:16]


def _check_replay(d, n, protocol, body, emitted):
    """Which opaque tokens this request sent back; written for the runner only."""
    raw = json.dumps(body)
    back = sorted(t for t in emitted if t in raw)
    record = {"n": n, "protocol": protocol, "opaque_back": back,
              "opaque_emitted_before": sorted(emitted),
              "include": body.get("include"), "reasoning": body.get("reasoning"),
              "thinking": body.get("thinking"), "stream": body.get("stream"),
              "settings": {k: (v if len(json.dumps(v)) < 300 else "...")
                           for k, v in settings_of(body).items()}}
    with open(d / "checks.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def make_server(d, port):
    """The relay for the directory ``d`` on 127.0.0.1:``port`` (0 takes a free
    port), not yet serving. What it counts -- the attempts at each request,
    the opaque tokens it gave out -- is this server's own."""
    d.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    attempts: dict[int, int] = {}
    emitted: dict[str, int] = {}  # opaque token -> the request that emitted it

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code, obj, headers=()):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"object": "list", "data": [
                {"id": "relay-model", "object": "model", "owned_by": "relay"}]})

        def _sse(self, protocol, event):
            if protocol == "anthropic":
                line = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            else:
                line = f"data: {json.dumps(event)}\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        def do_POST(self):
            protocol = _protocol(self.path.split("?")[0])
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if protocol is None:
                self._send(404, {"error": {"message": f"no such endpoint {self.path}"}})
                return
            body = json.loads(raw.decode("utf-8"))
            with lock:
                n = None
                existing = _requests(d)
                if existing and (d / f"{existing[-1]}.request.json").read_bytes() == raw:
                    n = existing[-1]
                if n is None:
                    n = (existing or [0])[-1] + 1
                    # The meta first, then the request whole: whoever waits for
                    # N.request.json reads it and N.meta.json the moment it
                    # appears, and written in place it could be seen empty,
                    # before its meta existed.
                    (d / f"{n}.meta.json").write_text(json.dumps(
                        {"protocol": protocol, "path": self.path,
                         "auth": "present" if (self.headers.get("Authorization")
                                               or self.headers.get("x-api-key")) else "missing"}),
                        encoding="utf-8")
                    staged = d / f"{n}.request.part"
                    staged.write_bytes(raw)
                    staged.replace(d / f"{n}.request.json")
                    _check_replay(d, n, protocol, body, emitted)
                attempts[n] = attempts.get(n, 0) + 1
                attempt = attempts[n]
            faults = {}
            fpath = d / "faults.json"
            if fpath.is_file():
                faults = json.loads(fpath.read_text(encoding="utf-8"))
            planned = faults.get(str(n)) or []
            fault = planned[attempt - 1] if attempt <= len(planned) else None
            with open(d / "attempts.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"n": n, "attempt": attempt, "fault": fault,
                                     "at": time.time()}) + "\n")
            if fault in ("429", "500", "quota", "overloaded"):
                self._fail(protocol, fault)
                return
            stream = bool(body.get("stream"))
            reply_path = d / f"{n}.response.json"
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                last = time.time()
                while not reply_path.is_file():
                    time.sleep(0.5)
                    if time.time() - last > 10:
                        try:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        except OSError:
                            return
                        last = time.time()
            else:
                while not reply_path.is_file():
                    time.sleep(0.5)
            time.sleep(0.2)
            reply = json.loads(reply_path.read_text(encoding="utf-8"))
            usage = _usage_numbers(raw, len(reply_path.read_bytes()))
            try:
                if stream:
                    getattr(self, f"_stream_{protocol}")(n, body, reply, usage, fault)
                else:
                    self._send(200, getattr(self, f"_single_{protocol}")(n, body, reply, usage))
            except (ConnectionError, OSError):
                pass

        def _fail(self, protocol, fault):
            if fault == "429":
                body = ({"type": "error", "error": {"type": "rate_limit_error",
                                                     "message": "relay: rate limited"}}
                        if protocol == "anthropic" else
                        {"error": {"type": "rate_limit_exceeded", "code": "rate_limit_exceeded",
                                   "message": "relay: rate limited"}})
                self._send(429, body, [("Retry-After", "1")])
            elif fault == "quota":
                self._send(429, {"error": {"type": "insufficient_quota",
                                           "code": "insufficient_quota",
                                           "message": "relay: you exceeded your quota"}})
            elif fault == "overloaded":
                self._send(529, {"type": "error", "error": {"type": "overloaded_error",
                                                             "message": "relay: overloaded"}})
            else:
                self._send(500, {"error": {"type": "server_error",
                                           "message": "relay: internal error"}})

        # chat completions -------------------------------------------------
        def _calls(self, n, reply):
            return [(f"call_{n}_{i}", c["name"], json.dumps(c.get("arguments") or {}))
                    for i, c in enumerate(reply.get("tool_calls") or [])]

        def _single_chat(self, n, body, reply, usage):
            calls = self._calls(n, reply)
            message = {"role": "assistant", "content": reply.get("content") or None}
            if reply.get("reasoning"):
                message["reasoning_content"] = reply["reasoning"]
            if calls:
                message["tool_calls"] = [{"id": i, "type": "function",
                                          "function": {"name": nm, "arguments": a}}
                                         for i, nm, a in calls]
            return {"id": f"relay-{n}", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": "tool_calls" if calls else "stop"}],
                    "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                              "total_tokens": sum(usage)}}

        def _stream_chat(self, n, body, reply, usage, fault):
            base = {"id": f"relay-{n}", "object": "chat.completion.chunk",
                    "model": body.get("model")}

            def chunk(delta, finish=None):
                return {**base, "choices": [{"index": 0, "delta": delta,
                                             "finish_reason": finish}]}
            events = [chunk({"role": "assistant"})]
            for p in _pieces(reply.get("reasoning") or ""):
                events.append(chunk({"reasoning_content": p}))
            for p in _pieces(reply.get("content") or ""):
                events.append(chunk({"content": p}))
            for i, (cid, name, args) in enumerate(self._calls(n, reply)):
                events.append(chunk({"tool_calls": [{"index": i, "id": cid, "type": "function",
                                                     "function": {"name": name,
                                                                  "arguments": ""}}]}))
                for p in _pieces(args, 2):
                    events.append(chunk({"tool_calls": [{"index": i,
                                                         "function": {"arguments": p}}]}))
            events.append(chunk({}, "tool_calls" if reply.get("tool_calls") else "stop"))
            events.append({**base, "choices": [], "usage": {
                "prompt_tokens": usage[0], "completion_tokens": usage[1],
                "total_tokens": sum(usage)}})
            self._emit("chat", events, fault, done_sentinel=True)

        def _emit(self, protocol, events, fault, done_sentinel=False):
            cut = (len(events) // 2 if fault in ("drop", "stream_error", "failed")
                   else None)
            for i, e in enumerate(events):
                if cut is not None and i == max(cut, 1):
                    if fault in ("stream_error", "failed"):
                        if protocol == "anthropic":
                            err = {"type": "error", "error": {"type": "overloaded_error",
                                                              "message": "relay: overloaded"}}
                        elif protocol == "chat":
                            # OpenRouter's shape: an error beside a finish_reason
                            # of "error", and the stream then closes normally.
                            err = {"id": e.get("id"), "object": "chat.completion.chunk",
                                   "model": e.get("model"),
                                   "error": {"code": "server_error",
                                             "message": "relay: stream error"},
                                   "choices": [{"index": 0, "delta": {"content": ""},
                                                "finish_reason": "error"}]}
                        elif fault == "failed":
                            err = {"type": "response.failed", "response": {
                                "id": "resp_failed", "status": "failed", "output": [],
                                "error": {"code": "server_error",
                                          "message": "relay: the server had an error"}}}
                        else:
                            # The Responses API's own error event: code and message
                            # at the top level.
                            err = {"type": "error", "code": "server_error",
                                   "message": "relay: stream error", "param": None}
                        self._sse(protocol, err)
                        if protocol == "chat":
                            self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return  # connection closes here
                self._sse(protocol, e)
            if done_sentinel:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        # responses ---------------------------------------------------------
        def _responses_output(self, n, body, reply):
            output = []
            if reply.get("reasoning"):
                item = {"type": "reasoning", "id": f"rs_{n}",
                        "summary": [{"type": "summary_text", "text": reply["reasoning"]}]}
                if "reasoning.encrypted_content" in (body.get("include") or []):
                    token = _opaque_token(n, "enc")
                    emitted[token] = n
                    item["encrypted_content"] = token
                output.append(item)
            if reply.get("content"):
                output.append({"type": "message", "id": f"msg_{n}", "role": "assistant",
                               "status": "completed",
                               "content": [{"type": "output_text", "text": reply["content"],
                                            "annotations": []}]})
            for cid, name, args in self._calls(n, reply):
                output.append({"type": "function_call", "id": f"fc_{cid}", "call_id": cid,
                               "name": name, "arguments": args, "status": "completed"})
            return output

        def _responses_obj(self, n, body, output, usage, status):
            return {"id": f"resp_{n}", "object": "response", "status": status,
                    "model": body.get("model"), "output": output,
                    "usage": {"input_tokens": usage[0], "output_tokens": usage[1],
                              "total_tokens": sum(usage),
                              "input_tokens_details": {"cached_tokens": 0},
                              "output_tokens_details": {"reasoning_tokens": 0}}}

        def _single_responses(self, n, body, reply, usage):
            return self._responses_obj(n, body, self._responses_output(n, body, reply),
                                       usage, "completed")

        def _stream_responses(self, n, body, reply, usage, fault):
            output = self._responses_output(n, body, reply)
            events = [{"type": "response.created",
                       "response": self._responses_obj(n, body, [], (0, 0), "in_progress")}]
            for idx, item in enumerate(output):
                if item["type"] == "reasoning":
                    events.append({"type": "response.output_item.added", "output_index": idx,
                                   "item": {"type": "reasoning", "id": item["id"],
                                            "summary": []}})
                elif item["type"] == "message":
                    events.append({"type": "response.output_item.added", "output_index": idx,
                                   "item": {**item, "content": [], "status": "in_progress"}})
                    for p in _pieces(item["content"][0]["text"]):
                        events.append({"type": "response.output_text.delta",
                                       "output_index": idx, "content_index": 0,
                                       "item_id": item["id"], "delta": p})
                else:
                    events.append({"type": "response.output_item.added", "output_index": idx,
                                   "item": {**item, "arguments": "", "status": "in_progress"}})
                    for p in _pieces(item["arguments"], 2):
                        events.append({"type": "response.function_call_arguments.delta",
                                       "output_index": idx, "item_id": item["id"],
                                       "delta": p})
                events.append({"type": "response.output_item.done", "output_index": idx,
                               "item": item})
            events.append({"type": "response.completed",
                           "response": self._responses_obj(n, body, output, usage,
                                                           "completed")})
            self._emit("responses", events, fault)

        # anthropic messages ------------------------------------------------
        def _anthropic_blocks(self, n, body, reply):
            blocks = []
            if reply.get("reasoning") and body.get("thinking"):
                token = _opaque_token(n, "sig")
                emitted[token] = n
                blocks.append({"type": "thinking", "thinking": reply["reasoning"],
                               "signature": token})
            if reply.get("content"):
                blocks.append({"type": "text", "text": reply["content"]})
            for cid, name, args in self._calls(n, reply):
                blocks.append({"type": "tool_use", "id": "toolu_" + cid, "name": name,
                               "input": json.loads(args)})
            return blocks

        def _anthropic_usage(self, usage):
            return {"input_tokens": usage[0], "output_tokens": usage[1],
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

        def _single_anthropic(self, n, body, reply, usage):
            blocks = self._anthropic_blocks(n, body, reply)
            return {"id": f"msg_{n}", "type": "message", "role": "assistant",
                    "model": body.get("model"), "content": blocks,
                    "stop_reason": "tool_use" if reply.get("tool_calls") else "end_turn",
                    "stop_sequence": None, "usage": self._anthropic_usage(usage)}

        def _stream_anthropic(self, n, body, reply, usage, fault):
            blocks = self._anthropic_blocks(n, body, reply)
            events = [{"type": "message_start", "message": {
                "id": f"msg_{n}", "type": "message", "role": "assistant",
                "model": body.get("model"), "content": [], "stop_reason": None,
                "usage": {**self._anthropic_usage((usage[0], 1))}}},
                {"type": "ping"}]
            for i, b in enumerate(blocks):
                if b["type"] == "thinking":
                    events.append({"type": "content_block_start", "index": i,
                                   "content_block": {"type": "thinking", "thinking": "",
                                                     "signature": ""}})
                    for p in _pieces(b["thinking"]):
                        events.append({"type": "content_block_delta", "index": i,
                                       "delta": {"type": "thinking_delta", "thinking": p}})
                    events.append({"type": "content_block_delta", "index": i,
                                   "delta": {"type": "signature_delta",
                                             "signature": b["signature"]}})
                elif b["type"] == "text":
                    events.append({"type": "content_block_start", "index": i,
                                   "content_block": {"type": "text", "text": ""}})
                    for p in _pieces(b["text"]):
                        events.append({"type": "content_block_delta", "index": i,
                                       "delta": {"type": "text_delta", "text": p}})
                else:
                    events.append({"type": "content_block_start", "index": i,
                                   "content_block": {"type": "tool_use", "id": b["id"],
                                                     "name": b["name"], "input": {}}})
                    for p in _pieces(json.dumps(b["input"]), 2):
                        events.append({"type": "content_block_delta", "index": i,
                                       "delta": {"type": "input_json_delta",
                                                 "partial_json": p}})
                events.append({"type": "content_block_stop", "index": i})
            events.append({"type": "message_delta",
                           "delta": {"stop_reason": "tool_use" if reply.get("tool_calls")
                                     else "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": usage[1]}})
            events.append({"type": "message_stop"})
            self._emit("anthropic", events, fault)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(d, port):
    make_server(d, port).serve_forever()


# --- what the agent sees ---------------------------------------------------

def _render_entry(kind, payload):
    if kind == "system":
        return f"### system / instructions\n{payload}"
    if kind == "user":
        return f"### user\n{payload}"
    if kind == "tool":
        flag = " (is_error)" if payload.get("error") else ""
        return f"### tool result for {payload.get('id')}{flag}\n{payload.get('text')}"
    lines = ["### assistant (you, earlier)"]
    if payload.get("reasoning_items"):
        lines.append(f"[your earlier reasoning, {payload['reasoning_items']} item(s), "
                     "sent back opaquely]")
    if payload.get("text"):
        lines.append(payload["text"])
    for name, args, cid in payload.get("calls") or []:
        lines.append(f"  -> called {name} {args}   [{cid}]")
    return "\n".join(lines)


def _load(d, n):
    body = json.loads((d / f"{n}.request.json").read_text(encoding="utf-8"))
    meta = json.loads((d / f"{n}.meta.json").read_text(encoding="utf-8"))
    return meta["protocol"], body


def _render(d, n):
    protocol, body = _load(d, n)
    entries = normalise(protocol, body)
    tools = tools_of(protocol, body)
    base = None
    for k in range(n - 1, max(0, n - 30), -1):
        try:
            p2, b2 = _load(d, k)
        except FileNotFoundError:
            continue
        e2 = normalise(p2, b2)
        if len(e2) < len(entries) and entries[:len(e2)] == e2:
            base = (k, e2, tools_of(p2, b2))
            break
    parts = [f"===== request {n} ====="]
    settings = settings_of(body)
    if base is None:
        parts.append("(a new conversation, or one rewritten since the last request: "
                     "the whole of it follows)")
        start = 0
    else:
        k, e2, t2 = base
        start = len(e2)
        parts.append(f"(continues request {k}; only what is new since then follows)")
        if start < len(entries) and entries[start][0] == "assistant":
            parts.append("(your reply to request %d is recorded as the next assistant turn)" % k)
            parts.append(_render_entry(*entries[start]))
            start += 1
    if base is None or tools != base[2]:
        if tools:
            parts.append("## tools you can call")
            for name, desc, params in tools:
                parts.append(f"- {name}: {desc}\n  parameters: "
                             + json.dumps(params, separators=(",", ":")))
        else:
            parts.append("## no tools are offered in this request: answer in text")
    if settings:
        parts.append("## request settings\n" + json.dumps(settings, indent=1)[:4000])
    for kind, payload in entries[start:]:
        parts.append(_render_entry(kind, payload))
    parts.append(f"===== reply with: python verify/relay.py reply <dir> {n} <file> =====")
    return "\n\n".join(parts)


def next_request(d, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        for n in _requests(d):
            if not (d / f"{n}.response.json").is_file():
                view = d / f"{n}.view.txt"
                if not view.is_file():
                    view.write_text(_render(d, n), encoding="utf-8")
                text = view.read_text(encoding="utf-8")
                if len(text) <= 20000:
                    print(text)
                else:
                    print(text[:18000])
                    print(f"\n[... this request is {len(text)} characters long; the whole of "
                          f"it is in {view} -- read that file before replying ...]")
                return 0
        if (d / "DONE").is_file():
            print("SESSION OVER: the client has exited. Stop.")
            return 0
        time.sleep(1)
    print("no request yet; run `next` again")
    return 0


def _inline(value, base):
    """{"$file": "name"} anywhere in the reply becomes that file's text (beside the reply)."""
    if isinstance(value, dict):
        if set(value) == {"$file"}:
            return (base / value["$file"]).read_text(encoding="utf-8")
        return {k: _inline(v, base) for k, v in value.items()}
    if isinstance(value, list):
        return [_inline(v, base) for v in value]
    return value


def reply(d, n, file):
    file = Path(file)
    data = _inline(json.loads(file.read_text(encoding="utf-8")), file.parent)
    content = data.get("content") or ""
    calls = data.get("tool_calls") or []
    reasoning = data.get("reasoning") or ""
    if not isinstance(content, str) or not isinstance(calls, list) \
            or not isinstance(reasoning, str):
        raise SystemExit("content and reasoning must be strings and tool_calls a list")
    protocol, body = _load(d, n)
    offered = {t[0] for t in tools_of(protocol, body)}
    for c in calls:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str):
            raise SystemExit("each tool call needs a name")
        if not isinstance(c.get("arguments", {}), dict):
            raise SystemExit("arguments must be a JSON object")
        if c["name"] not in offered:
            raise SystemExit(f"{c['name']} is not among the tools this request offers: "
                             f"{sorted(offered)}")
    if not content and not calls:
        raise SystemExit("a reply needs content or tool calls")
    target = d / f"{n}.response.json"
    if target.exists():
        raise SystemExit(f"request {n} is already answered")
    tmp = d / f"{n}.response.tmp"
    tmp.write_text(json.dumps({"content": content, "tool_calls": calls,
                               "reasoning": reasoning}), encoding="utf-8")
    tmp.replace(target)
    print(f"answered request {n}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, d = sys.argv[1], Path(sys.argv[2])
    if cmd == "serve":
        serve(d, int(sys.argv[3]))
    elif cmd == "next":
        sys.exit(next_request(d, float(sys.argv[3]) if len(sys.argv) > 3 else 540))
    elif cmd == "reply":
        sys.exit(reply(d, int(sys.argv[3]), sys.argv[4]))
