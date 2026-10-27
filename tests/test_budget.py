"""The money guard: no key or not live -> no call; budget is a hard stop."""
import pytest

from floorhand import db, models
from floorhand.models import BudgetStop


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "b.db")
    db.init(c)
    return c


def test_off_by_default(conn, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "x")
    called = []
    monkeypatch.setattr(models, "_post", lambda *a, **k: called.append(1))
    with pytest.raises(BudgetStop):
        models.respond(conn, "test", "hello")
    assert not called


def test_budget_is_a_hard_stop(conn, monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setenv("FLOORHAND_BUDGET_USD", "0.01")
    usage = {"usage": {"input_tokens": 1000, "output_tokens": 1000}}
    monkeypatch.setattr(models, "_post", lambda *a, **k: usage)
    calls = 0
    with pytest.raises(BudgetStop):
        for _ in range(1000):
            models.respond(conn, "test", "hello", model="muse-spark-1.3",
                           max_output_tokens=1000)
            calls += 1
    assert 0 < calls < 1000
    assert models.spent_micro(conn) <= models.budget_micro()


def test_missing_usage_is_charged_the_worst_case(conn, monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setattr(models, "_post", lambda *a, **k: {})
    models.respond(conn, "test", "hello", model="muse-spark-1.3", max_output_tokens=1000)
    row = conn.execute("SELECT estimated, cost_micro FROM model_calls").fetchone()
    assert row["estimated"] == 1
    assert row["cost_micro"] >= models.token_cost_micro("muse-spark-1.3", 0, 1000)


def test_prices():
    # 1M in + 1M out on Spark standard = 1.25 + 4.25
    assert models.token_cost_micro("muse-spark-1.3", 1_000_000, 1_000_000) == 5_500_000
    assert models.unit_cost_micro("sam-3.1", 1000) == 2_500_000
