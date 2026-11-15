"""Run a set of scenarios against a real endpoint, one after another.

    python verify/live_batch.py [--set p1|p9-11] [--model MODEL] [--chat] [--budget USD]

Each scenario goes through run.py --live (Anthropic Messages), or with --chat
through run.py --live-chat (Anthropic's OpenAI-compatible endpoint, the Chat
Completions adapter). The key is found once (live.py) and passed down in the
environment; it is written nowhere. Under verify/out/, which git ignores:
a log per scenario, a line per scenario in live_results-SET[-chat].jsonl, and
live_DONE at the end. --budget stops the batch before the scenario that would
start once the estimates so far have reached that many US dollars.

This spends money on your account. Run it yourself, in your own terminal.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live  # noqa: E402

SETS = {
    # P1: the golden fix, a feature, resume, compaction, a sub-agent, a review,
    # an MCP tool with hooks, the prompt, a refactor, memories.
    "p1": ["s1-chat-fix", "s2-resp-feature", "s3-anth-resume", "s4-chat-compact",
           "s5-resp-subagent", "s6-anth-review", "s7b-chat-mcp-hooks", "s8-resp-repl",
           "s9-anth-refactor-faults", "s10b-chat-memories"],
    # P9 to P11: the MCP server, retention, skills, approvals, goals, plan mode,
    # exec's variants, MCP over HTTP, /compact, and the commands of the prompt.
    "p9-11": ["s11-resp-mcp-server", "s12-chat-retention", "s13-anth-env-skills",
              "s14-chat-repl-approvals", "s15-anth-repl-goal", "s16b-resp-repl-plan-fork",
              "s17-chat-exec-variants", "s18-resp-mcp-http", "s19-anth-repl-compact",
              "s20-chat-repl-init", "s21-resp-repl-image", "s22-anth-cli"],
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scenarios against a real endpoint.")
    parser.add_argument("--set", dest="which", choices=sorted(SETS), default="p1")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--chat", action="store_true",
                        help="over Chat Completions, on Anthropic's OpenAI-compatible endpoint")
    parser.add_argument("--budget", type=float, default=None,
                        help="stop once the estimates so far reach this many US dollars")
    args = parser.parse_args(argv)

    out = HERE / "out"
    out.mkdir(exist_ok=True)
    env = {**os.environ, "VERIFY_MODEL": args.model, live.VARIABLE: live.obtain()}
    flag = "--live-chat" if args.chat else "--live"
    results = out / f"live_results-{args.which}{'-chat' if args.chat else ''}.jsonl"
    done = out / "live_DONE"
    done.unlink(missing_ok=True)
    spent = 0.0
    for name in SETS[args.which]:
        if args.budget is not None and spent >= args.budget:
            print(f"stopping before {name}: about ${spent:.2f} spent, the budget is "
                  f"${args.budget:.2f}", flush=True)
            break
        began = time.time()
        print(f"{time.strftime('%H:%M:%S')} {name} ...", flush=True)
        try:
            run = subprocess.run(
                [sys.executable, str(HERE / "run.py"), str(HERE / "scenarios" / f"{name}.json"),
                 flag, "--out", str(out / "runs")],
                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=1800)
            output, code = run.stdout + run.stderr, run.returncode
        except subprocess.TimeoutExpired as exc:
            output, code = (exc.stdout or "") if isinstance(exc.stdout, str) else "", "timeout"
        (out / f"{name}.live.log").write_text(output, encoding="utf-8")
        summary = {}
        start = output.find("\n{\n")
        if start >= 0:
            try:
                summary, _ = json.JSONDecoder().raw_decode(output, start + 1)
            except ValueError:
                pass
        usage = summary.get("usage") or {}
        line = {"name": name, "exit": code, "seconds": round(time.time() - began),
                "base": summary.get("base"),
                "runs": [{"exit": r.get("exit"), "termination": r.get("termination"),
                          "tools": len(r.get("tools") or [])} for r in summary.get("runs", [])],
                "check": (summary.get("check") or {}).get("exit"),
                "problems": summary.get("problems"), "usage": usage}
        with results.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        spent += usage.get("estimated_usd") or 0.0
        print(f"   exit {code} in {line['seconds']}s, about ${usage.get('estimated_usd')}"
              f" (${spent:.2f} so far); problems: {line['problems']}", flush=True)
    done.write_text("done", encoding="utf-8")
    print(f"all done: about ${spent:.2f}; results in {results}", flush=True)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
