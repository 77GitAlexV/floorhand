"""Meta Model API with a hard budget.

Calls go out only when FLOORHAND_META_LIVE=1 and MODEL_API_KEY are set.
Otherwise every caller gets BudgetStop and uses its stub path, so the demo
works with zero spend.

The platform itself only e-mails about spending, it does not stop it. So the
stop lives here: every call is priced and logged in model_calls, and a call is
refused if what is already spent plus the worst case of this call would cross
FLOORHAND_BUDGET_USD (default 120 of the 150 hackathon credits; the rest is a
margin for billing that shows up late).
"""
import json
import math
import os
import random
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

API = "https://api.meta.ai/v1"

# USD per 1M tokens: cached input, input, output (dev.meta.ai pricing, 27.10.2026)
TOKEN_PRICES = {
    "muse-spark-1.3": (0.15, 1.25, 4.25),
    "muse-spark-1.3-contributor": (0.002, 0.10, 0.20),
    "muse-wonder-1.0": (0.20, 4.00, 20.00),
}
UNIT_PRICES = {  # USD per unit
    "muse-image-1.0": 0.01,                 # per image
    "sam-3.1": 2.50 / 1000,                 # per image
    "muse-voice-transcribe-1.0": 0.18 / 3600,  # per audio second
}
# Contributor mode: a fraction of the price, inputs may be used by Meta to
# improve models. Fine here: every byte the demo sends is made up.
SPARK = os.environ.get("FLOORHAND_SPARK_MODEL", "muse-spark-1.3-contributor")


class BudgetStop(Exception):
    """Use offline: disabled, out of budget, or no usable model result."""


def live() -> bool:
    return os.environ.get("FLOORHAND_META_LIVE") == "1" and bool(os.environ.get("MODEL_API_KEY"))


def budget_micro() -> int:
    return int(float(os.environ.get("FLOORHAND_BUDGET_USD", "120")) * 1_000_000)


def spent_micro(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(SUM(cost_micro), 0) FROM model_calls").fetchone()[0]


def token_cost_micro(model: str, input_tokens: int, output_tokens: int,
                     cached_tokens: int = 0) -> int:
    cached, inp, out = TOKEN_PRICES[model]
    usd = ((input_tokens - cached_tokens) * inp + cached_tokens * cached
           + output_tokens * out) / 1_000_000
    return round(usd * 1_000_000)


def unit_cost_micro(model: str, units: float) -> int:
    return round(UNIT_PRICES[model] * units * 1_000_000)


def check(conn: sqlite3.Connection, worst_case_micro: int) -> None:
    if worst_case_micro < 0:
        raise ValueError("Negative model cost")
    if not live():
        raise BudgetStop("Meta API is off; use the offline path. / Meta API выключен; используйте офлайн-режим.")
    if spent_micro(conn) + worst_case_micro > budget_micro():
        raise BudgetStop("Model budget reached; use the offline path. / Бюджет исчерпан; используйте офлайн-режим.")


def _log(conn, model, purpose, *, input_tokens=0, cached_tokens=0, output_tokens=0,
         units=0.0, cost_micro, estimated, ok) -> None:
    conn.execute(
        "INSERT INTO model_calls (at, model, purpose, input_tokens, cached_tokens, "
        "output_tokens, units, cost_micro, estimated, ok) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), model, purpose,
         input_tokens, cached_tokens, output_tokens, units, cost_micro, int(estimated),
         int(ok)))
    conn.commit()


