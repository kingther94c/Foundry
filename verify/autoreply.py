"""Answer the relay's requests by script: a model with one thing to say.

    python verify/autoreply.py DIR

The first request that offers tools is answered by reading README.md, with
some reasoning; the next by calling finish (completed, no claims). A request
that offers no tools gets a line of text. That is enough to take Foundry
through a whole task on each protocol, which is what the smoke scenarios
check; nothing here solves a task.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402


def answer(d, n):
    """The reply to request ``n``, written beside it and handed to the relay."""
    protocol, body = relay._load(d, n)
    tools = {t[0] for t in relay.tools_of(protocol, body)}
    has_result = any(kind == "tool" for kind, _ in relay.normalise(protocol, body))
    if not tools:
        reply = {"content": "Summary: the user asked a question; nothing else happened."}
    elif not has_result and "read_file" in tools:
        reply = {"content": "Let me look.", "reasoning": "I should read the README first.",
                 "tool_calls": [{"name": "read_file", "arguments": {"path": "README.md"}}]}
    else:
        reply = {"content": "Done.", "reasoning": "The README answers it.",
                 "tool_calls": [{"name": "finish", "arguments": {
                     "status": "completed", "summary": "Read the README.", "claims": []}}]}
    path = d / f"auto{n}.json"
    path.write_text(json.dumps(reply), encoding="utf-8")
    relay.reply(d, n, path)


def main(d):
    answered = set()
    while not (d / "DONE").is_file():
        for n in relay._requests(d):
            if n not in answered and not (d / f"{n}.response.json").is_file():
                answer(d, n)
                answered.add(n)
        time.sleep(0.5)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
