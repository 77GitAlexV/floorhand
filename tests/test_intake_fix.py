"""The person has the last word on every proposed field; corrections are learned;
photos follow the same quote rule and work by hand when the model is off."""
import json
import random
from datetime import date

import pytest

from floorhand import db, domain, intake, models, seed
from floorhand.domain import DomainError

TODAY = date(2026, 10, 27)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    monkeypatch.setattr(domain, "today", lambda conn: TODAY.isoformat())
    c = db.connect(tmp_path / "f.db")
    seed.build(c, random.Random(3), today=TODAY)
    return c


def manager(conn):
    return conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]


def pid(conn, name):
    return conn.execute("SELECT id FROM products WHERE name = ?", (name,)).fetchone()[0]


def test_an_ok_field_can_be_corrected_and_the_correction_is_learned(conn):
    m = manager(conn)
    text = "Home & Co: 2 lord chairs in velvet beige, walnut"
    p = intake.propose(conn, m, text)
    f = p["items"][0]["fields"]["product"]
    assert f["status"] == "check"
    # the person types the name instead of picking an option
    res = intake.fix(conn, m, p["id"], 0, "product", "Lord rocking chair")
    assert res["ok"] and res["item"]["fields"]["product"]["value"] == "Lord rocking chair"
    # and changes an "ok" field the reader got right but the customer meant otherwise
    res = intake.fix(conn, m, p["id"], 0, "qty", "3")
    assert res["item"]["fields"]["qty"]["value"] == 3
    out = intake.apply(conn, m, p["id"], {})
    order = conn.execute("SELECT * FROM orders WHERE number = ?", (out["created"][0],)).fetchone()
    assert order["product_id"] == pid(conn, "Lord rocking chair") and order["qty"] == 3
    payload = json.loads(conn.execute("SELECT payload FROM agent_actions WHERE id = ?",
                                      (p["id"],)).fetchone()[0])
    assert {c["field"] for c in payload["corrections"]} == {"product", "qty"}
    again = intake.propose(conn, m, text)
    assert again["items"][0]["fields"]["product"]["value"] == "Lord rocking chair"


def test_typed_value_not_in_catalog_comes_back_with_suggestions(conn):
    m = manager(conn)
    p = intake.propose(conn, m, "Home & Co: 1 Cozy armchair, velvet beige")
    res = intake.fix(conn, m, p["id"], 0, "product", "Cozzy armchar")
    assert res["ok"] is False and any(o["label"] == "Cozy armchair" for o in res["options"])
    res = intake.fix(conn, m, p["id"], 0, "variant", "velvett beige")
    assert res["ok"] is False and res["options"][0]["label"].startswith("velvet beige")
    res = intake.fix(conn, m, p["id"], 0, "qty", "0")
    assert res["ok"] is False and "between 1 and 500" in res["message"]
    payload = json.loads(conn.execute("SELECT payload FROM agent_actions WHERE id = ?",
                                      (p["id"],)).fetchone()[0])
    assert payload["items"][0]["fields"]["product"]["value"] == "Cozy armchair"  # untouched


def test_add_a_missed_line_and_remove_a_wrong_one(conn):
    m = manager(conn)
    p = intake.propose(conn, m, "Urban Loft: 1 Cozy armchair velvet beige, 2 round poufs")
    assert len(p["items"]) == 2
    intake.remove_line(conn, m, p["id"], 1)
    idx = intake.add_line(conn, m, p["id"])["idx"]
    assert intake.apply(conn, m, p["id"], {})["waiting"] == 1  # the empty new line waits
    for field, value in (("product", "Square pouf"), ("qty", 4), ("customer", "Urban Loft"),
                         ("due_date", "Nov 20"), ("variant", "boucle milk")):
        assert intake.fix(conn, m, p["id"], idx, field, value)["ok"]
    out = intake.apply(conn, m, p["id"], {})
    assert out["waiting"] == 0 and len(out["created"]) == 1
    o = conn.execute("SELECT * FROM orders WHERE number = ?", (out["created"][0],)).fetchone()
    assert (o["qty"], o["due_date"], o["variant"]) == (4, "2026-11-20", "boucle milk")
    assert o["product_id"] == pid(conn, "Square pouf")  # the removed pouf line made nothing
    with pytest.raises(DomainError, match="closed"):
        intake.fix(conn, m, p["id"], 0, "qty", 2)


