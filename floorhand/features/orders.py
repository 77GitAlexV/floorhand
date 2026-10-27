"""Order depth: what makes the office trust the system.

Lessons carried over from running a real workshop ERP:
- Orders get edited almost as often as they get created. Every field can be
  changed at any time, and every change is a visible record (who, when, field,
  was, now, why). A refusal says why and what to do instead.
- "Already worked on it - don't touch" guards turned orders into hybrids of two
  versions. Here nothing is silently skipped: an edit that touches work already
  done asks for a decision (send back for rework / keep the work / move the
  started items to stock) and records it.
- An item and an order are different things. A cancelled order frees its
  finished items "to stock without customer"; the system offers them to the
  next similar order instead of leaving it to someone's memory.
- Rework is per item, with a reason and an explicit "who pays". It is written
  as compensating 'undo' records (work_events stays append-only); only the
  worker who made the mistake loses the pay, and only when they pay.
"""
import sqlite3

import base64
import binascii

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from .. import domain, intake, reconcile
from ..domain import DomainError
from ..web import Session, get_conn, office

SCHEMA = """
CREATE TABLE IF NOT EXISTS order_changes (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,            -- no FK: an emptied stock order may be removed
    number     INTEGER NOT NULL,            -- order number at the time, for reading later
    person_id  INTEGER NOT NULL REFERENCES people(id),
    at         TEXT NOT NULL,
    field      TEXT NOT NULL,
    old        TEXT,
    new        TEXT,
    reason     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'card'  -- card, sheet, intake, system
);
CREATE INDEX IF NOT EXISTS ix_order_changes ON order_changes(order_id, id);
CREATE TRIGGER IF NOT EXISTS order_changes_no_update
BEFORE UPDATE ON order_changes
BEGIN SELECT RAISE(ABORT, 'order_changes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_changes_no_delete
BEFORE DELETE ON order_changes
BEGIN SELECT RAISE(ABORT, 'order_changes is append-only'); END;

CREATE TABLE IF NOT EXISTS order_comments (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    person_id  INTEGER NOT NULL REFERENCES people(id),
    at         TEXT NOT NULL,
    text       TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS order_comments_no_update
BEFORE UPDATE ON order_comments
BEGIN SELECT RAISE(ABORT, 'order_comments is append-only'); END;

-- What the core orders table does not hold: the note for the shop, and the
-- link to the manager's sheet (book + the manager's own order number).
CREATE TABLE IF NOT EXISTS order_extra (
    order_id   INTEGER PRIMARY KEY REFERENCES orders(id),
    note       TEXT NOT NULL DEFAULT '',
    ext_book   TEXT,
    ext_ref    TEXT,
    fingerprint TEXT                       -- customer|product|variant at last sheet sync
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_order_ext ON order_extra(ext_book, ext_ref)
    WHERE ext_book IS NOT NULL AND ext_ref IS NOT NULL;

-- One row per item sent back. The undo records it wrote carry
-- idem_key 'rework:<id>:<event id>' and method 'rework:<payer>'.
-- Photos sent to intake (order forms, chat screenshots), kept next to the
-- proposal so the person can type from them when the model is off.
CREATE TABLE IF NOT EXISTS intake_photos (
    id          INTEGER PRIMARY KEY,
    mime        TEXT NOT NULL,
    data        BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    person_id   INTEGER REFERENCES people(id)
);

CREATE TABLE IF NOT EXISTS reworks (
    id          INTEGER PRIMARY KEY,
    unit_id     INTEGER NOT NULL REFERENCES units(id),
    to_stage_id INTEGER NOT NULL REFERENCES stages(id),
    reason      TEXT NOT NULL,
    cause       TEXT NOT NULL,              -- short list, for statistics
    payer       TEXT NOT NULL CHECK (payer IN ('worker', 'shop', 'customer')),
    blamed_id   INTEGER REFERENCES people(id),
    created_by  INTEGER NOT NULL REFERENCES people(id),
    created_at  TEXT NOT NULL,
    idem_key    TEXT UNIQUE
);
"""

PAYERS = ("worker", "shop", "customer")
CAUSES = ("fabric_defect", "sewing_error", "upholstery_error", "assembly_error", "damaged",
          "wrong_size", "customer_changed", "other")
EDITABLE = ("product", "variant", "qty", "customer", "due_date", "note")


# --- small helpers --------------------------------------------------------

def _now(conn) -> str:
    return domain.now(conn).isoformat(timespec="seconds")


