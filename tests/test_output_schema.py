"""exec --output-schema FILE (D-051): the run's result, as JSON of a shape the
caller chose.

The schema becomes a `result` argument of finish, checked by Foundry itself
against a JSON Schema subset -- not trusted to the model or the provider -- so
a result that does not match goes back to the model as an invalid call, and
what a script reads on stdout is the result and nothing else.
"""

from __future__ import annotations

import json

import pytest

from foundry.cli import app
from foundry.cli.unattended import FINISH_REMINDER, RESULT_REMINDER
from foundry.core.errors import InvalidToolCall
from foundry.core.events import EventSink, TerminalStatus, Termination
from foundry.core.json_schema import (
    MAX_DEPTH,
    MAX_PROBLEMS,
    SchemaError,
    _resolve,
    check_schema,
    embedded,
    problems,
)
from foundry.core.redaction import Redactor
from foundry.core.session import EventType, SessionStore
from foundry.core.tools.finish import RESULT_NOTE, Finish

from test_cli_flags import _exec_home
from test_resume import _core, call, fake_model, repo, turn  # noqa: F401 (fixtures)

PERSON = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "description": "who"},
        "age": {"type": "integer", "minimum": 0},
        "tags": {"type": "array", "items": {"$ref": "#/$defs/tag"}, "uniqueItems": True},
    },
    "required": ["name", "age"],
    "additionalProperties": False,
    "$defs": {"tag": {"type": "string", "enum": ["a", "b"]}},
    "title": "A person",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
}


# --- the schema -----------------------------------------------------------------------------


def test_a_schema_of_the_subset_is_accepted():
    check_schema(PERSON)
    check_schema(True)
    check_schema({"anyOf": [{"type": ["string", "null"]}, {"const": 3}],
                  "definitions": {"x": {"not": {"type": "object"}}}})


@pytest.mark.parametrize("schema, where", [
    ({"patternProperties": {"x": {}}}, "#: patternProperties is not supported"),
    ({"properties": {"a": {"if": {}, "then": {}}}}, "#/properties/a: if, then is not supported"),
    ({"prefixItems": [{}]}, "prefixItems is not supported"),
    ({"requird": ["a"]}, "requird is not supported"),
    ({"items": [{}, {}]}, "#/items: a list of schemas (tuple form) is not supported"),
    ({"type": "str"}, "#/type: each type is one of"),
    ({"type": []}, "#/type"),
    ({"enum": []}, "#/enum: a non-empty list"),
    ({"required": "a"}, "#/required: a list of property names"),
    ({"minLength": -1}, "#/minLength: a whole number"),
    ({"maxItems": True}, "#/maxItems: a whole number"),
    ({"minimum": "1"}, "#/minimum: a number"),
    ({"multipleOf": 0}, "#/multipleOf: a number above 0"),
    ({"uniqueItems": "yes"}, "#/uniqueItems: true or false"),
    ({"pattern": "("}, "#/pattern:"),
    ({"pattern": 3}, "#/pattern: a regular expression"),
    ({"$ref": "#/$defs/missing"}, "does not name a part of this schema"),
    ({"$ref": "https://example.com/s.json"}, "does not name a part of this schema"),
    ({"properties": ["a"]}, "#/properties: an object"),
    ({"anyOf": []}, "#/anyOf: a non-empty list"),
    ({"$defs": []}, "#/$defs: an object of schemas"),
    ({"additionalProperties": 3}, "#/additionalProperties: a schema is an object"),
    ([{"type": "string"}], "#: a schema is an object or true/false"),
])
def test_anything_else_is_refused_and_named(schema, where):
    with pytest.raises(SchemaError) as caught:
        check_schema(schema)
    assert where in str(caught.value)


def test_a_schema_nested_too_deeply_is_refused():
    schema: dict = {"type": "string"}
    for _ in range(MAX_DEPTH + 2):
        schema = {"items": schema}
    with pytest.raises(SchemaError, match="nests more than"):
        check_schema(schema)


# --- a value --------------------------------------------------------------------------------