def _post(path: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"],
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream" if body.get("stream") else "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        if body.get("stream"):
            return _read_stream(res)
        return json.load(res)


def _read_stream(res) -> dict:
    """Never accept a truncated SAM stream as a successful empty result."""
    parts, lines = [], []
    for raw in res:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line:
            if line.startswith("data:"):
                lines.append(line[5:].lstrip(" "))
            continue
        if not lines:
            continue
        event = json.loads("\n".join(lines))
        lines = []
        kind = event.get("type")
        if kind == "response.output_text.delta":
            parts.append(event["delta"])
        elif kind == "response.completed":
            data = event["response"]
            data["output_text"] = "".join(parts)
            return data
        elif kind in ("error", "response.failed", "response.incomplete"):
            code = event.get("code")
            # The documented transient terminal errors can retry the whole call.
            status = 503 if code in ("server_shutting_down", "service_overloaded",
                                     "backend_unavailable") else 400
            raise urllib.error.HTTPError(API, status, "Stream failed", {}, None)
    raise ValueError("Incomplete response stream")


def _post_multipart(path: str, fields: dict, files: dict, timeout: int = 120) -> dict:
    """Encode binary uploads without an SDK or extra dependencies."""
    boundary = "floorhand-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        is_json = isinstance(value, dict)
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                      + ("Content-Type: application/json\r\n" if is_json else "")
                      + "\r\n").encode())
        parts.append((json.dumps(value) if is_json else str(value)).encode("utf-8") + b"\r\n")
    for name, (filename, content, mime) in files.items():
        if any(c in name + filename + mime for c in '\r\n"'):
            raise ValueError("Invalid multipart header")
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                      f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode())
        parts.append(content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(API + path, data=b"".join(parts), method="POST", headers={
        "Authorization": "Bearer " + os.environ["MODEL_API_KEY"],
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def _reserve(conn, model, purpose, worst, units=0):
    """Reserve before I/O so overlapping requests cannot spend the same balance."""
    check(conn, worst)
    cur = conn.execute(
        "INSERT INTO model_calls (at, model, purpose, cost_micro, units, estimated, ok) "
        "SELECT ?,?,?,?,?,1,0 WHERE "
        "(SELECT COALESCE(SUM(cost_micro),0) FROM model_calls) + ? <= ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), model, purpose,
         worst, units, worst, budget_micro()))
    conn.commit()
    if not cur.rowcount:
        raise BudgetStop("Model budget reached; use offline. / Бюджет исчерпан; используйте офлайн-режим.")
    return cur.lastrowid


def _settle(conn, call_id, *, cost_micro, estimated, ok, input_tokens=0,
            output_tokens=0, cached_tokens=0, units=0):
    conn.execute("UPDATE model_calls SET cost_micro=?, estimated=?, ok=?, input_tokens=?, "
                 "output_tokens=?, cached_tokens=?, units=? WHERE id=?",
                 (cost_micro, int(estimated), int(ok), input_tokens, output_tokens,
                  cached_tokens, units, call_id))
    conn.commit()


def _retry_delay(exc, attempt):
    value = exc.headers.get("Retry-After") if exc.headers else None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 0
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    return 2 ** attempt + random.random()


def _request(conn, purpose, model, worst, send, account, units=0):
    for attempt in range(3):
        call_id = _reserve(conn, model, purpose, worst, units)
        try:
            data = send()
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
            # Leave the reservation charged: a failed/ambiguous attempt may be billed.
            if (isinstance(exc, urllib.error.HTTPError) and attempt < 2
                    and (exc.code == 429 or 500 <= exc.code < 600) and exc.code != 504):
                check(conn, worst)
                time.sleep(_retry_delay(exc, attempt))
                continue
            raise BudgetStop("Meta request failed; use offline. / Запрос Meta не выполнен; "
                             "используйте офлайн-режим.") from exc
        try:
            if not isinstance(data, dict) or data.get("error"):
                raise ValueError("Invalid response")
            values = account(data)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            values = {"cost_micro": worst, "estimated": True, "units": units}
        ok = isinstance(data, dict) and not data.get("error") and data.get("status") not in (
            "failed", "incomplete", "cancelled", "queued", "in_progress")
        _settle(conn, call_id, ok=ok, **values)
        if not ok:
            raise BudgetStop("Meta returned no complete result; use offline. / Нет полного ответа Meta; "
                             "используйте офлайн-режим.")
        return data


def _token_usage(data, model, worst):
    usage = data.get("usage") or {}
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
    cached = details.get("cached_tokens", 0)
    if any(type(n) is not int or n < 0 for n in (inp, out, cached)) or cached > inp:
        raise ValueError("Invalid usage")
    return dict(input_tokens=inp, output_tokens=out, cached_tokens=cached,
                cost_micro=token_cost_micro(model, inp, out, cached), estimated=False)


def respond(conn: sqlite3.Connection, purpose: str, input_: list | str, *,
            model: str = SPARK, max_output_tokens: int = 2000,
            est_input_tokens: int | None = None, **extra) -> dict:
    """POST /v1/responses under the budget. Returns the raw response JSON."""
    # Keep the legacy hint argument; reservations use the full request below.
    body = {"model": model, "input": input_, "max_output_tokens": max_output_tokens, **extra}
    if model not in TOKEN_PRICES:
        raise BudgetStop("Unknown model price; use offline. / Цена модели неизвестна; используйте офлайн-режим.")
    if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= 131072:
        raise ValueError("max_output_tokens must be between 16 and 131072")
    if extra.get("tools") and any(t.get("type") != "function" for t in extra["tools"]):
        raise ValueError("Only local function tools have budgeted pricing")
    if extra.get("previous_response_id") or extra.get("conversation"):
        raise ValueError("Replay input explicitly so the budget can account for it")
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    # Include schemas, tools, instructions and replay, not just the user prompt.
    # Images/files and encrypted reasoning are opaque: reserve the entire input window.
    opaque = any(s in encoded for s in (b'"input_image"', b'"input_file"',
                                        b'"input_video"', b'"encrypted_content"'))
    est_in = 1048576 - max_output_tokens if opaque else len(encoded) + 1000
    if est_in + max_output_tokens > 1048576:
        raise BudgetStop("Input is too large; split it. / Запрос слишком большой; разделите его.")
    worst = token_cost_micro(model, est_in, max_output_tokens)
    return _request(conn, purpose, model, worst, lambda: _post("/responses", body),
                    lambda data: _token_usage(data, model, worst))


def request_units(conn, purpose, model, units, path, *, body=None, fields=None, files=None,
                  usage_units=None):
    """Budget per-image/per-second endpoints through the same attempt ledger."""
    if model not in UNIT_PRICES or not math.isfinite(units) or units < 0:
        raise ValueError("Invalid unit pricing")
    worst = unit_cost_micro(model, units)

    def account(data):
        actual = usage_units(data) if usage_units else None
        estimated = actual is None
        actual = units if estimated else actual
        if not math.isfinite(actual) or actual < 0:
            raise ValueError("Invalid units")
        return dict(units=actual, cost_micro=unit_cost_micro(model, actual), estimated=estimated)

    send = (lambda: _post(path, body)) if body is not None else (
        lambda: _post_multipart(path, fields or {}, files or {}))
    return _request(conn, purpose, model, worst, send, account, units)


def transcribe(conn: sqlite3.Connection, purpose: str, audio: bytes, mime: str, *,
               language: str | None = None) -> str:
    """Keep marking on the shared, reserved-budget voice client."""
    from .meta import voice

    if len(audio) > 2_000_000:
        raise BudgetStop("Recording is too long; keep it under a minute. / "
                         "Запись слишком длинная; запишите фразу короче минуты.")
    return voice.transcribe(conn, audio, mime, language, purpose=purpose)["transcript"]


def vision(conn: sqlite3.Connection, purpose: str, prompt: str, image_b64: str, mime: str, *,
           max_output_tokens: int = 3000) -> dict:
    """Keep photo intake's raw response and conservative image reservation."""
    input_ = [{"role": "user", "content": [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": f"data:{mime};base64,{image_b64}"}]}]
    return respond(conn, purpose, input_, max_output_tokens=max_output_tokens)


def budget_status(conn: sqlite3.Connection) -> dict:
    spent = spent_micro(conn)
    return {"live": live(), "spent_usd": spent / 1e6, "budget_usd": budget_micro() / 1e6,
            "calls": conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0]}
