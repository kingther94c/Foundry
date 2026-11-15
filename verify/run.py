"""Run a scenario: Foundry against the relay, or against a real endpoint.

    python verify/run.py SCENARIO.json [SCENARIO.json ...]
                         [--auto | --live | --live-chat] [--out DIR]

A scenario is a small repository, a configuration and one or more Foundry
invocations:

  {"name": "s1-chat-fix", "protocol": "openai_compat", "repo": "sample" | "tools",
   "config": "extra TOML", "faults": {"2": ["drop"]},
   "runs": [{"args": ["exec", "--json", "TASK"], "stdin": null, "expect_exit": 0}],
   "check": "python -m pytest tests -q"}

With no flag the model is played from outside. The runner prints the relay's
directory (the READY line) and waits while whoever plays the model answers
each request with `relay.py next` and `relay.py reply`, seeing nothing but
what Foundry sent. --auto has autoreply.py answer instead: enough to take
Foundry through a task on each protocol with the scenario's injected faults,
which is what the smoke scenarios are for and what CI runs.

--live runs the scenario against the real Anthropic API over Anthropic
Messages, and --live-chat against Anthropic's OpenAI-compatible endpoint over
Chat Completions: no relay, the scenario's faults ignored, the model from
VERIFY_MODEL. The key comes from live.py, goes to Foundry in an environment
variable, and is in no file; the run's folder is searched for it afterwards.
A live run spends money, so it is started by hand, in your own terminal.

VERIFY_FOUNDRY names another checkout whose src/ to run instead of this one's:
a snapshot from before a change, to put the two through the same scenarios.

Each scenario prints a summary (also BASE/summary.json) and a verdict line.
The exit status is 1 if any verdict is FAIL: a canary or the key found in a
file, a check that fails, or a run whose exit status is not the `expect_exit`
its scenario gives (under --auto, a run with none is expected to exit 0).
"""

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live  # noqa: E402

ROOT = HERE.parent                                              # the kit's checkout
UNDER_TEST = Path(os.environ.get("VERIFY_FOUNDRY") or ROOT)     # whose src/ runs
REPOS = {"sample": ROOT / "tests" / "fixtures" / "sample_repo", "tools": ROOT / "tools"}
LIVE_MODEL = "claude-sonnet-5-5"
NOT_PASSED = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")
PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_repo(spec, repo):
    """The scenario's repository: a copy, its extra files, one commit, then
    whatever the scenario leaves uncommitted."""
    source = REPOS.get(spec.get("repo", "sample"), Path(spec.get("repo", "")))
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", "*.pyc"))
    for rel, text in (spec.get("files") or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for rel, data in (spec.get("files_b64") or {}).items():
        (repo / rel).write_bytes(base64.b64decode(data))
    for rel in spec.get("remove") or []:
        (repo / rel).unlink()
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "T"], ["config", "core.autocrlf", "false"],
                 ["add", "."], ["commit", "-qm", "initial"]):
        git(repo, *args)
    for rel, text in (spec.get("dirty") or {}).items():
        (repo / rel).write_text(text, encoding="utf-8")


def backend_config(spec, mode, port, model):
    """The [backend] table for this mode, before the scenario's own config."""
    extras = spec.get("backend") or ""
    if mode in ("live", "live-chat"):
        # A relay setting has no meaning on a real endpoint.
        extras = "".join(line + "\n" for line in extras.splitlines() if "relay" not in line)
        where = ('protocol = "openai_compat"\nbase_url = "https://api.anthropic.com/v1"\n'
                 if mode == "live-chat" else 'protocol = "anthropic_messages"\n')
        return (f'[backend]\n{where}model = "{model}"\n'
                f'env_key = "{live.VARIABLE}"\n{extras}\n')
    return (f'[backend]\nbase_url = "http://127.0.0.1:{port}/v1"\nmodel = "relay-model"\n'
            f'protocol = "{spec["protocol"]}"\ncredential_source = "gateway_token"\n'
            f"stream_idle_timeout_ms = 1800000\n{extras}\n")


def read_events(path):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events


def problems_of(spec, summary, mode):
    """Why this scenario's verdict is FAIL, if it is."""
    problems = []
    if summary["canary_leaks"]:
        problems.append(f"a canary is in {summary['canary_leaks']}")
    if summary.get("key_files"):
        problems.append(f"the key is in {summary['key_files']}")
    for run, result in zip(spec["runs"], summary["runs"]):
        expected = run.get("expect_exit", 0 if mode == "auto" else None)
        if expected is not None and result["exit"] != expected:
            problems.append(f"run {result['run']} exited {result['exit']}, not {expected}")
    if summary.get("check") and summary["check"]["exit"] != 0:
        problems.append("the check failed")
    return problems


