"""How workers mark work: one event, many ways in.

Every way of saying "I finished my stage on this piece" ends in domain.mark_done:
the "+" button, a QR scan, the number from the paper sheet, voice, a photo of the
sheet, the offline queue. So the rules are the same everywhere: a repeat never
undoes ("Already recorded"), the same idem_key records once, money follows the date
of the work, and the shared tablet never shows money.

Lessons taken from a working upholstery ERP (~30 people, 46 days of usage data):
- 60% of marks are the "+" on an assigned card, 25% scans, 8% typed numbers. All
  three go through `mark()` here.
- A repeated scan of the same sheet is almost always a re-check, not "the next
  piece" (packers re-scanned boxes and undid their own work). So a repeat never
  undoes; on a multi-piece order sheet a quick repeat asks before marking another.
- The scanner can fire twice within a second: same person + same code within
  3 s counts once, and we say so instead of answering "Recorded" twice.
- Missed marks were fixed with free-text "add money to myself" claims, later moved
  into marks by hand. Here a missed piece is added as a mark for its real day
  (up to 3 days back), and "Did other work" is only for work without a sheet.
- Claims went to payout without approval and rejections deleted them silently.
  Here a claim pays only after a manager approves it; rejected claims stay visible.
- Offline marks were lost when the shared tablet signed out before the queue sent
  them. Here the tablet keeps a log of who was marking when, and a queued tap is
  accepted for the person who was signed in at that moment.

Codes accepted by `resolve()` (the paper sheet and the QR on it):
    "/u/<token>" or a full URL ending in it   -> unit_tokens.token (QR per piece)
    "214-2", "#214/2", "№214 / 2"             -> order 214, piece 2
    "214", "#214"                             -> order 214 (next piece at my stage)
unit_tokens(token TEXT PRIMARY KEY, unit_id INTEGER) is shared with the paperwork
feature that prints the sheets; tokens are [A-Za-z0-9_-]{4,64}.
"""
import base64
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import domain, models, speech, web
from ..domain import LIVE_DONE, DomainError
from ..models import BudgetStop
from ..web import Session, get_conn, office, worker

SCHEMA = """
-- QR token -> piece. Same definition as the paperwork feature (whoever runs first creates it).
CREATE TABLE IF NOT EXISTS unit_tokens (token TEXT PRIMARY KEY, unit_id INTEGER);

-- How each mark came in: when the phone says it was tapped, when it arrived, and how
-- sure we are of the day. work_events itself stays the core table.
CREATE TABLE IF NOT EXISTS mark_meta (
    event_id      INTEGER PRIMARY KEY REFERENCES work_events(id),
    phone_at      TEXT,
    received_at   TEXT NOT NULL,
    time_quality  TEXT NOT NULL CHECK (time_quality IN ('live', 'phone', 'declared', 'adjusted')),
    note          TEXT,
    code          TEXT,
    tablet        TEXT
);

-- Every scan / typed code: catches double triggers and quick repeats.
CREATE TABLE IF NOT EXISTS scan_log (
    id         INTEGER PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES people(id),
    code       TEXT NOT NULL,
    unit_id    INTEGER,
    event_id   INTEGER,
    outcome    TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scan_log ON scan_log(person_id, code, id);

-- "Did other work": common jobs per stage with a fixed price per piece or by minutes.
CREATE TABLE IF NOT EXISTS claim_presets (
    id        INTEGER PRIMARY KEY,
    stage_id  INTEGER REFERENCES stages(id),     -- NULL = any stage
    name_en   TEXT NOT NULL,
    name_ru   TEXT NOT NULL,
    amount    INTEGER,                           -- per piece; NULL = paid by minutes
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS work_claims (
    id               INTEGER PRIMARY KEY,
    person_id        INTEGER NOT NULL REFERENCES people(id),
    work_date        TEXT NOT NULL,              -- money follows this date
    description      TEXT NOT NULL,
    preset_id        INTEGER REFERENCES claim_presets(id),
    qty              INTEGER,
    minutes          INTEGER,
    asked_amount     INTEGER,                    -- what the worker expects (NULL = not known)
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    approved_amount  INTEGER,
    decided_by       INTEGER REFERENCES people(id),
    decided_at       TEXT,
    decision_note    TEXT,
    created_at       TEXT NOT NULL,
    idem_key         TEXT UNIQUE,
    payout_id        INTEGER REFERENCES payouts(id),   -- set when paid: the row is frozen
    CHECK (status != 'approved' OR approved_amount > 0)
);
CREATE INDEX IF NOT EXISTS ix_claims_person ON work_claims(person_id, work_date);

CREATE TRIGGER IF NOT EXISTS work_claims_paid_frozen
BEFORE UPDATE ON work_claims WHEN OLD.payout_id IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'paid claim is frozen'); END;

CREATE TRIGGER IF NOT EXISTS work_claims_no_delete
BEFORE DELETE ON work_claims
BEGIN SELECT RAISE(ABORT, 'claims are kept; withdraw or reject instead'); END;

-- "Today recorded N pcs - all right?" answers.
CREATE TABLE IF NOT EXISTS day_checks (
    person_id     INTEGER NOT NULL REFERENCES people(id),
    work_date     TEXT NOT NULL,
    confirmed_at  TEXT NOT NULL,
    pieces        INTEGER NOT NULL,
    PRIMARY KEY (person_id, work_date)
);

-- Things the floor tells the office: material is out, a rate is missing.
CREATE TABLE IF NOT EXISTS shop_signals (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('material_out', 'rate_missing')),
    text        TEXT NOT NULL,
    product_id  INTEGER REFERENCES products(id),
    stage_id    INTEGER REFERENCES stages(id),
    person_id   INTEGER REFERENCES people(id),
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    closed_by   INTEGER REFERENCES people(id),
    closed_at   TEXT
);

-- Shared shop tablet: the device, and who was marking on it when.
CREATE TABLE IF NOT EXISTS tablets (
    token           TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    stage_id        INTEGER REFERENCES stages(id),   -- NULL = everyone on the floor
    created_by      INTEGER NOT NULL REFERENCES people(id),
    created_at      TEXT NOT NULL,
    acting_person   INTEGER REFERENCES people(id),
    acting_session  TEXT,
    last_seen       TEXT
);
CREATE TABLE IF NOT EXISTS tablet_shifts (
    id          INTEGER PRIMARY KEY,
    tablet      TEXT NOT NULL REFERENCES tablets(token),
    person_id   INTEGER NOT NULL REFERENCES people(id),
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    end_reason  TEXT
);
"""

DEFAULTS = {
    "mark_back_days": 3,         # a missed piece can be added up to 3 days back
    "eod_hour": 16,              # end-of-day check shows from 16:00 shop time
    "tablet_idle_sec": 120,      # shared tablet signs out after 2 quiet minutes
    "tablet_leave_hour": 20,     # ... and at 20:00 in any case
    "debounce_sec": 3,           # same code twice this fast = one scan
    "repeat_ask_sec": 600,       # repeat of a multi-piece sheet within 10 min: ask first
    "claim_max_amount": 200,     # one "other work" claim cannot ask for more
    "claim_max_minutes_day": 600,
    "proposal_ttl_min": 30,      # a voice/photo proposal older than this must be said again
}
TABLET_COOKIE = "fh_tablet"


# --- small helpers ------------------------------------------------------------

def setting(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row["value"])
    except ValueError:
        return row["value"]


