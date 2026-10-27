"""Order depth: edits with history, rework with who pays, cancel to stock.
Each rule here came from a real workshop's mistakes."""
import random
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, seed
from floorhand.domain import DomainError
from floorhand.features import orders as O


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    seed.build(c, random.Random(5), today=date.today())
    return c


def office(conn):
    return conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]


def stage(conn, code):
    return conn.execute("SELECT id FROM stages WHERE code = ?", (code,)).fetchone()[0]


def worker_at(conn, code, n=0):
    return conn.execute("SELECT id FROM people WHERE stage_id = ? ORDER BY id LIMIT 1 OFFSET ?",
                        (stage(conn, code), n)).fetchone()[0]


def product(conn, name="Cozy armchair"):
    return conn.execute("SELECT id FROM products WHERE name = ?", (name,)).fetchone()[0]


def new_order(conn, qty=1, name="Cozy armchair", customer=1, variant="velvet beige · walnut"):
    return domain.create_order(conn, product_id=product(conn, name), qty=qty,
                               customer_id=customer, due_date="2026-12-01", variant=variant,
                               source="manual")


def units(conn, oid):
    return [r["id"] for r in conn.execute("SELECT id FROM units WHERE order_id = ? ORDER BY seq",
                                          (oid,))]


def advance(conn, uid, upto):
    """Mark every stage up to and including `upto` for one item."""
    for s in domain.stages(conn):
        domain.mark_done(conn, worker_at(conn, s["code"]), uid)
        if s["code"] == upto:
            return


def money_of(conn, pid):
    e = domain.earnings(conn, pid, days=60)
    return e["paid"] + e["to_be_paid"]


def qty_matches_units(conn):
    bad = conn.execute("SELECT o.number FROM orders o WHERE o.qty != "
                       "(SELECT COUNT(*) FROM units u WHERE u.order_id = o.id)").fetchall()
    return [r[0] for r in bad]


# --- edits and their history ------------------------------------------------

def test_every_edit_is_recorded_with_who_old_new_and_reason(conn):
    oid = new_order(conn)
    m = office(conn)
    O.edit(conn, m, oid, "due_date", "2026-12-10", reason="Customer moved delivery")
    O.edit(conn, m, oid, "customer", 2)
    O.edit(conn, m, oid, "note", "Call before delivery")
    rows = conn.execute("SELECT field, old, new, reason, person_id FROM order_changes "
                        "WHERE order_id = ? ORDER BY id", (oid,)).fetchall()
    assert [r["field"] for r in rows] == ["due_date", "customer", "note"]
    assert (rows[0]["old"], rows[0]["new"], rows[0]["reason"]) == \
        ("2026-12-01", "2026-12-10", "Customer moved delivery")
    assert all(r["person_id"] == m for r in rows)
    story = O.story(conn, oid)
    assert story["note"] == "Call before delivery" and len(story["changes"]) == 3


def test_same_value_again_changes_nothing(conn):
    oid = new_order(conn)
    res = O.edit(conn, office(conn), oid, "due_date", "2026-12-01")
    assert res["changed"] is False and "Nothing changed" in res["message"]
    assert conn.execute("SELECT COUNT(*) FROM order_changes WHERE order_id = ?",
                        (oid,)).fetchone()[0] == 0


def test_order_changes_are_append_only(conn):
    oid = new_order(conn)
    O.edit(conn, office(conn), oid, "note", "x")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE order_changes SET new = 'y'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM order_changes")


def test_workers_cannot_edit_orders(conn):
    oid = new_order(conn)
    with pytest.raises(DomainError, match="office"):
        O.edit(conn, worker_at(conn, "sew"), oid, "note", "x")


def test_unknown_values_are_refused_with_the_closest_names(conn):
    oid = new_order(conn)
    with pytest.raises(DomainError, match="Closest"):
        O.edit(conn, office(conn), oid, "product", "Cozzy armchar", reason="typo")
    with pytest.raises(DomainError, match="fabric"):
        O.edit(conn, office(conn), oid, "variant", "velour pink")
    with pytest.raises(DomainError, match="2026-11-05"):
        O.edit(conn, office(conn), oid, "due_date", "next friday")


# --- quantity -----------------------------------------------------------------

def test_qty_up_adds_items_down_removes_not_started(conn):
    oid = new_order(conn, qty=2)
    m = office(conn)
    O.edit(conn, m, oid, "qty", 4)
    assert len(units(conn, oid)) == 4
    with pytest.raises(DomainError, match="why"):
        O.edit(conn, m, oid, "qty", 3)
    O.edit(conn, m, oid, "qty", 3, reason="Customer needs 3")
    assert len(units(conn, oid)) == 3 and qty_matches_units(conn) == []


