"""Wire contracts and money guard tested without credentials or network."""
import io
import json
import sqlite3
import urllib.error
import wave
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from floorhand import db, models
from floorhand.meta import BudgetStop, _schema, _wire, sam, text, tools, vision, voice

SCHEMA = {"type": "object", "properties": {"qty": {"type": "integer", "minimum": 1}},
          "required": ["qty"], "additionalProperties": False}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "invented-test-key")
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "10")
    monkeypatch.setattr(models, "_post", lambda *a, **k: pytest.fail("Unexpected network"))
    monkeypatch.setattr(models, "_post_multipart", lambda *a, **k: pytest.fail("Unexpected upload"))
    monkeypatch.setattr(models.time, "sleep", lambda _: None)
    c = db.connect(tmp_path / "meta.db")
    c.executescript(db.SCHEMA)
    yield c
    c.close()


def answer(value='{"qty": 2}', *, usage=True, **extra):
    data = {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": value}]}], **extra}
    if usage:
        data["usage"] = {"input_tokens": 100, "output_tokens": 20,
                         "input_tokens_details": {"cached_tokens": 40}}
    return data


def fake(monkeypatch, responses):
    requests = []
    responses = iter(responses)

    def post(path, body):
        requests.append((path, json.loads(json.dumps(body))))
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(models, "_post", post)
    return requests


def ledger(conn):
    return conn.execute("SELECT * FROM model_calls ORDER BY id").fetchall()


def wav(seconds=2.75, *, rate=24000, channels=1, width=2):
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0" * int(seconds * rate) * channels * width)
    return data.getvalue()


def test_native_json_request_and_cached_cost(conn, monkeypatch):
    requests = fake(monkeypatch, [answer()])
    assert text.respond_json(conn, "order-photo", "Extract fields", "invented form", SCHEMA) == {"qty": 2}
    path, body = requests[0]
    assert path == "/responses" and body["model"] == models.SPARK
    assert body["input"] == _wire.messages("Extract fields", "invented form")
    assert body["text"] == {"format": {"type": "json_schema", "name": "floorhand_result",
                                       "schema": SCHEMA, "strict": False}}
    assert body["store"] is False
    row, = ledger(conn)
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (100, 40, 20)
    assert row["cost_micro"] == models.token_cost_micro(models.SPARK, 100, 20, 40)
    assert row["ok"] == 1 and row["estimated"] == 0


@pytest.mark.parametrize("content", ['```json\n{"qty":2}\n```', 'Here: {"qty":2} done'])
def test_json_extraction(conn, monkeypatch, content):
    requests = fake(monkeypatch, [answer(content)])
    assert text.respond_json(conn, "json", "", "", SCHEMA) == {"qty": 2}
    assert len(requests) == 1


def test_braces_inside_json_strings(conn, monkeypatch):
    fake(monkeypatch, [answer('Text: {"label":"{leg} and }"} suffix')])
    schema = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}
    assert text.respond_json(conn, "json", "", "", schema)["label"] == "{leg} and }"


@pytest.mark.parametrize("bad", ['broken {', '{"qty":"2"}', '{"qty":0}', '{"qty":true}', '[]'])
def test_json_one_paid_repair(conn, monkeypatch, bad):
    requests = fake(monkeypatch, [answer(bad), answer()])
    assert text.respond_json(conn, "json", "", "", SCHEMA) == {"qty": 2}
    assert len(requests) == 2 and len(ledger(conn)) == 2
    assert ledger(conn)[1]["purpose"] == "json:repair"
    assert requests[1][1]["input"][-2]["content"][0]["text"] == bad


def test_invalid_repair_stops(conn, monkeypatch):
    requests = fake(monkeypatch, [answer("bad"), answer("also bad")])
    with pytest.raises(BudgetStop, match="JSON"):
        text.respond_json(conn, "json", "", "", SCHEMA)
    assert len(requests) == 2


@pytest.mark.parametrize("bad", ['{"outer":{"qty":2}}', '{"qty":1,"qty":2}'])
def test_json_never_uses_nested_object_or_duplicate_keys(conn, monkeypatch, bad):
    requests = fake(monkeypatch, [answer(bad), answer()])
    assert text.respond_json(conn, "json", "", "", SCHEMA) == {"qty": 2}
    assert len(requests) == 2


