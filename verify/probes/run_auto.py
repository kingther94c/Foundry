"""Run the probe projects with `foundry exec` against the real Anthropic API,
or against a gateway that speaks Chat Completions or Responses.

    python verify/probes/run_auto.py RUN [NAME ...] [--model MODEL] [--mode MODE]
                                     [--repeat N] [--budget USD]
                                     [--projects DIR] [--out DIR]
                                     [--gateway URL [--protocol openai_compat|responses]]

Each project (written by make_projects_b.py, and made here when they are
missing; or by make_projects_c.py, named with --projects) is copied to
OUT/RUN/NAME/ws and given a fresh home whose only command rules are the
project's allow.txt. What its beside/ holds goes next to ws -- the third
set's ResearchOS finds `tools` at ../tools -- and a project whose
requires.txt names a module that is not installed is skipped. It is run once with `foundry exec
--json`: nobody is asked anything, and what no rule allows is refused. Then
the project's hidden tests, which Foundry never saw, run on a copy of what it
left. --repeat runs each project that many times (NAME.1, NAME.2, ...): one
run of a task says little about a model that does not answer the same way
twice.

The key comes from live.py, goes to Foundry in an environment variable and is
in no file; each run's folder is searched for it afterwards. --budget stops
before the run that would start once the estimates so far reach that many US
dollars. VERIFY_FOUNDRY names another checkout whose src/ to run, to compare a
change with what came before it.

--gateway runs against that base URL instead (a local agent gateway such as
OpenClaw's, with --model one of its agents): the token is FOUNDRY_VERIFY_KEY or
typed at a prompt, never the Anthropic key `foundry login` stored. A gateway's
tokens have no price here, so --budget does not stop it; each run says how
many requests it made, as a subscription behind one counts them.

This spends money on your account. Run it yourself, in your own terminal.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import live  # noqa: E402

ROOT = HERE.parents[1]
UNDER_TEST = Path(os.environ.get("VERIFY_FOUNDRY") or ROOT)


def git(ws, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "scaffold", "GIT_AUTHOR_EMAIL": "scaffold@invalid",
           "GIT_COMMITTER_NAME": "scaffold", "GIT_COMMITTER_EMAIL": "scaffold@invalid"}
    return subprocess.run(["git", *args], cwd=ws, env=env, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def missing(src):
    """The modules the project at ``src`` needs that are not installed."""
    wanted = src / "requires.txt"
    names = wanted.read_text(encoding="utf-8").split() if wanted.is_file() else []
    return [name for name in names if importlib.util.find_spec(name) is None]


def lay_out(src, out):
    """The project's scaffold copied to ``out``/ws, and its beside/ next to it."""
    out.mkdir(parents=True)
    ws = out / "ws"
    shutil.copytree(src / "repo", ws)
    beside = src / "beside"
    for item in sorted(beside.iterdir()) if beside.is_dir() else []:
        shutil.copytree(item, out / item.name)
    return ws


def backend(model, endpoint=None):
    """The [backend] table of a run's home: Anthropic Messages, or with
    ``endpoint`` = (protocol, base URL) that gateway. The key is read from
    live.VARIABLE either way."""
    if endpoint is None:
        where = 'protocol = "anthropic_messages"\n'
    else:
        protocol, url = endpoint
        where = f'protocol = "{protocol}"\nbase_url = "{url}"\n'
    return f'[backend]\n{where}model = "{model}"\nenv_key = "{live.VARIABLE}"\n\n'