def _order(conn, order_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise DomainError("No such order.")
    return row


def _office(conn, person_id: int) -> sqlite3.Row:
    who = domain.person(conn, person_id)
    if who["role"] not in ("manager", "owner"):
        raise DomainError("Only the office can change orders.")
    return who


def extra(conn, order_id: int) -> dict:
    row = conn.execute("SELECT * FROM order_extra WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else {"order_id": order_id, "note": "", "ext_book": None,
                                  "ext_ref": None, "fingerprint": None}


def set_extra(conn, order_id: int, **fields) -> None:
    cur = extra(conn, order_id)
    cur.update(fields)
    conn.execute("INSERT OR REPLACE INTO order_extra (order_id, note, ext_book, ext_ref, "
                 "fingerprint) VALUES (?, ?, ?, ?, ?)",
                 (order_id, cur["note"] or "", cur["ext_book"], cur["ext_ref"],
                  cur["fingerprint"]))


def log(conn, order_id: int, person_id: int, field: str, old, new, reason: str = "",
        source: str = "card") -> None:
    number = conn.execute("SELECT number FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.execute("INSERT INTO order_changes (order_id, number, person_id, at, field, old, new, "
                 "reason, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (order_id, number[0] if number else 0, person_id, _now(conn), field,
                  None if old is None else str(old), None if new is None else str(new),
                  reason or "", source))


def _product_name(conn, product_id) -> str:
    row = conn.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
    return row["name"] if row else "?"


def _customer_name(conn, customer_id) -> str:
    if customer_id is None:
        return "to stock"
    row = conn.execute("SELECT name FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return row["name"] if row else "?"


def units_state(conn, order_id: int) -> list[dict]:
    """Every item of an order: where it is, what is done and by whom."""
    st = domain.stages(conn)
    out = []
    for u in conn.execute("SELECT id, seq FROM units WHERE order_id = ? ORDER BY seq",
                          (order_id,)):
        done = domain.done_stage_ids(conn, u["id"])
        any_events = conn.execute("SELECT 1 FROM work_events WHERE unit_id = ? LIMIT 1",
                                  (u["id"],)).fetchone() is not None
        cur = domain.current_stage(conn, u["id"])
        steps = []
        for s in st:
            if s["id"] in done:
                ev = conn.execute("SELECT e.id, e.work_date, p.name FROM work_events e "
                                  "JOIN people p ON p.id = e.person_id WHERE e.id = ?",
                                  (done[s["id"]],)).fetchone()
                steps.append({"stage_id": s["id"], "name_en": s["name_en"],
                              "name_ru": s["name_ru"], "done": True, "event_id": ev["id"],
                              "by": ev["name"], "date": ev["work_date"]})
            else:
                steps.append({"stage_id": s["id"], "name_en": s["name_en"],
                              "name_ru": s["name_ru"], "done": False})
        out.append({"unit_id": u["id"], "seq": u["seq"],
                    "label": domain.unit_label(conn, u["id"]),
                    "shipped": domain.is_shipped(conn, u["id"]),
                    "started": bool(done), "finished": cur is None,
                    "history": any_events,
                    "at": None if cur is None else {"id": cur["id"], "name_en": cur["name_en"],
                                                    "name_ru": cur["name_ru"]},
                    "steps": steps})
    return out


def _labels(units: list[dict]) -> str:
    return ", ".join(u["label"] for u in units)


def _where(u: dict) -> str:
    if u["shipped"]:
        return "shipped"
    return "finished" if u["finished"] else f"at {u['at']['name_en']}"


# --- moving items between orders -------------------------------------------

def _renumber_target(conn, order_id: int) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM units WHERE order_id = ?",
                        (order_id,)).fetchone()[0]


def split_off(conn, order: sqlite3.Row, unit_ids: list[int], person_id: int, *,
              to_stock: bool, cancelled: bool, reason: str, source: str = "card") -> int:
    """Move some items of an order into a new order of the same product and
    variant: to stock (no customer) or as a cancelled part. Work history moves
    with the items, because it belongs to the item, not to the order."""
    oid = domain.create_order(
        conn, product_id=order["product_id"], qty=len(unit_ids),
        customer_id=None if to_stock else order["customer_id"],
        due_date=None if to_stock else order["due_date"], variant=order["variant"],
        source=f"from #{order['number']}", commit=False)
    # create_order made fresh empty items; replace them with the moved ones.
    conn.execute("DELETE FROM units WHERE order_id = ?", (oid,))
    for i, uid in enumerate(unit_ids, 1):
        conn.execute("UPDATE units SET order_id = ?, seq = ? WHERE id = ?", (oid, i, uid))
    if cancelled:
        conn.execute("UPDATE orders SET cancelled = 1 WHERE id = ?", (oid,))
    left = conn.execute("SELECT COUNT(*) FROM units WHERE order_id = ?",
                        (order["id"],)).fetchone()[0]
    conn.execute("UPDATE orders SET qty = ? WHERE id = ?", (left, order["id"]))
    new_no = conn.execute("SELECT number FROM orders WHERE id = ?", (oid,)).fetchone()[0]
    what = "to stock" if to_stock else ("cancelled" if cancelled else "split")
    log(conn, order["id"], person_id, "items_out", f"{len(unit_ids)} pc",
        f"#{new_no} ({what})", reason, source)
    log(conn, oid, person_id, "items_in", f"#{order['number']}", f"{len(unit_ids)} pc",
        reason, source)
    return oid


def _remove_units(conn, order: sqlite3.Row, units: list[dict], person_id: int,
                  reason: str, source: str) -> list[str]:
    """Drop items that were never started. Items with no history at all are
    deleted; the rare one with only undone marks keeps its history in a
    cancelled part, because work_events cannot lose their item."""
    clean = [u for u in units if not u["history"]]
    kept = [u for u in units if u["history"]]
    for u in clean:
        conn.execute("DELETE FROM assignments WHERE unit_id = ?", (u["unit_id"],))
        conn.execute("DELETE FROM units WHERE id = ?", (u["unit_id"],))
    if kept:
        split_off(conn, order, [u["unit_id"] for u in kept], person_id, to_stock=False,
                  cancelled=True, reason=reason, source=source)
    left = conn.execute("SELECT COUNT(*) FROM units WHERE order_id = ?",
                        (order["id"],)).fetchone()[0]
    conn.execute("UPDATE orders SET qty = ? WHERE id = ?", (left, order["id"]))
    return [u["label"] for u in units]


def _add_units(conn, order_id: int, n: int) -> None:
    top = _renumber_target(conn, order_id)
    conn.executemany("INSERT INTO units (order_id, seq) VALUES (?, ?)",
                     [(order_id, top + i) for i in range(1, n + 1)])
    total = conn.execute("SELECT COUNT(*) FROM units WHERE order_id = ?",
                         (order_id,)).fetchone()[0]
    conn.execute("UPDATE orders SET qty = ? WHERE id = ?", (total, order_id))


# --- edit -----------------------------------------------------------------

def _needs(message: str, choice: str, options: list[dict], **extra_) -> dict:
    """Not a refusal: a decision the office has to make before the edit goes in."""
    return {"ok": False, "needs": choice, "options": options, "message": message, **extra_}


def _resolve_product(conn, value) -> int:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        pid = int(value)
        if conn.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone() is None:
            raise DomainError("Unknown product.")
        return pid
    cat = intake._catalog(conn)
    pid, options = intake._match("product", str(value or ""), cat)
    if pid is None:
        hint = ", ".join(o["label"] for o in options) or "none close"
        raise DomainError(f"No product called “{value}”. Closest: {hint}.")
    return pid


def _resolve_customer(conn, value) -> int | None:
    if value in (None, "", intake.STOCK, "to stock"):
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        cid = int(value)
        if conn.execute("SELECT 1 FROM customers WHERE id = ?", (cid,)).fetchone() is None:
            raise DomainError("Unknown customer.")
        return cid
    if isinstance(value, str) and value.startswith("new:"):
        name = value[4:].strip()
        if not name:
            raise DomainError("Type the new customer's name.")
        return conn.execute("INSERT INTO customers (name) VALUES (?)", (name,)).lastrowid
    cat = intake._catalog(conn)
    cid, options = intake._match("customer", str(value), cat)
    if cid is None:
        hint = ", ".join(o["label"] for o in options) or "none close"
        raise DomainError(f"No customer called “{value}”. Closest: {hint}. "
                          "To add one, choose “New customer”.")
    return cid


def _check_variant(conn, value: str) -> str:
    """Fabric and frame from the catalog, written 'fabric · frame'."""
    value = (value or "").strip()
    if not value:
        return ""
    cat = intake._catalog(conn)
    v = intake._variant(value.replace("·", " "), cat)
    if v["fabric"] is None and (v["fabric_options"] or not v["frame"]):
        import difflib
        close = difflib.get_close_matches(value.split("·")[0].strip().lower(),
                                          cat["fabrics"], n=3, cutoff=0.4)
        opts = v["fabric_options"] or close
        raise DomainError(f"No fabric “{value}” in the catalog."
                          + (f" Closest: {', '.join(opts)}." if opts else "")
                          + " Add a fabric by writing “add fabric …” in the catalog box.")
    return " · ".join(x for x in (v["fabric"], v["frame"]) if x)


def _valid_date(value) -> str | None:
    if value in (None, ""):
        return None
    from datetime import date
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise DomainError("Date must look like 2026-11-05.")


def edit(conn, person_id: int, order_id: int, field: str, value, *, reason: str = "",
         started: str | None = None, rework_stage_id: int | None = None,
         payer: str | None = None, keep_work: bool = False, source: str = "card") -> dict:
    """Change one field of an order. Safe changes go straight in. A change that
    touches work already done returns `needs` with the choices; it never
    silently keeps or drops work."""
    _office(conn, person_id)
    if field not in EDITABLE:
        raise DomainError(f"“{field}” cannot be edited. Editable: {', '.join(EDITABLE)}.")
    o = _order(conn, order_id)
    if o["cancelled"] and field != "note":
        raise DomainError(f"#{o['number']} is cancelled. Only the note can change.")
    units = units_state(conn, order_id)
    shipped = [u for u in units if u["shipped"]]
    live = [u for u in units if not u["shipped"]]
    began = [u for u in live if u["started"]]
    reason = (reason or "").strip()

    if field == "note":
        old = extra(conn, order_id)["note"]
        new = str(value or "").strip()[:2000]
        if new == old:
            return {"ok": True, "changed": False, "message": "Already so. Nothing changed."}
        set_extra(conn, order_id, note=new)
        log(conn, order_id, person_id, "note", old, new, reason, source)
        conn.commit()
        return {"ok": True, "changed": True, "message": f"Note saved on #{o['number']}."}

    if field == "due_date":
        new = _valid_date(value)
        if new == o["due_date"]:
            return {"ok": True, "changed": False, "message": "Already so. Nothing changed."}
        conn.execute("UPDATE orders SET due_date = ? WHERE id = ?", (new, order_id))
        log(conn, order_id, person_id, "due_date", o["due_date"], new, reason, source)
        conn.commit()
        return {"ok": True, "changed": True,
                "message": f"#{o['number']}: due {new or 'not set'} (was {o['due_date'] or 'not set'})."}

    if field == "customer":
        new = _resolve_customer(conn, value)
        if new == o["customer_id"]:
            return {"ok": True, "changed": False, "message": "Already so. Nothing changed."}
        if shipped:
            raise DomainError(
                f"{_labels(shipped)} already left for {_customer_name(conn, o['customer_id'])}. "
                "The customer of shipped items cannot change. Cancel the rest or make a new order.")
        conn.execute("UPDATE orders SET customer_id = ? WHERE id = ?", (new, order_id))
        log(conn, order_id, person_id, "customer", _customer_name(conn, o["customer_id"]),
            _customer_name(conn, new), reason, source)
        conn.commit()
        warn = " Printed sheets still show the old customer: print them again." if began else ""
        return {"ok": True, "changed": True,
                "message": f"#{o['number']} is now for {_customer_name(conn, new)}.{warn}"}

    if field == "qty":
        try:
            new = int(value)
        except (TypeError, ValueError):
            raise DomainError("Quantity must be a whole number.")
        if not 0 < new <= 500:
            raise DomainError("Quantity must be between 1 and 500.")
        if new == o["qty"]:
            return {"ok": True, "changed": False, "message": "Already so. Nothing changed."}
        if new > o["qty"]:
            _add_units(conn, order_id, new - o["qty"])
            log(conn, order_id, person_id, "qty", o["qty"], new, reason, source)
            conn.commit()
            return {"ok": True, "changed": True,
                    "message": f"#{o['number']}: {o['qty']} → {new} pc. "
                               f"{new - o['qty']} new item(s) wait at the first stage."}
        drop = old_qty = o["qty"]
        drop -= new
        if new < len(shipped):
            raise DomainError(f"{len(shipped)} pc of #{o['number']} are already shipped "
                              f"({_labels(shipped)}). Quantity cannot go below {len(shipped)}.")
        if not reason:
            raise DomainError("Say why the quantity goes down (the customer asked, a mistake …).")
        idle = sorted([u for u in live if not u["started"]],
                      key=lambda u: (u["history"], -u["seq"]))
        take_idle = idle[:drop]
        rest = drop - len(take_idle)
        if rest and started not in ("to_stock", "scrap"):
            cand = sorted(began, key=lambda u: sum(s["done"] for s in u["steps"]))[:rest]
            return _needs(
                f"Only {len(idle)} item(s) of #{o['number']} are not started. {rest} more must "
                f"come from items in work: {', '.join(u['label'] + ' (' + _where(u) + ')' for u in cand)}. "
                "Move them to stock without customer, or scrap them?",
                "started", [{"id": "to_stock", "label": "Move to stock"},
                            {"id": "scrap", "label": "Scrap (cancel)"}])
        removed = _remove_units(conn, o, take_idle, person_id, reason, source) if take_idle else []
        moved = []
        if rest:
            o = _order(conn, order_id)
            cand = sorted([u for u in units_state(conn, order_id)
                           if u["started"] and not u["shipped"]],
                          key=lambda u: sum(s["done"] for s in u["steps"]))[:rest]
            split_off(conn, o, [u["unit_id"] for u in cand], person_id,
                      to_stock=started == "to_stock", cancelled=started == "scrap",
                      reason=reason, source=source)
            moved = [u["label"] for u in cand]
        log(conn, order_id, person_id, "qty", old_qty, new, reason, source)
        conn.commit()
        msg = f"#{_order(conn, order_id)['number']}: now {new} pc."
        if removed:
            msg += f" Removed (not started): {', '.join(removed)}."
        if moved:
            msg += (f" Moved to stock: {', '.join(moved)}." if started == "to_stock"
                    else f" Scrapped: {', '.join(moved)}.")
        return {"ok": True, "changed": True, "message": msg}

    if field == "product":
        new = _resolve_product(conn, value)
        if new == o["product_id"]:
            return {"ok": True, "changed": False, "message": "Already so. Nothing changed."}
        old_name, new_name = _product_name(conn, o["product_id"]), _product_name(conn, new)
        if shipped:
            raise DomainError(f"{_labels(shipped)} already shipped as {old_name}. The product "
                              "cannot change on this order: make a new order for the rest.")
        if not reason:
            raise DomainError("Say why the product changes.")
        if began:
            if started not in ("to_stock", "scrap"):
                return _needs(
                    f"Work is done on {', '.join(u['label'] + ' (' + _where(u) + ')' for u in began)}. "
                    f"Those parts are made for {old_name}, so they cannot become {new_name}. "
                    f"Keep them as {old_name} in stock without customer, or scrap them? "
                    f"#{o['number']} then starts {new_name} from the first stage.",
                    "started", [{"id": "to_stock", "label": f"Keep as {old_name} in stock"},
                                {"id": "scrap", "label": "Scrap (cancel)"}],
                    payer_needed=started is None)
            if started == "scrap" and payer not in PAYERS:
                raise DomainError("Scrapping work needs who pays: worker, shop or customer.")
            n = len(began)
            _add_units(conn, order_id, n)  # first, so the order is never empty
            split_off(conn, o, [u["unit_id"] for u in began], person_id,
                      to_stock=started == "to_stock", cancelled=started == "scrap",
                      reason=f"{reason} (product → {new_name}"
                             + (f", {payer} pays" if payer else "") + ")", source=source)
        conn.execute("UPDATE orders SET product_id = ? WHERE id = ?", (new, order_id))
        log(conn, order_id, person_id, "product", old_name, new_name, reason, source)
        conn.commit()
        tail = ""
        if began:
            tail = (f" {len(began)} started item(s) stay {old_name} and went "
                    + ("to stock." if started == "to_stock" else "to scrap.")
                    + f" {len(began)} new item(s) start at the first stage.")
        return {"ok": True, "changed": True,
                "message": f"#{o['number']} is now {new_name} (was {old_name}).{tail}"}

    # variant
    new = _check_variant(conn, value)
    if new == (o["variant"] or ""):
        return {"ok": True, "changed": False, "message": "Already so. Nothing changed."}
    if shipped:
        raise DomainError(f"{_labels(shipped)} already shipped in {o['variant']}. "
                          "Make a new order for a different fabric.")
    done_somewhere = [u for u in began]
    if done_somewhere and not keep_work and rework_stage_id is None:
        return _needs(
            f"Work is done on {', '.join(u['label'] + ' (' + _where(u) + ')' for u in done_somewhere)} "
            f"in {o['variant'] or 'the old variant'}. Send them back to redo, or keep the work "
            "(when only later stages are affected, e.g. the frame colour)?",
            "rework", [{"id": "keep", "label": "Keep the work"}] +
            [{"id": s["id"], "label": f"Back to {s['name_en']}"} for s in domain.stages(conn)])
    conn.execute("UPDATE orders SET variant = ? WHERE id = ?", (new, order_id))
    log(conn, order_id, person_id, "variant", o["variant"], new,
        reason + (" (work kept)" if keep_work and done_somewhere else ""), source)
    msg = f"#{o['number']}: {new or 'no variant'} (was {o['variant'] or 'none'})."
    back = [u for u in done_somewhere if rework_stage_id is not None and not keep_work
            and any(st_["stage_id"] == rework_stage_id and st_["done"] for st_ in u["steps"])]
    if back:
        res = rework(conn, person_id, [u["unit_id"] for u in back], rework_stage_id,
                     reason=reason or "variant changed", cause="customer_changed",
                     payer=payer or "customer", commit=False)
        msg += " " + res["message"]
    conn.commit()
    return {"ok": True, "changed": True, "message": msg}


# --- rework ---------------------------------------------------------------

def rework(conn, person_id: int, unit_ids: list[int], stage_id: int, *, reason: str,
           cause: str = "other", payer: str, idem_key: str | None = None,
           commit: bool = True) -> dict:
    """Send items back to a stage. Writes one rework row per item and a
    compensating 'undo' for the stage and every later stage already done.
    The worker of the sent-back stage loses that pay only when payer='worker';
    everyone else keeps theirs and is paid again for the redo."""
    _office(conn, person_id)
    if idem_key:
        prev = conn.execute("SELECT * FROM reworks WHERE idem_key LIKE ? || ':%'",
                            (idem_key,)).fetchone()
        if prev is not None:
            return {"ok": True, "already": True,
                    "message": f"Already recorded: {domain.unit_label(conn, prev['unit_id'])} "
                               "sent back. Nothing changed."}
    if payer not in PAYERS:
        raise DomainError("Say who pays for the rework: worker, shop or customer.")
    if cause not in CAUSES:
        raise DomainError("Unknown cause. Pick one from the list.")
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("Say what is wrong (the reason is shown to the worker).")
    if not unit_ids:
        raise DomainError("Pick at least one item.")
    st = conn.execute("SELECT * FROM stages WHERE id = ?", (stage_id,)).fetchone()
    if st is None:
        raise DomainError("No such stage.")
    later = [s for s in domain.stages(conn) if s["seq"] >= st["seq"]]
    when = _now(conn)
    plans = []
    for uid in unit_ids:
        o = domain._unit_order(conn, uid)
        label = domain.unit_label(conn, uid)
        if o["cancelled"]:
            raise DomainError(f"{label} is cancelled.")
        if domain.is_shipped(conn, uid):
            raise DomainError(f"{label} has already been shipped. Rework after shipping is a "
                              "return: make a new order for it.")
        done = domain.done_stage_ids(conn, uid)
        if st["id"] not in done:
            cur = domain.current_stage(conn, uid)
            raise DomainError(f"{label} has not passed {st['name_en']} yet"
                              + (f" (it is at {cur['name_en']})" if cur else "") +
                              ". Nothing to send back.")
        events = [(s, done[s["id"]]) for s in reversed(later) if s["id"] in done]
        culprit = conn.execute("SELECT * FROM work_events WHERE id = ?",
                               (done[st["id"]],)).fetchone()
        if payer == "worker" and conn.execute(
                "SELECT 1 FROM payout_lines WHERE event_id = ?", (culprit["id"],)).fetchone():
            who = domain.person(conn, culprit["person_id"])["name"]
            raise DomainError(f"{who}'s {st['name_en']} on {label} is already paid out. "
                              "Choose “shop pays”, or settle the deduction in the next payout.")
        plans.append((uid, o, label, events, culprit))
    labels = []
    for uid, o, label, events, culprit in plans:
        rid = conn.execute(
            "INSERT INTO reworks (unit_id, to_stage_id, reason, cause, payer, blamed_id, "
            "created_by, created_at, idem_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, st["id"], reason, cause, payer,
             culprit["person_id"] if payer == "worker" else None, person_id, when,
             f"{idem_key}:{uid}" if idem_key else None)).lastrowid
        for s, ev_id in events:
            ev = conn.execute("SELECT * FROM work_events WHERE id = ?", (ev_id,)).fetchone()
            method = f"rework:{payer}" if ev_id == culprit["id"] else \
                ("rework:shop" if payer == "worker" else f"rework:{payer}")
            conn.execute(
                "INSERT INTO work_events (kind, unit_id, stage_id, person_id, work_date, "
                "created_at, method, undoes_id, idem_key) "
                "VALUES ('undo', ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, s["id"], person_id, ev["work_date"], when, method, ev_id,
                 f"rework:{rid}:{ev_id}"))
        log(conn, o["id"], person_id, "rework", label,
            f"back to {st['name_en']}, {payer} pays", reason)
        labels.append(label)
    if commit:
        conn.commit()
    pays = {"worker": "the worker who made it pays", "shop": "the shop pays",
            "customer": "the customer pays"}[payer]
    return {"ok": True, "already": False,
            "message": f"Sent back to {st['name_en']}: {', '.join(labels)}. {pays.capitalize()}."}