def _now(conn: sqlite3.Connection) -> datetime:
    """Shop time. Tests replace this to move the clock."""
    return domain.now(conn)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_dt(conn, raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    tz = domain.shop_tz(conn)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def lang_of(conn, person_id: int) -> str:
    row = conn.execute("SELECT lang FROM people WHERE id = ?", (person_id,)).fetchone()
    return "ru" if row and row["lang"] == "ru" else "en"


def T(lang: str, en: str, ru: str) -> str:
    return ru if lang == "ru" else en


def _stage(conn, stage_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM stages WHERE id = ?", (stage_id,)).fetchone()


def _sname(st, lang: str) -> str:
    return st["name_ru"] if lang == "ru" else st["name_en"]


def _money(conn, amount: int | None, lang: str) -> str:
    if amount is None:
        return T(lang, "price pending", "цена уточняется")
    return f"{domain.currency(conn)}{amount}"


def _pcs(n: int, lang: str) -> str:
    if lang != "ru":
        return f"{n} pc" if n == 1 else f"{n} pcs"
    return f"{n} шт"


@dataclass
class Actor:
    """Who is marking and on what device."""
    person_id: int
    show_money: bool = True
    tablet: str | None = None       # tablet token when marking on the shared tablet

    @classmethod
    def of(cls, s: Session, conn=None) -> "Actor":
        token = None
        if s.tablet and conn is not None:
            row = conn.execute("SELECT token FROM tablets WHERE acting_person = ?",
                               (s.person_id,)).fetchone()
            token = row["token"] if row else None
        return cls(person_id=s.person_id, show_money=not s.tablet, tablet=token)


# --- codes ------------------------------------------------------------------

TOKEN_RE = re.compile(r"(?:^|/)u/([A-Za-z0-9_-]{4,64})/?(?:[?#].*)?$")
NUMBER_RE = re.compile(
    r"^\s*(?:#|№|no\.?|order|заказ)?\s*(\d{1,7})(?:\s*[-/]\s*(\d{1,4}))?\s*$", re.I)


@dataclass
class Target:
    order_id: int
    number: int
    qty: int
    unit_id: int | None      # None = the sheet is for the whole order
    code: str
    warnings: tuple = ()     # from a printed sheet: old copy, order changed since printing


def _sheet_target(conn, raw: str, lang: str) -> Target | None:
    """A code printed by the paperwork feature (unit_codes, 'K7QM-2HXP', '/u/TOKEN/2').
    Its warnings (old copy, order changed) travel with the mark; only cancelled or
    shipped items are refused. None when the text is not such a code."""
    from . import paperwork
    try:
        token, _copy = paperwork.parse_code(raw)
    except DomainError:
        return None
    if not conn.execute("SELECT 1 FROM unit_codes WHERE token = ?", (token,)).fetchone():
        return None
    info = paperwork.resolve(conn, raw)
    warns = info["warnings_ru"] if lang == "ru" else info["warnings"]
    if not info["ok_to_mark"]:
        raise DomainError(" ".join(warns) or T(lang, "This item cannot be marked.",
                                                 "Эту штуку отметить нельзя."))
    o = conn.execute("SELECT qty FROM orders WHERE id = ?", (info["order_id"],)).fetchone()
    return Target(info["order_id"], info["order_number"], o["qty"], info["unit_id"],
                  f"u/{token}", tuple(warns))


def resolve(conn: sqlite3.Connection, code: str, lang: str = "en") -> Target:
    """A scanned or typed code -> the order and, when the code says so, the piece."""
    raw = (code or "").strip()
    sheet = _sheet_target(conn, raw, lang)
    if sheet is not None:
        return sheet
    m = TOKEN_RE.search(raw)
    if m:
        row = conn.execute(
            "SELECT t.unit_id, u.order_id, o.number, o.qty FROM unit_tokens t "
            "JOIN units u ON u.id = t.unit_id JOIN orders o ON o.id = u.order_id "
            "WHERE t.token = ?", (m[1],)).fetchone()
        if row is None:
            raise DomainError(T(lang, "This QR code is not in the system. Type the number "
                                      "from the sheet instead.",
                                "Этого QR-кода нет в системе. Введите номер с листка."))
        return Target(row["order_id"], row["number"], row["qty"], row["unit_id"], f"u/{m[1]}")
    m = NUMBER_RE.match(raw)
    if not m:
        raise DomainError(T(lang, f"Cannot read “{raw[:40]}”. Type the number from the "
                                  "sheet, like 214 or 214-2.",
                            f"Не понял «{raw[:40]}». Введите номер с листка, например 214 "
                            "или 214-2."))
    number, seq = int(m[1]), int(m[2]) if m[2] else None
    o = conn.execute("SELECT id, qty FROM orders WHERE number = ?", (number,)).fetchone()
    if o is None:
        raise DomainError(T(lang, f"There is no order #{number}. Check the number on the sheet.",
                            f"Заказа №{number} нет. Проверьте номер на листке."))
    if seq is None and o["qty"] == 1:
        seq = 1
    unit_id = None
    if seq is not None:
        u = conn.execute("SELECT id FROM units WHERE order_id = ? AND seq = ?",
                         (o["id"], seq)).fetchone()
        if u is None:
            raise DomainError(T(lang, f"Order #{number} has {_pcs(o['qty'], lang)}; there is "
                                      f"no piece {seq}.",
                                f"В заказе №{number} {_pcs(o['qty'], lang)}, штуки {seq} нет."))
        unit_id = u["id"]
    code_norm = f"{number}-{seq}" if seq is not None and o["qty"] > 1 else str(number)
    return Target(o["id"], number, o["qty"], unit_id, code_norm)


def _waiting_at(conn, stage_id: int, order_id: int | None = None) -> list[int]:
    ids = domain._waiting_units(conn)[stage_id]
    if order_id is None:
        return ids
    mine = {r["id"] for r in conn.execute("SELECT id FROM units WHERE order_id = ?", (order_id,))}
    return [u for u in ids if u in mine]


def _pick_units(conn, person_id: int, candidates: list[int], n: int) -> list[int]:
    """Assigned to this person first, then free ones, then the rest, in queue order."""
    who = domain.person(conn, person_id)
    owner = {r["unit_id"]: r["person_id"] for r in conn.execute(
        "SELECT unit_id, person_id FROM assignments WHERE stage_id = ?", (who["stage_id"],))}
    ranked = sorted(candidates, key=lambda u: (0 if owner.get(u) == person_id
                                               else 1 if u not in owner else 2))
    return ranked[:n]


# --- the date of the work -----------------------------------------------------

@dataclass
class WorkDay:
    work_date: str
    quality: str        # live | phone | declared | adjusted
    note: str = ""
    phone_at: str | None = None


def work_day(conn, person_id: int, created_at: str | None = None,
             for_date: str | None = None, lang: str = "en") -> WorkDay:
    """Which day a mark belongs to. Money follows this date.

    - for_date: the worker adds a missed piece for an earlier day (up to N days back).
    - created_at: when the phone says the tap happened (offline queue). Accepted when it
      is not in the future and not older than N days; otherwise the mark is still
      recorded (never lose work), for today, and the answer says so.
    """
    now = _now(conn)
    today = now.date()
    back = int(setting(conn, "mark_back_days"))
    if for_date:
        try:
            d = date.fromisoformat(str(for_date))
        except ValueError:
            raise DomainError(T(lang, "The date must look like 2026-10-27.",
                                "Дата должна быть вида 2026-10-27."))
        if d > today:
            raise DomainError(T(lang, "You cannot mark work for a day that has not come yet.",
                                "Нельзя отметить работу на будущий день."))
        if d < today - timedelta(days=back):
            raise DomainError(T(lang, f"A missed piece can be added for the last {back} days "
                                      "only. For older work, tell the manager.",
                                f"Пропущенную штуку можно добавить только за последние {back} "
                                "дня. За более старое — скажите менеджеру."))
        return _paid_note(conn, person_id, WorkDay(d.isoformat(), "declared"), lang)
    if not created_at:
        return WorkDay(today.isoformat(), "live")
    at = _parse_dt(conn, created_at)
    if at is None:
        return WorkDay(today.isoformat(), "adjusted",
                       T(lang, "The phone sent an unreadable time; recorded for today.",
                         "Телефон прислал непонятное время; записано на сегодня."),
                       str(created_at)[:40])
    if at > now + timedelta(minutes=2):
        return WorkDay(today.isoformat(), "adjusted",
                       T(lang, "The phone clock is ahead; recorded for today.",
                         "Часы телефона спешат; записано на сегодня."), _iso(at))
    if at < now - timedelta(days=back):
        return WorkDay(today.isoformat(), "adjusted",
                       T(lang, f"The phone says this was tapped on {at.date().isoformat()}, "
                               f"more than {back} days ago. Recorded for today; tell the "
                               "manager if it was another day.",
                         f"Телефон говорит, что отметка от {at.date().isoformat()} — больше "
                         f"{back} дней назад. Записано на сегодня; если это был другой день, "
                         "скажите менеджеру."), _iso(at))
    wd = WorkDay(at.date().isoformat(), "phone" if at < now - timedelta(minutes=1) else "live",
                 phone_at=_iso(at))
    if at.date() != today:
        wd.note = T(lang, f"Recorded for {at.date().isoformat()}, the day you tapped it.",
                    f"Записано на {at.date().isoformat()} — в тот день, когда нажали.")
    return _paid_note(conn, person_id, wd, lang)


def _paid_note(conn, person_id: int, wd: WorkDay, lang: str) -> WorkDay:
    """A late piece for a day that is already paid out is not lost and not mixed into
    the paid sum: it is a new unpaid record of that day, paid next time."""
    row = conn.execute("SELECT MAX(created_at) AS at FROM payouts WHERE person_id = ?",
                       (person_id,)).fetchone()
    if row and row["at"] and wd.work_date <= row["at"][:10]:
        extra = T(lang, "That day is already paid out; this piece is paid with the next payout.",
                  "Этот день уже выплачен; штука войдёт в следующую выплату.")
        wd.note = (wd.note + " " + extra).strip()
    return wd


# --- marking ----------------------------------------------------------------

def _today_totals(conn, person_id: int, show_money: bool) -> dict:
    day = _now(conn).date().isoformat()
    rows = conn.execute(f"SELECT unit_id, stage_id FROM ({LIVE_DONE}) WHERE person_id = ? "
                        "AND work_date = ?", (person_id, day)).fetchall()
    money, pending = 0, 0
    for r in rows:
        a = domain.rate(conn, r["unit_id"], r["stage_id"])
        if a is None:
            pending += 1
        else:
            money += a
    return {"count": len(rows), "money": money if show_money else None,
            "price_pending": pending}


def _event_answer(conn, actor: Actor, ev: sqlite3.Row, status: str, lang: str,
                  note: str = "") -> dict:
    st = _stage(conn, ev["stage_id"])
    label = domain.unit_label(conn, ev["unit_id"])
    amount = domain.rate(conn, ev["unit_id"], ev["stage_id"])
    product = _product(conn, ev["unit_id"])
    sname = _sname(st, lang)
    who = ""
    if actor.tablet:  # on the shared tablet always say whose mark it is
        who = T(lang, " for ", " на ") + domain.person(conn, actor.person_id)["name"]
    if status == "already":
        if ev["person_id"] != actor.person_id:
            other = domain.person(conn, ev["person_id"])["name"]
            msg = T(lang, f"Already recorded by {other}: {label}, {sname}, "
                          f"{ev['work_date']} {ev['created_at'][11:16]}. Nothing changed.",
                    f"Уже записано на {other}: {label}, {sname}, "
                    f"{ev['work_date']} {ev['created_at'][11:16]}. Ничего не изменилось.")
        else:
            msg = T(lang, f"Already recorded: {label}, {sname}. Nothing changed.",
                    f"Уже записано: {label}, {sname}. Ничего не изменилось.")
    else:
        money = f", {_money(conn, amount, lang)}" if actor.show_money else ""
        msg = T(lang, f"Recorded{who}: {label}, {sname}{money}.",
                f"Записано{who}: {label}, {sname}{money}.")
        if amount is None and actor.show_money:
            msg += T(lang, " The owner is asked to set the price.",
                     " Владельцу отправлено: вписать цену.")
    if note:
        msg += " " + note
    return {"ok": True, "status": status, "already": status == "already",
            "event_id": ev["id"], "unit_id": ev["unit_id"], "unit_label": label,
            "product": product, "stage": st["name_en"], "stage_ru": st["name_ru"],
            "work_date": ev["work_date"], "mine": ev["person_id"] == actor.person_id,
            "amount": amount if actor.show_money else None,
            "price_pending": amount is None,
            "today": _today_totals(conn, actor.person_id, actor.show_money),
            "message": msg}


def _log_scan(conn, person_id: int, code: str, at: datetime, outcome: str,
              unit_id: int | None = None, event_id: int | None = None) -> None:
    conn.execute("INSERT INTO scan_log (person_id, code, unit_id, event_id, outcome, at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (person_id, code, unit_id, event_id, outcome,
                                               _iso(at)))
    conn.commit()


def _rate_missing_signal(conn, unit_id: int, stage_id: int, person_id: int) -> None:
    o = domain._unit_order(conn, unit_id)
    if conn.execute("SELECT 1 FROM shop_signals WHERE kind = 'rate_missing' AND status = 'open' "
                    "AND product_id = ? AND stage_id = ?", (o["product_id"], stage_id)).fetchone():
        return
    p = conn.execute("SELECT name FROM products WHERE id = ?", (o["product_id"],)).fetchone()
    st = _stage(conn, stage_id)
    conn.execute("INSERT INTO shop_signals (kind, text, product_id, stage_id, person_id, "
                 "created_at) VALUES ('rate_missing', ?, ?, ?, ?, ?)",
                 (f"Set the {st['name_en']} rate for {p['name']}: work is being done without it.",
                  o["product_id"], stage_id, person_id, _iso(_now(conn))))
    conn.commit()


def mark(conn: sqlite3.Connection, actor: Actor, *, code: str | None = None,
         unit_id: int | None = None, order_id: int | None = None, method: str = "button",
         idem_key: str | None = None, created_at: str | None = None,
         for_date: str | None = None, confirm_next: bool = False) -> dict:
    """Mark my stage done on one piece. The single entry point for every way in.

    Returns {"status": "recorded" | "already" | "ask", "message": ..., ...}.
    "ask" only happens for a quick repeat of a multi-piece order sheet: the person
    confirms (confirm_next=True) before a second piece is marked.
    """
    lang = lang_of(conn, actor.person_id)
    if idem_key:
        idem_key = str(idem_key)[:80]
        prev = conn.execute("SELECT * FROM work_events WHERE idem_key = ?", (idem_key,)).fetchone()
        if prev is not None:
            return _event_answer(conn, actor, prev, "already", lang)
    who = domain.person(conn, actor.person_id)
    if who["stage_id"] is None:
        raise DomainError(T(lang, "You have no stage set. Ask the manager to set your stage.",
                            "У вас не указан участок. Попросите менеджера его указать."))
    my_stage = who["stage_id"]
    tap_at = (_parse_dt(conn, created_at) if created_at else None) or _now(conn)

    target: Target | None = None
    if code is not None:
        target = resolve(conn, code, lang)
        method = method if method != "button" else "scan"
    elif unit_id is not None:
        if conn.execute("SELECT 1 FROM units WHERE id = ?", (unit_id,)).fetchone() is None:
            raise DomainError(T(lang, "No such piece.", "Такой штуки нет."))
    elif order_id is not None:
        o = conn.execute("SELECT id, number, qty FROM orders WHERE id = ?", (order_id,)).fetchone()
        if o is None:
            raise DomainError(T(lang, "No such order.", "Такого заказа нет."))
        target = Target(o["id"], o["number"], o["qty"], None, f"order:{o['id']}")
    else:
        raise DomainError(T(lang, "Say which piece: scan the sheet or type its number.",
                            "Какую штуку? Отсканируйте листок или введите номер."))

    if target is not None and code is not None:
        # A scanner firing twice: same person, same code, a few seconds apart.
        last = conn.execute("SELECT * FROM scan_log WHERE person_id = ? AND code = ? "
                            "ORDER BY id DESC LIMIT 1", (actor.person_id, target.code)).fetchone()
        if last is not None and last["event_id"]:
            gap = (tap_at - _parse_dt(conn, last["at"])).total_seconds()
            if 0 <= gap < float(setting(conn, "debounce_sec")):
                ev = conn.execute("SELECT * FROM work_events WHERE id = ?",
                                  (last["event_id"],)).fetchone()
                out = _event_answer(conn, actor, ev, "already", lang)
                out["message"] = T(lang, f"Same code twice in a row — counted once: "
                                         f"{out['unit_label']}.",
                                   f"Один и тот же код дважды подряд — засчитан один раз: "
                                   f"{out['unit_label']}.")
                _log_scan(conn, actor.person_id, target.code, tap_at, "debounced",
                          ev["unit_id"], ev["id"])
                return out

    if target is not None and target.unit_id is None:
        unit_id = _unit_for_order_sheet(conn, actor, target, my_stage, tap_at, confirm_next, lang)
        if isinstance(unit_id, dict):
            return unit_id
    elif target is not None:
        unit_id = target.unit_id

    wd = work_day(conn, actor.person_id, created_at, for_date, lang)
    order = domain._unit_order(conn, unit_id)
    if wd.work_date < order["created_at"][:10]:
        wd = WorkDay(order["created_at"][:10], "adjusted",
                     T(lang, "The order did not exist on that day; recorded for the day it "
                             "was created.",
                       "В тот день заказа ещё не было; записано на день создания заказа."),
                     wd.phone_at)
    try:
        res = domain.mark_done(conn, actor.person_id, unit_id, idem_key=idem_key, method=method,
                               work_date=wd.work_date, show_money=True)
    except sqlite3.IntegrityError:  # the same idem_key won a race in another request
        conn.rollback()
        prev = conn.execute("SELECT * FROM work_events WHERE idem_key = ?", (idem_key,)).fetchone()
        return _event_answer(conn, actor, prev, "already", lang)
    ev = conn.execute("SELECT * FROM work_events WHERE id = ?", (res.event_id,)).fetchone()
    if not res.already:
        conn.execute("INSERT OR IGNORE INTO mark_meta (event_id, phone_at, received_at, "
                     "time_quality, note, code, tablet) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (ev["id"], wd.phone_at, _iso(_now(conn)), wd.quality, wd.note or None,
                      target.code if target else None, actor.tablet))
        conn.commit()
        if res.amount is None:
            _rate_missing_signal(conn, unit_id, ev["stage_id"], actor.person_id)
    if target is not None and code is not None:
        _log_scan(conn, actor.person_id, target.code, tap_at,
                  "already" if res.already else "recorded", unit_id, ev["id"])
    out = _event_answer(conn, actor, ev, "already" if res.already else "recorded", lang,
                        note=wd.note if not res.already else "")
    if target is not None and target.warnings:
        out["warnings"] = list(target.warnings)
        out["message"] += " " + " ".join(target.warnings)
    return out


