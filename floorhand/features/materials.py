"""Materials: fabric, foam, frames, fittings - and what the open orders need.

Stock is never stored as a number. On hand = receipts and corrections (stock_moves)
minus consumption, and consumption FOLLOWS the work events: a bill-of-materials
line says at which stage a material is used (fabric at Cutting, foam at
Upholstery, frame and fittings at Assembly), so marking that stage done consumes
it and an undo returns it - no second ledger that can drift from the floor.

Lesson from the real workshop: the fabric warehouse was wrong whenever people
marked work late or by hand; the only numbers that held were the ones derived
from events.
"""
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import domain
from ..domain import LIVE_DONE, DomainError
from ..web import Session, get_conn, office

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK (kind IN ('fabric', 'foam', 'frame', 'fittings', 'other')),
    unit          TEXT NOT NULL,                 -- m, pcs, kg
    reorder_point REAL NOT NULL DEFAULT 0,       -- below this: order more
    lead_days     INTEGER NOT NULL DEFAULT 7,
    supplier      TEXT NOT NULL DEFAULT ''
);

-- Bill of materials. by_variant = 1 means "the fabric named in the order's variant",
-- so one line per product covers every colour.
CREATE TABLE IF NOT EXISTS bom (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    material_id INTEGER REFERENCES materials(id),
    by_variant  INTEGER NOT NULL DEFAULT 0,
    qty         REAL NOT NULL CHECK (qty > 0),
    stage_id    INTEGER NOT NULL REFERENCES stages(id)   -- consumed when this stage is marked
);