# --- cancel and stock -----------------------------------------------------

def cancel(conn, person_id: int, order_id: int, reason: str, unfinished: str = "stop") -> dict:
    """The customer cancelled. Shipped items stay shipped. Finished items go
    to stock without customer. Items still in work stop (or go on to stock
    with unfinished='to_stock'). The order number stays findable."""
    _office(conn, person_id)
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("Say why the order is cancelled.")
    if unfinished not in ("stop", "to_stock"):
        raise DomainError("Items in work either stop or go on to stock.")
    o = _order(conn, order_id)
    if o["cancelled"]:
        return {"ok": True, "already": True, "message": f"#{o['number']} is already cancelled."}
    units = units_state(conn, order_id)
    shipped = [u for u in units if u["shipped"]]
    if len(shipped) == len(units):
        raise DomainError(f"#{o['number']} is fully shipped. Nothing left to cancel.")
    free = [u for u in units if not u["shipped"] and
            (u["finished"] or (unfinished == "to_stock" and u["started"]))]
    dead = [u for u in units if not u["shipped"] and u not in free]
    if o["customer_id"] is None:  # a stock order: nothing to free, it just stops
        free, dead = [], [u for u in units if not u["shipped"]]
    was = _customer_name(conn, o["customer_id"])
    stock_order = None
    if shipped:
        if free:
            stock_order = split_off(conn, o, [u["unit_id"] for u in free], person_id,
                                    to_stock=True, cancelled=False, reason=reason)
        if dead:
            split_off(conn, _order(conn, order_id), [u["unit_id"] for u in dead], person_id,
                      to_stock=False, cancelled=True, reason=reason)
        log(conn, order_id, person_id, "cancel", was, "rest cancelled", reason)
    elif dead:
        if free:
            stock_order = split_off(conn, o, [u["unit_id"] for u in free], person_id,
                                    to_stock=True, cancelled=False, reason=reason)
        conn.execute("UPDATE orders SET cancelled = 1 WHERE id = ?", (order_id,))
        log(conn, order_id, person_id, "cancel", was, "cancelled", reason)
    else:  # every item is finished (or goes on): the whole order becomes stock
        conn.execute("UPDATE orders SET customer_id = NULL, due_date = NULL WHERE id = ?",
                     (order_id,))
        log(conn, order_id, person_id, "cancel", was, "to stock without customer", reason)
        stock_order = order_id
    if stock_order and stock_order != order_id:
        set_extra(conn, stock_order, note=f"Cancelled by {was} (#{o['number']}): {reason}")
    elif stock_order:
        note = extra(conn, order_id)["note"]
        set_extra(conn, order_id, note=(note + "\n" if note else "")
                  + f"Cancelled by {was}: {reason}")
    conn.execute("DELETE FROM assignments WHERE unit_id IN (SELECT u.id FROM units u "
                 "JOIN orders x ON x.id = u.order_id WHERE x.cancelled = 1)")
    conn.commit()
    parts = [f"#{o['number']} cancelled ({reason})."]
    if shipped:
        parts.append(f"Already shipped stays: {_labels(shipped)}.")
    if free:
        n = conn.execute("SELECT number FROM orders WHERE id = ?", (stock_order,)).fetchone()[0]
        parts.append(f"{len(free)} item(s) went to stock without customer as #{n}.")
    if dead:
        parts.append(f"Stopped: {_labels(dead)} — put them aside.")
    offers = stock_offers(conn, stock_order=stock_order) if stock_order else []
    if offers:
        first = offers[0]
        parts.append(f"Order #{first['target_number']} ({first['target_customer']}) "
                     "needs the same: offer it there.")
    return {"ok": True, "already": False, "message": " ".join(parts), "offers": offers,
            "stock_order_id": stock_order}


