"""What the user approves is what runs, and "always" means exactly it (D-151).

P2's review found approvals that could be given to something unseen:

* `foundry mcp-server` cut the whole question at 4,000 characters, the
  command included, so a command padded with blanks past the cut was
  approved without its tail; a carriage return in it reached the client.
* An MCP call's display stops at 300 characters and the terminal showed only
  the display, so an argument past them was approved unseen.
* Padding hid a command's tail in the terminal too, below the fold.
* "always" wrote the call's target as a pattern, so a `*` in what the model
  sent became a wildcard: `{"query": "*"}` approved every later call.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from rich.console import Console

from foundry.cli import app, mcp_server
from foundry.cli.mcp_server import MAX_QUESTION_CHARS, MAX_SHOWN_CHARS, _question
from foundry.cli.render import Renderer, visible
from foundry.core.config import load_config
from foundry.core.events import ApprovalChoice, ApprovalKind, ApprovalRequest
from foundry.core.policy.engine import Decision, PolicyEngine, Verdict
from foundry.core.tools.base import Operation, ToolKind

from test_mcp_elicitation import _asking, _questions  # noqa: F401
from test_mcp_server import _finish, home, model  # noqa: F401  (fixtures)
from test_cli_e2e import tool_turn
from test_resume import _wire, repo, turn  # noqa: F401  (fixture)

PADDED = "Write-Output 'running the unit tests'" + " " * 4100 + "; Set-Content hidden.txt TAIL"
WIRING = SimpleNamespace(session=SimpleNamespace(session_id="s1"),
                         workspace=SimpleNamespace(root="C:/repo"))


def _request(display, detail="", kind=ApprovalKind.COMMAND, tool="run_command"):
    return ApprovalRequest(request_id="r1", approval_kind=kind, tool=tool, display=display,
                           reason="no rule allows it", detail=detail)


# --- nothing hidden in the layout --------------------------------------------------


def test_a_run_of_blanks_is_counted_in_its_place():
    assert visible("a" + " " * 40 + "b") == "a [40 blanks] b"
    assert visible("a" + "\t " * 10 + "b") == "a [20 blanks] b"
    # Indentation and ordinary spacing are left as they are.
    assert visible("if x:\n        y = 1") == "if x:\n        y = 1"


def test_a_run_of_empty_lines_is_counted_and_returns_are_line_ends():
    assert visible("a\n\n\n\n\nb") == "a\n[4 empty lines]\nb"
    assert visible("a\n\nb") == "a\n\nb"
    assert visible("safe\rrm -r ~") == "safe\nrm -r ~"
    assert visible("x\r\ny") == "x\ny"


# --- the client's question ----------------------------------------------------------


def test_what_would_run_is_never_cut():
    text = _question(_request(PADDED), WIRING, None)
    assert "[4100 blanks] ; Set-Content hidden.txt TAIL" in text
    assert "It asks because: no rule allows it" in text


def test_what_would_run_is_shown_whole_past_the_question_s_length():
    long = "Write-Output " + "x" * (MAX_QUESTION_CHARS + 500) + "; Set-Content hidden.txt TAIL"
    text = _question(_request(long), WIRING, None)
    assert len(text) > MAX_QUESTION_CHARS
    assert text.endswith("It asks because: no rule allows it")
    assert "; Set-Content hidden.txt TAIL" in text


def test_a_call_too_long_to_show_whole_is_not_asked_about():
    assert _question(_request("x" * (MAX_SHOWN_CHARS + 1)), WIRING, None) is None


def test_no_carriage_return_reaches_the_client():
    assert "\r" not in _question(_request("echo safe\rdel *"), WIRING, None)


def test_the_arguments_are_shown_whole_when_the_display_is_a_cut():
    args = json.dumps({"query": "q" * 400, "upload": "SECRET-TAIL"}, indent=2)
    text = _question(_request("docs: search {...", args, ApprovalKind.OTHER, "mcp__docs__search"),
                     WIRING, None)
    assert "With these arguments:" in text and "SECRET-TAIL" in text


def test_the_server_refuses_what_it_cannot_show(home, repo, monkeypatch):
    asking = _asking(home, repo)
    monkeypatch.setattr(mcp_server, "MAX_SHOWN_CHARS", 50)
    notices = []
    runtime = SimpleNamespace(scrub=None, _cancelled=False,
                              events=SimpleNamespace(emit=notices.append))
    entry = SimpleNamespace(wiring=SimpleNamespace(runtime=runtime, **vars(WIRING)))
    try:
        assert asking.server._ask(entry, _request("y" * 100)) is ApprovalChoice.DENY
    finally:
        asking.server.close()
    assert not [m for m in asking.got if m.get("method") == "elicitation/create"]
    assert "too long to show in full" in notices[0].text


# --- the terminal ---------------------------------------------------------------------


def _shown(request) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    console.input = lambda prompt="": "n"
    Renderer(console=console).ask_approval(request)
    return buffer.getvalue()


def test_the_terminal_shows_a_padded_command_s_tail_in_sight():
    shown = _shown(_request(PADDED))
    assert "[4100 blanks]" in shown and "TAIL" in shown
    assert len(shown.splitlines()) < 20


def test_the_terminal_shows_the_arguments_a_display_cut():
    shown = _shown(_request("docs: search {...", '{\n  "upload": "SECRET-TAIL"\n}',
                            ApprovalKind.OTHER, "mcp__docs__search"))
    assert "with these arguments" in shown and "SECRET-TAIL" in shown


def _asked_with(repo, home, op) -> ApprovalRequest:
    wiring = _wire(repo, home, [turn(text="unused")])
    seen = []
    wiring.runtime.approval = lambda request: (seen.append(request), ApprovalChoice.DENY)[1]
    wiring.runtime._ask(op, Decision(verdict=Verdict.ASK, reason="no rule matched"))
    return seen[0]


def _mcp_op(args):
    text = json.dumps(args, sort_keys=True, ensure_ascii=False)
    shown = text if len(text) <= 300 else text[:297] + "..."
    return Operation(tool="mcp__docs__search", kind=ToolKind.MUTATOR, args=args,
                     display=f"docs: search {shown}", target=text)


def test_an_mcp_call_cut_in_its_display_is_asked_with_its_arguments(repo, tmp_path):
    long = _asked_with(repo, tmp_path / "home",
                       _mcp_op({"query": "q" * 300, "zz": "SECRET-TAIL"}))
    assert "SECRET-TAIL" not in long.display and "SECRET-TAIL" in long.detail
    short = _asked_with(repo, tmp_path / "home2", _mcp_op({"query": "q"}))
    assert short.detail == ""


def test_a_command_is_asked_with_no_second_copy(repo, tmp_path):
    op = Operation(tool="run_command", kind=ToolKind.MUTATOR, args={"command": "git push"},
                   display="git push", target="git push")
    assert _asked_with(repo, tmp_path / "home", op).detail == ""


# --- always ---------------------------------------------------------------------------


def test_always_allows_exactly_the_call_approved(tmp_path):
    home = tmp_path / "home"
    policy = PolicyEngine(rules=[])
    approved = '{"query": "*"}'
    app._rule_writer(home, policy)(SimpleNamespace(tool="mcp__docs__search", target=approved))
    [rule] = [r for r in load_config(home=home).rules if r.tool == "mcp__docs__search"]
    assert rule.pattern == '{"query": "[*]"}'

    def allowed(target):
        op = Operation(tool="mcp__docs__search", kind=ToolKind.MUTATOR, args={},
                       display="x", target=target)
        return rule.matches(op)

    assert allowed(approved)
    assert not allowed('{"query": "x", "upload": "key"}')
    for special in ("a?b", "[ab]"):
        exact = f'{{"query": "{special}"}}'
        app._rule_writer(home, policy)(SimpleNamespace(tool="t", target=exact))
    rules = {r.pattern for r in load_config(home=home).rules if r.tool == "t"}
    assert rules == {'{"query": "a[?]b"}', '{"query": "[[]ab]"}'}
