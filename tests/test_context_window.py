"""The model's context window on every protocol, as codex's
``model_context_window`` (D-095).

``backend.max_context_tokens`` reached Claude alone: the OpenAI shapes planned
masking and compaction against 128,000 tokens whatever the model held -- a
quarter of GPT-5's window wasted, a 32,000-token local model overrun -- and
the setting was skipped without a word. And in a small window compaction
started past the point the window counts as full, so a long task ran out
before it compacted.
"""

from __future__ import annotations

import pytest

from foundry.core.backends.anthropic import AnthropicBackend
from foundry.core.backends.openai_compat import OpenAICompatBackend
from foundry.core.backends.responses import ResponsesBackend
from foundry.core.context import ContextManager
from foundry.core.events import Compacted, TerminalStatus

from test_anthropic_wiring import _build
from test_compaction import _Model, _reads, _summary_requests
from test_mode_model import _session
from test_resume import _core, _wire, repo, turn  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-window")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-window")


# --- every protocol ------------------------------------------------------------------------


@pytest.mark.parametrize("protocol, backend_class", [
    ("responses", ResponsesBackend), ("openai_compat", OpenAICompatBackend),
    ("anthropic_messages", AnthropicBackend),
])
def test_every_protocol_plans_against_the_window_it_is_given(tmp_path, protocol, backend_class):
    wiring = _build(tmp_path, f'[backend]\nprotocol = "{protocol}"\n'
                              "max_context_tokens = 400000\n")
    backend = wiring.runtime.backend
    assert isinstance(backend, backend_class)
    assert backend.capabilities().max_context_tokens == 400_000
    assert wiring.runtime.context.max_context_tokens == 400_000


@pytest.mark.parametrize("protocol, window", [
    ("responses", 128_000), ("openai_compat", 128_000), ("anthropic_messages", 200_000),
])
def test_unset_each_keeps_its_own(tmp_path, protocol, window):
    wiring = _build(tmp_path, f'[backend]\nprotocol = "{protocol}"\n')
    assert wiring.runtime.context.max_context_tokens == window


def test_only_the_window_changes(tmp_path):
    caps = _build(tmp_path, '[backend]\nprotocol = "responses"\nmax_context_tokens = 32000\n'
                  ).runtime.backend.capabilities()
    default = ResponsesBackend(base_url="http://x", model="m").capabilities()
    assert caps.parallel_tool_calls == default.parallel_tool_calls is True
    assert (caps.streaming, caps.prompt_caching, caps.edit_format) == (
        default.streaming, default.prompt_caching, default.edit_format)


def test_status_says_the_window_given(repo, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[backend]\nmax_context_tokens = 400000\n",
                                      encoding="utf-8")
    [row] = [line for line in _session(_wire(repo, home, []), "/status").printed
             if line.startswith("[dim]context")]
    assert " of 400,000 tokens" in row


# --- compaction before the window is full ----------------------------------------------------


@pytest.mark.parametrize("window, reserve, threshold", [
    (128_000, 8_000, 102_400),     # the share comes first, as before
    (40_000, 8_000, 32_000),       # where the two meet
    (32_000, 8_000, 24_000),       # the room for the answer comes first
    (10_000, 0, 8_000),
])
def test_compaction_starts_no_later_than_the_window_is_full(window, reserve, threshold):
    context = ContextManager(system_prompt="t", max_context_tokens=window,
                             reserve_output_tokens=reserve)
    assert context.compaction_threshold() == threshold


def test_a_limit_still_brings_it_sooner():
    context = ContextManager(system_prompt="t", max_context_tokens=32_000,
                             reserve_output_tokens=8_000, compact_above_tokens=10_000)
    assert context.compaction_threshold() == 10_000


def _task(tmp_path, *, max_context, reserve, files, lines):
    model = _Model([*_reads(files), turn(text="read them all")])
    runtime, session, repo = _core(tmp_path, [], max_context=max_context, reserve=reserve)
    runtime.backend = model
    for i in range(files):
        (repo / f"f{i}.txt").write_text("".join(f"line {i}.{n} " + "x" * 50 + "\n"
                                                for n in range(lines)), encoding="utf-8")
    events = []
    runtime.events.subscribe(events.append)
    return runtime, session, model, events


def test_a_small_window_compacts_instead_of_running_out(tmp_path):
    """Room kept for the answer past a fifth of the window: full at 1,500
    tokens, while 80% is 2,400. The context comes to rest between the two --
    where it used to run out without compacting."""
    runtime, session, model, events = _task(tmp_path, max_context=3_000, reserve=1_500,
                                            files=8, lines=50)
    outcome = runtime.run_turn("read the files")
    session.close()
    assert outcome.status is not TerminalStatus.PARTIAL, outcome
    assert outcome.text == "read them all"
    assert _summary_requests(model)
    [first, *_] = [e for e in events if isinstance(e, Compacted)]
    assert 3_000 - 1_500 < first.tokens_before < 0.8 * 3_000
