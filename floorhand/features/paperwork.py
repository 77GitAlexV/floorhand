"""Paperwork: order sheets with a permanent QR per item, delivery notes, e-mail outbox.

Lessons from running a real workshop ERP, each answered here:
- A printed code never changes, or yesterday's sheets stop scanning. The code
  registry outlives the order: a cancelled order's sheet still reads.
- Orders change after printing and the old sheet stays on the floor. Every copy
  keeps a snapshot of what was printed, so a stale sheet is found and named.
- Looking at a print page must not change data. Rendering is read-only; a print
  is recorded only by an explicit POST with an idempotency key.
- Lost and reprinted sheets end up as two papers in use. Every physical print is
  a numbered copy; a newer copy supersedes older ones; a reprint needs a reason.
- Three formulas gave three debts. Totals come only from domain.shipment_total.
- Corrections were made by sending the same document twice. A second e-mail of
  the same note to the same address needs an explicit "resend".
"""
import hashlib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .. import domain
from ..domain import DomainError
from ..web import get_conn, office, session

SCHEMA = """
-- One opaque code per item, forever. Printed on every copy of its sheet.
CREATE TABLE IF NOT EXISTS unit_codes (
    token      TEXT PRIMARY KEY,
    unit_id    INTEGER NOT NULL UNIQUE REFERENCES units(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS print_batches (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES people(id),
    scope      TEXT NOT NULL,           -- new, changed, today, orders, units
    reason     TEXT,                    -- NULL for first prints
    note       TEXT,
    idem_key   TEXT UNIQUE
);

-- The answer given for a batch, so a retried click gets the same receipt.
CREATE TABLE IF NOT EXISTS print_batch_results (
    batch_id INTEGER PRIMARY KEY REFERENCES print_batches(id),
    result   TEXT NOT NULL
);

-- Every physical print of a sheet. The snapshot is what the paper says.
CREATE TABLE IF NOT EXISTS sheet_copies (
    id          INTEGER PRIMARY KEY,
    unit_id     INTEGER NOT NULL REFERENCES units(id),
    copy        INTEGER NOT NULL,
    batch_id    INTEGER NOT NULL REFERENCES print_batches(id),
    printed_at  TEXT NOT NULL,
    printed_by  INTEGER NOT NULL REFERENCES people(id),
    reason      TEXT,                   -- lost, damaged, changed, other; NULL for copy 1
    snapshot    TEXT NOT NULL,          -- JSON
    fingerprint TEXT NOT NULL,
    UNIQUE (unit_id, copy)
);
CREATE INDEX IF NOT EXISTS ix_sheet_copies_batch ON sheet_copies(batch_id);

CREATE TABLE IF NOT EXISTS sheet_voids (
    id        INTEGER PRIMARY KEY,
    unit_id   INTEGER NOT NULL REFERENCES units(id),
    copy      INTEGER NOT NULL,
    reason    TEXT NOT NULL,
    voided_at TEXT NOT NULL,
    voided_by INTEGER NOT NULL REFERENCES people(id),
    UNIQUE (unit_id, copy)
);

CREATE TABLE IF NOT EXISTS customer_contacts (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id),
    email       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Everything sent outside goes here first (rule 8). Content is frozen once
-- written; only status, error and sent_at move.
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,      -- delivery_note
    ref_id          INTEGER NOT NULL,   -- shipment id
    to_addr         TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    attachment_name TEXT,
    attachment_html TEXT,
    status          TEXT NOT NULL CHECK (status IN ('draft', 'sent', 'failed')),
    error           TEXT,
    created_at      TEXT NOT NULL,
    created_by      INTEGER NOT NULL REFERENCES people(id),
    sent_at         TEXT,
    idem_key        TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_outbox_ref ON outbox(kind, ref_id);

CREATE TRIGGER IF NOT EXISTS outbox_no_delete BEFORE DELETE ON outbox
BEGIN SELECT RAISE(ABORT, 'outbox is permanent'); END;

CREATE TRIGGER IF NOT EXISTS outbox_frozen BEFORE UPDATE ON outbox
WHEN NEW.kind IS NOT OLD.kind OR NEW.ref_id IS NOT OLD.ref_id
  OR NEW.to_addr IS NOT OLD.to_addr OR NEW.subject IS NOT OLD.subject
  OR NEW.body IS NOT OLD.body OR NEW.attachment_name IS NOT OLD.attachment_name
  OR NEW.attachment_html IS NOT OLD.attachment_html OR NEW.created_at IS NOT OLD.created_at
  OR NEW.created_by IS NOT OLD.created_by OR NEW.idem_key IS NOT OLD.idem_key
  OR NEW.id IS NOT OLD.id
BEGIN SELECT RAISE(ABORT, 'outbox content is frozen'); END;
"""
for _table, _msg in (("unit_codes", "unit codes never change"),
                     ("sheet_copies", "sheet copies are append-only"),
                     ("sheet_voids", "sheet voids are append-only"),
                     ("print_batch_results", "print receipts are append-only")):
    for _op in ("UPDATE", "DELETE"):
        SCHEMA += (f"CREATE TRIGGER IF NOT EXISTS {_table}_no_{_op.lower()} "
                   f"BEFORE {_op} ON {_table} BEGIN SELECT RAISE(ABORT, '{_msg}'); END;\n")

# No 0/O, 1/I/L: the code is also typed by hand from a crumpled sheet.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
TOKEN_LEN = 8
SCOPES = ("new", "changed", "today", "orders", "units")
REASONS = ("lost", "damaged", "changed", "other")
# What makes a sheet wrong if it changes after printing.
FIELDS = ("product_id", "variant", "customer_id", "due_date", "qty")
# field -> (word for people, key whose value people recognise)
CHANGE_WORDS = {"product_id": ("product", "product"), "variant": ("fabric", "variant"),
                "customer_id": ("customer", "customer"), "due_date": ("due date", "due_date"),
                "qty": ("qty", "qty")}
CHANGE_WORDS_RU = {"product": "изделие", "fabric": "ткань", "customer": "заказчик",
                   "due date": "срок", "qty": "кол-во"}

_SEND_LOCK = threading.Lock()  # one SMTP conversation at a time: no double sends


# --- small helpers --------------------------------------------------------

def _stamp(conn) -> str:
    return domain.now(conn).isoformat(timespec="seconds")


