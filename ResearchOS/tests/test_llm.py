import json
from types import SimpleNamespace

import anthropic
import pytest

from conftest import FakeClient, text_response

from researchos import masking, perspectives, reflect
from researchos.llm import ClaudeJSON, LLMError

SCHEMA = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"],
          "additionalProperties": False}


def client_returning(resp):
    return FakeClient(lambda kwargs: resp)


def test_request_shape_without_prefix():
    client = client_returning(text_response('{"a": 1}'))
    llm = ClaudeJSON(client)
    assert llm.json("sys", "hello", SCHEMA) == {"a": 1}
    (req,) = client.requests
    assert req == {
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        "system": "sys",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
    }
    for banned in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert banned not in req


def test_request_with_prefix_puts_cache_breakpoint_on_prefix():
    client = client_returning(text_response('{"a": 2}'))
    llm = ClaudeJSON(client, model="m", effort="medium", max_tokens=99)
    llm.json("sys", "tail", SCHEMA, prefix="DOCS")
    req = client.requests[0]
    assert req["model"] == "m" and req["max_tokens"] == 99
    assert req["output_config"]["effort"] == "medium"
    assert req["messages"][0]["content"] == [
        {"type": "text", "text": "DOCS", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "tail"},
    ]


def test_refusal_with_and_without_category():
    resp = text_response("{}", stop_reason="refusal")
    resp.stop_details = SimpleNamespace(category="cyber")
    with pytest.raises(LLMError, match="cyber"):
        ClaudeJSON(client_returning(resp)).json("s", "p", SCHEMA)
    resp.stop_details = None
    with pytest.raises(LLMError, match="refused"):
        ClaudeJSON(client_returning(resp)).json("s", "p", SCHEMA)


def test_max_tokens_raises():
    with pytest.raises(LLMError, match="max_tokens"):
        ClaudeJSON(client_returning(text_response('{"a"', stop_reason="max_tokens"))).json(
            "s", "p", SCHEMA)


def test_no_text_block_and_invalid_json():
    resp = text_response("x")
    resp.content = [SimpleNamespace(type="thinking", thinking="...")]
    with pytest.raises(LLMError, match="no text"):
        ClaudeJSON(client_returning(resp)).json("s", "p", SCHEMA)
    with pytest.raises(LLMError, match="JSON"):
        ClaudeJSON(client_returning(text_response("not json"))).json("s", "p", SCHEMA)


def test_first_text_block_is_used():
    resp = text_response('{"a": 1}')
    resp.content.append(SimpleNamespace(type="text", text='{"a": 2}'))
    assert ClaudeJSON(client_returning(resp)).json("s", "p", SCHEMA) == {"a": 1}


def test_usage_totals_count_none_as_zero():
    llm = ClaudeJSON(client_returning(text_response('{"a": 1}')))
    llm.json("s", "p", SCHEMA)
    llm.json("s", "p", SCHEMA)
    assert llm.usage == {"input_tokens": 20, "output_tokens": 10,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 6}


def test_api_error_becomes_llm_error():
    def boom(kwargs):
        exc = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
        Exception.__init__(exc, "connection reset")
        raise exc

    with pytest.raises(LLMError):
        ClaudeJSON(FakeClient(boom)).json("s", "p", SCHEMA)


def _check_strict(node, path="schema"):
    assert "anyOf" not in node and "oneOf" not in node, path
    assert not isinstance(node.get("type"), list), path
    if node.get("type") == "object":
        assert node["additionalProperties"] is False, path
        assert sorted(node["required"]) == sorted(node["properties"]), path
        for key, child in node["properties"].items():
            _check_strict(child, f"{path}.{key}")
    if node.get("type") == "array":
        _check_strict(node["items"], f"{path}[]")


@pytest.mark.parametrize("schema", [masking.MASKER_SCHEMA, masking.GUESSER_SCHEMA,
                                    perspectives.VIEW_SCHEMA, perspectives.JUDGE_SCHEMA,
                                    reflect.REFLECT_SCHEMA])
def test_schemas_are_strict(schema):
    _check_strict(schema)
    json.dumps(schema)


def test_only_llm_imports_anthropic():
    from pathlib import Path

    pkg = Path(masking.__file__).parent
    for path in pkg.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        source.encode("ascii")  # source files are ASCII
        if path.name != "llm.py":
            assert "import anthropic" not in source, path.name
