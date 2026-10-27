"""How workers mark work. Each rule here comes from running a real workshop ERP."""
import json
import random
import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, seed, speech
from floorhand.domain import DomainError
from floorhand.features import marking as mk
from floorhand.features.marking import Actor

TZ = ZoneInfo(seed.TZ)
TODAY = datetime.now(TZ).date()


class Clock:
    def __init__(self):
        self.t = datetime.combine(TODAY, time(10, 0), TZ)

    def add(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(mk, "_now", lambda conn: c.t)
    return c


@pytest.fixture
def conn(tmp_path, clock):
    c = db.connect(tmp_path / "t.db")
    seed.build(c, random.Random(5), today=TODAY)
    return c


def stage_id(conn, code):
    return conn.execute("SELECT id FROM stages WHERE code = ?", (code,)).fetchone()[0]


def worker_at(conn, code, n=0):
    return conn.execute("SELECT id FROM people WHERE stage_id = ? ORDER BY id LIMIT 1 OFFSET ?",
                        (stage_id(conn, code), n)).fetchone()[0]


def waiting_order(conn, code, qty=1, created_before="9999"):
    """An order with exactly `qty` pieces, all waiting at this stage."""
    sid = stage_id(conn, code)
    waiting = set(domain._waiting_units(conn)[sid])
    for o in conn.execute("SELECT id, number, qty FROM orders WHERE qty = ? AND cancelled = 0 "
                          "AND substr(created_at, 1, 10) <= ? ORDER BY number",
                          (qty, created_before)):
        units = [r[0] for r in conn.execute("SELECT id FROM units WHERE order_id = ? ORDER BY seq",
                                            (o["id"],))]
        if all(u in waiting for u in units):
            return o, units
    pytest.skip(f"no {qty}-piece order waiting at {code}")


def events(conn):
    return conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0]


# --- speech -------------------------------------------------------------------

@pytest.mark.parametrize("said,order,seq", [
    ("two hundred fourteen done", 214, None),
    ("214 готов", 214, None),
    ("двести четырнадцатый готов", 214, None),
    ("двести четырнадцать дробь два готов", 214, 2),
    ("#214/2 done", 214, 2),
    ("two fourteen ready", 214, None),
    ("order one oh five finished", 105, None),
    ("сто пятый готов", 105, None),
    ("one thousand two hundred fourteen", 1214, None),
])
def test_speech_reads_order_numbers_in_both_languages(said, order, seq):
    p = speech.parse(said)
    assert [(i.kind, i.order, i.seq) for i in p.intents] == [("mark", order, seq)]


def test_speech_counts_kinds_material_undo_other_and_negation():
    p = speech.parse("сделал три пуфа")
    assert (p.intents[0].kind, p.intents[0].qty, p.intents[0].product_kind) == ("mark_qty", 3, "pouf")
    assert speech.parse("made two armchairs").intents[0].qty == 2
    m = speech.parse("кончилась ткань беж").intents[0]
    assert (m.kind, m.text) == ("material_out", "ткань беж")
    assert speech.parse("ran out of beige velvet").intents[0].text == "beige velvet"
    assert speech.parse("отмени последнюю").intents[0].kind == "undo_last"
    o = speech.parse("шил мешки 40 минут").intents[0]
    assert (o.kind, o.minutes) == ("other_work", 40)
    assert speech.parse("214 не готов").negated
    assert speech.parse("214 not done yet").negated
    assert speech.parse("пятно на ткани").intents == []          # "пятно" is not "пять"
    assert [i.order for i in speech.parse("сто пятый и сто шестой готовы").intents] == [105, 106]


# --- codes ----------------------------------------------------------------------

def test_resolve_accepts_sheet_number_piece_and_qr(conn):
    o, units = waiting_order(conn, "cut", qty=2)
    n = o["number"]
    for code in (f"{n}-2", f"#{n}/2", f"№{n} / 2", f"order {n}-2"):
        assert mk.resolve(conn, code).unit_id == units[1]
    assert mk.resolve(conn, str(n)).unit_id is None              # whole-order sheet
    conn.execute("INSERT INTO unit_tokens VALUES ('Tok_abc123', ?)", (units[0],))
    assert mk.resolve(conn, "/u/Tok_abc123").unit_id == units[0]
    assert mk.resolve(conn, "https://shop.example/u/Tok_abc123?x=1").unit_id == units[0]
    one, (u1,) = waiting_order(conn, "cut", qty=1)
    assert mk.resolve(conn, str(one["number"])).unit_id == u1    # one piece: the piece itself
    with pytest.raises(DomainError, match="not in the system"):
        mk.resolve(conn, "/u/nope1234")
    with pytest.raises(DomainError, match="no piece 7"):
        mk.resolve(conn, f"{n}-7")
    with pytest.raises(DomainError, match="no order #99999"):
        mk.resolve(conn, "99999")
    with pytest.raises(DomainError, match="Cannot read"):
        mk.resolve(conn, "hello")