@contextmanager
def _writing(conn):
    """One writer at a time. Copy numbers and idempotency checks read before
    they write, so the read happens under the write lock."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def require_office(conn, person_id: int, what: str = "prints sheets") -> None:
    if domain.person(conn, person_id)["role"] not in ("manager", "owner"):
        raise DomainError(f"Only the office {what}.")


def _ids_sql(ids) -> str:
    return ",".join(str(int(i)) for i in ids)


# --- codes ----------------------------------------------------------------

def _new_token() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LEN))


def ensure_codes(conn, unit_ids) -> dict[int, str]:
    """unit_id -> token. Creates missing ones; an existing token is never touched."""
    out = {}
    for uid in dict.fromkeys(unit_ids):
        row = conn.execute("SELECT token FROM unit_codes WHERE unit_id = ?", (uid,)).fetchone()
        if row:
            out[uid] = row["token"]
            continue
        if not conn.execute("SELECT 1 FROM units WHERE id = ?", (uid,)).fetchone():
            raise DomainError("No such item. Pick an existing order.")
        while uid not in out:
            token = _new_token()
            try:
                conn.execute("INSERT INTO unit_codes (token, unit_id, created_at) VALUES (?, ?, ?)",
                             (token, uid, _stamp(conn)))
                out[uid] = token
            except sqlite3.IntegrityError:  # token taken by another item: draw again
                continue
    return out


def format_code(token: str) -> str:
    return f"{token[:4]}-{token[4:]}"


def code_url(token: str, copy: int | None = None, base: str = "") -> str:
    return f"{base.rstrip('/')}/u/{token}" + (f"/{copy}" if copy is not None else "")


def parse_code(text) -> tuple[str, int | None]:
    """(token, copy) from whatever a scanner or a person gives us: a full URL,
    '/u/TOKEN/2', 'k7qm-2hxp', 'K7QM 2HXP.2'."""
    value = str(text or "").strip().upper()
    if "://" in value:
        value = urlsplit(value).path
    value = re.sub(r"[\s-]", "", value)
    value = re.sub(r"^/?U/", "", value).rstrip("/")
    match = re.fullmatch(f"([{ALPHABET}]{{{TOKEN_LEN}}})(?:[/.]([0-9]{{1,4}}))?", value)
    if not match:
        raise DomainError("This is not a Floorhand sheet code. "
                          "Type the order number from the sheet instead.")
    return match[1], int(match[2]) if match[2] else None


# --- what a sheet says and whether it is still right ----------------------

UNIT_SQL = """
    SELECT u.id AS unit_id, u.seq, o.id AS order_id, o.number AS order_number,
           o.product_id, p.name AS product, o.variant, o.customer_id,
           c.name AS customer, c.city, o.due_date, o.qty, o.cancelled,
           o.created_at AS order_created
    FROM units u JOIN orders o ON o.id = u.order_id
    JOIN products p ON p.id = o.product_id
    LEFT JOIN customers c ON c.id = o.customer_id
