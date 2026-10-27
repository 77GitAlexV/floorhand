"""SQLite storage. One connection per request; schema lives here.

Invariants enforced in the database itself (not only in code):
- work_events is append-only: UPDATE and DELETE raise.
- an event is paid at most once (payout_lines.event_id is unique).
- a unit leaves the shop at most once (shipment_lines.unit_id is unique).
"""
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "floorhand.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    id        INTEGER PRIMARY KEY,
    code      TEXT NOT NULL UNIQUE,
    name_en   TEXT NOT NULL,
    name_ru   TEXT NOT NULL,
    seq       INTEGER NOT NULL UNIQUE,
    capacity  INTEGER NOT NULL          -- units per working day
);

CREATE TABLE IF NOT EXISTS people (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    role      TEXT NOT NULL CHECK (role IN ('worker', 'manager', 'owner')),
    stage_id  INTEGER REFERENCES stages(id),
    lang      TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS products (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL,            -- armchair, pouf, sofa ...
    price     INTEGER NOT NULL          -- sale price, whole currency units
);

-- "no rate" is a missing row, never 0.
CREATE TABLE IF NOT EXISTS rates (
    product_id INTEGER NOT NULL REFERENCES products(id),
    stage_id   INTEGER NOT NULL REFERENCES stages(id),
    amount     INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (product_id, stage_id)
);

CREATE TABLE IF NOT EXISTS customers (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    city  TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY,
    number      INTEGER NOT NULL UNIQUE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    variant     TEXT NOT NULL DEFAULT '',
    qty         INTEGER NOT NULL CHECK (qty > 0),
    customer_id INTEGER REFERENCES customers(id),   -- NULL = to stock
    due_date    TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL,
    cancelled   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS units (
    id        INTEGER PRIMARY KEY,
    order_id  INTEGER NOT NULL REFERENCES orders(id),
    seq       INTEGER NOT NULL,
    UNIQUE (order_id, seq)
);

CREATE TABLE IF NOT EXISTS assignments (
    unit_id    INTEGER NOT NULL REFERENCES units(id),
    stage_id   INTEGER NOT NULL REFERENCES stages(id),
    person_id  INTEGER NOT NULL REFERENCES people(id),
    PRIMARY KEY (unit_id, stage_id)
);

CREATE TABLE IF NOT EXISTS work_events (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('done', 'undo')),
    unit_id     INTEGER NOT NULL REFERENCES units(id),
    stage_id    INTEGER NOT NULL REFERENCES stages(id),
    person_id   INTEGER NOT NULL REFERENCES people(id),
    work_date   TEXT NOT NULL,           -- money follows this date, shop time zone
    created_at  TEXT NOT NULL,
    method      TEXT NOT NULL DEFAULT 'button',   -- button, scan, number, voice, photo
    undoes_id   INTEGER REFERENCES work_events(id),
    idem_key    TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_events_unit ON work_events(unit_id, stage_id);
CREATE INDEX IF NOT EXISTS ix_events_person ON work_events(person_id, work_date);

CREATE TRIGGER IF NOT EXISTS work_events_no_update
BEFORE UPDATE ON work_events
BEGIN SELECT RAISE(ABORT, 'work_events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS work_events_no_delete
BEFORE DELETE ON work_events
BEGIN SELECT RAISE(ABORT, 'work_events is append-only'); END;

CREATE TABLE IF NOT EXISTS shipments (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    payment      TEXT NOT NULL CHECK (payment IN ('paid', 'credit', 'partial')),
    paid_amount  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    created_by   INTEGER NOT NULL REFERENCES people(id)
);

CREATE TABLE IF NOT EXISTS shipment_lines (
    shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    unit_id     INTEGER NOT NULL UNIQUE REFERENCES units(id),
    price       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payouts (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    created_at  TEXT NOT NULL,
    created_by  INTEGER NOT NULL REFERENCES people(id)
);

CREATE TABLE IF NOT EXISTS payout_lines (
    payout_id  INTEGER NOT NULL REFERENCES payouts(id),
    event_id   INTEGER NOT NULL UNIQUE REFERENCES work_events(id),
    amount     INTEGER NOT NULL
);

-- What a model proposed and what a human decided. The model never writes
-- domain tables; applying a proposal re-reads this row by id.
CREATE TABLE IF NOT EXISTS agent_actions (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,          -- JSON
    model       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'applied', 'rejected')),
    created_at  TEXT NOT NULL,
    for_person  INTEGER REFERENCES people(id),
    decided_by  INTEGER REFERENCES people(id),
    decided_at  TEXT,
    result      TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    person_id  INTEGER NOT NULL REFERENCES people(id),
    tablet     INTEGER NOT NULL DEFAULT 0,   -- shared tablet: no money on screen
    created_at TEXT NOT NULL
);
"""


def db_path() -> Path:
    return Path(os.environ.get("FLOORHAND_DB", DEFAULT_PATH))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
