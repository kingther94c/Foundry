"""The command-line settings codex users expect (D-042): -m, --mode, -c,
--profile, and exec --output-last-message.

They are the top layer of configuration, so they win over every file and the
environment -- and like every layer that is not a credential store, they may
not carry a secret.
"""

from __future__ import annotations

import pytest

from foundry.cli import app
from foundry.cli.repl import NEW_SESSION
from foundry.cli.session_commands import cmd_session
from foundry.cli.unattended import _write_last_message
from foundry.cli.wiring import _config_args
from foundry.core.config import load_config, parse_settings
from foundry.core.errors import ConfigError
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.policy.engine import Mode, PolicyEngine
from foundry.core.prompts import UNATTENDED_LINE, mode_line, permissions_paragraph
from test_resume import fake_model, repo  # noqa: F401  (fixtures)


def _home(tmp_path, text=""):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return home


# --- -c key=value ---------------------------------------------------------------


@pytest.mark.parametrize("item, layer", [
    ("backend.model=gpt-5", {"backend": {"model": "gpt-5"}}),
    ("model=gpt-5", {"backend": {"model": "gpt-5"}}),
    ("max_tool_rounds=60", {"runtime": {"max_tool_rounds": 60}}),
    ("runtime.auto_compact=false", {"runtime": {"auto_compact": False}}),
    ("mode=plan", {"runtime": {"mode": "plan"}}),
    ('backend.effort="high"', {"backend": {"effort": "high"}}),
    ("backend.base_url=http://127.0.0.1:9/v1", {"backend": {"base_url": "http://127.0.0.1:9/v1"}}),
])
def test_a_setting_is_read_as_toml_or_else_as_text(item, layer):
    assert parse_settings([item]) == layer


@pytest.mark.parametrize("item, message", [
    ("backend.api_key=sk-123", "secrets come only from the environment"),
    ("api_key=sk-123", "secrets come only from the environment"),
    ('backend.headers={Authorization="Bearer x"}', "secrets come only from the environment"),
    ("backend.modle=x", "no setting 'backend.modle'"),
    ("max_tool_round=5", "no setting 'max_tool_round'"),
    ("backend.name=x", "no setting 'backend.name'"),
    ("permissions.tool=x", "no setting 'permissions.tool'"),
    ("model", "expects key=value"),
    ("=x", "expects key=value"),
])
def test_a_setting_that_is_a_secret_or_a_typo_is_refused(item, message):
    with pytest.raises(ConfigError, match=message):
        parse_settings([item])


def test_settings_win_over_files_and_the_environment(tmp_path, monkeypatch):
    home = _home(tmp_path, '[backend]\nmodel = "from-file"\n[runtime]\nmax_tool_rounds = 10\n')
    monkeypatch.setenv("FOUNDRY_MODEL", "from-env")
    config = load_config(home=home, settings=parse_settings(
        ["model=from-cli", "max_tool_rounds=99"]))
    assert config.backend.model == "from-cli"
    assert config.max_tool_rounds == 99
    assert config.explain("backend.model").endswith("(from cli (-c))")


def test_a_bad_value_is_refused_like_one_in_a_file(tmp_path):
    with pytest.raises(ConfigError, match="max_tool_rounds must be an integer"):
        load_config(home=_home(tmp_path), settings=parse_settings(["max_tool_rounds=lots"]))


# --- profiles -------------------------------------------------------------------

PROFILES = """\
[backend]
model = "personal-model"
[runtime]
max_tool_rounds = 30

[profiles.work.backend]
model = "work-model"
base_url = "http://127.0.0.1:9/v1"
[profiles.work.runtime]
max_tool_rounds = 25
mode = "plan"

[profiles.fast.backend]
model = "fast-model"
"""


def test_a_profile_is_applied_over_the_users_settings(tmp_path):
    config = load_config(home=_home(tmp_path, PROFILES), profile="work")
    assert config.backend.model == "work-model"
    assert config.backend.base_url == "http://127.0.0.1:9/v1"
    assert config.max_tool_rounds == 25 and config.mode is Mode.PLAN
    assert config.backend.name == "work"
    assert config.explain("backend.model").endswith("(from profile work)")
    assert config.explain("profile").endswith("(from cli)")


