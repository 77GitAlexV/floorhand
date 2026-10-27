"""Invented photos and stock: a count is never a stock change without Yes."""
import builtins
import hashlib
import json
import random
import sqlite3
import struct
import types
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, models, shelf_vision as vision
from floorhand.features import shelf_count as shelf

BOXES = [[10, 10, 26, 35], [45, 10, 61, 35], [80, 10, 96, 35]]


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def photo(boxes=BOXES, width=120, height=60, color=0, filtering=0, inverted=False):
    """Synthetic parts, with PNG output that needs no imaging package."""
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    background, foreground = (0, 255) if inverted else (255, 0)
    grid = [[background] * width for _ in range(height)]
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1):
            grid[y][x0:x1] = [foreground] * (x1 - x0)
    previous, raw = bytes(width * channels), bytearray()
    for line in grid:
        row = bytes(v for p in line for v in ([p] * (3 if color in (2, 6) else 1)
                                            + ([255] if color in (4, 6) else [])))
        filtered = bytearray()
        for i, value in enumerate(row):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            prediction = 0
            if filtering == 1:
                prediction = left
            elif filtering == 2:
                prediction = up
            elif filtering == 3:
                prediction = (left + up) // 2
            elif filtering == 4:
                p = left + up - upper_left
                prediction = min((left, up, upper_left), key=lambda value: abs(value - p))
            filtered.append((value - prediction) % 256)
        raw.extend(bytes([filtering]) + filtered)
        previous = row
    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return vision.PNG + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    path = tmp_path / "shelf.db"
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    c = db.connect(path)
    db.init(c)
    c.execute("INSERT INTO settings VALUES ('tz', 'Asia/Bangkok')")
    c.execute("INSERT INTO stages VALUES (1, 'demo-cut', 'Demo cutting', 'Демо-раскрой', 1, 20)")
    c.executemany("INSERT INTO people (id, name, role, stage_id) VALUES (?, ?, ?, ?)",
                  [(1, "Demo keeper A", "manager", None), (2, "Demo keeper B", "owner", None),
                   (3, "Demo maker C", "worker", 1)])
    shelf.seed(c, random.Random(26))
    c.commit()
    yield c
    c.close()


@pytest.fixture
def client(conn):
    from floorhand.main import app
    with TestClient(app) as client:
        yield client


def proposal(conn, **kwargs):
    return shelf.propose(conn, 1, kwargs.pop("image", photo()), kwargs.pop("prompt", "armrest"),
                         kwargs.pop("item_id", 1), **kwargs)


def quantity(conn, item_id=1):
    return conn.execute("SELECT qty FROM shelf_items WHERE id = ?", (item_id,)).fetchone()[0]


def upload(client, **fields):
    return client.post("/api/shelf/count", files={"image": ("invented-parts.png", photo(), "image/png")},
                       data={"item_id": "1", "prompt": "подлокотник", **fields})


def live(monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "invented-test-key")
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "1")


def adapter(monkeypatch, count):
    original = shelf.importlib.import_module
    monkeypatch.setattr(shelf.importlib, "import_module",
                        lambda name, *a, **k: types.SimpleNamespace(count=count)
                        if name == "floorhand.meta.sam" else original(name, *a, **k))


@pytest.mark.parametrize("color", [0, 2, 4, 6])
@pytest.mark.parametrize("filtering", range(5))
def test_png_modes_filters_and_overlay(color, filtering):
    grid, width, height = vision._png_grid(photo(color=color, filtering=filtering))
    result = vision.count_grid(grid, width, height)
    assert result["count"] == 3
    assert result["boxes"] == BOXES
    assert (result["width"], result["height"]) == (120, 60)
    assert 0 < result["confidence"] <= 0.65


def test_offline_without_pillow(monkeypatch):
    original = builtins.__import__

    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("Absent for the test")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    assert vision.count_offline(photo())["boxes"] == BOXES


def test_optional_pillow_synthetic_photo():
    image_module = pytest.importorskip("PIL.Image")
    drawing = pytest.importorskip("PIL.ImageDraw")
    import io
    image = image_module.new("RGB", (120, 60), "white")
    draw = drawing.Draw(image)
    for x0, y0, x1, y1 in BOXES:
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill="black")
    data = io.BytesIO()
    image.save(data, format="PNG")
    assert vision.count_offline(data.getvalue())["boxes"] == BOXES


