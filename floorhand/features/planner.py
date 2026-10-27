"""Tomorrow's plan, computed - never guessed.

The numbers come from here; a model may only put them into words. Rules carried
over from the real workshop's day plan (the version that guessed ~10% right was
thrown out, the version below is what the manager did by hand):

- Capacity is per stage per WORKING day. What is already on someone's hands
  counts against tomorrow's capacity: unfinished work is not "new" work.
- Late and at-risk orders go first, then by due date, then work for stock.
- All units of one order go to one person when that person has room
  ("kit and one fabric to one pair of hands"), otherwise they are split.
- Fairness = pieces on hands including what is already there, not today's hand-out.
- The bottleneck floats: it is the stage whose queue needs the most days to clear.
- Idle capacity at the first stage is filled with work for stock, fastest-selling
  products first: with piece rates, no work means no money for people.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import domain
from ..domain import LIVE_DONE

RISK_LATE, RISK_AT_RISK, RISK_OK = "late", "risk", "ok"
IDLE_SHARE = 0.5           # queue below half the capacity = idle stage
OVERLOAD_FACTOR = 1.5      # more than 1.5x the per-person norm on hands = overloaded


# --- working days ---------------------------------------------------------

def workdays(conn: sqlite3.Connection) -> set[int]:
    """ISO weekdays the shop works, from settings (default Mon-Fri)."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'workdays'").fetchone()
    return {int(c) for c in (row["value"] if row else "12345")}


def next_workday(conn: sqlite3.Connection, day: date) -> date:
    wd = workdays(conn)
    d = day + timedelta(days=1)
    while d.isoweekday() not in wd:
        d += timedelta(days=1)
    return d


def workdays_until(conn: sqlite3.Connection, start: date, end: date) -> int:
    """Working days in (start, end]. Negative when end is before start."""
    if end <= start:
        return -workdays_until(conn, end, start) if end < start else 0
    wd = workdays(conn)
    return sum(1 for i in range(1, (end - start).days + 1)
               if (start + timedelta(days=i)).isoweekday() in wd)


# --- state of every open unit --------------------------------------------

@dataclass
class Unit:
    id: int
    order_id: int
    number: int
    label: str
    product: str
    product_id: int
    variant: str
    customer: str | None
    due: str | None
    stage_id: int           # current stage
    stage_idx: int          # 0-based position of the current stage
    stages_left: int
    assigned_to: int | None
    days_at_stage: int
    risk: str = RISK_OK
    late_days: int = 0
    slack: int = 0          # working days to due minus stages left


def open_units(conn: sqlite3.Connection, today: date) -> list[Unit]:
    st = domain.stages(conn)
    seq = {s["id"]: i for i, s in enumerate(st)}
    done: dict[int, dict[int, str]] = {}
    for r in conn.execute(f"SELECT unit_id, stage_id, work_date FROM ({LIVE_DONE})"):
        done.setdefault(r["unit_id"], {})[r["stage_id"]] = r["work_date"]
    shipped = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    assigned = {(r["unit_id"], r["stage_id"]): r["person_id"]
                for r in conn.execute("SELECT * FROM assignments")}
    out = []
    for u in conn.execute(
            "SELECT u.id, o.id AS order_id, o.number, o.qty, u.seq, o.product_id, o.variant, "
            "o.due_date, o.created_at, p.name AS product, c.name AS customer FROM units u "
            "JOIN orders o ON o.id = u.order_id JOIN products p ON p.id = o.product_id "
            "LEFT JOIN customers c ON c.id = o.customer_id WHERE o.cancelled = 0 "
            "ORDER BY o.number, u.seq"):
        if u["id"] in shipped:
            continue
        d = done.get(u["id"], {})
        cur = next((s for s in st if s["id"] not in d), None)
        if cur is None:
            continue
        idx = seq[cur["id"]]
        last = max((v for k, v in d.items()), default=u["created_at"][:10])
        label = f"#{u['number']}" if u["qty"] == 1 else f"#{u['number']}-{u['seq']}"
        unit = Unit(id=u["id"], order_id=u["order_id"], number=u["number"], label=label,
                    product=u["product"], product_id=u["product_id"], variant=u["variant"],
                    customer=u["customer"], due=u["due_date"], stage_id=cur["id"], stage_idx=idx,
                    stages_left=len(st) - idx, assigned_to=assigned.get((u["id"], cur["id"])),
                    days_at_stage=max(0, (today - date.fromisoformat(last)).days))
        if u["due_date"]:
            due = date.fromisoformat(u["due_date"])
            left = workdays_until(conn, today, due)
            unit.slack = left - unit.stages_left
            if due < today:
                unit.risk, unit.late_days = RISK_LATE, (today - due).days
            elif unit.slack < 0:          # ESTIMATE: one stage takes at least one working day
                unit.risk = RISK_AT_RISK
        out.append(unit)
    return out


def priority(u: Unit) -> tuple:
    """Late first (most late first), then at risk, then by due date, stock last."""
    rank = {RISK_LATE: 0, RISK_AT_RISK: 1, RISK_OK: 2}[u.risk]
    return (rank, -u.late_days, u.due or "9999-99-99", u.number, u.id)


# --- the plan ---------------------------------------------------------------

@dataclass
class StagePlan:
    stage_id: int
    code: str
    name_en: str
    name_ru: str
    capacity: int
    queue: int
    on_hands: int           # units already assigned and still waiting here
    workers: list[dict]
    lines: list[dict] = field(default_factory=list)
    left_after: int = 0
    days_to_clear: float = 0.0
    bottleneck: bool = False
    idle: int = 0           # free capacity tomorrow after the plan
    overloaded: list[dict] = field(default_factory=list)


