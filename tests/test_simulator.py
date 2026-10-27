"""The living demo obeys the same rules as people and never steals a phone shift."""
import asyncio
import json
import queue
import random
import threading
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from floorhand import db, domain
from floorhand.features import simulator as sim
from floorhand.web import COOKIE, session


@pytest.fixture
def shop(tmp_path, monkeypatch):
    path = tmp_path / "sim.db"
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    monkeypatch.delenv("FLOORHAND_SIMULATE", raising=False)
    for key in ("SPEED", "ORDERS_PER_HOUR", "UNDO_CHANCE", "BREAK_CHANCE"):
        monkeypatch.delenv("FLOORHAND_SIM_" + key, raising=False)
    at = datetime(2026, 10, 27, 10, tzinfo=ZoneInfo("Asia/Bangkok"))
    monkeypatch.setattr(domain, "now", lambda conn: at)
    with closing(db.connect(path)) as conn:
        db.init(conn)
        conn.execute("INSERT INTO settings VALUES ('tz', 'Asia/Bangkok')")
        # The simulator's existing Demo variant is a declared fictional fabric.
        conn.execute("INSERT INTO settings VALUES ('fabrics', '[\"Demo\"]')")
        for i, en, ru in ((1, "Demo cutting", "Демо раскрой"),
                          (2, "Demo sewing", "Демо шитьё"),
                          (3, "Demo packing", "Демо упаковка")):
            conn.execute("INSERT INTO stages VALUES (?, ?, ?, ?, ?, 4)", (i, str(i), en, ru, i))
            for j in (1, 2):
                conn.execute("INSERT INTO people (id, name, role, stage_id) VALUES (?, ?, 'worker', ?)",
                             (i * 10 + j, f"DemoLumo{i}{j} Fictional", i))
        conn.execute("INSERT INTO people (id, name, role) VALUES (90, 'DemoNeri', 'manager')")
        conn.execute("INSERT INTO people (id, name, role) VALUES (99, 'DemoTavi', 'owner')")
        conn.execute("INSERT INTO products VALUES (1, 'Imaginary cloud stool', 'pouf', 17)")
        conn.execute("INSERT INTO customers VALUES (1, 'Imaginary Cloud Workshop', 'Demo town')")
        conn.commit()
        domain.create_order(conn, product_id=1, qty=12, customer_id=1, due_date=None,
                            variant="Demo / Демо", source="manual")
    return path, at


def login(path, person, token="phone", *, tablet=False, age=0):
    with closing(db.connect(path)) as conn:
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                     (token, person, tablet, (domain.now(conn) - timedelta(seconds=age)).isoformat()))
        conn.commit()
    return token


def app_for_shop():
    app = FastAPI()
    app.include_router(sim.router)

    @app.get("/api/human")
    def human(s=Depends(session)):
        return {"ok": True}

    return app