# --- marking --------------------------------------------------------------------

def test_typed_number_marks_and_says_money_and_today(conn):
    o, (u,) = waiting_order(conn, "uph")
    me = Actor(worker_at(conn, "uph"))
    r = mk.mark(conn, me, code=str(o["number"]), method="number")
    assert r["status"] == "recorded" and r["unit_id"] == u
    assert r["today"]["count"] >= 1 and "Recorded:" in r["message"]
    ev = conn.execute("SELECT method FROM work_events WHERE id = ?", (r["event_id"],)).fetchone()
    assert ev["method"] == "number"
    meta = conn.execute("SELECT * FROM mark_meta WHERE event_id = ?", (r["event_id"],)).fetchone()
    assert meta["time_quality"] == "live"


def test_repeat_scan_never_undoes(conn, clock):
    o, (u,) = waiting_order(conn, "uph")
    me = Actor(worker_at(conn, "uph"))
    first = mk.mark(conn, me, code=str(o["number"]))
    n = events(conn)
    clock.add(minutes=5)
    again = mk.mark(conn, me, code=str(o["number"]))
    assert again["status"] == "already" and again["event_id"] == first["event_id"]
    assert "Nothing changed" in again["message"]
    assert events(conn) == n
    assert my_stage_done(conn, u, "uph")


def my_stage_done(conn, unit_id, code):
    return stage_id(conn, code) in domain.done_stage_ids(conn, unit_id)


def test_scanner_double_fire_counts_once_even_on_a_multi_piece_sheet(conn, clock):
    o, units = waiting_order(conn, "sew", qty=2)
    me = Actor(worker_at(conn, "sew"))
    mk.mark(conn, me, code=str(o["number"]))
    n = events(conn)
    clock.add(seconds=1)
    r = mk.mark(conn, me, code=str(o["number"]))
    assert r["status"] == "already" and "counted once" in r["message"]
    assert events(conn) == n
    assert not my_stage_done(conn, units[1], "sew")


def test_quick_repeat_of_order_sheet_asks_before_next_piece(conn, clock):
    o, units = waiting_order(conn, "sew", qty=2)
    me = Actor(worker_at(conn, "sew"))
    mk.mark(conn, me, code=str(o["number"]))
    clock.add(seconds=40)
    ask = mk.mark(conn, me, code=str(o["number"]))
    assert ask["status"] == "ask" and ask["confirm"] == {"confirm_next": True}
    assert not my_stage_done(conn, units[1], "sew")
    yes = mk.mark(conn, me, code=str(o["number"]), confirm_next=True)
    assert yes["status"] == "recorded" and yes["unit_id"] == units[1]
    clock.add(seconds=40)
    done = mk.mark(conn, me, code=str(o["number"]))
    assert done["status"] == "already" and "All 2 pcs" in done["message"]


def test_slow_repeat_of_order_sheet_marks_next_piece(conn, clock):
    o, units = waiting_order(conn, "sew", qty=2)
    me = Actor(worker_at(conn, "sew"))
    mk.mark(conn, me, code=str(o["number"]))
    clock.add(minutes=30)
    r = mk.mark(conn, me, code=str(o["number"]))
    assert r["status"] == "recorded" and r["unit_id"] == units[1]


def test_same_idem_key_on_order_sheet_records_once(conn, clock):
    o, units = waiting_order(conn, "sew", qty=2)
    me = Actor(worker_at(conn, "sew"))
    a = mk.mark(conn, me, code=str(o["number"]), idem_key="tap-9")
    clock.add(minutes=30)
    b = mk.mark(conn, me, code=str(o["number"]), idem_key="tap-9")
    assert b["status"] == "already" and b["event_id"] == a["event_id"]
    assert not my_stage_done(conn, units[1], "sew")