def _unit_for_order_sheet(conn, actor: Actor, target: Target, my_stage: int, tap_at: datetime,
                          confirm_next: bool, lang: str) -> int | dict:
    """A sheet for a whole multi-piece order: mark the next piece waiting at my stage.

    A quick repeat of the same sheet is usually a re-check, so it asks first.
    When every piece is done at my stage, it says so and changes nothing.
    """
    waiting = _waiting_at(conn, my_stage, target.order_id)
    st = _stage(conn, my_stage)
    if not waiting:
        units = [r["id"] for r in conn.execute("SELECT id FROM units WHERE order_id = ? "
                                               "ORDER BY seq", (target.order_id,))]
        done = [u for u in units if my_stage in domain.done_stage_ids(conn, u)]
        if len(done) == len(units):
            ev = conn.execute(f"SELECT * FROM ({LIVE_DONE}) WHERE unit_id = ? AND stage_id = ?",
                              (done[-1], my_stage)).fetchone()
            out = _event_answer(conn, actor, ev, "already", lang)
            out["message"] = T(
                lang, f"All {_pcs(len(units), lang)} of #{target.number} are recorded at "
                      f"{st['name_en']}. Nothing changed. Made a mistake? Undo it in "
                      "“Done today”.",
                f"Все {_pcs(len(units), lang)} заказа №{target.number} уже записаны на этапе "
                f"«{st['name_ru']}». Ничего не изменилось. Ошиблись — отмените в «Сегодня "
                "сделал».")
            _log_scan(conn, actor.person_id, target.code, tap_at, "all_done")
            return out
        # Not at my stage yet: let mark_done explain where it is.
        return next(u for u in units if my_stage not in domain.done_stage_ids(conn, u))
    if not confirm_next:
        last = conn.execute("SELECT * FROM scan_log WHERE person_id = ? AND code = ? AND "
                            "outcome = 'recorded' ORDER BY id DESC LIMIT 1",
                            (actor.person_id, target.code)).fetchone()
        if last is not None:
            gap = (tap_at - _parse_dt(conn, last["at"])).total_seconds()
            if 0 <= gap < float(setting(conn, "repeat_ask_sec")):
                prev_label = domain.unit_label(conn, last["unit_id"])
                nxt = _pick_units(conn, actor.person_id, waiting, 1)[0]
                nxt_label = domain.unit_label(conn, nxt)
                _log_scan(conn, actor.person_id, target.code, tap_at, "asked")
                return {"ok": True, "status": "ask", "already": False,
                        "unit_label": nxt_label, "confirm": {"confirm_next": True},
                        "message": T(lang, f"{prev_label} was recorded {int(gap)} s ago. "
                                           f"Mark {nxt_label} too?",
                                     f"{prev_label} записан {int(gap)} с назад. "
                                     f"Отметить ещё и {nxt_label}?")}
    return _pick_units(conn, actor.person_id, waiting, 1)[0]


def undo(conn: sqlite3.Connection, actor: Actor, event_id: int) -> dict:
    """“Made a mistake — undo”: a visible undo record. Undoing twice is not an error."""
    lang = lang_of(conn, actor.person_id)
    ev = conn.execute("SELECT * FROM work_events WHERE id = ?", (event_id,)).fetchone()
    if ev is not None and ev["person_id"] == actor.person_id and conn.execute(
            "SELECT 1 FROM work_events WHERE kind = 'undo' AND undoes_id = ?",
            (event_id,)).fetchone():
        label = domain.unit_label(conn, ev["unit_id"])
        return {"ok": True, "status": "already", "already": True, "unit_label": label,
                "today": _today_totals(conn, actor.person_id, actor.show_money),
                "message": T(lang, f"Already undone: {label}. Nothing changed.",
                             f"Уже отменено: {label}. Ничего не изменилось.")}
    res = domain.undo(conn, actor.person_id, event_id)
    st = _stage(conn, ev["stage_id"])
    return {"ok": True, "status": "undone", "already": False, "unit_label": res.unit_label,
            "today": _today_totals(conn, actor.person_id, actor.show_money),
            "message": T(lang, f"Undone: {res.unit_label}, {st['name_en']}.",
                         f"Отменено: {res.unit_label}, {st['name_ru']}.")}


# --- offline queue ------------------------------------------------------------

MAX_SYNC = 200


def sync(conn: sqlite3.Connection, items: list[dict], *, session: Session | None,
         tablet: sqlite3.Row | None) -> dict:
    """Marks the phone or tablet queued while offline. Each answers on its own:
    recorded / already (drop from the queue), refused (keep and show the reason), and
    nothing is ever dropped silently. Refusals are not stored, so a refused tap can be
    sent again later (e.g. once the previous stage marks the piece).
    """
    if len(items) > MAX_SYNC:
        raise DomainError(f"Send at most {MAX_SYNC} marks at once.")
    out = []
    for it in items:
        key = str(it.get("idem_key") or "")[:80]
        if not key:
            out.append({"idem_key": None, "status": "refused",
                        "message": "Missing idem_key: the phone must give every tap an id."})
            continue
        try:
            actor = _sync_actor(conn, it, session, tablet)
            kind = it.get("kind", "mark")
            if kind == "undo":
                r = undo(conn, actor, int(it["event_id"]))
            else:
                r = mark(conn, actor, code=it.get("code"), unit_id=it.get("unit_id"),
                         order_id=it.get("order_id"), method=it.get("method") or "button",
                         idem_key=key, created_at=it.get("created_at"),
                         for_date=it.get("for_date"),
                         confirm_next=bool(it.get("confirm_next")))
            if r["status"] == "ask":
                # Offline nobody could be asked: keep it for the person to confirm.
                r = {**r, "status": "refused", "needs_confirm": True}
            out.append({"idem_key": key, **r})
        except DomainError as exc:
            out.append({"idem_key": key, "status": "refused", "ok": False, "message": str(exc)})
    left = [o for o in out if o["status"] == "refused"]
    return {"ok": True, "results": out, "recorded": sum(o["status"] in ("recorded", "undone")
                                                         for o in out),
            "already": sum(o["status"] == "already" for o in out), "refused": len(left),
            "message": f"Sent {len(out)}: {len(out) - len(left)} in, {len(left)} need a look."}


