"""When automatic compaction starts, as codex's
``model_auto_compact_token_limit`` sets it (D-094):
``[runtime] auto_compact_token_limit = N`` compacts once the context passes N
tokens, when that is sooner than the default share of the window.

What it may not do: make compaction later than the default -- a limit past
the window would leave a session to run out of it -- compact when
``auto_compact = false``, or be set by a repository, since compacting early
summarises away what the user was reading.
"""

from __future__ import annotations

import pytest

from foundry.core.config import load_config
from foundry.core.context import COMPACT_ABOVE_FRACTION, ContextManager
from foundry.core.errors import ConfigError
from foundry.core.events import Compacted

from test_anthropic_wiring import _home
from test_compaction import _context, _long_task, _summary_requests, assistant, user
from test_mode_model import _session
from test_resume import _wire, repo  # noqa: F401  (fixture)


# --- the threshold -------------------------------------------------------------------------


@pytest.mark.parametrize("limit, threshold", [
    (None, 8_000), (3_000, 3_000), (7_999, 7_999), (8_000, 8_000), (50_000, 8_000),
])
def test_the_limit_only_ever_brings_it_sooner(limit, threshold):
    context = ContextManager(system_prompt="t", max_context_tokens=10_000,
                             reserve_output_tokens=0, compact_above_tokens=limit)
    assert COMPACT_ABOVE_FRACTION == 0.8
    assert context.compaction_threshold() == threshold


def test_past_the_limit_it_needs_compacting():
    context = _context(user("x" * 8_000), assistant("y"), max_context=1_000_000)
    used = context.estimated_context_tokens()
    assert not context.needs_compaction()
    context.compact_above_tokens = used
    assert not context.needs_compaction()        # at it is not past it
    context.compact_above_tokens = used - 1
    assert context.needs_compaction()


def test_only_model_output_is_worth_compacting_however_low_the_limit():
    context = _context(user("x" * 8_000), max_context=1_000_000)
    context.compact_above_tokens = 1_000
    assert not context.needs_compaction()


# --- a task --------------------------------------------------------------------------------


def test_a_task_compacts_at_the_limit_well_before_the_window(tmp_path):
    runtime, session, _, model, events = _long_task(tmp_path, max_context=1_000_000)
    runtime.context.compact_above_tokens = 2_000
    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert outcome.text == "read them all"
    [compacted, *_] = [e for e in events if isinstance(e, Compacted)]
    assert compacted.auto and 2_000 < compacted.tokens_before < 0.8 * 1_000_000
    assert _summary_requests(model)


def test_without_it_the_same_task_never_compacts(tmp_path):
    runtime, session, _, model, events = _long_task(tmp_path, max_context=1_000_000)
    outcome = runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert outcome.text == "read them all"
    assert _summary_requests(model) == [] and not any(isinstance(e, Compacted) for e in events)


def test_off_still_means_never(tmp_path):
    runtime, session, _, model, _ = _long_task(tmp_path, max_context=1_000_000)
    runtime.context.compact_above_tokens = 2_000
    runtime.auto_compact = False
    runtime.run_turn("read f0.txt to f7.txt")
    session.close()
    assert _summary_requests(model) == []


# --- the setting ---------------------------------------------------------------------------


def test_it_loads_from_the_users_file_and_the_command_line(tmp_path):
    from foundry.core.config import parse_settings

    home = _home(tmp_path, "[runtime]\nauto_compact_token_limit = 50000\n")
    assert load_config(home=home).auto_compact_token_limit == 50_000
    assert load_config(home=tmp_path / "nowhere").auto_compact_token_limit is None
    assert load_config(home=home, settings=parse_settings(
        ["auto_compact_token_limit=20000"])).auto_compact_token_limit == 20_000


@pytest.mark.parametrize("value", ['"50k"', "999", "0", "-5", "true", "1.5e4"])
def test_anything_but_a_number_of_tokens_is_refused(tmp_path, value):
    home = _home(tmp_path, f"[runtime]\nauto_compact_token_limit = {value}\n")
    with pytest.raises(ConfigError, match="auto_compact_token_limit must be a whole number "
                                          "of tokens, at least 1000"):
        load_config(home=home)


def test_a_repository_may_not_set_it(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".foundry").mkdir(parents=True)
    (workspace / ".foundry" / "config.toml").write_text(
        "[runtime]\nauto_compact_token_limit = 2000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="project config may not set auto_compact_token_limit"):
        load_config(workspace, home=_home(tmp_path, ""))


def test_codex_s_name_is_told_foundry_s(tmp_path):
    home = _home(tmp_path, "model_auto_compact_token_limit = 50000\n")
    with pytest.raises(ConfigError, match="Foundry calls it runtime.auto_compact_token_limit"):
        load_config(home=home)


# --- a session -----------------------------------------------------------------------------


def _wired(repo, tmp_path, toml):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(toml, encoding="utf-8")
    return _wire(repo, home, [])


def test_build_hands_it_to_the_context(repo, tmp_path):
    wiring = _wired(repo, tmp_path, "[runtime]\nauto_compact_token_limit = 4000\n")
    assert wiring.runtime.context.compact_above_tokens == 4_000
    wiring.session.close()


def _context_row(wiring) -> str:
    [row] = [line for line in _session(wiring, "/status").printed
             if line.startswith("[dim]context")]
    return row


def test_status_says_where_it_compacts_when_a_limit_is_set(repo, tmp_path):
    row = _context_row(_wired(repo, tmp_path, "[runtime]\nauto_compact_token_limit = 4000\n"))
    assert row.endswith("; compacts past 4,000")


@pytest.mark.parametrize("toml", ["", "[runtime]\nauto_compact = false\n"
                                      "auto_compact_token_limit = 4000\n"])
def test_status_says_nothing_of_it_otherwise(repo, tmp_path, toml):
    assert "compacts past" not in _context_row(_wired(repo, tmp_path, toml))


def test_status_says_where_it_compacts_not_what_was_asked(repo, tmp_path):
    """A limit past the window changes nothing, and /status says so."""
    wiring = _wired(repo, tmp_path, "[runtime]\nauto_compact_token_limit = 900000000\n")
    window = wiring.runtime.context.max_context_tokens
    assert _context_row(wiring).endswith(f"; compacts past {int(window * 0.8):,}")
