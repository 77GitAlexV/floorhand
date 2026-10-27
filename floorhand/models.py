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
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone

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
    """No paid call: not live, no key, or the budget would be crossed."""


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
    if not live():
        raise BudgetStop("Meta API is off (FLOORHAND_META_LIVE / MODEL_API_KEY not set).")
    if spent_micro(conn) + worst_case_micro > budget_micro():
        raise BudgetStop("Model budget reached; using the offline path.")


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
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def respond(conn: sqlite3.Connection, purpose: str, input_: list | str, *,
            model: str = SPARK, max_output_tokens: int = 2000, **extra) -> dict:
    """POST /v1/responses under the budget. Returns the raw response JSON."""
    # Worst case: 1 token per 2 chars plus 1,000 for framing, and all of
    # max_output_tokens used. Real usage is usually far below this.
    est_in = len(json.dumps(input_, ensure_ascii=False)) // 2 + 1000
    worst = token_cost_micro(model, est_in, max_output_tokens)
    check(conn, worst)
    body = {"model": model, "input": input_, "max_output_tokens": max_output_tokens, **extra}
    try:
        data = _post("/responses", body)
    except (urllib.error.URLError, TimeoutError) as exc:
        # The request may still have been billed: charge the worst case.
        _log(conn, model, purpose, cost_micro=worst, estimated=True, ok=False)
        raise BudgetStop(f"Meta API call failed: {exc}") from exc
    usage = data.get("usage") or {}
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    if inp is None or out is None:
        _log(conn, model, purpose, cost_micro=worst, estimated=True, ok=True)
    else:
        _log(conn, model, purpose, input_tokens=inp, cached_tokens=cached, output_tokens=out,
             cost_micro=token_cost_micro(model, inp, out, cached), estimated=False, ok=True)
    return data


VOICE = "muse-voice-transcribe-1.0"
VOICE_MAX_SECONDS = 60


def transcribe(conn: sqlite3.Connection, purpose: str, audio: bytes, mime: str, *,
               language: str | None = None) -> str:
    """Speech to text with Muse Voice Transcribe under the budget.

    Billed per audio second. We do not decode audio here, so the worst case is the
    longest clip we accept (VOICE_MAX_SECONDS); a worker's phrase is 2-5 seconds.
    Request shape follows the dev.meta.ai docs as read on 27.10.2026 (an
    /audio/transcriptions call with base64 audio); check it before the first live call.
    """
    import base64
    if len(audio) > 2_000_000:
        raise BudgetStop("Recording is too long; keep it under a minute.")
    worst = unit_cost_micro(VOICE, VOICE_MAX_SECONDS)
    check(conn, worst)
    body = {"model": VOICE, "audio": base64.b64encode(audio).decode(), "mime_type": mime}
    if language:
        body["language"] = language
    try:
        data = _post("/audio/transcriptions", body, timeout=60)
    except (urllib.error.URLError, TimeoutError) as exc:
        _log(conn, VOICE, purpose, units=VOICE_MAX_SECONDS, cost_micro=worst,
             estimated=True, ok=False)
        raise BudgetStop(f"Meta API call failed: {exc}") from exc
    seconds = data.get("duration") or (data.get("usage") or {}).get("seconds")
    if seconds is None:
        _log(conn, VOICE, purpose, units=VOICE_MAX_SECONDS, cost_micro=worst,
             estimated=True, ok=True)
    else:
        seconds = min(float(seconds), VOICE_MAX_SECONDS)
        _log(conn, VOICE, purpose, units=seconds, cost_micro=unit_cost_micro(VOICE, seconds),
             estimated=False, ok=True)
    return str(data.get("text") or "")


def budget_status(conn: sqlite3.Connection) -> dict:
    spent = spent_micro(conn)
    return {"live": live(), "spent_usd": spent / 1e6, "budget_usd": budget_micro() / 1e6,
            "calls": conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0]}