def test_already_marked_by_someone_else_names_them(conn):
    o, (u,) = waiting_order(conn, "uph")
    a, b = worker_at(conn, "uph", 0), worker_at(conn, "uph", 1)
    mk.mark(conn, Actor(a), unit_id=u)
    r = mk.mark(conn, Actor(b), unit_id=u)
    name = domain.person(conn, a)["name"]
    assert r["status"] == "already" and not r["mine"] and f"by {name}" in r["message"]


def test_missing_rate_records_says_pending_and_tells_the_owner_once(conn):
    bunny = conn.execute("SELECT id FROM products WHERE name LIKE 'Kids%'").fetchone()[0]
    oid = domain.create_order(conn, product_id=bunny, qty=2, customer_id=None, due_date=None,
                              variant="", source="manual")
    units = [r[0] for r in conn.execute("SELECT id FROM units WHERE order_id = ?", (oid,))]
    sids = [stage_id(conn, c) for c in ("cut", "sew")]
    for u in units:
        for code in ("cut", "sew"):
            domain.mark_done(conn, worker_at(conn, code), u)
    me = Actor(worker_at(conn, "uph"))
    r = mk.mark(conn, me, unit_id=units[0])
    assert r["status"] == "recorded" and r["amount"] is None and r["price_pending"]
    assert "price pending" in r["message"] and "$0" not in r["message"]
    mk.mark(conn, me, unit_id=units[1])
    sig = conn.execute("SELECT COUNT(*) FROM shop_signals WHERE kind = 'rate_missing'").fetchone()[0]
    assert sig == 1 and sids


def test_shared_tablet_mark_shows_no_money_and_names_the_person(conn):
    o, (u,) = waiting_order(conn, "uph")
    pid = worker_at(conn, "uph")
    r = mk.mark(conn, Actor(pid, show_money=False, tablet="tab"), unit_id=u)
    assert r["amount"] is None and r["today"]["money"] is None
    assert "$" not in r["message"]
    assert domain.person(conn, pid)["name"] in r["message"]


# --- dates ------------------------------------------------------------------------

def test_phone_time_puts_a_late_mark_on_the_day_it_was_tapped(conn, clock):
    o, (u,) = waiting_order(conn, "uph", created_before=(TODAY - timedelta(days=1)).isoformat())
    me = Actor(worker_at(conn, "uph"))
    tapped = (clock.t - timedelta(days=1)).isoformat()
    r = mk.mark(conn, me, unit_id=u, created_at=tapped, idem_key="q1")
    assert r["work_date"] == (TODAY - timedelta(days=1)).isoformat()
    assert "the day you tapped it" in r["message"]
    late = mk.review(conn)["late_marks"]
    assert any(x["event_id"] == r["event_id"] for x in late)


def test_phone_time_too_old_or_in_future_still_records_for_today(conn, clock):
    me = Actor(worker_at(conn, "uph"))
    o1, (u1,) = waiting_order(conn, "uph")
    old = mk.mark(conn, me, unit_id=u1, created_at=(clock.t - timedelta(days=5)).isoformat())
    assert old["status"] == "recorded" and old["work_date"] == TODAY.isoformat()
    assert "more than 3 days ago" in old["message"]
    o2, (u2,) = waiting_order(conn, "uph")
    fut = mk.mark(conn, me, unit_id=u2, created_at=(clock.t + timedelta(hours=3)).isoformat())
    assert fut["work_date"] == TODAY.isoformat() and "clock is ahead" in fut["message"]
    q = conn.execute("SELECT time_quality FROM mark_meta WHERE event_id = ?",
                     (fut["event_id"],)).fetchone()[0]
    assert q == "adjusted"


def test_missed_piece_can_be_added_for_its_day_within_the_window(conn, clock):
    me = Actor(worker_at(conn, "uph"))
    d = (TODAY - timedelta(days=2)).isoformat()
    o, (u,) = waiting_order(conn, "uph", created_before=d)
    r = mk.mark(conn, me, unit_id=u, for_date=d)
    assert r["work_date"] == d
    with pytest.raises(DomainError, match="last 3 days"):
        mk.work_day(conn, me.person_id, for_date=(TODAY - timedelta(days=4)).isoformat())
    with pytest.raises(DomainError, match="not come yet"):
        mk.work_day(conn, me.person_id, for_date=(TODAY + timedelta(days=1)).isoformat())


