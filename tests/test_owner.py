"""The owner's feed: materials follow the events, the planner respects capacity,
a model only reads, "Yes" applies the stored proposal and nothing else."""
import json
import random
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, models, seed
from floorhand.domain import DomainError
from floorhand.features import ask, materials, owner, planner

TODAY = date(2026, 10, 27)   # a Tuesday


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    monkeypatch.setattr(domain, "today", lambda conn: TODAY.isoformat())
    c = db.connect(tmp_path / "o.db")
    seed.build(c, random.Random(7), today=TODAY)   # 7: the first stage has room tomorrow
    return c


def owner_id(conn):
    return conn.execute("SELECT id FROM people WHERE role = 'owner'").fetchone()[0]


# --- materials ----------------------------------------------------------------

def test_stock_follows_events_and_undo_returns_it(conn):
    sew = conn.execute("SELECT id FROM stages WHERE code = 'sew'").fetchone()[0]
    uid = domain._waiting_units(conn)[sew][0]
    order = domain._unit_order(conn, uid)
    fabric = materials.fabric_for_variant(conn, order["variant"])
    per_unit = conn.execute("SELECT qty FROM bom WHERE product_id = ? AND by_variant = 1",
                            (order["product_id"],)).fetchone()[0]
    before = materials.on_hand(conn)[fabric]
    who = conn.execute("SELECT id FROM people WHERE stage_id = ? LIMIT 1", (sew,)).fetchone()[0]
    res = domain.mark_done(conn, who, uid)
    assert materials.on_hand(conn)[fabric] == pytest.approx(before - per_unit)
    domain.undo(conn, who, res.event_id)
    assert materials.on_hand(conn)[fabric] == pytest.approx(before)
    assert conn.execute("SELECT COUNT(*) FROM stock_moves WHERE kind NOT IN ('receipt', 'adjust')").fetchone()[0] == 0


def test_shortage_math(conn):
    short = materials.shortages(conn)
    names = {s["name"] for s in short}
    assert {"velvet beige", "boucle ash"} <= names and len(short) == 2
    need, have = materials.requirement(conn), materials.on_hand(conn)
    for s in short:
        assert s["short"] == pytest.approx(need[s["material_id"]]["total"] - have[s["material_id"]], abs=0.01)
        assert s["short"] > 0
        assert s["stops_next"] == (have[s["material_id"]] < need[s["material_id"]]["next"])
    # a receipt covering the gap closes the shortage
    beige = next(s for s in short if s["name"] == "velvet beige")
    materials.receive(conn, owner_id(conn), beige["material_id"], beige["short"] + 1, "test delivery")
    assert "velvet beige" not in {s["name"] for s in materials.shortages(conn)}
    with pytest.raises(DomainError):
        materials.receive(conn, owner_id(conn), beige["material_id"], -3, kind="receipt")


# --- planner ------------------------------------------------------------------

def test_planner_respects_capacity_and_priority(conn):
    plan = planner.plan_tomorrow(conn, TODAY)
    assert plan["target"] == "2026-10-28"
    assert planner.next_workday(conn, date(2026, 10, 30)) == date(2026, 11, 2)   # Fri -> Mon
    for s in plan["stages"]:
        assert s["on_hands"] + len(s["lines"]) <= max(s["capacity"], s["on_hands"])
        assert len(s["lines"]) <= s["capacity"]
        # late-risk units in the hand-out come before the rest
        risks = [ln["risk"] != planner.RISK_OK for ln in s["lines"]]
        assert risks == sorted(risks, reverse=True)
        for ln in s["lines"]:
            assert ln["person_id"] in ln["options"]
    assert any(s["bottleneck"] for s in plan["stages"])
    assert sum(1 for s in plan["stages"] if s["bottleneck"]) == 1


