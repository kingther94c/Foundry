"""The verification kit under verify/ is itself checked (D-199).

The kit runs Foundry as a user does, against a relay or a real endpoint, so
it is not part of the suite. These tests are about the kit: its scenarios
parse and name what the runner knows, the relay answers in each protocol's
shape and injects the failures asked of it, the mutation check kills and
spares what it should and puts files back as they were, and nothing in the
folder is shaped like a credential or names a machine.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import threading
import time
import tomllib
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "verify"
SCENARIOS = sorted((KIT / "scenarios").glob("*.json"))

PROTOCOLS = {"openai_compat", "responses", "anthropic_messages"}
FAULTS = {"429", "500", "quota", "overloaded", "drop", "stream_error", "failed"}
PLACEHOLDERS = {"HOME", "REPO", "BASE", "FOUNDRY", "KIT", "PYTHON", "MCPPORT", "SECRET", "PATH"}


def _kit(name: str):
    """A script of the kit, loaded by path: the kit is not a package, and its
    folder is not on sys.path for the rest of the suite."""
    spec = importlib.util.spec_from_file_location(
        "verify_kit_" + name.replace("/", "_"), KIT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scripts(monkeypatch):
    """Loads a script that puts its own folder on sys.path to import its
    neighbours (run.py, live_batch.py, autoreply.py, probes/run_auto.py).
    The path, and the neighbours it imported, are put back afterwards."""
    monkeypatch.syspath_prepend(str(KIT))
    before = set(sys.modules)
    yield _kit
    for name, module in list(sys.modules.items()):
        if name not in before and str(getattr(module, "__file__", None) or "").startswith(str(KIT)):
            del sys.modules[name]


def _files() -> list[Path]:
    return sorted(p for p in KIT.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                  and "out" not in p.relative_to(KIT).parts[:1]
                  and p.relative_to(KIT).parts[:2] != ("probes", "projects"))


# -- the scenarios -----------------------------------------------------------

def test_there_are_scenarios_and_the_three_smoke_ones():
    names = {p.stem for p in SCENARIOS}

    assert {"smoke-openai_compat", "smoke-responses", "smoke-anthropic_messages"} <= names
    assert len(names) >= 25


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_a_scenario_parses_and_says_what_the_runner_knows(path):
    text = path.read_text(encoding="utf-8")
    spec = json.loads(text)

    assert spec["name"] == path.stem, "the runner names the run after the scenario"
    assert spec["protocol"] in PROTOCOLS
    assert spec.get("repo", "sample") in {"sample", "tools"}
    assert spec["runs"], "a scenario runs Foundry at least once"
    for run in spec["runs"]:
        # `args` go to Foundry and may be none at all (the REPL, fed on stdin);
        # `argv` is some other program, so it names one.
        command = run["argv"] if "argv" in run else run["args"]
        assert isinstance(command, list) and all(isinstance(part, str) for part in command), run
        assert "argv" not in run or run["argv"], run
    for number, kinds in (spec.get("faults") or {}).items():
        assert number.isdigit() and set(kinds) <= FAULTS, (number, kinds)
    assert set(re.findall(r"\{([A-Z][A-Z0-9_]*)\}", text)) <= PLACEHOLDERS


def test_the_runner_substitutes_every_placeholder_a_scenario_uses():
    runner = (KIT / "run.py").read_text(encoding="utf-8")

    for name in sorted(PLACEHOLDERS):
        assert f'"{name}"' in runner or "{" + name + "}" in runner, name


def test_a_live_batch_names_only_scenarios_that_exist():
    tree = ast.parse((KIT / "live_batch.py").read_text(encoding="utf-8"))
    sets = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and node.targets[0].id == "SETS")

    assert set(sets) == {"p1", "p9-11"}
    for names in sets.values():
        for name in names:
            assert (KIT / "scenarios" / f"{name}.json").is_file(), name


def test_ci_runs_the_three_smoke_scenarios():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "verify/run.py --auto" in workflow
    for protocol in sorted(PROTOCOLS):
        assert f"verify/scenarios/smoke-{protocol}.json" in workflow


# -- what must not be in the folder -------------------------------------------

CREDENTIAL_SHAPES = {
    "an Anthropic key": r"sk-ant-[A-Za-z0-9_-]{8,}",
    "an OpenAI-style key": r"\bsk-[A-Za-z0-9]{20,}",
    "a GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    "an AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "a private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "a bearer token": r"Bearer\s+[A-Za-z0-9._~+/-]{20,}",
    "a JSON web token": r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.",
    "a canary from a run": r"\b(?:relay|service)-canary-[0-9a-f]{24}\b",
}
MACHINE_SHAPES = {
    "a Windows profile": r"[A-Za-z]:[\\/]+Users[\\/]",
    "a home folder": r"/(?:Users|home)/[A-Za-z]",
    "a drive path": r"\b[A-Za-z]:\\\\?projects\b",
    "an address": r"[\w.+-]+@(?!example\.(?:com|org)\b)[\w-]+\.[A-Za-z]{2,}",
}


def _found(shapes: dict[str, str]) -> list[str]:
    found = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        found += [f"{path.relative_to(ROOT).as_posix()}: {what}"
                  for what, shape in shapes.items() if re.search(shape, text)]
    return found


def test_nothing_in_the_kit_is_shaped_like_a_credential():
    assert _found(CREDENTIAL_SHAPES) == []


def test_nothing_in_the_kit_names_a_machine_or_a_person():
    assert _found(MACHINE_SHAPES) == []


@pytest.mark.parametrize("text, shapes, what", [
    ("key = 'sk-ant-api03-abcdefgh'", CREDENTIAL_SHAPES, "an Anthropic key"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwx", CREDENTIAL_SHAPES, "a bearer token"),
    ("relay-canary-0123456789abcdef01234567", CREDENTIAL_SHAPES, "a canary from a run"),
    (r"C:\Users\someone\AppData", MACHINE_SHAPES, "a Windows profile"),
    ("/home/someone/.config", MACHINE_SHAPES, "a home folder"),
    ("mail someone@corp.example.net", MACHINE_SHAPES, "an address"),
])
def test_the_shapes_looked_for_are_found_when_present(text, shapes, what):
    assert re.search(shapes[what], text)


@pytest.mark.parametrize("text", [
    'f"Bearer {TOKEN}"', "t@example.com", "scaffold@invalid", "sk-ant- is a prefix",
    "python -m pytest tests -q", "{HOME}/config.toml",
])
def test_what_the_kit_does_write_is_not_taken_for_one(text):
    for shape in (*CREDENTIAL_SHAPES.values(), *MACHINE_SHAPES.values()):
        assert not re.search(shape, text), shape


def test_the_kit_is_ascii_and_its_scripts_compile():
    for path in _files():
        data = path.read_bytes()
        assert data.isascii(), f"{path.relative_to(ROOT).as_posix()} is not ASCII"
        if path.suffix == ".py":
            compile(data.decode("ascii"), str(path), "exec")


def test_what_a_run_writes_is_ignored():
    ignored = (KIT / ".gitignore").read_text(encoding="utf-8").split()

    assert {"out/", "probes/projects/"} <= set(ignored)


# -- the relay ---------------------------------------------------------------

@pytest.fixture
def relay(tmp_path):
    module = _kit("relay")
    server = module.make_server(tmp_path, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    module.url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    module.dir = tmp_path
    yield module
    server.shutdown()
    server.server_close()


def _post(url: str, body: dict):
    """(status, headers, body bytes), through no proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer test"})
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


