"""Sales snapshots and launch cards never turn a retry into duplicate work."""
import csv
import hashlib
import io
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from floorhand import db, domain, seed as demo_seed
from floorhand.domain import DomainError
from floorhand.features import channels
from floorhand.web import get_conn

DAY = date(2026, 10, 27)
VARIANT = "velvet beige"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(domain, "today", lambda c: DAY.isoformat())
    c = db.connect(tmp_path / "channels.db")
    db.init(c)
    c.execute("INSERT INTO settings VALUES ('tz', 'Asia/Bangkok')")
    c.execute("INSERT INTO settings VALUES ('currency', 'demo credits')")
    for seq, capacity in enumerate((10, 4, 8), 1):
        c.execute("INSERT INTO stages VALUES (?, ?, ?, ?, ?, ?)",
                  (seq, f"stage-{seq}", f"Demo stage {seq}", f"Демоэтап {seq}", seq, capacity))
        c.execute("INSERT INTO people VALUES (?, ?, 'worker', ?, 'en')", (seq, f"Demo worker {seq}", seq))
    c.execute("INSERT INTO people VALUES (10, 'Demo owner', 'owner', NULL, 'en')")
    c.execute("INSERT INTO people VALUES (11, 'Demo manager', 'manager', NULL, 'ru')")
    c.execute("INSERT INTO products VALUES (1, 'Round pouf', 'pouf', 137)")
    c.execute("INSERT INTO products VALUES (2, 'Cloudstep seat', 'chair', 173)")
    c.execute("INSERT INTO customers VALUES (1, 'Demo showroom', 'Demo town')")
    c.commit()
    yield c
    c.close()


def listing(conn, *, product=1, variant=VARIANT, sku="P-1", stock=1, channel="Northmart"):
    conn.execute("INSERT INTO channels (name, kind) VALUES (?, 'marketplace') ON CONFLICT(name) DO NOTHING", (channel,))
    cid = conn.execute("SELECT id FROM channels WHERE name = ?", (channel,)).fetchone()[0]
    conn.execute("INSERT INTO channel_listings (channel_id, product_id, variant, sku, price, stock_at_channel) "
                 "VALUES (?, ?, ?, ?, 137, ?)", (cid, product, variant, sku, stock))
    conn.commit()
    return cid


def demand(conn, cid=None):
    cid = cid or listing(conn)
    for ago in range(1, 29):
        conn.execute("INSERT INTO channel_sales (channel_id, sku, date, qty) VALUES (?, 'P-1', ?, ?)",
                     (cid, (DAY - timedelta(days=ago)).isoformat(), int(ago % 2 == 0)))
    conn.commit()


def order(conn, qty=1, *, customer=None, variant=VARIANT):
    return domain.create_order(conn, product_id=1, qty=qty, customer_id=customer,
                               due_date=None, variant=variant, source="test")


def finish(conn, oid):
    units = [r[0] for r in conn.execute("SELECT id FROM units WHERE order_id = ?", (oid,))]
    for uid in units:
        for person in (1, 2, 3):
            domain.mark_done(conn, person, uid)
    return units


def export(delimiter=","):
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(["Qty", "SKU", "Product", "Channel", "Kind", "Stock", "Price", "Variant", "Date"])
    writer.writerow([2, "P-1", "Round pouf", "Northmart", "marketplace", 3, 137, VARIANT, "2026-10-26"])
    return out.getvalue()


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|", "^", ":"])
def test_csv_by_header_any_delimiter_is_only_a_preview(conn, delimiter):
    text = export(delimiter)
    p = channels.propose_import(conn, 10, text, show_money=True)
    assert p["diff"] == {"new": channels.metric(2), "changed": channels.metric(0), "unchanged": channels.metric(0)}
    assert p["message_ru"] and p["rows"] == channels.metric(1)
    assert not conn.execute("SELECT 1 FROM channels").fetchone()
    assert not conn.execute("SELECT 1 FROM channel_sales").fetchone()
    stored = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (p["action_id"],)).fetchone()
    assert stored["kind"] == "channel_import" and stored["status"] == "proposed"
    assert stored["for_person"] == 10 and stored["model"] == "offline-rules"
    channels.apply_import(conn, 11, p["id"])
    row = conn.execute("SELECT * FROM channel_listings").fetchone()
    assert row["stock_at_channel"] == 3 and row["product_id"] == 1 and row["price"] == 137
    assert conn.execute("SELECT qty FROM channel_sales").fetchone()[0] == 2
    assert conn.execute("SELECT decided_by FROM agent_actions").fetchone()[0] == 11