def test_inverse_background_noise_blank_and_touching():
    assert vision.count_offline(photo(inverted=True))["count"] == 3
    assert vision.count_offline(photo(boxes=[]))["count"] == 0
    assert vision.count_offline(photo(boxes=[[10, 10, 11, 11]]))["count"] == 0
    # The limitation is visible: touching parts form one component.
    assert vision.count_offline(photo(boxes=[[10, 10, 25, 25], [25, 10, 40, 25]]))["count"] == 1
    assert vision.count_offline(photo(boxes=[[0, 10, 20, 30]]))["confidence"] < 0.5


def test_downsampling_preserves_original_overlay_coordinates():
    result = vision.count_offline(photo(boxes=[[100, 20, 200, 100]], width=1200, height=600))
    assert result["count"] == 1
    assert result["width"] == 1200 and result["height"] == 600
    assert result["boxes"] == [[100, 20, 200, 100]]


def test_transparency_is_composited_onto_white():
    header = struct.pack(">IIBBBBB", 20, 20, 8, 6, 0, 0, 0)
    raw = (b"\0" + bytes([0, 0, 0, 0]) * 20) * 20
    image = vision.PNG + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    assert vision.count_offline(image)["count"] == 0


@pytest.mark.parametrize("image", [b"", b"not an image", photo()[:-4], photo() + b"garbage",
                                    photo().replace(b"IDAT", b"JUNK", 1)])
def test_invalid_images_are_clear_refusals(image):
    with pytest.raises(domain.DomainError, match=" / "):
        vision.decode_grid(image)


def test_unsupported_and_oversized_png_are_bounded():
    for header in [struct.pack(">IIBBBBB", 3000, 3000, 8, 0, 0, 0, 0),
                   struct.pack(">IIBBBBB", 20, 20, 8, 3, 0, 0, 0),
                   struct.pack(">IIBBBBB", 20, 20, 8, 0, 0, 0, 1)]:
        image = vision.PNG + chunk(b"IHDR", header) + chunk(b"IEND", b"")
        with pytest.raises(domain.DomainError):
            vision._png_grid(image)
    header = struct.pack(">IIBBBBB", 10, 10, 8, 0, 0, 0, 0)
    bomb = vision.PNG + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\0" * 10000)) + chunk(b"IEND", b"")
    with pytest.raises(domain.DomainError):
        vision._png_grid(bomb)


@pytest.mark.parametrize("grid", [[], [[]], [[0], [0, 1]], [[-1]], [[256]], [[True]], [[0.5]]])
def test_decoded_grid_is_validated(grid):
    with pytest.raises(domain.DomainError):
        vision.count_grid(grid)


def test_seed_is_idempotent_and_auto_loaded(conn):
    shelf.seed(conn, random.Random(1))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM shelf_items").fetchone()[0] == 3
    assert [r[0] for r in conn.execute("SELECT qty FROM shelf_items ORDER BY id")] == [25, 14, 18]


def test_offline_proposal_changes_no_stock(conn):
    result = proposal(conn)
    assert result["count"] == 3 and result["before"] == 25
    assert result["model"] == "offline-components" and result["status"] == "proposed"
    assert result["evidence"]["count"] == "ESTIMATE" and result["evidence"]["before"] == "FACT"
    assert result["message_ru"] and result["note_ru"] and "not part types" in result["note"]
    assert result["photo_hash"] == hashlib.sha256(photo()).hexdigest()
    assert result["box_format"] == "xyxy-pixels" and result["boxes"] == BOXES
    assert quantity(conn) == 25
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (result["id"],)).fetchone()
    assert row["kind"] == "shelf_count" and row["for_person"] == 1
    assert row["created_at"].endswith("+07:00")


