"""The default fictional demo supports the reviewer's owner and worker flows."""
import random
from datetime import date

from fastapi.testclient import TestClient

from floorhand import db, domain, seed
from floorhand.features import planner, ripple


def test_default_seed_owner_scenarios_and_worker_boundary(tmp_path, monkeypatch):
    day = "2026-10-28"
    monkeypatch.setattr(domain, "today", lambda conn: day)
    monkeypatch.setenv("FLOORHAND_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("FLOORHAND_META_LIVE", "0")
    monkeypatch.setenv("FLOORHAND_SIMULATE", "0")
    conn = db.connect()
    seed.build(conn, random.Random(26), today=date.fromisoformat(day))
    saved = ripple.cards(conn)[0]
    cut = conn.execute("SELECT id FROM stages WHERE code='cut'").fetchone()[0]
    queued = {u.order_id for u in planner.open_units(conn, date.fromisoformat(day)) if u.stage_id == cut}
    assert queued and saved["shock"]["stage_id"] == cut
    assert {m["order_id"] for m in saved["stage_movements"]} >= queued
    wait = saved["options"][0]
    assert wait["total_late_orders"] == wait["baseline_late_orders"] + wait["late_orders"]
    assert saved["fingerprint"] == ripple.snapshot(conn)
    conn.close()

    from floorhand.main import app
    client = TestClient(app)
    assert client.post("/api/login", json={"person_id": 30}).status_code == 200
    assert client.get("/api/ripple/cards").json()["cards"]
    products = client.get("/api/rush/catalog").json()["products"]
    assert products
    response = client.post("/api/rush/quote", json={"request_key": "demo-quote", "product_id": products[0]["id"],
        "qty": 2, "due_date": "2026-10-29", "variant": "velvet beige · walnut"})
    assert response.status_code == 200
    quote = response.json()["card"]["quote"]
    assert isinstance(quote["fits"], bool)
    for field in ("margin_before", "margin_after", "extra_cost", "surcharge"):
        assert field in quote
    assert client.get("/api/gate").status_code == 200
    assert client.get("/api/slang/aliases").status_code == 200
    assert client.post("/api/login", json={"person_id": 1}).status_code == 200
    assert client.get("/api/slang/catalog").status_code == 200
    assert client.get("/api/rush/catalog").status_code == 403