def test_json_repair_obeys_remaining_budget(conn, monkeypatch):
    requests = []

    def post(*args):
        requests.append(1)
        monkeypatch.setenv("FLOORHAND_BUDGET_USD", "0")
        return answer("broken")
    monkeypatch.setattr(models, "_post", post)
    with pytest.raises(BudgetStop):
        text.respond_json(conn, "json", "", "", SCHEMA)
    assert requests == [1]


@pytest.mark.parametrize("client", [
    lambda c: text.respond_json(c, "json", "", "", SCHEMA),
    lambda c: vision.understand(c, "vision", b"invented", "image/png", "Read form"),
    lambda c: voice.transcribe(c, wav(), "audio/wav", "en"),
    lambda c: sam.segment(c, b"invented", "image/png", "chair leg"),
    lambda c: tools.run(c, "ask", "", "", {}),
])
@pytest.mark.parametrize("guard", ["off", "no-key", "budget"])
def test_all_clients_stop_before_transport(conn, monkeypatch, client, guard):
    if guard == "off":
        monkeypatch.delenv("FLOORHAND_META_LIVE")
    elif guard == "no-key":
        monkeypatch.delenv("MODEL_API_KEY")
    else:
        monkeypatch.setenv("FLOORHAND_BUDGET_USD", "0")
    with pytest.raises(BudgetStop):
        client(conn)
    assert not ledger(conn)


def test_vision_shape_and_json(conn, monkeypatch):
    requests = fake(monkeypatch, [answer("A fictional sheet"), answer()])
    assert vision.understand(conn, "sheet", b"abc", "image/png", "Read") == "A fictional sheet"
    assert vision.understand(conn, "form", b"abc", "image/png", "Read", SCHEMA) == {"qty": 2}
    assert requests[0][1]["input"] == [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "Read"},
        {"type": "input_image", "image_url": "data:image/png;base64,YWJj"}]}]


@pytest.mark.parametrize("image,mime", [(b"", "image/png"), (b"x", "text/plain")])
def test_invalid_image_uses_offline(conn, image, mime):
    with pytest.raises(BudgetStop):
        vision.understand(conn, "image", image, mime, "Read")
    assert not ledger(conn)


def test_voice_request_and_whole_seconds(conn, monkeypatch):
    requests = []
    audio = wav()

    def post(path, fields, files):
        requests.append((path, fields, files))
        return {"transcript": "Invented chair", "audioDurationMs": 2750, "turns": []}
    monkeypatch.setattr(models, "_post_multipart", post)
    assert voice.transcribe(conn, audio, "audio/wav", "en")["transcript"] == "Invented chair"
    assert requests == [("/asr/transcribe", {"request": {"model": "muse-voice-transcribe-1.0",
        "mode": "PUSH_TO_TALK", "audioEncoding": "WAV", "languageBias": ["English"]}},
        {"audio": ("recording.wav", audio, "audio/wav")})]
    row, = ledger(conn)
    assert row["units"] == 2 and row["cost_micro"] == 100 and row["estimated"] == 0


def test_voice_missing_duration_uses_measured_audio(conn, monkeypatch):
    monkeypatch.setattr(models, "_post_multipart", lambda *a: {"transcript": ""})
    voice.transcribe(conn, wav(3.9), "audio/wav")
    row, = ledger(conn)
    assert row["units"] == 3 and row["cost_micro"] == 150 and row["estimated"] == 1


def test_unsupported_voice_language_uses_offline(conn):
    with pytest.raises(BudgetStop, match="language hint"):
        voice.transcribe(conn, wav(), "audio/wav", "ru")
    assert not ledger(conn)


def test_voice_multipart_retry_charges_audio_seconds(conn, monkeypatch):
    calls = []

    def post(*args):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("test", 503, "retry", {}, None)
        return {"transcript": "Invented", "audioDurationMs": 2750}
    monkeypatch.setattr(models, "_post_multipart", post)
    voice.transcribe(conn, wav(), "audio/wav")
    assert len(calls) == 2
    assert [(r["units"], r["cost_micro"], r["ok"]) for r in ledger(conn)] == [(2, 100, 0), (2, 100, 1)]


