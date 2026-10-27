"""Public readiness reveals no shop records and never calls a model."""
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from floorhand import db, models


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "health.db"
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "5")
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with closing(db.connect(path)) as conn:
        db.init(conn)
        conn.execute("INSERT INTO people (name, role) VALUES ('Demo worker / Деморабочий', 'worker')")
        conn.commit()
    from floorhand.main import app
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_public_and_offline(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A health check must never call the model API")
    monkeypatch.setattr(models, "_post", forbidden)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": True, "meta_live": False,
                               "budget_left_usd": 5.0}
    assert isinstance(response.json()["budget_left_usd"], float)
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("flag,key,live", [("0", "fake-demo-key", False),
                                         ("1", "", False), ("1", "fake-demo-key", True)])
def test_health_reports_effective_model_configuration(client, monkeypatch, flag, key, live):
    monkeypatch.setenv("FLOORHAND_META_LIVE", flag)
    monkeypatch.setenv("MODEL_API_KEY", key)
    assert client.get("/healthz").json()["meta_live"] is live


@pytest.mark.parametrize("spent,left", [(1_250_000, 3.75), (6_000_000, 0.0)])
def test_health_uses_ledger_and_clamps_exhausted_budget(client, spent, left):
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO model_calls (at, model, purpose, cost_micro, ok) "
                     "VALUES ('2026-10-27', 'demo', 'test', ?, 1)", (spent,))
        conn.commit()
    assert client.get("/healthz").json()["budget_left_usd"] == left


@pytest.mark.parametrize("broken", ["missing", "corrupt", "schema", "budget"])
def test_health_failure_is_503_without_creating_database(client, monkeypatch, tmp_path, broken):
    path = tmp_path / "broken.db"
    if broken == "budget":
        monkeypatch.setenv("FLOORHAND_BUDGET_USD", "invalid")
    else:
        monkeypatch.setenv("FLOORHAND_DB", str(path))
        if broken == "corrupt":
            path.write_bytes(b"not a sqlite database")
        elif broken == "schema":
            with closing(sqlite3.connect(path)):
                pass
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["db"] is False
    assert "broken.db" not in response.text
    if broken == "missing":
        assert not path.exists()


@pytest.mark.parametrize("flag,demo", [(None, False), ("0", False), ("1", True)])
def test_me_exposes_demo_setting_only_after_login(client, monkeypatch, flag, demo):
    if flag is None:
        monkeypatch.delenv("FLOORHAND_DEMO", raising=False)
    else:
        monkeypatch.setenv("FLOORHAND_DEMO", flag)
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"person_id": 1}).status_code == 200
    assert client.get("/api/me").json()["demo"] is demo
