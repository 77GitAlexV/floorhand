"""Small invented queues; dates and costs below are checked by hand."""
import json
import random
from datetime import date

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, models, seed
from floorhand.domain import DomainError
from floorhand.features import owner, planner, ripple, rush
from floorhand.web import Session

TODAY = "2026-10-27"  # Tuesday


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    monkeypatch.setattr(domain, "today", lambda c: TODAY)
    c = db.connect(tmp_path / "ripple.db")
    db.init(c)
    c.executescript("""
        INSERT INTO settings VALUES('tz','Asia/Bangkok'),('currency','$');
        INSERT INTO settings VALUES('fabrics','["Demo weave"]'),('frames','["Demo finish"]');
        INSERT INTO stages VALUES(1,'cut','Cutting','Раскрой',1,2),(2,'uph','Upholstery','Обивка',2,2);
        INSERT INTO people(id,name,role,stage_id) VALUES
            (1,'Demo Cutter','worker',1),(2,'Demo Upholsterer','worker',2),
            (3,'Demo Owner','owner',NULL),(4,'Demo Manager','manager',NULL);
        INSERT INTO products VALUES(1,'Demo Harbor sofa','sofa',100);
        INSERT INTO rates VALUES(1,1,10),(1,2,20);
        INSERT INTO customers VALUES(1,'Demo Cloud Workshop','Imaginary Town');
        INSERT INTO materials(id,name,kind,unit) VALUES(1,'Demo foam','foam','kg');
        INSERT INTO bom(product_id,material_id,qty,stage_id) VALUES(1,1,1,2);
        INSERT INTO stock_moves(material_id,kind,qty,at) VALUES(1,'receipt',100,'2026-10-27');
        INSERT INTO ripple_material_costs VALUES(1,10);
    """)
    c.commit()
    yield c
    c.close()


def order(conn, qty=2, customer=1, due="2026-10-30"):
    return domain.create_order(conn, product_id=1, qty=qty, customer_id=customer, due_date=due,
                               variant="Demo weave · Demo finish", source="test")


def propose(conn, text="saw is broken for 2 days", key="r1"):
    card, model = ripple.build_card(conn, text)
    aid = ripple.store(conn, 3, "ripple", key, text, card, model)
    return aid, card


def test_machine_ripple_and_starvation_hand_math(conn):
    oid = order(conn)
    aid, card = propose(conn)
    # Two pieces: cut Wed, upholstery Thu. Lose Wed+Thu cutting -> cut Fri, upholstery Mon.
    move = card["movements"][0]
    assert move["order_id"] == oid
    assert (move["old_eta"], move["new_eta"], move["days"]) == ("2026-10-29", "2026-11-02", 4)
    assert move["notify"] and move["tag"] == "ESTIMATE"
    gap = next(g for g in card["starvation"] if g["stage_id"] == 2)
    assert gap["at"] == "2026-10-29T08:00+07:00"
    assert card["options"][0]["late_orders"] == 1
    assert 2 <= len(card["options"]) <= 3
    assert conn.execute("SELECT COUNT(*) FROM ripple_adjustments").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


@pytest.mark.parametrize("sid", [1, 2])
def test_two_day_outage_moves_every_order_queued_at_affected_stage(conn, sid):
    queued = [order(conn, qty=1, due="2026-10-30") for _ in range(2)]
    if sid == 2:
        for uid, in conn.execute("SELECT id FROM units").fetchall():
            domain.mark_done(conn, 1, uid)
    stage = next(s for s in domain.stages(conn) if s["id"] == sid)
    baseline = ripple.simulate(conn, [])
    shock = {"stage_id": sid, "lost_capacity": stage["capacity"],
             "start": "2026-10-28", "until": "2026-10-30"}
    disrupted = ripple.simulate(conn, [{"shock": shock}])
    assert {m["order_id"] for m in ripple.movements(baseline, disrupted)} >= set(queued)
    assert {m["order_id"] for m in ripple.stage_movements(baseline, disrupted)} >= set(queued)


def test_lateness_is_caused_by_shock_and_baseline_is_stated_separately(conn):
    already_late = order(conn, qty=1, due="2026-10-27")
    newly_late = order(conn, qty=1, due="2026-10-30")
    card = ripple.build_card(conn, "saw is broken for 2 days", False)[0]
    wait = card["options"][0]
    assert wait["baseline_late_orders"] == 1
    assert wait["late_orders"] == 1
    assert wait["total_late_orders"] == 2
    assert {m["order_id"] for m in card["movements"]} == {already_late, newly_late}
    assert all(o["saved_orders"] <= 1 for o in card["options"])