"""
SNAP_KEYS = ("product_id", "product", "variant", "customer_id", "customer", "city",
             "due_date", "qty", "order_number", "seq")


def _label(row) -> str:
    # Same rule as domain.unit_label, computed from a row we already have.
    return f"#{row['order_number']}" if row["qty"] == 1 else f"#{row['order_number']}-{row['seq']}"


def snapshot(conn, unit_id: int) -> dict:
    row = conn.execute(UNIT_SQL + " WHERE u.id = ?", (unit_id,)).fetchone()
    if row is None:
        raise DomainError("No such item. Pick an existing order.")
    return {k: row[k] for k in SNAP_KEYS}


def fingerprint(snap: dict) -> str:
    canon = json.dumps({k: snap.get(k) for k in FIELDS}, sort_keys=True,
                       ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(canon.encode()).hexdigest()


def _changes(old: dict, now: dict) -> list[dict]:
    out = []
    for key, (word, shown) in CHANGE_WORDS.items():
        if old.get(key) != now.get(key):
            out.append({"field": word, "field_ru": CHANGE_WORDS_RU[word],
                        "was": old.get(shown), "now": now.get(shown)})
    return out


def states(conn, unit_ids=None) -> dict[int, dict]:
    """Paper state of many items in a handful of queries (the floor has thousands)."""
    where = f" WHERE u.id IN ({_ids_sql(unit_ids)})" if unit_ids is not None else ""
    if unit_ids is not None and not unit_ids:
        return {}
    rows = conn.execute(UNIT_SQL + where + " ORDER BY o.number, u.seq").fetchall()
    latest = {r["unit_id"]: r for r in conn.execute(
        "SELECT sc.* FROM sheet_copies sc WHERE sc.copy = "
        "(SELECT MAX(copy) FROM sheet_copies x WHERE x.unit_id = sc.unit_id)")}
    voids = {(r["unit_id"], r["copy"]): r["reason"] for r in conn.execute(
        "SELECT unit_id, copy, reason FROM sheet_voids")}
    shipped = {r[0] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    n_stages = len(domain.stages(conn))
    done = {r[0]: r[1] for r in conn.execute(
        f"SELECT unit_id, COUNT(DISTINCT stage_id) FROM ({domain.LIVE_DONE}) GROUP BY unit_id")}
    out = {}
    for row in rows:
        uid = row["unit_id"]
        snap = {k: row[k] for k in SNAP_KEYS}
        last = latest.get(uid)
        printed_snap = json.loads(last["snapshot"]) if last else None
        stale = bool(last and fingerprint(snap) != last["fingerprint"])
        void_reason = voids.get((uid, last["copy"])) if last else None
        cancelled, is_shipped = bool(row["cancelled"]), uid in shipped
        finished = done.get(uid, 0) >= n_stages
        out[uid] = {
            "unit_id": uid, "order_id": row["order_id"], "label": _label(row),
            "snapshot": snap, "order_created": row["order_created"],
            "printed": last is not None,
            "latest_copy": last["copy"] if last else None,
            "latest_printed_at": last["printed_at"] if last else None,
            "latest_reason": last["reason"] if last else None,
            "voided": void_reason is not None, "void_reason": void_reason,
            "stale": stale, "changes": _changes(printed_snap, snap) if stale else [],
            "cancelled": cancelled, "shipped": is_shipped, "finished": finished,
            "live": not (cancelled or is_shipped or finished),
        }
    return out


def sheet_state(conn, unit_id: int) -> dict:
    st = states(conn, [unit_id]).get(unit_id)
    if st is None:
        raise DomainError("No such item. Pick an existing order.")
    return st


def units_for_scope(conn, scope: str, order_ids=None, unit_ids=None) -> list[int]:
    """Which items a print button means. Automatic scopes take live items only;
    explicit orders/units take everything so the answer can say why one is skipped."""
    if scope not in SCOPES:
        raise DomainError("Choose what to print: new, changed, today, orders or units.")
    if scope == "units":
        found = states(conn, list(unit_ids or []))
        missing = [u for u in unit_ids or [] if u not in found]
        if missing or not unit_ids:
            raise DomainError("No such item. Pick items from an existing order.")
        return list(dict.fromkeys(unit_ids))
    if scope == "orders":
        if not order_ids:
            raise DomainError("Pick at least one order.")
        rows = conn.execute(f"SELECT o.id, u.id AS uid FROM orders o JOIN units u ON u.order_id = o.id "
                            f"WHERE o.id IN ({_ids_sql(order_ids)}) ORDER BY o.number, u.seq").fetchall()
        known = {r["id"] for r in rows}
        if any(o not in known for o in order_ids):
            raise DomainError("No such order. Check the order numbers.")
        return [r["uid"] for r in rows]
    day = domain.today(conn)
    out = []
    for uid, st in states(conn).items():
        if not st["live"]:
            continue
        if ((scope == "new" and not st["printed"])
                or (scope == "changed" and st["stale"] and not st["voided"])
                or (scope == "today" and st["order_created"][:10] == day)):
            out.append(uid)
    return out


# --- printing -------------------------------------------------------------

def _skip_reason(st: dict, reason: str | None) -> tuple[str, str] | None:
    """(kind, words) when this item gets no new copy."""
    label = st["label"]
    if st["cancelled"]:
        return "cancelled", f"{label} is cancelled: no sheet. Put it aside and tell the manager."
    if st["shipped"]:
        return "shipped", f"{label} has shipped: no sheet needed. See its delivery note."
    if st["printed"] and not reason:
        return "printed", (f"{label} already has a sheet (copy {st['latest_copy']}, printed "
                           f"{st['latest_printed_at'][:10]}). To print again pick a reason: "
                           "lost, damaged or order changed.")
    return None


SKIP_GROUPS = {  # one sentence for many items of the same kind
    "cancelled": "{labels}: cancelled, no sheet. Put them aside and tell the manager.",
    "shipped": "{labels}: already shipped, no sheet needed.",
    "printed": "{labels} already have a sheet. To print again pick a reason: "
               "lost, damaged or order changed.",
}


def _skips_in_words(skipped: list[dict]) -> str:
    """Four poufs of one order are one sentence, not four."""
    out = []
    for kind in SKIP_GROUPS:
        items = [s for s in skipped if s["kind"] == kind]
        if len(items) == 1:
            out.append(items[0]["why"])
        elif items:
            labels = ", ".join(s["label"] for s in items[:8]) + (
                f" and {len(items) - 8} more" if len(items) > 8 else "")
            out.append(SKIP_GROUPS[kind].format(labels=labels))
    return " ".join(out)


def _batch_receipt(conn, batch_id: int, skipped: list[dict]) -> dict:
    copies = [{"unit_id": r["unit_id"], "label": domain.unit_label(conn, r["unit_id"]),
               "copy": r["copy"]}
              for r in conn.execute("SELECT unit_id, copy FROM sheet_copies WHERE batch_id = ? "
                                    "ORDER BY id", (batch_id,))]
    msg = f"Batch {batch_id}: {len(copies)} sheet(s) ready to print."
    if skipped:
        msg += f" Skipped {len(skipped)}: " + _skips_in_words(skipped)
    return {"ok": True, "batch_id": batch_id, "already": False, "printed": len(copies),
            "copies": copies, "skipped": skipped, "message": msg,
            "url": f"/paperwork/sheets/batch/{batch_id}"}


def print_sheets(conn, person_id: int, *, scope: str, order_ids=None, unit_ids=None,
                 reason: str | None = None, note: str | None = None,
                 idem_key: str | None = None) -> dict:
    """Record a print: each item gets its next copy number. Nothing is printed by
    looking at a page; this call is the only thing that makes copies."""
    require_office(conn, person_id)
    if scope == "changed":
        reason = "changed"
    reason = reason or None
    if reason is not None and reason not in REASONS:
        raise DomainError("Pick a reprint reason: lost, damaged, changed or other.")
    with _writing(conn):
        if idem_key:
            prev = conn.execute("SELECT r.result FROM print_batches b JOIN print_batch_results r "
                                "ON r.batch_id = b.id WHERE b.idem_key = ?", (idem_key,)).fetchone()
            if prev:
                return {**json.loads(prev["result"]), "already": True}
        unit_list = units_for_scope(conn, scope, order_ids, unit_ids)
        st_all = states(conn, unit_list)
        ready, skipped = [], []
        for uid in unit_list:
            st = st_all[uid]
            skip = _skip_reason(st, reason)
            if skip:
                skipped.append({"unit_id": uid, "label": st["label"], "kind": skip[0], "why": skip[1]})
            else:
                ready.append((uid, (st["latest_copy"] or 0) + 1))
        if not ready:
            if not skipped:
                raise DomainError({"new": "Every live item already has a sheet.",
                                   "changed": "No sheet on the floor is out of date.",
                                   "today": "No live items from orders taken today."
                                   }.get(scope, "Nothing to print."))
            raise DomainError(_skips_in_words(skipped))
        when = _stamp(conn)
        batch_reason = reason if any(copy > 1 for _, copy in ready) else None
        bid = conn.execute(
            "INSERT INTO print_batches (created_at, created_by, scope, reason, note, idem_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (when, person_id, scope, batch_reason, note, idem_key)).lastrowid
        ensure_codes(conn, [uid for uid, _ in ready])
        for uid, copy in ready:
            snap = st_all[uid]["snapshot"]
            conn.execute(
                "INSERT INTO sheet_copies (unit_id, copy, batch_id, printed_at, printed_by, "
                "reason, snapshot, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, copy, bid, when, person_id, reason if copy > 1 else None,
                 json.dumps(snap, ensure_ascii=False), fingerprint(snap)))
        receipt = _batch_receipt(conn, bid, skipped)
        conn.execute("INSERT INTO print_batch_results (batch_id, result) VALUES (?, ?)",
                     (bid, json.dumps(receipt, ensure_ascii=False)))
        return receipt


def void_sheet(conn, person_id: int, unit_id: int, reason: str) -> dict:
    """Take the latest copy out of use (order cancelled, sheet found torn)."""
    require_office(conn, person_id, "voids sheets")
    if not (reason or "").strip():
        raise DomainError("Say why the sheet is voided.")
    st = sheet_state(conn, unit_id)
    if not st["printed"]:
        raise DomainError(f"{st['label']} has no printed sheet. Nothing to void.")
    with _writing(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO sheet_voids (unit_id, copy, reason, voided_at, voided_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (unit_id, st["latest_copy"], reason.strip(), _stamp(conn), person_id))
    if not cur.rowcount:
        return {"ok": True, "already": True,
                "message": f"Already voided: {st['label']}, copy {st['latest_copy']}. Nothing changed."}
    return {"ok": True, "already": False,
            "message": f"Voided: {st['label']}, copy {st['latest_copy']}. Pull it from the floor."}


# --- scanning a sheet -----------------------------------------------------

def _done_rows(conn, unit_id: int, before: str | None = None) -> list[dict]:
    """Live 'done' marks of one item; with `before`, as they stood at that moment."""
    if before is None:
        sql = f"SELECT * FROM ({domain.LIVE_DONE}) e WHERE e.unit_id = ?"
        params: tuple = (unit_id,)
    else:
        sql = ("SELECT * FROM work_events e WHERE e.kind = 'done' AND e.unit_id = ? "
               "AND e.created_at <= ? AND NOT EXISTS (SELECT 1 FROM work_events u "
               "WHERE u.kind = 'undo' AND u.undoes_id = e.id AND u.created_at <= ?)")
        params = (unit_id, before, before)
    return [dict(r) for r in conn.execute(
        f"SELECT s.id AS stage_id, s.name_en, s.name_ru, p.name AS person, e.work_date "
        f"FROM ({sql}) e JOIN stages s ON s.id = e.stage_id JOIN people p ON p.id = e.person_id "
        f"ORDER BY s.seq", params)]


def resolve(conn, code) -> dict:
    """What a scanned sheet is and what to tell the person holding it.
    Warnings never block marking except cancelled and shipped: in a real shop
    every lock became an item that left without a record."""
    token, copy = parse_code(code)
    row = conn.execute("SELECT unit_id FROM unit_codes WHERE token = ?", (token,)).fetchone()
    if row is None:
        raise DomainError(f"Code {format_code(token)} is not known here. "
                          "Type the order number from the sheet instead.")
    uid = row["unit_id"]
    st = sheet_state(conn, uid)
    latest = st["latest_copy"] or 0
    if copy is not None and not 1 <= copy <= latest:
        raise DomainError(f"Copy {copy} of {st['label']} was never printed. "
                          "Ask the office for a proper sheet.")
    warn: list[tuple[str, str]] = []
    if copy is not None and copy < latest:
        date = st["latest_printed_at"][:10]
        why = st["latest_reason"] or "reprint"
        warn.append((f"This is copy {copy}; copy {latest} was printed on {date} ({why}). "
                     f"Throw this sheet away and use copy {latest}.",
                     f"Это копия {copy}; копия {latest} напечатана {date}. "
                     f"Этот листок выбросьте, работайте по копии {latest}."))
    voided = conn.execute("SELECT reason FROM sheet_voids WHERE unit_id = ? AND copy = ?",
                          (uid, copy or latest)).fetchone()
    if voided:
        warn.append((f"This sheet is voided ({voided['reason']}). Pull it from the floor "
                     "and ask the office for a new one.",
                     f"Листок аннулирован ({voided['reason']}). Уберите его и попросите новый в офисе."))
    scanned = conn.execute("SELECT snapshot FROM sheet_copies WHERE unit_id = ? AND copy = ?",
                           (uid, copy or latest)).fetchone()
    if scanned:
        changes = _changes(json.loads(scanned["snapshot"]), st["snapshot"])
        if changes:
            en = "; ".join(f"{c['field']} was {c['was'] or '—'}, now {c['now'] or '—'}" for c in changes)
            ru = "; ".join(f"{c['field_ru']}: было {c['was'] or '—'}, стало {c['now'] or '—'}"
                           for c in changes)
            warn.append((f"Old sheet: {en}. Work to the new details and ask the office for a new sheet.",
                         f"Листок устарел: {ru}. Делайте по новому и попросите новый листок."))
    if st["cancelled"]:
        warn.append(("This order is cancelled. Put the item aside and tell the manager.",
                     "Заказ отменён. Отложите изделие и скажите менеджеру."))
    elif st["shipped"]:
        warn.append(("This item has already shipped. Tell the manager.",
                     "Изделие уже отгружено. Скажите менеджеру."))
    elif st["finished"]:
        warn.append(("This item is finished. Take it to shipping.",
                     "Изделие готово. Отнесите на отгрузку."))
    snap = st["snapshot"]
    stage = domain.current_stage(conn, uid)
    return {
        "unit_id": uid, "order_id": st["order_id"], "order_number": snap["order_number"],
        "label": st["label"], "token": token, "code": format_code(token),
        "copy": copy, "latest_copy": st["latest_copy"],
        "product": snap["product"], "variant": snap["variant"],
        "customer": snap["customer"], "due_date": snap["due_date"],
        "stage_now": ({k: stage[k] for k in ("id", "name_en", "name_ru")} if stage else None),
        "done": _done_rows(conn, uid), "status": domain.order_status(conn, st["order_id"]),
        "ok_to_mark": not (st["cancelled"] or st["shipped"]),
        "warnings": [w[0] for w in warn], "warnings_ru": [w[1] for w in warn],
    }


def attention(conn) -> dict:
    """Paper problems the office should act on, from one pass over the items."""
    all_states = states(conn)
    day = domain.today(conn)
    changed, cancelled = [], []
    counts = {"new": 0, "changed": 0, "today": 0}
    for uid, st in all_states.items():
        if st["live"]:
            counts["new"] += not st["printed"]
            counts["today"] += st["order_created"][:10] == day and not st["printed"]
            if st["stale"] and not st["voided"]:
                counts["changed"] += 1
                changed.append({"unit_id": uid, "order_id": st["order_id"],
                                "order_number": st["snapshot"]["order_number"],
                                "label": st["label"], "changes": st["changes"]})
        if st["cancelled"] and st["printed"] and not st["voided"]:
            cancelled.append({"unit_id": uid, "order_id": st["order_id"], "label": st["label"]})
    return {"never_printed": counts["new"], "changed": changed,
            "cancelled_with_live_sheet": [c["label"] for c in cancelled],
            "cancelled_sheets": cancelled, "counts": counts}


# --- delivery notes -------------------------------------------------------

EMAIL_RE = re.compile(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+")


def valid_email(email) -> str:
    email = (email or "").strip() if isinstance(email, str) else ""
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise DomainError("Enter an e-mail like orders@shop.example.com, without spaces.")
    return email


def customer_email(conn, customer_id: int) -> str | None:
    row = conn.execute("SELECT email FROM customer_contacts WHERE customer_id = ?",
                       (customer_id,)).fetchone()
    return row["email"] if row else None


def set_customer_email(conn, person_id: int, customer_id: int, email: str) -> dict:
    require_office(conn, person_id, "edits customer contacts")
    email = valid_email(email)
    c = conn.execute("SELECT name FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if c is None:
        raise DomainError("No such customer.")
    with _writing(conn):
        conn.execute("INSERT INTO customer_contacts (customer_id, email, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (customer_id) DO UPDATE SET email = excluded.email, "
                     "updated_at = excluded.updated_at", (customer_id, email, _stamp(conn)))
    return {"ok": True, "message": f"Saved: {c['name']} gets notes at {email}."}


def note_data(conn, shipment_id: int) -> dict:
    """Everything a delivery note prints. Money comes only from domain.invoice and
    domain.shipment_total, the one function behind every total in the product."""
    inv = domain.invoice(conn, shipment_id)
    sh = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    previous = 0
    for row in conn.execute(
            "SELECT id, paid_amount FROM shipments WHERE customer_id = ? AND id <> ? "
            "AND (created_at < ? OR (created_at = ? AND id < ?))",
            (sh["customer_id"], shipment_id, sh["created_at"], sh["created_at"], shipment_id)):
        previous += domain.shipment_total(conn, row["id"]) - row["paid_amount"]
    return {**inv, "customer_id": sh["customer_id"],
            "shop_block": {k: settings.get(k, "") for k in
                           ("shop_name", "shop_address", "shop_phone", "shop_email", "shop_tax_id")},
            "email": customer_email(conn, sh["customer_id"]),
            "number": f"DN-{shipment_id:06d}",
            "created_by": domain.person(conn, sh["created_by"])["name"],
            "previous_owed": previous, "owed_with_previous": inv["owed"] + previous}


def notes_list(conn, limit: int = 30) -> list[dict]:
    out = []
    for sh in conn.execute("SELECT id FROM shipments ORDER BY created_at DESC, id DESC LIMIT ?",
                           (max(1, min(limit, 200)),)):
        d = note_data(conn, sh["id"])
        last = conn.execute("SELECT status FROM outbox WHERE kind = 'delivery_note' AND ref_id = ? "
                            "ORDER BY id DESC LIMIT 1", (sh["id"],)).fetchone()
        out.append({"id": d["id"], "number": d["number"], "date": d["date"],
                    "customer_id": d["customer_id"], "customer": d["customer"],
                    "items": len(d["lines"]), "total": d["total"], "owed": d["owed"],
                    "currency": d["currency"], "email": d["email"],
                    "last_outbox_status": last["status"] if last else None})
    return out


# --- e-mail through the outbox --------------------------------------------

def smtp_config() -> dict | None:
    """Real sending only when the shop configured it (rule 8)."""
    host, sender = os.getenv("FLOORHAND_SMTP_HOST"), os.getenv("FLOORHAND_SMTP_FROM")
    if not host or not sender:
        return None
    return {"host": host, "port": int(os.getenv("FLOORHAND_SMTP_PORT", "587")),
            "user": os.getenv("FLOORHAND_SMTP_USER"),
            "password": os.getenv("FLOORHAND_SMTP_PASSWORD", ""),
            "sender": sender, "tls": os.getenv("FLOORHAND_SMTP_TLS", "1") != "0"}


def _outbox_answer(row, already: bool = False) -> dict:
    if already:
        msg = f"Already in the outbox (#{row['id']}) for this note. Nothing new was sent."
    elif row["status"] == "sent":
        msg = f"Sent to {row['to_addr']}."
    elif row["status"] == "failed":
        msg = f"Not sent: {row['error']}. It stays in the outbox; try again later."
    else:
        msg = "Saved to outbox (sending is off)."
    return {"ok": row["status"] != "failed", "already": already, "outbox_id": row["id"],
            "status": row["status"], "message": msg}


def _note_mail(data: dict, lang: str) -> tuple[str, str]:
    from .paperwork_html import fmt_money
    m = lambda n: fmt_money(data["currency"], n)  # noqa: E731
    shop = data["shop_block"]
    sign = "\n".join(v for v in (shop["shop_name"], shop["shop_phone"], shop["shop_email"]) if v)
    if lang == "ru":
        subject = f"Накладная {data['number']} от {data['shop']}"
        body = (f"Здравствуйте!\n\nВо вложении накладная {data['number']} от {data['date']}.\n"
                f"Изделий: {len(data['lines'])}\nИтого: {m(data['total'])}\n"
                f"Оплачено: {m(data['paid'])}\nК оплате: {m(data['owed'])}\n\n{sign}\n")
    else:
        subject = f"Delivery note {data['number']} from {data['shop']}"
        body = (f"Hello,\n\nPlease find delivery note {data['number']} of {data['date']} attached.\n"
                f"Items: {len(data['lines'])}\nTotal: {m(data['total'])}\n"
                f"Paid: {m(data['paid'])}\nOwed: {m(data['owed'])}\n\n{sign}\n")
    return subject, body


def email_note(conn, person_id: int, shipment_id: int, *, to: str | None = None,
               resend: bool = False, idem_key: str | None = None) -> dict:
    """Put a delivery note into the outbox; send it only if SMTP is configured."""
    from . import paperwork_html as html
    require_office(conn, person_id, "sends delivery notes")
    lang = domain.person(conn, person_id)["lang"]
    with _writing(conn):
        if idem_key:
            prev = conn.execute("SELECT * FROM outbox WHERE idem_key = ?", (idem_key,)).fetchone()
            if prev:
                return _outbox_answer(prev, already=True)
        data = note_data(conn, shipment_id)
        addr = to or data["email"]
        if not addr:
            raise DomainError(f"No e-mail for {data['customer']}. Add it on the Paperwork page first.")
        addr = valid_email(addr)
        prev = conn.execute(
            "SELECT * FROM outbox WHERE kind = 'delivery_note' AND ref_id = ? AND to_addr = ? "
            "AND status IN ('draft', 'sent') ORDER BY id DESC LIMIT 1",
            (shipment_id, addr)).fetchone()
        if prev and not resend:
            return _outbox_answer(prev, already=True)
        subject, body = _note_mail(data, lang)
        oid = conn.execute(
            "INSERT INTO outbox (kind, ref_id, to_addr, subject, body, attachment_name, "
            "attachment_html, status, created_at, created_by, idem_key) "
            "VALUES ('delivery_note', ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
            (shipment_id, addr, subject, body, f"{data['number']}.html",
             html.render_note(data, copies=1, lang=lang, toolbar=False),
             _stamp(conn), person_id, idem_key)).lastrowid
    if smtp_config():
        return send_outbox(conn, person_id, oid)
    return _outbox_answer(conn.execute("SELECT * FROM outbox WHERE id = ?", (oid,)).fetchone())


def _deliver(config: dict, row) -> str | None:
    """Talk to the mail server. Returns an error in words, or None when sent."""
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = config["sender"], row["to_addr"], row["subject"]
        msg.set_content(row["body"])
        if row["attachment_html"]:
            msg.add_attachment(row["attachment_html"].encode("utf-8"), maintype="text",
                               subtype="html", filename=row["attachment_name"])
        with smtplib.SMTP(config["host"], config["port"], timeout=20) as smtp:
            if config["tls"]:
                smtp.starttls()
            if config["user"]:
                smtp.login(config["user"], config["password"])
            smtp.send_message(msg)
        return None
    except Exception as exc:  # any failure is an answer, not a crash
        return str(exc) or exc.__class__.__name__


def send_outbox(conn, person_id: int, outbox_id: int) -> dict:
    """Send (or retry) one outbox row. The mail server is called without holding
    the database lock, so a slow server never stops marks on the floor."""
    require_office(conn, person_id, "sends e-mail")
    with _SEND_LOCK:
        row = conn.execute("SELECT * FROM outbox WHERE id = ?", (outbox_id,)).fetchone()
        if row is None:
            raise DomainError("No such message in the outbox.")
        if row["status"] == "sent":
            return {"ok": True, "already": True, "outbox_id": outbox_id, "status": "sent",
                    "message": f"Already sent on {row['sent_at'][:16].replace('T', ' ')}. Nothing changed."}
        config = smtp_config()
        if config is None:
            return {"ok": True, "already": False, "outbox_id": outbox_id, "status": row["status"],
                    "message": "Sending is off: it stays in the outbox."}
        error = _deliver(config, row)
        with _writing(conn):
            if error is None:
                conn.execute("UPDATE outbox SET status = 'sent', sent_at = ?, error = NULL "
                             "WHERE id = ?", (_stamp(conn), outbox_id))
            else:
                conn.execute("UPDATE outbox SET status = 'failed', error = ? WHERE id = ?",
                             (error, outbox_id))
        return _outbox_answer(conn.execute("SELECT * FROM outbox WHERE id = ?", (outbox_id,)).fetchone())


def outbox_list(conn, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT o.id, o.kind, o.ref_id, o.to_addr, o.subject, o.body, o.attachment_name, "
        "o.status, o.error, o.created_at, o.sent_at, c.name AS customer "
        "FROM outbox o LEFT JOIN shipments s ON s.id = o.ref_id "
        "LEFT JOIN customers c ON c.id = s.customer_id ORDER BY o.id DESC LIMIT ?",
        (max(1, min(limit, 200)),))]


# --- pages: data for printing (read-only) ---------------------------------

def batch_data(conn, batch_id: int) -> dict:
    """A batch exactly as it was printed: the copy's snapshot, the marks that
    existed at print time. Looking at it later shows the same paper."""
    b = conn.execute("SELECT b.*, p.name AS printed_by_name FROM print_batches b "
                     "JOIN people p ON p.id = b.created_by WHERE b.id = ?", (batch_id,)).fetchone()
    if b is None:
        raise DomainError("No such print batch. Print from the Paperwork page.")
    shop = conn.execute("SELECT value FROM settings WHERE key = 'shop_name'").fetchone()
    stage_rows = [dict(s) for s in domain.stages(conn)]
    sheets = []
    for row in conn.execute(
            "SELECT sc.*, uc.token, p.name AS printed_by_name FROM sheet_copies sc "
            "JOIN unit_codes uc ON uc.unit_id = sc.unit_id JOIN people p ON p.id = sc.printed_by "
            "WHERE sc.batch_id = ? ORDER BY sc.id", (batch_id,)):
        snap = json.loads(row["snapshot"])
        done = _done_rows(conn, row["unit_id"], before=row["printed_at"])
        printed_day = datetime.fromisoformat(row["printed_at"]).date()
        urgent = bool(snap.get("due_date") and len(done) < len(stage_rows)
                      and snap["due_date"] <= (printed_day + timedelta(days=2)).isoformat())
        sheets.append({**dict(row), "snapshot": snap, "done": done, "stages": stage_rows,
                       "shop": shop["value"] if shop else "", "urgent": urgent})
    return {**dict(b), "sheets": sheets}


def order_sheet_data(conn, order_id: int) -> dict:
    """The office folder sheet: every item of an order, its code and progress.
    Read-only: an item without a code shows a hint instead of getting one."""
    rows = conn.execute("SELECT id FROM units WHERE order_id = ? ORDER BY seq", (order_id,)).fetchall()
    if not rows:
        raise DomainError("No such order.")
    units = []
    for r in rows:
        tok = conn.execute("SELECT token FROM unit_codes WHERE unit_id = ?", (r["id"],)).fetchone()
        units.append({"snapshot": snapshot(conn, r["id"]), "label": domain.unit_label(conn, r["id"]),
                      "token": tok["token"] if tok else None,
                      "done": domain.done_stage_ids(conn, r["id"])})
    return {"units": units, "stages": [dict(s) for s in domain.stages(conn)]}


# --- routes ---------------------------------------------------------------

router = APIRouter()


def private_office(s=Depends(office)):
    """Money pages: office people on their own device, never the shared tablet."""
    if s.tablet:
        raise HTTPException(403, "Money is not shown on the shared tablet.")
    return s


class PrintIn(BaseModel):
    scope: str
    order_ids: list[int] | None = None
    unit_ids: list[int] | None = None
    reason: str | None = None
    note: str | None = None
    idem_key: str | None = None


class VoidIn(BaseModel):
    unit_id: int
    reason: str


class ContactIn(BaseModel):
    email: str


class MailIn(BaseModel):
    to: str | None = None
    resend: bool = False
    idem_key: str | None = None


@router.post("/api/paperwork/sheets/print")
def api_print(body: PrintIn, s=Depends(office), conn=Depends(get_conn)) -> dict:
    return print_sheets(conn, s.person_id, **body.model_dump())


@router.get("/api/paperwork/sheets/attention")
def api_attention(s=Depends(office), conn=Depends(get_conn)) -> dict:
    return attention(conn)


@router.get("/api/paperwork/sheets/state/{unit_id}")
def api_state(unit_id: int, s=Depends(office), conn=Depends(get_conn)) -> dict:
    return sheet_state(conn, unit_id)


@router.post("/api/paperwork/sheets/void")
def api_void(body: VoidIn, s=Depends(office), conn=Depends(get_conn)) -> dict:
    return void_sheet(conn, s.person_id, body.unit_id, body.reason)


@router.get("/api/paperwork/resolve")
def api_resolve(code: str, s=Depends(session), conn=Depends(get_conn)) -> dict:
    return resolve(conn, code)  # no money in it: fine for the shop tablet


@router.get("/api/paperwork/notes")
def api_notes(limit: int = 30, s=Depends(private_office), conn=Depends(get_conn)) -> list[dict]:
    return notes_list(conn, limit)


@router.put("/api/paperwork/customers/{customer_id}/email")
def api_contact(customer_id: int, body: ContactIn, s=Depends(office),
                conn=Depends(get_conn)) -> dict:
    return set_customer_email(conn, s.person_id, customer_id, body.email)


@router.post("/api/paperwork/notes/{shipment_id}/email")
def api_email(shipment_id: int, body: MailIn, s=Depends(private_office),
              conn=Depends(get_conn)) -> dict:
    return email_note(conn, s.person_id, shipment_id, **body.model_dump())


@router.get("/api/paperwork/outbox")
def api_outbox(s=Depends(private_office), conn=Depends(get_conn)) -> dict:
    return {"sending": smtp_config() is not None, "rows": outbox_list(conn)}


@router.post("/api/paperwork/outbox/{outbox_id}/send")
def api_send(outbox_id: int, s=Depends(private_office), conn=Depends(get_conn)) -> dict:
    return send_outbox(conn, s.person_id, outbox_id)


def _lang(conn, s, lang: str) -> str:
    return lang if lang in ("en", "ru") else domain.person(conn, s.person_id)["lang"]


def _base(conn, request: Request) -> str:
    """Where the QR points: the shop's public address if set, else this server."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'public_url'").fetchone()
    return (row["value"] if row and row["value"] else str(request.base_url)).rstrip("/")


