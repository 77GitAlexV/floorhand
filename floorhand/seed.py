"""Fictional furniture workshop for the demo: 28 workers on 5 stages, 12 products,
~200 orders and two weeks of history simulated day by day.

Every name and number here is made up. Run: python -m floorhand.seed
"""
import random
import sys
from datetime import date, datetime, time, timedelta

from . import db
from .domain import shop_tz

TZ = "Asia/Bangkok"
CURRENCY = "$"

STAGES = [  # code, en, ru, capacity per working day
    ("cut", "Cutting", "Раскрой", 30),
    ("sew", "Sewing", "Шитьё", 26),
    ("uph", "Upholstery", "Обивка", 21),
    ("asm", "Assembly", "Сборка", 24),
    ("pack", "Packing", "Упаковка", 30),
]

WORKERS = {
    "cut": ["Oleg", "Dmitri", "Sam", "Viktor"],
    "sew": ["Anna", "Irina", "Maria", "Lena", "Olga", "Nadia", "Rosa"],
    "uph": ["Ivan", "Pavel", "Marco", "Yuri", "Tom", "Igor", "Nikita", "Leo"],
    "asm": ["Peter", "Artem", "Ben", "Gleb", "Kirill", "Max"],
    "pack": ["Vera", "Zoe", "Sonya"],
}
OFFICE = [("Kate", "manager"), ("Alex", "owner")]

PRODUCTS = [  # name, kind, price, rates per stage (None = rate not set yet)
    ("Cozy armchair", "armchair", 380, [4, 9, 12, 6, 2]),
    ("Lord armchair", "armchair", 520, [5, 11, 15, 8, 2]),
    ("Lord rocking chair", "rocking chair", 610, [5, 11, 15, 10, 3]),
    ("Nordic lounge chair", "armchair", 450, [4, 10, 13, 7, 2]),
    ("Bruno chair", "chair", 160, [2, 4, 6, 3, 1]),
    ("Round pouf", "pouf", 120, [2, 3, 5, 2, 1]),
    ("Square pouf", "pouf", 110, [2, 3, 5, 2, 1]),
    ("Footstool", "pouf", 90, [1, 3, 4, 2, 1]),
    ("Sofa Oslo 3-seat", "sofa", 1450, [12, 25, 38, 20, 5]),
    ("Sofa Oslo 2-seat", "sofa", 1100, [10, 20, 30, 16, 4]),
    ("Daybed Lina", "daybed", 890, [8, 16, 22, 12, 4]),
    ("Kids armchair Bunny", "armchair", 210, [3, 6, None, 4, 1]),
]

FABRICS = ["velvet beige", "velvet graphite", "boucle milk", "linen sand",
           "velvet emerald", "chenille grey", "boucle ash"]
FRAMES = ["walnut", "oak", "black", "natural"]

CUSTOMERS = [
    ("Home & Co", "Austin"), ("Nest Interiors", "Denver"), ("Casa Verde", "Miami"),
    ("Urban Loft", "Chicago"), ("Pine Street Store", "Portland"), ("Hygge House", "Seattle"),
    ("The Reading Corner", "Boston"), ("Baxter Hotels", "Nashville"),
    ("Sunny Cafe Group", "San Diego"), ("Maple Kids", "Columbus"),
    ("Willow Design", "Atlanta"), ("North Shore Rentals", "Duluth"),
    ("Studio Blanc", "New York"), ("Field & Fern", "Raleigh"), ("Olive Market", "Phoenix"),
]

N_ORDERS = 200
HISTORY_DAYS = 16


def _is_workday(d: date) -> bool:
    return d.weekday() < 5