def _sync_actor(conn, it: dict, session: Session | None, tablet: sqlite3.Row | None) -> Actor:
    if tablet is None:
        if session is None:
            raise DomainError("Sign in first.")
        return Actor(session.person_id, show_money=not session.tablet)
    # Shared tablet: accept the tap for whoever was signed in on it at that moment,
    # even if they have been signed out since (that is how queued marks got lost).
    pid, at = it.get("person_id"), _parse_dt(conn, it.get("created_at") or "")
    if pid is None or at is None:
        raise DomainError("A tablet mark needs the person and the time of the tap.")
    grace = timedelta(seconds=5)
    for sh in conn.execute("SELECT * FROM tablet_shifts WHERE tablet = ? AND person_id = ?",
                           (tablet["token"], int(pid))):
        start = _parse_dt(conn, sh["started_at"]) - grace
        end = (_parse_dt(conn, sh["ended_at"]) if sh["ended_at"] else _now(conn)) + grace
        if start <= at <= end:
            return Actor(int(pid), show_money=False, tablet=tablet["token"])
    raise DomainError("This tap was made when that person was not signed in on this tablet.")


# --- voice and photo: proposals only ------------------------------------------------

def _kind_words(conn) -> dict[str, str]:
    """Catalog words people use for products -> product kind (product names, synonyms)."""
    out = {}
    for p in conn.execute("SELECT id, name, kind FROM products"):
        for w in re.findall(r"[a-zа-я]+", p["name"].lower()):
            if len(w) > 3 and w not in ("chair", "sofa", "seat"):
                out.setdefault(w, p["kind"])
    for s in conn.execute("SELECT s.alias, p.kind FROM synonyms s JOIN products p "
                          "ON p.id = s.target_id WHERE s.kind = 'product'"):
        if " " not in s["alias"]:
            out.setdefault(s["alias"], s["kind"])
    return out


SPARK_PROMPT = """A furniture worker said this on the shop floor (English or Russian):
<<<{text}>>>
Return JSON only: {{"intents": [ ... ]}} where each intent is one of
{{"kind": "mark", "order": <order number>, "seq": <piece number or null>, "quote": "<exact words>"}}
{{"kind": "mark_qty", "qty": <how many>, "product_kind": "<one of: {kinds}>", "quote": "<exact words>"}}
{{"kind": "material_out", "text": "<what ran out>", "quote": "<exact words>"}}
{{"kind": "undo_last", "quote": "<exact words>"}}
Every quote must be copied exactly from the words above. If unsure, return no intent."""


def _spark_intents(conn, text: str) -> list[speech.Intent]:
    kinds = sorted({r["kind"] for r in conn.execute("SELECT DISTINCT kind FROM products")})
    data = models.respond(conn, "marking-voice-parse",
                          SPARK_PROMPT.format(text=text[:500], kinds=", ".join(kinds)),
                          max_output_tokens=400)
    from ..intake import _output_text
    m = re.search(r"\{.*\}", _output_text(data), re.S)
    out = []
    for it in (json.loads(m[0]).get("intents", []) if m else []):
        quote = str(it.get("quote") or "")
        if not quote or speech.norm(quote) not in speech.norm(text):
            continue  # grounding: only what was actually said
        k = it.get("kind")
        if k == "mark" and str(it.get("order", "")).isdigit():
            seq = it.get("seq")
            out.append(speech.Intent("mark", quote, order=int(it["order"]),
                                     seq=int(seq) if str(seq).isdigit() else None))
        elif k == "mark_qty" and str(it.get("qty", "")).isdigit() and it.get("product_kind") in kinds:
            out.append(speech.Intent("mark_qty", quote, qty=int(it["qty"]),
                                     product_kind=it["product_kind"]))
        elif k == "material_out" and it.get("text"):
            out.append(speech.Intent("material_out", quote, text=str(it["text"])[:120]))
        elif k == "undo_last":
            out.append(speech.Intent("undo_last", quote))
    return out


def propose(conn: sqlite3.Connection, actor: Actor, text: str, *, method: str = "voice",
            heard_by: str = "browser-speech", created_at: str | None = None,
            intents: list[speech.Intent] | None = None) -> dict:
    """What was said or read -> a proposal “Understood as: … [Yes] [No]”.
    Nothing is marked here; only decide(yes) applies the stored items."""
    lang = lang_of(conn, actor.person_id)
    text = (text or "").strip()[:500]
    who = domain.person(conn, actor.person_id)
    if who["stage_id"] is None:
        raise DomainError(T(lang, "You have no stage set. Ask the manager to set your stage.",
                            "У вас не указан участок. Попросите менеджера его указать."))
    model = heard_by + "+rules"
    if intents is None:
        parsed = speech.parse(text, _kind_words(conn))
        if parsed.negated:
            return {"ok": False, "status": "nothing", "heard": text,
                    "message": T(lang, f"Heard “{text}” — that says it is not done, so "
                                       "nothing to record.",
                                 f"Услышал «{text}» — это «не готово», записывать нечего.")}
        intents = parsed.intents
        if not intents:
            try:
                intents = _spark_intents(conn, text)
                model = heard_by + "+" + models.SPARK
            except (BudgetStop, ValueError, KeyError):
                intents = []
    if not intents:
        return {"ok": False, "status": "nothing", "heard": text,
                "message": T(lang, f"Heard “{text}”, but found no order number. Say it like "
                                   "“two hundred fourteen done”, or type the number.",
                             f"Услышал «{text}», но не нашёл номер заказа. Скажите, например, "
                             "«двести четырнадцатый готов» или введите номер.")}
    stage = _stage(conn, who["stage_id"])
    items, taken = [], set()
    for it in intents:
        items.extend(_items_for(conn, actor, it, stage, taken, lang))
    doable = [i for i in items if i["type"] != "problem"]
    understood = "; ".join(i["say"] for i in items)
    payload = {"heard": text, "method": method, "created_at": created_at, "items": items,
               "stage_id": stage["id"]}
    aid = None
    if doable:
        aid = conn.execute(
            "INSERT INTO agent_actions (kind, payload, model, created_at, for_person) "
            "VALUES ('marking', ?, ?, ?, ?)",
            (json.dumps(payload, ensure_ascii=False), model, _iso(_now(conn)),
             actor.person_id)).lastrowid
        conn.commit()
    return {"ok": bool(doable), "status": "proposal" if doable else "nothing", "id": aid,
            "heard": text, "model": model, "items": items, "can_apply": len(doable),
            "message": T(lang, "Understood as: ", "Понял так: ") + understood
            + ("" if doable else T(lang, " Nothing to record.", " Записывать нечего."))}


def _product(conn, unit_id: int) -> str:
    return conn.execute("SELECT p.name FROM units u JOIN orders o ON o.id = u.order_id "
                        "JOIN products p ON p.id = o.product_id WHERE u.id = ?",
                        (unit_id,)).fetchone()["name"]


def _items_for(conn, actor: Actor, it: speech.Intent, stage, taken: set, lang: str) -> list[dict]:
    sname = _sname(stage, lang)
    if it.kind == "mark":
        code = f"{it.order}-{it.seq}" if it.seq else str(it.order)
        try:
            target = resolve(conn, code, lang)
        except DomainError as exc:
            return [{"type": "problem", "quote": it.quote, "say": str(exc)}]
        if target.unit_id is None:
            waiting = [u for u in _waiting_at(conn, stage["id"], target.order_id) if u not in taken]
            if not waiting:
                return [{"type": "problem", "quote": it.quote,
                         "say": T(lang, f"#{target.number}: nothing waiting at {sname}",
                                  f"№{target.number}: на этапе «{sname}» ничего не ждёт")}]
            uid = _pick_units(conn, actor.person_id, waiting, 1)[0]
        else:
            uid = target.unit_id
        taken.add(uid)
        label = domain.unit_label(conn, uid)
        return [{"type": "mark", "unit_id": uid, "label": label, "quote": it.quote,
                 "product": _product(conn, uid), "stage": stage["name_en"],
                 "say": f"{label} {_product(conn, uid)}, {sname}"}]
    if it.kind == "mark_qty":
        cands = [u for u in _waiting_at(conn, stage["id"]) if u not in taken and
                 conn.execute("SELECT 1 FROM units u JOIN orders o ON o.id = u.order_id JOIN "
                              "products p ON p.id = o.product_id WHERE u.id = ? AND p.kind = ?",
                              (u, it.product_kind)).fetchone()]
        picked = _pick_units(conn, actor.person_id, cands, it.qty or 1)
        out = []
        for uid in picked:
            taken.add(uid)
            label = domain.unit_label(conn, uid)
            out.append({"type": "mark", "unit_id": uid, "label": label, "quote": it.quote,
                        "product": _product(conn, uid), "stage": stage["name_en"],
                        "say": f"{label} {_product(conn, uid)}, {sname}"})
        if len(picked) < (it.qty or 1):
            out.append({"type": "problem", "quote": it.quote,
                        "say": T(lang, f"only {len(picked)} {it.product_kind} waiting at {sname}, "
                                       f"not {it.qty}",
                                 f"на этапе «{sname}» ждут только {len(picked)}, а не {it.qty}")})
        return out
    if it.kind == "undo_last":
        ev = conn.execute(f"SELECT * FROM ({LIVE_DONE}) WHERE person_id = ? ORDER BY id DESC "
                          "LIMIT 1", (actor.person_id,)).fetchone()
        if ev is None:
            return [{"type": "problem", "quote": it.quote,
                     "say": T(lang, "nothing of yours to undo", "отменять нечего")}]
        label = domain.unit_label(conn, ev["unit_id"])
        return [{"type": "undo", "event_id": ev["id"], "label": label, "quote": it.quote,
                 "say": T(lang, f"undo {label}", f"отменить {label}")}]
    if it.kind == "material_out":
        return [{"type": "signal", "text": it.text, "quote": it.quote,
                 "say": T(lang, f"tell the office: out of {it.text}",
                          f"сообщить в офис: кончилось «{it.text}»")}]
    if it.kind == "other_work":
        mins = f", {it.minutes} min" if it.minutes else ""
        return [{"type": "claim", "text": it.text or it.quote, "minutes": it.minutes,
                 "quote": it.quote,
                 "say": T(lang, f"other work: {it.text}{mins} (manager approves)",
                          f"другая работа: {it.text}{mins} (подтвердит менеджер)")}]
    return []