def test_qty_down_into_started_items_asks_and_moves_them_to_stock(conn):
    oid = new_order(conn, qty=2)
    u1, u2 = units(conn, oid)
    advance(conn, u1, "sew")
    advance(conn, u2, "cut")
    m = office(conn)
    with pytest.raises(DomainError, match="why"):
        O.edit(conn, m, oid, "qty", 1)
    res = O.edit(conn, m, oid, "qty", 1, reason="Customer wants one")
    assert res["ok"] is False and res["needs"] == "started"
    assert len(units(conn, oid)) == 2  # nothing happened yet
    res = O.edit(conn, m, oid, "qty", 1, reason="Customer wants one", started="to_stock")
    assert res["ok"] and "stock" in res["message"]
    assert units(conn, oid) == [u1]  # the least advanced item went to stock
    stock = domain._unit_order(conn, u2)
    assert stock["customer_id"] is None and not stock["cancelled"]
    assert domain.done_stage_ids(conn, u2)  # its work moved with it
    assert qty_matches_units(conn) == []


def test_customer_of_shipped_items_cannot_change(conn):
    oid = new_order(conn, qty=2)
    u1, _ = units(conn, oid)
    advance(conn, u1, "pack")
    domain.ship(conn, office(conn), 1, [u1], "paid")
    with pytest.raises(DomainError, match="already left"):
        O.edit(conn, office(conn), oid, "customer", 2)


def test_qty_cannot_drop_shipped_items(conn):
    oid = new_order(conn, qty=3)
    us = units(conn, oid)
    for u in us[:2]:
        advance(conn, u, "pack")
    domain.ship(conn, office(conn), 1, us[:2], "paid")
    with pytest.raises(DomainError, match="cannot go below 2"):
        O.edit(conn, office(conn), oid, "qty", 1, reason="less")


# --- product and variant after work -----------------------------------------

def test_product_change_before_work_is_plain(conn):
    oid = new_order(conn)
    res = O.edit(conn, office(conn), oid, "product", "Lord armchair", reason="Customer chose Lord")
    assert res["changed"]
    assert domain._unit_order(conn, units(conn, oid)[0])["product_id"] == product(conn, "Lord armchair")


def test_product_change_after_cutting_keeps_the_cut_parts_as_what_they_are(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "cut")
    m = office(conn)
    res = O.edit(conn, m, oid, "product", "Lord armchair", reason="Customer chose Lord")
    assert res["needs"] == "started" and "Cozy armchair" in res["message"]
    res = O.edit(conn, m, oid, "product", "Lord armchair", reason="Customer chose Lord",
                 started="to_stock")
    assert res["ok"]
    old = domain._unit_order(conn, u)
    assert old["product_id"] == product(conn) and old["customer_id"] is None  # cut Cozy in stock
    (fresh,) = units(conn, oid)
    assert fresh != u and not domain.done_stage_ids(conn, fresh)  # Lord starts at Cutting
    assert domain._unit_order(conn, fresh)["product_id"] == product(conn, "Lord armchair")
    assert qty_matches_units(conn) == []


def test_product_of_shipped_items_cannot_change(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "pack")
    domain.ship(conn, office(conn), 1, [u], "paid")
    with pytest.raises(DomainError, match="already shipped"):
        O.edit(conn, office(conn), oid, "product", "Lord armchair", reason="x")


def test_variant_change_after_work_asks_keep_or_send_back(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "uph")
    m = office(conn)
    res = O.edit(conn, m, oid, "variant", "velvet beige · oak")
    assert res["needs"] == "rework"
    res = O.edit(conn, m, oid, "variant", "velvet beige · oak", keep_work=True,
                 reason="Only the legs change")
    assert res["changed"] and domain.current_stage(conn, u)["code"] == "asm"
    res = O.edit(conn, m, oid, "variant", "boucle milk · oak", rework_stage_id=stage(conn, "cut"),
                 payer="customer", reason="Customer changed fabric")
    assert res["changed"] and domain.current_stage(conn, u)["code"] == "cut"


# --- rework -----------------------------------------------------------------

