"""Bounded Ask loop. Register trusted read-only callables, never domain mutators.

Tool closures must use the supplied connection. SQLite query_only blocks their
writes; confirmation and proposal application belong outside this loop.
"""
import json
from dataclasses import dataclass
from typing import Callable

from .. import models
from . import _schema, _wire
from .text import load_json


@dataclass(frozen=True)
class Tool:
    function: Callable
    parameters: dict
    description: str = ""


def run(conn, purpose, system, user, tools: dict[str, Tool], *, max_steps=5,
        max_tool_calls=20, max_output_tokens=2000, model=models.SPARK) -> dict:
    if type(max_steps) is not int or max_steps < 1 or type(max_tool_calls) is not int or max_tool_calls < 1:
        raise ValueError("Tool limits must be positive integers")
    for tool in tools.values():
        _schema.check_schema(tool.parameters)
    definitions = [{"type": "function", "name": name, "description": tool.description,
                    "parameters": tool.parameters, "strict": False} for name, tool in tools.items()]
    policy = ("Use only read-only tools. Never change shop data or send messages. "
              "Label numeric statements FACT / CALC / ESTIMATE; answer in the user's language "
              "(English or Russian). / Инструменты только для чтения. Не меняйте данные и не "
              "отправляйте сообщения. Числа помечайте ФАКТ / РАСЧЁТ / ОЦЕНКА.")
    history = _wire.messages(system + "\n" + policy, user)
    trace, seen = [], set()
    for _ in range(max_steps):
        data = models.respond(conn, purpose, history, model=model, tools=definitions,
                              max_output_tokens=max_output_tokens, store=False,
                              include=["reasoning.encrypted_content"])
        output = _wire.output_items(data)
        calls = [item for item in output if item.get("type") == "function_call"]
        if not calls:
            answer = _wire.output_text(data)
            if not answer.strip():
                raise _wire.stop("No final answer returned.", "Итоговый ответ не получен.")
            return {"answer": answer, "trace": trace}
        if len(trace) + len(calls) > max_tool_calls:
            raise _wire.stop("Too many tool calls; narrow the question.", "Слишком много вызовов; уточните вопрос.")
        history += output  # Preserve encrypted reasoning and commentary phase verbatim.
        for call in calls:
            call_id, name = call.get("call_id"), call.get("name")
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 64 or call_id in seen:
                raise _wire.stop("Invalid or repeated tool call id.", "Некорректный или повторный идентификатор вызова.")
            seen.add(call_id)
            entry = {"call_id": call_id, "name": name, "arguments": None, "ok": False}
            try:
                if name not in tools:
                    raise ValueError("Unknown tool")
                arguments = load_json(call["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                _schema.validate(arguments, tools[name].parameters)
                entry["arguments"] = arguments
                query_only = conn.execute("PRAGMA query_only").fetchone()[0]
                conn.execute("PRAGMA query_only=ON")
                try:
                    result = tools[name].function(**arguments)
                    result_json = json.dumps(result, ensure_ascii=False, allow_nan=False)
                finally:
                    conn.execute(f"PRAGMA query_only={int(query_only)}")
                entry.update(ok=True, result=result)
            except models.BudgetStop:
                raise
            except Exception:
                # Do not leak SQL, credentials or arbitrary exception text to the model.
                result_json = json.dumps({"error": "Tool unavailable or arguments invalid. / "
                                          "Инструмент недоступен или аргументы неверны."})
                entry["error"] = json.loads(result_json)["error"]
            trace.append(entry)
            history.append({"type": "function_call_output", "call_id": call_id, "output": result_json})
    raise _wire.stop("Tool step limit reached; narrow the question.", "Достигнут лимит шагов; уточните вопрос.")
