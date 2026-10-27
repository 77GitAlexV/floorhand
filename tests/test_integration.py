"""Rules that cross feature modules (checked after merging the feature branches)."""
import random
from datetime import date

import pytest

from floorhand import db, domain, seed
from floorhand.domain import DomainError
from floorhand.features import marking as mk
from floorhand.features import paperwork as pw


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    seed.build(c, random.Random(11), today=date.today())
    yield c
    c.close()


def _waiting_at_first_stage(conn):
    first = conn.execute("SELECT id FROM stages ORDER BY seq LIMIT 1").fetchone()[0]
    uid = domain._waiting_units(conn)[first][0]
    worker = conn.execute("SELECT id FROM people WHERE stage_id = ? ORDER BY id LIMIT 1",
                          (first,)).fetchone()[0]
    return uid, worker


def _manager(conn):
    return conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]


def test_printed_sheet_code_marks_through_marking(conn):
    """The QR the paperwork feature prints is the code marking accepts."""
    uid, worker = _waiting_at_first_stage(conn)
    pw.print_sheets(conn, _manager(conn), scope="units", unit_ids=[uid])
    token = pw.ensure_codes(conn, [uid])[uid]
    for code in (f"/u/{token}/1", pw.format_code(token).lower()):
        assert mk.resolve(conn, code).unit_id == uid
    r = mk.mark(conn, mk.Actor(worker), code=f"https://shop.example/u/{token}/1", method="scan")
    assert r["status"] == "recorded" and r["unit_id"] == uid


def test_old_copy_still_marks_and_says_so(conn):
    uid, worker = _waiting_at_first_stage(conn)
    m = _manager(conn)
    pw.print_sheets(conn, m, scope="units", unit_ids=[uid])
    pw.print_sheets(conn, m, scope="units", unit_ids=[uid], reason="lost")
    token = pw.ensure_codes(conn, [uid])[uid]
    r = mk.mark(conn, mk.Actor(worker), code=f"/u/{token}/1", method="scan")
    assert r["status"] == "recorded"
    assert "copy 2" in r["message"] and r["warnings"]


def test_sheet_of_cancelled_order_is_refused_in_words(conn):
    uid, worker = _waiting_at_first_stage(conn)
    pw.print_sheets(conn, _manager(conn), scope="units", unit_ids=[uid])
    oid = conn.execute("SELECT order_id FROM units WHERE id = ?", (uid,)).fetchone()[0]
    conn.execute("UPDATE orders SET cancelled = 1 WHERE id = ?", (oid,))
    conn.commit()
    token = pw.ensure_codes(conn, [uid])[uid]
    before = conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0]
    with pytest.raises(DomainError, match="cancelled"):
        mk.mark(conn, mk.Actor(worker), code=f"/u/{token}", method="scan")
    assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] == before


def test_one_outbox_for_owner_cards_and_paperwork(conn):
    """Rule 8: a purchase request from an owner card and a delivery note from paperwork
    land in the same outbox; a row without an e-mail address is never sent."""
    from floorhand.features import owner
    boss = conn.execute("SELECT id FROM people WHERE role = 'owner'").fetchone()[0]
    feed = owner.today_feed(conn, boss)
    card = next(c for c in feed["cards"] if c["kind"] == "shortage")
    owner.apply(conn, boss, card["id"], owner.DecideIn(pick=card["options"][0]["id"]))
    rows = pw.outbox_list(conn)
    purchase = next(r for r in rows if r["kind"] == "purchase")
    assert purchase["to_addr"] is None and purchase["action_id"] == card["id"]
    assert any(r["kind"] == "delivery_note" for r in rows)       # paperwork's seed drafts
    r = pw.send_outbox(conn, _manager(conn), purchase["id"])
    assert not r["ok"] and "No e-mail address" in r["message"]
    assert conn.execute("SELECT status FROM outbox WHERE id = ?", (purchase["id"],)).fetchone()[0] == "draft"


@pytest.mark.parametrize("payer,keeps", [("shop", True), ("worker", False)])
def test_rework_money_is_the_same_everywhere(conn, payer, keeps):
    """A rework the shop pays for keeps the worker's pay in every money view (the
    worker's today, the payroll answer in the Ask box); one the worker pays removes it."""
    from floorhand.features import ask, orders
    uid, worker = _waiting_at_first_stage(conn)
    stage = conn.execute("SELECT stage_id FROM people WHERE id = ?", (worker,)).fetchone()[0]
    if domain.rate(conn, uid, stage) is None:
        pytest.skip("no rate for this piece")
    mk.mark(conn, mk.Actor(worker), unit_id=uid, method="button")
    before = mk._today_totals(conn, worker, True)["money"]
    due_before = ask.t_payroll_due(conn)
    orders.rework(conn, _manager(conn), [uid], stage, reason="seam", cause="sewing_error",
                  payer=payer)
    after = mk._today_totals(conn, worker, True)["money"]
    home = domain.worker_home(conn, worker, show_money=True)["today"]["money"]
    assert (after == before) is keeps and home == after
    assert (ask.t_payroll_due(conn) == due_before) is keeps
