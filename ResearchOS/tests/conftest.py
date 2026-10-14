import json
from types import SimpleNamespace

import pytest

from researchos.cases import Case, Document, Outcome


class FakeLLM:
    """Records every call; answers with fn(system, prompt, schema, prefix) or a queue."""

    def __init__(self, fn=None, responses=None):
        self.fn = fn
        self.responses = list(responses or [])
        self.calls = []

    def json(self, system, prompt, schema, *, prefix=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "prefix": prefix})
        if self.fn is not None:
            return self.fn(system, prompt, schema, prefix)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def texts(self):
        out = []
        for c in self.calls:
            out.extend([c["system"], c["prompt"], c["prefix"] or ""])
        return out


def make_case(case_id="acme-q4"):
    release = Document(
        kind="release",
        title="Acme Widgets Inc. reports fourth quarter FY2025 results",
        text=("Acme Widgets Inc. (NASDAQ: ACMW) reported revenue of $1,200.0 million for the "
              "fourth quarter of 2025, up 12% from 2024. WidgetPro sales grew 30%. "
              "CEO Jane Roe said demand for the Zephyr line was strong."),
        source="https://acme.example.com/ir/q4",
    )
    transcript = Document(
        kind="transcript",
        title="ACMW Q4 FY2025 earnings call",
        text=("Jane Roe: Thanks. acme delivered a record quarter. Gross margin was 41%. "
              "We expect revenue of $5 billion in 2026."),
        source="https://example.com/transcripts/acmw",
    )
    expectations = Document(
        kind="expectations",
        title="Consensus",
        text="Consensus revenue was $1,150.0 million; EPS $0.95.",
    )
    return Case(
        case_id=case_id,
        company="Acme Widgets Inc.",
        ticker="ACMW",
        aliases=["Acme", "Jane Roe", "WidgetPro", ""],
        fiscal_period="Q4 FY2025",
        event_date="2026-02-01",
        documents=[release, transcript, expectations],
    )


def make_outcome(ar=0.031, notes="Acme Widgets shares jumped after WidgetPro beat."):
    return Outcome(abnormal_return_1d=ar, stock_return_1d=0.04, market_return_1d=0.009,
                   notes=notes, sources=["https://news.example.com/acme"])


@pytest.fixture
def case():
    return make_case()


# ---- canned agent behaviour, dispatched on the schema ------------------------------

def schema_kind(schema):
    props = set(schema["properties"])
    if "redactions" in props:
        return "masker"
    if "guesses" in props:
        return "guesser"
    if "brief" in props:
        return "abstractor"
    if "stance" in props:
        return "view"
    if "thesis" in props:
        return "judge"
    return "reflect"


VIEW_PROBS = {"growth": 0.8, "profitability": 0.7, "guidance": 0.6, "cash_flow": 0.55,
              "management_tone": 0.5, "expectations_gap": 0.3, "risks": 0.2}


def default_answer(schema, prompt=""):
    kind = schema_kind(schema)
    if kind == "masker":
        return {"redactions": [{"term": "Zephyr", "replacement": "the product line",
                                "reason": "brand"}]}
    if kind == "guesser":
        return {"guesses": [{"company": "Globex", "ticker": "GBX", "confidence": 0.2}],
                "clues": []}
    if kind == "view":
        name = prompt.splitlines()[0].split(": ", 1)[1]
        return {"stance": "bullish", "prob_up": VIEW_PROBS[name], "conviction": "medium",
                "key_points": [f"{name} point"]}
    if kind == "judge":
        return {"prob_up": 0.65, "thesis": "Beat and raise."}
    if kind == "abstractor":
        return {"brief": "Revenue grew 12% from the prior year and beat expectations; gross "
                         "margin was 41%; guidance was raised."}
    return {"why_market_moved": "beat", "where_we_were_wrong": "nowhere",
            "lessons": [{"lesson": "Guidance raises dominate the reaction.",
                         "applies_when": "guidance is raised", "perspective": "guidance"}]}


def make_roles(**overrides):
    from researchos.loop import Roles

    def fn(system, prompt, schema, prefix):
        return default_answer(schema, prompt)

    roles = {name: FakeLLM(fn) for name in ("masker", "guesser", "analyst", "judge", "reflector")}
    roles.update(overrides)
    return Roles(**roles)


class FakeClient:
    """Stands in for anthropic.Anthropic(): client.beta.messages.create(**kwargs)."""

    def __init__(self, responder):
        self.requests = []
        self.responder = responder
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responder(kwargs)


def text_response(text, stop_reason="end_turn", usage=None, thinking=True):
    content = []
    if thinking:
        content.append(SimpleNamespace(type="thinking", thinking="hmm", signature="x"))
    content.append(SimpleNamespace(type="text", text=text))
    usage = usage or SimpleNamespace(input_tokens=10, output_tokens=5,
                                     cache_read_input_tokens=None,
                                     cache_creation_input_tokens=3)
    return SimpleNamespace(stop_reason=stop_reason, stop_details=None, content=content,
                           usage=usage)


def schema_client():
    def responder(kwargs):
        schema = kwargs["output_config"]["format"]["schema"]
        prompt = kwargs["messages"][0]["content"][-1]["text"]
        return text_response(json.dumps(default_answer(schema, prompt)))

    return FakeClient(responder)
