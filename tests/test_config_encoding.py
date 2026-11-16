"""A configuration file read as Windows editors and shells save it (A2).

Notepad, and PowerShell 5.1's `Set-Content -Encoding utf8`, begin a UTF-8
file with a byte-order mark, which tomllib called a syntax error on line 1.
PowerShell 5.1's `>` and `Out-File` write UTF-16, which ended in a
traceback. A mark is taken off now; UTF-16, or text in the system's code
page, is said for what it is, with how to save the file again. Every file
Foundry reads as configuration goes through one reader: the user's, the
repository's and the machine's config.toml, a hooks.json beside one, and
the user's file when it is edited (`foundry mcp add`, `foundry features`,
"always").
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from foundry.cli import app
from foundry.cli.wiring import _rule_writer
from foundry.core.config import load_config
from foundry.core.config_edit import add_server, set_feature
from foundry.core.config_text import read_config_text
from foundry.core.errors import ConfigError
from foundry.core.events import ApprovalChoice, Notice
from foundry.core.hooks import read_hooks_file
from foundry.core.policy.engine import PolicyEngine

from test_runtime import call, harness, turn  # noqa: F401  (fixture)

BOM = b"\xef\xbb\xbf"
TEXT = '[backend]\nmodel = "gpt-test"\n\n[runtime]\nmax_tool_rounds = 7\n'


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    """No machine policy but the test's own."""
    folder = tmp_path / "programdata"
    monkeypatch.setenv("PROGRAMDATA", str(folder))
    return folder / "Foundry"


def _home(tmp_path, data: bytes):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_bytes(data)
    return home


def _utf16(text: str, *, mark: bool = True) -> bytes:
    """As PowerShell 5.1's `>` writes it: UTF-16 LE, with a mark."""
    return text.encode("utf-16" if mark else "utf-16-le")


def test_a_mark_is_taken_off_in_every_layer(tmp_path, machine):
    config = load_config(home=_home(tmp_path, BOM + TEXT.encode()))
    assert config.backend.model == "gpt-test" and config.max_tool_rounds == 7

    repo = tmp_path / "repo"
    (repo / ".foundry").mkdir(parents=True)
    (repo / ".foundry" / "config.toml").write_bytes(BOM + b"[runtime]\nmax_tool_rounds = 5\n")
    machine.mkdir(parents=True)
    (machine / "policy.toml").write_bytes(
        BOM + b'[[permissions]]\ntool = "run_command"\npattern = "git push*"\ndecision = "deny"\n')

    config = load_config(repo, home=tmp_path / "home")
    assert config.max_tool_rounds == 5
    assert [r.pattern for r in config.rules if r.tool == "run_command"] == ["git push*"]


@pytest.mark.parametrize("data", [_utf16(TEXT), _utf16(TEXT, mark=False),
                                  _utf16(TEXT).replace(b"\n\x00", b"\r\x00\n\x00")],
                         ids=["marked", "unmarked", "crlf"])
def test_utf16_is_named_with_how_to_save_it_again(tmp_path, data):
    home = _home(tmp_path, data)

    with pytest.raises(ConfigError) as caught:
        load_config(home=home)

    message = str(caught.value)
    assert f"{home / 'config.toml'} is saved as UTF-16, and Foundry reads UTF-8" in message
    assert "Notepad, File > Save as, Encoding: UTF-8" in message
    assert (f"(Get-Content -LiteralPath '{home / 'config.toml'}') | Set-Content -Encoding utf8 "
            f"-LiteralPath '{home / 'config.toml'}'") in message


def test_the_system_code_page_is_named_with_its_line(tmp_path):
    # Set-Content's default in PowerShell 5.1: the ANSI code page, where an
    # accented letter is one byte that UTF-8 does not allow alone.
    home = _home(tmp_path, ("# caf\u00e9\n" + TEXT).encode("cp1252"))

    with pytest.raises(ConfigError, match=r"is not UTF-8 \(byte 0xE9 on line 1\), as a file "
                                          r"saved in the system's code page is not: save it"):
        load_config(home=home)

    home = _home(tmp_path, (TEXT + 'name = "caf\u00e9"\n').encode("cp1252"))
    with pytest.raises(ConfigError, match=r"byte 0xE9 on line 6\)"):
        load_config(home=home)


def test_saved_again_as_powershell_says_the_file_loads(tmp_path):
    # What `Set-Content -Encoding utf8` writes in PowerShell 5.1: UTF-8 with
    # a mark, CRLF line ends.
    home = _home(tmp_path, BOM + TEXT.replace("\n", "\r\n").encode())
    assert load_config(home=home).backend.model == "gpt-test"


