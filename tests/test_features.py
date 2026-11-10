"""codex's [features] table (D-144), read as Foundry's own settings: a feature
Foundry has is a switch, one it has no counterpart for is off and refused
as on, and `foundry features` lists them and turns one on or off in the
user's own config.toml.
"""

from __future__ import annotations

import argparse

import pytest

from foundry.cli import app
from foundry.cli.slash_commands import CODEX_COMMANDS
from foundry.cli.wiring import _close, _config_args, build
from foundry.core.config import Config, load_config, parse_settings
from foundry.core.errors import ConfigError
from foundry.core.features import BY_NAME, CODEX_FEATURES, FEATURES, refusal, state

from test_resume import _wire, call, repo, turn  # noqa: F401  (fixture)
from test_slash_commands import _repl, _shown


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    """No machine policy but the test's own."""
    folder = tmp_path / "programdata"
    monkeypatch.setenv("PROGRAMDATA", str(folder))
    return folder / "Foundry"


def _home(tmp_path, text: str = ""):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    return home


def _config(tmp_path, text: str, **kwargs) -> Config:
    return load_config(home=_home(tmp_path, text), **kwargs)


def _hook(command: str) -> str:
    return (f'[[hooks.PreToolUse]]\n[[hooks.PreToolUse.hooks]]\ntype = "command"\n'
            f'command = "{command}"\n')


def _replies(request) -> dict:
    """The tool results a request sends back, by call id."""
    return {block.call_id: block for message in request.messages for block in message.blocks
            if getattr(block, "call_id", None) and hasattr(block, "is_error")}


# --- the switches -------------------------------------------------------------------


def test_each_is_as_codex_s_is_by_default(tmp_path):
    config = _config(tmp_path, "")
    assert (config.subagents, config.run_hooks, config.shell_tool, config.view_image,
            config.goals, config.memories) == (True, True, True, True, True, False)
    assert {f.name: state(config, f) for f in FEATURES if f.setting} == {
        "multi_agent": "on", "hooks": "on", "shell_tool": "on", "view_image": "on",
        "goals": "on", "memories": "off"}


@pytest.mark.parametrize("name, setting", [("multi_agent", "subagents"),
                                           ("hooks", "run_hooks"),
                                           ("codex_hooks", "run_hooks"),
                                           ("shell_tool", "shell_tool"),
                                           ("view_image", "view_image"),
                                           ("goals", "goals"),
                                           ("memories", "memories")])
def test_off_is_the_setting_s_off(tmp_path, name, setting):
    config = _config(tmp_path, f"[features]\n{name} = false\n")
    assert getattr(config, setting) is False
    assert config.explain(setting).endswith("(from user)")


def test_the_runtime_setting_and_the_feature_are_one(tmp_path):
    config = _config(tmp_path, "[runtime]\nsubagents = false\n[features]\nmulti_agent = true\n")
    assert config.subagents is True


def test_a_name_and_its_alias_together_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="features.hooks and features.codex_hooks are the "
                                          "same feature; set hooks alone"):
        _config(tmp_path, "[features]\nhooks = false\ncodex_hooks = true\n")


def test_a_feature_is_true_or_false(tmp_path):
    with pytest.raises(ConfigError, match=r"features.multi_agent must be true or false, got "
                                          r"str \('no'\)"):
        _config(tmp_path, '[features]\nmulti_agent = "no"\n')


def test_off_hooks_are_your_own_the_machine_s_still_run(tmp_path, machine):
    machine.mkdir(parents=True)
    (machine / "policy.toml").write_text(_hook("policy"), encoding="utf-8")
    config = _config(tmp_path, "[features]\nhooks = false\n" + _hook("mine"))
    assert [h.command for h in config.hooks] == ["policy", "mine"]
    assert [h.command for h in config.active_hooks()] == ["policy"]
    on = load_config(home=tmp_path / "home", settings=parse_settings(["features.hooks=true"]))
    assert [h.command for h in on.active_hooks()] == ["policy", "mine"]