def decide(conn: sqlite3.Connection, actor: Actor, action_id: int, yes: bool) -> dict:
    """Apply (Yes) or drop (No) a stored proposal. Only the stored items are applied,
    each with its own idem key, so a double tap on Yes records once."""
    lang = lang_of(conn, actor.person_id)
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind = 'marking'",
                       (action_id,)).fetchone()
    if row is None:
        raise DomainError(T(lang, "No such proposal.", "Такого предложения нет."))
    if row["for_person"] != actor.person_id:
        raise DomainError(T(lang, "This proposal is someone else's.", "Это чужое предложение."))
    if row["status"] == "applied":
        res = json.loads(row["result"] or "{}")
        return {"ok": True, "status": "already", "results": res.get("results", []),
                "message": T(lang, "Already done. Nothing changed.", "Уже сделано. Ничего не "
                                                                   "изменилось.")}
    if row["status"] == "rejected":
        return {"ok": True, "status": "already",
                "message": T(lang, "Already dismissed. Nothing was recorded.",
                             "Уже отклонено. Ничего не записано.")}
    when = _iso(_now(conn))
    if not yes:
        conn.execute("UPDATE agent_actions SET status = 'rejected', decided_by = ?, "
                     "decided_at = ?, result = ? WHERE id = ? AND status = 'proposed'",
                     (actor.person_id, when, json.dumps({"reason": "no"}), action_id))
        conn.commit()
        return {"ok": True, "status": "rejected",
                "message": T(lang, "OK, nothing recorded.", "Хорошо, ничего не записано.")}
    age = _now(conn) - _parse_dt(conn, row["created_at"])
    if age > timedelta(minutes=int(setting(conn, "proposal_ttl_min"))):
        conn.execute("UPDATE agent_actions SET status = 'rejected', decided_at = ?, result = ? "
                     "WHERE id = ? AND status = 'proposed'",
                     (when, json.dumps({"reason": "expired"}), action_id))
        conn.commit()
        raise DomainError(T(lang, "This was said too long ago. Say it again.",
                            "Это было слишком давно. Скажите ещё раз."))
    payload = json.loads(row["payload"])
    results = []
    for i, item in enumerate(payload["items"]):
        key = f"aa{action_id}.{i}"
        try:
            if item["type"] == "mark":
                r = mark(conn, actor, unit_id=item["unit_id"], method=payload["method"],
                         idem_key=key, created_at=payload.get("created_at"))
            elif item["type"] == "undo":
                r = undo(conn, actor, item["event_id"])
            elif item["type"] == "signal":
                r = material_signal(conn, actor, item["text"])
            elif item["type"] == "claim":
                r = create_claim(conn, actor, description=item["text"], minutes=item["minutes"],
                                 idem_key=key, created_at=payload.get("created_at"))
            else:
                continue
            results.append({"item": i, "ok": True, "status": r["status"],
                            "message": r["message"]})
        except DomainError as exc:
            results.append({"item": i, "ok": False, "status": "refused", "message": str(exc)})
    conn.execute("UPDATE agent_actions SET status = 'applied', decided_by = ?, decided_at = ?, "
                 "result = ? WHERE id = ? AND status = 'proposed'",
                 (actor.person_id, when, json.dumps({"results": results}, ensure_ascii=False),
                  action_id))
    conn.commit()
    good = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    msg = " ".join(r["message"] for r in good)
    if bad:
        msg += " " + T(lang, "Not recorded: ", "Не записано: ") + " ".join(r["message"] for r in bad)
    return {"ok": bool(good), "status": "applied", "results": results,
            "today": _today_totals(conn, actor.person_id, actor.show_money),
            "message": msg.strip()}


def voice_audio(conn, actor: Actor, audio: bytes, mime: str, created_at: str | None) -> dict:
    lang = lang_of(conn, actor.person_id)
    if not audio:
        raise DomainError(T(lang, "Nothing was recorded. Hold the button and speak.",
                            "Ничего не записалось. Держите кнопку и говорите."))
    try:
        text = models.transcribe(conn, "marking-voice", audio, mime,
                                 language="ru" if lang == "ru" else "en")
    except BudgetStop:
        raise DomainError(T(lang, "Server speech recognition is off. The phone's own "
                                  "recognition still works — or type the number.",
                            "Распознавание речи на сервере выключено. Работает распознавание "
                            "самого телефона — или введите номер."))
    return propose(conn, actor, text, method="voice", heard_by=models.VOICE,
                   created_at=created_at)


PHOTO_PROMPT = """This is a photo of a paper work sheet from a furniture shop. Find the order
numbers on it (and piece numbers like 214-2 or 214/2 if written). Return JSON only:
{"numbers": [{"order": 214, "seq": null, "quote": "<exactly as printed>"}]}"""


def photo(conn, actor: Actor, image: bytes, mime: str, created_at: str | None) -> dict:
    """Photo of a sheet -> Spark vision reads the numbers -> the same proposal flow."""
    lang = lang_of(conn, actor.person_id)
    if not image or len(image) > 6_000_000:
        raise DomainError(T(lang, "Send one photo under 6 MB.", "Пришлите одно фото до 6 МБ."))
    data_url = f"data:{mime or 'image/jpeg'};base64," + base64.b64encode(image).decode()
    try:
        data = models.respond(conn, "marking-photo", [{"role": "user", "content": [
            {"type": "input_text", "text": PHOTO_PROMPT},
            {"type": "input_image", "image_url": data_url}]}], max_output_tokens=300)
    except BudgetStop:
        raise DomainError(T(lang, "Reading photos needs the Meta model, which is off right now. "
                                  "Scan the QR on the sheet or type its number.",
                            "Чтение фото требует модели Meta, она сейчас выключена. "
                            "Отсканируйте QR на листке или введите номер."))
    from ..intake import _output_text
    m = re.search(r"\{.*\}", _output_text(data), re.S)
    intents = []
    for n in (json.loads(m[0]).get("numbers", []) if m else []):
        if str(n.get("order", "")).isdigit():
            seq = n.get("seq")
            intents.append(speech.Intent("mark", str(n.get("quote") or n["order"]),
                                         order=int(n["order"]),
                                         seq=int(seq) if str(seq).isdigit() else None))
    if not intents:
        return {"ok": False, "status": "nothing",
                "message": T(lang, "No order number found on the photo. Type it instead.",
                             "На фото не нашёл номер заказа. Введите его.")}
    return propose(conn, actor, " ".join(i.quote for i in intents), method="photo",
                   heard_by=models.SPARK, created_at=created_at, intents=intents)


# --- material signals ---------------------------------------------------------------

def material_signal(conn, actor: Actor, text: str) -> dict:
    lang = lang_of(conn, actor.person_id)
    text = (text or "").strip()[:120]
    if len(text) < 2:
        raise DomainError(T(lang, "Say what ran out.", "Скажите, что кончилось."))
    who = domain.person(conn, actor.person_id)
    # SQLite lower() only folds ASCII, so compare in Python (Cyrillic material names).
    dup = next((r for r in conn.execute("SELECT id, text FROM shop_signals WHERE kind = "
                                        "'material_out' AND status = 'open'")
                if r["text"].casefold() == text.casefold()), None)
    if dup:
        return {"ok": True, "status": "already", "signal_id": dup["id"],
                "message": T(lang, f"The office already knows: out of {text}.",
                             f"Офис уже знает: кончилось «{text}».")}
    sid = conn.execute("INSERT INTO shop_signals (kind, text, stage_id, person_id, created_at) "
                       "VALUES ('material_out', ?, ?, ?, ?)",
                       (text, who["stage_id"], actor.person_id, _iso(_now(conn)))).lastrowid
    conn.commit()
    return {"ok": True, "status": "recorded", "signal_id": sid,
            "message": T(lang, f"Sent to the office: out of {text}.",
                         f"Отправлено в офис: кончилось «{text}».")}


# --- "Did other work" ---------------------------------------------------------------

def claim_presets(conn, person_id: int) -> list[dict]:
    lang = lang_of(conn, person_id)
    who = domain.person(conn, person_id)
    rows = conn.execute("SELECT * FROM claim_presets WHERE active = 1 AND (stage_id IS NULL OR "
                        "stage_id = ?) ORDER BY stage_id IS NULL, id", (who["stage_id"],))
    return [{"id": r["id"], "name": r["name_ru"] if lang == "ru" else r["name_en"],
             "amount": r["amount"], "by_minutes": r["amount"] is None} for r in rows]


def _expected(conn, preset, qty, minutes, amount) -> int | None:
    if preset is not None and preset["amount"] is not None:
        return preset["amount"] * (qty or 1)
    if amount is not None:
        return amount
    rate = setting(conn, "hourly_rate")
    if minutes and rate:
        return max(1, round(minutes * float(rate) / 60))
    return None   # no rate is not 0: the manager sets the amount