def test_new_customer_typed_by_hand_is_created_and_learned(conn):
    m = manager(conn)
    text = "from Bluebird Cafe: 2 Bruno chairs, linen sand"
    p = intake.propose(conn, m, text)
    f = p["items"][0]["fields"]["customer"]
    res = intake.fix(conn, m, p["id"], 0, "customer", "Bluebird")
    assert res["ok"] is False and any(o["id"] == "new:Bluebird" for o in res["options"])
    assert intake.fix(conn, m, p["id"], 0, "customer", "new:Bluebird Cafe")["ok"]
    out = intake.apply(conn, m, p["id"], {})
    cid = conn.execute("SELECT customer_id FROM orders WHERE number = ?",
                       (out["created"][0],)).fetchone()[0]
    assert conn.execute("SELECT name FROM customers WHERE id = ?", (cid,)).fetchone()[0] \
        == "Bluebird Cafe"
    if f.get("quote"):
        assert conn.execute("SELECT target_id FROM synonyms WHERE kind = 'customer' "
                            "AND alias = ?", (intake.norm(f["quote"]),)).fetchone()[0] == cid


def test_photo_with_the_model_off_is_kept_for_typing_by_hand(conn):
    m = manager(conn)
    p = intake.propose_photo(conn, m, PNG, "image/png")
    assert p["model"] == "offline" and p["items"] == [] and "off" in p["message"]
    assert conn.execute("SELECT mime FROM intake_photos WHERE id = ?",
                        (p["photo_id"],)).fetchone()[0] == "image/png"
    idx = intake.add_line(conn, m, p["id"])["idx"]
    for field, value in (("product", "Footstool"), ("qty", 1), ("customer", "stock")):
        intake.fix(conn, m, p["id"], idx, field, value)
    assert len(intake.apply(conn, m, p["id"], {})["created"]) == 1


def test_photo_must_be_an_image(conn):
    with pytest.raises(DomainError, match="JPEG"):
        intake.propose_photo(conn, manager(conn), b"hello", "image/png")
    with pytest.raises(DomainError, match="JPEG"):
        intake.propose_photo(conn, manager(conn), PNG, "application/pdf")


def test_photo_quotes_must_be_in_the_transcript(conn, monkeypatch):
    answer = {"transcript": "ORDER FORM\nUrban Loft\nCozy armchair x 2 velvet beige",
              "orders": [{"product": {"value": "Cozy armchair", "quote": "Cozy armchair"},
                          "qty": {"value": "2", "quote": "x 2"},
                          "customer": {"value": "Home & Co", "quote": "Home & Co"},
                          "variant": {"value": "velvet beige", "quote": "velvet beige"}}]}
    monkeypatch.setattr(models, "vision", lambda *a, **k: {"output_text": json.dumps(answer)})
    p = intake.propose_photo(conn, manager(conn), PNG, "image/png")
    f = p["items"][0]["fields"]
    assert p["source"].startswith("ORDER FORM")
    assert f["product"]["value"] == "Cozy armchair" and f["qty"]["value"] == 2
    assert f["customer"]["status"] != "ok"  # "Home & Co" is not on the photo: dropped


def test_vision_budget_counts_the_image_not_its_base64(conn, monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "test")
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "0.01")
    sent = []
    monkeypatch.setattr(models, "_post", lambda path, body, timeout=120: sent.append(body) or
                        {"output_text": "{}", "usage": {"input_tokens": 3000, "output_tokens": 50}})
    big = "A" * 4_000_000  # ~3 MB photo: by text length it would look like 2M tokens
    models.vision(conn, "test", "read it", big, "image/png")
    assert sent and sent[0]["input"][0]["content"][1]["type"] == "input_image"
    monkeypatch.delenv("FLOORHAND_META_LIVE")
    with pytest.raises(models.BudgetStop):
        models.vision(conn, "test", "read it", "AAAA", "image/png")