@pytest.mark.parametrize("value, schema, expected", [
    ({"name": "Ann", "age": 3, "tags": ["a"]}, PERSON, None),
    ({"name": "Ann", "age": 3.0}, PERSON, None),                     # 3.0 is an integer
    ({"name": "Ann", "age": True}, PERSON, "$.age: expected integer, got boolean"),
    ({"name": "", "age": 3}, PERSON, "$.name: shorter than 1 characters"),
    ({"name": "Ann"}, PERSON, "$: 'age' is required"),
    ({"name": "Ann", "age": 3, "x": 1}, PERSON, "$: 'x' is not allowed"),
    ({"name": "Ann", "age": -1}, PERSON, "$.age: below the minimum 0"),
    ({"name": "Ann", "age": 3, "tags": ["a", "c"]}, PERSON, "$.tags[1]: must be one of"),
    ({"name": "Ann", "age": 3, "tags": ["a", "a"]}, PERSON, "$.tags: items are not unique"),
    ([], PERSON, "$: expected object, got array"),
    (None, {"type": "null"}, None),
    (1.5, {"type": "number"}, None),
    (1.5, {"type": "integer"}, "expected integer, got number"),
    (False, {"type": "number"}, "expected number, got boolean"),
    (1, {"enum": [1.0]}, None),
    (1, {"const": True}, "$: must be true"),
    (True, {"enum": [1]}, "must be one of [1]"),
    ("abc", {"maxLength": 2}, "longer than 2 characters"),
    ("abc", {"pattern": "^a"}, None),
    ("xbc", {"pattern": "^a"}, "does not match '^a'"),
    (5, {"maximum": 4}, "above the maximum 4"),
    (4, {"exclusiveMinimum": 4}, "not above 4"),
    (4, {"exclusiveMaximum": 4}, "not below 4"),
    (0.3, {"multipleOf": 0.1}, None),
    (0.35, {"multipleOf": 0.1}, "not a multiple of 0.1"),
    ([1], {"minItems": 2}, "fewer than 2 items"),
    ([1, 2, 3], {"maxItems": 2}, "more than 2 items"),
    ({}, {"minProperties": 1}, "fewer than 1 properties"),
    ({"a": 1, "b": 2}, {"maxProperties": 1}, "more than 1 properties"),
    ({"a b": 1}, {"additionalProperties": {"type": "string"}}, '$["a b"]: expected string'),
    ({"a": {"b": 1}}, {"properties": {"a": {"properties": {"b": {"type": "string"}}}}},
     "$.a.b: expected string, got integer"),
    (3, {"allOf": [{"type": "integer"}, {"minimum": 5}]}, "below the minimum 5"),
    (3, {"anyOf": [{"type": "string"}, {"type": "null"}]}, "matches none of the 2 choices"),
    (3, {"oneOf": [{"type": "integer"}, {"minimum": 0}]}, "matches 2 of the choices in oneOf"),
    (3, {"oneOf": [{"type": "integer"}, {"type": "string"}]}, None),
    (3, {"not": {"type": "integer"}}, "matches the schema under not"),
    (3, False, "no value is allowed here"),
    (3, True, None),
])
def test_a_value_is_checked_against_the_schema(value, schema, expected):
    check_schema(schema)
    found = problems(value, schema)
    if expected is None:
        assert found == []
    else:
        assert any(expected in p for p in found), found


def test_a_schema_may_refer_to_itself_for_nested_data():
    tree = {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#"}}},
            "required": ["kids"]}
    check_schema(tree)
    assert problems({"kids": [{"kids": []}, {"kids": [{"kids": []}]}]}, tree) == []
    assert problems({"kids": [{"kids": [{}]}]}, tree) == ["$.kids[0].kids[0]: 'kids' is required"]


def test_a_reference_that_loops_on_itself_ends():
    loop = {"$ref": "#"}
    check_schema(loop)
    assert problems(1, loop) == [f"$: the schema refers to itself more than {MAX_DEPTH} times"]


def test_only_the_first_few_problems_are_named():
    schema = {"type": "array", "items": {"type": "string"}}
    assert len(problems(list(range(50)), schema)) == MAX_PROBLEMS


# --- inside finish's parameters -------------------------------------------------------------


def test_the_schema_is_moved_under_result_with_its_refs():
    schema = {"type": "object",
              "properties": {"enum": {"$ref": "#/$defs/tag"},       # a property named enum
                             "next": {"$ref": "#"},
                             "same": {"$ref": "#/properties/enum"}},
              "enum": [{"$ref": "a value, not a ref"}],
              "$defs": {"tag": {"anyOf": [{"$ref": "#/properties/next"}, {"type": "null"}]}}}
    check_schema(schema)
    moved, definitions = embedded(schema, "#/properties/result")
    assert definitions == {"$defs": {"tag": {"anyOf": [
        {"$ref": "#/properties/result/properties/next"}, {"type": "null"}]}}}
    assert moved["properties"] == {"enum": {"$ref": "#/$defs/tag"},
                                   "next": {"$ref": "#/properties/result"},
                                   "same": {"$ref": "#/properties/result/properties/enum"}}
    assert moved["enum"] == [{"$ref": "a value, not a ref"}] and "$defs" not in moved
    # Every ref resolves in the document it now lives in.
    document = {"properties": {"result": moved}, **definitions}
    for ref in ("#/$defs/tag", "#/properties/result", "#/properties/result/properties/enum",
                "#/properties/result/properties/next"):
        assert _resolve(document, ref) is not None


