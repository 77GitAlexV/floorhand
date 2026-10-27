"""Rules carried over from running a real workshop ERP. Each one is a test."""
import random
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, seed
from floorhand.domain import DomainError


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    seed.build(c, random.Random(1), today=date.today())
    return c


def waiting_unit(conn, stage_code):
    stage_id = conn.execute("SELECT id FROM stages WHERE code = ?", (stage_code,)).fetchone()[0]
    uid = domain._waiting_units(conn)[stage_id][0]
    pid = conn.execute("SELECT id FROM people WHERE stage_id = ? LIMIT 1", (stage_id,)).fetchone()[0]
    return uid, pid


def test_seed_has_a_living_shop(conn):
    # feature seeds may add a little history of their own on top of the base shop
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] >= seed.N_ORDERS
    assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] > 500
    floor = domain.floor_overview(conn)
    assert [s["name_en"] for s in floor if s["bottleneck"]] == ["Upholstery"]


def test_work_events_are_append_only(conn):
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE work_events SET person_id = 1")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM work_events")


def test_repeat_mark_never_undoes(conn):
    uid, pid = waiting_unit(conn, "uph")
    first = domain.mark_done(conn, pid, uid)
    second = domain.mark_done(conn, pid, uid)
    assert first.ok and not first.already
    assert second.already and second.event_id == first.event_id
    assert "Nothing changed" in second.message


def test_same_idem_key_records_once(conn):
    uid, pid = waiting_unit(conn, "sew")
    before = conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0]
    a = domain.mark_done(conn, pid, uid, idem_key="tap-1")
    b = domain.mark_done(conn, pid, uid, idem_key="tap-1")
    assert a.event_id == b.event_id
    assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] == before + 1


def test_cannot_skip_a_stage(conn):
    uid, _ = waiting_unit(conn, "cut")
    asm = conn.execute("SELECT p.id FROM people p JOIN stages s ON s.id = p.stage_id "
                       "WHERE s.code = 'asm' LIMIT 1").fetchone()[0]
    with pytest.raises(DomainError, match="still at Cutting"):
        domain.mark_done(conn, asm, uid)


def test_undo_is_a_visible_record(conn):
    uid, pid = waiting_unit(conn, "uph")
    res = domain.mark_done(conn, pid, uid)
    domain.undo(conn, pid, res.event_id)
    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM work_events WHERE unit_id = ? AND stage_id = ? ORDER BY id",
        (uid, conn.execute("SELECT stage_id FROM people WHERE id = ?", (pid,)).fetchone()[0]))]
    assert kinds[-2:] == ["done", "undo"]
    assert domain.current_stage(conn, uid)["code"] == "uph"


def test_cannot_undo_after_next_stage(conn):
    uid, pid = waiting_unit(conn, "pack")
    prev = conn.execute("SELECT e.id, e.person_id FROM work_events e JOIN stages s "
                        "ON s.id = e.stage_id WHERE e.unit_id = ? AND s.code = 'asm'",
                        (uid,)).fetchone()
    domain.mark_done(conn, pid, uid)
    with pytest.raises(DomainError, match="already passed Packing"):
        domain.undo(conn, prev["person_id"], prev["id"])


def test_missing_rate_is_not_zero(conn):
    bunny = conn.execute("SELECT id FROM products WHERE name LIKE 'Kids%'").fetchone()[0]
    uph = conn.execute("SELECT id FROM stages WHERE code = 'uph'").fetchone()[0]
    assert conn.execute("SELECT 1 FROM rates WHERE product_id = ? AND stage_id = ?",
                        (bunny, uph)).fetchone() is None


def test_only_finished_units_ship_and_only_once(conn):
    uid, _ = waiting_unit(conn, "asm")
    order = domain._unit_order(conn, uid)
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    if order["customer_id"] is not None:
        with pytest.raises(DomainError, match="not ready"):
            domain.ship(conn, manager, order["customer_id"], [uid], "paid")
    shipped = conn.execute("SELECT sl.unit_id, s.customer_id FROM shipment_lines sl "
                           "JOIN shipments s ON s.id = sl.shipment_id LIMIT 1").fetchone()
    with pytest.raises(DomainError, match="already been shipped"):
        domain.ship(conn, manager, shipped["customer_id"], [shipped["unit_id"]], "paid")


def test_api_needs_session_and_tablet_hides_money(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    seed.build(db.connect(path), random.Random(2), today=date.today())
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    from floorhand.main import app

    client = TestClient(app)
    assert client.get("/api/work").status_code == 401
    assert client.get("/api/floor").status_code == 401

    c = db.connect(path)
    worker = c.execute("SELECT p.id FROM people p JOIN stages s ON s.id = p.stage_id "
                       "WHERE s.code = 'uph' LIMIT 1").fetchone()[0]
    client.post("/api/login", json={"person_id": worker, "tablet": True})
    home = client.get("/api/work").json()
    assert home["today"]["money"] is None
    assert all(card["rate"] is None for card in home["mine"] + home["free"])
    assert client.get("/api/money").status_code == 403
    assert client.get("/api/floor").status_code == 403

    client.post("/api/login", json={"person_id": worker, "tablet": False})
    assert client.get("/api/work").json()["today"]["money"] is not None


def test_order_status_is_derived_and_late_orders_surface(conn):
    r = domain.orders_list(conn, view="all")
    statuses = {o["status"] for o in r["orders"]}
    assert {"shipped", "in_work", "ready"} <= statuses
    for o in r["attention"]:
        assert o["late_days"] > 0 and o["status"] not in ("shipped", "ready")
    for o in r["orders"]:
        line = o["line"]
        assert sum(s["here"] for s in line) + o["finished"] == o["qty"]


def test_ship_flow_and_invoice_total(conn):
    board = domain.shipping_board(conn)
    cust = board["ready"][0]["customer_id"]
    panel = domain.customer_shipping(conn, cust)
    ready = [i["unit_id"] for i in panel["items"] if i["ready"]]
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    with pytest.raises(DomainError, match="twice"):
        domain.ship(conn, manager, cust, [ready[0], ready[0]], "paid")
    sid = domain.ship(conn, manager, cust, ready, "credit")
    inv = domain.invoice(conn, sid)
    assert inv["total"] == sum(i["price"] for i in panel["items"] if i["ready"])
    assert inv["owed"] == inv["total"]
    assert all(not i["ready"] for i in domain.customer_shipping(conn, cust)["items"])
    worker = conn.execute("SELECT id FROM people WHERE role = 'worker' LIMIT 1").fetchone()[0]
    with pytest.raises(DomainError, match="office"):
        domain.ship(conn, worker, cust, ready, "paid")