@pytest.mark.parametrize("audio,mime", [(b"abc", "audio/wav"), (wav(), "audio/webm"),
    (wav(channels=2), "audio/wav"), (wav(rate=44100), "audio/wav"),
    (wav(width=1), "audio/wav"), (wav()[:-2], "audio/wav"), (wav(0), "audio/wav")],
    ids=["invalid", "webm", "stereo", "rate", "sample-width", "truncated", "empty"])
def test_voice_rejects_unsupported_audio_without_spend(conn, audio, mime):
    with pytest.raises(BudgetStop, match="PCM"):
        voice.transcribe(conn, audio, mime)
    assert not ledger(conn)


SAM_TEXT = ("<0f>2<|box;x1=1;y1=2;x2=4;y2=6;w=10;h=12|>"
            "<|mask;x=0;y=0;data=5,4,~a,!~b|>,7<|box;x1=5;y1=2;x2=7;y2=8;w=10;h=12|>"
            "<|mask;x=0;y=0;data=7,3,!payload|>")


@pytest.mark.parametrize("usage,estimated", [({"images_processed": 1}, 0), ({}, 1)])
def test_sam_shape_masks_boxes_count_and_price(conn, monkeypatch, usage, estimated):
    requests = fake(monkeypatch, [{"output_text": SAM_TEXT, "usage": usage}])
    result = sam.segment(conn, b"abc", "image/png", "upholstered armrest")
    assert result["count"] == 2 and result["basis"] == "ESTIMATE"
    assert [o["id"] for o in result["objects"]] == [2, 7]
    assert result["masks"][0] == {"height": 5, "width": 4, "encoding": "lossless", "payload": "a,!~b"}
    assert result["masks"][1]["encoding"] == "one_bit"
    assert requests[0][1]["stream"] is True and requests[0][1]["model"] == "sam-3.1"
    assert "max_output_tokens" not in requests[0][1]
    row, = ledger(conn)
    assert row["units"] == 1 and row["cost_micro"] == 2500 and row["estimated"] == estimated


def test_sam_zero_matches_valid(conn, monkeypatch):
    fake(monkeypatch, [{"output_text": "", "usage": {"images_processed": 1}}])
    assert sam.segment(conn, b"abc", "image/png", "chair leg")["count"] == 0


@pytest.mark.parametrize("value", ["json {}", "<1f>" + SAM_TEXT[4:], SAM_TEXT + ",", SAM_TEXT + SAM_TEXT])
def test_sam_malformed_response_stops(conn, monkeypatch, value):
    fake(monkeypatch, [{"output_text": value}])
    with pytest.raises(BudgetStop):
        sam.segment(conn, b"abc", "image/png", "chair leg")
    assert ledger(conn)[0]["cost_micro"] == 2500


def test_sam_confidence():
    result = _wire.segmentation("<0f>8<|box;h=12;w=10;x2=4;y2=6;x1=1;y1=2;c=0.9|>"
                                "<|mask;x=0;y=0;c=0.9;data=5,4,~abc|>")
    assert result["objects"][0]["confidence"] == 0.9


def tool_call(name="lookup", arguments='{"qty":2}', call_id="call_1"):
    return {"type": "function_call", "name": name, "arguments": arguments, "call_id": call_id}


def test_tool_loop_replays_reasoning_phase_and_multiple_calls(conn, monkeypatch):
    invoked = []
    reasoning = {"type": "reasoning", "encrypted_content": "opaque", "id": "rs_1", "summary": []}
    commentary = {"type": "message", "role": "assistant", "phase": "commentary",
                  "content": [{"type": "output_text", "text": "Checking"}]}
    output = [reasoning, commentary, tool_call(), tool_call(call_id="call_2")]
    requests = fake(monkeypatch, [answer(output=output), answer("FACT: 4 units / ФАКТ: 4 изделия")])
    result = tools.run(conn, "ask", "Owner", "Count", {
        "lookup": tools.Tool(lambda qty: invoked.append(qty) or {"units": qty}, SCHEMA, "Count fictional units")})
    assert invoked == [2, 2] and len(result["trace"]) == 2
    assert result["answer"].startswith("FACT")
    assert requests[0][1]["tools"] == [{"type": "function", "name": "lookup",
        "description": "Count fictional units", "parameters": SCHEMA, "strict": False}]
    assert requests[1][1]["input"][2:6] == output
    assert requests[1][1]["input"][-1] == {"type": "function_call_output", "call_id": "call_2",
                                                          "output": '{"units": 2}'}
    assert requests[0][1]["include"] == ["reasoning.encrypted_content"]