def test_a_toml_error_still_says_where(tmp_path):
    with pytest.raises(ConfigError, match=r"cannot read .*config\.toml: .*line 2"):
        load_config(home=_home(tmp_path, BOM + b"[backend]\nmodel = \n"))


def test_the_reader_alone(tmp_path):
    path = tmp_path / "f.toml"
    path.write_bytes(BOM + "a = '\u00e9'\n".encode())
    assert read_config_text(path) == "a = '\u00e9'\n"
    # A mark only at the start is one.
    path.write_bytes(b"a = 1\n" + BOM)
    assert read_config_text(path) == "a = 1\n\ufeff"
    with pytest.raises(ConfigError, match=r"cannot read .*nowhere\.toml"):
        read_config_text(tmp_path / "nowhere.toml")
    # Only the mark, as an empty file saved as UTF-16 is: no zero byte.
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError, match="is saved as UTF-16"):
        read_config_text(path)


def test_a_hooks_file_is_read_the_same_way(tmp_path):
    hooks = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
    path = tmp_path / "hooks.json"
    path.write_bytes(BOM + json.dumps(hooks).encode())
    assert len(read_hooks_file(path)) == 1

    path.write_bytes(json.dumps(hooks).encode("utf-16"))
    with pytest.raises(ConfigError, match="is saved as UTF-16"):
        read_hooks_file(path)


def test_an_edit_reads_a_marked_file_and_refuses_utf16_untouched(tmp_path):
    path = _home(tmp_path, BOM + TEXT.encode()) / "config.toml"

    add_server(path, "docs", {"command": "docs-server"})
    set_feature(path, "goals", False)

    text = path.read_bytes().decode("utf-8")
    assert not text.startswith("\ufeff") and text.startswith(TEXT)
    config = load_config(home=path.parent)
    assert "docs" in config.mcp_servers and config.goals is False

    before = _utf16(TEXT)
    path.write_bytes(before)
    for edit in (lambda: add_server(path, "docs", {"command": "docs-server"}),
                 lambda: set_feature(path, "goals", False)):
        with pytest.raises(ConfigError, match="is saved as UTF-16"):
            edit()
        assert path.read_bytes() == before


def test_the_cli_says_it_without_a_traceback(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, _utf16(TEXT))
    monkeypatch.setenv("FOUNDRY_HOME", str(home))

    assert app.main(["features", "list"]) != 0

    out = capsys.readouterr()
    shown = " ".join((out.out + out.err).split())
    assert "is saved as UTF-16, and Foundry reads UTF-8" in shown
    assert "Traceback" not in out.out + out.err


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_always_with_a_marked_file_adds_rules_that_load(tmp_path, newline):
    home = _home(tmp_path, BOM + TEXT.replace("\n", newline).encode())
    write = _rule_writer(home, PolicyEngine(rules=[]))

    # Twice: the second reads what the first wrote, and a CR kept from the
    # first and written again in text mode would be one TOML refuses.
    write(SimpleNamespace(tool="run_command", target="git status"))
    write(SimpleNamespace(tool="run_command", target="git diff"))
    write(SimpleNamespace(tool="run_command", target="git diff"))

    config = load_config(home=home)
    assert config.backend.model == "gpt-test"
    assert [r.pattern for r in config.rules if r.tool == "run_command"] == ["git status",
                                                                             "git diff"]
    assert b"\r\r" not in (home / "config.toml").read_bytes()


def test_always_with_a_utf16_file_is_this_session_s_alone(tmp_path, harness):
    home = _home(tmp_path, _utf16(TEXT))
    before = (home / "config.toml").read_bytes()
    policy = PolicyEngine(rules=[])
    command = {"command": "python -c \"print(1)\""}
    runtime, events = harness([
        turn("", call("run_command", command, "c1")),
        turn("", call("run_command", command, "c2")),
        turn("done"),
    ], policy=policy, approval=lambda req: ApprovalChoice.ALWAYS)
    runtime.persist_rule = _rule_writer(home, policy)

    runtime.run_turn("run it twice")

    warnings = [e.text for e in events if isinstance(e, Notice) and e.level == "warning"]
    assert any("could not save the rule" in w and "is saved as UTF-16" in w
               and "it applies to this session only" in w for w in warnings), warnings
    assert (home / "config.toml").read_bytes() == before
    # Granted for the session all the same: asked once, run twice.
    ran = [e for e in events if type(e).__name__ == "ToolEnd" and e.tool == "run_command"]
    assert len(ran) == 2