def test_off_hooks_are_not_run_and_hooks_says_so(repo, tmp_path):
    home = _home(tmp_path, "[features]\nhooks = false\n" + _hook("mine"))
    wiring = _wire(repo, home, [turn(text="ok")])
    assert wiring.runtime.hooks is None
    shown = _shown(_repl(wiring, "/hooks"))
    assert "1 hook(s) of your own files are not run: features.hooks = false" in shown
    assert "no hooks:" not in shown


def test_off_shell_tool_refuses_every_command_ahead_of_an_allow_rule(repo, tmp_path):
    home = _home(tmp_path, "[features]\nshell_tool = false\n[[permissions]]\n"
                           'tool = "run_command"\npattern = "*"\ndecision = "allow"\n')
    wiring = _wire(repo, home, [turn(call("c1", "run_command", {"command": "git status"})),
                                turn(text="no shell")])
    _repl(wiring, "look")
    reply = _replies(wiring.runtime.backend.seen[1])["c1"]
    assert reply.is_error and "features.shell_tool" in reply.content
    # The model is told with the rules, as codex's model has no shell.
    system = wiring.runtime.backend.seen[0].messages[0].blocks[0].text
    assert "run_command is off: features.shell_tool = false" in system


def test_off_shell_tool_is_a_rule_policy_check_sees(tmp_path):
    config = _config(tmp_path, "[features]\nshell_tool = false\n")
    rule = config.rules[-1]
    assert (rule.tool, rule.pattern, rule.verdict.value, rule.rule_id) == (
        "run_command", "*", "deny", "features.shell_tool")
    assert not any(r.rule_id == "features.shell_tool" for r in _config(tmp_path, "").rules)


def test_off_view_image_is_not_offered_to_the_model_or_its_sub_agents(repo, tmp_path):
    home = _home(tmp_path, "[features]\nview_image = false\n")
    wiring = _wire(repo, home, [
        turn(call("s1", "spawn_agent", {"task": "Find A."})),
        turn(call("f9", "finish", {"status": "completed", "summary": "A is 1",
                                   "claims": []})),
        turn(text="A is 1.")])
    assert "view_image" not in wiring.runtime.registry.names()
    _repl(wiring, "use a sub-agent")
    child = wiring.runtime.backend.seen[1]
    assert "view_image" not in [tool.name for tool in child.tools]
    assert "view_image" in [tool.name for tool in
                            _wire(repo, _home(tmp_path / "on"), []).runtime.registry.tools.values()]


def test_view_image_is_off_where_the_backend_takes_no_images(tmp_path):
    config = _config(tmp_path, "[backend]\nimages = false\n")
    assert state(config, BY_NAME["view_image"]) == "off"


def test_off_multi_agent_offers_no_sub_agents(repo, tmp_path):
    wiring = _wire(repo, _home(tmp_path, "[features]\nmulti_agent = false\n"), [])
    assert "spawn_agent" not in wiring.runtime.registry.names()


def test_off_goals_leave_goal_refused(repo, tmp_path):
    wiring = _wire(repo, _home(tmp_path, "[features]\ngoals = false\n"), [])
    shown = _shown(_repl(wiring, "/goal ship it"))
    assert "goals are off in your configuration (features.goals = false)" in shown
    assert wiring.runtime.goal is None and wiring.runtime.backend.seen == []


def test_a_resumed_session_s_goal_stays_in_its_journal_with_goals_off(repo, tmp_path):
    from foundry.core.goal import Goal
    from foundry.core.session import TerminalStatus

    home = _home(tmp_path)
    wiring = _wire(repo, home, [turn(text="partly")])
    wiring.runtime.change_goal(Goal("ship it", token_budget=1000), by="user")
    wiring.runtime.run_turn("go")
    _close(wiring, TerminalStatus.CANCELLED, "killed")
    (home / "config.toml").write_text("[features]\ngoals = false\n", encoding="utf-8")
    resumed = _wire(repo, home, [], resume="last")
    assert resumed.runtime.goal is None
    assert "update_goal" not in resumed.runtime.registry.tools