def test_hash_and_natural_keys_prevent_duplicate_imports(conn):
    text = export()
    first = channels.propose_import(conn, 10, text)
    assert channels.propose_import(conn, 11, text)["id"] == first["id"]
    channels.apply_import(conn, 10, first["id"])
    assert channels.apply_import(conn, 11, first["id"])["already"]
    assert channels.propose_import(conn, 10, text)["status"] == "applied"
    other = channels.propose_import(conn, 10, export(";"))
    assert other["diff"]["unchanged"]["value"] == 2
    channels.apply_import(conn, 10, other["id"])
    assert conn.execute("SELECT COUNT(*) FROM channel_sales").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM channel_listings").fetchone()[0] == 1
    update = channels.propose_import(conn, 10,
        "channel;sku;date;qty;stock\nNorthmart;P-1;2026-10-26;5;9")
    assert update["diff"]["changed"]["value"] == 2
    channels.apply_import(conn, 10, update["id"])
    assert conn.execute("SELECT qty FROM channel_sales").fetchone()[0] == 5
    assert conn.execute("SELECT stock_at_channel FROM channel_listings").fetchone()[0] == 9
    # A sales-only export preserves current stock and price.
    sale = channels.propose_import(conn, 10, "channel,sku,date,qty\nNorthmart,P-1,2026-10-25,0")
    channels.apply_import(conn, 10, sale["id"])
    assert conn.execute("SELECT COUNT(*) FROM channel_sales").fetchone()[0] == 2
    assert conn.execute("SELECT SUM(qty) FROM channel_sales").fetchone()[0] == 5


def test_repeated_rows_are_one_snapshot_but_conflicts_refuse_atomically(conn):
    lines = export().splitlines()
    p = channels.propose_import(conn, 10, "\n".join([*lines, lines[1]]))
    assert p["rows"]["value"] == 2 and p["diff"]["new"]["value"] == 2
    with pytest.raises(DomainError, match="Conflicting"):
        channels.propose_import(conn, 10, "\n".join([*lines, lines[1].replace("2,P-1", "7,P-1")]))
    assert conn.execute("SELECT COUNT(*) FROM channel_imports").fetchone()[0] == 1
    assert not conn.execute("SELECT 1 FROM channels").fetchone()


@pytest.mark.parametrize("text", [
    "", "sku,stock\nP-1,2", "channel,sku,stock\nNorthmart,P-1,2,extra",
    "channel,sku,stock,kind,product_id\nNorthmart,P-1,-1,marketplace,1",
    "channel,sku,stock,kind,product_id\nNorthmart,P-1,1.5,marketplace,1",
    "channel,sku,stock,kind,product_id\nNorthmart,P-1,2,unknown,1",
    "channel,sku,stock,kind,product_id\nNorthmart,P-1,2,webshop,999",
    "channel,sku,stock,kind,product_id,date,qty\nNorthmart,P-1,2,webshop,1,2026-10-28,1",
    "channel,sku,stock,kind,product_id,date,qty\nNorthmart,P-1,2,webshop,1,2026-02-30,1",
    "channel,sku,stock,kind,product_id,date,qty\nNorthmart,P-1,2,webshop,1,2026-10-26,",
    "channel,sku,stock,kind,product_id\nNorthmart,P-1,,webshop,1",
    "channel,sku,stock,kind,product_id\nNorthmart,,2,webshop,1",
    "channel,sku,stock,stock,kind,product_id\nNorthmart,P-1,2,2,webshop,1",
    "channel,sku,stock,kind,product\nNorthmart,P-1,2,webshop,unknown furniture",
])
def test_invalid_imports_write_nothing_and_explain(conn, text):
    with pytest.raises(DomainError) as exc:
        channels.propose_import(conn, 10, text)
    assert "/" in str(exc.value)
    assert not conn.execute("SELECT 1 FROM agent_actions").fetchone()
    assert not conn.execute("SELECT 1 FROM channel_imports").fetchone()