def test_step_is_pure_and_deterministic_with_a_seed(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        snap = sim.snapshot(conn, at)
        before = conn.total_changes
        state = sim.State()
        a = sim.step(snap, state, at, 7200, random.Random(7), sim.Config(break_chance=0))
        b = sim.step(snap, state, at, 7200, random.Random(7), sim.Config(break_chance=0))
        assert a == b
        assert conn.total_changes == before
        assert state == sim.State()
        marks = [action for action in a[1] if isinstance(action, sim.Mark)]
        assert len(marks) == 1 and marks[0].stage_id == 1


def test_stage_capacity_speed_and_no_burst_credit(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        snap = sim.snapshot(conn, at)
    # One stage completion per two shop hours at this deliberately tiny capacity.
    real = sim.step(snap, sim.State(), at, 120, random.Random(1), sim.Config(orders_per_hour=0))
    fast = sim.step(snap, sim.State(), at, 120, random.Random(1),
                    sim.Config(speed=60, orders_per_hour=0, break_chance=0))
    assert real[1] == ()
    assert len(fast[1]) == 1
    capped = replace(snap, stages=tuple(replace(st, done=st.capacity) for st in snap.stages))
    assert sim.step(capped, sim.State(), at, 7200, random.Random(1),
                    sim.Config(orders_per_hour=0))[1] == ()
    assert all(0 <= credit <= 1 for _, credit in fast[0].credits)


@pytest.mark.parametrize("at", [
    datetime(2026, 10, 27, 7), datetime(2026, 10, 27, 12, 30),
    datetime(2026, 10, 27, 17), datetime(2026, 10, 31, 10)])
def test_closed_shop_and_lunch_never_act_even_with_credit(shop, at):
    path, _ = shop
    at = at.replace(tzinfo=ZoneInfo("Asia/Bangkok"))
    with closing(db.connect(path)) as conn:
        snap = sim.snapshot(conn, at)
    state = sim.State(at.date().isoformat(), ((1, 1.0),))
    assert not sim.step(snap, state, at, 86400, random.Random(1), sim.Config(speed=60))[1]


def test_working_time_and_day_reset_use_shop_timezone(shop):
    path, at = shop
    assert sim.working_seconds(at.replace(hour=14), 7 * 3600) == 5 * 3600
    assert sim.working_seconds(at.replace(hour=8), 86400) == 0
    with closing(db.connect(path)) as conn:
        assert domain.now(conn).utcoffset() == timedelta(hours=7)
        snap = sim.snapshot(conn, at)
    state, actions = sim.step(snap, sim.State("2026-10-26", ((1, 1),)), at, 0,
                              random.Random(1), sim.Config(orders_per_hour=0))
    assert actions == () and dict(state.credits)[1] == 0


def test_active_phone_and_their_assignments_are_protected(shop):
    path, at = shop
    login(path, 11)
    with closing(db.connect(path)) as conn:
        conn.executemany("INSERT INTO assignments VALUES (?, 1, 11)", [(i,) for i in range(1, 13)])
        conn.commit()
        snap = sim.snapshot(conn, at)
        assert snap.active == {11}
        assert sim.step(snap, sim.State(), at, 7200, random.Random(1),
                        sim.Config(orders_per_hour=0, break_chance=0))[1] == ()
        conn.execute("DELETE FROM assignments")
        conn.commit()
        snap = sim.snapshot(conn, at)
    _, actions = sim.step(snap, sim.State(), at, 7200, random.Random(1),
                          sim.Config(orders_per_hour=0, break_chance=0))
    assert actions[0].person_id == 12


def test_old_phone_expires_and_tablet_does_not_reserve_person(shop):
    path, at = shop
    login(path, 11, age=301)
    login(path, 12, "tablet", tablet=True)
    with closing(db.connect(path)) as conn:
        assert sim.active_people(conn, at) == set()


def test_slow_worker_random_break_and_typo_are_seeded(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        snap = sim.snapshot(conn, at)
    config = sim.Config(orders_per_hour=0, break_chance=0, undo_chance=1)
    # Slow pacing also applies when all units are assigned, as in the seeded demo.
    snap = replace(snap, stages=(replace(snap.stages[0],
                                        waiting=((1, 11), (2, 12))),) + snap.stages[1:])
    counts = {11: 0, 12: 0}
    for i in range(200):
        _, actions = sim.step(snap, sim.State(), at, 7200, random.Random(i), config)
        counts[actions[0].person_id] += 1
        assert actions[0].undo and actions[0].method in ("button", "scan")
    assert counts[11] < counts[12]
    resting, actions = sim.step(snap, sim.State(), at, 7200, random.Random(0),
                                replace(config, break_chance=1))
    assert not actions and 180 <= dict(resting.breaks)[1] <= 600
    assert not sim.step(snap, resting, at, 1, random.Random(0), config)[1]


def test_execution_stage_order_capacity_append_only_and_no_money_writes(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        sql = []
        conn.set_trace_callback(sql.append)
        state, rnd = sim.State(), random.Random(26)
        for tick in range(30):
            state, actions = sim.step(sim.snapshot(conn, at), state, at, 7200, rnd,
                                      sim.Config(orders_per_hour=0, break_chance=0, undo_chance=0))
            sim.apply(conn, actions, f"tick-{tick}")
        for row in conn.execute("SELECT stage_id, COUNT(*) n FROM work_events GROUP BY stage_id"):
            assert row["n"] == 4
        for unit in conn.execute("SELECT id FROM units"):
            done = set(domain.done_stage_ids(conn, unit["id"]))
            assert done in (set(), {1}, {1, 2}, {1, 2, 3})
        writes = [q.upper().strip() for q in sql if q.upper().strip().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes and all(q.startswith("INSERT INTO WORK_EVENTS") for q in writes)
        assert domain.order_status(conn, 1) == "in_work"


def test_executor_rechecks_active_person_capacity_and_current_stage(shop):
    path, at = shop
    action = sim.Mark(11, 1, 1, "scan", False)
    login(path, 11)
    with closing(db.connect(path)) as conn:
        assert sim.apply(conn, [action], "active") == 0
        assert sim.apply(conn, [sim.Mark(21, 1, 2, "button", False)], "skip") == 0
        conn.execute("DELETE FROM sessions")
        conn.commit()
        conn.execute("INSERT INTO assignments VALUES (1, 1, 12)")
        conn.commit()
        assert sim.apply(conn, [action], "reassigned") == 0
        conn.execute("DELETE FROM assignments")
        conn.commit()
        assert sim.apply(conn, [action], "once") == 1
        assert sim.apply(conn, [action], "once") == 0
        for uid in (2, 3, 4):
            domain.mark_done(conn, 11, uid)
        assert sim.apply(conn, [replace(action, unit_id=5)], "full") == 0


def test_executor_rechecks_closing_time(shop, monkeypatch):
    path, at = shop
    monkeypatch.setattr(domain, "now", lambda conn: at.replace(hour=17))
    with closing(db.connect(path)) as conn:
        assert sim.apply(conn, [sim.Mark(11, 1, 1, "button", False),
                                sim.NewOrder(1, 1, 1)], "closed") == 0
        assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_stop_cancels_pending_actions(shop):
    path, _ = shop
    with closing(db.connect(path)) as conn:
        assert sim.apply(conn, [sim.Mark(11, 1, 1, "button", False)], "cancelled",
                         cancelled=lambda: True) == 0
        assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] == 0


def test_undo_is_visible_and_does_not_restore_capacity(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        for uid in range(1, 5):
            assert sim.apply(conn, [sim.Mark(11, uid, 1, "button", True)], f"undo-{uid}") == 2
        assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] == 8
        assert domain.current_stage(conn, 1)["id"] == 1
        assert sim.snapshot(conn, at).stages[0].done == 4
        assert sim.apply(conn, [sim.Mark(11, 1, 1, "scan", False)], "redo") == 0


def test_configurable_email_order_uses_domain_and_demo_variant(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        snap = sim.snapshot(conn, at)
        _, actions = sim.step(snap, sim.State(), at, 7200, random.Random(1),
                              sim.Config(orders_per_hour=1000, break_chance=1))
        assert len(actions) == 1 and isinstance(actions[0], sim.NewOrder)
        assert sim.apply(conn, actions, "email") == 1
        order = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 1").fetchone()
        assert order["source"] == "email" and "Demo" in order["variant"]
        assert conn.execute("SELECT COUNT(*) FROM units WHERE order_id = ?", (order["id"],)).fetchone()[0] == order["qty"]
        assert domain.order_status(conn, order["id"]) == "accepted"
        empty = replace(snap, customers=())
        assert not sim.step(empty, sim.State(), at, 7200, random.Random(1),
                            sim.Config(orders_per_hour=1000, break_chance=1))[1]


def test_permissions_default_off_and_start_stop_idempotent(shop):
    path, _ = shop
    app = app_for_shop()
    with TestClient(app) as client:
        assert not app.state.floorhand_simulator.running
        for url in ("/api/sim/status", "/api/live", "/api/live/snapshot"):
            assert client.get(url).status_code == 401
        for action in ("start", "stop"):
            assert client.post(f"/api/sim/{action}").status_code == 401
        for person, status_code in ((11, 403), (90, 200), (99, 200)):
            client.cookies.set(COOKIE, login(path, person, str(person)))
            assert client.get("/api/sim/status").status_code == status_code
            assert client.get("/api/live/snapshot").status_code == status_code
            if person != 99:
                assert client.post("/api/sim/start").status_code == 403
                assert client.post("/api/sim/stop").status_code == 403
        first = client.post("/api/sim/start").json()
        thread = app.state.floorhand_simulator.thread
        assert first["running"] and first["speed"] == 1 and first["fact"] == "FACT"
        assert client.post("/api/sim/start").json()["running"]
        assert app.state.floorhand_simulator.thread is thread
        assert client.post("/api/sim/stop").json()["running"] is False
        assert client.post("/api/sim/stop").json()["running"] is False
    assert not thread.is_alive()


def test_lifespan_explicit_env_enable_and_shutdown(shop, monkeypatch):
    monkeypatch.setenv("FLOORHAND_SIMULATE", "1")
    monkeypatch.setenv("FLOORHAND_SIM_SPEED", "60")
    app = app_for_shop()
    with TestClient(app):
        service = app.state.floorhand_simulator
        assert service.running and service.config.speed == 60
    assert not service.running


def test_background_thread_makes_and_publishes_work(shop, monkeypatch):
    path, _ = shop
    monkeypatch.setenv("FLOORHAND_SIMULATE", "1")
    monkeypatch.setenv("FLOORHAND_SIM_SPEED", "72000")
    monkeypatch.setenv("FLOORHAND_SIM_BREAK_CHANCE", "0")
    monkeypatch.setenv("FLOORHAND_SIM_UNDO_CHANCE", "0")
    monkeypatch.setenv("FLOORHAND_SIM_ORDERS_PER_HOUR", "0")
    app = app_for_shop()
    with TestClient(app):
        service = app.state.floorhand_simulator
        with closing(db.connect(path)) as conn:
            listener = service.hub.subscribe(conn)
        name, payload = listener.get(timeout=5)
        assert name == "marked" and payload["number"] == 101
        assert service.events >= 1
        service.hub.unsubscribe(listener)
    assert not service.running and not service.error


def test_phone_activity_tracks_existing_routes_and_shared_tablet(shop):
    path, at = shop
    phone = login(path, 11, age=3600)
    tablet = login(path, 12, "tablet", tablet=True, age=3600)
    with TestClient(app_for_shop()) as client:
        client.cookies.set(COOKIE, phone)
        assert client.get("/api/human").status_code == 200
        client.cookies.set(COOKIE, tablet)
        assert client.get("/api/human").status_code == 200
        with closing(db.connect(path)) as conn:
            assert sim.active_people(conn, at) == {11}
            assert conn.execute("SELECT COUNT(*) FROM simulator_activity").fetchone()[0] == 1


def test_module_is_discovered_and_lifespan_works_in_the_real_app(shop):
    path, _ = shop
    from floorhand.main import app

    with TestClient(app) as client:
        assert not app.state.floorhand_simulator.running
        assert client.post("/api/login", json={"person_id": 99}).status_code == 200
        assert client.get("/api/sim/status").status_code == 200
        assert client.get("/api/me").status_code == 200
        with closing(db.connect(path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM simulator_activity").fetchone()[0] == 1


def test_hub_catches_human_work_orders_shipments_without_money_or_duplicates(shop):
    path, _ = shop
    hub = sim.Hub()
    with closing(db.connect(path)) as conn:
        listener = hub.subscribe(conn)
        order = domain.create_order(conn, product_id=1, qty=1, customer_id=1,
                                    due_date=None, variant="Demo", source="manual")
        unit = conn.execute("SELECT id FROM units WHERE order_id = ?", (order,)).fetchone()[0]
        for person in (11, 21, 31):
            domain.mark_done(conn, person, unit)
        domain.ship(conn, 90, 1, [unit], "paid")
        hub.poll(conn)
        events = [listener.get_nowait() for _ in range(listener.qsize())]
        assert [name for name, _ in events] == ["marked"] * 3 + ["order_created", "shipment"]
        assert events[0][1]["worker"] == "DemoLumo11"
        assert events[0][1]["number"] == 102 and events[0][1]["stage_ru"]
        assert events[0][1]["product"] == "Imaginary cloud stool"
        assert events[0][1]["seq"] == 1 and events[0][1]["created_at"]
        assert events[-1][1]["numbers"] == [102]
        assert all(data["created_at"] for _, data in events)
        assert all(data["fact"] == "FACT" and data["message_ru"] for _, data in events)
        encoded = json.dumps(events)
        assert not any(key in encoded for key in ("amount", "price", "total", "paid"))
        hub.poll(conn)
        assert listener.empty()
        hub.unsubscribe(listener)
        assert not hub.listeners


def test_live_snapshot_has_recent_marks_late_orders_and_shop_time_without_money(shop):
    path, at = shop
    with closing(db.connect(path)) as conn:
        conn.execute("UPDATE orders SET due_date = '2026-10-26'")
        conn.commit()
        unit = conn.execute("SELECT id FROM units ORDER BY id LIMIT 1").fetchone()[0]
        event = domain.mark_done(conn, 11, unit)
        domain.undo(conn, 11, event.event_id)
        before = conn.total_changes
        data = sim.live_snapshot(conn)
        assert conn.total_changes == before
        assert data["date"] == "2026-10-27" and data["as_of"] == at.isoformat(timespec="seconds")
        assert [m["type"] for m in data["marks"]] == ["undo", "marked"]
        assert data["marks"][0]["product"] == "Imaginary cloud stool"
        assert data["late"][0]["late_days"] == 1
        assert data["stages"] == domain.floor_overview(conn)
        assert data["message"] and data["message_ru"] and data["fact"] == "FACT"
        encoded = json.dumps(data)
        assert not any(key in encoded for key in ("amount", "price", "total", "paid", "currency"))


def test_live_snapshot_empty_shop_and_recent_history_bound(shop):
    path, _ = shop
    with closing(db.connect(path)) as conn:
        assert sim.live_snapshot(conn)["marks"] == []
        units = conn.execute("SELECT id FROM units ORDER BY id").fetchall()
        for unit in units:
            domain.mark_done(conn, 11, unit["id"])
        data = sim.live_snapshot(conn)
        assert len(data["marks"]) == 8
        assert [m["seq"] for m in data["marks"]] == list(range(12, 4, -1))


def test_bounded_pubsub_tells_slow_clients_to_refresh():
    hub = sim.Hub()
    listener = queue.Queue(maxsize=1)
    hub.listeners.add(listener)
    hub.publish(("marked", {}))
    hub.publish(("marked", {}))
    assert listener.get_nowait()[0] == "resync"


def test_simulator_failure_stops_cleanly_and_is_reported(shop, monkeypatch):
    path, _ = shop
    service = sim.Simulator(path)
    reached = threading.Event()

    def fail(*args, **kwargs):
        reached.set()
        raise RuntimeError("Invented test failure")

    monkeypatch.setattr(sim, "snapshot", fail)
    service.start()
    try:
        assert reached.wait(timeout=5)
    finally:
        service.stop()
    assert service.error and not service.running
    assert "Check the server log" in service.status()["message"]


def test_sse_endpoint_streams_human_event_and_cleans_up_on_disconnect(shop):
    path, _ = shop
    token = login(path, 11)
    app = app_for_shop()

    async def exercise():
        async with app.router.lifespan_context(app):
            disconnected = asyncio.Event()
            chunks, headers = [], []

            async def receive():
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.start":
                    headers.extend(message["headers"])
                if message["type"] == "http.response.body":
                    text = message.get("body", b"").decode()
                    chunks.append(text)
                    if ": connected" in text:
                        with closing(db.connect(path)) as conn:
                            domain.mark_done(conn, 12, 1)
                    if "event: marked" in text:
                        disconnected.set()

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
                     "method": "GET", "scheme": "http", "path": "/api/live",
                     "raw_path": b"/api/live", "query_string": b"", "root_path": "",
                     "headers": [(b"cookie", f"{COOKIE}={token}".encode())],
                     "client": ("demo", 1), "server": ("demo", 80)}
            await asyncio.wait_for(app(scope, receive, send), timeout=5)
            assert any(k == b"content-type" and b"text/event-stream" in v for k, v in headers)
            assert '"number":101' in "".join(chunks)
            assert '"worker":"DemoLumo12"' in "".join(chunks)
            assert not app.state.floorhand_simulator.hub.listeners

    asyncio.run(exercise())


def test_sse_heartbeat_and_logout_close_stream(shop):
    path, _ = shop
    token = login(path, 11)
    service = sim.Simulator(path)

    class Connected:
        async def is_disconnected(self):
            return False

    async def exercise():
        with closing(db.connect(path)) as conn:
            listener = service.hub.subscribe(conn)
        stream = sim.live_stream(Connected(), service, listener, token, interval=0, heartbeat=0)
        assert ": connected" in await anext(stream)
        assert ": heartbeat" in await anext(stream)
        with closing(db.connect(path)) as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert not service.hub.listeners

    asyncio.run(exercise())


@pytest.mark.parametrize("key,value", [("SPEED", "0"), ("SPEED", "nan"),
                                      ("SPEED", "no"), ("ORDERS_PER_HOUR", "-1"),
                                      ("UNDO_CHANCE", "2"), ("BREAK_CHANCE", "-1")])
def test_invalid_configuration_is_rejected(monkeypatch, key, value):
    monkeypatch.setenv("FLOORHAND_SIM_" + key, value)
    with pytest.raises(ValueError):
        sim.Config.from_env()


def test_restarting_app_rebinds_phone_activity_to_configured_database(shop, monkeypatch):
    path, _ = shop
    app = app_for_shop()
    with TestClient(app):
        first_service = app.state.floorhand_simulator
    other = path.with_name("second-demo.db")
    with closing(db.connect(path)) as source, closing(db.connect(other)) as target:
        source.backup(target)
    monkeypatch.setenv("FLOORHAND_DB", str(other))
    token = login(other, 12, "second-phone")
    with TestClient(app) as client:
        assert app.state.floorhand_simulator is not first_service
        assert app.state.floorhand_simulator.path == other
        client.cookies.set(COOKIE, token)
        assert client.get("/api/human").status_code == 200
    with closing(db.connect(other)) as conn:
        assert conn.execute("SELECT token FROM simulator_activity").fetchone()[0] == token
    with closing(db.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM simulator_activity").fetchone()[0] == 0