def test_rework_sends_back_with_visible_undo_records(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "asm")
    before = conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0]
    res = O.rework(conn, office(conn), [u], stage(conn, "sew"), reason="Seam open",
                   cause="sewing_error", payer="shop")
    assert res["ok"] and domain.current_stage(conn, u)["code"] == "sew"
    undo = conn.execute("SELECT method FROM work_events WHERE id > ? ORDER BY id",
                        (before,)).fetchall()
    assert [r["method"] for r in undo] == ["rework:shop"] * 3  # asm, uph, sew
    assert O.story(conn, oid)["reworks"][0]["payer"] == "shop"


def test_shop_pays_everyone_keeps_their_money(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "uph")
    sewer, uph = worker_at(conn, "sew"), worker_at(conn, "uph")
    s0, u0 = money_of(conn, sewer), money_of(conn, uph)
    O.rework(conn, office(conn), [u], stage(conn, "sew"), reason="Wrong thread", payer="shop")
    assert (money_of(conn, sewer), money_of(conn, uph)) == (s0, u0)
    domain.mark_done(conn, sewer, u)  # the redo is paid again
    assert money_of(conn, sewer) == s0 + domain.rate(conn, u, stage(conn, "sew"))


def test_worker_pays_only_the_one_who_made_the_mistake(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "uph")
    sewer, uph = worker_at(conn, "sew"), worker_at(conn, "uph")
    s0, u0 = money_of(conn, sewer), money_of(conn, uph)
    rate = domain.rate(conn, u, stage(conn, "sew"))
    O.rework(conn, office(conn), [u], stage(conn, "sew"), reason="Seam crooked",
             cause="sewing_error", payer="worker")
    assert money_of(conn, sewer) == s0 - rate  # the first sewing is not paid
    assert money_of(conn, uph) == u0  # upholstery did nothing wrong
    domain.mark_done(conn, sewer, u)
    assert money_of(conn, sewer) == s0  # paid once for sewing it twice
    assert O.story(conn, oid)["reworks"][0]["blamed"] is not None


def test_rework_on_paid_out_work_cannot_take_money_back(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "sew")
    ev = domain.done_stage_ids(conn, u)[stage(conn, "sew")]
    pid = conn.execute("INSERT INTO payouts (person_id, created_at, created_by) "
                       "VALUES (?, 'x', ?)", (worker_at(conn, "sew"), office(conn))).lastrowid
    conn.execute("INSERT INTO payout_lines VALUES (?, ?, 9)", (pid, ev))
    with pytest.raises(DomainError, match="paid out"):
        O.rework(conn, office(conn), [u], stage(conn, "sew"), reason="x", payer="worker")
    assert O.rework(conn, office(conn), [u], stage(conn, "sew"), reason="x", payer="shop")["ok"]


def test_rework_refusals_say_why(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "cut")
    m = office(conn)
    with pytest.raises(DomainError, match="not passed Upholstery"):
        O.rework(conn, m, [u], stage(conn, "uph"), reason="x", payer="shop")
    with pytest.raises(DomainError, match="who pays"):
        O.rework(conn, m, [u], stage(conn, "cut"), reason="x", payer="nobody")
    with pytest.raises(DomainError, match="what is wrong"):
        O.rework(conn, m, [u], stage(conn, "cut"), reason=" ", payer="shop")


def test_rework_retry_records_once(conn):
    oid = new_order(conn, qty=2)
    us = units(conn, oid)
    for u in us:
        advance(conn, u, "sew")
    m = office(conn)
    a = O.rework(conn, m, us, stage(conn, "sew"), reason="x", payer="shop", idem_key="k1")
    b = O.rework(conn, m, us, stage(conn, "sew"), reason="x", payer="shop", idem_key="k1")
    assert not a["already"] and b["already"]
    assert conn.execute("SELECT COUNT(*) FROM reworks WHERE idem_key LIKE 'k1:%'").fetchone()[0] == 2


def test_worker_cannot_undo_what_a_rework_undid(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "sew")
    ev = domain.done_stage_ids(conn, u)[stage(conn, "sew")]
    O.rework(conn, office(conn), [u], stage(conn, "sew"), reason="x", payer="shop")
    with pytest.raises(DomainError, match="already undone"):
        domain.undo(conn, worker_at(conn, "sew"), ev)


# --- cancel, stock, offers --------------------------------------------------

