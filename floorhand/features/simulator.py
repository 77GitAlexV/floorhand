"""Opt-in demo motion and a money-free live feed; no model or external sends."""
import asyncio
import json
import logging
import math
import os
import queue
import random
import threading
import time
import uuid
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request as StarletteRequest

from .. import db, domain
from ..web import COOKIE, Session, office, session

log = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS simulator_activity (
    token TEXT PRIMARY KEY REFERENCES sessions(token) ON DELETE CASCADE,
    last_seen TEXT NOT NULL
);
"""
WORK_SECONDS = 8 * 3600  # 08:00–17:00, lunch 12:00–13:00, weekdays


@dataclass(frozen=True)
class Config:
    speed: float = 1
    orders_per_hour: float = 0.2
    undo_chance: float = 0.015
    break_chance: float = 0.08

    @classmethod
    def from_env(cls):
        try:
            values = [float(os.getenv(name, default)) for name, default in (
                ("FLOORHAND_SIM_SPEED", "1"),
                ("FLOORHAND_SIM_ORDERS_PER_HOUR", "0.2"),
                ("FLOORHAND_SIM_UNDO_CHANCE", "0.015"),
                ("FLOORHAND_SIM_BREAK_CHANCE", "0.08"))]
        except ValueError as exc:
            raise ValueError("Use numeric simulator settings. / Задайте числовые настройки симулятора.") from exc
        if (not all(math.isfinite(v) for v in values) or values[0] <= 0
                or values[1] < 0 or not all(0 <= v <= 1 for v in values[2:])):
            raise ValueError("Invalid simulator settings. / Неверные настройки симулятора.")
        return cls(*values)


@dataclass(frozen=True)
class Stage:
    id: int
    capacity: int
    done: int
    workers: tuple[int, ...]
    # (unit id, assigned worker or None), already filtered to the current stage.
    waiting: tuple[tuple[int, int | None], ...]


@dataclass(frozen=True)
class Snapshot:
    stages: tuple[Stage, ...]
    active: frozenset[int]
    products: tuple[int, ...]
    customers: tuple[int, ...]


@dataclass(frozen=True)
class State:
    day: str = ""
    credits: tuple[tuple[int, float], ...] = ()
    breaks: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class Mark:
    person_id: int
    unit_id: int
    stage_id: int
    method: str
    undo: bool


@dataclass(frozen=True)
class NewOrder:
    product_id: int
    customer_id: int
    qty: int


def working_seconds(at: datetime, elapsed: float) -> float:
    """Count only today's open hours, so pauses and midnight never cause catch-up."""
    if at.weekday() >= 5:
        return 0
    start = max(at - timedelta(seconds=max(0, elapsed)),
                at.replace(hour=0, minute=0, second=0, microsecond=0))
    total = 0.0
    for begin, end in ((8, 12), (13, 17)):
        left = max(start, at.replace(hour=begin, minute=0, second=0, microsecond=0))
        right = min(at, at.replace(hour=end, minute=0, second=0, microsecond=0))
        total += max(0, (right - left).total_seconds())
    return total


def is_open(at: datetime) -> bool:
    return at.weekday() < 5 and (8 <= at.hour < 12 or 13 <= at.hour < 17)


def step(snapshot: Snapshot, state: State, at: datetime, elapsed: float,
         rnd: random.Random, config: Config) -> tuple[State, tuple[Mark | NewOrder, ...]]:
    """Pure planning: explicit clock/input/RNG; no database, sleeps or environment."""
    day = at.date().isoformat()
    credits = dict(state.credits) if state.day == day else {}
    breaks = dict(state.breaks) if state.day == day else {}
    seconds = working_seconds(at, elapsed) * config.speed
    # Nothing happens during lunch, after closing, or on weekends, even with credit.
    if not is_open(at):
        return State(day), ()
    actions = []
    for stage in snapshot.stages:
        credit = min(1.0, credits.get(stage.id, 0) + seconds * stage.capacity / WORK_SECONDS)
        credits[stage.id] = credit
        breaks[stage.id] = max(0, breaks.get(stage.id, 0) - seconds)
        if credit < 1 or stage.done >= stage.capacity or breaks[stage.id] > 0:
            continue
        workers = [p for p in stage.workers if p not in snapshot.active]
        options = [(unit, [assigned] if assigned in workers else workers)
                   for unit, assigned in stage.waiting
                   if assigned is None or assigned in workers]
        if not options or not workers:
            continue
        if rnd.random() < config.break_chance:
            breaks[stage.id] = rnd.uniform(3, 10) * 60
            continue
        # One deliberately slower invented worker per stage; nobody is favoured by name.
        choices = [(unit, person) for unit, eligible in options[:10] for person in eligible]
        weights = [0.35 if person == stage.workers[0] else 1 for _, person in choices]
        unit, person = rnd.choices(choices, weights=weights)[0]
        actions.append(Mark(person, unit, stage.id, rnd.choice(("button", "scan")),
                            rnd.random() < config.undo_chance))
        credits[stage.id] -= 1
    chance = -math.expm1(-config.orders_per_hour * seconds / 3600)
    if snapshot.products and snapshot.customers and rnd.random() < chance:
        actions.append(NewOrder(rnd.choice(snapshot.products), rnd.choice(snapshot.customers),
                                rnd.choice((1, 1, 2, 3))))
    return State(day, tuple(sorted(credits.items())), tuple(sorted(breaks.items()))), tuple(actions)