def test_russian_headers_quoted_values_zero_stock_and_unknown_price(conn):
    text = 'Канал;Тип;Артикул;код_товара;Вариант;Остаток;Дата;Продажи\n"Demo; shop";webshop;P-1;1;бежевый;0;2026-10-26;0'
    p = channels.propose_import(conn, 10, text)
    channels.apply_import(conn, 10, p["id"])
    row = conn.execute("SELECT * FROM channel_listings").fetchone()
    assert row["price"] is None and row["stock_at_channel"] == 0 and row["variant"] == "бежевый"
    item = channels.overview(conn, show_money=True)["items"][0]
    assert item["listings"][0]["price"] == channels.metric(None)
    assert item["days_of_cover"] == channels.metric(None, "ESTIMATE") and item["no_sales"]


def test_preview_row_limit_writes_nothing(conn):
    text = "channel,sku,stock\n" + "Northmart,P-1,0\n" * 5001
    with pytest.raises(DomainError, match="split large files"):
        channels.propose_import(conn, 10, text)
    assert not conn.execute("SELECT 1 FROM agent_actions").fetchone()


def test_cannot_reassign_sku_or_change_channel_kind(conn):
    listing(conn)
    for text in (
        "channel,sku,stock,product_id\nNorthmart,P-1,2,2",
        "channel,sku,stock,variant\nNorthmart,P-1,2,graphite",
        "channel,sku,stock,kind\nNorthmart,P-1,2,wholesale",
    ):
        with pytest.raises(DomainError):
            channels.propose_import(conn, 10, text)


def test_import_stale_diff_reject_and_wrong_kind_guards(conn):
    listing(conn)
    p = channels.propose_import(conn, 10, "channel,sku,stock\nNorthmart,P-1,3")
    conn.execute("UPDATE channel_listings SET stock_at_channel = 9")
    conn.commit()
    with pytest.raises(DomainError, match="stale"):
        channels.apply_import(conn, 10, p["id"])
    assert conn.execute("SELECT stock_at_channel FROM channel_listings").fetchone()[0] == 9
    assert conn.execute("SELECT status FROM channel_imports").fetchone()[0] == "proposed"
    with pytest.raises(DomainError, match="No such proposal"):
        channels.apply_proposal(conn, 10, p["action_id"])
    channels.reject_proposal(conn, 11, p["action_id"])
    assert channels.apply_import(conn, 10, p["id"])["status"] == "rejected"
    assert channels.reject_proposal(conn, 10, p["action_id"])["already"]
    assert conn.execute("SELECT status FROM channel_imports").fetchone()[0] == "rejected"
    for func in (channels.apply_import, channels.apply_proposal, channels.reject_proposal):
        with pytest.raises(DomainError):
            func(conn, 10, 9999)