def create_claim(conn: sqlite3.Connection, actor: Actor, *, description: str = "",
                 preset_id: int | None = None, qty: int | None = None,
                 minutes: int | None = None, amount: int | None = None,
                 for_date: str | None = None, created_at: str | None = None,
                 idem_key: str | None = None) -> dict:
    """Work without a sheet (bags, fitting, repairs). Counts only after approval."""
    lang = lang_of(conn, actor.person_id)
    if idem_key:
        prev = conn.execute("SELECT * FROM work_claims WHERE idem_key = ?", (idem_key,)).fetchone()
        if prev is not None:
            return _claim_answer(conn, actor, prev, "already", lang)
    preset = None
    if preset_id is not None:
        preset = conn.execute("SELECT * FROM claim_presets WHERE id = ? AND active = 1",
                              (preset_id,)).fetchone()
        if preset is None:
            raise DomainError(T(lang, "Pick one of the listed jobs.", "Выберите работу из списка."))
        description = description or (preset["name_ru"] if lang == "ru" else preset["name_en"])
    description = re.sub(r"\s+", " ", description or "").strip()
    if len(description) < 3:
        raise DomainError(T(lang, "Say what you did, in a few words.",
                            "Напишите в двух словах, что сделали."))
    if len(description) > 200:
        raise DomainError(T(lang, "Keep the description under 200 characters.",
                            "Описание — до 200 знаков."))
    if qty is not None and not 1 <= int(qty) <= 100:
        raise DomainError(T(lang, "How many: 1 to 100.", "Сколько штук: от 1 до 100."))
    if minutes is not None and not 1 <= int(minutes) <= 600:
        raise DomainError(T(lang, "Minutes: 1 to 600.", "Минут: от 1 до 600."))
    if amount is not None:
        if not actor.show_money:
            raise DomainError(T(lang, "Amounts are not entered on the shared tablet. Give the "
                                      "minutes; the manager sets the amount.",
                                "На общем планшете суммы не вводят. Укажите минуты, сумму "
                                "поставит менеджер."))
        cap = int(setting(conn, "claim_max_amount"))
        if not 1 <= int(amount) <= cap:
            raise DomainError(T(lang, f"One claim is from 1 to {domain.currency(conn)}{cap}. "
                                      "More than that — talk to the manager.",
                                f"Одна заявка — от 1 до {domain.currency(conn)}{cap}. Больше — "
                                "через менеджера."))
    if preset is None and minutes is None and amount is None:
        raise DomainError(T(lang, "Give the minutes it took (or the amount).",
                            "Укажите, сколько минут заняло (или сумму)."))
    if preset is not None and preset["amount"] is None and minutes is None:
        raise DomainError(T(lang, "This job is paid by time: give the minutes.",
                            "Эта работа оплачивается по времени: укажите минуты."))
    wd = work_day(conn, actor.person_id, created_at, for_date, lang)
    if minutes:
        used = conn.execute("SELECT COALESCE(SUM(minutes), 0) FROM work_claims WHERE "
                            "person_id = ? AND work_date = ? AND status IN ('pending', "
                            "'approved')", (actor.person_id, wd.work_date)).fetchone()[0]
        cap = int(setting(conn, "claim_max_minutes_day"))
        if used + int(minutes) > cap:
            raise DomainError(T(lang, f"That would be over {cap // 60} hours of other work on "
                                      f"{wd.work_date}. Talk to the manager.",
                                f"Получится больше {cap // 60} часов другой работы за "
                                f"{wd.work_date}. Поговорите с менеджером."))
    asked = _expected(conn, preset, qty, minutes, amount)
    try:
        cid = conn.execute(
            "INSERT INTO work_claims (person_id, work_date, description, preset_id, qty, minutes, "
            "asked_amount, created_at, idem_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (actor.person_id, wd.work_date, description, preset_id, qty, minutes, asked,
             _iso(_now(conn)), idem_key)).lastrowid
    except sqlite3.IntegrityError:
        conn.rollback()
        prev = conn.execute("SELECT * FROM work_claims WHERE idem_key = ?", (idem_key,)).fetchone()
        return _claim_answer(conn, actor, prev, "already", lang)
    conn.commit()
    row = conn.execute("SELECT * FROM work_claims WHERE id = ?", (cid,)).fetchone()
    out = _claim_answer(conn, actor, row, "recorded", lang)
    hint = _order_in_text(conn, description)
    if hint:
        out["hint_order"] = hint
        out["message"] += " " + T(
            lang, f"This mentions order #{hint}. If it is work on that order, mark it as work "
                  "instead: it gets the right price and day.",
            f"Здесь упомянут заказ №{hint}. Если это работа по нему — отметьте её как работу: "
            "будет верная цена и день.")
    return out


def _order_in_text(conn, text: str) -> int | None:
    for n in re.findall(r"(?<![\d.,])(\d{3,7})(?![\d.,])", text):
        if conn.execute("SELECT 1 FROM orders WHERE number = ?", (int(n),)).fetchone():
            return int(n)
    return None


def _claim_answer(conn, actor: Actor, row, status: str, lang: str) -> dict:
    d = _claim_dict(conn, row, actor.show_money, lang)
    if status == "already":
        msg = T(lang, f"Already sent: {d['description']}. Nothing changed.",
                f"Уже отправлено: {d['description']}. Ничего не изменилось.")
    else:
        money = ""
        if actor.show_money and d["asked_amount"] is not None:
            money = f" (~{domain.currency(conn)}{d['asked_amount']})"
        what = d["description"] + (f", {d['minutes']} min" if d["minutes"] else "") + \
            (f" × {d['qty']}" if d["qty"] else "")
        msg = T(lang, f"Sent to the manager: {what}{money}. It counts after approval.",
                f"Отправлено менеджеру: {what}{money}. Засчитается после подтверждения.")
    return {"ok": True, "status": status, "claim": d, "message": msg}


def _claim_dict(conn, r, show_money: bool, lang: str = "en") -> dict:
    who = domain.person(conn, r["person_id"])
    return {"id": r["id"], "person_id": r["person_id"], "person": who["name"],
            "work_date": r["work_date"], "description": r["description"], "qty": r["qty"],
            "minutes": r["minutes"], "status": r["status"],
            "asked_amount": r["asked_amount"] if show_money else None,
            "approved_amount": r["approved_amount"] if show_money else None,
            "decision_note": r["decision_note"], "paid": r["payout_id"] is not None,
            "created_at": r["created_at"]}


def withdraw_claim(conn, actor: Actor, claim_id: int) -> dict:
    lang = lang_of(conn, actor.person_id)
    r = conn.execute("SELECT * FROM work_claims WHERE id = ?", (claim_id,)).fetchone()
    if r is None or r["person_id"] != actor.person_id:
        raise DomainError(T(lang, "No such claim of yours.", "Такой вашей заявки нет."))
    cur = conn.execute("UPDATE work_claims SET status = 'withdrawn', decided_at = ? "
                       "WHERE id = ? AND status = 'pending'", (_iso(_now(conn)), claim_id))
    conn.commit()
    if cur.rowcount == 0:
        if r["status"] == "withdrawn":
            return {"ok": True, "status": "already",
                    "message": T(lang, "Already withdrawn.", "Уже отозвано.")}
        raise DomainError(T(lang, f"The manager has already decided: {r['status']}.",
                            f"Менеджер уже решил: {r['status']}."))
    return {"ok": True, "status": "withdrawn",
            "message": T(lang, f"Withdrawn: {r['description']}.", f"Отозвано: {r['description']}.")}


def decide_claim(conn: sqlite3.Connection, manager_id: int, claim_id: int, approve: bool,
                 amount: int | None = None, note: str = "") -> dict:
    """Manager approves (with the amount) or rejects with a reason the worker sees.
    Decided once: the second tap answers with the first decision."""
    r = conn.execute("SELECT * FROM work_claims WHERE id = ?", (claim_id,)).fetchone()
    if r is None:
        raise DomainError("No such claim.")
    if r["person_id"] == manager_id:
        raise DomainError("You cannot decide your own claim. Ask another manager or the owner.")
    if r["status"] != "pending":
        by = domain.person(conn, r["decided_by"])["name"] if r["decided_by"] else "the worker"
        return {"ok": True, "status": "already",
                "message": f"Already {r['status']} by {by}. Nothing changed."}
    when = _iso(_now(conn))
    if approve:
        amount = amount if amount is not None else r["asked_amount"]
        if amount is None:
            raise DomainError("There is no rate for this job: type the amount to approve.")
        if int(amount) <= 0:
            raise DomainError("Approve with an amount above zero, or reject with a reason.")
        cur = conn.execute("UPDATE work_claims SET status = 'approved', approved_amount = ?, "
                           "decided_by = ?, decided_at = ?, decision_note = ? "
                           "WHERE id = ? AND status = 'pending'",
                           (int(amount), manager_id, when, note or None, claim_id))
    else:
        if len((note or "").strip()) < 3:
            raise DomainError("Say why it is rejected: the worker sees the reason.")
        cur = conn.execute("UPDATE work_claims SET status = 'rejected', decided_by = ?, "
                           "decided_at = ?, decision_note = ? WHERE id = ? AND status = 'pending'",
                           (manager_id, when, note.strip(), claim_id))
    conn.commit()
    if cur.rowcount == 0:
        return decide_claim(conn, manager_id, claim_id, approve, amount, note)
    who = domain.person(conn, r["person_id"])["name"]
    return {"ok": True, "status": "approved" if approve else "rejected",
            "message": (f"Approved for {who}: {r['description']}, {domain.currency(conn)}"
                        f"{int(amount)} on {r['work_date']}." if approve else
                        f"Rejected for {who}: {r['description']}. Reason sent.")}


def claims_money(conn, person_id: int, start: str, end: str) -> int:
    """Approved other work by work date (inclusive). For earnings and payouts."""
    return conn.execute("SELECT COALESCE(SUM(approved_amount), 0) FROM work_claims WHERE "
                        "person_id = ? AND status = 'approved' AND work_date BETWEEN ? AND ?",
                        (person_id, start, end)).fetchone()[0]


def link_claims_to_payout(conn, payout_id: int, person_id: int, upto: str) -> int:
    """Freeze approved, unpaid claims up to a date into a payout. Returns the amount.
    For the payout feature: after this the rows cannot change (trigger)."""
    total = conn.execute("SELECT COALESCE(SUM(approved_amount), 0) FROM work_claims WHERE "
                         "person_id = ? AND status = 'approved' AND payout_id IS NULL AND "
                         "work_date <= ?", (person_id, upto)).fetchone()[0]
    conn.execute("UPDATE work_claims SET payout_id = ? WHERE person_id = ? AND status = "
                 "'approved' AND payout_id IS NULL AND work_date <= ?", (payout_id, person_id, upto))
    return total


# --- end of day check -----------------------------------------------------------------

def day_check(conn: sqlite3.Connection, actor: Actor, day: str | None = None) -> dict:
    """“Today recorded N pcs for $X — all right?” with the list behind it.
    Shown from eod_hour shop time; the worker confirms or adds what is missing."""
    lang = lang_of(conn, actor.person_id)
    now = _now(conn)
    day = day or now.date().isoformat()
    rows = conn.execute(
        f"SELECT e.*, m.time_quality, m.note FROM ({LIVE_DONE}) e LEFT JOIN mark_meta m "
        "ON m.event_id = e.id WHERE e.person_id = ? AND e.work_date = ? ORDER BY e.created_at",
        (actor.person_id, day)).fetchall()
    items, money, pending = [], 0, 0
    for e in rows:
        a = domain.rate(conn, e["unit_id"], e["stage_id"])
        if a is None:
            pending += 1
        else:
            money += a
        o = domain._unit_order(conn, e["unit_id"])
        p = conn.execute("SELECT name FROM products WHERE id = ?", (o["product_id"],)).fetchone()
        st = _stage(conn, e["stage_id"])
        items.append({"event_id": e["id"], "unit": domain.unit_label(conn, e["unit_id"]),
                      "product": p["name"], "stage": st["name_en"], "stage_ru": st["name_ru"],
                      "at": e["created_at"][11:16], "method": e["method"],
                      "late": (e["time_quality"] or "live") != "live",
                      "amount": a if actor.show_money else None})
    claims = [_claim_dict(conn, c, actor.show_money, lang) for c in conn.execute(
        "SELECT * FROM work_claims WHERE person_id = ? AND work_date = ? AND status IN "
        "('pending', 'approved') ORDER BY id", (actor.person_id, day))]
    claims_ok = sum(c["approved_amount"] or 0 for c in claims if c["status"] == "approved") \
        if actor.show_money else 0
    total = money + claims_ok
    confirmed = conn.execute("SELECT * FROM day_checks WHERE person_id = ? AND work_date = ?",
                             (actor.person_id, day)).fetchone()
    n = len(items)
    if actor.show_money:
        head = T(lang, f"{'Today' if day == now.date().isoformat() else day} recorded "
                       f"{_pcs(n, lang)} for {domain.currency(conn)}{total}",
                 f"{'Сегодня' if day == now.date().isoformat() else day} записано "
                 f"{_pcs(n, lang)} на {domain.currency(conn)}{total}")
    else:
        head = T(lang, f"{'Today' if day == now.date().isoformat() else day} recorded "
                       f"{_pcs(n, lang)}",
                 f"{'Сегодня' if day == now.date().isoformat() else day} записано "
                 f"{_pcs(n, lang)}")
    if pending:
        head += T(lang, f" (+{pending} price pending)", f" (+{pending} цена уточняется)")
    pend_claims = sum(c["status"] == "pending" for c in claims)
    if pend_claims:
        head += T(lang, f", {pend_claims} other job(s) waiting for approval",
                  f", другая работа ждёт подтверждения: {pend_claims}")
    changed = bool(confirmed) and confirmed["pieces"] != n
    return {"date": day, "due": day < now.date().isoformat() or now.hour >= int(
                setting(conn, "eod_hour")),
            "pieces": n, "money": total if actor.show_money else None,
            "price_pending": pending, "items": items, "claims": claims,
            "confirmed": bool(confirmed) and not changed, "changed_since_confirmed": changed,
            "currency": domain.currency(conn),
            "can_add_back_days": int(setting(conn, "mark_back_days")),
            "message": head + T(lang, " — all right?", " — всё так?")}