-- Receipts and corrections only. Consumption is derived from work events.
CREATE TABLE IF NOT EXISTS stock_moves (
    id          INTEGER PRIMARY KEY,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    kind        TEXT NOT NULL CHECK (kind IN ('receipt', 'adjust')),
    qty         REAL NOT NULL,
    at          TEXT NOT NULL,
    by_person   INTEGER REFERENCES people(id),
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_stock_moves_material ON stock_moves(material_id, at);
"""

router = APIRouter()


# --- lookups --------------------------------------------------------------

def _fabrics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, name FROM materials WHERE kind = 'fabric' "
                        "ORDER BY LENGTH(name) DESC").fetchall()


def fabric_for_variant(conn: sqlite3.Connection, variant: str,
                       fabrics: list | None = None) -> int | None:
    """The fabric material an order's variant names ('velvet beige · walnut')."""
    v = (variant or "").lower()
    for f in fabrics if fabrics is not None else _fabrics(conn):
        if f["name"].lower() in v:
            return f["id"]
    return None


def _bom_lines(conn: sqlite3.Connection) -> dict[int, list[sqlite3.Row]]:
    out: dict[int, list] = {}
    for r in conn.execute("SELECT * FROM bom"):
        out.setdefault(r["product_id"], []).append(r)
    return out


def _unit_material_needs(conn: sqlite3.Connection, product_id: int, variant: str,
                         bom: dict, fabrics: list) -> list[tuple[int, int, float]]:
    """(material_id, stage_id, qty) for one unit of this product and variant."""
    out = []
    for line in bom.get(product_id, []):
        mid = fabric_for_variant(conn, variant, fabrics) if line["by_variant"] else line["material_id"]
        if mid is not None:
            out.append((mid, line["stage_id"], line["qty"]))
    return out


# --- derived stock --------------------------------------------------------

def consumption(conn: sqlite3.Connection) -> dict[int, float]:
    """material_id -> used so far, from live 'done' events at the consuming stage."""
    bom, fabrics = _bom_lines(conn), _fabrics(conn)
    used: dict[int, float] = {}
    for ev in conn.execute(
            f"SELECT e.stage_id, o.product_id, o.variant FROM ({LIVE_DONE}) e "
            "JOIN units u ON u.id = e.unit_id JOIN orders o ON o.id = u.order_id"):
        for mid, sid, qty in _unit_material_needs(conn, ev["product_id"], ev["variant"],
                                                  bom, fabrics):
            if sid == ev["stage_id"]:
                used[mid] = used.get(mid, 0.0) + qty
    return used


def on_hand(conn: sqlite3.Connection) -> dict[int, float]:
    received = {r["material_id"]: r["q"] for r in conn.execute(
        "SELECT material_id, SUM(qty) AS q FROM stock_moves GROUP BY material_id")}
    used = consumption(conn)
    return {m["id"]: round(received.get(m["id"], 0.0) - used.get(m["id"], 0.0), 2)
            for m in conn.execute("SELECT id FROM materials")}


def requirement(conn: sqlite3.Connection) -> dict[int, dict]:
    """What open units still need, per material: total, 'next' (units already
    waiting at the consuming stage, so it is consumed on the next mark) and the
    orders behind the need."""
    bom, fabrics = _bom_lines(conn), _fabrics(conn)
    stage_seq = {s["id"]: s["seq"] for s in domain.stages(conn)}
    done: dict[int, set[int]] = {}
    for r in conn.execute(f"SELECT unit_id, stage_id FROM ({LIVE_DONE})"):
        done.setdefault(r["unit_id"], set()).add(r["stage_id"])
    shipped = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    need: dict[int, dict] = {}
    for u in conn.execute("SELECT u.id, o.id AS order_id, o.number, o.product_id, o.variant "
                          "FROM units u JOIN orders o ON o.id = u.order_id WHERE o.cancelled = 0"):
        if u["id"] in shipped:
            continue
        d = done.get(u["id"], set())
        cur = min((stage_seq[s] for s in stage_seq if s not in d), default=None)
        if cur is None:
            continue
        for mid, sid, qty in _unit_material_needs(conn, u["product_id"], u["variant"],
                                                  bom, fabrics):
            if sid in d:
                continue
            n = need.setdefault(mid, {"total": 0.0, "next": 0.0, "orders": {}})
            n["total"] += qty
            if stage_seq[sid] == cur:
                n["next"] += qty
            n["orders"][u["number"]] = n["orders"].get(u["number"], 0.0) + qty
    for n in need.values():
        n["total"], n["next"] = round(n["total"], 2), round(n["next"], 2)
    return need


def shortages(conn: sqlite3.Connection) -> list[dict]:
    """Materials where what is on hand does not cover the open work."""
    have, need = on_hand(conn), requirement(conn)
    out = []
    for m in conn.execute("SELECT * FROM materials ORDER BY name"):
        n = need.get(m["id"])
        if not n:
            continue
        short = round(n["total"] - have[m["id"]], 2)
        if short <= 0:
            continue
        out.append({"material_id": m["id"], "name": m["name"], "kind": m["kind"],
                    "unit": m["unit"], "on_hand": have[m["id"]], "need": n["total"],
                    "need_next": n["next"], "short": short,
                    "stops_next": have[m["id"]] < n["next"],
                    "lead_days": m["lead_days"], "supplier": m["supplier"],
                    "reorder_point": m["reorder_point"],
                    "orders": sorted(n["orders"].items(), key=lambda kv: -kv[1])})
    out.sort(key=lambda s: (not s["stops_next"], -s["short"]))
    return out


def board(conn: sqlite3.Connection) -> dict:
    have, need = on_hand(conn), requirement(conn)
    rows = []
    for m in conn.execute("SELECT * FROM materials ORDER BY kind, name"):
        n = need.get(m["id"], {"total": 0.0, "next": 0.0})
        rows.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "unit": m["unit"],
                     "on_hand": have[m["id"]], "need": n["total"], "need_next": n["next"],
                     "short": max(0.0, round(n["total"] - have[m["id"]], 2)),
                     "low": have[m["id"]] < m["reorder_point"], "supplier": m["supplier"],
                     "lead_days": m["lead_days"]})
    return {"materials": rows, "shortages": shortages(conn)}