def test_apply_uses_stored_offer_and_audits_who_when_hash(conn):
    offered = proposal(conn)
    offered["count"], offered["item_id"], offered["before"] = 999, 2, 999
    result = shelf.apply(conn, 2, offered["id"])
    assert quantity(conn) == 3 and quantity(conn, 2) == 14
    assert result["after"] == 3 and result["evidence"] == "FACT"
    row = conn.execute("SELECT * FROM stock_counts").fetchone()
    assert (row["person_id"], row["item_id"], row["before_qty"], row["after_qty"]) == (2, 1, 25, 3)
    assert row["photo_hash"] == hashlib.sha256(photo()).hexdigest()
    assert row["created_at"].endswith("+07:00")
    action = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (offered["id"],)).fetchone()
    assert action["status"] == "applied" and action["decided_by"] == 2
    again = shelf.apply(conn, 1, offered["id"])
    assert again["already"] and "Already recorded" in again["message"]
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE stock_counts SET after_qty = 8")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM stock_counts")


def test_rejection_and_retries_never_change_stock(conn):
    offered = proposal(conn)
    result = shelf.apply(conn, 1, offered["id"], "no")
    assert result["status"] == "rejected" and quantity(conn) == 25
    assert shelf.apply(conn, 1, offered["id"], "no")["already"]
    with pytest.raises(domain.DomainError, match="closed"):
        shelf.apply(conn, 1, offered["id"], "yes")
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 0


def test_cannot_reject_applied_proposal(conn):
    offered = proposal(conn)
    shelf.apply(conn, 1, offered["id"])
    with pytest.raises(domain.DomainError, match="closed"):
        shelf.apply(conn, 1, offered["id"], "no")
    assert quantity(conn) == 3


def test_stale_proposal_refused_even_if_stock_returns_to_before(conn):
    old, newer = proposal(conn), proposal(conn)
    shelf.apply(conn, 1, newer["id"])
    with pytest.raises(domain.DomainError, match="Stock changed"):
        shelf.apply(conn, 1, old["id"])
    many = [[5 + x * 20, 5 + y * 10, 13 + x * 20, 11 + y * 10] for y in range(5) for x in range(5)]
    restore = proposal(conn, image=photo(boxes=many))
    assert restore["count"] == 25
    shelf.apply(conn, 1, restore["id"])
    assert quantity(conn) == 25
    with pytest.raises(domain.DomainError, match="Stock changed"):
        shelf.apply(conn, 1, old["id"])
    assert conn.execute("SELECT status FROM agent_actions WHERE id = ?", (old["id"],)).fetchone()[0] == "proposed"


def test_zero_is_an_offered_count_not_an_automatic_change(conn):
    offered = proposal(conn, image=photo(boxes=[]))
    assert offered["count"] == 0 and quantity(conn) == 25
    assert offered["confidence"] < 0.5
    shelf.apply(conn, 1, offered["id"])
    assert quantity(conn) == 0


def test_same_key_records_one_proposal_and_refuses_different_request(conn, monkeypatch):
    offered = proposal(conn, idem_key="invented-photo-1")
    monkeypatch.setattr(shelf, "count_with_sam", lambda *a: pytest.fail("Retry must not count again"))
    repeated = proposal(conn, idem_key="invented-photo-1")
    assert repeated["id"] == offered["id"] and repeated["already"]
    for fields in ({"prompt": "frame"}, {"item_id": 2}, {"image": photo(boxes=[])}):
        with pytest.raises(domain.DomainError, match="another photo or item"):
            proposal(conn, idem_key="invented-photo-1", **fields)
    assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 1


def test_parallel_confirmations_record_once(conn):
    offered = proposal(conn)
    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def confirm(person_id):
        other = db.connect(path)
        try:
            return shelf.apply(other, person_id, offered["id"])
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, [1, 2]))
    assert sorted(r["already"] for r in results) == [False, True]
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 1
    assert quantity(conn) == 3


