"""A gateway on loopback that answers in each protocol Foundry speaks (D-205).

It answers what the checkup's protocol level asks -- a word, the numbers to
40 or to 300, a call of the add tool, the turn after its result -- as the
three APIs shape it, streamed or once, and it lists its models. A request
that offers ``finish`` is a session's: it is answered as a model that does
the checkup's task would (D-206) -- a read, a patch, the tests, finish
citing them.
Each fault a real gateway has been seen to have, or could have, is a switch:

* ``drop_tools``: the tools are taken out of the request.
* ``buffer``: a stream is held back and sent whole, a second later.
* ``no_sse``: a stream is asked for and a single answer given.
* ``ignore_ceiling``: the ceiling on the reply is not kept.
* ``hide_cut``: the reply is cut at the ceiling and said to be whole.
* ``mangle_args``: a call's arguments are a string, not an object.
* ``reject_tool_result``: the turn after a tool's result is refused.
* ``any_model``: a model it does not have is answered by one it has.
* ``post401``: every turn is refused HTTP 401; the model list is not.
* ``post500``: every turn fails HTTP 500.
* ``echo_auth``: the one-word answer quotes the credential it was sent.

And for the task, how the model goes about it:

* ``solver_wrong``: it patches the README, not the module.
* ``solver_tests``: it changes the tests to fit the module.
* ``solver_quiet``: it ends with a word instead of finish.

``speaks`` names the protocols it answers ("chat", "resp", "anth"); a turn in
another is answered HTTP 404, as at an address that does not exist.
"""

from __future__ import annotations

import http.server
import json
import re
import time

MODEL = "gpt-test"
MODELS = json.dumps({"data": [{"id": MODEL}, {"id": "other"}]}).encode()
ALL = ("chat", "resp", "anth")
_PATHS = {"/chat/completions": "chat", "/responses": "resp", "/messages": "anth"}


def _protocol(path: str) -> str | None:
    path = path.split("?")[0]
    for end, protocol in _PATHS.items():
        if path.endswith(end):
            return protocol
    return None


def _ceiling(protocol: str, body: dict) -> int | None:
    key = {"chat": "max_completion_tokens", "resp": "max_output_tokens",
           "anth": "max_tokens"}[protocol]
    return body.get(key)


def _continuing(protocol: str, body: dict) -> bool:
    if protocol == "chat":
        return body["messages"][-1].get("role") == "tool"
    if protocol == "resp":
        return body["input"][-1].get("type") == "function_call_output"
    content = body["messages"][-1].get("content")
    return isinstance(content, list) and any(part.get("type") == "tool_result"
                                             for part in content)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or ())


def _result(protocol: str, body: dict) -> str:
    """The tool's result the turn after a call carries."""
    if protocol == "chat":
        return _text_of(body["messages"][-1].get("content"))
    if protocol == "resp":
        return _text_of(body["input"][-1].get("output"))
    return "".join(_text_of(part.get("content")) for part in body["messages"][-1]["content"]
                   if part.get("type") == "tool_result")


# The task's patches, as a model would write them.
_FIX = ("*** Begin Patch\n*** Update File: interval.py\n<<<<<<< SEARCH\n"
        "    return max(0, end - start)\n=======\n    return max(0, end - start + 1)\n"
        ">>>>>>> REPLACE\n*** End Patch")
_README = ("*** Begin Patch\n*** Update File: README.md\n<<<<<<< SEARCH\n# interval\n=======\n"
           "# interval, fixed\n>>>>>>> REPLACE\n*** End Patch")
_TESTS = ("*** Begin Patch\n*** Update File: test_interval.py\n<<<<<<< SEARCH\n"
          "        self.assertEqual(overlap((1, 10), (3, 4)), 2)\n=======\n"
          "        self.assertEqual(overlap((1, 10), (3, 4)), 1)\n>>>>>>> REPLACE\n"
          "<<<<<<< SEARCH\n        self.assertEqual(overlap((1, 3), (3, 6)), 1)\n=======\n"
          "        self.assertEqual(overlap((1, 3), (3, 6)), 0)\n>>>>>>> REPLACE\n"
          "*** End Patch")


def _tool_names(protocol: str, body: dict) -> set[str]:
    tools = body.get("tools") or []
    if protocol == "chat":
        return {tool.get("function", {}).get("name") for tool in tools}
    return {tool.get("name") for tool in tools}


def _results(protocol: str, body: dict) -> list[str]:
    """The tools' results the conversation holds, in order."""
    if protocol == "chat":
        return [_text_of(m.get("content")) for m in body["messages"] if m.get("role") == "tool"]
    if protocol == "resp":
        return [_text_of(item.get("output")) for item in body["input"]
                if item.get("type") == "function_call_output"]
    return [_text_of(part.get("content")) for m in body["messages"]
            if isinstance(m.get("content"), list)
            for part in m["content"] if part.get("type") == "tool_result"]