def _stock_units(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT u.id AS unit_id, o.id AS order_id, o.number, o.product_id, o.variant "
        "FROM units u JOIN orders o ON o.id = u.order_id "
        "WHERE o.customer_id IS NULL AND o.cancelled = 0 "
        "AND u.id NOT IN (SELECT unit_id FROM shipment_lines) ORDER BY o.number, u.seq"
    ).fetchall()


def _variant_score(a: str, b: str) -> int:
    """3 = same fabric and frame, 2 = same fabric, 0 = different fabric.
    The fabric is the part people care about; the frame can often be swapped."""
    pa = [x.strip() for x in (a or "").split("·")]
    pb = [x.strip() for x in (b or "").split("·")]
    if pa == pb:
        return 3
    if pa[0] and pa[0] == pb[0]:
        return 2
    return 0


def stock_offers(conn, *, stock_order: int | None = None, target: int | None = None) -> list[dict]:
    """Stock items that could serve an open customer order: same product,
    same fabric; finished ones first, then the furthest along. Only orders
    with items not started yet can take one (it replaces a not-started item)."""
    st = domain.stages(conn)
    stock = [s for s in _stock_units(conn) if stock_order is None or s["order_id"] == stock_order]
    if not stock:
        return []
    targets = conn.execute(
        "SELECT o.*, c.name AS customer FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE o.cancelled = 0" + (" AND o.id = ?" if target else "") +
        " ORDER BY COALESCE(o.due_date, '9999'), o.number", (target,) if target else ()).fetchall()
    out = []
    for t in targets:
        idle = [u for u in units_state(conn, t["id"]) if not u["started"] and not u["shipped"]]
        if not idle:
            continue
        cands = []
        for s in stock:
            if s["product_id"] != t["product_id"]:
                continue
            score = _variant_score(s["variant"], t["variant"])
            if score < 2:
                continue
            done = domain.done_stage_ids(conn, s["unit_id"])
            cands.append((score, len(done), s))
        cands.sort(key=lambda c: (-c[0], -c[1]))
        for score, ndone, s in cands[:len(idle)]:
            out.append({"stock_unit_id": s["unit_id"],
                        "stock_label": domain.unit_label(conn, s["unit_id"]),
                        "stock_order_id": s["order_id"], "stock_number": s["number"],
                        "progress": f"{ndone}/{len(st)}", "finished": ndone == len(st),
                        "match": "exact" if score == 3 else "same fabric, other frame",
                        "variant": s["variant"],
                        "target_id": t["id"], "target_number": t["number"],
                        "target_customer": t["customer"], "target_due": t["due_date"],
                        "target_variant": t["variant"]})
    out.sort(key=lambda x: (not x["finished"], x["match"] != "exact",
                            x["target_due"] or "9999"))
    return out