def test_work_is_never_dated_before_its_order_existed(conn, clock):
    me = Actor(worker_at(conn, "cut"))
    pid = conn.execute("SELECT id FROM products LIMIT 1").fetchone()[0]
    oid = domain.create_order(conn, product_id=pid, qty=1, customer_id=None, due_date=None,
                              variant="", source="manual")
    u = conn.execute("SELECT id FROM units WHERE order_id = ?", (oid,)).fetchone()[0]
    r = mk.mark(conn, me, unit_id=u, for_date=(TODAY - timedelta(days=2)).isoformat())
    created = conn.execute("SELECT created_at FROM orders WHERE id = ?", (oid,)).fetchone()[0]
    assert r["work_date"] == created[:10] and "did not exist" in r["message"]


def test_late_piece_for_a_paid_day_is_kept_and_paid_next_time(conn, clock):
    me = Actor(worker_at(conn, "uph"))
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    conn.execute("INSERT INTO payouts (person_id, created_at, created_by) VALUES (?, ?, ?)",
                 (me.person_id, clock.t.isoformat(), manager))
    o, (u,) = waiting_order(conn, "uph", created_before=(TODAY - timedelta(days=1)).isoformat())
    r = mk.mark(conn, me, unit_id=u, for_date=(TODAY - timedelta(days=1)).isoformat())
    assert r["status"] == "recorded" and "next payout" in r["message"]
    assert conn.execute("SELECT 1 FROM payout_lines WHERE event_id = ?",
                        (r["event_id"],)).fetchone() is None


# --- offline queue ------------------------------------------------------------------

def test_sync_batch_answers_each_mark_and_does_not_cache_refusals(conn, clock):
    me_id = worker_at(conn, "sew")
    sess = mk.Session(person_id=me_id, role="worker", stage_id=stage_id(conn, "sew"), tablet=False)
    o, (u,) = waiting_order(conn, "sew")
    early, (eu,) = waiting_order(conn, "cut")         # not at sewing yet
    t = (clock.t - timedelta(hours=2)).isoformat()
    res = mk.sync(conn, [{"idem_key": "k1", "unit_id": u, "created_at": t},
                         {"idem_key": "k1", "unit_id": u, "created_at": t},
                         {"idem_key": "k2", "unit_id": eu, "created_at": t},
                         {"unit_id": u}], session=sess, tablet=None)
    st = [x["status"] for x in res["results"]]
    assert st == ["recorded", "already", "refused", "refused"]
    assert "still at Cutting" in res["results"][2]["message"]
    domain.mark_done(conn, worker_at(conn, "cut"), eu)          # cutting catches up
    again = mk.sync(conn, [{"idem_key": "k2", "unit_id": eu, "created_at": t}],
                    session=sess, tablet=None)
    assert again["results"][0]["status"] == "recorded"


def test_sync_quick_repeat_of_order_sheet_waits_for_the_person(conn, clock):
    me_id = worker_at(conn, "sew")
    sess = mk.Session(person_id=me_id, role="worker", stage_id=stage_id(conn, "sew"), tablet=False)
    o, units = waiting_order(conn, "sew", qty=2)
    t1, t2 = clock.t - timedelta(minutes=3), clock.t - timedelta(minutes=2)
    res = mk.sync(conn, [{"idem_key": "a", "code": str(o["number"]), "created_at": t1.isoformat()},
                         {"idem_key": "b", "code": str(o["number"]), "created_at": t2.isoformat()}],
                  session=sess, tablet=None)
    assert [x["status"] for x in res["results"]] == ["recorded", "refused"]
    assert res["results"][1]["needs_confirm"]
    ok = mk.sync(conn, [{"idem_key": "b", "code": str(o["number"]), "confirm_next": True,
                         "created_at": t2.isoformat()}], session=sess, tablet=None)
    assert ok["results"][0]["status"] == "recorded"


def test_tablet_queue_survives_sign_out(conn, clock):
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    token = mk.setup_tablet(conn, manager, "Upholstery tablet", stage_id(conn, "uph"))
    tab = mk._tablet(conn, token)
    pid, other = worker_at(conn, "uph", 0), worker_at(conn, "uph", 1)
    mk.tablet_act(conn, tab, pid)
    tapped = clock.t + timedelta(seconds=30)
    clock.add(minutes=10)                                       # idle: signed out
    assert mk.tablet_state(conn, mk._tablet(conn, token))["acting"] is None
    o, (u,) = waiting_order(conn, "uph")
    res = mk.sync(conn, [{"idem_key": "t1", "unit_id": u, "person_id": pid,
                          "created_at": tapped.isoformat()},
                         {"idem_key": "t2", "unit_id": u, "person_id": other,
                          "created_at": tapped.isoformat()}],
                  session=None, tablet=mk._tablet(conn, token))
    assert [x["status"] for x in res["results"]] == ["recorded", "refused"]
    assert res["results"][0]["amount"] is None
    ev = conn.execute("SELECT person_id FROM work_events WHERE idem_key = 't1'").fetchone()
    assert ev["person_id"] == pid