def test_parallel_same_key_creates_one_offer(conn):
    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def count(_):
        other = db.connect(path)
        try:
            return proposal(other, idem_key="parallel-invented-photo")
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(count, range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 1
    assert quantity(conn) == 25


def test_failed_audit_rolls_back_stock_and_decision(conn):
    offered = proposal(conn)
    conn.execute("CREATE TRIGGER fail_shelf_audit BEFORE INSERT ON stock_counts "
                 "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match="test failure"):
        shelf.apply(conn, 1, offered["id"])
    assert quantity(conn) == 25
    assert conn.execute("SELECT status FROM agent_actions WHERE id = ?", (offered["id"],)).fetchone()[0] == "proposed"


@pytest.mark.parametrize("fields", [{"prompt": " "}, {"prompt": "x" * 201}, {"item_id": 900},
                                     {"idem_key": " "}, {"idem_key": "x" * 121},
                                     {"image": b"bad"}])
def test_bad_inputs_do_not_create_proposals(conn, fields):
    with pytest.raises(domain.DomainError, match=" / "):
        proposal(conn, **fields)
    assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 0


def test_wrong_action_kind_invalid_decisions_and_worker_are_refused(conn):
    with pytest.raises(domain.DomainError, match="No shelf proposal"):
        shelf.apply(conn, 1, 999)
    wrong = conn.execute("INSERT INTO agent_actions (kind, payload, model, created_at) "
                         "VALUES ('intake', '{}', 'offline-rules', 'demo')").lastrowid
    conn.commit()
    with pytest.raises(domain.DomainError, match="No shelf proposal"):
        shelf.apply(conn, 1, wrong)
    offered = proposal(conn)
    with pytest.raises(domain.DomainError, match="offered"):
        shelf.apply(conn, 1, offered["id"], "set 12")
    with pytest.raises(domain.DomainError, match="office"):
        shelf.propose(conn, 3, photo(), "armrest", 1)
    with pytest.raises(domain.DomainError, match="office"):
        shelf.apply(conn, 3, offered["id"])


def test_sam_guard_prevents_adapter_when_off_or_budget_exhausted(conn, monkeypatch):
    adapter(monkeypatch, lambda *a: pytest.fail("No paid call allowed"))
    with pytest.raises(models.BudgetStop):
        shelf.count_with_sam(conn, photo(), "armrest")
    live(monkeypatch)
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "0")
    with pytest.raises(models.BudgetStop):
        shelf.count_with_sam(conn, photo(), "armrest")
    assert proposal(conn)["model"] == "offline-components"


def test_live_sam_adapter_contract_and_accounting(conn, monkeypatch):
    live(monkeypatch)

    def count(c, image, prompt):
        assert image == photo() and prompt == "armrest"
        # The real adapter must make its paid call and accounting via models.py.
        models._log(c, "sam-3.1", "shelf_count", units=1,
                    cost_micro=models.unit_cost_micro("sam-3.1", 1), estimated=True, ok=True)
        return {"count": 3, "boxes": BOXES, "width": 120, "height": 60, "confidence": 0.92}

    adapter(monkeypatch, count)
    offered = proposal(conn)
    assert offered["model"] == "sam-3.1" and "SAM 3.1" in offered["message"]
    assert offered["confidence"] == 0.92 and quantity(conn) == 25
    assert models.spent_micro(conn) == models.unit_cost_micro("sam-3.1", 1)
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 0


@pytest.mark.parametrize("patch", [{"count": 99}, {"count": True}, {"confidence": float('nan')},
                                    {"confidence": 1.2}, {"width": 0}, {"boxes": [[0, 0, 121, 50]]},
                                    {"boxes": [[0, 0, -1, 50]]}, {"boxes": [[True, 0, 4, 4]]}])
def test_malformed_sam_falls_back(conn, monkeypatch, patch):
    live(monkeypatch)
    adapter(monkeypatch, lambda *a: {"count": 3, "boxes": BOXES, "width": 120, "height": 60,
                                    "confidence": 0.8, **patch})
    result = proposal(conn)
    assert result["model"] == "offline-components" and result["count"] == 3


def test_unavailable_sam_adapter_falls_back(conn, monkeypatch):
    live(monkeypatch)
    adapter(monkeypatch, None)
    assert proposal(conn)["model"] == "offline-components"


def test_missing_sam_module_and_provider_failure_fall_back(conn, monkeypatch):
    live(monkeypatch)
    original = shelf.importlib.import_module

    def missing(name, *args, **kwargs):
        if name == "floorhand.meta.sam":
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)

    with monkeypatch.context() as isolated:
        isolated.setattr(shelf.importlib, "import_module", missing)
        assert proposal(conn)["model"] == "offline-components"

    def failed(*args):
        raise models.BudgetStop("Invented provider failure")

    adapter(monkeypatch, failed)
    assert proposal(conn)["model"] == "offline-components"