def test_import_failure_rolls_back_every_snapshot(conn):
    p = channels.propose_import(conn, 10, export())
    conn.execute("CREATE TRIGGER refuse_demo_sale BEFORE INSERT ON channel_sales "
                 "BEGIN SELECT RAISE(ABORT, 'demo failure'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="demo failure"):
        channels.apply_import(conn, 10, p["id"])
    assert not conn.execute("SELECT 1 FROM channels").fetchone()
    assert not conn.execute("SELECT 1 FROM channel_listings").fetchone()
    assert conn.execute("SELECT status FROM agent_actions").fetchone()[0] == "proposed"


def test_overview_uses_domain_readiness_shipping_and_exact_variants(conn):
    listing(conn, stock=2)
    listing(conn, stock=3, channel="Hearth & Home online")
    ready = finish(conn, order(conn, 2))
    order(conn, 1)  # In progress: not immediately saleable.
    finish(conn, order(conn, 3, customer=1))
    finish(conn, order(conn, variant="graphite"))
    cancelled = order(conn)
    finish(conn, cancelled)
    conn.execute("UPDATE orders SET cancelled = 1 WHERE id = ?", (cancelled,))
    sid = conn.execute("INSERT INTO shipments (customer_id, payment, created_at, created_by) "
                       "VALUES (1, 'paid', ?, 10)", (domain.now(conn).isoformat(),)).lastrowid
    conn.execute("INSERT INTO shipment_lines VALUES (?, ?, 137)", (sid, ready[0]))
    conn.commit()
    items = channels.overview(conn)["items"]
    item = next(i for i in items if i["variant"] == VARIANT)
    assert item["stock_at_channels"]["value"] == 5
    assert item["shop_ready"]["value"] == 1 and item["shop_in_progress"]["value"] == 1
    assert item["stock_total"] == channels.metric(6, "CALC")
    last = conn.execute("SELECT id FROM work_events WHERE unit_id = ? AND stage_id = 3", (ready[1],)).fetchone()[0]
    domain.undo(conn, 3, last)
    item = next(i for i in channels.overview(conn)["items"] if i["variant"] == VARIANT)
    assert item["shop_ready"]["value"] == 0 and item["shop_in_progress"]["value"] == 2


def test_completed_sales_windows_and_number_tags(conn):
    cid = listing(conn)
    for ago, qty in ((0, 999), (1, 2), (7, 5), (8, 3), (28, 4), (29, 999)):
        conn.execute("INSERT INTO channel_sales (channel_id, sku, date, qty) VALUES (?, 'P-1', ?, ?)",
                     (cid, (DAY - timedelta(days=ago)).isoformat(), qty))
    conn.commit()
    item = channels.overview(conn, show_money=True)["items"][0]
    assert item["sales_7_days"] == channels.metric(7)
    assert item["sales_28_days"] == channels.metric(14)
    assert item["sales_per_day_7"] == channels.metric(1, "CALC")
    assert item["sales_per_day_28"] == channels.metric(.5, "CALC")
    assert item["days_of_cover"] == channels.metric(1, "ESTIMATE")
    assert item["make_next"] == channels.metric(13, "ESTIMATE")
    assert all(item[key]["tag"] in {"FACT", "CALC", "ESTIMATE"} for key in (
        "stock_at_channels", "shop_ready", "shop_in_progress", "stock_total", "forecast_per_day", "target_days", "needed_qty"))


def test_six_unit_launch_uses_stored_offer_and_is_idempotent(conn):
    demand(conn)
    p = channels.suggest(conn, 10)["proposals"][0]
    assert p["kind"] == "make_to_stock" and p["qty"]["value"] == 6
    assert p["projected_cover"]["value"] == 14
    assert "ESTIMATE 6" in p["message"] and p["message_ru"]
    assert not conn.execute("SELECT 1 FROM orders").fetchone()
    assert channels.suggest(conn, 11)["proposals"][0]["id"] == p["id"]
    result = channels.apply_proposal(conn, 11, p["id"])
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (result["order_id"],)).fetchone()
    assert row["qty"] == 6 and row["source"] == "channels" and row["customer_id"] is None
    assert row["variant"] == VARIANT and row["due_date"] is None
    assert conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 6
    assert domain.order_detail(conn, row["id"])["status"] == "accepted"
    assert channels.apply_proposal(conn, 10, p["id"])["result"] == {"order_id": row["id"]}
    assert channels.reject_proposal(conn, 10, p["id"])["status"] == "applied"
    assert channels.suggest(conn, 10)["proposals"] == []  # Work already launched covers the need.


def test_capacity_shared_across_products_pending_cards_and_customer_work(conn):
    demand(conn)
    cid = listing(conn, product=2, sku="C-2", stock=0)
    conn.execute("INSERT INTO channel_sales (channel_id, sku, date, qty) VALUES (?, 'C-2', '2026-10-26', 70)", (cid,))
    order(conn, 35, customer=1)  # Second stage has 40 slots within the horizon, leaving five.
    conn.commit()
    data = channels.overview(conn)
    assert data["capacity"]["bottleneck"]["stage_id"] == 2
    assert data["capacity"]["available_launch_qty"]["value"] == 5
    assert sum(i["make_next"]["value"] for i in data["items"]) == 5
    assert any(i["capacity_limited"] for i in data["items"])
    cards = channels.suggest(conn, 10)["proposals"]
    assert sum(p["qty"]["value"] for p in cards) == 5
    assert channels.suggest(conn, 11)["proposals"] == cards
    order(conn, 5, customer=1)
    with pytest.raises(DomainError, match="stale"):
        channels.apply_proposal(conn, 10, cards[0]["id"])
    channels.reject_proposal(conn, 10, cards[0]["id"])
    assert not channels.suggest(conn, 10)["proposals"]