def confirm_day(conn, actor: Actor, day: str | None = None) -> dict:
    lang = lang_of(conn, actor.person_id)
    chk = day_check(conn, actor, day)
    conn.execute("INSERT OR REPLACE INTO day_checks (person_id, work_date, confirmed_at, pieces) "
                 "VALUES (?, ?, ?, ?)", (actor.person_id, chk["date"], _iso(_now(conn)),
                                         chk["pieces"]))
    conn.commit()
    return {"ok": True, "status": "recorded", "pieces": chk["pieces"], "money": chk["money"],
            "message": T(lang, f"Thank you. {chk['date']}: {_pcs(chk['pieces'], lang)} confirmed.",
                         f"Спасибо. {chk['date']}: {_pcs(chk['pieces'], lang)} — подтверждено.")}


# --- shared shop tablet -----------------------------------------------------------------

def setup_tablet(conn, manager_id: int, name: str, stage_id: int | None) -> str:
    if stage_id is not None and _stage(conn, stage_id) is None:
        raise DomainError("No such stage.")
    name = (name or "").strip()[:40] or "Shop tablet"
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO tablets (token, name, stage_id, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?)", (token, name, stage_id, manager_id, _iso(_now(conn))))
    conn.commit()
    return token


def _tablet(conn, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute("SELECT * FROM tablets WHERE token = ?", (token,)).fetchone()


def _leave_cut(conn, now: datetime) -> datetime:
    cut = now.replace(hour=int(setting(conn, "tablet_leave_hour")), minute=0, second=0,
                      microsecond=0)
    return cut if now >= cut else cut - timedelta(days=1)


def tablet_expire(conn, tab: sqlite3.Row) -> sqlite3.Row:
    """Sign the person out after a quiet spell or at the end of the day."""
    if not tab["acting_person"]:
        return tab
    now = _now(conn)
    seen = _parse_dt(conn, tab["last_seen"]) if tab["last_seen"] else now
    if (now - seen).total_seconds() > int(setting(conn, "tablet_idle_sec")):
        tablet_leave(conn, tab["token"], "idle")
    elif seen < _leave_cut(conn, now) <= now:
        tablet_leave(conn, tab["token"], "end_of_day")
    return _tablet(conn, tab["token"])


def tablet_state(conn, tab: sqlite3.Row) -> dict:
    tab = tablet_expire(conn, tab)
    q = "SELECT p.id, p.name, s.name_en AS stage, s.name_ru AS stage_ru FROM people p " \
        "JOIN stages s ON s.id = p.stage_id WHERE p.role = 'worker'"
    args: tuple = ()
    if tab["stage_id"] is not None:
        q += " AND p.stage_id = ?"
        args = (tab["stage_id"],)
    people = [dict(r) for r in conn.execute(q + " ORDER BY p.name", args)]
    acting = None
    if tab["acting_person"]:
        acting = {"id": tab["acting_person"],
                  "name": domain.person(conn, tab["acting_person"])["name"]}
    st = _stage(conn, tab["stage_id"]) if tab["stage_id"] else None
    return {"name": tab["name"], "stage": st["name_en"] if st else None,
            "stage_ru": st["name_ru"] if st else None, "acting": acting, "people": people,
            "idle_sec": int(setting(conn, "tablet_idle_sec")), "show_money": False}


def tablet_act(conn, tab: sqlite3.Row, person_id: int) -> str:
    """Tap your name: the tablet now marks as you (no money). Returns the session token."""
    who = domain.person(conn, person_id)
    if who["role"] != "worker" or who["stage_id"] is None:
        raise DomainError("Only shop-floor workers sign in on the tablet.")
    if tab["stage_id"] is not None and who["stage_id"] != tab["stage_id"]:
        raise DomainError("This tablet is for another stage.")
    tablet_leave(conn, tab["token"], "switch")
    now = _iso(_now(conn))
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO sessions (token, person_id, tablet, created_at) VALUES (?, ?, 1, ?)",
                 (token, person_id, now))
    conn.execute("UPDATE tablets SET acting_person = ?, acting_session = ?, last_seen = ? "
                 "WHERE token = ?", (person_id, token, now, tab["token"]))
    conn.execute("INSERT INTO tablet_shifts (tablet, person_id, started_at) VALUES (?, ?, ?)",
                 (tab["token"], person_id, now))
    conn.commit()
    return token


def tablet_leave(conn, tablet_token: str, reason: str = "leave") -> None:
    tab = _tablet(conn, tablet_token)
    if tab is None or not tab["acting_person"]:
        return
    now = _iso(_now(conn))
    conn.execute("DELETE FROM sessions WHERE token = ?", (tab["acting_session"],))
    conn.execute("UPDATE tablet_shifts SET ended_at = ?, end_reason = ? WHERE tablet = ? AND "
                 "ended_at IS NULL", (now, reason, tablet_token))
    conn.execute("UPDATE tablets SET acting_person = NULL, acting_session = NULL WHERE token = ?",
                 (tablet_token,))
    conn.commit()


def _tablet_session_check(conn, token: str, request: Request, row) -> None:
    """Runs on every signed-in request (web.SESSION_CHECKS). A tablet session ends after
    a quiet spell even if the screen keeps polling; taps (POST) count as activity."""
    if not row["tablet"]:
        return
    tab = conn.execute("SELECT * FROM tablets WHERE acting_session = ?", (token,)).fetchone()
    if tab is None:
        return
    tab = tablet_expire(conn, tab)
    if tab["acting_session"] != token:
        raise HTTPException(401, "Signed out on the shared tablet. Tap your name again.")
    if request.method == "POST":
        conn.execute("UPDATE tablets SET last_seen = ? WHERE token = ?",
                     (_iso(_now(conn)), tab["token"]))
        conn.commit()


if _tablet_session_check not in web.SESSION_CHECKS:
    web.SESSION_CHECKS.append(_tablet_session_check)


# --- office review ------------------------------------------------------------------------

def review(conn: sqlite3.Connection) -> dict:
    """What the office should look at from the floor: claims, signals, late marks,
    who has not confirmed their day."""
    now = _now(conn)
    day = now.date().isoformat()
    week_ago = (now.date() - timedelta(days=7)).isoformat()
    claims = [_claim_dict(conn, r, True) for r in conn.execute(
        "SELECT * FROM work_claims WHERE status = 'pending' ORDER BY work_date, id")]
    signals = [dict(r) for r in conn.execute(
        "SELECT s.id, s.kind, s.text, s.created_at, p.name AS person FROM shop_signals s "
        "LEFT JOIN people p ON p.id = s.person_id WHERE s.status = 'open' ORDER BY s.id DESC")]
    late = [dict(r) for r in conn.execute(
        "SELECT e.id AS event_id, e.work_date, e.created_at, m.time_quality, m.note, "
        "p.name AS person, s.name_en AS stage FROM mark_meta m JOIN work_events e "
        "ON e.id = m.event_id JOIN people p ON p.id = e.person_id JOIN stages s "
        "ON s.id = e.stage_id WHERE (m.time_quality IN ('declared', 'adjusted') OR "
        "(m.time_quality = 'phone' AND e.work_date < substr(m.received_at, 1, 10))) "
        "AND substr(m.received_at, 1, 10) >= ? ORDER BY e.id DESC LIMIT 50", (week_ago,))]
    for x in late:
        x["unit"] = domain.unit_label(conn, conn.execute(
            "SELECT unit_id FROM work_events WHERE id = ?", (x["event_id"],)).fetchone()[0])
    marked = conn.execute(f"SELECT DISTINCT person_id FROM ({LIVE_DONE}) WHERE work_date = ?",
                          (day,)).fetchall()
    confirmed = {r["person_id"] for r in conn.execute(
        "SELECT person_id FROM day_checks WHERE work_date = ?", (day,))}
    missing = [domain.person(conn, r["person_id"])["name"] for r in marked
               if r["person_id"] not in confirmed]
    return {"date": day, "claims_pending": claims, "signals": signals, "late_marks": late,
            "day_checks": {"confirmed": len(confirmed), "marked_today": len(marked),
                           "not_confirmed": sorted(missing),
                           "due": now.hour >= int(setting(conn, "eod_hour"))},
            "currency": domain.currency(conn)}


def close_signal(conn, manager_id: int, signal_id: int) -> dict:
    cur = conn.execute("UPDATE shop_signals SET status = 'done', closed_by = ?, closed_at = ? "
                       "WHERE id = ? AND status = 'open'", (manager_id, _iso(_now(conn)),
                                                            signal_id))
    conn.commit()
    if cur.rowcount == 0:
        if conn.execute("SELECT 1 FROM shop_signals WHERE id = ?", (signal_id,)).fetchone() is None:
            raise DomainError("No such signal.")
        return {"ok": True, "status": "already", "message": "Already closed."}
    return {"ok": True, "status": "done", "message": "Closed."}


# --- HTTP ---------------------------------------------------------------------------------

router = APIRouter(prefix="/api/marking")


class MarkIn(BaseModel):
    code: str | None = None
    unit_id: int | None = None
    order_id: int | None = None
    method: str = "button"
    idem_key: str | None = None
    created_at: str | None = None
    for_date: str | None = None
    confirm_next: bool = False


METHODS = {"button", "scan", "number", "voice", "photo", "sheet"}


