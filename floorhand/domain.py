"""Shop-floor domain. Every write to the database goes through this module.

Order status is never set by hand: it follows from units, work events and
shipments. Agent tools (later) call these same functions.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

LIVE_DONE = """
    SELECT e.* FROM work_events e
    WHERE e.kind = 'done'
      AND NOT EXISTS (SELECT 1 FROM work_events u
                      WHERE u.kind = 'undo' AND u.undoes_id = e.id)
"""


class DomainError(Exception):
    """Refusal that is shown to the person as-is: what went wrong and what to do."""


@dataclass
class MarkResult:
    ok: bool
    already: bool = False
    event_id: int | None = None
    unit_label: str = ""
    stage: str = ""
    amount: int | None = None           # None = rate not set yet
    message: str = ""
    extra: dict = field(default_factory=dict)


# --- time -----------------------------------------------------------------

def shop_tz(conn: sqlite3.Connection) -> ZoneInfo:
    row = conn.execute("SELECT value FROM settings WHERE key = 'tz'").fetchone()
    return ZoneInfo(row["value"] if row else "UTC")


def now(conn: sqlite3.Connection) -> datetime:
    return datetime.now(shop_tz(conn))


def today(conn: sqlite3.Connection) -> str:
    return now(conn).date().isoformat()


def currency(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = 'currency'").fetchone()
    return row["value"] if row else "$"


# --- lookups --------------------------------------------------------------

def stages(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM stages ORDER BY seq").fetchall()


def person(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise DomainError("Unknown person.")
    return row


def unit_label(conn: sqlite3.Connection, unit_id: int) -> str:
    row = conn.execute(
        "SELECT o.number, o.qty, u.seq FROM units u JOIN orders o ON o.id = u.order_id "
        "WHERE u.id = ?", (unit_id,)).fetchone()
    if row is None:
        raise DomainError("No such item.")
    return f"#{row['number']}" if row["qty"] == 1 else f"#{row['number']}-{row['seq']}"


def rate(conn: sqlite3.Connection, unit_id: int, stage_id: int) -> int | None:
    row = conn.execute(
        "SELECT r.amount FROM units u JOIN orders o ON o.id = u.order_id "
        "JOIN rates r ON r.product_id = o.product_id AND r.stage_id = ? "
        "WHERE u.id = ?", (stage_id, unit_id)).fetchone()
    return row["amount"] if row else None


def done_stage_ids(conn: sqlite3.Connection, unit_id: int) -> dict[int, int]:
    """stage_id -> live 'done' event id for one unit."""
    rows = conn.execute(f"SELECT stage_id, id FROM ({LIVE_DONE}) WHERE unit_id = ?",
                        (unit_id,)).fetchall()
    return {r["stage_id"]: r["id"] for r in rows}


def current_stage(conn: sqlite3.Connection, unit_id: int) -> sqlite3.Row | None:
    """First stage not done yet; None when the unit is finished."""
    done = done_stage_ids(conn, unit_id)
    for st in stages(conn):
        if st["id"] not in done:
            return st
    return None


def is_shipped(conn: sqlite3.Connection, unit_id: int) -> bool:
    return conn.execute("SELECT 1 FROM shipment_lines WHERE unit_id = ?",
                        (unit_id,)).fetchone() is not None


def _unit_order(conn: sqlite3.Connection, unit_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT o.* FROM units u JOIN orders o ON o.id = u.order_id "
                       "WHERE u.id = ?", (unit_id,)).fetchone()
    if row is None:
        raise DomainError("No such item.")
    return row


# --- marking work ---------------------------------------------------------

def mark_done(conn: sqlite3.Connection, person_id: int, unit_id: int, *,
              idem_key: str | None = None, method: str = "button",
              work_date: str | None = None, show_money: bool = True) -> MarkResult:
    """Record that a worker finished their stage on one unit.

    Repeating a mark never undoes anything: it answers "already recorded".
    The same idem_key (offline queue retry) returns the original result.
    """
    if idem_key:
        prev = conn.execute("SELECT * FROM work_events WHERE idem_key = ?",
                            (idem_key,)).fetchone()
        if prev is not None:
            return _result_for_event(conn, prev, already=True, show_money=show_money)

    who = person(conn, person_id)
    if who["stage_id"] is None:
        raise DomainError("You have no stage set. Ask the manager to set your stage.")
    order = _unit_order(conn, unit_id)
    label = unit_label(conn, unit_id)
    if order["cancelled"]:
        raise DomainError(f"{label} is cancelled. Put it aside and tell the manager.")
    if is_shipped(conn, unit_id):
        raise DomainError(f"{label} has already been shipped.")

    my_stage = conn.execute("SELECT * FROM stages WHERE id = ?",
                            (who["stage_id"],)).fetchone()
    done = done_stage_ids(conn, unit_id)
    if my_stage["id"] in done:
        prev = conn.execute("SELECT * FROM work_events WHERE id = ?",
                            (done[my_stage["id"]],)).fetchone()
        return _result_for_event(conn, prev, already=True, show_money=show_money)

    cur = current_stage(conn, unit_id)
    if cur is not None and cur["seq"] < my_stage["seq"]:
        raise DomainError(
            f"{label} is still at {cur['name_en']}. It reaches {my_stage['name_en']} "
            f"after {cur['name_en']} marks it done.")

    when = now(conn)
    cur_ev = conn.execute(
        "INSERT INTO work_events (kind, unit_id, stage_id, person_id, work_date, "
        "created_at, method, idem_key) VALUES ('done', ?, ?, ?, ?, ?, ?, ?)",
        (unit_id, my_stage["id"], person_id, work_date or when.date().isoformat(),
         when.isoformat(timespec="seconds"), method, idem_key))
    conn.commit()
    ev = conn.execute("SELECT * FROM work_events WHERE id = ?",
                      (cur_ev.lastrowid,)).fetchone()
    return _result_for_event(conn, ev, already=False, show_money=show_money)


def _result_for_event(conn, ev: sqlite3.Row, *, already: bool,
                      show_money: bool) -> MarkResult:
    st = conn.execute("SELECT * FROM stages WHERE id = ?", (ev["stage_id"],)).fetchone()
    label = unit_label(conn, ev["unit_id"])
    amount = rate(conn, ev["unit_id"], ev["stage_id"]) if show_money else None
    if already:
        msg = f"Already recorded: {label}, {st['name_en']}. Nothing changed."
    elif not show_money:
        msg = f"Recorded: {label}, {st['name_en']}."
    else:
        money = f"{currency(conn)}{amount}" if amount is not None else "price pending"
        msg = f"Recorded: {label}, {st['name_en']}, {money}."
    return MarkResult(ok=True, already=already, event_id=ev["id"], unit_label=label,
                      stage=st["name_en"], amount=amount, message=msg)


def next_unit_for(conn: sqlite3.Connection, person_id: int, order_id: int) -> int:
    """The unit of this order a worker should mark next: assigned to them first,
    then free ones waiting at their stage."""
    who = person(conn, person_id)
    units = conn.execute("SELECT id FROM units WHERE order_id = ? ORDER BY seq",
                         (order_id,)).fetchall()
    waiting = [u["id"] for u in units
               if not is_shipped(conn, u["id"])
               and (cs := current_stage(conn, u["id"])) is not None
               and cs["id"] == who["stage_id"]]
    if not waiting:
        raise DomainError("Nothing from this order is waiting at your stage.")
    mine = {r["unit_id"] for r in conn.execute(
        "SELECT unit_id FROM assignments WHERE stage_id = ? AND person_id = ?",
        (who["stage_id"], person_id))}
    for uid in waiting:
        if uid in mine:
            return uid
    return waiting[0]


def undo(conn: sqlite3.Connection, person_id: int, event_id: int) -> MarkResult:
    """'Made a mistake — undo'. Adds a visible undo record; nothing is deleted."""
    ev = conn.execute("SELECT * FROM work_events WHERE id = ? AND kind = 'done'",
                      (event_id,)).fetchone()
    if ev is None:
        raise DomainError("No such record.")
    if ev["person_id"] != person_id:
        raise DomainError("You can only undo your own records. Ask the manager.")
    if conn.execute("SELECT 1 FROM work_events WHERE kind = 'undo' AND undoes_id = ?",
                    (event_id,)).fetchone():
        raise DomainError("This record is already undone.")
    if conn.execute("SELECT 1 FROM payout_lines WHERE event_id = ?",
                    (event_id,)).fetchone():
        raise DomainError("This work is already paid out. Ask the manager to fix it.")
    st = conn.execute("SELECT * FROM stages WHERE id = ?", (ev["stage_id"],)).fetchone()
    later = [s for s in stages(conn) if s["seq"] > st["seq"]]
    done = done_stage_ids(conn, ev["unit_id"])
    label = unit_label(conn, ev["unit_id"])
    for s in later:
        if s["id"] in done:
            raise DomainError(f"{label} has already passed {s['name_en']}. "
                              "Ask the manager to send it back for rework.")
    if is_shipped(conn, ev["unit_id"]):
        raise DomainError(f"{label} has already been shipped.")
    when = now(conn)
    conn.execute(
        "INSERT INTO work_events (kind, unit_id, stage_id, person_id, work_date, "
        "created_at, method, undoes_id) VALUES ('undo', ?, ?, ?, ?, ?, 'button', ?)",
        (ev["unit_id"], ev["stage_id"], person_id, ev["work_date"],
         when.isoformat(timespec="seconds"), event_id))
    conn.commit()
    return MarkResult(ok=True, unit_label=label, stage=st["name_en"],
                      message=f"Undone: {label}, {st['name_en']}.")


# --- reading the floor ----------------------------------------------------

def _waiting_units(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """stage_id -> unit ids waiting there (not finished, not shipped, not cancelled)."""
    st = stages(conn)
    done_by_unit: dict[int, set[int]] = {}
    for r in conn.execute(f"SELECT unit_id, stage_id FROM ({LIVE_DONE})"):
        done_by_unit.setdefault(r["unit_id"], set()).add(r["stage_id"])
    shipped = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    out: dict[int, list[int]] = {s["id"]: [] for s in st}
    for u in conn.execute("SELECT u.id FROM units u JOIN orders o ON o.id = u.order_id "
                          "WHERE o.cancelled = 0 ORDER BY o.due_date, o.number, u.seq"):
        if u["id"] in shipped:
            continue
        done = done_by_unit.get(u["id"], set())
        for s in st:
            if s["id"] not in done:
                out[s["id"]].append(u["id"])
                break
    return out


def worker_home(conn: sqlite3.Connection, person_id: int, *, show_money: bool) -> dict:
    """Everything the worker 'Work' screen needs in one call."""
    who = person(conn, person_id)
    if who["stage_id"] is None:
        raise DomainError("You have no stage set. Ask the manager to set your stage.")
    stage = conn.execute("SELECT * FROM stages WHERE id = ?", (who["stage_id"],)).fetchone()
    day = today(conn)
    waiting = _waiting_units(conn)[stage["id"]]
    assigned = {r["unit_id"]: r["person_id"] for r in conn.execute(
        "SELECT unit_id, person_id FROM assignments WHERE stage_id = ?", (stage["id"],))}

    def card(order_id: int, unit_ids: list[int]) -> dict:
        o = conn.execute("SELECT o.*, p.name AS product, p.kind, c.name AS customer "
                         "FROM orders o JOIN products p ON p.id = o.product_id "
                         "LEFT JOIN customers c ON c.id = o.customer_id WHERE o.id = ?",
                         (order_id,)).fetchone()
        done_here = conn.execute(
            f"SELECT COUNT(*) FROM ({LIVE_DONE}) e JOIN units u ON u.id = e.unit_id "
            "WHERE u.order_id = ? AND e.stage_id = ?", (order_id, stage["id"])).fetchone()[0]
        r = rate(conn, unit_ids[0], stage["id"])
        return {
            "order_id": order_id, "number": o["number"], "product": o["product"],
            "kind": o["kind"], "variant": o["variant"], "qty": o["qty"],
            "waiting": len(unit_ids), "done_here": done_here,
            "customer": o["customer"], "due_date": o["due_date"],
            "rate": r if show_money else None, "rate_set": r is not None,
        }

    def group(unit_ids: list[int]) -> list[dict]:
        by_order: dict[int, list[int]] = {}
        for uid in unit_ids:
            oid = conn.execute("SELECT order_id FROM units WHERE id = ?", (uid,)).fetchone()[0]
            by_order.setdefault(oid, []).append(uid)
        return [card(oid, uids) for oid, uids in by_order.items()]

    mine = [u for u in waiting if assigned.get(u) == person_id]
    free = [u for u in waiting if u not in assigned]

    today_rows = conn.execute(
        f"SELECT e.* FROM ({LIVE_DONE}) e WHERE e.person_id = ? AND e.work_date = ? "
        "ORDER BY e.id", (person_id, day)).fetchall()
    done_today = []
    total = 0
    pending = 0
    for ev in today_rows:
        amount = rate(conn, ev["unit_id"], ev["stage_id"])
        if amount is None:
            pending += 1
        else:
            total += amount
        done_today.append({"event_id": ev["id"], "unit": unit_label(conn, ev["unit_id"]),
                           "at": ev["created_at"][11:16],
                           "amount": amount if show_money else None})

    return {
        "person": {"id": who["id"], "name": who["name"], "lang": who["lang"]},
        "stage": {"id": stage["id"], "name_en": stage["name_en"], "name_ru": stage["name_ru"]},
        "date": day,
        "currency": currency(conn),
        "show_money": show_money,
        "today": {"count": len(today_rows), "money": total if show_money else None,
                  "price_pending": pending},
        "mine": group(mine),
        "free": group(free),
        "done_today": done_today,
    }


def take(conn: sqlite3.Connection, person_id: int, order_id: int, n: int = 1) -> int:
    """Worker takes free units of an order at their stage. Returns how many."""
    who = person(conn, person_id)
    waiting = _waiting_units(conn)[who["stage_id"]]
    taken = {r["unit_id"] for r in conn.execute(
        "SELECT unit_id FROM assignments WHERE stage_id = ?", (who["stage_id"],))}
    units = [r["id"] for r in conn.execute("SELECT id FROM units WHERE order_id = ? "
                                           "ORDER BY seq", (order_id,))]
    free = [u for u in units if u in waiting and u not in taken][:n]
    if not free:
        raise DomainError("Nothing free left in this order at your stage.")
    conn.executemany("INSERT INTO assignments (unit_id, stage_id, person_id) VALUES (?, ?, ?)",
                     [(u, who["stage_id"], person_id) for u in free])
    conn.commit()
    return len(free)


def earnings(conn: sqlite3.Connection, person_id: int, days: int = 7) -> dict:
    """Money by work date for the last `days` days, plus paid / to be paid."""
    end = date.fromisoformat(today(conn))
    start = end - timedelta(days=days - 1)
    rows = conn.execute(
        f"SELECT e.*, (SELECT 1 FROM payout_lines pl WHERE pl.event_id = e.id) AS paid "
        f"FROM ({LIVE_DONE}) e WHERE e.person_id = ?", (person_id,)).fetchall()
    by_day = {(start + timedelta(days=i)).isoformat(): {"count": 0, "money": 0}
              for i in range(days)}
    paid = unpaid = pending = 0
    for ev in rows:
        amount = rate(conn, ev["unit_id"], ev["stage_id"])
        if amount is None:
            pending += 1
            continue
        if ev["paid"]:
            paid += amount
        else:
            unpaid += amount
        if ev["work_date"] in by_day:
            by_day[ev["work_date"]]["count"] += 1
            by_day[ev["work_date"]]["money"] += amount
    return {"currency": currency(conn),
            "days": [{"date": d, **v} for d, v in by_day.items()],
            "paid": paid, "to_be_paid": unpaid, "price_pending": pending}


def order_status(conn: sqlite3.Connection, order_id: int) -> str:
    """accepted -> in_work -> ready -> shipped. Derived, never stored."""
    o = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if o["cancelled"]:
        return "cancelled"
    units = [r["id"] for r in conn.execute("SELECT id FROM units WHERE order_id = ?",
                                           (order_id,))]
    if all(is_shipped(conn, u) for u in units):
        return "shipped"
    if all(current_stage(conn, u) is None for u in units):
        return "ready"
    if any(done_stage_ids(conn, u) for u in units):
        return "in_work"
    return "accepted"


def floor_overview(conn: sqlite3.Connection) -> list[dict]:
    """Per stage: queue, done today vs capacity, who did how much today."""
    day = today(conn)
    waiting = _waiting_units(conn)
    out = []
    for s in stages(conn):
        done_today = conn.execute(f"SELECT COUNT(*) FROM ({LIVE_DONE}) WHERE stage_id = ? "
                                  "AND work_date = ?", (s["id"], day)).fetchone()[0]
        people = conn.execute(
            f"SELECT p.id, p.name, (SELECT COUNT(*) FROM ({LIVE_DONE}) e "
            "WHERE e.person_id = p.id AND e.work_date = ?) AS today "
            "FROM people p WHERE p.stage_id = ? ORDER BY p.name", (day, s["id"])).fetchall()
        out.append({"id": s["id"], "name_en": s["name_en"], "name_ru": s["name_ru"],
                    "queue": len(waiting[s["id"]]), "done_today": done_today,
                    "capacity": s["capacity"],
                    "people": [dict(p) for p in people]})
    worst = max(out, key=lambda x: x["queue"] / max(x["capacity"], 1))
    for x in out:
        x["bottleneck"] = x is worst and worst["queue"] > worst["capacity"]
    return out


# --- shipping -------------------------------------------------------------

def shipment_total(conn: sqlite3.Connection, shipment_id: int) -> int:
    """The only place a shipment total is computed (invoice, debts, reports)."""
    return conn.execute("SELECT COALESCE(SUM(price), 0) FROM shipment_lines "
                        "WHERE shipment_id = ?", (shipment_id,)).fetchone()[0]


def ship(conn: sqlite3.Connection, person_id: int, customer_id: int,
         unit_ids: list[int], payment: str, paid_amount: int = 0) -> int:
    """The only way an item leaves the shop. Only finished units of this
    customer's orders can go."""
    who = person(conn, person_id)
    if who["role"] not in ("manager", "owner"):
        raise DomainError("Only the office can ship.")
    if not unit_ids:
        raise DomainError("Pick at least one item to ship.")
    if len(set(unit_ids)) != len(unit_ids):
        raise DomainError("The same item is picked twice.")
    if payment not in ("paid", "credit", "partial"):
        raise DomainError("Payment must be paid, credit or partial.")
    prices = []
    for uid in unit_ids:
        o = _unit_order(conn, uid)
        label = unit_label(conn, uid)
        if o["customer_id"] != customer_id:
            raise DomainError(f"{label} belongs to another customer.")
        if o["cancelled"]:
            raise DomainError(f"{label} is cancelled.")
        if is_shipped(conn, uid):
            raise DomainError(f"{label} has already been shipped.")
        cs = current_stage(conn, uid)
        if cs is not None:
            raise DomainError(f"{label} is not ready: still at {cs['name_en']}.")
        price = conn.execute("SELECT price FROM products WHERE id = ?",
                             (o["product_id"],)).fetchone()[0]
        prices.append((uid, price))
    when = now(conn).isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO shipments (customer_id, payment, paid_amount, "
                       "created_at, created_by) VALUES (?, ?, 0, ?, ?)",
                       (customer_id, payment, when, person_id))
    sid = cur.lastrowid
    conn.executemany("INSERT INTO shipment_lines (shipment_id, unit_id, price) "
                     "VALUES (?, ?, ?)", [(sid, u, p) for u, p in prices])
    total = shipment_total(conn, sid)
    paid = total if payment == "paid" else (0 if payment == "credit" else paid_amount)
    if not 0 <= paid <= total:
        conn.rollback()
        raise DomainError("Paid amount must be between 0 and the total.")
    conn.execute("UPDATE shipments SET paid_amount = ? WHERE id = ?", (paid, sid))
    conn.commit()
    return sid