def test_finish_without_a_schema_takes_no_result():
    tool = Finish()
    assert "result" not in tool.schema().parameters["properties"]
    with pytest.raises(InvalidToolCall, match="unknown argument"):
        tool.validate({"status": "completed", "summary": "s", "result": 1})


def test_finish_with_one_asks_for_it():
    tool = Finish(result_schema=PERSON)
    schema = tool.schema()
    assert schema.description.endswith(RESULT_NOTE)
    assert schema.parameters["properties"]["result"]["properties"]["tags"]["items"] == \
        {"$ref": "#/$defs/tag"}
    assert schema.parameters["$defs"] == PERSON["$defs"]
    assert schema.parameters["required"] == ["status", "summary"]
    assert Finish().schema().parameters == Finish()._schema().parameters


def test_a_completed_finish_needs_a_result_that_matches():
    tool = Finish(result_schema=PERSON)
    with pytest.raises(InvalidToolCall, match="this run asks for a result"):
        tool.validate({"status": "completed", "summary": "s"})
    with pytest.raises(InvalidToolCall, match=r"\$: 'age' is required"):
        tool.validate({"status": "completed", "summary": "s", "result": {"name": "A"}})
    with pytest.raises(InvalidToolCall, match="plain JSON"):
        Finish(result_schema=True).validate({"status": "completed", "summary": "s",
                                             "result": float("nan")})
    op = tool.validate({"status": "completed", "summary": "s",
                        "result": {"name": "A", "age": 1}})
    assert tool.execute(op, None).metadata["result"] == {"name": "A", "age": 1}


def test_another_status_may_come_without_one():
    tool = Finish(result_schema=PERSON)
    op = tool.validate({"status": "blocked", "summary": "no access"})
    assert "result" not in tool.execute(op, None).metadata
    with pytest.raises(InvalidToolCall):
        tool.validate({"status": "partial", "summary": "s", "result": {"name": 1, "age": 1}})


# --- the runtime ----------------------------------------------------------------------------


def _finish(result=..., status="completed", cid="f1"):
    args = {"status": status, "summary": "done", "claims": []}
    if result is not ...:
        args["result"] = result
    return turn(call(cid, "finish", args))


def test_a_result_that_does_not_match_goes_back_to_the_model(tmp_path):
    runtime, session, _ = _core(tmp_path, [_finish({"name": "A"}, cid="f1"),
                                           _finish({"name": "A", "age": 2}, cid="f2")])
    runtime.registry.get("finish").result_schema = PERSON
    events: list = []
    runtime.events.subscribe(events.append)
    outcome = runtime.run_turn("who?")
    assert outcome.status is TerminalStatus.COMPLETED
    assert json.loads(outcome.result_json) == {"name": "A", "age": 2}
    replies = [r.payload for r in SessionStore.read_records(session.path)
               if r.type == EventType.TOOL_REPLY]
    assert replies[0]["is_error"] and "'age' is required" in replies[0]["content"]
    (termination,) = [r.payload for r in SessionStore.read_records(session.path)
                      if r.type == EventType.TERMINATION]
    assert termination["result"] == {"name": "A", "age": 2}
    (event,) = [e for e in events if isinstance(e, Termination)]
    assert event.result == {"name": "A", "age": 2}


def test_without_a_schema_nothing_changes(tmp_path):
    runtime, session, _ = _core(tmp_path, [_finish()])
    outcome = runtime.run_turn("go")
    assert outcome.result_json is None
    (termination,) = [r.payload for r in SessionStore.read_records(session.path)
                      if r.type == EventType.TERMINATION]
    assert "result" not in termination


def test_a_result_in_an_event_is_scrubbed():
    redactor = Redactor()
    redactor.register("tok-0123456789-secret")
    seen: list = []
    sink = EventSink(redactor=redactor)
    sink.subscribe(seen.append)
    result = {"key": "tok-0123456789-secret", "list": ["a", "tok-0123456789-secret"],
              "tok-0123456789-secret": 1, "n": 2}
    sink.emit(Termination(TerminalStatus.COMPLETED, "r", "s", result=result))
    assert "tok-0123" not in json.dumps(seen[0].result)
    clean = {"a": ["b", {"c": 1}]}
    sink.emit(Termination(TerminalStatus.COMPLETED, "r", "s", result=clean))
    assert seen[1].result is clean