@router.post("/mark")
def http_mark(body: MarkIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    method = body.method if body.method in METHODS else "button"
    return mark(conn, Actor.of(s, conn), code=body.code, unit_id=body.unit_id,
                order_id=body.order_id, method=method, idem_key=body.idem_key,
                created_at=body.created_at, for_date=body.for_date,
                confirm_next=body.confirm_next)


class UndoIn(BaseModel):
    event_id: int


@router.post("/undo")
def http_undo(body: UndoIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return undo(conn, Actor.of(s, conn), body.event_id)


class SyncIn(BaseModel):
    items: list[dict]


@router.post("/sync")
def http_sync(body: SyncIn, request: Request, conn=Depends(get_conn)) -> dict:
    tab = _tablet(conn, request.cookies.get(TABLET_COOKIE))
    sess = None
    if tab is None:
        sess = web.session(request, conn)
    return sync(conn, body.items, session=sess, tablet=tab)


class TextIn(BaseModel):
    transcript: str
    created_at: str | None = None


@router.post("/voice/text")
def http_voice_text(body: TextIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    """Offline path: the phone's own speech recognition sends the words."""
    return propose(conn, Actor.of(s, conn), body.transcript, created_at=body.created_at)


@router.post("/voice/audio")
async def http_voice_audio(request: Request, s: Session = Depends(worker),
                           conn=Depends(get_conn)) -> dict:
    """Raw audio body (audio/webm, audio/ogg, audio/mp4, audio/wav) -> Muse Voice Transcribe."""
    audio = await request.body()
    return voice_audio(conn, Actor.of(s, conn), audio,
                       request.headers.get("content-type", "audio/webm"),
                       request.headers.get("x-created-at"))


@router.post("/photo")
async def http_photo(request: Request, s: Session = Depends(worker),
                     conn=Depends(get_conn)) -> dict:
    """Raw image body (image/jpeg, image/png) -> Spark vision -> proposal."""
    image = await request.body()
    return photo(conn, Actor.of(s, conn), image, request.headers.get("content-type", "image/jpeg"),
                 request.headers.get("x-created-at"))


class DecideIn(BaseModel):
    yes: bool


@router.post("/proposals/{action_id}/decide")
def http_decide(action_id: int, body: DecideIn, s: Session = Depends(worker),
                conn=Depends(get_conn)) -> dict:
    return decide(conn, Actor.of(s, conn), action_id, body.yes)


@router.get("/claims")
def http_claims(s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    lang = lang_of(conn, s.person_id)
    rows = conn.execute("SELECT * FROM work_claims WHERE person_id = ? ORDER BY id DESC LIMIT 30",
                        (s.person_id,)).fetchall()
    presets = claim_presets(conn, s.person_id)
    if s.tablet:
        for p in presets:
            p["amount"] = None
    return {"presets": presets, "claims": [_claim_dict(conn, r, not s.tablet, lang) for r in rows],
            "currency": domain.currency(conn)}


class ClaimIn(BaseModel):
    description: str = ""
    preset_id: int | None = None
    qty: int | None = None
    minutes: int | None = None
    amount: int | None = None
    for_date: str | None = None
    created_at: str | None = None
    idem_key: str | None = None


@router.post("/claims")
def http_claim(body: ClaimIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return create_claim(conn, Actor.of(s, conn), **body.model_dump())


@router.post("/claims/{claim_id}/withdraw")
def http_withdraw(claim_id: int, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return withdraw_claim(conn, Actor.of(s, conn), claim_id)


class ClaimDecideIn(BaseModel):
    approve: bool
    amount: int | None = None
    note: str = ""


@router.post("/claims/{claim_id}/decide")
def http_decide_claim(claim_id: int, body: ClaimDecideIn, s: Session = Depends(office),
                      conn=Depends(get_conn)) -> dict:
    return decide_claim(conn, s.person_id, claim_id, body.approve, body.amount, body.note)


@router.get("/day-check")
def http_day_check(day: str | None = None, s: Session = Depends(worker),
                   conn=Depends(get_conn)) -> dict:
    return day_check(conn, Actor.of(s, conn), day)


class DayIn(BaseModel):
    day: str | None = None


@router.post("/day-check/confirm")
def http_confirm(body: DayIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return confirm_day(conn, Actor.of(s, conn), body.day)


@router.get("/review")
def http_review(s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return review(conn)


@router.post("/signals/{signal_id}/close")
def http_close_signal(signal_id: int, s: Session = Depends(office),
                      conn=Depends(get_conn)) -> dict:
    return close_signal(conn, s.person_id, signal_id)


class MaterialIn(BaseModel):
    text: str


@router.post("/material")
def http_material(body: MaterialIn, s: Session = Depends(worker), conn=Depends(get_conn)) -> dict:
    return material_signal(conn, Actor.of(s, conn), body.text)


class TabletSetupIn(BaseModel):
    name: str = ""
    stage_id: int | None = None


@router.post("/tablet/setup")
def http_tablet_setup(body: TabletSetupIn, request: Request, response: Response,
                      s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    """The manager turns this device into the shop tablet. The manager is signed out
    on it, so the tablet never carries office rights."""
    token = setup_tablet(conn, s.person_id, body.name, body.stage_id)
    conn.execute("DELETE FROM sessions WHERE token = ?", (request.cookies.get(web.COOKIE),))
    conn.commit()
    response.delete_cookie(web.COOKIE)
    response.set_cookie(TABLET_COOKIE, token, httponly=True, samesite="lax",
                        max_age=3600 * 24 * 365)
    return {"ok": True, "message": "This device is now the shop tablet. Workers tap their name "
                                   "to mark; no money is shown here."}


def _require_tablet(request: Request, conn) -> sqlite3.Row:
    tab = _tablet(conn, request.cookies.get(TABLET_COOKIE))
    if tab is None:
        raise HTTPException(401, "This device is not set up as a shop tablet.")
    return tab


@router.get("/tablet")
def http_tablet(request: Request, conn=Depends(get_conn)) -> dict:
    return tablet_state(conn, _require_tablet(request, conn))


class ActIn(BaseModel):
    person_id: int


@router.post("/tablet/act")
def http_tablet_act(body: ActIn, request: Request, response: Response,
                    conn=Depends(get_conn)) -> dict:
    tab = _require_tablet(request, conn)
    token = tablet_act(conn, tab, body.person_id)
    response.set_cookie(web.COOKIE, token, httponly=True, samesite="lax")
    name = domain.person(conn, body.person_id)["name"]
    return {"ok": True, "acting": {"id": body.person_id, "name": name},
            "message": f"Marking: {name}. Tap “Sign out” when you finish."}


@router.post("/tablet/leave")
def http_tablet_leave(request: Request, response: Response, conn=Depends(get_conn)) -> dict:
    tab = _require_tablet(request, conn)
    tablet_leave(conn, tab["token"], "leave")
    response.delete_cookie(web.COOKIE)
    return {"ok": True, "message": "Signed out. Who is working next?"}


@router.post("/tablet/ping")
def http_tablet_ping(request: Request, conn=Depends(get_conn)) -> dict:
    """The screen calls this on every touch: a person still at the tablet is not idle."""
    tab = tablet_expire(conn, _require_tablet(request, conn))
    if tab["acting_person"]:
        conn.execute("UPDATE tablets SET last_seen = ? WHERE token = ?",
                     (_iso(_now(conn)), tab["token"]))
        conn.commit()
    return {"ok": True, "acting": tab["acting_person"]}


# --- demo data ----------------------------------------------------------------------------

PRESETS = [  # stage code (None = any), en, ru, amount per piece (None = by minutes)
    ("sew", "Sewed bags", "Сшил мешки", 2),
    ("sew", "Seat cushion", "Подушка сиденья", 3),
    ("uph", "Dust bag for armchair", "Мешок на кресло", 2),
    ("uph", "Re-upholstery repair", "Перетяжка (ремонт)", None),
    ("asm", "Frame repair", "Ремонт каркаса", None),
    ("pack", "Carton made by hand", "Короб вручную", 1),
    (None, "Fitting / adjustment", "Подгонка", None),
    (None, "Repair of returned item", "Ремонт возврата", None),
]


def seed(conn: sqlite3.Connection, rnd) -> None:
    """Presets, an hourly rate, QR tokens for open pieces and a few days of claims."""
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('hourly_rate', '14')")
    codes = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM stages")}
    if not conn.execute("SELECT 1 FROM claim_presets").fetchone():
        conn.executemany("INSERT INTO claim_presets (stage_id, name_en, name_ru, amount) "
                         "VALUES (?, ?, ?, ?)",
                         [(codes.get(c), en, ru, a) for c, en, ru, a in PRESETS])
    if not conn.execute("SELECT 1 FROM unit_tokens").fetchone():
        conn.executemany("INSERT INTO unit_tokens (token, unit_id) VALUES (?, ?)",
                         [(secrets.token_urlsafe(9), r["id"]) for r in conn.execute(
                             "SELECT id FROM units")])
    today = domain.now(conn).date()
    workers = conn.execute("SELECT id, stage_id FROM people WHERE role = 'worker'").fetchall()
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    presets = conn.execute("SELECT * FROM claim_presets").fetchall()
    for back in range(1, 6):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        for w in rnd.sample(list(workers), 3):
            ps = [p for p in presets if p["stage_id"] in (None, w["stage_id"])]
            p = rnd.choice(ps)
            qty = rnd.randint(2, 8) if p["amount"] else None
            mins = None if p["amount"] else rnd.choice([20, 30, 45, 60, 90])
            asked = p["amount"] * qty if p["amount"] else round(mins * 14 / 60)
            approved = back > 1 or rnd.random() < 0.5
            conn.execute(
                "INSERT INTO work_claims (person_id, work_date, description, preset_id, qty, "
                "minutes, asked_amount, status, approved_amount, decided_by, decided_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (w["id"], d.isoformat(), p["name_en"], p["id"], qty, mins, asked,
                 "approved" if approved else "pending", asked if approved else None,
                 manager if approved else None, f"{d.isoformat()}T17:30:00" if approved else None,
                 f"{d.isoformat()}T15:10:00"))
    # one free-text claim that is really work on an order: the hint the demo shows
    sew = next((w for w in workers if w["stage_id"] == codes.get("uph")), workers[0])
    num = conn.execute("SELECT number FROM orders ORDER BY number DESC LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO work_claims (person_id, work_date, description, minutes, "
                 "asked_amount, created_at) VALUES (?, ?, ?, 50, 12, ?)",
                 (sew["id"], today.isoformat(), f"Upholstered armchair for order {num}, no rate",
                  f"{today.isoformat()}T11:40:00"))
    conn.execute("INSERT INTO shop_signals (kind, text, stage_id, person_id, created_at) "
                 "VALUES ('material_out', 'velvet beige', ?, ?, ?)",
                 (codes.get("uph"), sew["id"], f"{today.isoformat()}T10:05:00"))