def test_api_auth_multipart_count_and_confirm_by_id(client, conn):
    assert client.get("/api/shelf/items").status_code == 401
    assert client.get("/api/shelf/1").status_code == 401
    assert client.post("/api/shelf/count").status_code == 401
    assert client.post("/api/shelf/1/apply", json={"decision": "yes"}).status_code == 401
    client.post("/api/login", json={"person_id": 3, "tablet": True})
    assert upload(client).status_code == 403
    assert client.get("/api/shelf/items").status_code == 403
    assert client.post("/api/shelf/1/apply", json={"decision": "yes"}).status_code == 403
    client.post("/api/login", json={"person_id": 1})
    items = client.get("/api/shelf/items").json()
    assert len(items["items"]) == 3 and items["evidence"] == "FACT"
    response = upload(client, idem_key="api-demo")
    assert response.status_code == 200, response.text
    offered = response.json()
    assert offered["prompt"] == "подлокотник" and offered["count"] == 3
    assert quantity(conn) == 25
    assert upload(client, idem_key="api-demo").json()["id"] == offered["id"]
    assert client.get(f"/api/shelf/{offered['id']}").json()["photo_hash"] == offered["photo_hash"]
    applied = client.post(f"/api/shelf/{offered['id']}/apply", json={"decision": "yes"})
    assert applied.status_code == 200 and applied.json()["after"] == 3
    assert quantity(conn) == 3
    closed = client.get(f"/api/shelf/{offered['id']}").json()
    assert closed["choices"] == [] and closed["result"]["status"] == "applied"
    assert "after your confirmation" in closed["message"]
    assert upload(client, idem_key="api-demo").json()["choices"] == []


@pytest.mark.parametrize("extra", [{"count": 99}, {"item_id": 2}, {"before": 0}, {"after": -1},
                                    {"photo_hash": "fake"}, {"qty": 3}])
def test_api_refuses_values_not_offered(client, conn, extra):
    offered = proposal(conn)
    client.post("/api/login", json={"person_id": 1})
    response = client.post(f"/api/shelf/{offered['id']}/apply", json={"decision": "yes", **extra})
    assert response.status_code == 409 and "cannot be changed" in response.json()["message"]
    assert quantity(conn) == 25
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 0


def test_api_raw_upload_no_and_bad_upload_messages(client, conn):
    client.post("/api/login", json={"person_id": 1})
    response = client.post("/api/shelf/count?item_id=2&prompt=frame", content=photo(),
                           headers={"Content-Type": "image/png"})
    assert response.status_code == 200
    action_id = response.json()["id"]
    assert client.post(f"/api/shelf/{action_id}/apply", json={"decision": "no"}).json()["status"] == "rejected"
    assert quantity(conn, 2) == 14
    for kwargs in [{"json": {}}, {"content": b"incomplete", "headers": {
        "Content-Type": "multipart/form-data; boundary=fictional-boundary"}}]:
        bad = client.post("/api/shelf/count", **kwargs)
        assert bad.status_code == 409 and " / " in bad.json()["message"]
    bad = client.post("/api/shelf/count", files={"image": ("demo.png", photo(), "image/png")},
                      data={"item_id": "not-an-id", "prompt": "armrest"})
    assert bad.status_code == 409


def test_api_limits_upload_size_before_decoding(client, monkeypatch):
    client.post("/api/login", json={"person_id": 1})
    monkeypatch.setattr(shelf, "MAX_BYTES", 10)
    response = upload(client)
    assert response.status_code == 409 and "8 MiB" in response.json()["message"]