def test_saw_resolves_cutting_code_instead_of_stage_position(conn):
    conn.execute("UPDATE stages SET code='prep' WHERE id=1")
    conn.execute("UPDATE stages SET code='cut' WHERE id=2")
    oid = order(conn, qty=1)
    uid = conn.execute("SELECT id FROM units WHERE order_id=?", (oid,)).fetchone()[0]
    domain.mark_done(conn, 1, uid)
    card = ripple.build_card(conn, "Пила сломана на 2 дня", False)[0]
    assert card["shock"]["stage_id"] == 2
    assert card["movements"][0]["order_id"] == oid
    assert card["movements"][0]["new_eta"] > card["movements"][0]["old_eta"]


def test_downstream_buffer_keeps_final_eta_but_reports_queued_stage_delay(conn):
    # Existing upholstery backlog absorbs two lost cutting days for a stock order.
    backlog = order(conn, qty=8, due="2026-10-30")
    for uid, in conn.execute("SELECT id FROM units WHERE order_id=?", (backlog,)).fetchall():
        domain.mark_done(conn, 1, uid)
    queued = order(conn, qty=1, customer=None, due=None)
    card = ripple.build_card(conn, "saw is broken for 2 days", False)[0]
    assert card["shock"]["stage_id"] == 1
    assert not card["movements"]
    assert {m["order_id"] for m in card["stage_movements"]} == {queued}
    assert card["options"][0]["late_orders"] == 0
    assert card["options"][0]["baseline_late_orders"] == 1


def test_gate_requirement_change_invalidates_stored_scenario(conn):
    order(conn)
    aid, _ = propose(conn)
    conn.execute("DELETE FROM product_requirements WHERE product_id=1")
    conn.commit()
    with pytest.raises(DomainError, match="queue changed"):
        ripple.decide(conn, 3, aid, "wait")


def test_starvation_clock_uses_ready_inventory(conn):
    oid = order(conn, qty=1)
    uid = conn.execute("SELECT id FROM units WHERE order_id=?", (oid,)).fetchone()[0]
    domain.mark_done(conn, 1, uid)
    order(conn, qty=2)
    card = ripple.build_card(conn, "saw is broken for 2 days")[0]
    # One upholstery piece at 2/day uses half the 8h shift: stock runs out at noon.
    # Baseline also starves Wed at noon; only newly affected gaps are displayed.
    plan = ripple.simulate(conn, [{"shock": card["shock"]}])
    assert plan["gaps"][0]["at"] == "2026-10-28T12:00+07:00"


def test_material_delay_consumes_stock_first_and_only_at_bom_stage(conn):
    order(conn, qty=2)
    conn.execute("UPDATE stock_moves SET qty=1")
    conn.commit()
    card = ripple.build_card(conn, "foam delivery is 3 days late")[0]
    plan = ripple.simulate(conn, [{"shock": card["shock"]}])
    uph = [t["date"] for t in plan["tasks"] if t["stage_id"] == 2]
    assert uph == ["2026-10-29", "2026-11-02"]  # one from stock, remainder after Saturday recovery
    assert [t["date"] for t in plan["tasks"] if t["stage_id"] == 1] == ["2026-10-28"] * 2


def test_applied_material_delay_limits_real_planner_handout(conn):
    oid = order(conn, qty=2)
    for row in conn.execute("SELECT id FROM units WHERE order_id=?", (oid,)).fetchall():
        domain.mark_done(conn, 1, row[0])
    conn.execute("UPDATE stock_moves SET qty=1")
    conn.commit()
    aid, _ = propose(conn, "foam delivery is 3 days late")
    ripple.decide(conn, 3, aid, "wait")
    plan = planner.plan_tomorrow(conn, date.fromisoformat(TODAY))
    assert len(plan["stages"][1]["lines"]) == 1


