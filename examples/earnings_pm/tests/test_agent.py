"""Tests for earnings_pm.agent with a fake Claude client.

All responses, figures and memos below are synthetic and hand-written; they
describe no real company and no real API traffic.
"""

import copy
import json
from types import SimpleNamespace

import pytest

from earnings_pm.agent import (
    REMINDER,
    SYSTEM_PROMPT,
    TOOLS,
    AgentError,
    AgentRefused,
    AgentResult,
    build_tools,
    run_agent,
)
from earnings_pm.edgar import EdgarClient
from earnings_pm.memo import render_markdown

STRICT_UNION_LIMIT = 16  # API limit across all strict tools in one request


def count_union_params(node):
    """Count subschemas whose "type" is a list or that use anyOf, at any depth."""
    count = 0
    if isinstance(node, dict):
        if isinstance(node.get("type"), list) or "anyOf" in node:
            count += 1
        for value in node.values():
            count += count_union_params(value)
    elif isinstance(node, list):
        for item in node:
            count += count_union_params(item)
    return count


def test_count_union_params_counts_nested():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": ["string", "null"]},
            "b": {"anyOf": [{"type": "object", "properties": {"c": {"type": ["number", "null"]}}},
                            {"type": "null"}]},
            "d": {"type": "array", "items": {"type": ["integer", "null"]}},
        },
    }
    assert count_union_params(schema) == 4


@pytest.mark.parametrize("edgar_available", [True, False])
def test_strict_tools_stay_within_union_type_limit(edgar_available):
    strict = [t for t in build_tools(edgar_available) if t.get("strict") is True]
    assert any(t["name"] == "submit_memo" for t in strict)
    total = sum(count_union_params(t["input_schema"]) for t in strict)
    assert total <= STRICT_UNION_LIMIT, f"{total} union-typed parameters in strict tools"


# Synthetic memo.
MEMO = {
    "ticker": "EXMP",
    "company": "Example Corp",
    "period": "Q3 2026",
    "report_date": "2026-10-01",
    "rating": "neutral",
    "conviction": "low",
    "headline": "Revenue up 20%.",
    "thesis": "Synthetic thesis.",
    "key_metrics": [],
    "guidance": None,
    "positives": [],
    "risks": [],
    "watch_items": [],
    "sources": [{"title": "Synthetic release", "url": "https://example.com/q3"}],
}


def _period(label, revenue):
    return {
        "label": label,
        "revenue": revenue,
        "gross_profit": None,
        "operating_income": revenue / 4,
        "net_income": None,
        "diluted_eps": None,
        "operating_cash_flow": None,
        "capex": None,
    }


METRICS_INPUT = {
    "current": _period("Q3 2026", 1200.0),
    "prior_year": _period("Q3 2025", 1000.0),
    "prior_quarter": None,
}


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(name, tool_input, id_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=tool_input)


def server_tool_use(name, tool_input, id_):
    return SimpleNamespace(type="server_tool_use", id=id_, name=name, input=tool_input)


def response(stop_reason, *blocks, stop_details=None, inp=10, out=5,
             cache_read=None, cache_creation=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=list(blocks),
        usage=SimpleNamespace(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        ),
    )


def submit():
    return response("tool_use", tool_use("submit_memo", copy.deepcopy(MEMO), "tu_submit"))


