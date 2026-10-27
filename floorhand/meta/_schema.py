"""Small fail-closed JSON Schema subset for tool arguments and model output.

Unsupported keywords are refused before a paid call. Keep this dependency-free;
the API still receives the original schema for constrained generation.
"""
import math
import re

_ANNOTATIONS = {"title", "description", "default", "$schema", "$id", "examples", "$comment"}
_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const",
             "anyOf", "oneOf", "allOf", "$ref", "$defs", "definitions", "minimum", "maximum",
             "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern",
             "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties"}


def check_schema(schema):
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict) or set(schema) - _KEYWORDS - _ANNOTATIONS:
        raise ValueError("Unsupported JSON Schema keyword")
    for key in ("properties", "$defs", "definitions"):
        for child in schema.get(key, {}).values():
            check_schema(child)
    for key in ("additionalProperties", "items"):
        if key in schema:
            check_schema(schema[key])
    for key in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(key, []):
            check_schema(child)
    if "$ref" in schema and not schema["$ref"].startswith("#/"):
        raise ValueError("Only local schema references are supported")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if set(types) - {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ValueError("Unsupported JSON type")
    if "pattern" in schema:
        re.compile(schema["pattern"])


def validate(value, schema, root=None, depth=0):
    root = schema if root is None else root
    if depth > 50 or schema is False:
        raise ValueError("JSON does not match schema")
    if schema is True:
        return
    if "$ref" in schema:
        target = root
        for key in schema["$ref"][2:].split("/"):
            target = target[key.replace("~1", "/").replace("~0", "~")]
        validate(value, target, root, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            matches = 0
            for child in schema[key]:
                try:
                    validate(value, child, root, depth + 1)
                    matches += 1
                except ValueError:
                    pass
            if ((key == "anyOf" and not matches) or (key == "oneOf" and matches != 1)
                    or (key == "allOf" and matches != len(schema[key]))):
                raise ValueError("JSON does not match alternatives")
    kinds = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool,
             "null": value is None}
    types = schema.get("type", list(kinds))
    types = [types] if isinstance(types, str) else types
    if not any(kinds[t] for t in types):
        raise ValueError("Wrong JSON type")
    if "enum" in schema and not any(_equal(value, v) for v in schema["enum"]):
        raise ValueError("Value is not in enum")
    if "const" in schema and not _equal(value, schema["const"]):
        raise ValueError("Wrong constant")
    if isinstance(value, dict):
        if set(schema.get("required", [])) - value.keys():
            raise ValueError("Missing required properties")
        properties = schema.get("properties", {})
        for key, child in value.items():
            validate(child, properties.get(key, schema.get("additionalProperties", True)), root, depth + 1)
        _length(len(value), schema, "Properties")
    elif isinstance(value, list):
        for child in value:
            validate(child, schema.get("items", True), root, depth + 1)
        _length(len(value), schema, "Items")
        if schema.get("uniqueItems") and any(_equal(v, w) for i, v in enumerate(value) for w in value[:i]):
            raise ValueError("Duplicate array items")
    elif isinstance(value, str):
        _length(len(value), schema, "Length")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValueError("String pattern mismatch")
    elif type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite number")
        for key, invalid in (("minimum", lambda n: value < n), ("maximum", lambda n: value > n),
                             ("exclusiveMinimum", lambda n: value <= n),
                             ("exclusiveMaximum", lambda n: value >= n)):
            if key in schema and invalid(schema[key]):
                raise ValueError("Number outside bounds")


def _length(length, schema, suffix):
    if length < schema.get("min" + suffix, 0) or length > schema.get("max" + suffix, math.inf):
        raise ValueError("Length outside bounds")


def _equal(left, right):
    # JSON boolean true is not the JSON number 1, even though Python says so.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right