# --- voice and photo -------------------------------------------------------------------

def test_voice_proposes_and_only_yes_marks_once(conn):
    o, (u,) = waiting_order(conn, "uph")
    me = Actor(worker_at(conn, "uph"))
    n = events(conn)
    p = mk.propose(conn, me, f"{o['number']} готов")
    assert p["status"] == "proposal" and p["items"][0]["unit_id"] == u
    assert p["message"].startswith("Understood as:") and "Upholstery" in p["message"]
    assert events(conn) == n                                   # nothing marked yet
    row = conn.execute("SELECT status, kind FROM agent_actions WHERE id = ?", (p["id"],)).fetchone()
    assert tuple(row) == ("proposed", "marking")
    yes = mk.decide(conn, me, p["id"], True)
    assert yes["status"] == "applied" and events(conn) == n + 1
    ev = conn.execute("SELECT method FROM work_events ORDER BY id DESC LIMIT 1").fetchone()
    assert ev["method"] == "voice"
    again = mk.decide(conn, me, p["id"], True)
    assert again["status"] == "already" and events(conn) == n + 1


def test_voice_no_records_nothing_and_proposal_belongs_to_its_person(conn):
    o, (u,) = waiting_order(conn, "uph")
    me, other = Actor(worker_at(conn, "uph", 0)), Actor(worker_at(conn, "uph", 1))
    p = mk.propose(conn, me, f"{o['number']} done")
    with pytest.raises(DomainError, match="someone else"):
        mk.decide(conn, other, p["id"], True)
    n = events(conn)
    assert mk.decide(conn, me, p["id"], False)["status"] == "rejected"
    assert mk.decide(conn, me, p["id"], True)["status"] == "already"
    assert events(conn) == n


def test_old_proposal_must_be_said_again(conn, clock):
    o, (u,) = waiting_order(conn, "uph")
    me = Actor(worker_at(conn, "uph"))
    p = mk.propose(conn, me, f"{o['number']} done")
    clock.add(minutes=45)
    with pytest.raises(DomainError, match="too long ago"):
        mk.decide(conn, me, p["id"], True)


def test_voice_count_of_a_kind_picks_waiting_pieces(conn):
    me = Actor(worker_at(conn, "uph"))
    sid = stage_id(conn, "uph")
    poufs = [u for u in domain._waiting_units(conn)[sid] if conn.execute(
        "SELECT 1 FROM units u JOIN orders o ON o.id = u.order_id JOIN products p "
        "ON p.id = o.product_id WHERE u.id = ? AND p.kind = 'pouf'", (u,)).fetchone()]
    if len(poufs) < 2:
        pytest.skip("not enough poufs waiting")
    p = mk.propose(conn, me, "сделал два пуфа")
    marks = [i for i in p["items"] if i["type"] == "mark"]
    assert len(marks) == 2 and all(i["unit_id"] in poufs for i in marks)
    assert len({i["unit_id"] for i in marks}) == 2
    mine = {r[0] for r in conn.execute("SELECT unit_id FROM assignments WHERE person_id = ? AND "
                                       "stage_id = ?", (me.person_id, sid))}
    if mine & set(poufs):
        assert marks[0]["unit_id"] in mine                     # assigned to me first


def test_voice_without_a_number_or_negated_proposes_nothing(conn):
    me = Actor(worker_at(conn, "uph"))
    before = conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0]
    assert mk.propose(conn, me, "hello there")["status"] == "nothing"
    neg = mk.propose(conn, me, "214 не готов")
    assert neg["status"] == "nothing" and "not done" in neg["message"]
    assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == before


def test_voice_material_out_reaches_the_office_once(conn):
    me = Actor(worker_at(conn, "uph"))
    p = mk.propose(conn, me, "кончился поролон 40")
    assert p["items"][0]["type"] == "signal"
    mk.decide(conn, me, p["id"], True)
    assert any(s["text"] == "поролон 40" for s in mk.review(conn)["signals"])
    assert mk.material_signal(conn, me, "Поролон 40")["status"] == "already"