@pytest.mark.parametrize("name,args", [("unknown", "{}"), ("lookup", "bad"), ("lookup", "[]"),
                                      ("lookup", '{"qty":true}'), ("lookup", '{"qty":2,"extra":1}')])
def test_bad_tool_never_executes(conn, monkeypatch, name, args):
    fake(monkeypatch, [answer(output=[tool_call(name, args)]), answer("Tool unavailable")])
    result = tools.run(conn, "ask", "", "", {"lookup": tools.Tool(lambda **k: pytest.fail("Executed"), SCHEMA)})
    assert result["trace"][0]["ok"] is False


def test_tool_cannot_write_shop_data_and_query_only_is_restored(conn, monkeypatch):
    fake(monkeypatch, [answer(output=[tool_call()]), answer("Please mark work manually")])
    tool = tools.Tool(lambda qty: conn.execute("INSERT INTO settings VALUES ('model-write','no')"), SCHEMA)
    result = tools.run(conn, "ask", "", "", {"lookup": tool})
    assert result["trace"][0]["ok"] is False
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 0
    assert len(ledger(conn)) == 2


def test_duplicate_tool_id_never_executes_twice(conn, monkeypatch):
    invoked = []
    fake(monkeypatch, [answer(output=[tool_call(), tool_call()])])
    with pytest.raises(BudgetStop):
        tools.run(conn, "ask", "", "", {"lookup": tools.Tool(lambda **k: invoked.append(1), SCHEMA)})
    assert invoked == [1]


def test_tool_limits(conn, monkeypatch):
    fake(monkeypatch, [answer(output=[tool_call()])])
    with pytest.raises(BudgetStop, match="step limit"):
        tools.run(conn, "ask", "", "", {"lookup": tools.Tool(lambda **k: {}, SCHEMA)}, max_steps=1)
    assert len(ledger(conn)) == 1


def test_tool_call_count_limit_is_checked_before_execution(conn, monkeypatch):
    fake(monkeypatch, [answer(output=[tool_call(), tool_call(call_id="call_2")])])
    invoked = []
    with pytest.raises(BudgetStop, match="Too many tool calls"):
        tools.run(conn, "ask", "", "", {"lookup": tools.Tool(lambda **k: invoked.append(1), SCHEMA)},
                  max_tool_calls=1)
    assert not invoked