def test_duplicate_bom_lines_share_one_balance(conn):
    oid = order(conn, qty=1)
    conn.execute("INSERT INTO bom(product_id,material_id,qty,stage_id) VALUES(1,1,1,2)")
    conn.execute("UPDATE stock_moves SET qty=1")
    conn.commit()
    shock = ripple.parse(conn, "foam delivery is 3 days late")[0]
    tasks = ripple.simulate(conn, [{"shock": shock}])["tasks"]
    assert next(t["date"] for t in tasks if t["stage_id"] == 2) == "2026-11-02"
    q = rush.build_card(conn, rush.QuoteIn(request_key="q", product_id=1, qty=1, due_date="2026-11-03"))[0]["quote"]
    assert len(q["material_shortages"]) == 1 and q["material_shortages"][0]["need"] == 4


def test_planner_reserves_delayed_material_across_stages_once(conn):
    first = order(conn, qty=1)
    uid = conn.execute("SELECT id FROM units WHERE order_id=?", (first,)).fetchone()[0]
    domain.mark_done(conn, 1, uid)
    order(conn, qty=1)
    conn.execute("INSERT INTO bom(product_id,material_id,qty,stage_id) VALUES(1,1,1,1)")
    conn.execute("UPDATE stock_moves SET qty=2")  # one consumed by the recorded cut, one left
    conn.commit()
    aid, _ = propose(conn, "foam delivery is 3 days late")
    ripple.decide(conn, 3, aid, "wait")
    plan = planner.plan_tomorrow(conn, date.fromisoformat(TODAY))
    assert sum(len(s["lines"]) for s in plan["stages"]) == 1


def test_absence_never_moves_another_trade_and_distinct_absences_add(conn):
    order(conn)
    aid, card = propose(conn, "Demo Cutter is sick for 2 days")
    ripple.decide(conn, 3, aid, "wait")
    plan = planner.plan_tomorrow(conn, date.fromisoformat(TODAY))
    cut, uph = plan["stages"]
    assert cut["capacity"] == 0 and cut["workers"] == [] and cut["lines"] == []
    assert [p["id"] for p in uph["workers"]] == [2]
    conn.execute("INSERT INTO people(id,name,role,stage_id) VALUES(5,'Demo Second Cutter','worker',1)")
    conn.commit()
    shock = dict(card["shock"], person_id=5)
    cap, absent, _ = ripple.planning_context(conn, "2026-10-28", [{"shock": card["shock"]}, {"shock": shock}])
    assert cap[1] == 0 and absent == {1, 5}


def test_overtime_cost_and_priority_effect_hand_math(conn):
    customer = order(conn, qty=4, due="2026-11-02")
    order(conn, qty=2, customer=None, due=None)
    _, card = propose(conn)
    ot = next(o for o in card["options"] if o["id"] == "half_shift")
    # Friday cut capacity 2 + 1 extra piece; one OT cutting piece at 10 * 1.5 = 15.
    assert ot["cost"] == 15
    assert ot["adjustment"]["overtime"][0]["person_ids"] == [1]
    assert all(m["tag"] == "ESTIMATE" for m in ot["movements"])
    assert next(o for o in card["options"] if o["id"] == "prioritize")["adjustment"]["priority_orders"] == [customer]


def test_stored_only_atomic_idempotent_apply_and_dry_run(conn):
    order(conn)
    aid, card = propose(conn)
    with pytest.raises(DomainError, match="stored"):
        ripple.decide(conn, 3, aid, "make_up_an_option")
    result = ripple.decide(conn, 3, aid, "wait")
    assert result["drafts"] == 1
    assert ripple.decide(conn, 3, aid, "wait")["already"]
    assert conn.execute("SELECT COUNT(*) FROM ripple_adjustments").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    notice = conn.execute("SELECT * FROM outbox").fetchone()
    assert notice["status"] == "draft" and notice["action_id"] == aid
    assert "ESTIMATE" in notice["body"] and "Заказ" in notice["body"]
    assert "ESTIMATE" in notice["attachment_html"]  # the existing outbox Preview is readable
    with pytest.raises(DomainError, match="Already decided"):
        ripple.decide(conn, 3, aid, "half_shift")
    assert conn.execute("SELECT COUNT(*) FROM work_events").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["UPDATE stages SET capacity=3 WHERE id=1", "UPDATE rates SET amount=11 WHERE stage_id=1",
                                     "UPDATE stock_moves SET qty=99", "UPDATE orders SET due_date='2026-11-03'"])