# --- what Foundry has no counterpart for ---------------------------------------------


@pytest.mark.parametrize("name, said", [
    ("fast_mode", "Foundry asks for no service tier"),
    ("web_search_request", "Foundry has no web search of its own"),
    ("unified_exec", "Foundry runs each command to its end"),
    ("apps", "Foundry has no apps"),
])
def test_on_is_refused_with_what_foundry_has_instead(tmp_path, name, said):
    with pytest.raises(ConfigError, match=f"user config: features.{name} cannot be turned on: "
                                          f"{said}"):
        _config(tmp_path, f"[features]\n{name} = true\n")
    _config(tmp_path, f"[features]\n{name} = false\n")      # what it already is


def test_a_table_for_one_foundry_lacks_is_refused_as_on(tmp_path):
    with pytest.raises(ConfigError, match="features.network_proxy cannot be turned on"):
        _config(tmp_path, "[features.network_proxy]\nenabled = true\n")


def test_codex_s_other_names_are_off_here(tmp_path):
    assert "steer" in CODEX_FEATURES and not CODEX_FEATURES & set(BY_NAME)
    with pytest.raises(ConfigError, match="codex's steer has no counterpart in Foundry"):
        _config(tmp_path, "[features]\nsteer = true\n")
    _config(tmp_path, "[features]\nsteer = false\n")


def test_removed_ones_are_ignored_as_codex_ignores_them(tmp_path):
    config = _config(tmp_path, "[features]\npersonality = true\nundo = false\n")
    assert state(config, BY_NAME["personality"]) == "ignored"
    assert config.explain("features.personality").endswith("(from user)")


def test_a_name_codex_does_not_know_is_a_typo(tmp_path):
    with pytest.raises(ConfigError, match="no feature 'multi_agnet'; did you mean "
                                          "'multi_agent'"):
        _config(tmp_path, "[features]\nmulti_agnet = false\n")


def test_features_must_be_a_table(tmp_path):
    with pytest.raises(ConfigError, match="features must be a table"):
        _config(tmp_path, "features = 1\n")


# --- where they may be set -----------------------------------------------------------


def test_a_repository_may_not_set_them(tmp_path, repo):
    (repo / ".foundry").mkdir()
    (repo / ".foundry" / "config.toml").write_text("[features]\nmulti_agent = false\n",
                                                   encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set features; set them in "
                                          "your own config.toml"):
        load_config(repo, home=_home(tmp_path))


def test_a_profile_and_the_machine_policy_may(tmp_path, machine):
    config = _config(tmp_path, "[profiles.p.features]\nmulti_agent = false\n", profile="p")
    assert config.subagents is False
    machine.mkdir(parents=True)
    (machine / "policy.toml").write_text("[features]\ngoals = false\n", encoding="utf-8")
    assert _config(tmp_path, "").goals is False


def test_dash_c_sets_one(tmp_path):
    config = _config(tmp_path, "", settings=parse_settings(["features.multi_agent=false"]))
    assert config.subagents is False
    assert config.explain("features.multi_agent").endswith("(from cli (-c))")
    with pytest.raises(ConfigError, match="features.apps cannot be turned on"):
        _config(tmp_path, "", settings=parse_settings(["features.apps=true"]))


def _flags(*argv: str) -> argparse.Namespace:
    return app.build_parser().parse_args(["exec", *argv, "task"])


def test_enable_and_disable_are_dash_c_features():
    settings = _config_args(_flags("--disable", "multi_agent", "--enable", "goals",
                                       "-c", "features.hooks=false"))["settings"]
    assert settings == {"features": {"hooks": False, "multi_agent": False, "goals": True}}
    with pytest.raises(ConfigError, match="--enable and --disable both name goals"):
        _config_args(_flags("--enable", "goals", "--disable", "goals"))


def test_enable_reaches_the_session(repo, tmp_path):
    settings = _config_args(_flags("--disable", "multi_agent"))["settings"]
    wiring = build(repo, home=_home(tmp_path), settings=settings,
                       overrides={"model": "fake"})
    try:
        assert "spawn_agent" not in wiring.runtime.registry.names()
    finally:
        wiring.session.close()


