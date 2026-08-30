"""Round-six findings: coherence gaps left by five rounds of patching.

The theme is settings and guarantees that were declared, documented, and
protected -- but never actually connected to anything.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time

import pytest
from rich.console import Console

from foundry.core.events import (
    ApprovalChoice,
    ApprovalKind,
    ApprovalRequest,
    ErrorEvent,
    EventSink,
    MessageDelta,
    Notice,
    TerminalStatus,
    Termination,
    ToolBegin,
    ToolEnd,
)
from foundry.core.redaction import Redactor
from foundry.core.winapi import decode_output

CANARY = "sk-canaryLEAK0123456789abcdefXYZ"


# --- credentials do not leave the runtime in an event ---------------------


def _sink_with_canary():
    redactor = Redactor()
    redactor.register(CANARY)
    sink = EventSink(redactor=redactor)
    seen = []
    sink.subscribe(seen.append)
    return sink, seen


@pytest.mark.parametrize("event, field", [
    (MessageDelta(text=f"the key is {CANARY}"), "text"),
    (ToolBegin("c1", "run_command", f"echo {CANARY}"), "display"),
    (ToolEnd("c1", "run_command", True, f"exit 0: {CANARY}"), "summary"),
    (Notice(f"using {CANARY}"), "text"),
    (ErrorEvent(f"auth failed for {CANARY}"), "message"),
    (Termination(TerminalStatus.COMPLETED, "done", f"used {CANARY}"), "summary"),
])
def test_a_credential_never_reaches_a_subscriber(event, field):
    """The journal wrote [redacted] while `foundry exec --json` printed the same
    credential verbatim, so redirecting a run into a CI log captured exactly
    what the audited journal refused to keep."""
    sink, seen = _sink_with_canary()
    sink.emit(event)
    sink.flush()   # text deltas are held until the stream moves on
    assert CANARY not in getattr(seen[0], field)
    assert "[redacted]" in getattr(seen[0], field)


def test_redaction_does_not_damage_enum_fields():
    """ApprovalChoice and TerminalStatus are str subclasses; rewriting one into
    a plain string would break every subscriber comparing against the enum."""
    sink, seen = _sink_with_canary()
    sink.emit(ApprovalRequest(request_id="r1", approval_kind=ApprovalKind.COMMAND,
                              tool="run_command", display=f"echo {CANARY}",
                              reason="not allowlisted"))
    request = seen[0]
    assert CANARY not in request.display
    assert request.approval_kind is ApprovalKind.COMMAND
    assert request.options[0] is ApprovalChoice.ONCE
    assert request.kind == "approval_request"


def test_a_credential_split_across_deltas_is_still_removed():
    """Scrubbing each delta on its own is not enough. The model's text arrives
    in arbitrary chunks, so an echoed credential almost always straddles two of
    them, neither fragment matches, and the renderer reassembles it on screen --
    which is exactly how it reached the terminal in the clean-install run."""
    sink, seen = _sink_with_canary()
    for i in range(0, len(CANARY), 7):
        sink.emit(MessageDelta(text=CANARY[i:i + 7]))
    sink.emit(Termination(TerminalStatus.COMPLETED, "done"))

    reassembled = "".join(e.text for e in seen if isinstance(e, MessageDelta))
    assert CANARY not in reassembled
    assert "[redacted]" in reassembled


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 13, 64])
def test_no_chunk_size_lets_a_credential_through(chunk):
    sink, seen = _sink_with_canary()
    text = f"before {CANARY} after"
    for i in range(0, len(text), chunk):
        sink.emit(MessageDelta(text=text[i:i + chunk]))
    sink.flush()

    reassembled = "".join(e.text for e in seen if isinstance(e, MessageDelta))
    assert CANARY not in reassembled
    assert reassembled.startswith("before ") and reassembled.endswith(" after")


def test_held_text_is_released_in_order_before_the_next_event():
    sink, seen = _sink_with_canary()
    sink.emit(MessageDelta(text="thinking about it"))
    sink.emit(ToolBegin("c1", "read_file", "read app.py"))

    kinds = [type(e).__name__ for e in seen]
    assert kinds == ["MessageDelta", "ToolBegin"], kinds
    assert seen[0].text == "thinking about it"


def test_ordinary_text_is_not_swallowed():
    sink, seen = _sink_with_canary()
    sink.emit(MessageDelta(text="a" * 500))
    sink.flush()
    assert "".join(e.text for e in seen) == "a" * 500


def test_a_sink_without_a_redactor_still_works():
    sink = EventSink()
    seen = []
    sink.subscribe(seen.append)
    sink.emit(Notice("plain"))
    assert seen[0].text == "plain"


def test_the_wiring_actually_connects_the_redactor(tmp_path, monkeypatch):
    """The isolated sink test passes whether or not build() ever hands it a
    redactor; this is the half that was missing."""
    from rich.console import Console

    from foundry.cli.app import build
    from foundry.core.redaction import default_redactor
    from foundry.core.tools.git import run_git

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init"], ["config", "user.email", "t@e.com"],
                 ["config", "user.name", "T"], ["add", "-A"], ["commit", "-m", "i"]):
        run_git(args, repo)

    monkeypatch.setenv("OPENAI_API_KEY", CANARY)
    default_redactor().register(CANARY)
    log = tmp_path / "o.txt"
    wiring = build(repo, home=tmp_path / "home", interactive=False,
                   console=Console(file=open(log, "w", encoding="utf-8")))
    try:
        seen = []
        wiring.runtime.events.subscribe(seen.append)
        wiring.runtime.events.emit(Notice(f"connecting with {CANARY}"))
        assert CANARY not in seen[0].text
    finally:
        wiring.session.close()
        default_redactor().clear()


# --- one bad byte does not rewrite the whole stream -----------------------


def test_damaged_utf8_stays_utf8():
    """The fallbacks are code pages that map all 256 byte values and can never
    produce a U+FFFD, so the old "fewer replacements wins" comparison handed the
    whole output to the model as mojibake over a single stray byte."""
    raw = "FAILED tests/test_café.py -- 断言失败\n".encode("utf-8")
    decoded = decode_output(raw + b"\x81")
    assert decoded.encoding == "utf-8"
    assert "café" in decoded.text
    assert "断言失败" in decoded.text


@pytest.mark.parametrize("cut", [1, 2, 3])
def test_a_capture_cut_mid_character_keeps_the_text(cut):
    """_drain slices at the byte cap with chunk[:room], so a multi-byte
    character is split by construction on every large non-ASCII output."""
    raw = "结果: 全部通过 ✅".encode("utf-8")
    decoded = decode_output(raw[:-cut])
    assert decoded.encoding == "utf-8"
    assert decoded.text.startswith("结果: 全部通过")


def test_a_genuine_legacy_stream_still_switches_codec():
    decoded = decode_output("断言失败\n".encode("cp936"))
    assert decoded.encoding != "utf-8"


def test_clean_utf8_and_ascii_are_untouched():
    assert decode_output(b"hello\n") == decode_output(b"hello\n")
    assert decode_output("héllo 世界\n".encode("utf-8")).text == "héllo 世界\n"
    assert decode_output(b"").text == ""


# --- the approval prompt shows the keys it accepts ------------------------


def test_the_approval_prompt_is_not_eaten_by_markup():
    """rich reads [y] as a style tag and deletes it, so the prompt rendered as
    'allow? es / ession / alays / o / bort:' -- four truncated words and no sign
    of which key to press, on the surface the safety model depends on."""
    from foundry.cli.render import Renderer

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    console.input = lambda prompt="": (console.print(prompt), "y")[1]

    renderer = Renderer(console=console)
    choice = renderer.ask_approval(ApprovalRequest(
        request_id="r1", approval_kind=ApprovalKind.COMMAND, tool="run_command",
        display="pytest -q", reason="not allowlisted"))

    rendered = buffer.getvalue()
    assert choice is ApprovalChoice.ONCE
    for key in ("(y)es", "(s)ession", "al(w)ays", "(n)o", "(a)bort"):
        assert key in rendered, f"{key} missing from: {rendered!r}"


def test_ctrl_c_aborts_the_task_rather_than_one_operation():
    """Mapping KeyboardInterrupt to DENY denied one operation and let the loop
    resample, burning tokens against an explicit stop signal."""
    from foundry.cli.render import Renderer

    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    console.input = interrupt
    renderer = Renderer(console=console)
    assert renderer.ask_approval(ApprovalRequest(
        request_id="r1", approval_kind=ApprovalKind.COMMAND, tool="run_command",
        display="pytest -q", reason="not allowlisted")) is ApprovalChoice.ABORT


def test_eof_still_fails_closed():
    from foundry.cli.render import Renderer

    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    def eof(prompt=""):
        raise EOFError

    console.input = eof
    assert Renderer(console=console).ask_approval(ApprovalRequest(
        request_id="r1", approval_kind=ApprovalKind.COMMAND, tool="run_command",
        display="pytest -q", reason="not allowlisted")) is ApprovalChoice.DENY


# --- the configured timeout ceiling actually bounds a command -------------


def test_the_configured_ceiling_bounds_a_command(tmp_path):
    """command_timeout_s was type-checked, provenance-tracked and protected by
    the tighten-only rule -- and read by nothing, so an operator who lowered it
    to bound runaway commands got no bound at all."""
    from foundry.core.session import ArtifactStore
    from foundry.core.tools.base import ReadTracker, ToolContext
    from foundry.core.tools.command import RunCommand
    from foundry.core.workspace import Workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = ToolContext(workspace=Workspace(repo), artifacts=ArtifactStore(tmp_path / "a"),
                      read_tracker=ReadTracker(), max_command_timeout_s=3)

    tool = RunCommand()
    op = tool.validate({"command": 'python -c "import time; time.sleep(30)"',
                        "timeout_s": 600})
    started = time.monotonic()
    out = tool.execute(op, ctx)
    elapsed = time.monotonic() - started

    assert elapsed < 20, f"the ceiling did not bind: ran {elapsed:.1f}s"
    assert "TIMED OUT after 3s" in out.content
    assert "ceiling is 3s" in out.content, "the model should learn why it was cut short"


def test_without_a_ceiling_the_tool_default_still_applies(tmp_path):
    from foundry.core.tools.command import DEFAULT_TIMEOUT_S, RunCommand

    op = RunCommand().validate({"command": "echo hi"})
    assert op.args["timeout_s"] == DEFAULT_TIMEOUT_S


def test_the_config_ceiling_ships_at_the_tools_own_maximum():
    """Stock behaviour must be unchanged: a lower shipped default would silently
    cap every long build the moment this became a real ceiling."""
    from foundry.core.config import Config
    from foundry.core.tools.command import MAX_TIMEOUT_S

    assert Config().command_timeout_s == MAX_TIMEOUT_S


# --- the branch name is the branch name ----------------------------------


def _repo(tmp_path, *extra_init):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", *extra_init], cwd=repo, capture_output=True)
    for args in (["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)
    return repo


def test_an_unborn_branch_reports_its_real_name(tmp_path):
    """rev-parse --abbrev-ref fails on an unborn branch, so the "(detached)"
    fallback fired where the name was actually known: a fresh `git init -b main`
    told the model "Git branch: (detached)"."""
    from foundry.core.tools.git import capture_baseline

    baseline = capture_baseline(_repo(tmp_path, "-b", "main"))
    assert baseline.branch.startswith("main")
    assert "no commits" in baseline.branch


def test_a_detached_head_is_reported_as_detached(tmp_path):
    """The same call *succeeds* on a real detached HEAD, printing the literal
    string "HEAD" -- which read as a branch named HEAD."""
    from foundry.core.tools.git import capture_baseline

    repo = _repo(tmp_path, "-b", "main")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=repo, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "--detach", head], cwd=repo,
                   capture_output=True)

    assert capture_baseline(repo).branch == "(detached)"


def test_an_ordinary_branch_is_reported_plainly(tmp_path):
    from foundry.core.tools.git import capture_baseline

    repo = _repo(tmp_path, "-b", "main")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=repo, capture_output=True)

    assert capture_baseline(repo).branch == "main"


# --- streaming streams ----------------------------------------------------


def test_deltas_arrive_before_the_stream_ends():
    """list() made the stream retryable but stopped it being a stream: nothing
    reached the renderer until the model had finished generating."""
    from foundry.core.httpc import open_retrying_stream

    produced: list[int] = []

    def source():
        for i in range(3):
            produced.append(i)
            yield {"n": i}

    stream = open_retrying_stream(source, attempts=2)
    first = next(stream)

    assert first == {"n": 0}
    assert produced == [0], f"the whole stream was drained first: {produced}"
    assert [e["n"] for e in stream] == [1, 2]


def test_a_transient_failure_before_the_first_event_is_retried():
    from foundry.core.errors import TransientError
    from foundry.core.httpc import open_retrying_stream

    attempts = {"n": 0}

    def source():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TransientError("429 slow down")
        yield {"ok": True}

    events = list(open_retrying_stream(source, attempts=3, sleep=lambda _: None))
    assert events == [{"ok": True}]
    assert attempts["n"] == 2


def test_an_empty_stream_is_not_an_error():
    from foundry.core.httpc import open_retrying_stream

    assert list(open_retrying_stream(lambda: iter(()), attempts=2)) == []


# --- the journal says which vintage produced it ---------------------------


def test_the_session_header_carries_every_version_stamp(tmp_path):
    from foundry.core.conversation import IR_VERSION
    from foundry.core.events import PROTOCOL_VERSION
    from foundry.core.session import SCHEMA_VERSION, SessionStore

    session = SessionStore(tmp_path / "sessions")
    session.write_header(workspace="w", profile="p", model="m", foundry_version="0.1")
    session.close()

    header = json.loads(session.path.read_text(encoding="utf-8").splitlines()[0])
    payload = header["payload"]
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["ir_version"] == IR_VERSION
    assert payload["protocol_version"] == PROTOCOL_VERSION


def test_the_separator_constant_drives_the_split():
    """It declared the separator set beside a hardcoded duplicate, so editing it
    changed nothing while reading as the place separators are configured."""
    from foundry.core.policy import segmenter

    assert set(segmenter._SEPARATORS) >= {"&&", "||", ";", "|", "\n", "\r"}
    assert segmenter._SEPARATORS.index("&&") < segmenter._SEPARATORS.index("|")
    assert len(segmenter.segment_command("git status && git log").segments) == 2
    assert len(segmenter.segment_command("a | b ; c").segments) == 3


def test_login_refuses_rather_than_hanging_without_a_terminal(tmp_path, monkeypatch,
                                                              capsys):
    """getpass.win_getpass reads the console through msvcrt and ignores stdin,
    so the EOFError handler could never fire and a piped login hung forever.
    getpass must not be reached at all when there is no terminal."""
    import argparse
    import getpass

    from foundry.cli.app import cmd_login

    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(getpass, "getpass",
                        lambda *a, **k: pytest.fail("getpass was reached anyway"))

    assert cmd_login(argparse.Namespace()) == 1
    assert "not a terminal" in capsys.readouterr().out
    assert not (tmp_path / "auth.json").exists()


def test_login_stores_the_credential_the_configured_source_reads(tmp_path, monkeypatch):
    """login always wrote {"api_key": ...} while a gateway profile reads
    {"token": ...}, so it reported "saved" and the next run raised an AuthError
    telling the user to run login."""
    import argparse
    import getpass

    from foundry.core.auth import CredentialVault, StaticTokenSource
    from foundry.cli.app import cmd_login

    (tmp_path / "config.toml").write_text(
        '[backend]\ncredential_source = "gateway_token"\n', encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path))
    monkeypatch.delenv("FOUNDRY_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: "tok-abcdef123456")

    assert cmd_login(argparse.Namespace()) == 0
    handle = StaticTokenSource(CredentialVault(tmp_path / "auth.json")).acquire()
    assert handle.reveal() == "tok-abcdef123456"
    assert handle.label == "gateway token"