def test_tool_errors_are_traced_without_leaking_exception(conn, monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError("invented-secret")
    requests = fake(monkeypatch, [answer(output=[tool_call()]), answer("Try again later")])
    result = tools.run(conn, "ask", "", "", {"lookup": tools.Tool(unavailable, SCHEMA)})
    assert result["trace"][0]["ok"] is False
    assert "invented-secret" not in json.dumps(result) + json.dumps(requests)


def test_tool_budget_stops_between_steps(conn, monkeypatch):
    requests = fake(monkeypatch, [answer(output=[tool_call()])])

    def lookup(qty):
        monkeypatch.setenv("FLOORHAND_BUDGET_USD", str(models.spent_micro(conn) / 1e6))
        return {"units": qty}
    with pytest.raises(BudgetStop):
        tools.run(conn, "ask", "", "", {"lookup": tools.Tool(lookup, SCHEMA)})
    assert len(requests) == 1


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_retries_and_failed_attempts_are_charged(conn, monkeypatch, code):
    delays = []
    monkeypatch.setattr(models.time, "sleep", delays.append)
    requests = fake(monkeypatch, [urllib.error.HTTPError("test", code, "retry", {"Retry-After": "3"}, None), answer()])
    models.respond(conn, "retry", "Invented")
    assert len(requests) == 2 and delays == [3]
    failed, success = ledger(conn)
    assert failed["estimated"] == 1 and failed["ok"] == 0 and failed["cost_micro"] > 0
    assert success["estimated"] == 0 and success["ok"] == 1


def test_retry_backoff_is_exponential_and_bounded(conn, monkeypatch):
    delays = []
    monkeypatch.setattr(models.time, "sleep", delays.append)
    monkeypatch.setattr(models.random, "random", lambda: 0.5)
    requests = fake(monkeypatch, [urllib.error.HTTPError("test", 500, "retry", {}, None) for _ in range(3)])
    with pytest.raises(BudgetStop):
        models.respond(conn, "retry", "Invented")
    assert len(requests) == 3 and delays == [1.5, 2.5]
    assert all(row["ok"] == 0 for row in ledger(conn))


def test_retry_after_http_date_and_invalid_header(monkeypatch):
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    error = urllib.error.HTTPError("test", 503, "retry", {"Retry-After": future}, None)
    assert 28 <= models._retry_delay(error, 0) <= 31
    monkeypatch.setattr(models.random, "random", lambda: 0.25)
    error.headers["Retry-After"] = "not-a-date"
    assert models._retry_delay(error, 1) == 2.25


@pytest.mark.parametrize("failure", [TimeoutError(), urllib.error.URLError("offline"), ValueError("bad JSON")])
def test_ambiguous_failures_keep_charge_and_use_offline(conn, monkeypatch, failure):
    requests = fake(monkeypatch, [failure])
    with pytest.raises(BudgetStop):
        models.respond(conn, "error", "Invented")
    assert len(requests) == 1 and ledger(conn)[0]["ok"] == 0 and ledger(conn)[0]["estimated"] == 1


@pytest.mark.parametrize("payload", [{"error": {"message": "failed"}}, {"status": "incomplete"}, []])
def test_unsuccessful_response_uses_offline(conn, monkeypatch, payload):
    fake(monkeypatch, [payload])
    with pytest.raises(BudgetStop):
        models.respond(conn, "error", "Invented")
    assert ledger(conn)[0]["ok"] == 0


@pytest.mark.parametrize("payload", [answer(output=None), answer(output=[None]),
    answer(output=[{"type": "message", "content": "wrong"}]),
    answer(output=[{"type": "message", "content": [{"type": "output_text", "text": 3}]}]),
    answer(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "declined"}]}])])
def test_unusable_text_response_uses_offline(conn, monkeypatch, payload):
    fake(monkeypatch, [payload])
    with pytest.raises(BudgetStop):
        text.respond_json(conn, "json", "", "", SCHEMA)


@pytest.mark.parametrize("code", [400, 401, 402, 403, 404, 504])
def test_permanent_errors_and_documented_timeout_do_not_retry(conn, monkeypatch, code):
    requests = fake(monkeypatch, [urllib.error.HTTPError("test", code, "stop", {}, None)])
    with pytest.raises(BudgetStop):
        models.respond(conn, "error", "Invented")
    assert len(requests) == 1


def test_budget_can_stop_retry(conn, monkeypatch):
    def post(*args):
        monkeypatch.setenv("FLOORHAND_BUDGET_USD", str(models.spent_micro(conn) / 1e6))
        raise urllib.error.HTTPError("test", 429, "retry", {}, None)
    monkeypatch.setattr(models, "_post", post)
    with pytest.raises(BudgetStop):
        models.respond(conn, "retry", "Invented")
    assert len(ledger(conn)) == 1


@pytest.mark.parametrize("usage", [None, {"input_tokens": -1, "output_tokens": 2},
    {"input_tokens": 2, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 3}},
    {"input_tokens": True, "output_tokens": 2}])
def test_missing_or_bad_usage_never_refunds_reservation(conn, monkeypatch, usage):
    fake(monkeypatch, [answer(usage=False, **{}) | {"usage": usage}])
    models.respond(conn, "usage", "Invented")
    row, = ledger(conn)
    assert row["estimated"] == 1 and row["cost_micro"] > 0


def test_schema_and_tools_are_in_budget_estimate(conn, monkeypatch):
    fake(monkeypatch, [answer(usage=False), answer(usage=False)])
    models.respond(conn, "small", "Invented")
    models.respond(conn, "large", "Invented", instructions="x" * 10000,
                   tools=[{"type": "function", "name": "lookup", "parameters": SCHEMA}])
    first, second = ledger(conn)
    assert second["cost_micro"] > first["cost_micro"]


