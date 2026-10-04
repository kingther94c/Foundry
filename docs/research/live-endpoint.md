# The first live endpoint check (2026-09-01)

Until now, every "end to end" run of Foundry had been against a
`ScriptedBackend` or an `HTTPServer` started inside a test. The user provided a
local OpenClaw gateway, which let this code talk to a **real HTTP service** for
the first time.

## The environment

| | Address | Protocol | Auth |
|---|---|---|---|
| gateway | `http://127.0.0.1:18789/v1` | Chat Completions | `gateway.auth.token` (a real token) |
| Responses facade | `http://127.0.0.1:18790/v1` | Responses | **any** bearer value |

The facade translates Responses requests into the gateway's
`/v1/chat/completions`, then repackages the answer as a Responses object -- the
shape is simulated, the answer is real.

## The key conclusion: this endpoint cannot verify tool calls

The gateway **accepts** `tools` / `tool_choice`, and even validates them:
when `tool_choice="required"` produces no tool call, it answers
`HTTP 502 "tool_choice=required was not satisfied by the agent response"`. But
the 7 models behind it (all of the trade-advisor family) **never emit
tool_calls**:

```
openclaw/default             tool_choice=auto     -> finish=stop, tool_calls=none
openclaw/default             tool_choice=required -> HTTP 502
openclaw/trade-advisor-panel tool_choice=required -> HTTP 502
```

So **[OQ-6](../open-questions.md) is still open**. The M3 entry gate wants
"tool-call streaming redaction fixtures from the real Gateway", and this
machine cannot produce them -- making the facade forward `tools` would not help
either, because what does not emit tool calls is the model, not the facade. Do
not mark M3 done just because something ran.

One thing that is slightly reassuring: SSE fragment reassembly for tool calls
**was already** tested over a real socket
(`test_backend_openai.py::test_streaming_reassembles_text_and_tool_calls` and
`test_streaming_handles_parallel_tool_calls` both start a real `HTTPServer`),
just with a synthetic server on the other end. The missing piece is that nobody
has seen what a real corporate Gateway's tool call looks like.

## What did get verified

Both adapters completed a full round against a real service, streaming
included:

| | Protocol | Result |
|---|---|---|
| `openai_compat` -> 18789 | Chat Completions | `OK`, usage 17095/20, the real token did not leak |
| `responses` -> 18790 | Responses | `OK`, usage 17090/23, the real token did not leak |

The `responses` adapter had **never run against any real server** before (when
D-022 promoted it to mandatory for M3, it was annotated "real protocol
behaviour unverified"). It has now completed streaming, usage and termination
events against a genuinely Responses-shaped endpoint.

Two shapes in the real wire format are worth recording, and are now byte-level
fixtures under `tests/fixtures/live_gateway/`:

- Chat Completions' **final chunk carries usage but an empty `choices`
  array**. An adapter that takes `choices[0]` before looking for usage reports
  zero tokens for every streaming round.
- Each frame of the facade's Responses SSE is preceded by an `event: <name>`
  line, and the stream still ends with `data: [DONE]`.

## Four defects found and fixed

A real service immediately exposed things a scripted backend cannot reach:

1. **The `--json` event stream was lossy.** Serialisation went through a
   hand-maintained attribute allowlist, and any field nobody remembered to add
   was silently dropped: `token_count` came out as `{"kind": "token_count"}`
   with not a single number in it -- while the journal right next to it
   recorded the real counts; `tool_begin`/`tool_end` had no `call_id`, so a
   consumer could not pair them. Changed to serialise what the event actually
   carries.
2. **No termination event in the stream.** A headless run that ends normally
   never reaches `runtime._terminate`, so `--json` simply stopped: a CI
   consumer could not get the final status from the stream, only infer it from
   the exit code.
3. **exit 10 came with no explanation.** `"headless run ended"` does not
   explain why a perfectly reasonable-looking answer is PARTIAL. For a model
   that **never calls tools at all**, that is the only reachable outcome.
4. **Loopback was being sent through the corporate proxy.** A urllib bypass
   list essentially never contains `127.0.0.1` (`proxy_bypass('127.0.0.1')`
   returns `False`), so on a machine with `HTTP_PROXY` set -- which is to say
   Foundry's target environment, always -- requests to the local gateway went
   to the proxy, and the proxy does not route back to the caller's own
   loopback. It looks like a connection timeout, identical to "the local
   service is not running".

The fourth is the most valuable of the four: it only happens when there is both
a local endpoint and a corporate proxy, which is exactly the user's real
topology. Without this local gateway it would have stayed hidden.

## Pulling the thread: a proxy path audit

The fourth defect forced the question "what else on the proxy path has nobody
looked at?", so a five-viewpoint audit was run against real captured traffic
(SSE framing / the chat adapter / the responses adapter / errors and retries /
loopback and proxies), with every finding then handed to a verifier whose job
was to refute it. Of 33 findings, 23 were refuted, leaving 10.

**One of them is the worst defect in the entire project**: with a corporate
proxy configured, the API key crosses the CONNECT tunnel **in plaintext**. See
[threat-model.md](../threat-model.md) §3(b3), which also records why six rounds
of adversarial review never touched it.

The other nine (all fixed; see `tests/test_transport_audit.py`):

| | Defect |
|---|---|
| high | the comment on a dropped stream promises "it will retry", but no layer actually retries; and it kills the whole REPL session, losing all of the user's context |
| medium | `Retry-After` was read only on 429 and only in its seconds form; a 503 saying "come back in 30 seconds" was ignored in favour of three attempts at 1/3/7 seconds |
| medium | `request_max_retries = 0` (the most natural way to turn retries off) reached `raise None` -> TypeError, and the CLI printed a traceback |
| medium | the error response body was stored in `payload` and then never read, so a 400 showed only "request rejected (HTTP 400)" while the body said plainly which field was wrong |
| medium | when `SSL_CERT_FILE` pointed at a nonexistent file it raised a bare `FileNotFoundError` (without even the filename), outside the error taxonomy |
| medium | a tool call with `arguments: null` crashed the non-streaming path (`json.loads(None)` raises TypeError, which `parse_arguments` does not catch) |
| medium | on a NotStreaming downgrade, `stream_options` was not removed with it, so a strict gateway answered 400 -- the rescue path was what broke the round |
| medium | a stream consisting only of `data: [DONE]` was treated as a successful empty answer |
| low | the non-tunnel branch dropped the proxy credentials, and the 407 message told the user to do something they had already done |

## Reproducing

```bash
curl http://127.0.0.1:18790/healthz
python -m pytest tests/test_live_endpoint_findings.py -q   # offline, byte-level fixtures
```