def active_people(conn, at: datetime) -> frozenset[int]:
    rows = conn.execute(
        "SELECT s.person_id, COALESCE(a.last_seen, s.created_at) AS seen FROM sessions s "
        "LEFT JOIN simulator_activity a ON a.token = s.token WHERE s.tablet = 0")
    active = set()
    for row in rows:
        try:
            seen = datetime.fromisoformat(row["seen"])
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=at.tzinfo)
            if seen >= at - timedelta(minutes=5):
                active.add(row["person_id"])
        except ValueError:
            active.add(row["person_id"])  # uncertain activity must favour the human
    return frozenset(active)


def snapshot(conn, at: datetime) -> Snapshot:
    waiting = domain._waiting_units(conn)
    assigned = {(r["stage_id"], r["unit_id"]): r["person_id"]
                for r in conn.execute("SELECT * FROM assignments")}
    counts = dict(conn.execute(
        "SELECT stage_id, COUNT(*) FROM work_events WHERE kind = 'done' "
        "AND work_date = ? GROUP BY stage_id", (at.date().isoformat(),)).fetchall())
    stages = []
    for st in domain.stages(conn):
        workers = tuple(r[0] for r in conn.execute(
            "SELECT id FROM people WHERE role = 'worker' AND stage_id = ? ORDER BY id",
            (st["id"],)))
        stages.append(Stage(st["id"], st["capacity"], counts.get(st["id"], 0), workers,
                            tuple((u, assigned.get((st["id"], u))) for u in waiting[st["id"]])))
    return Snapshot(tuple(stages), active_people(conn, at),
                    tuple(r[0] for r in conn.execute("SELECT id FROM products ORDER BY id")),
                    tuple(r[0] for r in conn.execute("SELECT id FROM customers ORDER BY id")))


def apply(conn, actions, prefix: str, *, cancelled=None) -> int:
    """Recheck under SQLite's write lock; every business write uses domain functions."""
    made = 0
    for index, action in enumerate(actions):
        if cancelled and cancelled():
            break
        try:
            conn.execute("BEGIN IMMEDIATE")
            if cancelled and cancelled():
                conn.rollback()
                break
            at = domain.now(conn)
            if not is_open(at):
                conn.rollback()
                continue
            if isinstance(action, NewOrder):
                domain.create_order(conn, product_id=action.product_id, qty=action.qty,
                                    customer_id=action.customer_id,
                                    due_date=(at.date() + timedelta(days=7)).isoformat(),
                                    variant="Demo fabric / Демонстрационная ткань", source="email")
                made += 1
                continue
            if action.person_id in active_people(conn, at):
                conn.rollback()
                continue
            assigned = conn.execute("SELECT person_id FROM assignments WHERE unit_id = ? "
                                    "AND stage_id = ?", (action.unit_id, action.stage_id)).fetchone()
            if assigned and assigned[0] != action.person_id:
                conn.rollback()
                continue
            stage = domain.current_stage(conn, action.unit_id)
            count = conn.execute(
                "SELECT COUNT(*) FROM work_events WHERE kind = 'done' AND stage_id = ? "
                "AND work_date = ?", (action.stage_id, at.date().isoformat())).fetchone()[0]
            if stage is None or stage["id"] != action.stage_id or count >= stage["capacity"]:
                conn.rollback()
                continue
            result = domain.mark_done(conn, action.person_id, action.unit_id,
                                      method=action.method, idem_key=f"sim:{prefix}:{index}",
                                      show_money=False)
            if not result.already:
                made += 1
                if action.undo:
                    # Recheck activity after mark_done's commit releases the write lock.
                    conn.execute("BEGIN IMMEDIATE")
                    if action.person_id not in active_people(conn, domain.now(conn)):
                        domain.undo(conn, action.person_id, result.event_id)
                        made += 1
            conn.rollback()  # release any read-only transaction (already recorded / active)
        except domain.DomainError:
            conn.rollback()  # a human may have advanced/shipped/paid the item
        except Exception:
            conn.rollback()
            raise
    return made