def take_from_stock(conn, person_id: int, order_id: int, unit_ids: list[int]) -> dict:
    """Give stock items to a customer order. Each one replaces a not-started
    item of that order, so the quantity stays what the customer asked for."""
    _office(conn, person_id)
    t = _order(conn, order_id)
    if t["cancelled"] or t["customer_id"] is None:
        raise DomainError("Stock items go to an open customer order.")
    if not unit_ids:
        raise DomainError("Pick at least one stock item.")
    idle = sorted([u for u in units_state(conn, order_id)
                   if not u["started"] and not u["shipped"] and not u["history"]],
                  key=lambda u: -u["seq"])
    if len(idle) < len(unit_ids):
        raise DomainError(f"#{t['number']} has only {len(idle)} item(s) not started; "
                          "a stock item replaces one of those.")
    moved = []
    for uid, gone in zip(unit_ids, idle):
        s = domain._unit_order(conn, uid)
        label = domain.unit_label(conn, uid)
        if s["customer_id"] is not None or s["cancelled"] or domain.is_shipped(conn, uid):
            raise DomainError(f"{label} is not a free stock item.")
        if s["product_id"] != t["product_id"]:
            raise DomainError(f"{label} is a {_product_name(conn, s['product_id'])}, "
                              f"#{t['number']} needs {_product_name(conn, t['product_id'])}.")
        if _variant_score(s["variant"], t["variant"]) < 2:
            raise DomainError(f"{label} is in {s['variant']}, #{t['number']} needs {t['variant']}.")
        conn.execute("DELETE FROM assignments WHERE unit_id = ?", (gone["unit_id"],))
        conn.execute("DELETE FROM units WHERE id = ?", (gone["unit_id"],))
        conn.execute("UPDATE units SET order_id = ?, seq = ? WHERE id = ?",
                     (order_id, gone["seq"], uid))
        left = conn.execute("SELECT COUNT(*) FROM units WHERE order_id = ?",
                            (s["id"],)).fetchone()[0]
        log(conn, order_id, person_id, "from_stock", label, domain.unit_label(conn, uid),
            f"from stock order #{s['number']}")
        log(conn, s["id"], person_id, "items_out", label, f"#{t['number']}", "given to order")
        if left:
            conn.execute("UPDATE orders SET qty = ? WHERE id = ?", (left, s["id"]))
        else:  # the stock order is empty now; its story lives in order_changes
            conn.execute("DELETE FROM order_extra WHERE order_id = ?", (s["id"],))
            conn.execute("DELETE FROM orders WHERE id = ?", (s["id"],))
        if s["variant"] != t["variant"]:
            log(conn, order_id, person_id, "note", "", f"{domain.unit_label(conn, uid)} is "
                f"{s['variant']} (order says {t['variant']})", "taken from stock")
        moved.append(domain.unit_label(conn, uid))
    conn.commit()
    return {"ok": True, "message": f"#{t['number']} took from stock: {', '.join(moved)}. "
                                   "Their work so far counts for this order."}


