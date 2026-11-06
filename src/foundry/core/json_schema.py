"""A subset of JSON Schema, checked with the standard library (D-051).

``exec --output-schema`` asks for a final result of a shape the caller chose,
and Foundry checks the result itself rather than trusting the model or the
provider. The subset is what structured-output APIs accept: types,
``properties`` and ``required``, ``additionalProperties``, ``items``,
``enum`` and ``const``, ``anyOf``/``oneOf``/``allOf``/``not``, ``$ref`` into
the schema's own ``$defs``, and the bounds on strings, numbers, arrays and
objects. A schema that uses anything else is refused before the run starts,
so no keyword is silently left unenforced.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

# Words about the schema that constrain nothing.
ANNOTATIONS = frozenset({"title", "description", "default", "examples", "$schema", "$id",
                         "$comment", "format", "deprecated", "readOnly", "writeOnly"})
ASSERTIONS = frozenset({"type", "enum", "const", "properties", "required",
                        "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
                        "minLength", "maxLength", "pattern", "minimum", "maximum",
                        "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minProperties",
                        "maxProperties", "anyOf", "oneOf", "allOf", "not", "$ref"})
DEFINITIONS = ("$defs", "definitions")
TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")
_COUNTS = ("minItems", "maxItems", "minLength", "maxLength", "minProperties", "maxProperties")
_BOUNDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
MAX_DEPTH = 64
MAX_PROBLEMS = 5


class SchemaError(ValueError):
    """The schema uses something this module does not enforce, or is malformed."""


# --- the schema ---------------------------------------------------------------------------


def check_schema(schema: Any) -> None:
    """Raise SchemaError naming the first part of ``schema`` that would not
    be enforced."""
    _check(schema, "#", schema, 0)


def _check(node: Any, where: str, root: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise SchemaError(f"{where}: the schema nests more than {MAX_DEPTH} levels")
    if isinstance(node, bool):
        return
    if not isinstance(node, dict):
        raise SchemaError(f"{where}: a schema is an object or true/false")
    unknown = sorted(set(node) - ANNOTATIONS - ASSERTIONS - set(DEFINITIONS))
    if unknown:
        raise SchemaError(f"{where}: {', '.join(unknown)} is not supported "
                          "(supported: " + ", ".join(sorted(ASSERTIONS)) + ")")
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not types or any(t not in TYPES for t in types):
            raise SchemaError(f"{where}/type: each type is one of {', '.join(TYPES)}")
    if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
        raise SchemaError(f"{where}/enum: a non-empty list")
    if "required" in node and not (isinstance(node["required"], list)
                                   and all(isinstance(k, str) for k in node["required"])):
        raise SchemaError(f"{where}/required: a list of property names")
    for key in _COUNTS:
        if key in node and not _count(node[key]):
            raise SchemaError(f"{where}/{key}: a whole number, 0 or more")
    for key in _BOUNDS:
        if key in node and not _number(node[key]):
            raise SchemaError(f"{where}/{key}: a number")
    if "multipleOf" in node and not (_number(node["multipleOf"]) and node["multipleOf"] > 0):
        raise SchemaError(f"{where}/multipleOf: a number above 0")
    if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
        raise SchemaError(f"{where}/uniqueItems: true or false")
    if "pattern" in node:
        if not isinstance(node["pattern"], str):
            raise SchemaError(f"{where}/pattern: a regular expression")
        try:
            re.compile(node["pattern"])
        except re.error as exc:
            raise SchemaError(f"{where}/pattern: {exc}") from exc
    if "$ref" in node:
        if not isinstance(node["$ref"], str) or _resolve(root, node["$ref"]) is _MISSING:
            raise SchemaError(f"{where}/$ref: {node['$ref']!r} does not name a part of this "
                              "schema (only refs within it, such as #/$defs/name)")
    if "properties" in node:
        if not isinstance(node["properties"], dict):
            raise SchemaError(f"{where}/properties: an object")
        for name, sub in node["properties"].items():
            _check(sub, f"{where}/properties/{name}", root, depth + 1)
    for key in ("additionalProperties", "items", "not"):
        if key in node:
            if key == "items" and isinstance(node[key], list):
                raise SchemaError(f"{where}/items: a list of schemas (tuple form) is not "
                                  "supported; give one schema for every item")
            _check(node[key], f"{where}/{key}", root, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node:
            if not isinstance(node[key], list) or not node[key]:
                raise SchemaError(f"{where}/{key}: a non-empty list of schemas")
            for index, sub in enumerate(node[key]):
                _check(sub, f"{where}/{key}/{index}", root, depth + 1)
    for key in DEFINITIONS:
        if key in node:
            if not isinstance(node[key], dict):
                raise SchemaError(f"{where}/{key}: an object of schemas")
            for name, sub in node[key].items():
                _check(sub, f"{where}/{key}/{name}", root, depth + 1)


_MISSING = object()


def _resolve(root: Any, ref: str) -> Any:
    """The part of ``root`` a local ``$ref`` (``#`` or ``#/a/b``) points to."""
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        return _MISSING
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return _MISSING
    return node


def embedded(schema: Any, at: str) -> tuple[Any, dict]:
    """``schema`` placed at JSON pointer ``at`` of another document -- a tool's
    parameters -- with its refs rewritten to match: definitions are returned
    to be put at that document's top, where their refs still point, and every
    other ref is moved under ``at``."""
    definitions: dict = {}
    hoisted = tuple(f"#/{d}/" for d in DEFINITIONS)

    def move(node: Any, top: bool) -> Any:
        if not isinstance(node, dict):
            return node     # true or false
        out = {}
        for key, value in node.items():
            if key in DEFINITIONS and top:
                definitions[key] = {name: move(sub, False) for name, sub in value.items()}
            elif key in DEFINITIONS or key == "properties":
                out[key] = {name: move(sub, False) for name, sub in value.items()}
            elif key in ("additionalProperties", "items", "not"):
                out[key] = move(value, False)
            elif key in ("anyOf", "oneOf", "allOf"):
                out[key] = [move(sub, False) for sub in value]
            elif key == "$ref":
                out[key] = value if value.startswith(hoisted) else at + value[1:]
            else:
                out[key] = value    # a value or an annotation, not a schema
        return out

    return move(schema, True), definitions


# --- a value ------------------------------------------------------------------------------


def problems(value: Any, schema: Any) -> list[str]:
    """What is wrong with ``value`` under ``schema`` (already checked), the
    first few, each with the place it is: ``$``, ``$.name``, ``$[0]``."""
    found: list[str] = []
    _validate(value, schema, "$", schema, found, 0)
    return found[:MAX_PROBLEMS]


def _validate(value: Any, node: Any, where: str, root: Any, found: list[str],
              depth: int) -> None:
    if len(found) >= MAX_PROBLEMS:
        return
    if depth > MAX_DEPTH:
        found.append(f"{where}: the schema refers to itself more than {MAX_DEPTH} times")
        return
    if node is True:
        return
    if node is False:
        found.append(f"{where}: no value is allowed here")
        return
    if "$ref" in node:
        _validate(value, _resolve(root, node["$ref"]), where, root, found, depth + 1)
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_is(value, t) for t in types):
            found.append(f"{where}: expected {' or '.join(types)}, got {_kind(value)}")
            return
    if "enum" in node and not any(_same(value, option) for option in node["enum"]):
        found.append(f"{where}: must be one of {_brief(node['enum'])}")
    if "const" in node and not _same(value, node["const"]):
        found.append(f"{where}: must be {_brief(node['const'])}")
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0):
            found.append(f"{where}: shorter than {node['minLength']} characters")
        if "maxLength" in node and len(value) > node["maxLength"]:
            found.append(f"{where}: longer than {node['maxLength']} characters")
        if "pattern" in node and re.search(node["pattern"], value) is None:
            found.append(f"{where}: does not match {node['pattern']!r}")
    if _number(value):
        _bounds(value, node, where, found)
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            found.append(f"{where}: fewer than {node['minItems']} items")
        if "maxItems" in node and len(value) > node["maxItems"]:
            found.append(f"{where}: more than {node['maxItems']} items")
        if node.get("uniqueItems") and any(_same(a, b) for i, a in enumerate(value)
                                           for b in value[i + 1:]):
            found.append(f"{where}: items are not unique")
        if "items" in node:
            for index, item in enumerate(value):
                _validate(item, node["items"], f"{where}[{index}]", root, found, depth + 1)
    if isinstance(value, dict):
        for name in node.get("required", ()):
            if name not in value:
                found.append(f"{where}: {name!r} is required")
        if len(value) < node.get("minProperties", 0):
            found.append(f"{where}: fewer than {node['minProperties']} properties")
        if "maxProperties" in node and len(value) > node["maxProperties"]:
            found.append(f"{where}: more than {node['maxProperties']} properties")
        properties = node.get("properties", {})
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], _at(where, name), root, found, depth + 1)
            elif node.get("additionalProperties") is False:
                found.append(f"{where}: {name!r} is not allowed")
            elif isinstance(node.get("additionalProperties"), dict):
                _validate(item, node["additionalProperties"], _at(where, name), root, found,
                          depth + 1)
    for sub in node.get("allOf", ()):
        _validate(value, sub, where, root, found, depth + 1)
    if "anyOf" in node and not any(_passes(value, sub, root, depth) for sub in node["anyOf"]):
        found.append(f"{where}: matches none of the {len(node['anyOf'])} choices in anyOf")
    if "oneOf" in node:
        passed = sum(_passes(value, sub, root, depth) for sub in node["oneOf"])
        if passed != 1:
            found.append(f"{where}: matches {passed} of the choices in oneOf, not exactly one")
    if "not" in node and _passes(value, node["not"], root, depth):
        found.append(f"{where}: matches the schema under not")