@pytest.mark.parametrize("extra", [{"tools": [{"type": "web_search"}]}, {"previous_response_id": "resp_1"}])
def test_unbudgeted_options_refused(conn, extra):
    with pytest.raises(ValueError):
        models.respond(conn, "budget", "", **extra)
    assert not ledger(conn)


def test_concurrent_calls_reserve_before_network(conn, monkeypatch):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    barrier = Barrier(2)
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "0.0025")

    def post(*args):
        return answer(usage=False)
    monkeypatch.setattr(models, "_post", post)

    def call():
        with sqlite3.connect(path) as other:
            barrier.wait()
            try:
                models.respond(other, "overlap", "Invented", model="muse-spark-1.3", max_output_tokens=200)
                return True
            except BudgetStop:
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(), range(2)))
    assert sorted(results) == [False, True]
    assert models.spent_micro(conn) <= models.budget_micro()


def sse(events):
    return io.BytesIO(b"".join(("data: " + json.dumps(event) + "\r\n\r\n").encode() for event in events))


def test_stream_accumulates_deltas_until_completion():
    result = models._read_stream(sse([{"type": "response.output_text.delta", "delta": "abc"},
        {"type": "response.output_text.delta", "delta": "def"},
        {"type": "response.completed", "response": {"usage": {"images_processed": 1}}}]))
    assert result == {"output_text": "abcdef", "usage": {"images_processed": 1}}


def test_stream_zero_matches_and_truncation():
    assert models._read_stream(sse([{"type": "response.completed", "response": {}}]))["output_text"] == ""
    with pytest.raises(ValueError):
        models._read_stream(sse([{"type": "response.output_text.delta", "delta": "partial"}]))
    with pytest.raises(urllib.error.HTTPError) as exc:
        models._read_stream(sse([{"type": "error", "code": "server_shutting_down"}]))
    assert exc.value.code == 503


def test_real_transport_headers_json_and_multipart(conn, monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append(request)
        return io.BytesIO(b'{"transcript":"invented"}')
    monkeypatch.setattr(models.urllib.request, "urlopen", urlopen)
    # Exercise transport references retained before the fixture patches them.
    REAL_POST("/responses", {"model": models.SPARK, "input": "invented"})
    REAL_MULTIPART("/asr/transcribe", _wire.voice_fields(None), {"audio": ("recording.wav", b"abc", "audio/wav")})
    assert seen[0].full_url == "https://api.meta.ai/v1/responses"
    assert seen[0].get_header("Authorization") == "Bearer invented-test-key"
    assert json.loads(seen[0].data)["input"] == "invented"
    request = seen[1]
    assert request.full_url.endswith("/asr/transcribe")
    assert b'name="request"\r\nContent-Type: application/json' in request.data
    assert b'name="audio"; filename="recording.wav"\r\nContent-Type: audio/wav' in request.data
    assert b"abc\r\n" in request.data


REAL_POST = models._post
REAL_MULTIPART = models._post_multipart


@pytest.mark.parametrize("value,schema", [(True, {"type": "integer"}),
    ({"qty": 2, "extra": 1}, SCHEMA), (0, {"enum": [False]}),
    ("x", {"minLength": 2}), ([1, 1], {"uniqueItems": True}),
    (2, {"anyOf": [{"const": 3}, {"const": 4}]})])
def test_schema_rejects_invalid_values(value, schema):
    with pytest.raises(ValueError):
        _schema.validate(value, schema)


def test_schema_local_ref_nested_arrays_and_optional_fields():
    schema = {"type": "object", "$defs": {"qty": {"type": ["integer", "null"]}},
        "properties": {"units": {"type": "array", "items": {"$ref": "#/$defs/qty"}}}}
    _schema.check_schema(schema)
    _schema.validate({"units": [2, None]}, schema)
    with pytest.raises(ValueError):
        _schema.validate({"units": [True]}, schema)


def test_unsupported_schema_stops_before_spend(conn):
    with pytest.raises(BudgetStop):
        text.respond_json(conn, "json", "", "", {"type": "object", "patternProperties": {}})
    assert not ledger(conn)