# --- comments and the order story ---------------------------------------

def comment(conn, person_id: int, order_id: int, text: str) -> dict:
    _office(conn, person_id)
    o = _order(conn, order_id)
    text = (text or "").strip()
    if not text:
        raise DomainError("Write the comment first.")
    conn.execute("INSERT INTO order_comments (order_id, person_id, at, text) VALUES (?, ?, ?, ?)",
                 (order_id, person_id, _now(conn), text[:2000]))
    conn.commit()
    return {"ok": True, "message": f"Comment added to #{o['number']}."}


def story(conn, order_id: int) -> dict:
    """Everything the order card needs beyond the core detail: items with
    their stages, what can be edited and why not, changes, comments, reworks,
    and stock items that could serve this order."""
    o = _order(conn, order_id)
    units = units_state(conn, order_id)
    shipped = [u for u in units if u["shipped"]]
    began = [u for u in units if u["started"] and not u["shipped"]]
    locks = {}
    if o["cancelled"]:
        locks = {f: "cancelled" for f in EDITABLE if f != "note"}
    else:
        if shipped:
            for f in ("product", "variant", "customer"):
                locks[f] = f"{len(shipped)} pc already shipped"
        elif began:
            locks["product"] = "work started: started items go to stock or scrap"
            locks["variant"] = "work started: send back or keep the work"
    people = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM people")}
    changes = [dict(r) | {"person": people.get(r["person_id"], "?")} for r in conn.execute(
        "SELECT * FROM order_changes WHERE order_id = ? ORDER BY id DESC", (order_id,))]
    comments = [dict(r) | {"person": people.get(r["person_id"], "?")} for r in conn.execute(
        "SELECT * FROM order_comments WHERE order_id = ? ORDER BY id DESC", (order_id,))]
    reworks = [dict(r) | {"label": domain.unit_label(conn, r["unit_id"]),
                          "by": people.get(r["created_by"], "?"),
                          "blamed": people.get(r["blamed_id"]) if r["blamed_id"] else None}
               for r in conn.execute(
                   "SELECT r.*, s.name_en AS stage, s.name_ru AS stage_ru FROM reworks r "
                   "JOIN stages s ON s.id = r.to_stage_id JOIN units u ON u.id = r.unit_id "
                   "WHERE u.order_id = ? ORDER BY r.id DESC", (order_id,))]
    ex = extra(conn, order_id)
    return {"id": o["id"], "number": o["number"], "note": ex["note"],
            "ext_book": ex["ext_book"], "ext_ref": ex["ext_ref"],
            "product_id": o["product_id"], "customer_id": o["customer_id"],
            "variant": o["variant"], "qty": o["qty"], "due_date": o["due_date"],
            "cancelled": bool(o["cancelled"]), "units": units, "locks": locks,
            "changes": changes, "comments": comments, "reworks": reworks,
            "offers": stock_offers(conn, target=order_id) if o["customer_id"] else [],
            "causes": list(CAUSES), "payers": list(PAYERS)}