def _passes(value: Any, node: Any, root: Any, depth: int) -> bool:
    found: list[str] = []
    _validate(value, node, "$", root, found, depth + 1)
    return not found


def _bounds(value, node: dict, where: str, found: list[str]) -> None:
    if "minimum" in node and value < node["minimum"]:
        found.append(f"{where}: below the minimum {node['minimum']}")
    if "maximum" in node and value > node["maximum"]:
        found.append(f"{where}: above the maximum {node['maximum']}")
    if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"]:
        found.append(f"{where}: not above {node['exclusiveMinimum']}")
    if "exclusiveMaximum" in node and value >= node["exclusiveMaximum"]:
        found.append(f"{where}: not below {node['exclusiveMaximum']}")
    if "multipleOf" in node:
        quotient = value / node["multipleOf"]
        if abs(quotient - round(quotient)) > 1e-9 * max(1.0, abs(quotient)):
            found.append(f"{where}: not a multiple of {node['multipleOf']}")


def _is(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    if kind == "integer":
        return _number(value) and float(value).is_integer()
    return _number(value)   # number


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _same(a: Any, b: Any) -> bool:
    """JSON equality: 1 and 1.0 are the same number, true and 1 are not (a
    boolean is no number, and the last line compares types)."""
    if _number(a) and _number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _kind(value: Any) -> str:
    for kind in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is(value, kind):
            return kind
    return type(value).__name__


def _at(where: str, name: str) -> str:
    return f"{where}.{name}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) \
        else f"{where}[{json.dumps(name)}]"


def _brief(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 200 else text[:197] + "..."
