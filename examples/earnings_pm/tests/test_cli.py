"""Tests for the earnings_pm CLI with a fake Claude client.

All responses and memos below are synthetic and hand-written; nothing touches
the network and no API key is needed.
"""

import copy
import json
from types import SimpleNamespace

import anthropic
import pytest

from earnings_pm.__main__ import main
from earnings_pm.memo import render_markdown

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


def response(stop_reason, *blocks, stop_details=None, inp=10, out=5):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=inp, output_tokens=out),
    )


def submit():
    block = SimpleNamespace(
        type="tool_use", id="tu_submit", name="submit_memo", input=copy.deepcopy(MEMO)
    )
    return response("tool_use", block, inp=100, out=50)


class FakeClient:
    """Stands in for anthropic.Anthropic; each scripted item is returned or raised."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def factory_for(client):
    return lambda: client


# The SDK's errors only store the request and read .request, .status_code and
# .headers from the response, so plain stand-ins build them without a network
# or a particular HTTP library.
FAKE_REQUEST = SimpleNamespace(method="POST", url="https://api.example.invalid/v1/messages")


def _status_error(cls, status, message):
    resp = SimpleNamespace(request=FAKE_REQUEST, status_code=status, headers={})
    return cls(message, response=resp, body=None)


@pytest.fixture(autouse=True)
def no_edgar(monkeypatch):
    monkeypatch.delenv("EARNINGS_PM_USER_AGENT", raising=False)


def test_success_with_out(tmp_path, capsys):
    client = FakeClient([submit()])
    out_dir = tmp_path / "memos"
    code = main(
        ["exmp", "--effort", "medium", "--max-requests", "3", "--out", str(out_dir)],
        client_factory=factory_for(client),
    )
    assert code == 0

    captured = capsys.readouterr()
    assert captured.out.rstrip("\n") == render_markdown(MEMO).rstrip("\n")
    err_lines = captured.err.splitlines()
    assert any(line.startswith("[request] request 1") for line in err_lines)
    assert any(line.startswith("[tool] submit_memo") for line in err_lines)

    md = (out_dir / "EXMP.md").read_text(encoding="utf-8")
    assert md == render_markdown(MEMO)
    record = json.loads((out_dir / "EXMP.json").read_text(encoding="utf-8"))
    assert record == {
        "memo": MEMO,
        "model": "claude-opus-5-5",
        "effort": "medium",
        "requests": 1,
        "input_tokens": 100,
        "output_tokens": 50,
    }

    [call] = client.calls
    assert call["output_config"] == {"effort": "medium"}
    # No EARNINGS_PM_USER_AGENT: EDGAR is not offered.
    assert "get_company_facts" not in {t["name"] for t in call["tools"]}


def test_edgar_enabled_by_user_agent(monkeypatch, capsys):
    monkeypatch.setenv("EARNINGS_PM_USER_AGENT", "Synthetic Tester test@example.com")
    client = FakeClient([submit()])
    assert main(["EXMP"], client_factory=factory_for(client)) == 0
    [call] = client.calls
    assert "get_company_facts" in {t["name"] for t in call["tools"]}


def test_refusal_exit_3(capsys):
    details = SimpleNamespace(category="cyber", explanation="synthetic reason")
    client = FakeClient([response("refusal", stop_details=details)])
    assert main(["EXMP"], client_factory=factory_for(client)) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "synthetic reason" in captured.err


def test_agent_error_exit_4(capsys):
    client = FakeClient([response("max_tokens")])
    assert main(["EXMP"], client_factory=factory_for(client)) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "max_tokens" in captured.err


def test_api_error_exit_5(capsys):
    error = _status_error(anthropic.BadRequestError, 400, "synthetic bad request")
    client = FakeClient([error])
    assert main(["EXMP"], client_factory=factory_for(client)) == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "synthetic bad request" in captured.err


def test_connection_error_exit_5(capsys):
    client = FakeClient([anthropic.APIConnectionError(request=FAKE_REQUEST)])
    assert main(["EXMP"], client_factory=factory_for(client)) == 5
    assert "APIConnectionError" in capsys.readouterr().err


def test_authentication_error_names_api_key(capsys):
    error = _status_error(anthropic.AuthenticationError, 401, "synthetic invalid key")
    client = FakeClient([error])
    assert main(["EXMP"], client_factory=factory_for(client)) == 5
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["EXMP", "--effort", "extreme"],
        ["EXMP", "--max-requests", "0"],
        ["EXMP", "--max-requests", "x"],
        ["../etc"],
    ],
)
def test_bad_arguments_exit_2(argv, capsys):
    def factory():
        raise AssertionError("client must not be built for bad arguments")

    assert main(argv, client_factory=factory) == 2
    assert capsys.readouterr().out == ""