def build(conn, rnd: random.Random | None = None, today: date | None = None) -> dict:
    rnd = rnd or random.Random(26)
    db.init(conn)
    if conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]:
        raise SystemExit("Database already has data. Delete it first.")

    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)",
                     [("tz", TZ), ("currency", CURRENCY), ("shop_name", "Birch & Thread")])
    for seq, (code, en, ru, cap) in enumerate(STAGES, 1):
        conn.execute("INSERT INTO stages (code, name_en, name_ru, seq, capacity) "
                     "VALUES (?, ?, ?, ?, ?)", (code, en, ru, seq, cap))
    stage_ids = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM stages")}
    stage_order = [stage_ids[s[0]] for s in STAGES]

    workers_by_stage: dict[int, list[int]] = {}
    for code, names in WORKERS.items():
        for name in names:
            cur = conn.execute("INSERT INTO people (name, role, stage_id) VALUES (?, 'worker', ?)",
                               (name, stage_ids[code]))
            workers_by_stage.setdefault(stage_ids[code], []).append(cur.lastrowid)
    for name, role in OFFICE:
        conn.execute("INSERT INTO people (name, role) VALUES (?, ?)", (name, role))

    product_ids = []
    for name, kind, price, rates in PRODUCTS:
        cur = conn.execute("INSERT INTO products (name, kind, price) VALUES (?, ?, ?)",
                           (name, kind, price))
        product_ids.append((cur.lastrowid, kind))
        for sid, amount in zip(stage_order, rates):
            if amount is not None:
                conn.execute("INSERT INTO rates VALUES (?, ?, ?)", (cur.lastrowid, sid, amount))

    customer_ids = []
    for name, city in CUSTOMERS:
        customer_ids.append(conn.execute("INSERT INTO customers (name, city) VALUES (?, ?)",
                                         (name, city)).lastrowid)

    tz = shop_tz(conn)
    today = today or datetime.now(tz).date()
    start = today - timedelta(days=HISTORY_DAYS)
    days = [start + timedelta(days=i) for i in range(HISTORY_DAYS + 1)]

    # --- orders ---
    units_by_day: dict[date, list[int]] = {d: [] for d in days}
    unit_info: dict[int, dict] = {}
    weights = [3 if _is_workday(d) else 1 for d in days]
    for number in range(101, 101 + N_ORDERS):
        d = rnd.choices(days, weights)[0]
        pid, kind = rnd.choice(product_ids)
        qty = rnd.choice([1, 1, 1, 2, 2, 3, 4]) if kind in ("pouf", "chair") else \
            rnd.choice([1, 1, 1, 1, 2])
        to_stock = rnd.random() < 0.15
        cust = None if to_stock else rnd.choice(customer_ids)
        due = None if to_stock else (d + timedelta(days=rnd.randint(5, 11))).isoformat()
        variant = f"{rnd.choice(FABRICS)} · {rnd.choice(FRAMES)}"
        created = datetime.combine(d, time(rnd.randint(8, 17), rnd.randint(0, 59)), tz)
        oid = conn.execute(
            "INSERT INTO orders (number, product_id, variant, qty, customer_id, due_date, "
            "source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (number, pid, variant, qty, cust, due,
             rnd.choice(["manual", "sheet", "email", "chat"]),
             created.isoformat(timespec="seconds"))).lastrowid
        for seq in range(1, qty + 1):
            uid = conn.execute("INSERT INTO units (order_id, seq) VALUES (?, ?)",
                               (oid, seq)).lastrowid
            units_by_day[d].append(uid)
            unit_info[uid] = {"order": oid, "customer": cust, "due": due or "9999",
                              "stage": 0, "moved_on": None}

    # --- simulate the floor day by day ---
    caps = {stage_ids[s[0]]: s[3] for s in STAGES}
    open_units: list[int] = []
    finished: dict[int, date] = {}
    for d in days:
        open_units.extend(units_by_day[d])
        if not _is_workday(d):
            continue
        share = 0.35 if d == today else rnd.uniform(0.85, 1.05)  # today: morning only
        # Later stages first, so a unit moves at most one stage per day.
        for idx in reversed(range(len(stage_order))):
            sid = stage_order[idx]
            # Roughly first come, first served: a real floor does not sort by due date.
            queue = sorted((u for u in open_units
                            if unit_info[u]["stage"] == idx and unit_info[u]["moved_on"] != d),
                           key=lambda u: u + rnd.randint(0, 40))
            for uid in queue[:int(caps[sid] * share)]:
                who = rnd.choice(workers_by_stage[sid])
                at = datetime.combine(d, time(rnd.randint(8, 16), rnd.randint(0, 59)), tz)
                conn.execute(
                    "INSERT INTO work_events (kind, unit_id, stage_id, person_id, work_date, "
                    "created_at, method) VALUES ('done', ?, ?, ?, ?, ?, ?)",
                    (uid, sid, who, d.isoformat(), at.isoformat(timespec="seconds"),
                     rnd.choices(["button", "scan", "number"], [6, 3, 1])[0]))
                info = unit_info[uid]
                info["stage"] += 1
                info["moved_on"] = d
                if info["stage"] == len(stage_order):
                    open_units.remove(uid)
                    finished[uid] = d

    # --- ship complete customer orders a day or two after they are ready ---
    office = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    by_order: dict[int, list[int]] = {}
    for uid, info in unit_info.items():
        by_order.setdefault(info["order"], []).append(uid)
    for oid, uids in by_order.items():
        cust = unit_info[uids[0]]["customer"]
        if cust is None or not all(u in finished for u in uids):
            continue
        ready = max(finished[u] for u in uids)
        ship_day = ready + timedelta(days=rnd.randint(1, 2))
        if ship_day >= today:
            continue
        at = datetime.combine(ship_day, time(rnd.randint(10, 17)), tz)
        payment = rnd.choices(["paid", "credit", "partial"], [6, 3, 1])[0]
        sid = conn.execute("INSERT INTO shipments (customer_id, payment, paid_amount, "
                           "created_at, created_by) VALUES (?, ?, 0, ?, ?)",
                           (cust, payment, at.isoformat(timespec="seconds"), office)).lastrowid
        for u in uids:
            conn.execute("INSERT INTO shipment_lines (shipment_id, unit_id, price) "
                         "SELECT ?, ?, p.price FROM units un JOIN orders o ON o.id = un.order_id "
                         "JOIN products p ON p.id = o.product_id WHERE un.id = ?", (sid, u, u))
        total = conn.execute("SELECT SUM(price) FROM shipment_lines WHERE shipment_id = ?",
                             (sid,)).fetchone()[0]
        paid = {"paid": total, "credit": 0, "partial": total // 2}[payment]
        conn.execute("UPDATE shipments SET paid_amount = ? WHERE id = ?", (paid, sid))

    # --- hand out part of each queue so workers have "assigned to me" cards ---
    for idx, sid in enumerate(stage_order):
        queue = sorted((u for u in open_units if unit_info[u]["stage"] == idx),
                       key=lambda u: (unit_info[u]["due"], u))
        for i, uid in enumerate(queue[:len(workers_by_stage[sid]) * 3]):
            who = workers_by_stage[sid][i % len(workers_by_stage[sid])]
            conn.execute("INSERT INTO assignments VALUES (?, ?, ?)", (uid, sid, who))

    conn.commit()
    return {"orders": N_ORDERS, "units": len(unit_info),
            "events": conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0],
            "shipments": conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]}


if __name__ == "__main__":
    path = db.db_path()
    if "--fresh" in sys.argv:
        for f in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            f.unlink(missing_ok=True)
    conn = db.connect(path)
    print(build(conn))