# --- foundry features ---------------------------------------------------------------


@pytest.fixture()
def user(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    return home / "config.toml"


def test_list_says_each_one_s_state_and_what_it_is_here(user, capsys):
    user.write_text("[features]\nmulti_agent = false\n", encoding="utf-8")
    assert app.main(["features", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    rows = {line.split()[0]: line.split()[1] for line in lines[:len(FEATURES)]}
    assert rows["multi_agent"] == "off" and rows["hooks"] == "on"
    assert rows["memories"] == "off" and rows["personality"] == "ignored"
    assert len(rows) == len(FEATURES)
    assert "codex's other features have no counterpart here" in lines[-1]
    assert app.main(["features"]) == 0


def test_enable_and_disable_write_your_config(user, capsys):
    assert app.main(["features", "disable", "multi_agent"]) == 0
    assert user.read_text(encoding="utf-8") == "[features]\nmulti_agent = false\n"
    assert "features.multi_agent = false in" in capsys.readouterr().out
    assert app.main(["features", "enable", "multi_agent"]) == 0
    assert app.main(["features", "disable", "goals"]) == 0
    assert user.read_text(encoding="utf-8") == ("[features]\ngoals = false\n"
                                                "multi_agent = true\n")
    assert load_config(home=user.parent).goals is False


def test_an_alias_is_written_by_its_name(user):
    assert app.main(["features", "disable", "codex_hooks"]) == 0
    assert user.read_text(encoding="utf-8") == "[features]\nhooks = false\n"


def test_the_rest_of_the_file_is_kept(user):
    text = ('model = "x"\r\n\r\n# mine\r\n[features]\r\n# about goals\r\n'
            "goals = true  # on for now\r\n\r\n[runtime]\r\nmax_tool_rounds = 9\r\n")
    user.write_bytes(text.replace('model = "x"', "[backend]\r\nmodel = \"x\"").encode())
    assert app.main(["features", "disable", "goals"]) == 0
    assert user.read_bytes().decode() == text.replace(
        'model = "x"', "[backend]\r\nmodel = \"x\"").replace(
        "goals = true  # on for now", "goals = false  # on for now")
    assert app.main(["features", "disable", "hooks"]) == 0
    assert b"[features]\r\nhooks = false\r\n# about goals" in user.read_bytes()


def test_a_table_at_the_end_without_a_newline(user):
    user.write_text("[features]", encoding="utf-8")
    assert app.main(["features", "disable", "goals"]) == 0
    assert user.read_text(encoding="utf-8") == "[features]\ngoals = false\n"


def test_features_written_another_way_are_left_for_you(user, capsys):
    before = "features = { goals = true }\n"
    user.write_text(before, encoding="utf-8")
    assert app.main(["features", "disable", "goals"]) != 0
    assert "not written as a [features] table" in " ".join(capsys.readouterr().out.split())
    assert user.read_text(encoding="utf-8") == before


def test_a_line_inside_a_value_is_not_the_setting(user, capsys):
    before = '[features]\nnote = """\ngoals = true\n"""\n'
    user.write_text(before, encoding="utf-8")
    assert app.main(["features", "disable", "goals"]) != 0
    assert user.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("argv, said", [
    (["enable", "web_search"], "features.web_search cannot be turned on"),
    (["enable", "steer"], "codex's steer has no counterpart"),
    (["disable", "multi_agnet"], "no feature 'multi_agnet'"),
])
def test_what_cannot_be_written_is_not(user, capsys, argv, said):
    assert app.main(["features", *argv]) != 0
    assert said in capsys.readouterr().out
    assert user.read_text(encoding="utf-8") == ""


def test_codex_s_experimental_command_points_here():
    assert "foundry features list" in CODEX_COMMANDS["experimental"]


def test_refusal_says_nothing_of_what_is_taken():
    assert refusal("multi_agent", False) == refusal("memories", False) == ""
    assert refusal("steer", False) == refusal("personality", True) == ""
