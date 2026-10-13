"""foundry report and cached input tokens.

The Anthropic backend reads most of each prompt from the provider's cache. The
report used to count every one of those tokens as full-price input; it now
shows the cached part and its share of input, and tolerates journals written
before the field existed.
"""

from __future__ import annotations

import json

from foundry.cli.report import analyse, render, summarize


def _session(root, name, *usages, status="completed"):
    directory = root / name
    directory.mkdir(parents=True)
    lines = []
    for ordinal, payload in enumerate(usages, start=1):
        lines.append(json.dumps({"ts": "t", "ordinal": ordinal,
                                 "type": "token_usage", "payload": payload}))
    lines.append(json.dumps({"ts": "t", "ordinal": len(usages) + 1,
                             "type": "termination", "payload": {"status": status}}))
    path = directory / "events.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_analyse_sums_cached_input_tokens(tmp_path):
    path = _session(tmp_path, "s1",
                    {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 800},
                    {"input_tokens": 1000, "output_tokens": 20, "cached_input_tokens": 900})
    report = analyse(path)
    assert report.input_tokens == 2000
    assert report.output_tokens == 30
    assert report.cached_input_tokens == 1700


def test_a_journal_without_the_cached_field_counts_as_zero(tmp_path):
    path = _session(tmp_path, "old", {"input_tokens": 500, "output_tokens": 5})
    report = analyse(path)
    assert report.input_tokens == 500
    assert report.cached_input_tokens == 0


def test_old_and_new_journals_mix(tmp_path):
    _session(tmp_path, "a_old", {"input_tokens": 1000, "output_tokens": 5})
    _session(tmp_path, "b_new",
             {"input_tokens": 3000, "output_tokens": 5, "cached_input_tokens": 3000})
    summary = json.loads(render(tmp_path, as_json=True))
    assert summary["tokens"]["input"] == 4000
    assert summary["tokens"]["cached_input"] == 3000
    assert summary["tokens"]["cache_read_share"] == 0.75


def test_json_carries_cached_input_and_share(tmp_path):
    _session(tmp_path, "s1",
             {"input_tokens": 4206, "output_tokens": 7, "cached_input_tokens": 4202})
    summary = json.loads(render(tmp_path, as_json=True))
    assert summary["tokens"] == {"input": 4206, "output": 7, "cached_input": 4202,
                                 "cache_read_share": round(4202 / 4206, 3)}


def test_json_share_is_null_with_no_input(tmp_path):
    _session(tmp_path, "s1")
    summary = json.loads(render(tmp_path, as_json=True))
    assert summary["tokens"]["cached_input"] == 0
    assert summary["tokens"]["cache_read_share"] is None


def test_text_shows_cached_tokens_next_to_the_token_line(tmp_path):
    _session(tmp_path, "s1",
             {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 750})
    lines = render(tmp_path).splitlines()
    token_line = next(i for i, line in enumerate(lines) if line.strip().startswith("tokens"))
    assert lines[token_line].split() == ["tokens", "1000", "in", "/", "10", "out"]
    assert lines[token_line + 1] == "  cached input tokens   750 (75% of input read from cache)"


def test_text_for_an_old_journal_shows_zero_cached(tmp_path):
    _session(tmp_path, "old", {"input_tokens": 400, "output_tokens": 4})
    text = render(tmp_path)
    assert "  cached input tokens   0 (0% of input read from cache)" in text


def test_text_with_no_input_does_not_divide_by_zero(tmp_path):
    _session(tmp_path, "s1")
    text = render(tmp_path)
    assert "  cached input tokens   0 (no input recorded)" in text


def test_empty_summary_is_unchanged():
    assert summarize([]) == {"sessions": 0}
