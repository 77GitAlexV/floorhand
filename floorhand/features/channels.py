"""Channel stock, daily sales snapshots and confirmed make-to-stock decisions."""
import csv
import hashlib
import io
import json
import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import domain
from ..domain import DomainError
from ..web import Session, get_conn, office

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('marketplace', 'webshop', 'wholesale'))
);
CREATE TABLE IF NOT EXISTS channel_listings (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    variant TEXT NOT NULL DEFAULT '',
    sku TEXT NOT NULL,
    price INTEGER CHECK (price >= 0),
    stock_at_channel INTEGER NOT NULL CHECK (stock_at_channel >= 0),
    UNIQUE (channel_id, sku)
);
CREATE TABLE IF NOT EXISTS channel_sales (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    sku TEXT NOT NULL,
    date TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty >= 0),
    FOREIGN KEY (channel_id, sku) REFERENCES channel_listings(channel_id, sku),
    UNIQUE (channel_id, sku, date)
);
CREATE TABLE IF NOT EXISTS channel_imports (
    id INTEGER PRIMARY KEY,
    who INTEGER NOT NULL REFERENCES people(id),
    created_at TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    rows INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'applied', 'rejected')),
    action_id INTEGER NOT NULL UNIQUE REFERENCES agent_actions(id)
);
"""

router = APIRouter(prefix="/api/channels", tags=["channels"])
KINDS = {"marketplace", "webshop", "wholesale"}
HEADERS = {
    "channel": {"channel", "channel_name", "канал"},
    "kind": {"kind", "channel_kind", "тип"},
    "product_id": {"product_id", "код_товара"},
    "product": {"product", "product_name", "товар", "изделие"},
    "variant": {"variant", "вариант", "ткань"},
    "sku": {"sku", "артикул"},
    "price": {"price", "цена"},
    "stock_at_channel": {"stock_at_channel", "stock", "остаток"},
    "date": {"date", "дата"},
    "qty": {"qty", "quantity", "sales", "продажи", "количество"},
}


def metric(value, tag="FACT"):
    return {"value": value, "tag": tag}


def message(en, ru, **extra):
    return {"ok": True, "message": en, "message_ru": ru, **extra}


def refuse(en, ru):
    raise DomainError(f"{en} / {ru}")


def _office(conn, person_id):
    who = domain.person(conn, person_id)
    if who["role"] not in ("owner", "manager"):
        refuse("This screen is for the office. Sign in with an office account.",
               "Этот экран для офиса. Войдите под учётной записью офиса.")
    return who


def _integer(text, label):
    try:
        value = int(text)
    except (ValueError, TypeError):
        refuse(f"{label}: use a whole non-negative number and import again.",
               f"{label}: укажите целое неотрицательное число и повторите импорт.")
    if value < 0 or value > 10**9:
        refuse(f"{label}: number is outside the supported range. Correct the file.",
               f"{label}: число вне допустимого диапазона. Исправьте файл.")
    return value


def _read_csv(text):
    """Sniff punctuation delimiters, while preserving quoted cells and headers."""
    if not text.strip() or len(text.encode("utf-8")) > 1_000_000:
        refuse("Paste a non-empty CSV smaller than one megabyte.",
               "Вставьте непустой CSV размером меньше одного мегабайта.")
    source = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(source[:16384])
        reader = csv.reader(io.StringIO(source), dialect, strict=True)
        headers = next(reader)
        columns = {}
        for i, header in enumerate(headers):
            h = header.strip().lower().replace(" ", "_")
            key = next((k for k, aliases in HEADERS.items() if h in aliases), None)
            if key:
                if key in columns:
                    raise ValueError("duplicate header")
                columns[key] = i
        if not {"channel", "sku"} <= columns.keys():
            raise ValueError("missing headers")
        rows = []
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                raise ValueError("row width")
            rows.append({k: row[i].strip() for k, i in columns.items()})
            if len(rows) > 5000:
                raise ValueError("too many rows")
        if not rows:
            raise ValueError("no rows")
        return rows
    except (csv.Error, ValueError, StopIteration):
        refuse("Check CSV headers and row widths. Include channel, sku and stock or date/qty; split large files.",
               "Проверьте заголовки и число столбцов CSV. Нужны channel, sku и stock либо date/qty; разделите большие файлы.")


def _listing(conn, channel, sku):
    row = conn.execute(
        "SELECT l.product_id, l.variant, l.price, l.stock_at_channel FROM channel_listings l "
        "JOIN channels c ON c.id = l.channel_id WHERE c.name = ? AND l.sku = ?",
        (channel, sku)).fetchone()
    return dict(row) if row else None


def _sale(conn, channel, sku, day):
    row = conn.execute(
        "SELECT s.qty FROM channel_sales s JOIN channels c ON c.id = s.channel_id "
        "WHERE c.name = ? AND s.sku = ? AND s.date = ?", (channel, sku, day)).fetchone()
    return {"qty": row["qty"]} if row else None


def _diff_row(kind, key, before, after):
    return {"kind": kind, "key": key, "before": before, "after": after,
            "status": "unchanged" if before == after else "new" if before is None else "changed"}


def _import_diff(conn, rows):
    """Natural keys describe snapshots, never increments. Conflicting duplicates refuse."""
    changes = {}
    listing_cache = {}
    for row in rows:
        channel, sku = row["channel"], row["sku"]
        if not channel or not sku:
            refuse("Each row needs channel and sku. Correct the file.",
                   "В каждой строке нужны канал и артикул. Исправьте файл.")
        existing_channel = conn.execute("SELECT kind FROM channels WHERE name = ?", (channel,)).fetchone()
        kind = row.get("kind") or (existing_channel["kind"] if existing_channel else "")
        if kind not in KINDS:
            refuse("Set kind to marketplace, webshop or wholesale for each new channel.",
                   "Для нового канала задайте kind: marketplace, webshop или wholesale.")
        if existing_channel and kind != existing_channel["kind"]:
            refuse("Channel kind differs from the catalog. Correct the file.",
                   "Тип канала отличается от каталога. Исправьте файл.")
        _collect(changes, ("channel", channel), _diff_row(
            "channel", {"channel": channel}, dict(existing_channel) if existing_channel else None, {"kind": kind}))
        key = (channel, sku)
        before = _listing(conn, *key)
        base = listing_cache.get(key, before)
        pid = _integer(row["product_id"], "product_id") if row.get("product_id") else None
        if row.get("product"):
            found = conn.execute("SELECT id FROM products WHERE name = ? COLLATE NOCASE", (row["product"],)).fetchall()
            if len(found) != 1 or (pid is not None and pid != found[0]["id"]):
                refuse("Product name is unknown or ambiguous. Use the catalog product_id.",
                       "Товар неизвестен или неоднозначен. Укажите product_id из каталога.")
            pid = found[0]["id"]
        if pid is None:
            pid = base["product_id"] if base else None
        if pid is None or not conn.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone():
            refuse("New SKUs need a catalog product or product_id. Correct the file.",
                   "Новому артикулу нужен товар или product_id из каталога. Исправьте файл.")
        after = {"product_id": pid,
                 "variant": row.get("variant", base["variant"] if base else ""),
                 "price": _integer(row["price"], "price") if row.get("price") else (base["price"] if base else None),
                 "stock_at_channel": (_integer(row["stock_at_channel"], "stock") if row.get("stock_at_channel")
                                      else base["stock_at_channel"] if base else None)}
        if after["stock_at_channel"] is None:
            refuse("New SKUs need an explicit stock count, including zero. Correct the file.",
                   "Новому артикулу нужен явный остаток, включая ноль. Исправьте файл.")
        if before and (after["product_id"], after["variant"]) != (before["product_id"], before["variant"]):
            refuse("An existing SKU cannot change product or variant: use a new SKU.",
                   "Нельзя менять товар или вариант существующего артикула: создайте новый артикул.")
        listing_cache[key] = after
        _collect(changes, ("listing", *key), _diff_row("listing", {"channel": channel, "sku": sku}, before, after))
        day, qty = row.get("date", ""), row.get("qty", "")
        if bool(day) != bool(qty) or not (row.get("stock_at_channel") or day):
            refuse("Supply stock or both date and qty. Correct the file.",
                   "Укажите остаток либо дату и количество продаж. Исправьте файл.")
        if day:
            try:
                parsed = date.fromisoformat(day)
                if parsed.isoformat() != day or day > domain.today(conn):
                    raise ValueError("future or non-ISO date")
            except ValueError:
                refuse("Sales dates must be YYYY-MM-DD and not in the future. Correct the file.",
                       "Дата продаж должна быть ГГГГ-ММ-ДД и не в будущем. Исправьте файл.")
            _collect(changes, ("sale", *key, day), _diff_row(
                "sale", {"channel": channel, "sku": sku, "date": day}, _sale(conn, channel, sku, day),
                {"qty": _integer(qty, "qty")}))
    return list(changes.values())


def _collect(changes, key, change):
    if key in changes and changes[key]["after"] != change["after"]:
        refuse("Conflicting rows share a natural key. Keep one daily total per channel/SKU/date and one stock snapshot.",
               "Строки с одинаковым ключом противоречат друг другу. Оставьте один итог за день и один остаток артикула.")
    changes[key] = change


def _public_diff(changes, show_money):
    out = []
    for change in changes:
        item = {k: v for k, v in change.items() if k not in ("before", "after")}
        for side in ("before", "after"):
            values = change[side]
            item[side] = None if values is None else {
                k: metric(v) if k in ("qty", "stock_at_channel", "price") else v
                for k, v in values.items() if k != "price" or show_money}
        out.append(item)
    return out


def _import_card(conn, record, show_money):
    action = conn.execute("SELECT payload FROM agent_actions WHERE id = ?", (record["action_id"],)).fetchone()
    changes = json.loads(action["payload"])["changes"]
    # Counts describe unique stock/sales records; channels have a separate diff.
    counts = {s: metric(sum(c["status"] == s for c in changes if c["kind"] != "channel"))
              for s in ("new", "changed", "unchanged")}
    return message("Review the CSV diff, then choose Yes to apply it.",
                   "Проверьте изменения CSV и нажмите Да для применения.",
                   id=record["id"], action_id=record["action_id"], status=record["status"],
                   file_hash=record["file_hash"], rows=metric(record["rows"]), diff=counts,
                   changes=_public_diff(changes, show_money))


def propose_import(conn, person_id, text, *, show_money=False):
    _office(conn, person_id)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT * FROM channel_imports WHERE file_hash = ?", (digest,)).fetchone()
        if prior:
            card = _import_card(conn, prior, show_money)
            card.update(already=True, message="Already recorded. Review the existing import.",
                        message_ru="Уже записано. Откройте существующий импорт.")
            return card
        rows = _read_csv(text)
        changes = _import_diff(conn, rows)
        action_id = _store_action(conn, person_id, "channel_import", {"changes": changes})
        iid = conn.execute(
            "INSERT INTO channel_imports (who, created_at, file_hash, rows, status, action_id) "
            "VALUES (?, ?, ?, ?, 'proposed', ?)",
            (person_id, domain.now(conn).isoformat(timespec="seconds"), digest, len(rows), action_id)).lastrowid
        record = conn.execute("SELECT * FROM channel_imports WHERE id = ?", (iid,)).fetchone()
        return _import_card(conn, record, show_money)


def _store_action(conn, person_id, kind, payload):
    return conn.execute(
        "INSERT INTO agent_actions (kind, payload, model, created_at, for_person) VALUES (?, ?, ?, ?, ?)",
        (kind, json.dumps(payload, ensure_ascii=False), "offline-rules",
         domain.now(conn).isoformat(timespec="seconds"), person_id)).lastrowid


def _action(conn, action_id, kind):
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind = ?", (action_id, kind)).fetchone()
    if row is None:
        refuse("No such proposal. Refresh the channels screen.", "Предложение не найдено. Обновите экран каналов.")
    return row


def _closed(row):
    if row["status"] == "proposed":
        return None
    return message("Already recorded. Nothing changed.", "Уже записано. Ничего не изменилось.",
                   already=True, status=row["status"], result=json.loads(row["result"] or "{}"))


def _decide(conn, action_id, person_id, status, result):
    conn.execute("UPDATE agent_actions SET status = ?, decided_by = ?, decided_at = ?, result = ? WHERE id = ?",
                 (status, person_id, domain.now(conn).isoformat(timespec="seconds"), json.dumps(result), action_id))


def apply_import(conn, person_id, import_id):
    _office(conn, person_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        record = conn.execute("SELECT * FROM channel_imports WHERE id = ?", (import_id,)).fetchone()
        if record is None:
            refuse("No such import. Preview the CSV first.", "Импорт не найден. Сначала загрузите CSV для проверки.")
        action = _action(conn, record["action_id"], "channel_import")
        if closed := _closed(action):
            return closed
        changes = json.loads(action["payload"])["changes"]
        # Check all snapshots before writing: a stale Yes must never overwrite newer data.
        for c in changes:
            key = c["key"]
            if c["kind"] == "channel":
                r = conn.execute("SELECT kind FROM channels WHERE name = ?", (key["channel"],)).fetchone()
                current = dict(r) if r else None
            elif c["kind"] == "listing":
                current = _listing(conn, key["channel"], key["sku"])
            else:
                current = _sale(conn, key["channel"], key["sku"], key["date"])
            if current != c["before"] and current != c["after"]:
                refuse("The CSV diff is stale. Reject this import and preview a fresh export.",
                       "Данные изменились после проверки. Отклоните импорт и загрузите свежую выгрузку.")
        for c in changes:
            key, after = c["key"], c["after"]
            if c["kind"] == "channel":
                conn.execute("INSERT INTO channels (name, kind) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
                             (key["channel"], after["kind"]))
                continue
            cid = conn.execute("SELECT id FROM channels WHERE name = ?", (key["channel"],)).fetchone()[0]
            if c["kind"] == "listing":
                conn.execute(
                    "INSERT INTO channel_listings (channel_id, sku, product_id, variant, price, stock_at_channel) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(channel_id, sku) DO UPDATE SET "
                    "price = excluded.price, stock_at_channel = excluded.stock_at_channel",
                    (cid, key["sku"], after["product_id"], after["variant"], after["price"], after["stock_at_channel"]))
            else:
                conn.execute(
                    "INSERT INTO channel_sales (channel_id, sku, date, qty) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(channel_id, sku, date) DO UPDATE SET qty = excluded.qty",
                    (cid, key["sku"], key["date"], after["qty"]))
        result = {"import_id": import_id}
        _decide(conn, action["id"], person_id, "applied", result)
        conn.execute("UPDATE channel_imports SET status = 'applied' WHERE id = ?", (import_id,))
        return message("CSV applied. Stock and daily sales totals are recorded; nothing was sent outside.",
                       "CSV применён. Остатки и дневные итоги продаж записаны; внешних отправок нет.",
                       status="applied", **result)


def _plan(conn, target_days):
    if not 1 <= target_days <= 90:
        refuse("Choose a cover horizon within the supported range.", "Выберите допустимый горизонт запаса.")
    day = date.fromisoformat(domain.today(conn))
    stages = domain.stages(conn)
    bottleneck = min(stages, key=lambda s: s["capacity"]) if stages else None
    working_days = sum((day + timedelta(days=i)).weekday() < 5 for i in range(1, target_days + 1))
    # Every open unit still needs capacity at its unfinished stages, including customer work.
    loads = {s["id"]: 0 for s in stages}
    shop = {}
    for u in conn.execute("SELECT u.id, o.product_id, o.variant, o.customer_id FROM units u "
                          "JOIN orders o ON o.id = u.order_id WHERE o.cancelled = 0"):
        if domain.is_shipped(conn, u["id"]):
            continue
        current = domain.current_stage(conn, u["id"])
        if current:
            done = domain.done_stage_ids(conn, u["id"])
            for s in stages:
                loads[s["id"]] += s["id"] not in done
        if u["customer_id"] is None:
            counts = shop.setdefault((u["product_id"], u["variant"]), [0, 0])
            counts[0 if current is None else 1] += 1
    available = max(0, min((s["capacity"] * working_days - loads[s["id"]] for s in stages), default=0))
    groups = {}
    for listing in conn.execute("SELECT l.*, p.name AS product, c.name AS channel, c.kind FROM channel_listings l "
                                "JOIN products p ON p.id = l.product_id JOIN channels c ON c.id = l.channel_id"):
        key = (listing["product_id"], listing["variant"])
        item = groups.setdefault(key, {"product_id": key[0], "product": listing["product"], "variant": key[1],
                                       "listings": [], "stock": 0, "sales7": 0, "sales28": 0})
        item["listings"].append(dict(listing))
        item["stock"] += listing["stock_at_channel"]
        for n in (7, 28):
            start = (day - timedelta(days=n)).isoformat()
            # Completed calendar days only: today's partial sales cannot dilute the average.
            item[f"sales{n}"] += conn.execute(
                "SELECT COALESCE(SUM(qty), 0) FROM channel_sales WHERE channel_id = ? AND sku = ? "
                "AND date >= ? AND date < ?", (listing["channel_id"], listing["sku"], start, day.isoformat())).fetchone()[0]
    for key, counts in shop.items():
        if key not in groups:
            product = conn.execute("SELECT name FROM products WHERE id = ?", (key[0],)).fetchone()[0]
            groups[key] = {"product_id": key[0], "product": product, "variant": key[1], "listings": [],
                           "stock": 0, "sales7": 0, "sales28": 0}
    for key, item in groups.items():
        item["ready"], item["in_progress"] = shop.get(key, [0, 0])
        item["total"] = item["stock"] + item["ready"]
        # Integer numerators prevent a floating-point ceil from proposing an extra unit.
        numerator = max(item["sales7"] * 4, item["sales28"])
        item["demand"] = numerator / 28
        item["cover"] = item["total"] * 28 / numerator if numerator else None
        item["need"] = max(0, (numerator * target_days + 27) // 28 - item["total"] - item["in_progress"])
    items = sorted(groups.values(), key=lambda i: (i["cover"] if i["cover"] is not None else math.inf,
                                                   i["product_id"], i["variant"]))
    budget = available
    for item in items:
        item["launch"] = min(item["need"], budget, 500)
        budget -= item["launch"]
    return items, {"bottleneck": bottleneck, "available": available, "working_days": working_days,
                   "loads": loads, "stages": stages}


def _item_card(item, target_days, show_money):
    listings = []
    for row in item["listings"]:
        card = {"channel_id": row["channel_id"], "channel": row["channel"], "kind": row["kind"], "sku": row["sku"],
                "stock_at_channel": metric(row["stock_at_channel"])}
        if show_money:
            card["price"] = metric(row["price"])
        listings.append(card)
    return {"product_id": item["product_id"], "product": item["product"], "variant": item["variant"],
            "listings": listings, "stock_at_channels": metric(item["stock"]), "shop_ready": metric(item["ready"]),
            "shop_in_progress": metric(item["in_progress"]), "stock_total": metric(item["total"], "CALC"),
            "sales_7_days": metric(item["sales7"]), "sales_28_days": metric(item["sales28"]),
            "sales_per_day_7": metric(round(item["sales7"] / 7, 4), "CALC"),
            "sales_per_day_28": metric(round(item["sales28"] / 28, 4), "CALC"),
            "forecast_per_day": metric(round(item["demand"], 4), "ESTIMATE"),
            "days_of_cover": metric(round(item["cover"], 2) if item["cover"] is not None else None, "ESTIMATE"),
            "target_days": metric(target_days, "ESTIMATE"), "needed_qty": metric(item["need"], "ESTIMATE"),
            "make_next": metric(item["launch"], "ESTIMATE"),
            "capacity_limited": item["launch"] < item["need"], "no_sales": item["demand"] == 0}


def overview(conn, target_days=14, *, show_money=False):
    items, capacity = _plan(conn, target_days)
    bottleneck = capacity["bottleneck"]
    out = message("Stock and sales are ready. Review low cover and choose Suggest to prepare launch cards.",
                  "Остатки и продажи готовы. Проверьте низкий запас и нажмите Предложить для подготовки запуска.",
                  date=domain.today(conn), target_days=metric(target_days, "ESTIMATE"),
                  items=[_item_card(i, target_days, show_money) for i in items],
                  capacity={"bottleneck": None if bottleneck is None else {
                      "stage_id": bottleneck["id"], "name_en": bottleneck["name_en"], "name_ru": bottleneck["name_ru"],
                      "per_workday": metric(bottleneck["capacity"])},
                      "working_days": metric(capacity["working_days"], "CALC"),
                      "available_launch_qty": metric(capacity["available"], "ESTIMATE"),
                      "stages": [{"stage_id": s["id"], "name_en": s["name_en"], "name_ru": s["name_ru"],
                                  "per_workday": metric(s["capacity"]), "unfinished_units": metric(capacity["loads"][s["id"]])}
                                 for s in capacity["stages"]]},
                  assumptions={"en": "Use the higher completed-day sales average; missing days count as zero. "
                               "Cover excludes work in progress; launch need subtracts it. Capacity assumes weekdays, "
                               "shared units per stage, and current unfinished work; delivery time and future orders are unknown. "
                               "No sales means unknown cover. Forecasts are estimates, not delivery promises.",
                               "ru": "Используется больший средний объём продаж за полные дни; пропущенные дни считаются нулевыми. "
                               "Запас не включает незавершённую работу; потребность её учитывает. Мощность рассчитана по будням, "
                               "общему числу изделий на этапе и текущей работе; сроки доставки и будущие заказы неизвестны. "
                               "Без продаж запас в днях неизвестен. Прогноз — оценка, а не обещание срока."},
                  proposals=[_proposal_card(r) for r in conn.execute(
                      "SELECT * FROM agent_actions WHERE kind = 'make_to_stock' AND status = 'proposed' ORDER BY id")],
                  imports=[{"id": r["id"], "action_id": r["action_id"], "who": r["who"], "created_at": r["created_at"],
                            "file_hash": r["file_hash"], "rows": metric(r["rows"]), "status": r["status"]}
                           for r in conn.execute("SELECT * FROM channel_imports ORDER BY id DESC LIMIT 20")])
    if show_money:
        out["currency"] = domain.currency(conn)
    return out


def _proposal_card(row):
    p = json.loads(row["payload"])
    projected = p["projected_cover"]
    cover_en = f"ESTIMATE {projected:g} days" if projected is not None else "unknown cover"
    cover_ru = f"ESTIMATE {projected:g} дн." if projected is not None else "запас неизвестен"
    return {"id": row["id"], "kind": row["kind"], "status": row["status"], "model": row["model"],
            "product_id": p["product_id"], "product": p["product"], "variant": p["variant"],
            "qty": metric(p["qty"], "ESTIMATE"), "target_days": metric(p["target_days"], "ESTIMATE"),
            "projected_cover": metric(projected, "ESTIMATE"), "created_at": row["created_at"],
            "message": f"Launch ESTIMATE {p['qty']} × {p['product']} {p['variant']} to stock (covers {cover_en}).",
            "message_ru": f"Запустить ESTIMATE {p['qty']} × {p['product']} {p['variant']} на склад (запас {cover_ru})."}


def suggest(conn, person_id, target_days=14):
    _office(conn, person_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        items, capacity = _plan(conn, target_days)
        existing = conn.execute("SELECT * FROM agent_actions WHERE kind = 'make_to_stock' AND status = 'proposed'").fetchall()
        pending = [json.loads(r["payload"]) for r in existing]
        reserved = sum(p["qty"] for p in pending)
        budget = max(0, capacity["available"] - reserved)
        keys = {(p["product_id"], p["variant"]) for p in pending}
        cards = [_proposal_card(r) for r in existing]
        for item in items:
            if (item["product_id"], item["variant"]) in keys:
                continue
            qty = min(item["need"], budget, 500)
            if not qty:
                continue
            budget -= qty
            payload = {"product_id": item["product_id"], "product": item["product"], "variant": item["variant"],
                       "qty": qty, "target_days": target_days,
                       "projected_cover": round((item["total"] + item["in_progress"] + qty) / item["demand"], 2),
                       "basis": _item_card(item, target_days, False)}
            action_id = _store_action(conn, person_id, "make_to_stock", payload)
            cards.append(_proposal_card(_action(conn, action_id, "make_to_stock")))
        return message("Review launch cards and choose Yes or No. No orders were created.",
                       "Проверьте карточки запуска и выберите Да или Нет. Заказы не созданы.", proposals=cards,
                       reason=None if cards else {"en": "No uncovered demand or no available stage capacity.",
                                                  "ru": "Нет непокрытого спроса или свободной мощности этапов."})


def apply_proposal(conn, person_id, action_id):
    _office(conn, person_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _action(conn, action_id, "make_to_stock")
        if closed := _closed(row):
            return closed
        p = json.loads(row["payload"])
        items, capacity = _plan(conn, p["target_days"])
        current = next((i for i in items if (i["product_id"], i["variant"]) == (p["product_id"], p["variant"])), None)
        if current is None or current["need"] < p["qty"] or capacity["available"] < p["qty"]:
            refuse("This launch is stale: stock, demand or capacity changed. Choose No, then Suggest again.",
                   "Предложение устарело: изменились запас, спрос или мощность. Выберите Нет и запросите новое.")
        oid = domain.create_order(conn, product_id=p["product_id"], qty=p["qty"], customer_id=None,
                                  due_date=None, variant=p["variant"], source="channels", commit=False)
        result = {"order_id": oid}
        _decide(conn, action_id, person_id, "applied", result)
        return message("To-stock order created from the confirmed launch card.",
                       "Заказ на склад создан по подтверждённой карточке запуска.", status="applied", **result)


def reject_proposal(conn, person_id, action_id):
    _office(conn, person_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind IN ('make_to_stock', 'channel_import')",
                           (action_id,)).fetchone()
        if row is None:
            refuse("No such proposal. Refresh the screen.", "Предложение не найдено. Обновите экран.")
        if closed := _closed(row):
            return closed
        _decide(conn, action_id, person_id, "rejected", {})
        conn.execute("UPDATE channel_imports SET status = 'rejected' WHERE action_id = ?", (action_id,))
        return message("Rejected. The decision is recorded; no stock or orders changed.",
                       "Отклонено. Решение записано; остатки и заказы не изменены.", status="rejected")


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_days: int = Field(default=14, ge=1, le=90, strict=True)


@router.get("/overview")
def get_overview(target_days: int = 14, s: Session = Depends(office), conn=Depends(get_conn)):
    return overview(conn, target_days, show_money=s.role == "owner" and not s.tablet)


@router.post("/import")
async def import_csv(request: Request, s: Session = Depends(office), conn=Depends(get_conn)):
    raw = await request.body()
    if len(raw) > 1_000_000:
        refuse("Split this CSV into smaller files.", "Разделите CSV на меньшие файлы.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        refuse("Save the CSV as UTF-8 and try again.", "Сохраните CSV в UTF-8 и повторите загрузку.")
    return propose_import(conn, s.person_id, text, show_money=s.role == "owner" and not s.tablet)


@router.post("/import/{import_id}/apply")
def confirm_import(import_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    return apply_import(conn, s.person_id, import_id)


@router.post("/suggest")
def post_suggest(body: SuggestIn = SuggestIn(), s: Session = Depends(office), conn=Depends(get_conn)):
    return suggest(conn, s.person_id, body.target_days)


@router.post("/proposals/{action_id}/apply")
def confirm_launch(action_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    return apply_proposal(conn, s.person_id, action_id)


@router.post("/proposals/{action_id}/reject")
def reject(action_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    return reject_proposal(conn, s.person_id, action_id)


def seed(conn, rnd):
    """Invented marketplace snapshots: two low-stock lines and one slower seller."""
    if conn.execute("SELECT 1 FROM channels LIMIT 1").fetchone():
        return
    products = conn.execute("SELECT id, name FROM products ORDER BY CASE WHEN kind = 'pouf' THEN 0 ELSE 1 END, id LIMIT 3").fetchall()
    if len(products) < 3:
        return
    day = date.fromisoformat(domain.today(conn))
    names = [("Northmart", "marketplace"), ("Hearth & Home online", "webshop"), ("Birchbay trade", "wholesale")]
    for idx, (name, kind) in enumerate(names):
        cid = conn.execute("INSERT INTO channels (name, kind) VALUES (?, ?)", (name, kind)).lastrowid
        for j, product in enumerate(products):
            sku = f"DEMO-{idx + 1}-{j + 1}"
            conn.execute("INSERT INTO channel_listings (channel_id, product_id, variant, sku, price, stock_at_channel) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (cid, product["id"], "velvet beige", sku, 137 + j * 19, (1 if j < 2 else 24) + idx))
            for ago in range(1, 29):
                qty = rnd.choice([0, 1, 1, 2]) if j < 2 else int(ago % 7 == 0)
                conn.execute("INSERT INTO channel_sales (channel_id, sku, date, qty) VALUES (?, ?, ?, ?)",
                             (cid, sku, (day - timedelta(days=ago)).isoformat(), qty))