def test_stale_input_refuses_without_side_effects(conn, mutation):
    order(conn)
    aid, _ = propose(conn)
    conn.execute(mutation)
    conn.commit()
    with pytest.raises(DomainError, match="queue changed"):
        ripple.decide(conn, 3, aid, "wait")
    assert conn.execute("SELECT COUNT(*) FROM ripple_adjustments").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_next_shop_day_invalidates_and_retry_key_is_bound(conn, monkeypatch):
    order(conn)
    aid, _ = propose(conn)
    assert ripple.existing_request(conn, 3, "ripple", "r1", "saw is broken for 2 days") == aid
    with pytest.raises(DomainError, match="different input"):
        ripple.existing_request(conn, 3, "ripple", "r1", "another report")
    monkeypatch.setattr(domain, "today", lambda c: "2026-10-28")
    with pytest.raises(DomainError, match="queue changed"):
        ripple.decide(conn, 3, aid, "wait")


def test_spark_parse_validated_no_domain_writes(conn, monkeypatch):
    order(conn)
    seen = []
    def respond(c, purpose, prompt, **kwargs):
        seen.append(purpose)
        return {"output_text": json.dumps({"kind": "absence", "person_id": 1, "days": 2})}
    monkeypatch.setattr(models, "respond", respond)
    card, model = ripple.build_card(conn, "slang worker unavailable")
    assert card["shock"]["person_id"] == 1 and model == models.SPARK
    assert seen == ["ripple-parse"]
    assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 0
    monkeypatch.setattr(models, "respond", lambda *a, **k: {"output_text": '{"kind":"absence","person_id":999,"days":2}'})
    with pytest.raises(DomainError, match="resolve"):
        ripple.build_card(conn, "slang")


def test_proposal_refuses_concurrent_input_change_and_draft_is_escaped(conn, monkeypatch):
    order(conn)
    original = ripple.simulate
    def moving_floor(c, *args, **kwargs):
        result = original(c, *args, **kwargs)
        c.execute("UPDATE stages SET capacity=capacity+1 WHERE id=1")
        c.commit()
        return result
    monkeypatch.setattr(ripple, "simulate", moving_floor)
    with pytest.raises(DomainError, match="during calculation"):
        ripple.build_card(conn, "saw broken")
    assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 0
    ripple.draft(conn, 3, None, "<script>Demo</script>", "Demo <subject>", "<script>unsafe</script>")
    preview = conn.execute("SELECT attachment_html FROM outbox").fetchone()[0]
    assert "<script>" not in preview and "&lt;script&gt;" in preview
    conn.commit()


def test_draft_failure_rolls_back_adjustment_and_card(conn, monkeypatch):
    order(conn)
    aid, _ = propose(conn)
    def fail(*args):
        raise RuntimeError("simulated draft failure")
    monkeypatch.setattr(ripple, "draft", fail)
    with pytest.raises(RuntimeError):
        ripple.decide(conn, 3, aid, "wait")
    assert conn.execute("SELECT COUNT(*) FROM ripple_adjustments").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM agent_actions WHERE id=?", (aid,)).fetchone()[0] == "proposed"


@pytest.mark.parametrize("text", ["unknown person sick", "saw broken 999 days", "foam delivery 0 days late"])
def test_unresolved_or_unbounded_report_is_actionable(conn, text):
    with pytest.raises(DomainError):
        ripple.build_card(conn, text)


def test_zero_capacity_finite_unknown_eta_and_calendar(conn):
    oid = order(conn)
    conn.execute("UPDATE stages SET capacity=0 WHERE id=1")
    conn.commit()
    assert ripple.simulate(conn)["orders"][oid]["eta"] is None
    conn.execute("UPDATE stages SET capacity=2 WHERE id=1")
    conn.execute("INSERT INTO settings VALUES('workdays','135')")
    conn.commit()
    # Wednesday cutting -> Friday upholstery, respecting configured days.
    assert ripple.simulate(conn)["orders"][oid]["eta"] == "2026-10-30"


def test_rush_margin_materials_and_wip_math(conn):
    body = rush.QuoteIn(request_key="q1", product_id=1, qty=2, due_date="2026-10-30", target_margin=0.5)
    card, _ = rush.build_card(conn, body)
    q = card["quote"]
    # Price 2*100; cost 2*(10 material +10 cut +20 uph)=80. No OT; margin 60%.
    assert q["fits"] and q["eta"] == "2026-10-29"
    assert q["base_cost"] == 80 and q["extra_cost"] == 0
    assert q["margin_before"] == q["margin_after"] == q["margin_quoted"] == 0.6
    assert q["surcharge"] == 0 and q["wip_cash"]["value"] == 0 and q["incremental_wip"] == 80
    bridge = rush.margin_bridge(200, 120, 20, 0.5)
    assert bridge == {"base_cost": 120, "extra_cost": 20, "margin_before": 0.4, "margin_after": 0.3,
                      "required_revenue": 280, "surcharge": 80, "margin_quoted": 0.5}
    assert rush.margin_bridge(100, 61, 0, 0.4)["required_revenue"] == 102


