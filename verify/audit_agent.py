"""List every tool call the agent that played the model made, from its
Claude Code transcript: python verify/audit_agent.py TRANSCRIPT.jsonl

Whoever plays the model is to see only what Foundry sent. This shows, after
the fact, that it read and ran nothing but the relay.
"""
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    for line in fh:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            inp = part.get("input", {})
            name = part.get("name")
            if name == "Bash":
                print("Bash:", inp.get("command", "")[:1500].replace("\n", " | "))
            elif name in ("Write", "Edit", "Read"):
                print(f"{name}:", inp.get("file_path"))
            else:
                print(f"{name}:", json.dumps(inp)[:300])