# --- office: orders -------------------------------------------------------

def create_order(conn: sqlite3.Connection, *, product_id: int, qty: int,
                 customer_id: int | None, due_date: str | None, variant: str,
                 source: str, commit: bool = True) -> int:
    """New order with one unit row per piece. Number = last number + 1."""
    if not 0 < qty <= 500:
        raise DomainError("Quantity must be between 1 and 500.")
    if conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone() is None:
        raise DomainError("Unknown product.")
    number = conn.execute("SELECT COALESCE(MAX(number), 100) + 1 FROM orders").fetchone()[0]
    oid = conn.execute(
        "INSERT INTO orders (number, product_id, variant, qty, customer_id, due_date, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (number, product_id, variant, qty, customer_id, due_date, source,
         now(conn).isoformat(timespec="seconds"))).lastrowid
    conn.executemany("INSERT INTO units (order_id, seq) VALUES (?, ?)",
                     [(oid, i) for i in range(1, qty + 1)])
    if commit:
        conn.commit()
    return oid


def _order_progress(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """(order_id -> {stage_id: units done}, order_id -> units shipped)."""
    done: dict[int, dict[int, int]] = {}
    for r in conn.execute(f"SELECT u.order_id, e.stage_id, COUNT(*) AS n FROM ({LIVE_DONE}) e "
                          "JOIN units u ON u.id = e.unit_id GROUP BY u.order_id, e.stage_id"):
        done.setdefault(r["order_id"], {})[r["stage_id"]] = r["n"]
    shipped = {r["order_id"]: r["n"] for r in conn.execute(
        "SELECT u.order_id, COUNT(*) AS n FROM shipment_lines sl "
        "JOIN units u ON u.id = sl.unit_id GROUP BY u.order_id")}
    return done, shipped


def _summarize(order: sqlite3.Row, st: list, done: dict, shipped: int, day: str) -> dict:
    qty = order["qty"]
    line = []
    before = qty  # units that have passed the previous stage
    for s in st:
        n = done.get(s["id"], 0)
        line.append({"stage_id": s["id"], "name_en": s["name_en"], "name_ru": s["name_ru"],
                     "done": n, "here": before - n,
                     "state": "done" if n == qty else ("active" if before - n else "todo")})
        before = n
    finished = done.get(st[-1]["id"], 0)
    if order["cancelled"]:
        status = "cancelled"
    elif shipped == qty:
        status = "shipped"
    elif finished == qty:
        status = "ready"
    elif any(done.values()):
        status = "in_work"
    else:
        status = "accepted"
    due = order["due_date"]
    late_days = 0
    if due and status not in ("shipped", "cancelled", "ready") and due < day:
        late_days = (date.fromisoformat(day) - date.fromisoformat(due)).days
    return {"status": status, "line": line, "finished": finished, "shipped": shipped,
            "late_days": late_days}


def orders_list(conn: sqlite3.Connection, q: str = "", view: str = "open") -> dict:
    """Orders table plus the 'needs attention' block for the manager."""
    st = stages(conn)
    day = today(conn)
    done, shipped = _order_progress(conn)
    rows = conn.execute(
        "SELECT o.*, p.name AS product, c.name AS customer, c.city FROM orders o "
        "JOIN products p ON p.id = o.product_id LEFT JOIN customers c ON c.id = o.customer_id "
        "ORDER BY o.number DESC").fetchall()
    needle = q.strip().lower().lstrip("#")
    out, attention = [], []
    for o in rows:
        info = _summarize(o, st, done.get(o["id"], {}), shipped.get(o["id"], 0), day)
        item = {"id": o["id"], "number": o["number"], "product": o["product"],
                "variant": o["variant"], "qty": o["qty"], "customer": o["customer"],
                "city": o["city"], "due_date": o["due_date"], "source": o["source"], **info}
        if info["late_days"]:
            attention.append({**item, "reason": "late"})
        if needle and needle not in " ".join(
                str(v).lower() for v in (o["number"], o["product"], o["customer"], o["variant"])):
            continue
        if view == "open" and info["status"] in ("shipped", "cancelled"):
            continue
        if view in ("in_work", "ready", "shipped") and info["status"] != view:
            continue
        out.append(item)
    attention.sort(key=lambda x: -x["late_days"])
    return {"date": day, "orders": out, "attention": attention}


def order_detail(conn: sqlite3.Connection, order_id: int) -> dict:
    o = conn.execute(
        "SELECT o.*, p.name AS product, p.price, c.name AS customer, c.city FROM orders o "
        "JOIN products p ON p.id = o.product_id LEFT JOIN customers c ON c.id = o.customer_id "
        "WHERE o.id = ?", (order_id,)).fetchone()
    if o is None:
        raise DomainError("No such order.")
    st = stages(conn)
    done, shipped = _order_progress(conn)
    info = _summarize(o, st, done.get(order_id, {}), shipped.get(order_id, 0), today(conn))
    who: dict[int, list[str]] = {}
    for r in conn.execute(f"SELECT e.stage_id, p.name FROM ({LIVE_DONE}) e "
                          "JOIN units u ON u.id = e.unit_id JOIN people p ON p.id = e.person_id "
                          "WHERE u.order_id = ? ORDER BY e.id", (order_id,)):
        names = who.setdefault(r["stage_id"], [])
        if r["name"] not in names:
            names.append(r["name"])
    for step in info["line"]:
        step["people"] = who.get(step["stage_id"], [])
    history = [dict(r) for r in conn.execute(
        "SELECT e.kind, e.created_at, e.method, s.name_en AS stage, s.name_ru AS stage_ru, "
        "p.name AS person, u.seq FROM work_events e JOIN units u ON u.id = e.unit_id "
        "JOIN stages s ON s.id = e.stage_id JOIN people p ON p.id = e.person_id "
        "WHERE u.order_id = ? ORDER BY e.id DESC", (order_id,))]
    return {"id": o["id"], "number": o["number"], "product": o["product"],
            "variant": o["variant"], "qty": o["qty"], "price": o["price"],
            "customer": o["customer"], "customer_id": o["customer_id"], "city": o["city"],
            "due_date": o["due_date"], "source": o["source"], "created_at": o["created_at"],
            "currency": currency(conn), **info, "history": history}


# --- office: shipping -----------------------------------------------------

def _debts(conn: sqlite3.Connection) -> dict[int, int]:
    out: dict[int, int] = {}
    for sh in conn.execute("SELECT id, customer_id, paid_amount FROM shipments"):
        owed = shipment_total(conn, sh["id"]) - sh["paid_amount"]
        if owed:
            out[sh["customer_id"]] = out.get(sh["customer_id"], 0) + owed
    return out


def shipping_board(conn: sqlite3.Connection) -> dict:
    """Left side of the shipping screen: who has something ready, today, debts."""
    last = stages(conn)[-1]["id"]
    done_units = {r["unit_id"] for r in conn.execute(
        f"SELECT unit_id FROM ({LIVE_DONE}) WHERE stage_id = ?", (last,))}
    shipped = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    customers: dict[int, dict] = {}
    for r in conn.execute("SELECT u.id, o.customer_id, c.name, c.city FROM units u "
                          "JOIN orders o ON o.id = u.order_id "
                          "JOIN customers c ON c.id = o.customer_id WHERE o.cancelled = 0"):
        if r["id"] in shipped:
            continue
        c = customers.setdefault(r["customer_id"], {"customer_id": r["customer_id"],
                                                    "name": r["name"], "city": r["city"],
                                                    "ready": 0, "open": 0})
        c["open"] += 1
        c["ready"] += r["id"] in done_units
    ready = sorted((c for c in customers.values() if c["ready"]),
                   key=lambda c: (c["ready"] != c["open"], -c["ready"]))
    day = today(conn)
    today_rows = conn.execute(
        "SELECT s.id, c.name FROM shipments s JOIN customers c ON c.id = s.customer_id "
        "WHERE substr(s.created_at, 1, 10) = ? ORDER BY s.id", (day,)).fetchall()
    debts = _debts(conn)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM customers")}
    return {"currency": currency(conn), "ready": ready,
            "today": [{"id": r["id"], "customer": r["name"],
                       "total": shipment_total(conn, r["id"])} for r in today_rows],
            "debts": sorted(({"customer_id": k, "name": names[k], "owed": v}
                             for k, v in debts.items()), key=lambda d: -d["owed"]),
            "owed_total": sum(debts.values())}


def customer_shipping(conn: sqlite3.Connection, customer_id: int) -> dict:
    """Right side: this customer's open items, ready ones first, others with why."""
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if c is None:
        raise DomainError("No such customer.")
    items = []
    for u in conn.execute(
            "SELECT u.id, p.price, p.name AS product FROM units u "
            "JOIN orders o ON o.id = u.order_id JOIN products p ON p.id = o.product_id "
            "WHERE o.customer_id = ? AND o.cancelled = 0 ORDER BY o.number, u.seq",
            (customer_id,)):
        if is_shipped(conn, u["id"]):
            continue
        cs = current_stage(conn, u["id"])
        items.append({"unit_id": u["id"], "label": unit_label(conn, u["id"]),
                      "product": u["product"], "price": u["price"], "ready": cs is None,
                      "at": None if cs is None else cs["name_en"],
                      "at_ru": None if cs is None else cs["name_ru"]})
    items.sort(key=lambda i: not i["ready"])
    return {"customer_id": c["id"], "name": c["name"], "city": c["city"],
            "currency": currency(conn), "items": items,
            "owed": _debts(conn).get(customer_id, 0)}


def invoice(conn: sqlite3.Connection, shipment_id: int) -> dict:
    sh = conn.execute("SELECT s.*, c.name AS customer, c.city FROM shipments s "
                      "JOIN customers c ON c.id = s.customer_id WHERE s.id = ?",
                      (shipment_id,)).fetchone()
    if sh is None:
        raise DomainError("No such shipment.")
    lines = [{"label": unit_label(conn, r["unit_id"]), "product": r["product"],
              "variant": r["variant"], "price": r["price"]} for r in conn.execute(
        "SELECT sl.unit_id, sl.price, p.name AS product, o.variant FROM shipment_lines sl "
        "JOIN units u ON u.id = sl.unit_id JOIN orders o ON o.id = u.order_id "
        "JOIN products p ON p.id = o.product_id WHERE sl.shipment_id = ? "
        "ORDER BY o.number, u.seq", (shipment_id,))]
    total = shipment_total(conn, shipment_id)
    shop = conn.execute("SELECT value FROM settings WHERE key = 'shop_name'").fetchone()
    return {"id": sh["id"], "date": sh["created_at"][:10], "customer": sh["customer"],
            "city": sh["city"], "payment": sh["payment"], "paid": sh["paid_amount"],
            "total": total, "owed": total - sh["paid_amount"], "lines": lines,
            "currency": currency(conn), "shop": shop["value"] if shop else ""}