def test_voice_and_photo_without_meta_say_what_to_do(conn, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    me = Actor(worker_at(conn, "uph"))
    with pytest.raises(DomainError, match="phone's own recognition"):
        mk.voice_audio(conn, me, b"\x00" * 100, "audio/webm", None)
    with pytest.raises(DomainError, match="needs the Meta model"):
        mk.photo(conn, me, b"\xff\xd8" * 100, "image/jpeg", None)
    assert conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0


def test_photo_reading_goes_through_a_proposal(conn, monkeypatch):
    o, (u,) = waiting_order(conn, "uph")
    me = Actor(worker_at(conn, "uph"))
    reply = {"output_text": json.dumps({"numbers": [{"order": o["number"], "seq": None,
                                                     "quote": str(o["number"])}]})}
    monkeypatch.setattr(mk.models, "respond", lambda *a, **k: reply)
    n = events(conn)
    p = mk.photo(conn, me, b"\xff\xd8fake", "image/jpeg", None)
    assert p["status"] == "proposal" and p["items"][0]["unit_id"] == u
    assert events(conn) == n
    mk.decide(conn, me, p["id"], True)
    assert conn.execute("SELECT method FROM work_events ORDER BY id DESC LIMIT 1").fetchone()[0] \
        == "photo"


def test_model_parse_is_grounded_in_what_was_said(conn, monkeypatch):
    o, (u,) = waiting_order(conn, "uph")
    me = Actor(worker_at(conn, "uph"))
    reply = {"output_text": json.dumps({"intents": [
        {"kind": "mark", "order": o["number"], "quote": "the big one by the window"},
        {"kind": "mark", "order": 99999, "quote": "something never said"}]})}
    monkeypatch.setattr(mk.models, "respond", lambda *a, **k: reply)
    p = mk.propose(conn, me, "finished the big one by the window")
    assert [i.get("unit_id") for i in p["items"] if i["type"] == "mark"] == [u]


# --- other work ------------------------------------------------------------------------

def preset(conn, name):
    return conn.execute("SELECT id FROM claim_presets WHERE name_en = ?", (name,)).fetchone()[0]


def test_other_work_counts_only_after_approval_by_work_date(conn):
    me = Actor(worker_at(conn, "sew"))
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    day = TODAY.isoformat()
    base = mk.claims_money(conn, me.person_id, day, day)
    r = mk.create_claim(conn, me, preset_id=preset(conn, "Sewed bags"), qty=4, idem_key="c1")
    assert r["claim"]["asked_amount"] == 8 and "after approval" in r["message"]
    assert mk.create_claim(conn, me, preset_id=preset(conn, "Sewed bags"), qty=4,
                           idem_key="c1")["status"] == "already"
    assert mk.claims_money(conn, me.person_id, day, day) == base
    cid = r["claim"]["id"]
    with pytest.raises(DomainError, match="own claim"):
        mk.decide_claim(conn, me.person_id, cid, True)
    ok = mk.decide_claim(conn, manager, cid, True)
    assert ok["status"] == "approved"
    assert mk.decide_claim(conn, manager, cid, False, note="no")["status"] == "already"
    assert mk.claims_money(conn, me.person_id, day, day) == base + 8


def test_reject_needs_a_reason_and_stays_visible(conn):
    me = Actor(worker_at(conn, "sew"))
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    cid = mk.create_claim(conn, me, description="Fixed the sewing table", minutes=30)["claim"]["id"]
    with pytest.raises(DomainError, match="why"):
        mk.decide_claim(conn, manager, cid, False)
    mk.decide_claim(conn, manager, cid, False, note="That is part of the job")
    row = conn.execute("SELECT status, decision_note FROM work_claims WHERE id = ?",
                       (cid,)).fetchone()
    assert tuple(row) == ("rejected", "That is part of the job")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM work_claims WHERE id = ?", (cid,))


def test_no_hourly_rate_is_not_zero(conn):
    conn.execute("DELETE FROM settings WHERE key = 'hourly_rate'")
    me = Actor(worker_at(conn, "sew"))
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    r = mk.create_claim(conn, me, description="Fitting a cover", minutes=45)
    assert r["claim"]["asked_amount"] is None
    with pytest.raises(DomainError, match="type the amount"):
        mk.decide_claim(conn, manager, r["claim"]["id"], True)
    assert mk.decide_claim(conn, manager, r["claim"]["id"], True, amount=11)["status"] == "approved"


def test_claim_limits_tablet_and_order_hint(conn):
    me = Actor(worker_at(conn, "uph"))
    with pytest.raises(DomainError, match="minutes"):
        mk.create_claim(conn, me, description="Something done")
    with pytest.raises(DomainError, match="from 1 to"):
        mk.create_claim(conn, me, description="Big job", amount=5000)
    with pytest.raises(DomainError, match="shared tablet"):
        mk.create_claim(conn, Actor(me.person_id, show_money=False), description="Bags", amount=5)
    mk.create_claim(conn, me, description="Cleaning the floor", minutes=500)
    with pytest.raises(DomainError, match="over 10 hours"):
        mk.create_claim(conn, me, description="More cleaning", minutes=200)
    num = conn.execute("SELECT number FROM orders WHERE number >= 100 ORDER BY id LIMIT 1").fetchone()[0]
    h = mk.create_claim(conn, me, description=f"Armchair {num} no rate", minutes=30)
    assert h["hint_order"] == num and "mark it as work" in h["message"]


def test_paid_claim_is_frozen(conn):
    me = Actor(worker_at(conn, "sew"))
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    cid = mk.create_claim(conn, me, preset_id=preset(conn, "Seat cushion"), qty=2)["claim"]["id"]
    mk.decide_claim(conn, manager, cid, True)
    pay = conn.execute("INSERT INTO payouts (person_id, created_at, created_by) VALUES (?, ?, ?)",
                       (me.person_id, TODAY.isoformat(), manager)).lastrowid
    assert mk.link_claims_to_payout(conn, pay, me.person_id, TODAY.isoformat()) >= 6
    with pytest.raises(sqlite3.DatabaseError, match="frozen"):
        conn.execute("UPDATE work_claims SET approved_amount = 1 WHERE id = ?", (cid,))
    with pytest.raises(DomainError, match="already decided"):
        mk.withdraw_claim(conn, me, cid)


def test_withdraw_pending_claim(conn):
    me = Actor(worker_at(conn, "sew"))
    cid = mk.create_claim(conn, me, description="Bags by mistake", minutes=10)["claim"]["id"]
    assert mk.withdraw_claim(conn, me, cid)["status"] == "withdrawn"
    assert mk.withdraw_claim(conn, me, cid)["status"] == "already"


# --- end of day ----------------------------------------------------------------------------

def test_day_check_from_four_pm_with_confirm_and_change(conn, clock):
    me = Actor(worker_at(conn, "uph", 2))
    o, (u,) = waiting_order(conn, "uph")
    mk.mark(conn, me, unit_id=u)
    chk = mk.day_check(conn, me)
    assert not chk["due"] and chk["pieces"] >= 1
    clock.t = clock.t.replace(hour=16, minute=5)
    chk = mk.day_check(conn, me)
    assert chk["due"] and chk["message"].startswith(f"Today recorded {chk['pieces']} pc")
    assert chk["message"].endswith("— all right?") and "$" in chk["message"]
    assert mk.confirm_day(conn, me)["status"] == "recorded"
    assert mk.day_check(conn, me)["confirmed"]
    o2, (u2,) = waiting_order(conn, "uph")
    mk.mark(conn, me, unit_id=u2)
    after = mk.day_check(conn, me)
    assert not after["confirmed"] and after["changed_since_confirmed"]
    tablet = mk.day_check(conn, Actor(me.person_id, show_money=False))
    assert tablet["money"] is None and "$" not in tablet["message"]
    assert all(i["amount"] is None for i in tablet["items"])


def test_review_lists_who_has_not_confirmed(conn, clock):
    me = Actor(worker_at(conn, "uph", 3))
    o, (u,) = waiting_order(conn, "uph")
    mk.mark(conn, me, unit_id=u)
    name = domain.person(conn, me.person_id)["name"]
    assert name in mk.review(conn)["day_checks"]["not_confirmed"]
    mk.confirm_day(conn, me)
    assert name not in mk.review(conn)["day_checks"]["not_confirmed"]


# --- HTTP: tablet and sessions --------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch, clock):
    path = tmp_path / "api.db"
    seed.build(db.connect(path), random.Random(7), today=TODAY)
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    from floorhand.main import app
    return TestClient(app), db.connect(path)


def test_marking_api_needs_a_session(client):
    c, _ = client
    assert c.post("/api/marking/mark", json={"code": "101"}).status_code == 401
    assert c.get("/api/marking/day-check").status_code == 401
    assert c.get("/api/marking/tablet").status_code == 401
    assert c.post("/api/marking/sync", json={"items": []}).status_code == 401


def test_http_voice_flow_and_refusal_in_words(client):
    c, conn = client
    pid = worker_at(conn, "uph")
    c.post("/api/login", json={"person_id": pid})
    o, (u,) = waiting_order(conn, "uph")
    p = c.post("/api/marking/voice/text", json={"transcript": f"{o['number']} done"}).json()
    assert p["status"] == "proposal"
    r = c.post(f"/api/marking/proposals/{p['id']}/decide", json={"yes": True}).json()
    assert r["status"] == "applied" and "Recorded" in r["message"]
    bad = c.post("/api/marking/mark", json={"code": "99999"})
    assert bad.status_code == 409 and "no order #99999" in bad.json()["message"]


def test_shared_tablet_signs_out_after_quiet_minutes(client, clock):
    c, conn = client
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    c.post("/api/login", json={"person_id": manager})
    r = c.post("/api/marking/tablet/setup", json={"name": "Upholstery",
                                                   "stage_id": stage_id(conn, "uph")})
    assert r.status_code == 200
    assert c.get("/api/floor").status_code == 401              # manager is signed out on it
    st = c.get("/api/marking/tablet").json()
    assert st["acting"] is None and st["people"]
    other_stage = worker_at(conn, "sew")
    assert c.post("/api/marking/tablet/act", json={"person_id": other_stage}).status_code == 409
    pid = st["people"][0]["id"]
    c.post("/api/marking/tablet/act", json={"person_id": pid})
    home = c.get("/api/work").json()
    assert home["show_money"] is False and home["today"]["money"] is None
    assert c.get("/api/money").status_code == 403
    clock.add(seconds=90)
    o, (u,) = waiting_order(conn, "uph")
    m = c.post("/api/marking/mark", json={"unit_id": u}).json()
    assert m["status"] == "recorded" and m["amount"] is None   # a tap keeps the person in
    clock.add(seconds=90)
    assert c.get("/api/work").status_code == 200
    clock.add(seconds=60)                                       # 150 s without a tap
    gone = c.get("/api/work")
    assert gone.status_code == 401 and "Tap your name" in gone.json()["detail"]
    shift = conn.execute("SELECT end_reason FROM tablet_shifts ORDER BY id DESC").fetchone()
    assert shift["end_reason"] == "idle"
    assert c.get("/api/marking/tablet").json()["acting"] is None


def test_tablet_signs_out_at_end_of_day(conn, clock):
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    token = mk.setup_tablet(conn, manager, "Floor", None)
    clock.t = clock.t.replace(hour=19, minute=59)
    mk.tablet_act(conn, mk._tablet(conn, token), worker_at(conn, "pack"))
    clock.t = clock.t.replace(hour=20, minute=0, second=30)
    assert mk.tablet_state(conn, mk._tablet(conn, token))["acting"] is None
    assert conn.execute("SELECT end_reason FROM tablet_shifts").fetchone()[0] == "end_of_day"


def test_answers_in_the_workers_language(conn):
    me = Actor(worker_at(conn, "uph"))
    conn.execute("UPDATE people SET lang = 'ru' WHERE id = ?", (me.person_id,))
    o, (u,) = waiting_order(conn, "uph")
    r = mk.mark(conn, me, code=str(o["number"]))
    assert r["message"].startswith("Записано:") and "Обивка" in r["message"]
    p = mk.propose(conn, me, "кончилась ткань беж")
    assert p["message"].startswith("Понял так:")
    with pytest.raises(DomainError, match="Заказа №99999 нет"):
        mk.mark(conn, me, code="99999")


def test_server_voice_audio_keeps_model_attribution_and_requires_yes(conn, monkeypatch):
    order, (unit,) = waiting_order(conn, "uph")
    actor = Actor(worker_at(conn, "uph"))
    before = events(conn)
    monkeypatch.setattr(mk.models, "transcribe", lambda *a, **k: f"{order['number']} done")
    result = mk.voice_audio(conn, actor, b"invented-audio", "audio/wav", None)
    assert result["status"] == "proposal" and result["items"][0]["unit_id"] == unit
    assert events(conn) == before
    row = conn.execute("SELECT model FROM agent_actions WHERE id = ?", (result["id"],)).fetchone()
    assert row["model"] == "muse-voice-transcribe-1.0+rules"