def test_rush_overtime_by_stage_includes_displaced_work(conn):
    order(conn, qty=3, due="2026-10-29")
    card, _ = rush.build_card(conn, rush.QuoteIn(request_key="q1", product_id=1, qty=3, due_date="2026-10-29"))
    q = card["quote"]
    # Wed cuts 3 rush pieces; Thu upholsters 3. Premium = .5*(10+20)=15.
    assert q["priority_eta"] == "2026-10-30" and q["eta"] == "2026-10-29"
    assert q["extra_cost"] == 20  # Wed cut + Thu cut (displaced stock) + Thu upholstery
    assert q["overtime"][0]["pieces"] == 2 and q["overtime"][0]["extra_cost"] == 10
    assert q["overtime"][1]["pieces"] == 1 and q["overtime"][1]["extra_cost"] == 10
    assert card["movements"][0]["new_eta"] > card["movements"][0]["old_eta"]


def test_rush_no_rate_no_bom_no_material_price_are_unknown(conn):
    conn.execute("DELETE FROM rates WHERE stage_id=2")
    conn.commit()
    q = rush.build_card(conn, rush.QuoteIn(request_key="q", product_id=1, qty=1, due_date="2026-10-30"))[0]["quote"]
    assert q["stage_rates"][1]["rate"] is None and q["base_cost"] is None
    assert q["surcharge"] is None and q["margin_after"] is None
    conn.execute("DELETE FROM ripple_material_costs")
    conn.commit()
    assert rush.material_cost(conn, 1, "") is None
    conn.execute("DELETE FROM bom")
    conn.commit()
    q = rush.build_card(conn, rush.QuoteIn(request_key="q", product_id=1, qty=1, due_date="2026-10-30"))[0]["quote"]
    assert not q["fits"] and q["material_spec_unknown"]


def test_rush_material_shortage_and_impossible_deadline(conn):
    conn.execute("UPDATE stock_moves SET qty=0")
    conn.commit()
    q = rush.build_card(conn, rush.QuoteIn(request_key="q", product_id=1, qty=1, due_date="2026-10-30"))[0]["quote"]
    assert q["capacity_fits"] and not q["fits"] and q["material_shortages"][0]["on_hand"] == 0
    q = rush.build_card(conn, rush.QuoteIn(request_key="q", product_id=1, qty=1, due_date="2026-10-28"))[0]["quote"]
    assert not q["capacity_fits"]


def test_wip_earned_labour_and_frozen_payout(conn):
    oid = order(conn, qty=1)
    uid = conn.execute("SELECT id FROM units WHERE order_id=?", (oid,)).fetchone()[0]
    e = domain.mark_done(conn, 1, uid)
    assert rush.wip_cash(conn)["value"] == 10
    conn.execute("INSERT INTO payouts VALUES(1,1,'2026-10-27',3)")
    conn.execute("INSERT INTO payout_lines VALUES(1,?,10)", (e.event_id,))
    conn.execute("UPDATE rates SET amount=99 WHERE stage_id=1")
    conn.commit()
    assert rush.wip_cash(conn)["value"] == 10
    domain.mark_done(conn, 2, uid)
    assert rush.wip_cash(conn)["value"] == 40  # frozen cut10 + uph20 + foam10
    conn.execute("INSERT INTO shipments VALUES(1,1,'credit',0,'2026-10-27',3)")
    conn.execute("INSERT INTO shipment_lines VALUES(1,?,100)", (uid,))
    conn.commit()
    assert rush.wip_cash(conn)["value"] == 0