def _solve(protocol: str, body: dict, faults) -> tuple[tuple[str, str, str] | None, str]:
    """The session's next step: a call (id, name, arguments), or a word."""
    results = _results(protocol, body)
    step = len(results)
    path, patch = (("README.md", _README) if "solver_wrong" in faults
                   else ("test_interval.py", _TESTS) if "solver_tests" in faults
                   else ("interval.py", _FIX))
    if step == 0:
        # A file is read before it is patched, as Foundry asks.
        return ("call_s0", "read_file", json.dumps({"path": path})), ""
    if step == 1:
        return ("call_s1", "apply_patch", json.dumps({"patch": patch})), ""
    if step == 2:
        return ("call_s2", "run_command", json.dumps({"command": "python -m unittest"})), ""
    if step == 3 and "solver_quiet" not in faults:
        cited = re.findall(r"event_id=(\d+)", results[-1])
        claims = [{"claim_text": "python -m unittest passes", "expected_exit_code": 0,
                   "command_event_id": int(cited[-1])}] if cited else []
        return ("call_s3", "finish", json.dumps({
            "status": "completed", "summary": "Fixed the count in overlap.",
            "claims": claims})), ""
    return None, "Done."


def _asked(body: dict) -> str:
    """What the request asks for, as the checkup words it."""
    words = json.dumps(body)
    found = re.search(r"from 1 to (\d+)", words)
    if found:
        return " ".join(str(n) for n in range(1, int(found.group(1)) + 1))
    return "ready"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _seen(self, body=None):
        self.server.seen.append({
            "path": self.path,
            "authorization": (self.headers.get("Authorization")
                              or self.headers.get("x-api-key")),
            **({"body": body} if body is not None else {})})

    def _send(self, status, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._seen()
        status, content_type, body = self.server.answer
        self._send(status, body, content_type)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw)
        self._seen(body)
        faults = self.server.faults
        protocol = _protocol(self.path)
        if protocol is None or protocol not in self.server.speaks:
            self._send(404, {"error": {"message": f"no route for {self.path}"}})
            return
        if "post401" in faults:
            self._send(401, {"error": {"message": "invalid token"}})
            return
        if "post500" in faults:
            self._send(500, {"error": {"message": "upstream failed"}})
            return
        if body.get("model") != MODEL and "any_model" not in faults:
            self._send(404, {"error": {"type": "not_found_error",
                                       "message": f"model {body.get('model')} not found"}})
            return
        call, text, cut = None, "", None
        if "finish" in _tool_names(protocol, body):
            call, text = _solve(protocol, body, faults)
        elif _continuing(protocol, body):
            if "reject_tool_result" in faults:
                self._send(400, {"error": {"message": "unsupported message role"}})
                return
            text = f"The tool says {_result(protocol, body)}."
        elif body.get("tools") and "drop_tools" not in faults:
            call = ("call_1", "add",
                    '"a=2, b=3"' if "mangle_args" in faults else '{"a": 2, "b": 3}')
        elif body.get("tools"):
            text = "I cannot use tools."
        else:
            text = _asked(body)
            if "echo_auth" in faults and text == "ready":
                text += " " + (self.headers.get("x-api-key")
                               or self.headers.get("Authorization", "").split()[-1])
            ceiling = _ceiling(protocol, body)
            words = text.split(" ")
            if ceiling is not None and len(words) > ceiling and "ignore_ceiling" not in faults:
                text = " ".join(words[:ceiling])
                cut = "hide_cut" not in faults
        reply = {"model": MODEL if "any_model" in faults else body["model"], "text": text,
                 "call": call, "cut": cut,
                 "usage": (20, len(text.split(" ")) if text else 10)}
        if body.get("stream") and "no_sse" not in faults:
            self._stream(protocol, reply)
        else:
            self._send(200, getattr(self, f"_{protocol}_once")(reply))

    # -- once ---------------------------------------------------------------

    @staticmethod
    def _chat_once(reply):
        message = {"role": "assistant", "content": reply["text"] or None}
        finish = "length" if reply["cut"] else "stop"
        if reply["call"] is not None:
            call_id, name, arguments = reply["call"]
            message["tool_calls"] = [{"id": call_id, "type": "function",
                                      "function": {"name": name, "arguments": arguments}}]
            finish = "tool_calls"
        return {"id": "chatcmpl-1", "object": "chat.completion", "model": reply["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": reply["usage"][0],
                          "completion_tokens": reply["usage"][1]}}

    @staticmethod
    def _resp_output(reply):
        if reply["call"] is not None:
            call_id, name, arguments = reply["call"]
            return [{"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id,
                     "name": name, "arguments": arguments, "status": "completed"}]
        return [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": reply["text"],
                              "annotations": []}]}]

    @classmethod
    def _resp_object(cls, reply, output):
        whole = {"id": "resp_1", "object": "response", "model": reply["model"],
                 "status": "incomplete" if reply["cut"] else "completed", "output": output,
                 "usage": {"input_tokens": reply["usage"][0],
                           "output_tokens": reply["usage"][1]}}
        if reply["cut"]:
            whole["incomplete_details"] = {"reason": "max_output_tokens"}
        return whole

    @classmethod
    def _resp_once(cls, reply):
        return cls._resp_object(reply, cls._resp_output(reply))

    @staticmethod
    def _anth_stop(reply):
        return ("tool_use" if reply["call"] is not None
                else "max_tokens" if reply["cut"] else "end_turn")

    @classmethod
    def _anth_once(cls, reply):
        if reply["call"] is not None:
            call_id, name, arguments = reply["call"]
            content = [{"type": "tool_use", "id": f"toolu_{call_id}", "name": name,
                        "input": json.loads(arguments)}]
        else:
            content = [{"type": "text", "text": reply["text"]}]
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": reply["model"],
                "content": content, "stop_reason": cls._anth_stop(reply),
                "usage": {"input_tokens": reply["usage"][0],
                          "output_tokens": reply["usage"][1]}}

    # -- streamed -----------------------------------------------------------

    def _stream(self, protocol, reply):
        pieces = [word + " " for word in reply["text"].split(" ")[:-1]]
        pieces += reply["text"].split(" ")[-1:] if reply["text"] else []
        events = getattr(self, f"_{protocol}_events")(reply, pieces)
        lines = []
        for event in events:
            if event == "[DONE]":
                lines.append(b"data: [DONE]\n\n")
            elif protocol == "anth":
                lines.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
            else:
                lines.append(f"data: {json.dumps(event)}\n\n".encode())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if "buffer" in self.server.faults:
            time.sleep(1.2)
            self.wfile.write(b"".join(lines))
        else:
            for line in lines:
                self.wfile.write(line)
                self.wfile.flush()
        self.wfile.flush()

    def _chat_events(self, reply, pieces):
        def chunk(delta, finish=None):
            return {"id": "chatcmpl-1", "object": "chat.completion.chunk",
                    "model": reply["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        events = [chunk({"role": "assistant"})]
        events += [chunk({"content": piece}) for piece in pieces]
        finish = "length" if reply["cut"] else "stop"
        if reply["call"] is not None:
            call_id, name, arguments = reply["call"]
            events.append(chunk({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                                                 "function": {"name": name,
                                                              "arguments": ""}}]}))
            half = len(arguments) // 2
            for part in (arguments[:half], arguments[half:]):
                events.append(chunk({"tool_calls": [{"index": 0,
                                                     "function": {"arguments": part}}]}))
            finish = "tool_calls"
        events.append(chunk({}, finish))
        events.append({"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [],
                       "model": reply["model"],
                       "usage": {"prompt_tokens": reply["usage"][0],
                                 "completion_tokens": reply["usage"][1]}})
        return events + ["[DONE]"]

    def _resp_events(self, reply, pieces):
        output = self._resp_output(reply)
        item = output[0]
        events = [{"type": "response.created",
                   "response": {**self._resp_object(reply, []), "status": "in_progress"}}]
        if reply["call"] is not None:
            events.append({"type": "response.output_item.added", "output_index": 0,
                           "item": {**item, "arguments": "", "status": "in_progress"}})
            events.append({"type": "response.function_call_arguments.delta", "output_index": 0,
                           "item_id": item["id"], "delta": reply["call"][2]})
        else:
            events.append({"type": "response.output_item.added", "output_index": 0,
                           "item": {**item, "content": [], "status": "in_progress"}})
            events += [{"type": "response.output_text.delta", "output_index": 0,
                        "content_index": 0, "item_id": item["id"], "delta": piece}
                       for piece in pieces]
        events.append({"type": "response.output_item.done", "output_index": 0, "item": item})
        events.append({"type": "response.incomplete" if reply["cut"] else "response.completed",
                       "response": self._resp_object(reply, output)})
        return events

    def _anth_events(self, reply, pieces):
        events = [{"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": reply["model"],
            "content": [], "stop_reason": None,
            "usage": {"input_tokens": reply["usage"][0], "output_tokens": 1}}}]
        if reply["call"] is not None:
            call_id, name, arguments = reply["call"]
            events.append({"type": "content_block_start", "index": 0,
                           "content_block": {"type": "tool_use", "id": f"toolu_{call_id}",
                                             "name": name, "input": {}}})
            events.append({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "input_json_delta", "partial_json": arguments}})
        else:
            events.append({"type": "content_block_start", "index": 0,
                           "content_block": {"type": "text", "text": ""}})
            events += [{"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": piece}} for piece in pieces]
        events.append({"type": "content_block_stop", "index": 0})
        events.append({"type": "message_delta",
                       "delta": {"stop_reason": self._anth_stop(reply), "stop_sequence": None},
                       "usage": {"output_tokens": reply["usage"][1]}})
        events.append({"type": "message_stop"})
        return events


class Gateway(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *, answer=(200, "application/json", MODELS), faults=(), speaks=ALL):
        super().__init__(("127.0.0.1", 0), Handler)
        self.answer, self.faults, self.speaks, self.seen = answer, set(faults), speaks, []

    def handle_error(self, request, client_address):
        pass        # a client that refused the certificate, or hung up