class FakeClient:
    """Stands in for anthropic.Anthropic: .beta.messages.create(**kwargs)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self._responses:
            raise AssertionError("fake client ran out of scripted responses")
        return self._responses.pop(0)


def assert_append_only(calls):
    for prev, cur in zip(calls, calls[1:]):
        p, c = prev["messages"], cur["messages"]
        assert len(c) > len(p)
        assert c[: len(p)] == p


def test_happy_path_compute_then_submit():
    events = []
    client = FakeClient(
        [
            response(
                "tool_use",
                server_tool_use("web_search", {"query": "EXMP Q3 2026 results"}, "srv_1"),
                server_tool_use("web_fetch", {"url": "https://example.com/q3"}, "srv_2"),
                tool_use("compute_metrics", METRICS_INPUT, "tu_1"),
            ),
            submit(),
        ]
    )
    result = run_agent("exmp", client=client, on_event=lambda k, d: events.append((k, d)))

    assert isinstance(result, AgentResult)
    assert result.memo == MEMO
    assert result.markdown == render_markdown(MEMO)
    assert result.requests == 2
    assert result.input_tokens == 20
    assert result.output_tokens == 10

    second = client.calls[1]["messages"]
    tool_results = second[-1]
    assert tool_results["role"] == "user"
    [res] = tool_results["content"]
    assert res["type"] == "tool_result"
    assert res["tool_use_id"] == "tu_1"
    assert "is_error" not in res
    assert '"revenue_growth": 0.2' in res["content"]

    kinds = [k for k, _ in events]
    assert kinds.count("request") == 2
    assert ("server_tool_use", "web_search: EXMP Q3 2026 results") in events
    assert ("server_tool_use", "web_fetch: https://example.com/q3") in events
    assert any(k == "tool" and d.startswith("compute_metrics") for k, d in events)
    assert_append_only(client.calls)


def test_request_shape():
    client = FakeClient([submit()])
    run_agent("EXMP", client=client, effort="medium")
    [call] = client.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 16000
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "medium"}
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert forbidden not in call
    assert call["cache_control"] == {"type": "ephemeral"}
    types = {t.get("type") for t in call["tools"]}
    assert {"web_search_20260209", "web_fetch_20260209"} <= types
    names = {t["name"] for t in call["tools"]}
    assert "code_execution" not in names
    by_name = {t["name"]: t for t in call["tools"]}
    assert by_name["web_search"]["max_uses"] == 5
    assert by_name["web_fetch"]["max_uses"] == 6
    assert "EXMP" in call["messages"][0]["content"]
    assert "sec.gov" in call["system"]


def test_without_edgar_tool_and_prompt_omit_company_facts():
    client = FakeClient([submit()])
    run_agent("EXMP", client=client)
    [call] = client.calls
    names = [t["name"] for t in call["tools"]]
    assert names == ["web_search", "web_fetch", "compute_metrics", "submit_memo"]
    assert "get_company_facts" not in call["system"]
    assert "compute_metrics" in call["system"]
    assert "submit_memo" in call["system"]
    # Steps are renumbered without a gap.
    assert "\n3. Call compute_metrics" in call["system"]
    assert "\n4. Call submit_memo" in call["system"]
    assert "\n5." not in call["system"]


def test_with_edgar_tool_and_prompt_include_company_facts():
    def unused_fetch(url):
        raise AssertionError("EDGAR should not be fetched in this test")

    client = FakeClient([submit()])
    run_agent("EXMP", client=client, edgar=EdgarClient(fetch=unused_fetch))
    [call] = client.calls
    names = [t["name"] for t in call["tools"]]
    assert names == [
        "web_search",
        "web_fetch",
        "compute_metrics",
        "get_company_facts",
        "submit_memo",
    ]
    assert "\n3. Call get_company_facts" in call["system"]
    assert "\n5. Call submit_memo" in call["system"]
    # The module-level names describe the with-EDGAR configuration.
    assert call["system"] == SYSTEM_PROMPT
    assert call["tools"] == TOOLS


def test_pause_turn_resends_without_new_user_message():
    paused = response(
        "pause_turn",
        server_tool_use("web_search", {"query": "EXMP"}, "srv_1"),
        SimpleNamespace(type="web_search_tool_result", tool_use_id="srv_1", content=[]),
    )
    client = FakeClient([paused, submit()])
    result = run_agent("EXMP", client=client)
    assert result.requests == 2
    msgs = client.calls[1]["messages"]
    assert len(msgs) == 2
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == paused.content
    assert_append_only(client.calls)


def test_pause_turn_is_capped():
    client = FakeClient([response("pause_turn", text("...")) for _ in range(4)])
    with pytest.raises(AgentError, match="pause_turn"):
        run_agent("EXMP", client=client, max_continuations=2)
    assert len(client.calls) == 3


def test_reminder_then_submit():
    client = FakeClient([response("end_turn", text("Done.")), submit()])
    result = run_agent("EXMP", client=client)
    assert result.memo == MEMO
    last = client.calls[1]["messages"][-1]
    assert last == {"role": "user", "content": REMINDER}
    assert_append_only(client.calls)


def test_second_miss_is_an_error():
    client = FakeClient([response("end_turn", text("a")), response("end_turn", text("b"))])
    with pytest.raises(AgentError, match="submit_memo"):
        run_agent("EXMP", client=client)
    assert len(client.calls) == 2
    assert_append_only(client.calls)


def test_refusal_with_stop_details():
    details = SimpleNamespace(category="cyber", explanation="synthetic reason")
    client = FakeClient([response("refusal", stop_details=details)])
    with pytest.raises(AgentRefused) as info:
        run_agent("EXMP", client=client)
    assert info.value.category == "cyber"
    assert info.value.explanation == "synthetic reason"
    assert "synthetic reason" in str(info.value)


def test_refusal_without_stop_details():
    client = FakeClient([response("refusal", stop_details=None)])
    with pytest.raises(AgentRefused) as info:
        run_agent("EXMP", client=client)
    assert info.value.category is None
    assert info.value.explanation is None


def test_max_tokens_is_an_error():
    client = FakeClient([response("max_tokens", text("cut"))])
    with pytest.raises(AgentError, match="max_tokens"):
        run_agent("EXMP", client=client)


def test_raising_and_unknown_tools_become_is_error():
    def broken_fetch(url):
        raise OSError("synthetic network down")

    client = FakeClient(
        [
            response(
                "tool_use",
                tool_use("compute_metrics", {"nope": 1}, "tu_bad"),
                tool_use("get_company_facts", {"ticker": "EXMP"}, "tu_edgar"),
                tool_use("no_such_tool", {}, "tu_unknown"),
                tool_use("compute_metrics", METRICS_INPUT, "tu_ok"),
            ),
            submit(),
        ]
    )
    run_agent("EXMP", client=client, edgar=EdgarClient(fetch=broken_fetch))
    user_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "user"]
    results = {r["tool_use_id"]: r for r in user_msgs[-1]["content"]}
    assert list(results) == ["tu_bad", "tu_edgar", "tu_unknown", "tu_ok"]
    assert results["tu_bad"]["is_error"] is True
    assert "current" in results["tu_bad"]["content"]
    assert results["tu_edgar"]["is_error"] is True
    assert "synthetic network down" in results["tu_edgar"]["content"]
    assert results["tu_unknown"]["is_error"] is True
    assert "no_such_tool" in results["tu_unknown"]["content"]
    assert "is_error" not in results["tu_ok"]


def test_company_facts_without_edgar_is_error():
    client = FakeClient(
        [
            response("tool_use", tool_use("get_company_facts", {"ticker": "EXMP"}, "tu_1")),
            submit(),
        ]
    )
    run_agent("EXMP", client=client)
    [res] = client.calls[1]["messages"][-1]["content"]
    assert res["is_error"] is True
    assert "EDGAR is not configured" in res["content"]


def test_max_requests():
    looping = [
        response("tool_use", tool_use("compute_metrics", METRICS_INPUT, f"tu_{i}"))
        for i in range(5)
    ]
    client = FakeClient(looping)
    with pytest.raises(AgentError, match="max_requests"):
        run_agent("EXMP", client=client, max_requests=3)
    assert len(client.calls) == 3
    assert_append_only(client.calls)


def test_history_is_append_only_across_mixed_turns():
    passthrough = SimpleNamespace(type="code_execution_tool_result", tool_use_id="x", content={})
    first = response(
        "pause_turn",
        SimpleNamespace(type="thinking", thinking="synthetic", signature="sig"),
        server_tool_use("code_execution", {"code": "print(1)"}, "srv_x"),
        passthrough,
    )
    client = FakeClient(
        [
            first,
            response("tool_use", tool_use("compute_metrics", METRICS_INPUT, "tu_1")),
            response("end_turn", text("done")),
            submit(),
        ]
    )
    run_agent("EXMP", client=client)
    assert len(client.calls) == 4
    assert_append_only(client.calls)
    # The assistant content is the response's content, unchanged.
    assert client.calls[1]["messages"][1] == {"role": "assistant", "content": first.content}


@pytest.mark.parametrize("with_edgar", [False, True])
def test_cached_prefix_is_stable_across_requests(with_edgar):
    def unused_fetch(url):
        raise AssertionError("EDGAR should not be fetched in this test")

    edgar = EdgarClient(fetch=unused_fetch) if with_edgar else None
    client = FakeClient(
        [
            response("pause_turn", server_tool_use("web_search", {"query": "EXMP"}, "s1")),
            response("tool_use", tool_use("compute_metrics", METRICS_INPUT, "tu_1")),
            response("end_turn", text("done")),
            submit(),
        ]
    )
    run_agent("EXMP", client=client, edgar=edgar)
    assert len(client.calls) == 4
    first = client.calls[0]
    for call in client.calls:
        assert call["cache_control"] == {"type": "ephemeral"}
        assert call["system"] == first["system"]
        assert call["tools"] == first["tools"]
        # Byte-stable, not merely equal.
        assert repr(call["system"]) == repr(first["system"])
        assert repr(call["tools"]) == repr(first["tools"])
    assert_append_only(client.calls)


def test_usage_accounting_with_cache_fields():
    client = FakeClient(
        [
            response("tool_use", tool_use("compute_metrics", METRICS_INPUT, "tu_1"),
                     inp=1000, out=200, cache_read=None, cache_creation=None),
            response("end_turn", text("done"),
                     inp=50, out=20, cache_read=900, cache_creation=None),
            response("tool_use", tool_use("submit_memo", copy.deepcopy(MEMO), "tu_s"),
                     inp=30, out=300, cache_read=1000, cache_creation=120),
        ]
    )
    result = run_agent("EXMP", client=client)
    assert result.requests == 3
    assert result.usage == [
        {"input_tokens": 1000, "output_tokens": 200,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        {"input_tokens": 50, "output_tokens": 20,
         "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0},
        {"input_tokens": 30, "output_tokens": 300,
         "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 120},
    ]
    assert result.input_tokens == 1080
    assert result.output_tokens == 520
    assert result.cache_read_input_tokens == 1900
    assert result.cache_creation_input_tokens == 120


def test_usage_without_cache_attributes_counts_zero():
    resp = SimpleNamespace(
        stop_reason="tool_use",
        stop_details=None,
        content=[tool_use("submit_memo", copy.deepcopy(MEMO), "tu_s")],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )
    result = run_agent("EXMP", client=FakeClient([resp]))
    assert result.cache_read_input_tokens == 0
    assert result.cache_creation_input_tokens == 0
    assert result.usage == [{"input_tokens": 7, "output_tokens": 3,
                             "cache_read_input_tokens": 0,
                             "cache_creation_input_tokens": 0}]


def test_calculations_record_success_and_error():
    client = FakeClient(
        [
            response(
                "tool_use",
                tool_use("compute_metrics", {"nope": 1}, "tu_bad"),
                tool_use("get_company_facts", {"ticker": "EXMP"}, "tu_edgar"),
                tool_use("compute_metrics", copy.deepcopy(METRICS_INPUT), "tu_ok"),
            ),
            submit(),
        ]
    )
    result = run_agent("EXMP", client=client)
    assert [c["tool_use_id"] for c in result.calculations] == ["tu_bad", "tu_ok"]
    bad, ok = result.calculations
    assert bad["input"] == {"nope": 1}
    assert "output" not in bad
    assert "current" in bad["error"]
    assert ok["input"] == METRICS_INPUT
    assert "error" not in ok
    assert ok["output"]["yoy"]["revenue_growth"] == pytest.approx(0.2)
    # The recorded output is what the model was sent.
    results = {r["tool_use_id"]: r for r in client.calls[1]["messages"][-1]["content"]}
    assert json.loads(results["tu_ok"]["content"]) == ok["output"]
    assert results["tu_bad"]["content"] == bad["error"]


def test_no_calculations_when_metrics_not_called():
    result = run_agent("EXMP", client=FakeClient([submit()]))
    assert result.calculations == []


def test_submit_memo_ends_run_immediately():
    client = FakeClient(
        [
            response(
                "tool_use",
                tool_use("compute_metrics", METRICS_INPUT, "tu_1"),
                tool_use("submit_memo", copy.deepcopy(MEMO), "tu_2"),
            )
        ]
    )
    result = run_agent("EXMP", client=client)
    assert result.requests == 1
    assert result.memo == MEMO