# --- demo data ------------------------------------------------------------

def seed(conn, rnd) -> None:
    """A few stories the demo can open: an order with edits and a comment, a
    rework the shop pays for, and a cancelled order whose finished armchair
    sits in stock while a matching order waits for it."""
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    rows = conn.execute("SELECT id FROM orders WHERE customer_id IS NOT NULL AND cancelled = 0 "
                        "ORDER BY number").fetchall()
    if not rows:
        return
    # 1. edits + comment on an order nobody has started
    for r in rows:
        if not any(u["started"] for u in units_state(conn, r["id"])):
            o = _order(conn, r["id"])
            if o["due_date"]:
                from datetime import date, timedelta
                later = (date.fromisoformat(o["due_date"]) + timedelta(days=3)).isoformat()
                edit(conn, manager, o["id"], "due_date", later,
                     reason="Customer asked for a later delivery")
            edit(conn, manager, o["id"], "note", "Customer will call before delivery.")
            comment(conn, manager, o["id"], "Asked for photos of the fabric before sewing.")
            break
    # 2. a rework the shop pays for, on an item that passed Upholstery
    uph = conn.execute("SELECT id FROM stages WHERE code = 'uph'").fetchone()
    if uph:
        for r in rows:
            us = [u for u in units_state(conn, r["id"]) if not u["shipped"]
                  and any(s["stage_id"] == uph["id"] and s["done"] for s in u["steps"])]
            if us:
                rework(conn, manager, [us[0]["unit_id"]], uph["id"],
                       reason="Seam on the left arm is crooked", cause="upholstery_error",
                       payer="shop", idem_key="seed-rework")
                break
    # 3. a cancelled order with a finished item, and an open twin order
    for r in rows:
        us = units_state(conn, r["id"])
        if us and all(u["finished"] and not u["shipped"] for u in us):
            o = _order(conn, r["id"])
            twin = next((t for t in conn.execute(
                "SELECT id FROM orders WHERE product_id = ? AND customer_id IS NOT NULL "
                "AND customer_id != ? AND cancelled = 0 AND id != ? ORDER BY number DESC",
                (o["product_id"], o["customer_id"], o["id"]))
                if not any(u["started"] for u in units_state(conn, t["id"]))), None)
            if twin is None:
                continue
            # demo data: the waiting order asks for the same fabric and frame
            conn.execute("UPDATE orders SET variant = ? WHERE id = ?", (o["variant"], twin["id"]))
            cancel(conn, manager, o["id"], "Customer found a cheaper option")
            break
    # 4. the latest orders came from the manager's sheet (for the re-upload demo)
    reconcile.link_demo_orders(conn)
    conn.commit()


# --- routes ---------------------------------------------------------------

router = APIRouter()


class EditIn(BaseModel):
    field: str
    value: int | str | None = None
    reason: str = ""
    started: str | None = None
    rework_stage_id: int | None = None
    payer: str | None = None
    keep_work: bool = False