def test_launch_stale_after_stock_change_and_no_records_after_reject(conn):
    demand(conn)
    p = channels.suggest(conn, 10)["proposals"][0]
    conn.execute("UPDATE channel_listings SET stock_at_channel = 20")
    conn.commit()
    with pytest.raises(DomainError, match="stale"):
        channels.apply_proposal(conn, 10, p["id"])
    channels.reject_proposal(conn, 11, p["id"])
    assert channels.apply_proposal(conn, 10, p["id"])["status"] == "rejected"
    assert not conn.execute("SELECT 1 FROM orders").fetchone()
    row = conn.execute("SELECT * FROM agent_actions").fetchone()
    assert row["decided_by"] == 11 and row["decided_at"] and row["status"] == "rejected"


def test_launch_order_and_decision_are_atomic(conn, monkeypatch):
    demand(conn)
    p = channels.suggest(conn, 10)["proposals"][0]
    original = domain.create_order

    def fail_after_create(*args, **kwargs):
        original(*args, **kwargs)
        raise DomainError("demo failure")

    monkeypatch.setattr(domain, "create_order", fail_after_create)
    with pytest.raises(DomainError, match="demo failure"):
        channels.apply_proposal(conn, 10, p["id"])
    assert not conn.execute("SELECT 1 FROM orders").fetchone()
    assert not conn.execute("SELECT 1 FROM units").fetchone()
    assert conn.execute("SELECT status FROM agent_actions").fetchone()[0] == "proposed"


def test_concurrent_yes_creates_one_order(conn):
    demand(conn)
    p = channels.suggest(conn, 10)["proposals"][0]
    path = conn.execute("PRAGMA database_list").fetchone()["file"]

    def confirm(person):
        c = db.connect(path)
        try:
            return channels.apply_proposal(c, person, p["id"])
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(confirm, (10, 11)))
    assert sum(bool(r.get("already")) for r in responses) == 1
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_large_launches_respect_domain_batch_limit(conn):
    demand(conn)
    conn.execute("UPDATE stages SET capacity = 1000")
    conn.execute("UPDATE channel_sales SET qty = 100")
    conn.commit()
    p = channels.suggest(conn, 10)["proposals"][0]
    assert p["qty"]["value"] == 500
    channels.apply_proposal(conn, 10, p["id"])
    assert conn.execute("SELECT qty FROM orders").fetchone()[0] == 500


def test_another_busy_stage_can_limit_launches_before_the_bottleneck(conn):
    demand(conn)
    oid = order(conn, 100, customer=1)
    # These units only need the final stage, whose horizon budget is eighty.
    for uid in [r[0] for r in conn.execute("SELECT id FROM units WHERE order_id = ?", (oid,))]:
        domain.mark_done(conn, 1, uid)
        domain.mark_done(conn, 2, uid)
    data = channels.overview(conn)
    assert data["capacity"]["bottleneck"]["stage_id"] == 2
    assert data["capacity"]["available_launch_qty"]["value"] == 0
    assert data["items"][0]["needed_qty"]["value"] == 6
    assert data["items"][0]["make_next"]["value"] == 0
    assert channels.suggest(conn, 10)["reason"]["ru"]


def test_seed_has_four_weeks_low_stock_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setattr(domain, "today", lambda c: DAY.isoformat())
    c = db.connect(tmp_path / "demo.db")
    try:
        demo_seed.build(c, random.Random(26), today=DAY)
        assert c.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(DISTINCT date) FROM channel_sales").fetchone()[0] == 28
        assert c.execute("SELECT COUNT(*) FROM channel_sales").fetchone()[0] == 252
        data = channels.overview(c)
        assert sum(i["needed_qty"]["value"] > 0 for i in data["items"]) >= 2
        owner = c.execute("SELECT id FROM people WHERE role = 'owner'").fetchone()[0]
        assert channels.suggest(c, owner)["proposals"]
        channels.seed(c, random.Random(26))
        assert c.execute("SELECT COUNT(*) FROM channel_sales").fetchone()[0] == 252
    finally:
        c.close()