def _page(render) -> HTMLResponse:
    from .paperwork_html import error_page
    try:
        return HTMLResponse(render())
    except DomainError as exc:
        return HTMLResponse(error_page(str(exc)), status_code=409)


@router.get("/paperwork", response_class=HTMLResponse)
def hub_page(s=Depends(private_office), conn=Depends(get_conn)) -> HTMLResponse:
    from .paperwork_html import render_hub
    return HTMLResponse(render_hub(domain.person(conn, s.person_id)["lang"]))


@router.get("/paperwork/sheets/batch/{batch_id}", response_class=HTMLResponse)
def batch_page(batch_id: int, request: Request, layout: str = "a5", lang: str = "",
               s=Depends(office), conn=Depends(get_conn)) -> HTMLResponse:
    from .paperwork_html import render_batch
    lang, base = _lang(conn, s, lang), _base(conn, request)
    return _page(lambda: render_batch(batch_data(conn, batch_id), layout=layout, lang=lang, base=base))


@router.get("/paperwork/sheets/order/{order_id}", response_class=HTMLResponse)
def order_page(order_id: int, request: Request, lang: str = "", s=Depends(office),
               conn=Depends(get_conn)) -> HTMLResponse:
    from .paperwork_html import render_order
    lang, base = _lang(conn, s, lang), _base(conn, request)
    return _page(lambda: render_order(order_sheet_data(conn, order_id), lang=lang, base=base))