def test_planner_never_splits_a_customer_order(conn):
    plan = planner.plan_tomorrow(conn, TODAY)
    checked = 0
    for s in plan["stages"]:
        by_order = {}
        for ln in s["lines"]:
            if ln["customer"]:
                by_order.setdefault(ln["order_id"], set()).add(ln["person_id"])
        for oid, people in by_order.items():
            assert len(people) == 1, f"order {oid} split at {s['code']}"
            checked += 1
    assert checked > 0


# --- the feed and the rules ---------------------------------------------------

def test_feed_is_stable_and_shows_the_expected_cards(conn):
    feed = owner.today_feed(conn, owner_id(conn))
    kinds = [c["kind"] for c in feed["cards"]]
    assert kinds[0] == "plan" and kinds.count("shortage") == 2 and "late_risk" in kinds
    assert all(c["why"] and all(ln["tag"] in ("FACT", "CALC", "ESTIMATE") for ln in c["why"])
               for c in feed["cards"])
    assert all(c["headline"]["en"] and c["headline"]["ru"] for c in feed["cards"])
    again = owner.today_feed(conn, owner_id(conn))
    assert [c["id"] for c in again["cards"]] == [c["id"] for c in feed["cards"]]   # no duplicates
    # a shortage that disappears closes its card by itself
    beige = next(c for c in feed["cards"] if c["kind"] == "shortage" and c["material"] == "velvet beige")
    materials.receive(conn, owner_id(conn), beige["material_id"], 500, "big delivery")
    after = owner.today_feed(conn, owner_id(conn))
    assert beige["id"] not in [c["id"] for c in after["cards"]]
    row = conn.execute("SELECT status, result FROM agent_actions WHERE id = ?", (beige["id"],)).fetchone()
    assert row["status"] == "rejected" and json.loads(row["result"])["superseded"]


def test_apply_only_from_stored_proposal(conn):
    feed = owner.today_feed(conn, owner_id(conn))
    plan = next(c for c in feed["cards"] if c["kind"] == "plan")
    before = conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
    with pytest.raises(DomainError, match="No such card"):
        owner.apply(conn, owner_id(conn), 999999, owner.DecideIn())
    stranger = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    first = plan["lines"][0]
    with pytest.raises(DomainError, match="offered"):
        owner.apply(conn, owner_id(conn), plan["id"],
                    owner.DecideIn(moves={str(first["unit_id"]): stranger}))
    assert conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == before
    # Change: keep two lines, move one of them to another offered person
    keep = [ln["unit_id"] for ln in plan["lines"][:2]]
    other = next(p for p in first["options"] if p != first["person_id"])
    res = owner.apply(conn, owner_id(conn), plan["id"],
                      owner.DecideIn(keep=keep, moves={str(first["unit_id"]): other}))
    assert res["applied"] == 2 and res["changed"]
    assert conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == before + 2
    assert conn.execute("SELECT person_id FROM assignments WHERE unit_id = ? AND stage_id = ?",
                        (first["unit_id"], first["stage_id"])).fetchone()[0] == other
    with pytest.raises(DomainError, match="already decided"):
        owner.apply(conn, owner_id(conn), plan["id"], owner.DecideIn())
    # the decision is recorded for learning
    st = owner.shadow_stats(conn, "plan")
    assert st["accepted"] >= 1


def test_no_records_a_reason_and_shadow_kind_refuses_yes(conn):
    feed = owner.today_feed(conn, owner_id(conn))
    late = next(c for c in feed["cards"] if c["kind"] == "late_risk")
    r = owner.reject(conn, owner_id(conn), late["id"], "customers were called already")
    assert "called" in r["message"]
    assert owner.shadow_stats(conn, "late_risk")["rejected"] == 1
    idle = next((c for c in feed["cards"] if c["kind"] == "idle_capacity"), None)
    assert idle is not None and idle["shadow"]
    with pytest.raises(DomainError, match="still learning"):
        owner.apply(conn, owner_id(conn), idle["id"], owner.DecideIn())
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE source = 'agent'").fetchone()[0] == 0
    st = owner.shadow_stats(conn, "idle_capacity")
    assert st["scored"] >= 3 and st["rate"] is not None and not st["enabled"]