READ = {"content": "Let me look.", "reasoning": "",
        "tool_calls": [{"name": "read_file", "arguments": {"path": "README.md"}}]}
TOOL = {"name": "read_file", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}


def _answer_first(relay, reply=READ):
    """Have request 1's reply waiting, so the relay answers at once."""
    (relay.dir / "1.response.json").write_text(json.dumps(reply), encoding="utf-8")


def test_the_relay_answers_chat_completions_with_a_tool_call(relay):
    _answer_first(relay)

    status, _, raw = _post(relay.url + "/chat/completions", {
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": TOOL}]})

    choice = json.loads(raw)["choices"][0]
    assert status == 200 and choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "README.md"}
    assert json.loads((relay.dir / "1.meta.json").read_text(encoding="utf-8")) == {
        "protocol": "chat", "path": "/v1/chat/completions", "auth": "present"}


def test_the_relay_answers_responses_with_a_function_call(relay):
    _answer_first(relay)

    status, _, raw = _post(relay.url + "/responses", {
        "model": "m", "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", **TOOL}]})

    body = json.loads(raw)
    call = next(item for item in body["output"] if item["type"] == "function_call")
    assert status == 200 and body["status"] == "completed"
    assert call["name"] == "read_file" and call["call_id"]
    assert json.loads(call["arguments"]) == {"path": "README.md"}