@router.get("/paperwork/note/{shipment_id}", response_class=HTMLResponse)
def note_page(shipment_id: int, copies: int = 2, lang: str = "", autoprint: bool = False,
              s=Depends(private_office), conn=Depends(get_conn)) -> HTMLResponse:
    from .paperwork_html import render_note
    lang = _lang(conn, s, lang)
    return _page(lambda: render_note(note_data(conn, shipment_id), copies=copies, lang=lang,
                                     autoprint=autoprint))


@router.get("/paperwork/outbox/{outbox_id}", response_class=HTMLResponse)
def outbox_page(outbox_id: int, s=Depends(private_office), conn=Depends(get_conn)) -> HTMLResponse:
    def render():
        row = conn.execute("SELECT attachment_html FROM outbox WHERE id = ?", (outbox_id,)).fetchone()
        if row is None:
            raise DomainError("No such message in the outbox.")
        return row["attachment_html"] or ""
    return _page(render)


@router.get("/u/{token}")
@router.get("/u/{token}/{copy}")
def scan_entry(token: str, copy: int | None = None) -> RedirectResponse:
    """A phone camera opens the QR as a link. Hand it to the app; write nothing."""
    tok, cp = parse_code(token + (f"/{copy}" if copy is not None else ""))
    return RedirectResponse(f"/?code={tok}" + (f"/{cp}" if cp is not None else ""), status_code=302)