def test_shadow_switches_on_only_after_the_hit_rate_is_proven(conn):
    st = owner.shadow_stats(conn, "idle_capacity")
    assert st["scored"] < owner.SHADOW_MIN_SCORED
    with pytest.raises(DomainError, match="learning"):
        owner.enable_kind(conn, "idle_capacity")
    # two more scored hits bring it over the threshold, then the owner's word switches it on
    for i in range(2):
        aid = conn.execute("INSERT INTO agent_actions (kind, payload, model, status, created_at, result) "
                           "VALUES ('idle_capacity', '{}', 'planner-rules', 'rejected', '2026-10-01T06:30:00', "
                           "'{\"expired\": true}')").lastrowid
        conn.execute("INSERT INTO advice_scores VALUES (?, 'idle_capacity', '2026-10-01', 1, 1, 'x')", (aid,))
    conn.commit()
    assert owner.shadow_stats(conn, "idle_capacity")["ready"]
    owner.enable_kind(conn, "idle_capacity")
    feed = owner.today_feed(conn, owner_id(conn))
    idle = next(c for c in feed["cards"] if c["kind"] == "idle_capacity")
    before = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    res = owner.apply(conn, owner_id(conn), idle["id"], owner.DecideIn(keep=[idle["lines"][0]["product_id"]]))
    assert res["applied"] == 1
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before + 1
    o = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 1").fetchone()
    assert o["customer_id"] is None and o["source"] == "agent" and o["qty"] == idle["lines"][0]["qty"]


def test_shortage_apply_goes_to_outbox_not_outside(conn):
    feed = owner.today_feed(conn, owner_id(conn))
    card = next(c for c in feed["cards"] if c["kind"] == "shortage")
    with pytest.raises(DomainError, match="offered"):
        owner.apply(conn, owner_id(conn), card["id"], owner.DecideIn(pick=123456))
    res = owner.apply(conn, owner_id(conn), card["id"], owner.DecideIn(pick=card["options"][1]["id"]))
    assert "outbox" in res["message"] and "dry run" in res["message"]
    row = conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 1").fetchone()
    assert row["kind"] == "purchase" and row["status"] == "draft" and card["material"] in row["subject"]


def test_plan_is_scored_against_what_the_floor_did(conn):
    st = owner.shadow_stats(conn, "plan")
    assert st["scored"] >= 5 and 0.6 <= st["rate"] <= 0.95   # seeded history: ~80% matched plus noise
    row = conn.execute("SELECT a.id, a.payload FROM agent_actions a JOIN advice_scores s ON s.action_id = a.id "
                       "WHERE a.kind = 'plan' LIMIT 1").fetchone()
    p = json.loads(row["payload"])
    hits, total = owner._score(conn, "plan", p)
    assert total == len(p["lines"]) and 0 < hits <= total


# --- the model only reads ------------------------------------------------------

def test_tools_run_read_only_and_cut_money_by_role(conn):
    for name in ask.TOOLS:
        ask.run_tool(conn, "owner", name, {"number": 150, "name": "velvet beige"})
    with pytest.raises(DomainError, match="owner"):
        ask.run_tool(conn, "manager", "income", {})
    with pytest.raises(DomainError, match="owner"):
        ask.run_tool(conn, "manager", "payroll_due", {})
    ask.run_tool(conn, "manager", "debtors", {"min_days": 30})
    with pytest.raises(sqlite3.OperationalError):
        with ask.read_only(conn):
            conn.execute("INSERT INTO customers (name) VALUES ('x')")
    assert conn.execute("SELECT COUNT(*) FROM customers WHERE name = 'x'").fetchone()[0] == 0