def journal(conn: sqlite3.Connection, material_id: int, limit: int = 40) -> list[dict]:
    """Receipts and corrections from stock_moves, consumption from work events, newest first."""
    m = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    if m is None:
        raise DomainError("No such material.")
    rows = [{"at": r["at"], "kind": r["kind"], "qty": r["qty"], "note": r["note"], "unit": None}
            for r in conn.execute("SELECT * FROM stock_moves WHERE material_id = ?", (material_id,))]
    bom, fabrics = _bom_lines(conn), _fabrics(conn)
    for ev in conn.execute(
            f"SELECT e.stage_id, e.created_at, e.unit_id, o.product_id, o.variant, s.name_en "
            f"FROM ({LIVE_DONE}) e JOIN units u ON u.id = e.unit_id "
            "JOIN orders o ON o.id = u.order_id JOIN stages s ON s.id = e.stage_id"):
        for mid, sid, qty in _unit_material_needs(conn, ev["product_id"], ev["variant"],
                                                  bom, fabrics):
            if mid == material_id and sid == ev["stage_id"]:
                rows.append({"at": ev["created_at"], "kind": "used", "qty": -qty,
                             "note": ev["name_en"], "unit": domain.unit_label(conn, ev["unit_id"])})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:limit]


def receive(conn: sqlite3.Connection, person_id: int, material_id: int, qty: float,
            note: str = "", kind: str = "receipt") -> dict:
    m = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    if m is None:
        raise DomainError("No such material.")
    if kind not in ("receipt", "adjust"):
        raise DomainError("Kind must be receipt or adjust.")
    if kind == "receipt" and not qty > 0:
        raise DomainError("A receipt must be more than 0.")
    if qty == 0:
        raise DomainError("Nothing to record.")
    conn.execute("INSERT INTO stock_moves (material_id, kind, qty, at, by_person, note) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (material_id, kind, qty, domain.now(conn).isoformat(timespec="seconds"),
                  person_id, note.strip()))
    conn.commit()
    now = on_hand(conn)[material_id]
    return {"ok": True, "on_hand": now,
            "message": f"Recorded: {m['name']} {'+' if qty > 0 else ''}{qty:g} {m['unit']}. "
                       f"On hand now {now:g} {m['unit']}."}


# --- routes ---------------------------------------------------------------