# --- demo data ------------------------------------------------------------

def seed(conn, rnd) -> None:
    """A paper history for the fictional shop: everything older than a day has a
    sheet, a few were lost and reprinted, two orders changed after printing, one
    was cancelled with its sheet still on the floor. Times come from the data,
    so the same seed gives the same history."""
    from . import paperwork_html as html
    conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", [
        ("shop_address", "14 Mill Lane, Riverton"), ("shop_phone", "+1 555 0100"),
        ("shop_email", "office@birch-and-thread.example"), ("shop_tax_id", "TAX-000-1234")])
    reference = conn.execute("SELECT MAX(created_at) FROM orders").fetchone()[0]
    if not reference:
        return
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager' LIMIT 1").fetchone()[0]
    for c in conn.execute("SELECT id, name FROM customers ORDER BY id").fetchall()[2:]:
        slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")
        conn.execute("INSERT OR IGNORE INTO customer_contacts VALUES (?, ?, ?)",
                     (c["id"], f"orders@{slug}.example.com", reference))

    fabrics = json.loads((conn.execute("SELECT value FROM settings WHERE key = 'fabrics'").fetchone()
                          or ["[]"])[0]) or ["linen sand"]
    cutoff = (datetime.fromisoformat(reference).date() - timedelta(days=1)).isoformat()
    live = {uid for uid, st in states(conn).items() if st["live"]}
    batches: dict[str, int] = {}
    printed: list[int] = []
    stale_orders: dict[int, str] = {}  # order id -> what changed after print
    old_fabric: dict[int, str] = {}
    rows = conn.execute("SELECT u.id, o.id AS oid, o.created_at FROM units u "
                        "JOIN orders o ON o.id = u.order_id ORDER BY o.created_at, u.id").fetchall()
    for row in rows:
        uid = row["id"]
        token = "".join(rnd.choice(ALPHABET) for _ in range(TOKEN_LEN))
        while conn.execute("SELECT 1 FROM unit_codes WHERE token = ?", (token,)).fetchone():
            token = "".join(rnd.choice(ALPHABET) for _ in range(TOKEN_LEN))
        conn.execute("INSERT INTO unit_codes VALUES (?, ?, ?)", (token, uid, row["created_at"]))
        started = conn.execute("SELECT 1 FROM work_events WHERE unit_id = ?", (uid,)).fetchone()
        if not started and row["created_at"][:10] >= cutoff:
            continue  # taken in the last day and not started: waits for "Print new"
        when = (datetime.fromisoformat(row["created_at"])
                + timedelta(hours=rnd.randint(1, 3))).isoformat(timespec="seconds")
        day = row["created_at"][:10]
        if day not in batches:
            batches[day] = conn.execute(
                "INSERT INTO print_batches (created_at, created_by, scope) VALUES (?, ?, 'new')",
                (when, manager)).lastrowid
        snap = snapshot(conn, uid)
        if uid in live and (row["oid"] in stale_orders
                            or not stale_orders or (len(stale_orders) == 1 and snap["due_date"])):
            kind = stale_orders.setdefault(row["oid"], "fabric" if not stale_orders else "due")
            if kind == "fabric":  # the customer switched fabric after the sheet was printed
                fabric, _, frame = snap["variant"].partition(" · ")
                if row["oid"] not in old_fabric:
                    old_fabric[row["oid"]] = rnd.choice([f for f in fabrics if f != fabric] or ["linen sand"])
                snap["variant"] = f"{old_fabric[row['oid']]} · {frame}" if frame else old_fabric[row["oid"]]
            elif snap["due_date"]:  # the customer moved the date
                snap["due_date"] = (datetime.fromisoformat(snap["due_date"])
                                    + timedelta(days=4)).date().isoformat()
        conn.execute("INSERT INTO sheet_copies (unit_id, copy, batch_id, printed_at, printed_by, "
                     "snapshot, fingerprint) VALUES (?, 1, ?, ?, ?, ?, ?)",
                     (uid, batches[day], when, manager, json.dumps(snap), fingerprint(snap)))
        printed.append(uid)

    reprint = [u for u in printed if u in live
               and domain._unit_order(conn, u)["id"] not in stale_orders][:3]
    for uid in reprint:  # lost or torn on the floor, reprinted
        first = conn.execute("SELECT * FROM sheet_copies WHERE unit_id = ? AND copy = 1",
                             (uid,)).fetchone()
        when = (datetime.fromisoformat(first["printed_at"]) + timedelta(hours=4)).isoformat(timespec="seconds")
        reason = rnd.choice(("lost", "damaged"))
        bid = conn.execute("INSERT INTO print_batches (created_at, created_by, scope, reason) "
                           "VALUES (?, ?, 'units', ?)", (when, manager, reason)).lastrowid
        snap = json.loads(first["snapshot"])
        conn.execute("INSERT INTO sheet_copies (unit_id, copy, batch_id, printed_at, printed_by, "
                     "reason, snapshot, fingerprint) VALUES (?, 2, ?, ?, ?, ?, ?, ?)",
                     (uid, bid, when, manager, reason, first["snapshot"], fingerprint(snap)))

    # A customer cancelled; the sheet is still on the floor until someone voids it.
    for uid in reversed(printed):
        order = domain._unit_order(conn, uid)
        if uid in live and order["id"] not in stale_orders and order["customer_id"]:
            conn.execute("UPDATE orders SET cancelled = 1 WHERE id = ?", (order["id"],))
            break

    drafts = 0
    for row in conn.execute("SELECT id FROM shipments ORDER BY created_at DESC, id DESC").fetchall():
        data = note_data(conn, row["id"])
        if drafts == 2:
            break
        if not data["email"]:
            continue
        drafts += 1
        subject, body = _note_mail(data, "en")
        conn.execute("INSERT INTO outbox (kind, ref_id, to_addr, subject, body, attachment_name, "
                     "attachment_html, status, created_at, created_by) "
                     "VALUES ('delivery_note', ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                     (row["id"], data["email"], subject, body, f"{data['number']}.html",
                      html.render_note(data, copies=1, toolbar=False), data["date"] + "T18:00:00",
                      manager))
