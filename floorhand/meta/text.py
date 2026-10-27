"""Structured extraction that never applies the returned proposal."""
import json
import re

from .. import models
from . import _schema, _wire


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _invalid_number(value):
    raise ValueError("Invalid JSON number: " + value)


def load_json(value):
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_invalid_number)


def _extract(text, schema):
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_invalid_number)
    # raw_decode handles braces inside strings and fenced JSON without a greedy regex.
    start = text.find("{")
    if start >= 0:
        try:
            value, _ = decoder.raw_decode(text[start:])
            _schema.validate(value, schema)
            return value
        except (ValueError, KeyError, TypeError):
            pass
    # Never substitute a nested object from a rejected outer answer.
    raise ValueError("No matching JSON object")


def _json(conn, purpose, input_, schema, *, model, max_output_tokens):
    try:
        _schema.check_schema(schema)
    except (ValueError, TypeError, AttributeError, re.error) as exc:
        raise _wire.stop("Unsupported JSON schema.", "Неподдерживаемая схема JSON.") from exc
    history = list(input_)
    for attempt in range(2):
        data = models.respond(conn, purpose if not attempt else purpose + ":repair", history,
                              model=model, max_output_tokens=max_output_tokens,
                              text=_wire.json_format(schema), store=False)
        answer = _wire.output_text(data)
        try:
            return _extract(answer, schema)
        except ValueError:
            history += [{"role": "assistant", "content": [{"type": "output_text", "text": answer}]},
                        {"role": "user", "content": "Return only one JSON object matching the supplied schema. "
                         "Repair the format without inventing facts. / Верните объект JSON по схеме. "
                         "Исправьте формат, не придумывайте факты."}]
    raise _wire.stop("The model returned invalid JSON.", "Модель вернула некорректный JSON.")


def respond_json(conn, purpose, system, user, schema, *, model=models.SPARK, max_output_tokens=2000) -> dict:
    return _json(conn, purpose, _wire.messages(system, user), schema,
                 model=model, max_output_tokens=max_output_tokens)