@router.get("/api/materials")
def materials_board(s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return board(conn)


@router.get("/api/materials/{material_id}/journal")
def materials_journal(material_id: int, s: Session = Depends(office),
                      conn=Depends(get_conn)) -> dict:
    return {"rows": journal(conn, material_id)}


class ReceiveIn(BaseModel):
    material_id: int
    qty: float
    note: str = ""
    kind: str = "receipt"


@router.post("/api/materials/receive")
def materials_receive(body: ReceiveIn, s: Session = Depends(office),
                      conn=Depends(get_conn)) -> dict:
    return receive(conn, s.person_id, body.material_id, body.qty, body.note, body.kind)


# --- demo data ------------------------------------------------------------

# name, kind, unit, reorder point, lead days, supplier (all fictional)
MATERIALS = [
    ("foam block 40", "foam", "pcs", 20, 5, "Northfoam"),
    ("frame kit armchair", "frame", "pcs", 10, 10, "Oakline Joinery"),
    ("frame kit sofa", "frame", "pcs", 4, 14, "Oakline Joinery"),
    ("chair frame", "frame", "pcs", 8, 10, "Oakline Joinery"),
    ("pouf base", "frame", "pcs", 10, 7, "Oakline Joinery"),
    ("legs set", "fittings", "pcs", 20, 7, "Fitwell Parts"),
    ("zip and thread kit", "fittings", "pcs", 30, 3, "Fitwell Parts"),
    ("staples box", "fittings", "pcs", 5, 3, "Fitwell Parts"),
]
# On hand after the seeded history, as a share of what open work needs. Two run short
# on purpose: one covers a third of the need, one does not even cover the next marks.
FABRIC_STOCK = {"velvet beige": 0.35, "boucle ash": "stops_next"}
# product kind -> (fabric m, foam pcs, frame material, fittings)
BOM_BY_KIND = {
    "armchair": (2.8, 2, "frame kit armchair", ["legs set", "zip and thread kit", "staples box"]),
    "rocking chair": (3.0, 2, "frame kit armchair", ["zip and thread kit", "staples box"]),
    "chair": (1.5, 1, "chair frame", ["zip and thread kit"]),
    "pouf": (1.0, 1, "pouf base", ["zip and thread kit"]),
    "sofa": (7.5, 5, "frame kit sofa", ["legs set", "zip and thread kit", "staples box"]),
    "daybed": (5.5, 4, "frame kit sofa", ["legs set", "zip and thread kit", "staples box"]),
}
FITTING_QTY = {"legs set": 1, "zip and thread kit": 1, "staples box": 0.1}


def seed(conn: sqlite3.Connection, rnd) -> None:
    import json
    fabrics = json.loads(conn.execute("SELECT value FROM settings WHERE key = 'fabrics'")
                         .fetchone()["value"])
    for name in fabrics:
        conn.execute("INSERT INTO materials (name, kind, unit, reorder_point, lead_days, supplier) "
                     "VALUES (?, 'fabric', 'm', 15, 6, 'Loomhouse Textiles')", (name,))
    for name, kind, unit, rp, lead, sup in MATERIALS:
        conn.execute("INSERT INTO materials (name, kind, unit, reorder_point, lead_days, supplier) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (name, kind, unit, rp, lead, sup))
    mid = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM materials")}
    st = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM stages")}
    for p in conn.execute("SELECT id, kind FROM products"):
        fabric_m, foam, frame, fittings = BOM_BY_KIND[p["kind"]]
        # Fabric is written off at Sewing: the real shop learned that the cut cover
        # is what counts - cutting marks were the least reliable ones.
        conn.execute("INSERT INTO bom (product_id, by_variant, qty, stage_id) VALUES (?, 1, ?, ?)",
                     (p["id"], fabric_m, st["sew"]))
        conn.execute("INSERT INTO bom (product_id, material_id, qty, stage_id) VALUES (?, ?, ?, ?)",
                     (p["id"], mid["foam block 40"], foam, st["uph"]))
        conn.execute("INSERT INTO bom (product_id, material_id, qty, stage_id) VALUES (?, ?, 1, ?)",
                     (p["id"], mid[frame], st["asm"]))
        for f in fittings:
            conn.execute("INSERT INTO bom (product_id, material_id, qty, stage_id) "
                         "VALUES (?, ?, ?, ?)", (p["id"], mid[f], FITTING_QTY[f], st["asm"]))
    # Receipts: enough for what the seeded history already consumed, plus a target
    # remainder - spread over a few dated deliveries so the journal looks lived in.
    used, need = consumption(conn), requirement(conn)
    tz = domain.shop_tz(conn)
    today = domain.now(conn).date()
    for m in conn.execute("SELECT * FROM materials"):
        n = need.get(m["id"], {"total": 0.0, "next": 0.0})
        share = FABRIC_STOCK.get(m["name"], 1.4)
        if share == "stops_next":        # covers less than what is already waiting
            target = max(0.0, n["next"] - 1.5)
        else:
            target = max(n["total"] * share, m["reorder_point"] * (0.4 if share < 1 else 1.3))
        total = round(used.get(m["id"], 0.0) + target, 1)
        parts = [0.5, 0.3, 0.2] if total > 20 else [1.0]
        for i, share in enumerate(parts):
            day = today - timedelta(days=18 - i * 6 + rnd.randint(0, 2))
            at = datetime.combine(day, datetime.min.time(), tz).replace(hour=9 + i)
            conn.execute("INSERT INTO stock_moves (material_id, kind, qty, at, by_person, note) "
                         "VALUES (?, 'receipt', ?, ?, NULL, ?)",
                         (m["id"], round(total * share, 1), at.isoformat(timespec="seconds"),
                          f"delivery {m['supplier']}"))