def test_cancel_finished_goes_to_stock_unfinished_stops(conn):
    oid = new_order(conn, qty=3)
    u1, u2, u3 = units(conn, oid)
    advance(conn, u1, "pack")
    advance(conn, u2, "sew")
    with pytest.raises(DomainError, match="why"):
        O.cancel(conn, office(conn), oid, "")
    res = O.cancel(conn, office(conn), oid, "Customer changed their mind")
    assert res["ok"]
    stock = domain._unit_order(conn, u1)
    assert stock["customer_id"] is None and not stock["cancelled"]
    assert domain.order_status(conn, oid) == "cancelled"
    assert set(units(conn, oid)) == {u2, u3}
    with pytest.raises(DomainError, match="cancelled"):
        domain.mark_done(conn, worker_at(conn, "uph"), u2)
    assert O.cancel(conn, office(conn), oid, "again")["already"]
    assert "Cancelled by" in O.extra(conn, stock["id"])["note"]
    assert qty_matches_units(conn) == []


def test_cancel_keeps_shipped_items_shipped(conn):
    oid = new_order(conn, qty=2)
    u1, u2 = units(conn, oid)
    advance(conn, u1, "pack")
    domain.ship(conn, office(conn), 1, [u1], "paid")
    O.cancel(conn, office(conn), oid, "Second one not needed")
    assert domain._unit_order(conn, u1)["id"] == oid
    assert domain.order_status(conn, oid) == "shipped"
    assert domain._unit_order(conn, u2)["cancelled"] == 1


def test_cancelled_finished_item_is_offered_to_the_next_similar_order(conn):
    a = new_order(conn, customer=1)
    (u,) = units(conn, a)
    advance(conn, u, "pack")
    b = new_order(conn, customer=2, qty=2)
    res = O.cancel(conn, office(conn), a, "Customer cancelled")
    offer = next(x for x in res["offers"] if x["target_id"] == b)
    assert offer["stock_unit_id"] == u and offer["finished"] and offer["match"] == "exact"
    assert any(x["stock_unit_id"] == u for x in O.story(conn, b)["offers"])
    out = O.take_from_stock(conn, office(conn), b, [u])
    assert out["ok"]
    assert domain._unit_order(conn, u)["id"] == b and len(units(conn, b)) == 2
    assert domain.current_stage(conn, u) is None  # its finished work counts here
    assert qty_matches_units(conn) == []


def test_different_fabric_is_not_offered(conn):
    a = new_order(conn, customer=1)
    (u,) = units(conn, a)
    advance(conn, u, "pack")
    b = new_order(conn, customer=2, variant="linen sand · oak")
    O.cancel(conn, office(conn), a, "x")
    assert not [x for x in O.stock_offers(conn) if x["target_id"] == b and x["stock_unit_id"] == u]
    with pytest.raises(DomainError, match="needs"):
        O.take_from_stock(conn, office(conn), b, [u])


def test_comments_are_kept(conn):
    oid = new_order(conn)
    O.comment(conn, office(conn), oid, "Fabric photo sent to the customer")
    with pytest.raises(DomainError):
        O.comment(conn, office(conn), oid, "  ")
    assert O.story(conn, oid)["comments"][0]["text"] == "Fabric photo sent to the customer"


def test_story_says_what_is_locked_and_why(conn):
    oid = new_order(conn)
    (u,) = units(conn, oid)
    advance(conn, u, "cut")
    locks = O.story(conn, oid)["locks"]
    assert "product" in locks and "due_date" not in locks


def test_seed_tells_the_three_stories(conn):
    assert conn.execute("SELECT COUNT(*) FROM reworks").fetchone()[0] >= 1
    assert conn.execute("SELECT COUNT(*) FROM order_changes WHERE field = 'cancel'").fetchone()[0]
    assert O.stock_offers(conn)
    assert qty_matches_units(conn) == []


# --- HTTP -------------------------------------------------------------------

def test_routes_answer_in_words_and_are_office_only(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORHAND_DB", str(tmp_path / "o.db"))
    from floorhand.main import app
    client = TestClient(app)
    oid = new_order(conn)
    assert client.post(f"/api/orders/{oid}/edit", json={"field": "note", "value": "x"}
                       ).status_code == 401
    client.post("/api/login", json={"person_id": worker_at(conn, "sew")})
    assert client.post(f"/api/orders/{oid}/edit", json={"field": "note", "value": "x"}
                       ).status_code == 403
    client.post("/api/login", json={"person_id": office(conn)})
    r = client.post(f"/api/orders/{oid}/edit", json={"field": "qty", "value": 2})
    assert r.status_code == 200 and "2 pc" in r.json()["message"]
    r = client.post(f"/api/orders/{oid}/edit", json={"field": "qty", "value": 0})
    assert r.status_code == 409 and "between 1 and 500" in r.json()["message"]
    assert client.get(f"/api/orders/{oid}/story").json()["changes"][0]["field"] == "qty"