@router.post("/api/orders/{order_id}/edit")
def edit_route(order_id: int, body: EditIn, s: Session = Depends(office),
               conn=Depends(get_conn)) -> dict:
    return edit(conn, s.person_id, order_id, body.field, body.value, reason=body.reason,
                started=body.started, rework_stage_id=body.rework_stage_id, payer=body.payer,
                keep_work=body.keep_work)


class ReworkIn(BaseModel):
    unit_ids: list[int]
    stage_id: int
    reason: str
    cause: str = "other"
    payer: str
    idem_key: str | None = None


@router.post("/api/orders/{order_id}/rework")
def rework_route(order_id: int, body: ReworkIn, s: Session = Depends(office),
                 conn=Depends(get_conn)) -> dict:
    mine = {r["id"] for r in conn.execute("SELECT id FROM units WHERE order_id = ?", (order_id,))}
    if not set(body.unit_ids) <= mine:
        raise DomainError("These items are not from this order.")
    return rework(conn, s.person_id, body.unit_ids, body.stage_id, reason=body.reason,
                  cause=body.cause, payer=body.payer, idem_key=body.idem_key)


class CancelIn(BaseModel):
    reason: str
    unfinished: str = "stop"


@router.post("/api/orders/{order_id}/cancel")
def cancel_route(order_id: int, body: CancelIn, s: Session = Depends(office),
                 conn=Depends(get_conn)) -> dict:
    return cancel(conn, s.person_id, order_id, body.reason, body.unfinished)


class TakeIn(BaseModel):
    unit_ids: list[int]


@router.post("/api/orders/{order_id}/take-from-stock")
def take_route(order_id: int, body: TakeIn, s: Session = Depends(office),
               conn=Depends(get_conn)) -> dict:
    return take_from_stock(conn, s.person_id, order_id, body.unit_ids)


class CommentIn(BaseModel):
    text: str


@router.post("/api/orders/{order_id}/comment")
def comment_route(order_id: int, body: CommentIn, s: Session = Depends(office),
                  conn=Depends(get_conn)) -> dict:
    return comment(conn, s.person_id, order_id, body.text)


@router.get("/api/orders/{order_id}/story")
def story_route(order_id: int, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return story(conn, order_id)


@router.get("/api/orders/stock/offers")
def offers_route(s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return {"offers": stock_offers(conn)}


@router.get("/api/orders/catalog/lists")
def catalog_lists(s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    """What the edit form offers: products, customers, fabrics, frames, stages."""
    cat = intake._catalog(conn)
    return {"products": [{"id": i, "label": n} for i, n in sorted(cat["product"].items(),
                                                                   key=lambda x: x[1])],
            "customers": [{"id": i, "label": n} for i, n in sorted(cat["customer"].items(),
                                                                    key=lambda x: x[1])],
            "fabrics": cat["fabrics"], "frames": cat["frames"],
            "stages": [{"id": s_["id"], "name_en": s_["name_en"], "name_ru": s_["name_ru"]}
                       for s_ in domain.stages(conn)],
            "causes": list(CAUSES), "payers": list(PAYERS)}



# --- intake: the person fixes anything, photos -----------------------------

class FixIn(BaseModel):
    idx: int
    field: str
    value: int | str | None = None


@router.post("/api/orders/intake/{action_id}/fix")
def intake_fix(action_id: int, body: FixIn, s: Session = Depends(office),
               conn=Depends(get_conn)) -> dict:
    return intake.fix(conn, s.person_id, action_id, body.idx, body.field, body.value)


@router.post("/api/orders/intake/{action_id}/line")
def intake_add_line(action_id: int, s: Session = Depends(office),
                    conn=Depends(get_conn)) -> dict:
    return intake.add_line(conn, s.person_id, action_id)


@router.post("/api/orders/intake/{action_id}/line/{idx}/remove")
def intake_remove_line(action_id: int, idx: int, s: Session = Depends(office),
                       conn=Depends(get_conn)) -> dict:
    return intake.remove_line(conn, s.person_id, action_id, idx)


class PhotoIn(BaseModel):
    data_b64: str
    mime: str


@router.post("/api/orders/intake/photo")
def intake_photo(body: PhotoIn, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    try:
        data = base64.b64decode(body.data_b64.split(",")[-1], validate=True)
    except (binascii.Error, ValueError):
        raise DomainError("The photo did not arrive whole. Send it again.")
    return intake.propose_photo(conn, s.person_id, data, body.mime)


@router.get("/api/orders/intake/photo/{photo_id}")
def intake_photo_get(photo_id: int, s: Session = Depends(office),
                     conn=Depends(get_conn)) -> Response:
    row = conn.execute("SELECT mime, data FROM intake_photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        raise DomainError("No such photo.")
    return Response(row["data"], media_type=row["mime"])


# --- the manager's sheet ------------------------------------------------------

class SheetIn(BaseModel):
    data_b64: str | None = None
    text: str | None = None
    filename: str = ""
    book: str = ""


@router.post("/api/orders/sheet")
def sheet_propose(body: SheetIn, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    if body.data_b64:
        try:
            data: bytes | str = base64.b64decode(body.data_b64.split(",")[-1], validate=True)
        except (binascii.Error, ValueError):
            raise DomainError("The file did not arrive whole. Send it again.")
    elif body.text:
        data = body.text
    else:
        raise DomainError("Choose the sheet file (.xlsx or .csv).")
    return reconcile.propose(conn, s.person_id, data, body.filename, body.book)


class DecisionsIn(BaseModel):
    decisions: dict[str, str] = {}


@router.post("/api/orders/sheet/{action_id}/apply")
def sheet_apply(action_id: int, body: DecisionsIn, s: Session = Depends(office),
                conn=Depends(get_conn)) -> dict:
    return reconcile.apply(conn, s.person_id, action_id, body.decisions)


@router.get("/api/orders/sheet/demo.xlsx")
def sheet_demo(s: Session = Depends(office), conn=Depends(get_conn)) -> Response:
    """The demo manager's sheet, a day later, with everything a real sheet does."""
    return Response(reconcile.demo_sheet(conn), headers={
        "Content-Disposition": f'attachment; filename="{reconcile.DEMO_BOOK}.xlsx"'},
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