def test_no_profile_leaves_the_users_settings(tmp_path):
    config = load_config(home=_home(tmp_path, PROFILES))
    assert config.backend.model == "personal-model" and config.backend.name == "personal"


def test_the_file_or_the_environment_may_choose_one_and_the_flag_wins(tmp_path, monkeypatch):
    home = _home(tmp_path, 'profile = "fast"\n' + PROFILES)
    assert load_config(home=home).backend.model == "fast-model"
    monkeypatch.setenv("FOUNDRY_PROFILE", "work")
    assert load_config(home=home).backend.model == "work-model"
    assert load_config(home=home).explain("profile").endswith("(from environment)")
    assert load_config(home=home, profile="fast").backend.model == "fast-model"


def test_the_repository_still_comes_after_a_profile(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        "[runtime]\nmax_tool_rounds = 20\n", encoding="utf-8")
    config = load_config(workspace, home=_home(tmp_path, PROFILES), profile="work")
    assert config.max_tool_rounds == 20


def test_an_unknown_profile_names_the_ones_there_are(tmp_path):
    with pytest.raises(ConfigError, match=r"no profile 'home'.*\(profiles: fast, work\)"):
        load_config(home=_home(tmp_path, PROFILES), profile="home")


def test_a_profile_that_is_not_a_table_is_refused(tmp_path):
    with pytest.raises(ConfigError, match=r"must be tables, as \[profiles.<name>\]"):
        load_config(home=_home(tmp_path, 'profiles = { work = 1 }\n'), profile="work")
    with pytest.raises(ConfigError, match="profile must be the name"):
        load_config(home=_home(tmp_path, "profile = 3\n"))