def test_api_rejects_duplicate_fields_and_unoffered_decisions(client, conn):
    client.post("/api/login", json={"person_id": 1})
    response = client.post("/api/shelf/count", files=[
        ("image", ("first.png", photo(), "image/png")),
        ("image", ("second.png", photo(), "image/png"))],
        data={"item_id": "1", "prompt": "armrest"})
    assert response.status_code == 409
    offered = proposal(conn)
    for body in ["invalid json", "[]", '{}', '{"decision":true}', '{"decision":"set 99"}']:
        refused = client.post(f"/api/shelf/{offered['id']}/apply", content=body,
                              headers={"Content-Type": "application/json"})
        assert refused.status_code == 409 and " / " in refused.json()["message"]
    assert quantity(conn) == 25


def test_streaming_upload_limit_is_enforced(client):
    client.post("/api/login", json={"person_id": 1})
    response = client.post("/api/shelf/count?item_id=1&prompt=armrest",
                           content=b"x" * (shelf.MAX_BYTES + 65537),
                           headers={"Content-Type": "image/png"})
    assert response.status_code == 409 and "too large" in response.json()["message"]


# Exercise the integration seam with the real parser and a fake paid transport.
def sam_output(scores=None, widths=None):
    records = []
    for i, (x1, y1, x2, y2) in enumerate(BOXES):
        score = f";c={scores[i]}" if scores else ""
        width = widths[i] if widths else 120
        records.append(f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h=60{score}|>"
                       f"<|mask;x=0;y=0{score};data=1,1,~a|>")
    return "<0f>" + ",".join(records)


@pytest.mark.parametrize("scores,confidence", [(None, 0.0), ([.9, .6, .8], .6)])
def test_real_sam_adapter_proposes_without_changing_stock(conn, monkeypatch, scores, confidence):
    live(monkeypatch)
    requests = []

    def post(path, body):
        requests.append((path, body))
        row = conn.execute("SELECT * FROM model_calls").fetchone()
        assert row["ok"] == 0  # Reserve before any network I/O.
        return {"output_text": sam_output(scores), "usage": {"images_processed": 1}}

    monkeypatch.setattr(models, "_post", post)
    result = proposal(conn)
    assert result["model"] == "sam-3.1" and result["count"] == 3
    assert result["boxes"] == BOXES and (result["width"], result["height"]) == (120, 60)
    assert result["confidence"] == confidence and quantity(conn) == 25
    assert conn.execute("SELECT COUNT(*) FROM stock_counts").fetchone()[0] == 0
    row, = conn.execute("SELECT * FROM model_calls").fetchall()
    assert row["purpose"] == "shelf_count" and row["ok"] == 1
    assert row["cost_micro"] == models.unit_cost_micro("sam-3.1", 1)
    assert requests[0][0] == "/responses" and requests[0][1]["stream"] is True


def test_real_sam_adapter_zero_matches_keeps_photo_dimensions(conn, monkeypatch):
    from floorhand.meta import sam
    live(monkeypatch)
    monkeypatch.setattr(models, "_post", lambda *a: {"output_text": ""})
    result = sam.count(conn, photo(boxes=[]), "armrest")
    assert result == {"boxes": [], "width": 120, "height": 60, "confidence": 0.0, "count": 0}


def test_real_sam_adapter_jpeg_uses_source_pixel_boxes(conn, monkeypatch):
    from floorhand.meta import sam
    live(monkeypatch)
    requests = []

    def post(path, body):
        requests.append(body)
        return {"output_text": sam_output()}

    monkeypatch.setattr(models, "_post", post)
    result = sam.count(conn, b"\xff\xd8invented-jpeg", "armrest")
    assert result["boxes"] == BOXES and result["count"] == 3
    url = requests[0]["input"][0]["content"][1]["image_url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_real_sam_adapter_inconsistent_dimensions_use_offline(conn, monkeypatch):
    live(monkeypatch)
    monkeypatch.setattr(models, "_post", lambda *a: {"output_text": sam_output(widths=[120, 121, 120])})
    result = proposal(conn)
    assert result["model"] == "offline-components" and result["count"] == 3
    assert quantity(conn) == 25


def test_real_sam_adapter_offline_makes_no_request(conn, monkeypatch):
    from floorhand.meta import sam
    monkeypatch.setattr(models, "_post", lambda *a: pytest.fail("No network while offline"))
    with pytest.raises(models.BudgetStop):
        sam.count(conn, photo(), "armrest")
