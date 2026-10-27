"""Order intake: quotes or nothing, certain matches or a question, never a silent guess."""
import random
from datetime import date

import pytest

from floorhand import db, domain, intake, seed
from floorhand.domain import DomainError

TODAY = date(2026, 10, 27)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    monkeypatch.setattr(domain, "today", lambda conn: TODAY.isoformat())
    c = db.connect(tmp_path / "i.db")
    seed.build(c, random.Random(3), today=TODAY)
    return c


def manager(conn):
    return conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]


def test_email_with_two_items(conn):
    p = intake.propose(conn, manager(conn), (
        "Hi! Home & Co here.\n"
        "Please make 2 Cozy armchairs in velvet beige, walnut legs, and 3 round poufs boucle milk.\n"
        "We need them by Nov 12. Thanks!"))
    assert p["model"] == "offline-rules"
    a, b = p["items"]
    assert a["fields"]["product"]["value"] == "Cozy armchair"
    assert a["fields"]["qty"]["value"] == 2
    assert a["fields"]["customer"]["value"] == "Home & Co"
    assert a["fields"]["due_date"]["value"] == "2026-11-12"
    assert a["fields"]["variant"]["value"] == "velvet beige · walnut"
    assert b["fields"]["product"]["value"] == "Round pouf" and b["fields"]["qty"]["value"] == 3
    assert a["ready"] and b["ready"]


def test_table_columns_by_header_not_position(conn):
    p = intake.propose(conn, manager(conn), (
        "Client;Qty;Model;Fabric;Due\n"
        "Urban Loft;1;Sofa Oslo 3-seat;linen sand;2026-11-20\n"
        "Studio Blanc;4;Footstool;boucle ash;2026-11-03\n"))
    first, second = p["items"]
    assert first["fields"]["customer"]["value"] == "Urban Loft"
    assert first["fields"]["product"]["value"] == "Sofa Oslo 3-seat"
    assert first["fields"]["qty"]["value"] == 1
    assert second["fields"]["qty"]["value"] == 4 and second["ready"]


def test_ambiguous_product_is_a_question(conn):
    p = intake.propose(conn, manager(conn), "Urban Loft: 1 harbor chair, emerald velvet, oak")
    f = p["items"][0]["fields"]["product"]
    assert f["status"] == "check" and f["id"] is None
    labels = {o["label"] for o in f["options"]}
    assert {"Harbor armchair", "Harbor rocking chair"} <= labels
    assert not p["items"][0]["ready"]


def test_unknown_customer_offers_new_or_stock(conn):
    p = intake.propose(conn, manager(conn), "customer: Bluebird Cafe; 2 Bruno chairs")
    f = p["items"][0]["fields"]["customer"]
    assert f["status"] == "check"


def test_quote_not_in_source_drops_the_field(conn):
    cat = intake._catalog(conn)
    raw = {"product": {"value": "Harbor armchair", "quote": "Harbor armchair"},
           "qty": {"value": "5", "quote": "five"}, "customer": {"value": None, "quote": None}}
    item = intake.ground(conn, "we want five chairs", raw, cat, TODAY)
    assert item["fields"]["product"]["status"] == "empty"
    assert item["fields"]["qty"]["status"] == "ok"


def test_propose_writes_no_orders_and_apply_learns(conn):
    before = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    text = "Urban Loft: 1 harbor chair, velvet emerald, oak"
    p = intake.propose(conn, manager(conn), text)
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before
    harbor = conn.execute("SELECT id FROM products WHERE name = 'Harbor armchair'").fetchone()[0]
    with pytest.raises(DomainError, match="offered"):
        intake.apply(conn, manager(conn), p["id"], {"0": {"product": 99999}})
    res = intake.apply(conn, manager(conn), p["id"], {"0": {"product": harbor}})
    assert len(res["created"]) == 1 and res["waiting"] == 0
    order = conn.execute("SELECT * FROM orders WHERE number = ?", (res["created"][0],)).fetchone()
    assert order["product_id"] == harbor and order["source"] == "agent"
    assert conn.execute("SELECT COUNT(*) FROM units WHERE order_id = ?",
                        (order["id"],)).fetchone()[0] == 1
    with pytest.raises(DomainError, match="closed"):
        intake.apply(conn, manager(conn), p["id"], {})
    again = intake.propose(conn, manager(conn), text)
    assert again["items"][0]["fields"]["product"]["value"] == "Harbor armchair"
    assert again["items"][0]["ready"]


def test_numbers_in_dates_and_names_are_not_quantities(conn):
    p = intake.propose(conn, manager(conn), "Field & Fern, Sofa Oslo 3-seat linen sand oak, due 2026-11-15")
    q = p["items"][0]["fields"]["qty"]
    assert q["status"] == "check"  # amount not written: asked, not taken from "3-seat"


def test_options_share_a_word_not_a_digit(conn):
    p = intake.propose(conn, manager(conn), "Urban Loft: 3 poufs, velvet beige")
    labels = [o["label"] for o in p["items"][0]["fields"]["product"]["options"]]
    assert labels and all("pouf" in x.lower() for x in labels)