# --- exec --------------------------------------------------------------------------------------


def _schema_file(tmp_path, schema=PERSON):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def test_exec_prints_the_result_and_nothing_else(repo, tmp_path, monkeypatch, capsys,
                                                 fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [
        tool_turn("finish", {"status": "completed", "summary": "found her", "claims": [],
                             "result": {"name": "Zoë", "age": 30}}, "f1")], 0
    out = tmp_path / "last.json"
    code = app.main(["exec", "who?", "--workspace", str(repo),
                     "--output-schema", str(_schema_file(tmp_path)), "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {"name": "Zoë", "age": 30}
    assert captured.out.strip() == '{"name": "Zoë", "age": 30}'
    assert json.loads(out.read_text(encoding="utf-8")) == {"name": "Zoë", "age": 30}
    (finish,) = [t["function"] for t in httpd.requests[0]["tools"]
                 if t["function"]["name"] == "finish"]
    assert finish["parameters"]["properties"]["result"]["required"] == ["name", "age"]


def test_exec_reminds_the_model_of_the_result(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import text_turn, tool_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [
        text_turn("Ann, 30"),
        tool_turn("finish", {"status": "completed", "summary": "s", "claims": [],
                             "result": {"name": "Ann", "age": 30}}, "f1")], 0
    code = app.main(["exec", "who?", "--workspace", str(repo),
                     "--output-schema", str(_schema_file(tmp_path))])
    assert code == 0 and json.loads(capsys.readouterr().out)["name"] == "Ann"
    reminder = httpd.requests[1]["messages"][-1]["content"]
    assert FINISH_REMINDER in reminder and RESULT_REMINDER in reminder


def test_exec_without_a_result_leaves_stdout_empty(repo, tmp_path, monkeypatch, capsys,
                                                   fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [
        tool_turn("finish", {"status": "blocked", "summary": "no access to the data",
                             "claims": []}, "f1")], 0
    out = tmp_path / "last.json"
    code = app.main(["exec", "who?", "--workspace", str(repo),
                     "--output-schema", str(_schema_file(tmp_path)), "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 11 and captured.out == ""
    assert "no access to the data" in captured.err
    assert not out.exists()


def test_exec_json_carries_the_result_in_the_termination_event(repo, tmp_path, monkeypatch,
                                                               capsys, fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    handler.turns, handler.index = [
        tool_turn("finish", {"status": "completed", "summary": "s", "claims": [],
                             "result": {"name": "Ann", "age": 1}}, "f1")], 0
    code = app.main(["exec", "who?", "--workspace", str(repo), "--json",
                     "--output-schema", str(_schema_file(tmp_path))])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0
    (termination,) = [e for e in lines if e["kind"] == "termination"]
    assert termination["result"] == {"name": "Ann", "age": 1}


def test_exec_scrubs_the_result(repo, tmp_path, monkeypatch, capsys, fake_model):
    from test_cli_e2e import tool_turn

    httpd, handler = fake_model
    _exec_home(tmp_path, httpd, monkeypatch)
    secret = "s3cr3t-result-value-0123456789"
    redactor = Redactor()
    redactor.register(secret)
    monkeypatch.setattr("foundry.cli.unattended.default_redactor", lambda: redactor)
    monkeypatch.setattr("foundry.cli.wiring.default_redactor", lambda: redactor)
    handler.turns, handler.index = [
        tool_turn("finish", {"status": "completed", "summary": "s", "claims": [],
                             "result": {"name": secret, "age": 1}}, "f1")], 0
    app.main(["exec", "who?", "--workspace", str(repo),
              "--output-schema", str(_schema_file(tmp_path))])
    assert json.loads(capsys.readouterr().out) == {"name": "[redacted]", "age": 1}


@pytest.mark.parametrize("content, message", [
    (json.dumps({"if": {}}), "#: if is not supported"),
    ("{not json", "not JSON"),
    (json.dumps({"description": "x" * 70_000}), "larger than 64 KB"),
], ids=["unsupported", "not-json", "too-large"])
def test_exec_refuses_a_schema_it_cannot_enforce(repo, tmp_path, capsys, content, message):
    path = tmp_path / "schema.json"
    path.write_text(content, encoding="utf-8")
    assert app.main(["exec", "who?", "--workspace", str(repo), "--output-schema",
                     str(path)]) == 2
    assert message in capsys.readouterr().err


def test_exec_refuses_a_missing_schema_file(repo, tmp_path, capsys):
    assert app.main(["exec", "who?", "--workspace", str(repo), "--output-schema",
                     str(tmp_path / "nope.json")]) == 2
    assert "--output-schema" in capsys.readouterr().err
