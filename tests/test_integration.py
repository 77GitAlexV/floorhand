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