def test_only_the_users_own_file_defines_profiles(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        '[profiles.evil.backend]\nbase_url = "http://attacker"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="no profile 'evil'"):
        load_config(workspace, home=_home(tmp_path), profile="evil")


# --- the command line -------------------------------------------------------------


def _parsed(*argv):
    return app.build_parser().parse_args(list(argv))


def test_the_flags_become_build_arguments():
    args = _parsed("run", "-m", "m1", "--mode", "accept_edits", "-c", "max_tool_rounds=5",
                   "--profile", "work")
    assert _config_args(args) == {
        "overrides": {"model": "m1", "mode": Mode.ACCEPT_EDITS},
        "profile": "work",
        "settings": {"runtime": {"max_tool_rounds": 5}},
    }
    assert _config_args(_parsed("run")) == {"overrides": None, "profile": None,
                                                 "settings": {}}


def test_exec_offers_only_the_modes_an_unattended_run_can_use(capsys):
    assert _parsed("exec", "--mode", "accept_edits", "t").mode == "accept_edits"
    with pytest.raises(SystemExit):
        _parsed("exec", "--mode", "default", "t")
    assert "invalid choice: 'default'" in capsys.readouterr().err


def test_a_flag_first_means_run(monkeypatch):
    seen = []
    monkeypatch.setattr("foundry.cli.app.cmd_session", lambda args: seen.append(args) or 0)
    assert app.main(["-m", "m1", "fix it"]) == 0
    assert seen[0].command == "run" and seen[0].model == "m1" and seen[0].task == "fix it"
    assert app.main([]) == 0 and seen[1].command == "run"


def test_a_bare_word_is_still_a_subcommand(capsys):
    with pytest.raises(SystemExit):
        app.main(["exce", "a task"])
    assert "invalid choice: 'exce'" in capsys.readouterr().err


def test_a_new_session_keeps_the_flags(repo, tmp_path, monkeypatch):
    calls = []
    from foundry.cli.wiring import build as real_build

    def build(*args, **kwargs):
        calls.append(kwargs)
        return real_build(*args, **{**kwargs, "home": tmp_path / "home",
                                    "overrides": {**(kwargs.get("overrides") or {}),
                                                  "base_url": "http://127.0.0.1:9/v1"}})

    ends = iter([NEW_SESSION, None])
    monkeypatch.setattr("foundry.cli.session_commands.build", build)
    monkeypatch.setattr("foundry.cli.session_commands._repl",
                        lambda wiring, console, first=None: next(ends) or
                        TerminalStatus.CANCELLED)
    args = _parsed("run", "--workspace", str(repo), "-m", "m1", "-c", "max_tool_rounds=5")
    assert cmd_session(args) == EXIT_CODES[TerminalStatus.CANCELLED]
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs["overrides"]["model"] == "m1"
        assert kwargs["settings"] == {"runtime": {"max_tool_rounds": 5}}


def test_a_secret_on_the_command_line_stops_the_run(capsys):
    assert app.main(["run", "-c", "backend.api_key=sk-live-1234567890"]) == 12
    err = capsys.readouterr().out
    assert "secrets come only from the environment" in err


# --- exec: an unattended run in another mode, and its last message ---------------


def test_unattended_in_another_mode_is_told_nobody_can_approve():
    policy = PolicyEngine(mode=Mode.ACCEPT_EDITS)
    assert UNATTENDED_LINE not in permissions_paragraph(policy)
    policy.interactive = False
    assert UNATTENDED_LINE in permissions_paragraph(policy)
    policy.mode = Mode.DONT_ASK      # its own line already says so
    assert UNATTENDED_LINE not in permissions_paragraph(policy)


def _exec_home(tmp_path, httpd, monkeypatch):
    host, port = httpd.server_address
    home = _home(tmp_path, f'[backend]\nbase_url = "http://{host}:{port}/v1"\n'
                           'model = "fake"\nstream = false\n')
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    return home


def test_exec_in_accept_edits_writes_and_says_so(repo, tmp_path, monkeypatch, capsys,
                                                 fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    patch = ("*** Begin Patch\n*** Update File: a.py\n"
             "<<<<<<< SEARCH\nA = 1\n=======\nA = 2\n>>>>>>> REPLACE\n*** End Patch\n")
    handler.turns, handler.index = [
        tool_turn("read_file", {"path": "a.py"}, "c1"),
        tool_turn("apply_patch", {"patch": patch}, "c2"),
        tool_turn("finish", {"status": "partial", "summary": "A is now 2", "claims": []}, "f1"),
    ], 0
    out = tmp_path / "last.txt"
    code = app.main(["exec", "set A to 2", "--workspace", str(repo), "--mode", "accept_edits",
                     "-m", "fake-2", "-o", str(out)])
    capsys.readouterr()
    assert code == 10
    assert (repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    system = httpd.requests[0]["messages"][0]["content"]
    assert mode_line(Mode.ACCEPT_EDITS) in system and UNATTENDED_LINE in system
    assert httpd.requests[0]["model"] == "fake-2"
    assert out.read_text(encoding="utf-8").startswith("A is now 2")


def test_exec_stays_dont_ask_whatever_the_config_says(repo, tmp_path, monkeypatch, capsys,
                                                      fake_model):
    from test_cli_e2e import text_turn

    httpd, handler = fake_model
    home = _exec_home(tmp_path, httpd, monkeypatch)
    with (home / "config.toml").open("a", encoding="utf-8") as fh:
        fh.write('[runtime]\nmode = "accept_edits"\n')
    handler.turns, handler.index = [text_turn("hi"), text_turn("still hi")], 0
    out = tmp_path / "last.txt"
    assert app.main(["exec", "hello", "--workspace", str(repo), "-o", str(out)]) == 10
    capsys.readouterr()
    system = httpd.requests[0]["messages"][0]["content"]
    assert mode_line(Mode.DONT_ASK) in system and UNATTENDED_LINE not in system
    # The model's last reply, after the reminder to call finish.
    assert out.read_text(encoding="utf-8") == "still hi"


def test_the_last_message_is_scrubbed(tmp_path, monkeypatch):
    from foundry.core.redaction import Redactor
    from rich.console import Console

    secret = "s3cr3t-value-for-the-file-0123"
    redactor = Redactor()
    redactor.register(secret)
    monkeypatch.setattr("foundry.cli.unattended.default_redactor", lambda: redactor)
    out = tmp_path / "last.txt"
    _write_last_message(out, f"the key was {secret}", Console())
    assert out.read_text(encoding="utf-8") == "the key was [redacted]"


def test_a_last_message_that_cannot_be_written_is_reported(tmp_path, capsys):
    from rich.console import Console

    _write_last_message(tmp_path / "missing" / "dir" / "last.txt", "x", Console())
    assert "could not write" in capsys.readouterr().out