def test_model_path_never_writes(conn, monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "x")
    snapshot = lambda: [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
                        for t in ("orders", "assignments", "stock_moves", "outbox", "work_events")]
    before = snapshot()
    calls = []

    def fake_post(path, body, timeout=120):
        calls.append(body)
        if len(calls) == 1:   # the model "calls" a tool - with a write-looking argument
            return {"output": [{"type": "function_call", "name": "income", "call_id": "c1",
                                "arguments": json.dumps({"month": "2026-10; DELETE FROM orders"})}],
                    "usage": {"input_tokens": 10, "output_tokens": 5}}
        return {"output_text": "Invoiced this month as the tool says.",
                "usage": {"input_tokens": 10, "output_tokens": 5}}
    monkeypatch.setattr(models, "_post", fake_post)
    r = ask.ask(conn, "owner", "how much did we earn?", "en")
    assert r["model"] == models.SPARK and r["tools"] == ["income"] and r["lines"]
    assert snapshot() == before
    assert conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 2


def test_offline_ask_answers_the_common_questions(conn):
    cases = {"how much did we earn in October?": "income",
             "who owes us for more than 30 days?": "debtors",
             "what is late and why?": "late_orders",
             "how much velvet beige do we need this week?": "material_need",
             "where is order #150?": "order_status",
             "сколько заработали в октябре?": "income",
             "кто нам должен больше 30 дней?": "debtors"}
    for q, tool in cases.items():
        r = ask.ask(conn, "owner", q, "en")
        assert r["model"] == "offline-rules" and r["tools"] == [tool], q
        assert r["lines"] and all(ln["tag"] in ("FACT", "CALC", "ESTIMATE") for ln in r["lines"])
    debt = ask.ask(conn, "owner", "who owes us for more than 30 days?", "en")
    assert "0 customers" not in debt["lines"][0]["en"]
    r = ask.ask(conn, "owner", "tell me a joke", "en")
    assert r["model"] == "offline-rules" and r["examples"]


def test_morning_summary_per_role_is_numbers(conn):
    o = owner.morning(conn, owner_id(conn), "en")
    assert o["role"] == "owner" and "Bottleneck" in o["text"] and "Money" in o["text"]
    m = owner.morning(conn, conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0], "ru")
    assert "К отгрузке" in m["text"] and "Должны нам" in m["text"]
    w_id = conn.execute("SELECT id FROM people WHERE role = 'worker' LIMIT 1").fetchone()[0]
    w = owner.morning(conn, w_id, "en")
    assert "$" in w["text"]
    assert "$" not in owner.morning(conn, w_id, "en", tablet=True)["text"]   # shared tablet: no money


def test_api_roles(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(domain, "today", lambda conn: TODAY.isoformat())
    seed.build(db.connect(path), random.Random(7), today=TODAY)
    monkeypatch.setenv("FLOORHAND_DB", str(path))
    from floorhand.main import app
    client = TestClient(app)
    assert client.get("/api/owner/today").status_code == 401
    assert client.get("/api/materials").status_code == 401
    c = db.connect(path)
    manager = c.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    client.post("/api/login", json={"person_id": manager})
    assert client.get("/api/owner/today").status_code == 403
    assert client.get("/api/materials").status_code == 200
    assert client.post("/api/owner/ask", json={"text": "how much did we earn?"}).status_code == 409
    assert client.get("/api/owner/morning").json()["role"] == "manager"
    client.post("/api/login", json={"person_id": owner_id(c)})
    feed = client.get("/api/owner/today").json()
    assert feed["cards"] and feed["cards"][0]["kind"] == "plan"
    why = client.get(f"/api/owner/cards/{feed['cards'][0]['id']}/why?lang=ru").json()
    assert why["lines"] and why["model"] == "offline-rules"
    r = client.post(f"/api/owner/cards/{feed['cards'][0]['id']}/apply", json={}).json()
    assert r["ok"] and r["applied"] > 0