def plan_tomorrow(conn: sqlite3.Connection, today: date | None = None) -> dict:
    today = today or date.fromisoformat(domain.today(conn))
    target = next_workday(conn, today)
    units = open_units(conn, today)
    st = domain.stages(conn)
    people = conn.execute("SELECT id, name, stage_id FROM people WHERE role = 'worker' "
                          "AND stage_id IS NOT NULL ORDER BY name").fetchall()
    stages: list[StagePlan] = []
    for s in st:
        here = sorted((u for u in units if u.stage_id == s["id"]), key=priority)
        crew = [p for p in people if p["stage_id"] == s["id"]]
        load = {p["id"]: sum(1 for u in here if u.assigned_to == p["id"]) for p in crew}
        norm = s["capacity"] / max(len(crew), 1)
        sp = StagePlan(stage_id=s["id"], code=s["code"], name_en=s["name_en"],
                       name_ru=s["name_ru"], capacity=s["capacity"], queue=len(here),
                       on_hands=sum(load.values()),
                       workers=[{"id": p["id"], "name": p["name"], "on_hands": load[p["id"]],
                                 "norm": round(norm, 1)} for p in crew])
        free_cap = max(0, s["capacity"] - sp.on_hands)
        todo = [u for u in here if u.assigned_to is None][:free_cap]
        if crew:
            new: dict[int, int] = {p["id"]: 0 for p in crew}
            by_order: dict[int, list[Unit]] = {}
            for u in todo:
                by_order.setdefault(u.order_id, []).append(u)
            for order_units in by_order.values():
                # A customer's order is never cut: all of it to the least loaded person.
                # A batch for stock may be split when a person is at their norm.
                least = lambda: min(crew, key=lambda p: (load[p["id"]] + new[p["id"]], p["name"]))["id"]  # noqa
                pid = least()
                for u in order_units:
                    if u.customer is None and load[pid] + new[pid] >= norm:
                        pid = least()
                    new[pid] += 1
                    sp.lines.append({
                        "unit_id": u.id, "order_id": u.order_id, "label": u.label,
                        "product": u.product, "variant": u.variant, "customer": u.customer,
                        "due": u.due, "risk": u.risk, "late_days": u.late_days,
                        "stage_id": s["id"], "person_id": pid,
                        "person": next(p["name"] for p in crew if p["id"] == pid),
                        "options": [p["id"] for p in crew]})
            for w in sp.workers:
                w["planned"] = new[w["id"]]
                w["total"] = w["on_hands"] + new[w["id"]]
        sp.left_after = max(0, sp.queue - sp.on_hands - len(sp.lines))
        sp.days_to_clear = round(sp.queue / max(s["capacity"], 1), 1)
        sp.idle = max(0, free_cap - len(sp.lines)) if sp.queue < s["capacity"] * IDLE_SHARE \
            else 0
        sp.overloaded = [w for w in sp.workers if w["on_hands"] > norm * OVERLOAD_FACTOR]
        stages.append(sp)
    worst = max(stages, key=lambda x: x.days_to_clear, default=None)
    if worst and worst.queue > worst.capacity:
        worst.bottleneck = True
    late = sorted((u for u in units if u.risk == RISK_LATE), key=priority)
    risk = sorted((u for u in units if u.risk == RISK_AT_RISK), key=priority)
    return {"today": today.isoformat(), "target": target.isoformat(),
            "stages": [sp.__dict__ for sp in stages], "units": units,
            "late": late, "risk": risk, "open": len(units)}


# --- work for stock --------------------------------------------------------

def stock_candidates(conn: sqlite3.Connection, today: date, want: int, days: int = 30) -> list[dict]:
    """Products to launch for stock when the first stage has room: fastest-selling
    in the last `days`, minus what is already finished or in work for stock."""
    since = (today - timedelta(days=days)).isoformat()
    sold = {r["product_id"]: r["n"] for r in conn.execute(
        "SELECT o.product_id, COUNT(*) AS n FROM shipment_lines sl "
        "JOIN units u ON u.id = sl.unit_id JOIN orders o ON o.id = u.order_id "
        "JOIN shipments s ON s.id = sl.shipment_id WHERE substr(s.created_at, 1, 10) >= ? "
        "GROUP BY o.product_id", (since,))}
    shipped = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    in_stock: dict[int, int] = {}
    for r in conn.execute("SELECT u.id, o.product_id FROM units u JOIN orders o ON o.id = u.order_id "
                          "WHERE o.customer_id IS NULL AND o.cancelled = 0"):
        if r["id"] not in shipped:
            in_stock[r["product_id"]] = in_stock.get(r["product_id"], 0) + 1
    out = []
    for p in conn.execute("SELECT id, name, kind FROM products"):
        rate = sold.get(p["id"], 0) / days          # units per day, FACT from shipments
        cover_days = in_stock.get(p["id"], 0) / rate if rate else None
        if rate and (cover_days is None or cover_days < 14):
            out.append({"product_id": p["id"], "product": p["name"], "sold_30d": sold[p["id"]],
                        "per_day": round(rate, 2), "in_stock_or_work": in_stock.get(p["id"], 0),
                        "cover_days": None if cover_days is None else round(cover_days, 1)})
    out.sort(key=lambda c: (c["cover_days"] if c["cover_days"] is not None else 0, -c["per_day"]))
    plan, left = [], want
    for c in out[:3]:
        if left <= 0:
            break
        qty = max(1, min(left, round(c["per_day"] * 7)))   # a week of sales, within room
        plan.append({**c, "qty": qty})
        left -= qty
    return plan
