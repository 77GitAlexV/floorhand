"""Maintenance preserves WAL data, the budget ledger and the previous demo on failure."""
import sqlite3
from contextlib import closing

import pytest

from deploy import runtime
from floorhand import db, models, seed


@pytest.fixture
def path(tmp_path, monkeypatch):
    path = tmp_path / "floorhand.db"
    monkeypatch.setenv("FLOORHAND_DEMO", "1")
    runtime.initialize(path)
    return path


def test_initialize_seeds_once_and_keeps_existing_data(path):
    with closing(db.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == seed.N_ORDERS
        conn.execute("INSERT INTO sessions VALUES ('demo-test', 1, 0, '2026-10-27')")
        conn.commit()
    runtime.initialize(path)
    with closing(db.connect(path)) as conn:
        assert conn.execute("SELECT token FROM sessions").fetchone()[0] == "demo-test"


def test_backup_includes_committed_wal_and_rotates(path):
    with closing(db.connect(path)) as conn:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT INTO sessions VALUES ('in-wal', 1, 0, '2026-10-27')")
        conn.commit()
        snapshots = [runtime.backup(path, keep=2) for _ in range(3)]
        assert not snapshots[0].exists()
        assert snapshots[1].exists() and snapshots[2].exists()
        with closing(sqlite3.connect(snapshots[-1])) as saved:
            assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert saved.execute("SELECT token FROM sessions").fetchone()[0] == "in-wal"
        assert conn.execute("SELECT token FROM sessions").fetchone()[0] == "in-wal"


def test_backup_failure_does_not_rotate_good_snapshots(path, monkeypatch):
    saved = runtime.backup(path)
    with pytest.raises(ValueError):
        runtime.backup(path, keep=0)
    with pytest.raises(sqlite3.Error):
        runtime.backup(path.parent / "missing.db")
    assert saved.exists()
    assert not list(saved.parent.glob("*.tmp"))


def test_reset_preserves_model_spend_and_backs_up_visitor_changes(path):
    with closing(db.connect(path)) as conn:
        conn.execute("INSERT INTO sessions VALUES ('visitor', 1, 0, '2026-10-27')")
        conn.execute("INSERT INTO model_calls (at, model, purpose, cost_micro, ok) "
                     "VALUES ('2026-10-27', 'demo', 'test', 1234567, 1)")
        conn.commit()
    runtime.reset(path)
    with closing(db.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == seed.N_ORDERS
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert models.spent_micro(conn) == 1234567
    saved = next((path.parent / "backups").glob("*.db"))
    with closing(sqlite3.connect(saved)) as conn:
        assert conn.execute("SELECT token FROM sessions").fetchone()[0] == "visitor"


def test_reset_failure_keeps_old_database(path, monkeypatch):
    before = path.read_bytes()
    def fail(conn):
        raise RuntimeError("seed failed")
    monkeypatch.setattr(seed, "build", fail)
    with pytest.raises(RuntimeError, match="seed failed"):
        runtime.reset(path)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".seed-*"))


def test_reset_refuses_missing_ledger(path):
    with closing(db.connect(path)) as conn:
        conn.execute("DROP TABLE model_calls")
        conn.commit()
    with pytest.raises(sqlite3.Error):
        runtime.reset(path)
    with closing(db.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == seed.N_ORDERS


def test_maintenance_demo_guard(path, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOORHAND_DEMO", "0")
    with pytest.raises(RuntimeError, match="FLOORHAND_DEMO=1"):
        runtime.reset(path)
    with pytest.raises(RuntimeError, match="FLOORHAND_DEMO=1"):
        runtime.initialize(tmp_path / "new.db")
    assert not (path.parent / "backups").exists()