def one(src, out, model, mode, key, endpoint=None):
    """Run the project at ``src`` in the new folder ``out``; its summary."""
    name = src.name
    ws = lay_out(src, out)
    git(ws, "init", "-q", "-b", "main")
    git(ws, "config", "core.autocrlf", "false")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "scaffold")

    home = out / "home"
    home.mkdir()
    rules = "".join(f'\n[[permissions]]\ntool = "run_command"\npattern = "{p}"\n'
                    'decision = "allow"\nreason = "probe project"\n'
                    for p in (src / "allow.txt").read_text(encoding="utf-8").split("\n") if p)
    extra = (src / "extra.toml").read_text(encoding="utf-8")
    runtime = "[runtime]\nmax_tool_rounds = 40\n"
    if extra.startswith("[runtime]\n"):
        runtime += extra[len("[runtime]\n"):]
        extra = ""
    (home / "config.toml").write_text(
        backend(model, endpoint) + runtime + extra + rules, encoding="utf-8")
    (home / "trusted.json").write_text(json.dumps({"trusted": [str(ws.resolve())]}),
                                       encoding="utf-8")
    quiet = {k: v for k, v in os.environ.items()
             if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH", live.VARIABLE)}
    quiet.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env = {**quiet, "PYTHONPATH": str(UNDER_TEST / "src"), "FOUNDRY_HOME": str(home),
           live.VARIABLE: key}
    task = (src / "task.txt").read_text(encoding="utf-8").strip()
    argv = [sys.executable, "-m", "foundry", "exec", task, "--workspace", str(ws), "--json",
            *(["--mode", mode] if mode else [])]

    began = time.time()
    done = subprocess.run(argv, cwd=str(out), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600)
    (out / "events.jsonl").write_text(done.stdout, encoding="utf-8")
    (out / "stderr.txt").write_text(done.stderr, encoding="utf-8")

    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    tools = [e for e in events if e.get("kind") == "tool_begin"]
    ends = [e for e in events if e.get("kind") == "tool_end"]
    rejected = [{"display": e.get("display"), "reason": e.get("reason")}
                for e in events if e.get("kind") == "tool_rejected"]
    patch_failures = [e.get("summary") or e.get("output", "")[:200] for e in ends
                      if e.get("tool") == "apply_patch" and e.get("ok") is False]
    term = next((e for e in events if e.get("kind") == "termination"), {})
    used = live.usage_of(home / "sessions")

    # The hidden tests, on a copy, so that the attempt stays as it was left.
    judge = out / "judge"
    shutil.copytree(ws, judge, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for test in (src / "hidden").glob("*.py"):
        shutil.copy(test, judge / "tests" / test.name)
    # The second set is judged by its hidden tests alone: its repositories
    # carry failures of their own.
    only = ([f"tests/{t.name}" for t in (src / "hidden").glob("*.py")]
            if (src / "hidden_only").exists() else [])
    hidden = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *only], cwd=judge,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**quiet, "PYTHONPATH": ""})
    summary = {
        "name": name, "run": out.name, "model": model, "exit": done.returncode,
        "seconds": round(time.time() - began),
        "status": term.get("status"), "reason": term.get("reason"),
        "tool_calls": len(tools), "tools": [e.get("tool") for e in tools],
        "rejected": rejected, "patch_failures": patch_failures,
        "compactions": sum(1 for e in events if e.get("kind") == "compacted"),
        "hidden_exit": hidden.returncode, "hidden_tail": hidden.stdout[-1500:],
        "changed": git(ws, "status", "--short").splitlines(),
        "usage": used, "requests": used["records"], "estimated_usd": live.cost(model, used),
        "key_files": live.scan(out, key), "summary": term.get("summary"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="The probe projects on the real API.")
    parser.add_argument("run", help="a name for this batch; its folder under --out")
    parser.add_argument("names", nargs="*", help="the projects to run (default: all)")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--mode", default=None, help="exec's own default when left out")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument("--projects", type=Path, default=HERE / "projects")
    parser.add_argument("--out", type=Path, default=HERE.parent / "out" / "probes")
    parser.add_argument("--gateway", default=None,
                        help="the base URL of a gateway to run against instead of Anthropic")
    parser.add_argument("--protocol", default="openai_compat",
                        choices=("openai_compat", "responses"),
                        help="what the gateway speaks (with --gateway)")
    args = parser.parse_args(argv)
    endpoint = (args.protocol, args.gateway) if args.gateway else None

    if not args.projects.is_dir():
        subprocess.run([sys.executable, str(HERE / "make_projects_b.py"), str(args.projects)],
                       check=True, stdout=subprocess.DEVNULL)
    names = args.names or sorted(d.name for d in args.projects.iterdir() if d.is_dir())
    run_dir = args.out / args.run
    run_dir.mkdir(parents=True)
    key = (live.obtain("Token for the gateway (not shown, not stored): ", stored=False)
           if endpoint else live.obtain())
    spent, summaries = 0.0, []
    labels = [(name, name if args.repeat == 1 else f"{name}.{turn}")
              for name in names for turn in range(1, args.repeat + 1)]
    for name, label in labels:
        if args.budget is not None and spent >= args.budget:
            print(f"stopping before {label}: about ${spent:.2f} spent, the budget is "
                  f"${args.budget:.2f}", flush=True)
            break
        print(f"{time.strftime('%H:%M:%S')} {label} ...", flush=True)
        needs = missing(args.projects / name)
        if needs:
            print(f"   skipped: it needs {', '.join(needs)}, which is not installed "
                  f"(pip install {' '.join(needs)})", flush=True)
            continue
        try:
            s = one(args.projects / name, run_dir / label, args.model, args.mode, key, endpoint)
        except Exception as exc:              # one project's failure does not stop the rest
            print(f"   failed to run: {exc!r}", flush=True)
            continue
        summaries.append(s)
        spent += s["estimated_usd"] or 0.0
        priced = (f"about ${s['estimated_usd']}" if s["estimated_usd"] is not None
                  else "not priced")
        print(f"   {s['status']} | hidden tests {'pass' if s['hidden_exit'] == 0 else 'FAIL'}"
              f" | {s['tool_calls']} calls, {len(s['rejected'])} refused, "
              f"{len(s['patch_failures'])} patch failures, {s['compactions']} compactions"
              f" | {s.get('requests', 0)} requests, {priced}"
              + (f" | THE KEY IS IN {s['key_files']}" if s["key_files"] else ""), flush=True)
    passed = sum(1 for s in summaries if s["hidden_exit"] == 0)
    requests = sum(s.get("requests", 0) for s in summaries)
    (run_dir / "results.json").write_text(json.dumps(
        {"model": args.model, "endpoint": args.gateway or "anthropic",
         "protocol": args.protocol if args.gateway else "anthropic_messages",
         "runs": len(summaries), "passed": passed, "requests": requests,
         "estimated_usd": round(spent, 4), "summaries": summaries}, indent=1), encoding="utf-8")
    print(f"{passed} of {len(summaries)} passed their hidden tests; {requests} requests; "
          f"about ${spent:.2f}", flush=True)
    (run_dir / "DONE").write_text("done", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
