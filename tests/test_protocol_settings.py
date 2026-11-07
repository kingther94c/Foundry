"""Settings the protocol in use has no way to send, refused rather than dropped
(D-100), as backend.verbosity on Claude already was (D-090).

`fallbacks = "off"` on an OpenAI endpoint and `reasoning_summary = "auto"` on
Chat Completions were taken and did nothing -- and the exec header even listed
the summary as the run's setting. What it may not do: refuse what the protocol
does take, or the "ask for nothing" values every protocol already honours
(`fallbacks = "default"`, `reasoning_summary = "none"`).
"""

from __future__ import annotations

import pytest

from foundry.core.config import load_config, parse_settings
from foundry.core.errors import ConfigError

from test_anthropic_wiring import _home


def _load(tmp_path, text, **kwargs):
    return load_config(home=_home(tmp_path, text), **kwargs)


@pytest.mark.parametrize("protocol", ["openai_compat", "responses"])
def test_claude_s_fallbacks_are_refused_elsewhere(tmp_path, protocol):
    with pytest.raises(ConfigError) as caught:
        _load(tmp_path, f'[backend]\nprotocol = "{protocol}"\nfallbacks = "off"\n')
    assert str(caught.value) == (
        f"backend.fallbacks is not a setting the {protocol} protocol takes: it is Claude's "
        "server-side fallback model (anthropic_messages); set it in the profile of a "
        "Claude endpoint instead")


def test_claude_takes_them(tmp_path):
    config = _load(tmp_path, '[backend]\nprotocol = "anthropic_messages"\nfallbacks = "off"\n')
    assert config.backend.fallbacks == "off"


def test_the_default_is_no_setting_at_all(tmp_path):
    assert _load(tmp_path, '[backend]\nfallbacks = "default"\n').backend.fallbacks == "default"


@pytest.mark.parametrize("summary", ["auto", "concise", "detailed"])
def test_a_summary_asked_of_chat_completions_is_refused(tmp_path, summary):
    with pytest.raises(ConfigError) as caught:
        _load(tmp_path, f'[backend]\nreasoning_summary = "{summary}"\n')
    message = str(caught.value)
    assert message.startswith(f"backend.reasoning_summary {summary!r} is not one the "
                              "openai_compat protocol takes: Chat Completions has no reasoning "
                              "summary to ask for")
    assert message.endswith("shown unless runtime.show_reasoning = false")


@pytest.mark.parametrize("protocol", ["responses", "anthropic_messages"])
def test_the_protocols_with_summaries_take_them(tmp_path, protocol):
    config = _load(tmp_path, f'[backend]\nprotocol = "{protocol}"\nreasoning_summary = "auto"\n')
    assert config.backend.reasoning_summary == "auto"


def test_asking_for_none_is_taken_everywhere(tmp_path):
    assert _load(tmp_path, '[backend]\nreasoning_summary = "none"\n'
                 ).backend.reasoning_summary == "none"


def test_a_setting_a_profile_inherits_is_judged_by_the_profile_s_protocol(tmp_path):
    text = ('[backend]\nprotocol = "anthropic_messages"\nfallbacks = "off"\n'
            '[profiles.gpt.backend]\nprotocol = "responses"\n')
    assert _load(tmp_path, text).backend.fallbacks == "off"
    with pytest.raises(ConfigError, match="fallbacks is not a setting the responses protocol"):
        _load(tmp_path, text, profile="gpt")


def test_the_command_line_is_judged_the_same(tmp_path):
    with pytest.raises(ConfigError, match="reasoning_summary 'concise' is not one"):
        _load(tmp_path, "", settings=parse_settings(["reasoning_summary=concise"]))
    with pytest.raises(ConfigError, match="fallbacks is not a setting the openai_compat"):
        _load(tmp_path, "", settings=parse_settings(["fallbacks=off"]))