def touch_phone(path, token: str | None) -> bool:
    if not token:
        return False
    with closing(db.connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT tablet FROM sessions WHERE token = ?", (token,)).fetchone()
        if not row:
            return False
        if not row["tablet"]:
            conn.execute("INSERT INTO simulator_activity (token, last_seen) VALUES (?, ?) "
                         "ON CONFLICT(token) DO UPDATE SET last_seen = excluded.last_seen",
                         (token, domain.now(conn).isoformat(timespec="seconds")))
            conn.commit()
        return True


class PhoneActivity:
    """Installed by the router lifespan so existing human routes need no edits."""

    def __init__(self, app, path):
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            token = StarletteRequest(scope).cookies.get(COOKIE)
            if token:
                await run_in_threadpool(touch_phone, self.path, token)
        await self.app(scope, receive, send)


class Hub:
    """One cursor per process, bounded subscriber queues; DB polling also sees humans."""

    def __init__(self):
        self.lock = threading.RLock()
        self.cursors = None
        self.listeners = set()

    def subscribe(self, conn):
        with self.lock:
            self.poll(conn)
            listener = queue.Queue(maxsize=128)
            self.listeners.add(listener)
            return listener

    def unsubscribe(self, listener):
        with self.lock:
            self.listeners.discard(listener)

    def publish(self, event):
        with self.lock:
            for listener in self.listeners:
                try:
                    listener.put_nowait(event)
                except queue.Full:
                    try:
                        while True:
                            listener.get_nowait()
                    except queue.Empty:
                        pass
                    listener.put_nowait(("resync", {
                        "fact": "FACT", "message": "Refresh the floor.",
                        "message_ru": "Обновите состояние цеха."}))

    def poll(self, conn):
        with self.lock:
            if self.cursors is None:
                self.cursors = [conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
                                for table in ("work_events", "orders", "shipments")]
                return
            rows = conn.execute(
                "SELECT e.id, e.kind, e.unit_id, e.stage_id, o.number, s.name_en AS stage, "
                "s.name_ru AS stage_ru, p.name AS worker FROM work_events e "
                "JOIN units u ON u.id = e.unit_id JOIN orders o ON o.id = u.order_id "
                "JOIN stages s ON s.id = e.stage_id JOIN people p ON p.id = e.person_id "
                "WHERE e.id > ? ORDER BY e.id", (self.cursors[0],)).fetchall()
            for row in rows:
                data = dict(row)
                data["worker"] = next(iter(data["worker"].split()), "")
                data["fact"] = "FACT"
                data.update(message="Work recorded." if row["kind"] == "done" else "Work undone.",
                            message_ru="Работа отмечена." if row["kind"] == "done" else "Отметка отменена.")
                self.publish(("marked" if row["kind"] == "done" else "undo", data))
                self.cursors[0] = row["id"]
            for row in conn.execute("SELECT id, number, source FROM orders WHERE id > ? ORDER BY id",
                                    (self.cursors[1],)).fetchall():
                self.publish(("order_created", {**dict(row), "fact": "FACT",
                                              "message": "Order created.", "message_ru": "Заказ создан."}))
                self.cursors[1] = row["id"]
            for row in conn.execute(
                    "SELECT s.id, GROUP_CONCAT(DISTINCT o.number) AS numbers FROM shipments s "
                    "JOIN shipment_lines sl ON sl.shipment_id = s.id "
                    "JOIN units u ON u.id = sl.unit_id JOIN orders o ON o.id = u.order_id "
                    "WHERE s.id > ? GROUP BY s.id ORDER BY s.id", (self.cursors[2],)).fetchall():
                self.publish(("shipment", {"id": row["id"], "fact": "FACT",
                                          "numbers": [int(n) for n in row["numbers"].split(",")],
                                          "message": "Items shipped.", "message_ru": "Изделия отгружены."}))
                self.cursors[2] = row["id"]


class Simulator:
    def __init__(self, path):
        self.path = path
        self.hub = Hub()
        self.config = Config.from_env()
        self.thread = None
        self.halt = threading.Event()
        self.lock = threading.Lock()
        self.events = 0
        self.error = False

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        with self.lock:
            if self.running:
                return
            self.config = Config.from_env()
            self.events = 0
            self.error = False
            self.halt.clear()
            self.thread = threading.Thread(target=self._run, name="floorhand-simulator", daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.halt.set()
            if self.thread:
                self.thread.join(timeout=6)

    def _run(self):
        rnd, state = random.Random(), State()
        previous = time.monotonic()
        try:
            with closing(db.connect(self.path)) as conn:
                self.hub.poll(conn)
                while not self.halt.wait(1):
                    current = time.monotonic()
                    elapsed, previous = min(5, current - previous), current
                    at = domain.now(conn)
                    state, actions = step(snapshot(conn, at), state, at, elapsed, rnd, self.config)
                    self.events += apply(conn, actions, uuid.uuid4().hex, cancelled=self.halt.is_set)
                    self.hub.poll(conn)
        except Exception:
            self.error = True
            log.exception("Simulator stopped; inspect the demo database. / Симулятор остановлен.")

    def status(self, message=None, message_ru=None):
        if self.error:
            message = message or "Simulator stopped after an error. Check the server log and demo database."
            message_ru = message_ru or "Симулятор остановлен из-за ошибки. Проверьте журнал сервера и демо-базу."
        elif self.running and self.halt.is_set():
            message = message or "Simulator is stopping. Wait for the current action to finish."
            message_ru = message_ru or "Симулятор останавливается. Дождитесь завершения текущего действия."
        return {"running": self.running, "speed": self.config.speed,
                "events_made": self.events, "fact": "FACT", "error": self.error,
                "message": message or ("Simulator is running." if self.running else "Simulator is stopped."),
                "message_ru": message_ru or ("Симулятор работает." if self.running else "Симулятор остановлен.")}


def runtime(request: Request) -> Simulator:
    if not hasattr(request.app.state, "floorhand_simulator"):
        try:
            request.app.state.floorhand_simulator = Simulator(db.db_path())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return request.app.state.floorhand_simulator


@asynccontextmanager
async def lifespan(app):
    # A new app lifespan must bind the currently configured demo database.
    sim = Simulator(db.db_path())
    app.state.floorhand_simulator = sim
    previous = app.middleware_stack or app.build_middleware_stack()
    middleware = PhoneActivity(previous, sim.path)
    app.middleware_stack = middleware
    try:
        if os.getenv("FLOORHAND_SIMULATE") == "1":
            sim.start()
        yield
    finally:
        await run_in_threadpool(sim.stop)
        if app.middleware_stack is middleware:
            app.middleware_stack = previous


router = APIRouter(lifespan=lifespan)


def owner(s: Session = Depends(office)):
    if s.role != "owner":
        raise HTTPException(403, "Only the owner can control the simulator. / Управлять симулятором может только владелец.")
    return s


@router.get("/api/sim/status")
def status(request: Request, s: Session = Depends(office)):
    return runtime(request).status()


@router.post("/api/sim/start")
def start(request: Request, s: Session = Depends(owner)):
    sim = runtime(request)
    try:
        sim.start()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return sim.status("Simulator started.", "Симулятор запущен.")


@router.post("/api/sim/stop")
def stop(request: Request, s: Session = Depends(owner)):
    sim = runtime(request)
    sim.stop()
    return sim.status()


async def live_stream(request, sim, listener, token, *, interval=1, heartbeat=15):
    """Short-lived DB connections avoid retaining a transaction for the stream."""
    last_beat = time.monotonic()
    try:
        yield ": connected / подключено\n\n"
        while not await request.is_disconnected():
            def poll():
                with closing(db.connect(sim.path)) as conn:
                    valid = conn.execute("SELECT 1 FROM sessions WHERE token = ?", (token,)).fetchone()
                    if valid:
                        sim.hub.poll(conn)
                    return bool(valid)
            if not await run_in_threadpool(poll):
                return
            while True:
                try:
                    event, data = listener.get_nowait()
                except queue.Empty:
                    break
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
            if time.monotonic() - last_beat >= heartbeat:
                await run_in_threadpool(touch_phone, sim.path, token)
                yield ": heartbeat / пульс\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(interval)
    finally:
        sim.hub.unsubscribe(listener)


@router.get("/api/live")
def live(request: Request, s: Session = Depends(session)):
    sim = runtime(request)
    with closing(db.connect(sim.path)) as conn:
        listener = sim.hub.subscribe(conn)
    return StreamingResponse(live_stream(request, sim, listener, request.cookies.get(COOKIE)),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
