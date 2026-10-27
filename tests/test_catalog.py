"""The catalog by one sentence: a card first, written only on Yes."""
import json
import random
from datetime import date

import pytest

from floorhand import catalog, db, domain, seed
from floorhand.domain import DomainError


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    c = db.connect(tmp_path / "c.db")
    seed.build(c, random.Random(2), today=date.today())
    return c


def m(conn):
    return conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]


def stage(conn, code):
    return conn.execute("SELECT id FROM stages WHERE code = ?", (code,)).fetchone()[0]


def test_add_product_with_two_stages_and_one_rate(conn):
    p = catalog.propose(conn, m(conn), "add chair Milo, stages sewing and assembly, rate 3, price 160")
    assert p["model"] == "offline-rules" and p["action"] == "add_product"
    assert p["name"]["value"] == "Milo" and p["kind"]["value"] == "chair"
    assert [(r["name_en"], r["amount"]) for r in p["rates"]] == [("Sewing", 3), ("Assembly", 3)]
    assert {s["name_en"] for s in p["no_rate"]} == {"Cutting", "Upholstery", "Packing"}
    assert "not 0" in p["card"]
    assert conn.execute("SELECT 1 FROM products WHERE name = 'Milo'").fetchone() is None
    res = catalog.apply(conn, m(conn), p["id"])
    assert res["ok"]
    pid = conn.execute("SELECT id FROM products WHERE name = 'Milo'").fetchone()[0]
    rates = dict(conn.execute("SELECT stage_id, amount FROM rates WHERE product_id = ?", (pid,)))
    assert rates == {stage(conn, "sew"): 3, stage(conn, "asm"): 3}  # others: no row, not 0
    with pytest.raises(DomainError, match="closed"):
        catalog.apply(conn, m(conn), p["id"])


def test_russian_sentence_with_rates_per_stage(conn):
    p = catalog.propose(conn, m(conn), "добавь пуф Облако, этапы шитьё 2 и обивка 5, цена 90")
    assert p["kind"]["value"] == "pouf" and p["price"]["value"] == 90
    assert [(r["name_en"], r["amount"]) for r in p["rates"]] == [("Sewing", 2), ("Upholstery", 5)]


def test_missing_price_is_asked_not_guessed(conn):
    p = catalog.propose(conn, m(conn), "add sofa Fjord, all stages, rates 10/20/30/15/4")
    assert "price" in p["questions"] and len(p["rates"]) == 5
    with pytest.raises(DomainError, match="price"):
        catalog.apply(conn, m(conn), p["id"])
    assert "Fjord" in catalog.apply(conn, m(conn), p["id"], price=1300)["message"]


def test_existing_product_means_changing_its_rates(conn):
    p = catalog.propose(conn, m(conn), "rate 7 for Lord armchair at upholstery")
    assert p["action"] == "set_rates" and p["rates"][0]["old"] == 15
    catalog.apply(conn, m(conn), p["id"])
    pid = conn.execute("SELECT id FROM products WHERE name = 'Lord armchair'").fetchone()[0]
    assert conn.execute("SELECT amount FROM rates WHERE product_id = ? AND stage_id = ?",
                        (pid, stage(conn, "uph"))).fetchone()[0] == 7


def test_near_duplicate_name_is_pointed_out(conn):
    p = catalog.propose(conn, m(conn), "add chair Bruno, stages sewing, rate 3, price 150")
    assert "Bruno chair" in p["similar"] and "already exists" in p["card"]


def test_fabric_and_customer(conn):
    p = catalog.propose(conn, m(conn), "add fabric velvet ocean")
    catalog.apply(conn, m(conn), p["id"])
    fabrics = json.loads(conn.execute("SELECT value FROM settings WHERE key = 'fabrics'"
                                      ).fetchone()[0])
    assert "velvet ocean" in fabrics
    with pytest.raises(DomainError, match="already"):
        catalog.propose(conn, m(conn), "add fabric velvet ocean")
    p = catalog.propose(conn, m(conn), "add customer Blue Door Cafe, city Austin")
    catalog.apply(conn, m(conn), p["id"])
    assert conn.execute("SELECT city FROM customers WHERE name = 'Blue Door Cafe'"
                        ).fetchone()[0] == "Austin"


def test_model_quotes_not_in_the_sentence_are_dropped(conn, monkeypatch):
    monkeypatch.setattr(catalog, "_parse_spark", lambda conn, text: {
        "action": "add_product", "name": {"value": "Milo", "quote": "Milo"},
        "kind": {"value": "chair", "quote": "chair"},
        "price": {"value": "999", "quote": "price 999"},
        "stages": [{"stage": {"value": "Sewing", "quote": "sewing"},
                    "rate": {"value": "50", "quote": "rate 50"}}]})
    p = catalog.propose(conn, m(conn), "add chair Milo, sewing")
    assert p["price"]["value"] is None and p["rates"][0]["amount"] is None
    assert set(p["questions"]) == {"price", "rates"}


def test_workers_cannot_change_the_catalog(conn):
    w = conn.execute("SELECT id FROM people WHERE role = 'worker' LIMIT 1").fetchone()[0]
    with pytest.raises(DomainError, match="office"):
        catalog.propose(conn, w, "add fabric x")