def test_the_relay_answers_anthropic_messages_with_a_tool_use(relay):
    _answer_first(relay)

    status, _, raw = _post(relay.url + "/messages", {
        "model": "m", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "read_file", "description": "Read a file.",
                   "input_schema": TOOL["parameters"]}]})

    body = json.loads(raw)
    block = next(b for b in body["content"] if b["type"] == "tool_use")
    assert status == 200 and body["stop_reason"] == "tool_use"
    assert block["name"] == "read_file" and block["input"] == {"path": "README.md"}


def test_the_relay_streams_a_chat_tool_call_in_pieces_and_ends_with_done(relay):
    _answer_first(relay)

    _, headers, raw = _post(relay.url + "/chat/completions", {
        "model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": TOOL}]})

    lines = [line[6:] for line in raw.decode("utf-8").splitlines() if line.startswith("data: ")]
    assert headers["Content-Type"] == "text/event-stream" and lines[-1] == "[DONE]"
    pieces = [call["function"].get("arguments", "")
              for line in lines[:-1] for choice in json.loads(line)["choices"]
              for call in choice["delta"].get("tool_calls", [])]
    assert len(pieces) > 2, "the arguments arrive in more than one piece"
    assert json.loads("".join(pieces)) == {"path": "README.md"}


def test_the_relay_fails_the_attempts_it_is_told_to_then_serves(relay):
    _answer_first(relay)
    (relay.dir / "faults.json").write_text(json.dumps({"1": ["429", "500"]}), encoding="utf-8")
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": TOOL}]}

    first = _post(relay.url + "/chat/completions", body)
    second = _post(relay.url + "/chat/completions", body)
    third = _post(relay.url + "/chat/completions", body)

    assert first[0] == 429 and first[1]["Retry-After"] == "1"
    assert second[0] == 500 and third[0] == 200
    # The same bytes again are the same request: one file, three attempts.
    assert sorted(p.name for p in relay.dir.glob("*.request.json")) == ["1.request.json"]
    attempts = [json.loads(line) for line in
                (relay.dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(a["attempt"], a["fault"]) for a in attempts] == [(1, "429"), (2, "500"), (3, None)]


def test_a_cut_stream_stops_part_way_with_no_terminator(relay):
    _answer_first(relay)
    (relay.dir / "faults.json").write_text(json.dumps({"1": ["drop"]}), encoding="utf-8")

    _, _, raw = _post(relay.url + "/messages", {
        "model": "m", "stream": True, "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "read_file", "description": "d", "input_schema": TOOL["parameters"]}]})

    events = [line[7:] for line in raw.decode("utf-8").splitlines() if line.startswith("event: ")]
    assert events and events[0] == "message_start" and "message_stop" not in events


def test_a_reply_is_checked_against_what_the_request_offered(relay, tmp_path):
    done = []
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": TOOL}]}
    waiting = threading.Thread(
        target=lambda: done.append(_post(relay.url + "/chat/completions", body)), daemon=True)
    waiting.start()
    deadline = time.monotonic() + 10
    while not (relay.dir / "1.request.json").is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    reply = tmp_path / "reply.json"

    reply.write_text(json.dumps({"tool_calls": [{"name": "delete_everything"}]}),
                     encoding="utf-8")
    with pytest.raises(SystemExit, match="not among the tools this request offers"):
        relay.reply(relay.dir, 1, reply)
    reply.write_text(json.dumps({"content": "", "tool_calls": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="needs content or tool calls"):
        relay.reply(relay.dir, 1, reply)

    reply.write_text(json.dumps({"content": "Hello."}), encoding="utf-8")
    assert relay.reply(relay.dir, 1, reply) == 0
    waiting.join(timeout=10)
    assert done and done[0][0] == 200
    assert json.loads(done[0][2])["choices"][0]["message"]["content"] == "Hello."
    with pytest.raises(SystemExit, match="already answered"):
        relay.reply(relay.dir, 1, reply)


def test_what_the_player_is_shown_is_only_what_is_new(relay):
    first = {"model": "m", "messages": [{"role": "system", "content": "Be brief."},
                                        {"role": "user", "content": "What is here?"}],
             "tools": [{"type": "function", "function": TOOL}]}
    second = {**first, "messages": first["messages"] + [
        {"role": "assistant", "content": "Let me look.", "tool_calls": [
            {"id": "call_1_0", "type": "function",
             "function": {"name": "read_file", "arguments": "{\"path\": \"README.md\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1_0", "content": "# A project"}]}
    for number, body in ((1, first), (2, second)):
        (relay.dir / f"{number}.request.json").write_text(json.dumps(body), encoding="utf-8")
        (relay.dir / f"{number}.meta.json").write_text(
            json.dumps({"protocol": "chat", "path": "/v1/chat/completions"}), encoding="utf-8")

    whole, new = relay._render(relay.dir, 1), relay._render(relay.dir, 2)

    assert "Be brief." in whole and "## tools you can call" in whole
    assert "continues request 1" in new and "# A project" in new
    assert "Be brief." not in new and "## tools you can call" not in new


# -- the mutation check ------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    """A module with CRLF line endings and one test of it."""
    (tmp_path / "calc.py").write_bytes(
        b"def add(a, b):\r\n    return a + b\r\n\r\n\r\ndef spare(a):\r\n    return a\r\n")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8")
    return tmp_path


def _mutations(project, *mutations) -> str:
    path = project / "mutations.json"
    path.write_text(json.dumps(list(mutations)), encoding="utf-8")
    return str(path)


def test_a_mutation_is_killed_or_survives_and_the_file_comes_back(project, capsys):
    before = (project / "calc.py").read_bytes()
    listed = _mutations(
        project,
        {"name": "minus for plus", "file": "calc.py", "old": "a + b", "new": "a - b"},
        {"name": "what no test calls", "file": "calc.py",
         "old": "def spare(a):\n    return a", "new": "def spare(a):\n    return None"})

    status = _kit("mutate").main([listed, "test_calc.py", "--root", str(project)])

    out = capsys.readouterr().out
    assert status == 1
    assert " 1 killed: minus for plus" in out and " 2 SURVIVED: what no test calls" in out
    assert "survived: ['what no test calls']" in out
    assert (project / "calc.py").read_bytes() == before


def test_tests_that_fail_before_any_mutation_teach_nothing(project, capsys):
    (project / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 6\n",
        encoding="utf-8")
    listed = _mutations(project, {"name": "any", "file": "calc.py", "old": "a + b", "new": "0"})

    assert _kit("mutate").main([listed, "test_calc.py", "--root", str(project)]) == 2
    assert "do not pass before any mutation" in capsys.readouterr().out


def test_a_mutation_that_cannot_be_applied_stops_the_run(project, capsys):
    before = (project / "calc.py").read_bytes()
    listed = _mutations(project, {"name": "not there", "file": "calc.py",
                                  "old": "a * b", "new": "a - b"})

    assert _kit("mutate").main([listed, "test_calc.py", "--root", str(project)]) == 2
    assert "cannot apply: not there" in capsys.readouterr().out
    assert (project / "calc.py").read_bytes() == before


def test_a_run_can_begin_at_a_later_mutation(project, capsys):
    listed = _mutations(
        project,
        {"name": "minus for plus", "file": "calc.py", "old": "a + b", "new": "a - b"},
        {"name": "times for plus", "file": "calc.py", "old": "a + b", "new": "a * b"})

    status = _kit("mutate").main([listed, "test_calc.py", "--root", str(project), "--from", "2"])

    out = capsys.readouterr().out
    assert status == 0
    assert " 2 killed: times for plus" in out and "minus for plus" not in out


def test_a_mutation_is_matched_in_the_files_own_line_endings():
    mutated = _kit("mutate").mutated

    assert mutated(b"a\r\nb\r\n", "a\nb", "a\nc") == b"a\r\nc\r\n"
    assert mutated(b"a\nb\n", "a\nb", "a\nc") == b"a\nc\n"
    assert mutated(b"x x", "x", "y") == b"y x", "the first only"
    assert mutated(b"a\nb\n", "c", "d") is None


# -- a live run's key and cost ------------------------------------------------

@pytest.fixture
def live(monkeypatch, tmp_path):
    """live.py, with nothing of whoever runs the suite within its reach: no
    key in the environment, an empty folder for Foundry's, and a prompt that
    fails the test where it would have waited for someone to type."""
    import getpass

    module = _kit("live")
    monkeypatch.delenv(module.VARIABLE, raising=False)
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path / "foundry-home"))
    monkeypatch.setattr(getpass, "getpass", lambda prompt: pytest.fail("a key was asked for"))
    return module


def test_the_key_is_the_variables_when_it_is_set(live, monkeypatch):
    monkeypatch.setenv(live.VARIABLE, "  a-key-for-this-test  ")

    assert live.obtain() == "a-key-for-this-test"


def test_with_no_variable_the_key_is_what_foundry_login_stored(live, tmp_path):
    from foundry.core.auth import CredentialVault

    CredentialVault(tmp_path / "foundry-home" / "auth.json").save(
        {"anthropic_api_key": "a-key-foundry-stored", "api_key": "a-key-for-another-service"})

    assert live.obtain() == "a-key-foundry-stored"


def test_with_neither_the_key_is_typed_and_a_run_with_none_stops(live, monkeypatch):
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt: " typed-at-the-prompt ")

    assert live.obtain() == "typed-at-the-prompt"

    monkeypatch.setattr(getpass, "getpass", lambda prompt: "")
    with pytest.raises(SystemExit, match="no key entered"):
        live.obtain()


def test_a_folder_is_searched_for_the_key_itself(live, tmp_path):
    (tmp_path / "home" / "sessions").mkdir(parents=True)
    (tmp_path / "home" / "sessions" / "events.jsonl").write_text(
        '{"text": "Authorization: a-key-for-this-test"}\n', encoding="utf-8")
    (tmp_path / "run1.out").write_text("a-key-for-another-test\n", encoding="utf-8")

    assert live.scan(tmp_path, "a-key-for-this-test") == ["home/sessions/events.jsonl"]
    assert live.scan(tmp_path, "a-key-nobody-wrote") == []


def _journals(folder: Path) -> None:
    """Two sessions' journals: a million tokens in, 400,000 of them read from
    the cache, 100,000 out; and a record that is not about tokens."""
    for session, tokens in (("s1", (600_000, 300_000, 40_000)), ("s2", (400_000, 100_000, 60_000))):
        (folder / session).mkdir(parents=True)
        (folder / session / "events.jsonl").write_text(
            json.dumps({"type": "token_usage", "payload": {
                "input_tokens": tokens[0], "cached_input_tokens": tokens[1],
                "output_tokens": tokens[2]}}) + "\n"
            + json.dumps({"type": "notice", "payload": {"input_tokens": 9}}) + "\n",
            encoding="utf-8")


def test_the_tokens_used_are_what_the_journals_recorded(live, tmp_path):
    _journals(tmp_path)

    assert live.usage_of(tmp_path) == {
        "input_tokens": 1_000_000, "cached_input_tokens": 400_000,
        "output_tokens": 100_000, "records": 2}
    assert live.usage_of(tmp_path / "no-such-folder")["records"] == 0


@pytest.mark.parametrize("model, dollars", [
    # 600,000 fresh in, 400,000 from the cache, 100,000 out, at each model's
    # price per million: (in, out, cache read).
    ("claude-fable-5-2", 8.88),      # (8, 40, 0.20)
    ("claude-opus-5-5", 4.48),       # (4, 20, 0.20)
    ("claude-sonnet-5-5", 2.24),     # (2, 10, 0.10)
    ("claude-haiku-5-5", 0.114),     # (0.10, 0.50, 0.01)
    ("a-model-with-no-price", None),
])
def test_the_cost_is_those_tokens_at_the_models_prices(live, tmp_path, model, dollars):
    _journals(tmp_path)

    assert live.cost(model, live.usage_of(tmp_path)) == dollars


# -- the runner --------------------------------------------------------------

def test_a_live_configuration_names_the_variable_and_holds_no_key(scripts):
    runner = scripts("run")
    spec = {"protocol": "responses", "backend": 'effort = "high"\nrelay_only = 1\n'}

    def backend(mode, port=0):
        return tomllib.loads(runner.backend_config(spec, mode, port, "claude-haiku-5-5"))["backend"]

    assert backend("live") == {"protocol": "anthropic_messages", "model": "claude-haiku-5-5",
                               "env_key": "FOUNDRY_VERIFY_KEY", "effort": "high"}
    assert backend("live-chat") == {"protocol": "openai_compat",
                                    "base_url": "https://api.anthropic.com/v1",
                                    "model": "claude-haiku-5-5",
                                    "env_key": "FOUNDRY_VERIFY_KEY", "effort": "high"}
    # Offline it is the relay, whichever protocol the scenario names, and a
    # canary for a credential.
    assert backend("auto", 4321) == {
        "base_url": "http://127.0.0.1:4321/v1", "model": "relay-model", "protocol": "responses",
        "credential_source": "gateway_token", "stream_idle_timeout_ms": 1800000,
        "effort": "high", "relay_only": 1}


def test_a_verdict_names_each_thing_that_went_wrong(scripts):
    problems_of = scripts("run").problems_of
    spec = {"runs": [{"args": ["exec"]}, {"args": ["exec"], "expect_exit": 3}]}

    def summary(**changes):
        return {"canary_leaks": [], "runs": [{"run": 1, "exit": 0}, {"run": 2, "exit": 3}],
                **changes}

    assert problems_of(spec, summary(), "auto") == []
    assert problems_of(spec, summary(canary_leaks=["home/x.log"]), "auto") == [
        "a canary is in ['home/x.log']"]
    assert problems_of(spec, summary(key_files=["run1.out"]), "live") == [
        "the key is in ['run1.out']"]
    assert problems_of(spec, summary(check={"exit": 1, "tail": ""}), "relay") == [
        "the check failed"]
    assert problems_of(spec, summary(check={"exit": 0, "tail": ""}), "relay") == []
    wrong = summary(runs=[{"run": 1, "exit": 1}, {"run": 2, "exit": 0}])
    assert problems_of(spec, wrong, "auto") == ["run 1 exited 1, not 0", "run 2 exited 0, not 3"]
    # A model that answers for itself may end a run either way: only an exit
    # status the scenario names is held to.
    assert problems_of(spec, wrong, "live") == ["run 2 exited 0, not 3"]
    assert problems_of(spec, wrong, "relay") == ["run 2 exited 0, not 3"]


def test_the_runner_exits_1_if_any_verdict_is_fail_and_asks_for_a_key_only_live(
        scripts, monkeypatch):
    runner = scripts("run")
    problems = {"a.json": [], "b.json": ["the check failed"]}
    seen = []

    def run_scenario(path, mode, out, key=None):
        seen.append((path.name, mode, key))
        return {"name": path.stem, "problems": problems[path.name]}

    monkeypatch.setattr(runner, "run_scenario", run_scenario)
    monkeypatch.setattr(runner.live, "obtain", lambda: pytest.fail("no key is needed offline"))

    assert runner.main(["a.json", "--auto"]) == 0
    assert runner.main(["a.json", "b.json"]) == 1

    monkeypatch.setattr(runner.live, "obtain", lambda: "a-key-for-this-test")
    assert runner.main(["a.json", "--live-chat"]) == 0
    assert seen == [("a.json", "auto", None), ("a.json", "relay", None), ("b.json", "relay", None),
                    ("a.json", "live-chat", "a-key-for-this-test")]


def test_the_scripted_model_reads_then_finishes_and_answers_in_text_when_it_must(
        scripts, tmp_path):
    auto = scripts("autoreply")
    tools = [{"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
             for name in ("read_file", "finish")]
    asked = {"model": "m", "messages": [{"role": "user", "content": "What is here?"}],
             "tools": tools}
    told = {**asked, "messages": asked["messages"] + [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "# A project"}]}
    bare = {"model": "m", "messages": asked["messages"]}

    replies = []
    for number, body in enumerate((asked, told, bare), 1):
        (tmp_path / f"{number}.request.json").write_text(json.dumps(body), encoding="utf-8")
        (tmp_path / f"{number}.meta.json").write_text(
            json.dumps({"protocol": "chat", "path": "/v1/chat/completions"}), encoding="utf-8")
        auto.answer(tmp_path, number)
        replies.append(json.loads(
            (tmp_path / f"{number}.response.json").read_text(encoding="utf-8")))

    assert replies[0]["tool_calls"] == [{"name": "read_file", "arguments": {"path": "README.md"}}]
    assert [call["name"] for call in replies[1]["tool_calls"]] == ["finish"]
    assert replies[1]["tool_calls"][0]["arguments"]["status"] == "completed"
    assert replies[2]["tool_calls"] == [] and replies[2]["content"]


# -- what a batch spends -----------------------------------------------------

def test_a_batch_stops_before_the_scenario_that_would_start_past_its_budget(
        scripts, monkeypatch, tmp_path, capsys):
    batch = scripts("live_batch")
    started = []

    def run(argv, env, **_):
        started.append((argv, env))
        summary = {"name": "any", "base": "where/it/ran", "problems": [],
                   "runs": [{"exit": 0, "termination": [], "tools": ["read_file:ok"]}],
                   "usage": {"estimated_usd": 0.3}}
        return types.SimpleNamespace(
            stdout="READY relay dir: r\nrun 1 ['exec'] -> exit 0 in 1s\n"
                   + json.dumps(summary, indent=1) + "\nVERDICT any: OK\n",
            stderr="", returncode=0)

    monkeypatch.setattr(batch, "HERE", tmp_path)
    monkeypatch.setattr(batch.subprocess, "run", run)
    monkeypatch.setattr(batch.live, "obtain", lambda: "a-key-for-this-test")

    assert batch.main(["--set", "p1", "--chat", "--budget", "0.5"]) == 0

    # 0.30, then 0.60: the third scenario is not started.
    assert [Path(argv[2]).stem for argv, _ in started] == ["s1-chat-fix", "s2-resp-feature"]
    argv, env = started[0]
    assert "--live-chat" in argv and "--live" not in argv
    assert env[batch.live.VARIABLE] == "a-key-for-this-test"
    assert "a-key-for-this-test" not in " ".join(argv)
    lines = [json.loads(line) for line in (tmp_path / "out" / "live_results-p1-chat.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    assert [line["name"] for line in lines] == ["s1-chat-fix", "s2-resp-feature"]
    assert lines[0]["usage"] == {"estimated_usd": 0.3} and lines[0]["problems"] == []
    assert lines[0]["runs"] == [{"exit": 0, "termination": [], "tools": 1}]
    assert ("stopping before s3-anth-resume: about $0.60 spent, the budget is $0.50"
            in capsys.readouterr().out)
    assert batch.live.scan(tmp_path, "a-key-for-this-test") == [], "the key is in no file"


def test_the_probe_runner_stops_at_its_budget_and_counts_what_passed(
        scripts, monkeypatch, tmp_path, capsys):
    runner = scripts("probes/run_auto")
    for name in ("a", "b"):
        (tmp_path / "projects" / name).mkdir(parents=True)
    ran = []

    def one(src, out, model, mode, key):
        ran.append((src.name, out.name, model, key))
        return {"status": "completed", "hidden_exit": 0 if src.name == "a" else 1,
                "tool_calls": 3, "rejected": [], "patch_failures": [], "compactions": 0,
                "estimated_usd": 0.3, "key_files": []}

    monkeypatch.setattr(runner, "one", one)
    monkeypatch.setattr(runner.live, "obtain", lambda: "a-key-for-this-test")
    where = ["--projects", str(tmp_path / "projects"), "--out", str(tmp_path / "out")]

    assert runner.main(["three-times", "--model", "claude-haiku-5-5", "--repeat", "2",
                        "--budget", "0.5", *where]) == 0

    assert ran == [("a", "a.1", "claude-haiku-5-5", "a-key-for-this-test"),
                   ("a", "a.2", "claude-haiku-5-5", "a-key-for-this-test")]
    assert ("stopping before b.1: about $0.60 spent, the budget is $0.50"
            in capsys.readouterr().out)
    results = json.loads((tmp_path / "out" / "three-times" / "results.json")
                         .read_text(encoding="utf-8"))
    assert (results["runs"], results["passed"], results["estimated_usd"]) == (2, 2, 0.6)

    del ran[:]
    assert runner.main(["once", *where]) == 0

    assert [(name, label) for name, label, _, _ in ran] == [("a", "a"), ("b", "b")]
    results = json.loads((tmp_path / "out" / "once" / "results.json").read_text(encoding="utf-8"))
    assert (results["runs"], results["passed"]) == (2, 1)
    assert "1 of 2 passed their hidden tests" in capsys.readouterr().out