def test_rush_free_text_alias_friday_and_model_validation(conn, monkeypatch):
    conn.execute("INSERT INTO synonyms VALUES('product','cloud sofa',1,'2026-10-27')")
    conn.commit()
    data, model = rush.resolve(conn, rush.QuoteIn(request_key="q", text="customer wants 4 cloud sofa by Friday"))
    assert data == {"product_id": 1, "qty": 4, "due_date": "2026-10-30"}
    monkeypatch.setattr(models, "respond", lambda *a, **k: {"output_text": '{"product_id":999,"qty":1,"due_date":"2026-10-30"}'})
    with pytest.raises(DomainError):
        rush.resolve(conn, rush.QuoteIn(request_key="q", text="ambiguous"))


def test_http_auth_redaction_retry_and_stored_quote(conn):
    from floorhand.main import app
    from floorhand.web import get_conn, session
    app.dependency_overrides[get_conn] = lambda: conn
    client = TestClient(app)
    order(conn)
    try:
        assert client.get("/api/ripple/cards").status_code == 401
        assert client.post("/api/ripple/report", json={"text": "saw broken", "request_key": "x"}).status_code == 401
        assert client.post("/api/rush/quote", json={"request_key": "q"}).status_code == 401
        for role, pid, tablet in [("worker", 1, False), ("owner", 3, True), ("manager", 4, True)]:
            app.dependency_overrides[session] = lambda role=role, pid=pid, tablet=tablet: Session(person_id=pid, role=role, stage_id=1 if role == "worker" else None, tablet=tablet)
            assert client.get("/api/ripple/cards").status_code == 403
            assert client.get("/api/rush/catalog").status_code == 403
            assert client.get("/api/owner/outbox").status_code == 403
            assert client.post("/api/rush/quote", json={"request_key": "q"}).status_code == 403
            result = client.post("/api/ripple/report", json={"text": "saw broken 2 days", "request_key": f"x-{pid}-{tablet}"}).json()
            assert "card" not in result and "cost" not in json.dumps(result)
            assert client.post(f"/api/ripple/cards/{result['id']}/apply", json={"pick": "wait"}).status_code == 403
        app.dependency_overrides[session] = lambda: Session(person_id=3, role="owner", stage_id=None, tablet=False)
        feed = client.get("/api/owner/today").json()
        assert "ripple" in {c["kind"] for c in feed["cards"]}
        payload = {"request_key": "quote", "product_id": 1, "qty": 2, "due_date": "2026-10-30"}
        a = client.post("/api/rush/quote", json=payload).json()
        b = client.post("/api/rush/quote", json=payload).json()
        assert a["id"] == b["id"] and a["card"] == b["card"]
        aid = a["id"]
        assert client.post(f"/api/rush/cards/{aid}/apply", json={"pick": "draft_quote", "price": 1}).status_code == 422
        assert client.post(f"/api/rush/cards/{aid}/apply", json={"pick": "draft_quote"}).status_code == 200
        assert client.post(f"/api/rush/cards/{aid}/apply", json={"pick": "draft_quote"}).json()["already"]
        assert conn.execute("SELECT COUNT(*) FROM outbox WHERE action_id=?", (aid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1  # quote reserves nothing
        app.dependency_overrides[session] = lambda: Session(person_id=4, role="manager", stage_id=None, tablet=False)
        assert client.get("/api/ripple/cards").status_code == 200
        assert client.get("/api/rush/catalog").status_code == 200
        pending = client.get("/api/ripple/cards").json()["cards"][0]
        path = f"/api/ripple/cards/{pending['id']}/reject"
        assert client.post(path, json={"reason": "Demo plan changed / Демо-план изменился"}).json()["ok"]
        assert client.post(path, json={}).json()["already"]
        assert conn.execute("SELECT COUNT(*) FROM ripple_adjustments").fetchone()[0] == 0
    finally:
        app.dependency_overrides.clear()


def test_seed_scenario_is_live_and_bilingual(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOORHAND_META_LIVE", raising=False)
    c = db.connect(tmp_path / "seed.db")
    seed.build(c, random.Random(7), today=date.fromisoformat(TODAY))
    card = ripple.cards(c)[0]
    assert card["kind"] == "ripple" and card["movements"] and card["options"]
    assert card["headline"]["en"] and card["headline"]["ru"]
    assert card["fingerprint"] == ripple.snapshot(c)
    c.close()


def test_ui_modules_with_node():
    """Check escaping, bilingual unknowns, and id-only handlers without a browser or packages."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; UI syntax/render checks require Node")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--experimental-vm-modules", str(root / "tests" / "ripple-ui.mjs")],
                   cwd=root, check=True, capture_output=True, text=True)