@pytest.fixture
def client(conn):
    app = FastAPI()
    app.include_router(channels.router)
    path = conn.execute("PRAGMA database_list").fetchone()["file"]

    def connection():
        c = db.connect(path)
        try:
            yield c
        finally:
            c.close()

    @app.exception_handler(DomainError)
    def refusal(request, exc):
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)

    app.dependency_overrides[get_conn] = connection
    for token, who, tablet in (("owner", 10, 0), ("manager", 11, 0), ("tablet", 10, 1), ("worker", 1, 0)):
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token, who, tablet, domain.now(conn).isoformat()))
    conn.commit()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("method,url,body", [
    ("GET", "/overview", None), ("POST", "/import", "a,b"),
    ("POST", "/import/1/apply", None), ("POST", "/suggest", None),
    ("POST", "/proposals/1/apply", None), ("POST", "/proposals/1/reject", None),
])
def test_all_routes_require_office_session(client, method, url, body):
    assert client.request(method, "/api/channels" + url, content=body).status_code == 401
    client.cookies.set("fh_session", "worker")
    assert client.request(method, "/api/channels" + url, content=body).status_code == 403


def has_money(value):
    if isinstance(value, dict):
        return any(k in ("price", "currency") or has_money(v) for k, v in value.items())
    return isinstance(value, list) and any(has_money(v) for v in value)


def test_money_cut_on_server_for_manager_and_shared_tablet(client, conn):
    demand(conn)
    for role, allowed in (("owner", True), ("manager", False), ("tablet", False)):
        client.cookies.set("fh_session", role)
        data = client.get("/api/channels/overview").json()
        assert has_money(data) == allowed
        response = client.post("/api/channels/import", content=export(), headers={"Content-Type": "text/csv"})
        assert response.status_code == 200 and has_money(response.json()) == allowed
        assert not has_money(client.post("/api/channels/suggest").json())


def test_http_uses_only_stored_proposal_and_reports_invalid_input(client, conn):
    demand(conn)
    client.cookies.set("fh_session", "owner")
    card = client.post("/api/channels/suggest", json={"target_days": 14}).json()["proposals"][0]
    response = client.post(f"/api/channels/proposals/{card['id']}/apply",
                           json={"qty": 499, "customer_id": 1, "product_id": 2})
    assert response.status_code == 200
    row = conn.execute("SELECT * FROM orders").fetchone()
    assert row["qty"] == 6 and row["product_id"] == 1 and row["customer_id"] is None
    for body in ({"target_days": 0}, {"target_days": 91}, {"target_days": 1.5}, {"qty": 200}):
        assert client.post("/api/channels/suggest", json=body).status_code == 422
    assert client.get("/api/channels/overview?target_days=0").status_code == 409
    assert client.post("/api/channels/import", content=b"\xff\xfe").status_code == 409
    assert client.post("/api/channels/import", content="x" * 1_000_001).status_code == 409
    assert client.post("/api/channels/import", content="wrong csv").json()["message"]


def test_import_apply_ignores_client_replacement_values(client, conn):
    client.cookies.set("fh_session", "manager")
    p = client.post("/api/channels/import", content=export()).json()
    r = client.post(f"/api/channels/import/{p['id']}/apply", json={"stock_at_channel": 999, "qty": 999})
    assert r.status_code == 200 and r.json()["message_ru"]
    assert conn.execute("SELECT stock_at_channel FROM channel_listings").fetchone()[0] == 3
    assert conn.execute("SELECT qty FROM channel_sales").fetchone()[0] == 2


def test_http_file_hash_preserves_utf8_bom_bytes(client):
    client.cookies.set("fh_session", "owner")
    raw = ("\ufeff" + export()).encode("utf-8")
    p = client.post("/api/channels/import", content=raw).json()
    assert p["file_hash"] == hashlib.sha256(raw).hexdigest()
    assert client.post("/api/channels/import", content=raw).json()["id"] == p["id"]


def test_worker_cannot_call_mutations_directly(conn):
    for call in (lambda: channels.propose_import(conn, 1, export()),
                 lambda: channels.suggest(conn, 1), lambda: channels.apply_import(conn, 1, 1),
                 lambda: channels.apply_proposal(conn, 1, 1), lambda: channels.reject_proposal(conn, 1, 1)):
        with pytest.raises(DomainError, match="office"):
            call()