def run_scenario(path, mode, out, key=None):
    live_run = mode in ("live", "live-chat")
    text = Path(path).read_text(encoding="utf-8")
    if live_run:
        # The relay's second models (/model, review_model) as a real one.
        second = os.environ.get("VERIFY_MODEL2", "claude-haiku-5-5")
        text = text.replace("relay-model-2", second).replace("relay-review-model", second)
    spec = json.loads(text)
    name = spec["name"]
    out.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=out))
    relay, repo, home = base / "r", base / "repo", base / "home"
    for folder in (relay, home):
        folder.mkdir(parents=True)
    make_repo(spec, repo)

    port = free_port()
    canary = "relay-canary-" + secrets.token_hex(12)
    # A second secret a scenario may hand a service (an MCP server's token),
    # looked for afterwards as the canary is.
    secret, mcp_port = "service-canary-" + secrets.token_hex(12), free_port()

    def subst(text):
        for token, value in (("HOME", home), ("REPO", repo), ("BASE", base), ("FOUNDRY", ROOT),
                             ("KIT", HERE), ("PYTHON", sys.executable)):
            text = text.replace("{" + token + "}", str(value).replace("\\", "/"))
        return text.replace("{MCPPORT}", str(mcp_port)).replace("{SECRET}", secret)

    model = os.environ.get("VERIFY_MODEL") or LIVE_MODEL
    head = subst((spec.get("top") or "") + backend_config(spec, mode, port, model))
    if live_run and spec.get("faults"):
        print("live: the scenario's faults are ignored", flush=True)
    (home / "config.toml").write_text(head + subst(spec.get("config") or ""), encoding="utf-8")
    if spec.get("faults"):
        (relay / "faults.json").write_text(json.dumps(spec["faults"]), encoding="utf-8")
    for rel, text in (spec.get("home_files") or {}).items():
        target = home / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(subst(text), encoding="utf-8")

    def started(argv, log):
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                stderr=open(base / log, "w", encoding="utf-8"))

    server = None if live_run else started(
        [sys.executable, str(HERE / "relay.py"), "serve", str(relay), str(port)], "relay.err")
    player = started([sys.executable, str(HERE / "autoreply.py"), str(relay)],
                     "autoreply.err") if mode == "auto" else None
    services = [started([sys.executable if a == "{PYTHON}" else subst(a) for a in argv],
                        f"service{i}.err")
                for i, argv in enumerate(spec.get("services") or [], 1)]
    if services:
        time.sleep(2)
    print(f"READY relay dir: {relay}", flush=True)

    env = {**os.environ, "PYTHONPATH": str(UNDER_TEST / "src"), "PYTHONIOENCODING": "utf-8",
           "FOUNDRY_HOME": str(home), "FOUNDRY_GATEWAY_TOKEN": canary,
           "NO_COLOR": "1", "TERM": "dumb"}
    # A live run reaches the endpoint as this machine does; the relay is here.
    for variable in (*NOT_PASSED, live.VARIABLE, *(() if live_run else PROXIES)):
        env.pop(variable, None)
    env.update({k: subst(v).replace("{PATH}", os.environ.get("PATH", ""))
                for k, v in (spec.get("env") or {}).items()})
    quiet_env = dict(env)                       # for what needs no key: the check
    if live_run:
        env[live.VARIABLE] = key

    results = []
    try:
        for i, run in enumerate(spec["runs"], 1):
            if "config" in run:
                (home / "config.toml").write_text(head + subst(run["config"]), encoding="utf-8")
            if run.get("age_sessions_hours") and (home / "sessions").is_dir():
                then = time.time() - run["age_sessions_hours"] * 3600
                for f in (home / "sessions").rglob("*"):
                    if f.is_file():
                        os.utime(f, (then, then))
            if run.get("argv"):
                argv = [sys.executable if a == "{PYTHON}" else subst(a) for a in run["argv"]]
                run.setdefault("args", run["argv"][1:3])
            else:
                argv = [sys.executable, "-m", "foundry", *(subst(a) for a in run["args"])]
                if run.get("workspace", True):
                    argv += ["-C", str(repo)]
            began = time.time()
            stdin = run.get("stdin")
            try:
                done = subprocess.run(argv, cwd=str(repo), env=env, capture_output=True,
                                      input=subst(stdin) if stdin is not None else "",
                                      text=True, encoding="utf-8", errors="replace",
                                      timeout=run.get("timeout", 5400))
                code, stdout, stderr = done.returncode, done.stdout, done.stderr
            except subprocess.TimeoutExpired as exc:
                code, stdout, stderr = "timeout", exc.stdout or "", exc.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", "replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", "replace")
            (base / f"run{i}.out").write_text(stdout, encoding="utf-8")
            (base / f"run{i}.err").write_text(stderr, encoding="utf-8")
            results.append({"run": i, "args": run["args"][:2], "exit": code,
                            "seconds": round(time.time() - began)})
            print(f"run {i} {run['args'][:2]} -> exit {code} in {time.time() - began:.0f}s",
                  flush=True)
    finally:
        (relay / "DONE").write_text("done", encoding="utf-8")
        if server is not None:
            time.sleep(3)
            server.terminate()
        for process in (player, *services):
            if process is not None:
                process.terminate()

    summary = {"name": name, "base": str(base), "mode": mode, "protocol": spec["protocol"],
               "runs": results}
    for r in results:
        events = read_events(base / f"run{r['run']}.out")     # when it wrote JSON lines
        kinds = {}
        for e in events:
            kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
        r["event_kinds"] = kinds
        r["termination"] = [{k: e.get(k) for k in ("status", "reason")}
                            for e in events if e.get("kind") == "termination"]
        r["tools"] = [f"{e.get('tool')}:{'ok' if e.get('ok') else 'FAIL'}"
                      for e in events if e.get("kind") == "tool_end"]
        r["rejected"] = [e.get("tool") for e in events if e.get("kind") == "tool_rejected"]
        r["notices"] = [str(e.get("text"))[:200] for e in events
                        if e.get("kind") in ("notice", "error")][:20]
        r["stderr_tail"] = (base / f"run{r['run']}.err").read_text(encoding="utf-8")[-800:]

    # The canaries must be nowhere Foundry writes, and in no body it sends.
    leaks = []
    for f in base.rglob("*"):
        try:
            if f.is_file() and f.stat().st_size < 50_000_000:
                data = f.read_bytes()
                if canary.encode() in data or secret.encode() in data:
                    leaks.append(f.relative_to(base).as_posix())
        except OSError:
            pass
    summary["canary_leaks"] = sorted(leaks)
    if live_run:
        summary["key_files"] = live.scan(base, key)
    summary["requests"] = len(list(relay.glob("*.request.json")))
    models = {}
    for f in relay.glob("*.request.json"):
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
            asked = (body.get("body") or body).get("model")
        except (ValueError, AttributeError):
            asked = "?"
        models[str(asked)] = models.get(str(asked), 0) + 1
    summary["models"] = models

    def lines_of(file):
        return [json.loads(line) for line in (relay / file).read_text(
            encoding="utf-8").splitlines()] if (relay / file).is_file() else []
    summary["opaque"] = [{"n": c["n"], "back": len(c["opaque_back"]),
                          "emitted_before": len(c["opaque_emitted_before"])}
                         for c in lines_of("checks.jsonl") if c["opaque_emitted_before"]]
    summary["attempts"] = [a for a in lines_of("attempts.jsonl")
                           if a["fault"] or a["attempt"] > 1]
    summary["git_status"] = git(repo, "status", "--short").stdout.rstrip().splitlines()
    summary["git_log"] = git(repo, "log", "--oneline", "-5").stdout.strip().splitlines()
    if spec.get("check"):
        check = subprocess.run(spec["check"], cwd=repo, shell=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               env={**quiet_env, "PYTHONPATH": str(repo / "src")}, timeout=600)
        summary["check"] = {"exit": check.returncode,
                            "tail": (check.stdout + check.stderr)[-400:]}
    summary["journals"] = []
    sessions = home / "sessions"
    for journal in (sorted(sessions.glob("*/events.jsonl")) if sessions.is_dir() else []):
        records = [json.loads(line) for line in journal.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        types = {}
        for record in records:
            types[record["type"]] = types.get(record["type"], 0) + 1
        summary["journals"].append({
            "session": journal.parent.name, "records": len(records),
            "ordinals_unique": len({r["ordinal"] for r in records}) == len(records),
            "types": types})
    if live_run:
        used = live.usage_of(sessions)
        summary["usage"] = {**used, "model": model, "estimated_usd": live.cost(model, used)}
    summary["problems"] = problems_of(spec, summary, mode)
    (base / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(f"VERDICT {name}: " + ("FAIL: " + "; ".join(summary["problems"])
                                if summary["problems"] else "OK"), flush=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run verification scenarios.")
    parser.add_argument("scenarios", nargs="+", type=Path)
    how = parser.add_mutually_exclusive_group()
    how.add_argument("--auto", dest="mode", action="store_const", const="auto",
                     help="autoreply.py plays the model")
    how.add_argument("--live", dest="mode", action="store_const", const="live",
                     help="the real Anthropic API, over Anthropic Messages")
    how.add_argument("--live-chat", dest="mode", action="store_const", const="live-chat",
                     help="Anthropic's OpenAI-compatible endpoint, over Chat Completions")
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "foundry-verify",
                        help="where each scenario's folder goes (default: the temp folder)")
    args = parser.parse_args(argv)
    mode = args.mode or "relay"
    key = live.obtain() if mode in ("live", "live-chat") else None
    failed = [summary["name"] for summary in
              (run_scenario(path, mode, args.out, key) for path in args.scenarios)
              if summary["problems"]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
