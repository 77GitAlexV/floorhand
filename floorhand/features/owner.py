"""The owner's "Today": a feed of decisions, not a chat and not a bell.

Each card is a stored proposal (agent_actions): a one-line headline, the numbers,
a "Why?" with every line tagged FACT / CALC / ESTIMATE, and Yes / Change / No.
Nothing is preselected. "Yes" applies the STORED proposal by id (only what was
offered), "Change" applies a subset of it, "No" records the decision with a reason.

Lessons from the real workshop baked in:
- the same question is never asked twice (a key per card; a changed card
  supersedes the old one, a card whose reason is gone closes itself);
- a card where the system itself would advise "leave it" is not shown;
- a new kind of advice runs in the shadow first: it is scored against what the
  floor actually did, and "Yes" switches on only after the hit rate is proven
  and the owner says so (the old ERP's day plan guessed ~10% and was deleted);
- numbers are computed here; a model may only put them into words.
"""
import hashlib
import json
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import domain, models
from ..domain import LIVE_DONE, DomainError
from ..models import BudgetStop
from ..web import Session, get_conn, office, session
from . import ask as askmod, materials, planner
from .ask import calc, est, fact, tt

SCHEMA = """
-- Anything that would leave the shop (purchase requests, notes to customers)
-- lands here first. Real sending only when configured; by default it is a dry run.
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('purchase', 'email')),
    to_name     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  INTEGER REFERENCES people(id),
    action_id   INTEGER REFERENCES agent_actions(id),
    status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent'))
);

-- How a proposal scored against what the floor actually did (shadow learning).
CREATE TABLE IF NOT EXISTS advice_scores (
    action_id   INTEGER PRIMARY KEY REFERENCES agent_actions(id),
    kind        TEXT NOT NULL,
    target_date TEXT NOT NULL,
    hits        INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    scored_at   TEXT NOT NULL
);
"""

router = APIRouter()

KINDS = ("plan", "shortage", "late_risk", "idle_capacity")
SHADOW_MIN_SCORED = 5       # proposals scored before the kind may be switched on
SHADOW_MIN_RATE = 0.7       # ...and at least this share matched the floor
ENABLED_DEFAULT = ["plan", "shortage", "late_risk"]   # idle_capacity starts in the shadow


def _wd(d: str, lang: str) -> str:
    day = date.fromisoformat(d)
    names = {"en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
             "ru": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]}[lang]
    return f"{names[day.weekday()]} {day.strftime('%d.%m')}"


def _fp(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


# --- settings: which advice kinds are switched on --------------------------

def enabled_kinds(conn: sqlite3.Connection) -> set[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = 'advice_enabled'").fetchone()
    return set(json.loads(row["value"])) if row else set(ENABLED_DEFAULT)


def shadow_stats(conn: sqlite3.Connection, kind: str) -> dict:
    row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h, COALESCE(SUM(total), 0) AS t "
                       "FROM advice_scores WHERE kind = ?", (kind,)).fetchone()
    rate = row["h"] / row["t"] if row["t"] else None
    decided = conn.execute(
        "SELECT status, COUNT(*) AS n FROM agent_actions WHERE kind = ? AND status != 'proposed' "
        "AND COALESCE(result, '') NOT LIKE '%\"superseded\"%' AND COALESCE(result, '') NOT LIKE '%\"expired\"%' "
        "GROUP BY status", (kind,)).fetchall()
    dec = {r["status"]: r["n"] for r in decided}
    enabled = kind in enabled_kinds(conn)
    ready = row["n"] >= SHADOW_MIN_SCORED and rate is not None and rate >= SHADOW_MIN_RATE
    return {"kind": kind, "scored": row["n"], "hits": row["h"], "total": row["t"],
            "rate": None if rate is None else round(rate, 2), "enabled": enabled,
            "ready": ready and not enabled, "min_scored": SHADOW_MIN_SCORED,
            "min_rate": SHADOW_MIN_RATE, "accepted": dec.get("applied", 0),
            "rejected": dec.get("rejected", 0)}


def enable_kind(conn: sqlite3.Connection, kind: str) -> dict:
    if kind not in KINDS:
        raise DomainError("No such kind of advice.")
    st = shadow_stats(conn, kind)
    if st["enabled"]:
        return {"ok": True, "message": "Already switched on."}
    if not st["ready"]:
        raise DomainError(f"Still learning: {st['scored']} scored at "
                          f"{round((st['rate'] or 0) * 100)}%, needs {SHADOW_MIN_SCORED} at "
                          f"{round(SHADOW_MIN_RATE * 100)}%.")
    kinds = sorted(enabled_kinds(conn) | {kind})
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('advice_enabled', ?)",
                 (json.dumps(kinds),))
    conn.commit()
    return {"ok": True, "message": f"Switched on: {kind}. Yes now applies these cards."}


# --- scoring against the floor --------------------------------------------

def score_proposals(conn: sqlite3.Connection, today: date) -> int:
    """Score every past proposal of a scorable kind once. Returns how many were scored."""
    n = 0
    rows = conn.execute(
        "SELECT a.id, a.kind, a.payload FROM agent_actions a "
        "LEFT JOIN advice_scores s ON s.action_id = a.id "
        "WHERE a.kind IN ('plan', 'idle_capacity') AND s.action_id IS NULL "
        "AND COALESCE(a.result, '') NOT LIKE '%\"superseded\"%'").fetchall()
    for r in rows:
        p = json.loads(r["payload"])
        target = p.get("target_date")
        if not target or date.fromisoformat(target) >= today:
            continue
        hits, total = _score(conn, r["kind"], p)
        if total:
            conn.execute("INSERT INTO advice_scores VALUES (?, ?, ?, ?, ?, ?)",
                         (r["id"], r["kind"], target, hits, total,
                          domain.now(conn).isoformat(timespec="seconds")))
            n += 1
    conn.commit()
    return n


def _score(conn, kind: str, p: dict) -> tuple[int, int]:
    lines = p.get("lines", [])
    if kind == "plan":
        hits = 0
        for ln in lines:
            if conn.execute(f"SELECT 1 FROM ({LIVE_DONE}) WHERE unit_id = ? AND stage_id = ? "
                            "AND work_date = ?", (ln["unit_id"], ln["stage_id"], p["target_date"])).fetchone():
                hits += 1
        return hits, len(lines)
    if kind == "idle_capacity":
        until = (date.fromisoformat(p["target_date"]) + timedelta(days=3)).isoformat()
        hits = 0
        for ln in lines:
            if conn.execute("SELECT 1 FROM orders WHERE product_id = ? AND customer_id IS NULL "
                            "AND substr(created_at, 1, 10) BETWEEN ? AND ?",
                            (ln["product_id"], p["target_date"], until)).fetchone():
                hits += 1
        return hits, len(lines)
    return 0, 0


# --- building the cards -------------------------------------------------------

def _plan_card(conn, plan: dict) -> dict | None:
    lines = [ln for s in plan["stages"] for ln in s["lines"]]
    bn = next((s for s in plan["stages"] if s["bottleneck"]), None)
    if not lines and bn is None:
        return None
    target = plan["target"]
    on_hands = sum(s["on_hands"] for s in plan["stages"])
    first = sum(1 for ln in lines if ln["risk"] != planner.RISK_OK)
    bn_en = f"; {bn['name_en']} is the bottleneck: {bn['queue']} waiting at {bn['capacity']}/day" if bn else ""
    bn_ru = f"; узкое место {bn['name_ru']}: {bn['queue']} в очереди при {bn['capacity']}/день" if bn else ""
    why = []
    for s in plan["stages"]:
        why.append(fact(f"{s['name_en']}: {s['queue']} waiting, {s['on_hands']} already on hands, "
                        f"capacity {s['capacity']}/day.",
                        f"{s['name_ru']}: {s['queue']} в очереди, {s['on_hands']} уже на руках, "
                        f"мощность {s['capacity']}/день."))
        why.append(calc(f"{s['name_en']}: free capacity {max(0, s['capacity'] - s['on_hands'])} = "
                        f"capacity minus what is on hands -> hand out {len(s['lines'])}, "
                        f"{s['left_after']} still wait.",
                        f"{s['name_ru']}: свободно {max(0, s['capacity'] - s['on_hands'])} = мощность минус "
                        f"на руках -> выдать {len(s['lines'])}, ждут ещё {s['left_after']}."))
        for w in s["overloaded"]:
            why.append(fact(f"{w['name']} has {w['on_hands']} on hands against a norm of {w['norm']}: "
                            f"nothing new for them.",
                            f"У {w['name']} на руках {w['on_hands']} при норме {w['norm']}: нового не даём."))
    if bn:
        why.append(est(f"{bn['name_en']} needs {bn['days_to_clear']} working days to clear at full capacity; "
                       f"every stage after it waits.",
                       f"{bn['name_ru']} разгребёт очередь за {bn['days_to_clear']} рабочих дня на полной "
                       f"мощности; все этапы после ждут."))
    why.append(fact(f"{len(plan['late'])} late and {len(plan['risk'])} at-risk pieces are open; "
                    f"{first} of them are in tomorrow's hand-out, first.",
                    f"Открыто {len(plan['late'])} опаздывающих и {len(plan['risk'])} под угрозой; "
                    f"{first} из них в выдаче на завтра, первыми."))
    why.append(calc("One order goes to one person while they have room; fairness counts pieces "
                    "already on hands, not just today's hand-out.",
                    "Один заказ - одному человеку, пока есть место; ровность считается по штукам "
                    "на руках, а не только по сегодняшней выдаче."))
    stages = [{k: s[k] for k in ("stage_id", "code", "name_en", "name_ru", "capacity", "queue",
                                "on_hands", "left_after", "days_to_clear", "bottleneck", "idle")}
              | {"workers": s["workers"], "planned": len(s["lines"])} for s in plan["stages"]]
    first_en = f"{first} late-risk first" if first else "late ones are already on hands"
    first_ru = f"{first} опаздывающих первыми" if first else "опаздывающие уже на руках"
    return {
        "kind": "plan", "dkey": f"plan:{target}", "target_date": target,
        "headline": tt(f"Plan for {_wd(target, 'en')}: hand out {len(lines)} pcs, {first_en}{bn_en}.",
                       f"План на {_wd(target, 'ru')}: выдать {len(lines)} шт, {first_ru}{bn_ru}."),
        "numbers": [{"label": tt("to hand out", "выдать"), "value": len(lines), "tag": "CALC"},
                    {"label": tt("on hands", "на руках"), "value": on_hands, "tag": "FACT"},
                    {"label": tt("open pcs", "открыто шт"), "value": plan["open"], "tag": "FACT"},
                    {"label": tt("bottleneck", "узкое место"),
                     "value": tt(f"{bn['name_en']} {bn['days_to_clear']} d", f"{bn['name_ru']} {bn['days_to_clear']} дн")
                     if bn else tt("none", "нет"), "tag": "ESTIMATE"}],
        "lines": lines, "stages": stages, "why": why,
        "apply_label": tt("Yes, hand out", "Да, выдать"),
        "fingerprint": _fp([(ln["unit_id"], ln["person_id"]) for ln in lines]),
    }


def _shortage_cards(conn) -> list[dict]:
    out = []
    for s in materials.shortages(conn):
        u = s["unit"]
        top = ", ".join(f"#{n}" for n, _ in s["orders"][:4])
        opts = sorted({max(1, round(s["short"] + 0.49)),
                       max(1, round(s["short"] + s["reorder_point"] + 0.49)),
                       int(-(-max(s["short"], 1) // 10) * 10)})
        stop_en = " It stops the next marks." if s["stops_next"] else ""
        stop_ru = " Встанут ближайшие отметки." if s["stops_next"] else ""
        why = [fact(f"On hand {s['on_hand']:g} {u} (receipts minus what the marked work used).",
                    f"На складе {s['on_hand']:g} {u} (приходы минус расход по отметкам)."),
               fact(f"Open work needs {s['need']:g} {u}: {len(s['orders'])} orders ({top}).",
                    f"Открытая работа требует {s['need']:g} {u}: заказов {len(s['orders'])} ({top})."),
               calc(f"Short = need minus on hand = {s['short']:g} {u}.{stop_en}",
                    f"Не хватает = потребность минус остаток = {s['short']:g} {u}.{stop_ru}"),
               fact(f"{s['need_next']:g} {u} of the need is already waiting at the consuming stage.",
                    f"{s['need_next']:g} {u} потребности уже ждут на этапе расхода."),
               est(f"Lead time {s['lead_days']} days from {s['supplier'] or 'the supplier'}; "
                   f"with the reorder point {s['reorder_point']:g} {u} the order would be {opts[1]} {u}.",
                   f"Срок поставки {s['lead_days']} дн от {s['supplier'] or 'поставщика'}; с точкой заказа "
                   f"{s['reorder_point']:g} {u} заказ был бы {opts[1]} {u}.")]
        out.append({
            "kind": "shortage", "dkey": f"shortage:{s['material_id']}", "target_date": domain.today(conn),
            "material_id": s["material_id"], "material": s["name"], "unit": u,
            "headline": tt(f"Not enough {s['name']}: {len(s['orders'])} orders need {s['need']:g} {u}, "
                           f"{s['on_hand']:g} {u} on hand.{stop_en}",
                           f"Не хватит {s['name']}: {len(s['orders'])} заказов требуют {s['need']:g} {u}, "
                           f"есть {s['on_hand']:g} {u}.{stop_ru}"),
            "numbers": [{"label": tt("need", "нужно"), "value": f"{s['need']:g} {u}", "tag": "FACT"},
                        {"label": tt("on hand", "есть"), "value": f"{s['on_hand']:g} {u}", "tag": "FACT"},
                        {"label": tt("short", "не хватает"), "value": f"{s['short']:g} {u}", "tag": "CALC"},
                        {"label": tt("lead time", "поставка"), "value": f"{s['lead_days']} d", "tag": "ESTIMATE"}],
            "lines": [{"order": n, "qty": q} for n, q in s["orders"]],
            "options": [{"id": q, "label": tt(f"Order {q} {u}", f"Заказать {q} {u}")} for q in opts],
            "why": why, "apply_label": tt(f"Order {opts[0]} {u}", f"Заказать {opts[0]} {u}"),
            "fingerprint": _fp([s["short"], s["on_hand"]]),
        })
    return out


def _late_card(conn, plan: dict) -> dict | None:
    units = plan["late"] + plan["risk"]
    if not units:
        return None
    today = date.fromisoformat(plan["today"])
    orders: dict[int, dict] = {}
    for u in units:
        o = orders.setdefault(u.number, {"number": u.number, "order_id": u.order_id, "product": u.product,
                                         "customer": u.customer, "due": u.due, "risk": u.risk,
                                         "late_days": u.late_days, "stages_left": u.stages_left,
                                         "days_at_stage": u.days_at_stage, "units": 0})
        o["units"] += 1
        o["stages_left"] = max(o["stages_left"], u.stages_left)
    lines = sorted(orders.values(), key=lambda o: (o["risk"] != planner.RISK_LATE, -o["late_days"], o["due"] or ""))
    for o in lines:  # ESTIMATE: if put first, one stage per working day from tomorrow
        d = today
        for _ in range(o["stages_left"]):
            d = planner.next_workday(conn, d)
        o["estimate"] = d.isoformat()
    late = [o for o in lines if o["risk"] == planner.RISK_LATE]
    risk = [o for o in lines if o["risk"] == planner.RISK_AT_RISK]
    with_customer = [o for o in lines if o["customer"]]
    n_cust = len({o["customer"] for o in with_customer})
    why = [fact(f"{len(late)} orders are past their date; {len(risk)} have fewer working days left than stages.",
                f"{len(late)} заказов уже за сроком; у {len(risk)} рабочих дней меньше, чем этапов."),
           est("New date = one stage per working day from tomorrow, if the order goes first at each stage.",
               "Новая дата = этап в рабочий день начиная с завтра, если заказ идёт первым на каждом этапе."),
           calc(f"{len(with_customer)} of them have a customer to tell; stock orders are only reordered.",
                f"У {len(with_customer)} есть клиент, которому сказать; складские просто переставляются.")]
    bn = next((s for s in plan["stages"] if s["bottleneck"]), None)
    if bn:
        why.append(fact(f"Most of the wait is at {bn['name_en']}: {bn['queue']} pcs, {bn['days_to_clear']} days of work.",
                        f"Основное ожидание на этапе {bn['name_ru']}: {bn['queue']} шт, {bn['days_to_clear']} дня работы."))
    return {
        "kind": "late_risk", "dkey": f"late:{plan['today']}", "target_date": plan["today"],
        "headline": tt(f"{len(late)} orders late, {len(risk)} more at risk: put them first and tell "
                       f"{n_cust} customers the new dates.",
                       f"Опаздывают {len(late)} заказов, ещё {len(risk)} под угрозой: поставить первыми и "
                       f"назвать {n_cust} клиентам новые сроки."),
        "numbers": [{"label": tt("late", "опаздывают"), "value": len(late), "tag": "FACT"},
                    {"label": tt("at risk", "под угрозой"), "value": len(risk), "tag": "ESTIMATE"},
                    {"label": tt("customers", "клиентов"), "value": n_cust, "tag": "FACT"},
                    {"label": tt("worst", "худший"), "value": f"{late[0]['late_days']} d" if late else "-", "tag": "FACT"}],
        "lines": lines, "why": why,
        "apply_label": tt("Yes, draft the notes", "Да, подготовить письма"),
        "fingerprint": _fp([(o["number"], o["risk"]) for o in lines]),
    }


def _idle_card(conn, plan: dict) -> dict | None:
    first = plan["stages"][0]
    if not first["idle"]:
        return None
    today = date.fromisoformat(plan["today"])
    cands = planner.stock_candidates(conn, today, first["idle"])
    if not cands:
        return None
    total = sum(c["qty"] for c in cands)
    why = [fact(f"{first['name_en']} has {first['queue']} waiting against {first['capacity']}/day: "
                f"{first['idle']} pcs of room tomorrow.",
                f"{first['name_ru']}: {first['queue']} в очереди при {first['capacity']}/день: "
                f"завтра свободно {first['idle']} шт."),
           calc("With piece rates, an idle stage is people without money; customer orders go first, "
                "stock fills the rest.",
                "При сдельщине простой этапа = люди без денег; заказные первыми, склад добирает остаток.")]
    for c in cands:
        why.append(fact(f"{c['product']}: {c['sold_30d']} shipped in 30 days ({c['per_day']}/day), "
                        f"{c['in_stock_or_work']} in stock or in work = "
                        f"{c['cover_days'] if c['cover_days'] is not None else '-'} days of cover.",
                        f"{c['product']}: отгружено {c['sold_30d']} за 30 дней ({c['per_day']}/день), "
                        f"на складе или в работе {c['in_stock_or_work']} = запас на "
                        f"{c['cover_days'] if c['cover_days'] is not None else '-'} дн."))
    why.append(est("Quantity = about a week of sales, within the free room.",
                   "Количество = примерно неделя продаж в пределах свободного места."))
    return {
        "kind": "idle_capacity", "dkey": f"idle:{plan['target']}", "target_date": plan["target"],
        "headline": tt(f"{first['name_en']} has room for {first['idle']} tomorrow: launch {total} pcs for stock "
                       f"({', '.join(c['product'] + ' x' + str(c['qty']) for c in cands)}).",
                       f"{first['name_ru']}: завтра свободно {first['idle']}: запустить на склад {total} шт "
                       f"({', '.join(c['product'] + ' x' + str(c['qty']) for c in cands)})."),
        "numbers": [{"label": tt("room", "свободно"), "value": first["idle"], "tag": "CALC"},
                    {"label": tt("launch", "запустить"), "value": total, "tag": "CALC"},
                    {"label": tt("queue", "очередь"), "value": first["queue"], "tag": "FACT"},
                    {"label": tt("capacity", "мощность"), "value": first["capacity"], "tag": "FACT"}],
        "lines": cands, "why": why,
        "apply_label": tt("Yes, launch for stock", "Да, запустить на склад"),
        "fingerprint": _fp([(c["product_id"], c["qty"]) for c in cands]),
    }


def build_cards(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    plan = planner.plan_tomorrow(conn, today)
    cards = [c for c in (_plan_card(conn, plan), _late_card(conn, plan), _idle_card(conn, plan)) if c]
    cards[1:1] = _shortage_cards(conn)
    return cards


# --- the feed: upsert by key ---------------------------------------------

def refresh(conn: sqlite3.Connection, person_id: int) -> None:
    """Recompute the cards; keep open ones that did not change, supersede the
    ones that did, close the ones whose reason is gone, expire yesterday's."""
    today = date.fromisoformat(domain.today(conn))
    now = domain.now(conn).isoformat(timespec="seconds")
    open_rows = conn.execute("SELECT id, payload FROM agent_actions WHERE status = 'proposed' "
                             "AND kind IN ('plan', 'shortage', 'late_risk', 'idle_capacity')").fetchall()
    open_by_key = {}
    for r in open_rows:
        p = json.loads(r["payload"])
        if p.get("target_date", "9999") < today.isoformat():
            conn.execute("UPDATE agent_actions SET status = 'rejected', decided_at = ?, result = ? WHERE id = ?",
                         (now, json.dumps({"expired": True}), r["id"]))
            continue
        open_by_key[p["dkey"]] = (r["id"], p)
    seen = set()
    for card in build_cards(conn, today):
        seen.add(card["dkey"])
        old = open_by_key.get(card["dkey"])
        if old and old[1].get("fingerprint") == card["fingerprint"]:
            continue
        if old:
            conn.execute("UPDATE agent_actions SET status = 'rejected', decided_at = ?, result = ? WHERE id = ?",
                         (now, json.dumps({"superseded": True}), old[0]))
        conn.execute("INSERT INTO agent_actions (kind, payload, model, created_at, for_person) "
                     "VALUES (?, ?, 'planner-rules', ?, ?)",
                     (card["kind"], json.dumps(card, default=str), now, person_id))
    for key, (aid, _) in open_by_key.items():
        if key not in seen:
            conn.execute("UPDATE agent_actions SET status = 'rejected', decided_at = ?, result = ? WHERE id = ?",
                         (now, json.dumps({"superseded": True, "reason": "resolved"}), aid))
    conn.commit()
    score_proposals(conn, today)


def _card_out(conn, row: sqlite3.Row) -> dict:
    p = json.loads(row["payload"])
    st = shadow_stats(conn, row["kind"])
    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"],
            "decided_at": row["decided_at"], "result": json.loads(row["result"]) if row["result"] else None,
            "shadow": not st["enabled"], "stats": st, "model": row["model"], **p}


def today_feed(conn: sqlite3.Connection, person_id: int) -> dict:
    refresh(conn, person_id)
    day = domain.today(conn)
    cards = [_card_out(conn, r) for r in conn.execute(
        "SELECT * FROM agent_actions WHERE status = 'proposed' AND kind IN ('plan', 'shortage', "
        "'late_risk', 'idle_capacity') ORDER BY id")]
    order = {k: i for i, k in enumerate(("plan", "shortage", "late_risk", "idle_capacity"))}
    cards.sort(key=lambda c: (order[c["kind"]], c["id"]))
    decided = [_card_out(conn, r) for r in conn.execute(
        "SELECT * FROM agent_actions WHERE status != 'proposed' AND kind IN ('plan', 'shortage', "
        "'late_risk', 'idle_capacity') AND substr(decided_at, 1, 10) = ? "
        "AND COALESCE(result, '') NOT LIKE '%\"superseded\"%' AND COALESCE(result, '') NOT LIKE '%\"expired\"%' "
        "ORDER BY decided_at DESC LIMIT 10", (day,))]
    return {"date": day, "summary": _summary(conn), "cards": cards, "decided": decided,
            "stats": [shadow_stats(conn, k) for k in KINDS], "currency": domain.currency(conn),
            "examples": askmod.EXAMPLES,
            "outbox": conn.execute("SELECT COUNT(*) FROM outbox WHERE status = 'draft'").fetchone()[0]}


def _summary(conn) -> dict:
    day = domain.today(conn)
    r = domain.orders_list(conn, view="all")
    open_orders = [o for o in r["orders"] if o["status"] in ("accepted", "in_work", "ready")]
    floor = domain.floor_overview(conn)
    bn = next((s for s in floor if s["bottleneck"]), None)
    today_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE substr(created_at, 1, 10) = ?", (day,)).fetchone()[0]
    return {"orders_today": today_orders, "open_orders": len(open_orders),
            "ready": sum(1 for o in open_orders if o["status"] == "ready"),
            "late": len(r["attention"]), "done_today": sum(s["done_today"] for s in floor),
            "capacity": sum(s["capacity"] for s in floor),
            "bottleneck": {"name_en": bn["name_en"], "name_ru": bn["name_ru"], "queue": bn["queue"],
                           "capacity": bn["capacity"]} if bn else None,
            "shortages": len(materials.shortages(conn))}


# --- decisions ----------------------------------------------------------------

class DecideIn(BaseModel):
    keep: list[int] | None = None        # plan: unit ids; late_risk: order numbers; idle: product ids
    moves: dict[str, int] = {}           # plan: unit id -> person id (must be an offered option)
    pick: int | None = None              # shortage: quantity option id
    reason: str = ""


def _load(conn, action_id: int) -> tuple[sqlite3.Row, dict]:
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None or row["kind"] not in KINDS:
        raise DomainError("No such card.")
    if row["status"] != "proposed":
        raise DomainError("This card is already decided.")
    return row, json.loads(row["payload"])


def apply(conn: sqlite3.Connection, person_id: int, action_id: int, body: DecideIn) -> dict:
    """Yes / Change: apply the stored proposal, or the subset of it the person kept."""
    row, p = _load(conn, action_id)
    st = shadow_stats(conn, row["kind"])
    if not st["enabled"]:
        raise DomainError(f"This advice is still learning ({st['scored']} scored, "
                          f"{round((st['rate'] or 0) * 100)}% matched the floor). Switch it on first.")
    now = domain.now(conn).isoformat(timespec="seconds")
    changed = body.keep is not None or bool(body.moves) or body.pick is not None
    result = {"applied": 0, "skipped": [], "changed": changed}
    if row["kind"] == "plan":
        keep = set(body.keep) if body.keep is not None else {ln["unit_id"] for ln in p["lines"]}
        waiting = domain._waiting_units(conn)
        taken = {(r["unit_id"], r["stage_id"]) for r in conn.execute("SELECT unit_id, stage_id FROM assignments")}
        for ln in p["lines"]:
            if ln["unit_id"] not in keep:
                continue
            pid = body.moves.get(str(ln["unit_id"]), ln["person_id"])
            if pid not in ln["options"]:
                raise DomainError(f"{ln['label']}: pick one of the offered people.")
            if ln["unit_id"] not in waiting[ln["stage_id"]]:
                result["skipped"].append(f"{ln['label']}: moved on already")
                continue
            if (ln["unit_id"], ln["stage_id"]) in taken:
                result["skipped"].append(f"{ln['label']}: already taken")
                continue
            conn.execute("INSERT INTO assignments (unit_id, stage_id, person_id) VALUES (?, ?, ?)",
                         (ln["unit_id"], ln["stage_id"], pid))
            result["applied"] += 1
        msg = f"Handed out {result['applied']} pcs for {_wd(p['target_date'], 'en')}."
    elif row["kind"] == "shortage":
        allowed = {o["id"] for o in p["options"]}
        qty = body.pick if body.pick is not None else p["options"][0]["id"]
        if qty not in allowed:
            raise DomainError("Pick one of the offered quantities.")
        sup = conn.execute("SELECT supplier FROM materials WHERE id = ?", (p["material_id"],)).fetchone()
        conn.execute("INSERT INTO outbox (kind, to_name, subject, body, created_at, created_by, action_id) "
                     "VALUES ('purchase', ?, ?, ?, ?, ?, ?)",
                     (sup["supplier"] if sup else "", f"Purchase request: {p['material']} {qty} {p['unit']}",
                      f"Please deliver {qty} {p['unit']} of {p['material']}. "
                      f"Needed for orders {', '.join('#' + str(ln['order']) for ln in p['lines'][:6])}.",
                      now, person_id, action_id))
        result["applied"] = 1
        result["qty"] = qty
        msg = f"Purchase request for {qty} {p['unit']} {p['material']} is in the outbox (dry run: sending is not configured)."
    elif row["kind"] == "late_risk":
        keep = set(body.keep) if body.keep is not None else {o["number"] for o in p["lines"]}
        for o in p["lines"]:
            if o["number"] not in keep or not o["customer"]:
                continue
            conn.execute("INSERT INTO outbox (kind, to_name, subject, body, created_at, created_by, action_id) "
                         "VALUES ('email', ?, ?, ?, ?, ?, ?)",
                         (o["customer"], f"Order #{o['number']}: new date {o['estimate']}",
                          f"Order #{o['number']} ({o['product']}) was due {o['due']}. It is put first on every "
                          f"stage; the new date is {o['estimate']} (estimate). Sorry for the delay.",
                          now, person_id, action_id))
            result["applied"] += 1
        msg = f"{result['applied']} note(s) to customers are in the outbox (dry run: sending is not configured)."
    else:  # idle_capacity
        keep = set(body.keep) if body.keep is not None else {c["product_id"] for c in p["lines"]}
        created = []
        for c in p["lines"]:
            if c["product_id"] not in keep:
                continue
            variant = conn.execute(
                "SELECT o.variant, COUNT(*) AS n FROM shipment_lines sl JOIN units u ON u.id = sl.unit_id "
                "JOIN orders o ON o.id = u.order_id WHERE o.product_id = ? GROUP BY o.variant "
                "ORDER BY n DESC LIMIT 1", (c["product_id"],)).fetchone()
            oid = domain.create_order(conn, product_id=c["product_id"], qty=c["qty"], customer_id=None,
                                      due_date=None, variant=variant["variant"] if variant else "",
                                      source="agent", commit=False)
            created.append(conn.execute("SELECT number FROM orders WHERE id = ?", (oid,)).fetchone()[0])
        result["applied"] = len(created)
        result["created"] = created
        msg = "Launched for stock: " + ", ".join(f"#{n}" for n in created) + "." if created else "Nothing launched."
    conn.execute("UPDATE agent_actions SET status = 'applied', decided_by = ?, decided_at = ?, result = ? WHERE id = ?",
                 (person_id, now, json.dumps(result), action_id))
    conn.commit()
    if result["skipped"]:
        msg += " Skipped: " + "; ".join(result["skipped"]) + "."
    return {"ok": True, "message": msg, **result}


def reject(conn: sqlite3.Connection, person_id: int, action_id: int, reason: str) -> dict:
    row, p = _load(conn, action_id)
    now = domain.now(conn).isoformat(timespec="seconds")
    conn.execute("UPDATE agent_actions SET status = 'rejected', decided_by = ?, decided_at = ?, result = ? WHERE id = ?",
                 (person_id, now, json.dumps({"reason": reason.strip()}), action_id))
    conn.commit()
    return {"ok": True, "message": "Recorded: no." + (f" Reason: {reason.strip()}" if reason.strip() else "")}


# --- "Why?" in words ---------------------------------------------------------

EXPLAIN = ("Explain this shop-floor decision card to the workshop owner in {lang}, in one short "
           "paragraph (3-4 sentences), plain words, no new numbers: use only the figures below "
           "and keep their FACT / CALC / ESTIMATE meaning. Headline: {headline}\nLines:\n{lines}")


def explain(conn: sqlite3.Connection, action_id: int, lang: str) -> dict:
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise DomainError("No such card.")
    p = json.loads(row["payload"])
    key = f"explained_{lang}"
    if p.get(key):
        return {"text": p[key], "lines": p["why"], "model": p.get("explained_model", "")}
    text, model = "", "offline-rules"
    try:
        prompt = EXPLAIN.format(lang="Russian" if lang == "ru" else "English", headline=p["headline"][lang],
                                lines="\n".join(f"[{ln['tag']}] {ln[lang]}" for ln in p["why"]))
        from ..intake import _output_text
        data = models.respond(conn, "explain", prompt, max_output_tokens=400)
        text, model = _output_text(data).strip(), models.SPARK
    except BudgetStop:
        pass
    if not text:  # offline: the lines already are the explanation
        text = {"en": "Facts are measured from the records, calculations follow from them, estimates assume "
                      "one stage per working day. Details line by line:",
                "ru": "Факты измерены по записям, расчёты следуют из них, оценки считают этап в рабочий день. "
                      "Подробно по строкам:"}[lang]
    p[key], p["explained_model"] = text, model
    conn.execute("UPDATE agent_actions SET payload = ? WHERE id = ?", (json.dumps(p, default=str), action_id))
    conn.commit()
    return {"text": text, "lines": p["why"], "model": model}


# --- morning summary: one message per role ----------------------------------

def morning(conn: sqlite3.Connection, person_id: int, lang: str, tablet: bool = False) -> dict:
    who = domain.person(conn, person_id)
    day = domain.today(conn)
    cur = domain.currency(conn)
    shop = conn.execute("SELECT value FROM settings WHERE key = 'shop_name'").fetchone()
    head = f"{_wd(day, lang)} · {shop['value'] if shop else ''}"
    L = []
    if who["role"] == "owner":
        s = _summary(conn)
        floor = domain.floor_overview(conn)
        yday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        done_y = {r["stage_id"]: r["n"] for r in conn.execute(
            f"SELECT stage_id, COUNT(*) AS n FROM ({LIVE_DONE}) WHERE work_date = ? GROUP BY stage_id", (yday,))}
        money = askmod.t_income(conn, day[:7])
        pay = askmod.t_payroll_due(conn)
        bn = s["bottleneck"]
        waiting = conn.execute("SELECT COUNT(*) FROM agent_actions WHERE status = 'proposed' AND kind IN "
                               "('plan', 'shortage', 'late_risk', 'idle_capacity')").fetchone()[0]
        short = materials.shortages(conn)
        L = [tt(f"Open orders {s['open_orders']} · ready to ship {s['ready']} · late {s['late']} · new today {s['orders_today']}",
                f"Открытых заказов {s['open_orders']} · готово к отгрузке {s['ready']} · опаздывают {s['late']} · новых сегодня {s['orders_today']}"),
             tt("Yesterday: " + " · ".join(f"{st['name_en']} {done_y.get(st['id'], 0)}/{st['capacity']}" for st in floor),
                "Вчера: " + " · ".join(f"{st['name_ru']} {done_y.get(st['id'], 0)}/{st['capacity']}" for st in floor)),
             tt(f"Bottleneck: {bn['name_en']} {bn['queue']} waiting at {bn['capacity']}/day" if bn else "Bottleneck: none",
                f"Узкое место: {bn['name_ru']} {bn['queue']} в очереди при {bn['capacity']}/день" if bn else "Узкого места нет"),
             tt("Short: " + (", ".join(f"{x['name']} {x['short']:g} {x['unit']}" for x in short) or "nothing"),
                "Не хватает: " + (", ".join(f"{x['name']} {x['short']:g} {x['unit']}" for x in short) or "ничего")),
             tt(f"Money {day[:7]}: invoiced {cur}{money['invoiced']} · paid {cur}{money['paid']} · owed to us "
                f"{cur}{money['owed']} · to pay workers {cur}{pay['total']}",
                f"Деньги {day[:7]}: выставлено {cur}{money['invoiced']} · оплачено {cur}{money['paid']} · должны нам "
                f"{cur}{money['owed']} · к выплате рабочим {cur}{pay['total']}"),
             tt(f"Decisions waiting: {waiting}", f"Решений ждёт: {waiting}")]
    elif who["role"] == "manager":
        board = domain.shipping_board(conn)
        r = domain.orders_list(conn)
        ready_pcs = sum(c["ready"] for c in board["ready"])
        check = conn.execute("SELECT COUNT(*) FROM agent_actions WHERE kind = 'intake' AND status = 'proposed'").fetchone()[0]
        short = materials.shortages(conn)
        top = ", ".join(f"#{o['number']} {o['customer'] or 'stock'} {o['late_days']} d" for o in r["attention"][:3])
        top_ru = ", ".join(f"#{o['number']} {o['customer'] or 'склад'} {o['late_days']} дн" for o in r["attention"][:3])
        L = [tt(f"To ship: {len(board['ready'])} customers have {ready_pcs} pcs ready" +
                (": " + ", ".join(c["name"] for c in board["ready"][:4]) if board["ready"] else ""),
                f"К отгрузке: у {len(board['ready'])} клиентов готово {ready_pcs} шт" +
                (": " + ", ".join(c["name"] for c in board["ready"][:4]) if board["ready"] else "")),
             tt(f"Late: {len(r['attention'])} orders" + (f" ({top})" if top else ""),
                f"Опаздывают: {len(r['attention'])} заказов" + (f" ({top_ru})" if top_ru else "")),
             tt(f"Owed to us: {cur}{board['owed_total']} by {len(board['debts'])} customers",
                f"Должны нам: {cur}{board['owed_total']}, клиентов {len(board['debts'])}"),
             tt("Short: " + (", ".join(f"{x['name']} {x['short']:g} {x['unit']}" for x in short) or "nothing"),
                "Не хватает: " + (", ".join(f"{x['name']} {x['short']:g} {x['unit']}" for x in short) or "ничего")),
             tt(f"Intake to check: {check}", f"Разбор на проверку: {check}")]
    else:
        home = domain.worker_home(conn, person_id, show_money=not tablet)
        yday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        y = conn.execute(f"SELECT unit_id, stage_id FROM ({LIVE_DONE}) WHERE person_id = ? AND work_date = ?",
                         (person_id, yday)).fetchall()
        y_money = sum(domain.rate(conn, e["unit_id"], e["stage_id"]) or 0 for e in y)
        mine = sum(c["waiting"] for c in home["mine"])
        first = ", ".join(f"#{c['number']} {c['product']}" for c in home["mine"][:3])
        week = domain.earnings(conn, person_id, 7)
        money_en = f" · {cur}{y_money}" if not tablet else ""
        L = [tt(f"On your hands: {mine} pcs" + (f" ({first})" if first else "") + f" · free at your stage: {sum(c['waiting'] for c in home['free'])}",
                f"На руках: {mine} шт" + (f" ({first})" if first else "") + f" · свободно на этапе: {sum(c['waiting'] for c in home['free'])}"),
             tt(f"Yesterday: {len(y)} pcs{money_en}", f"Вчера: {len(y)} шт{money_en}")]
        if not tablet:
            L.append(tt(f"Last 7 days: {cur}{sum(d['money'] for d in week['days'])} · to be paid {cur}{week['to_be_paid']}",
                        f"За 7 дней: {cur}{sum(d['money'] for d in week['days'])} · к выплате {cur}{week['to_be_paid']}"))
    return {"role": who["role"], "name": who["name"], "head": head, "lines": [ln[lang] for ln in L],
            "text": head + "\n" + "\n".join(ln[lang] for ln in L)}


# --- routes --------------------------------------------------------------------

def owner_only(s: Session = Depends(session)) -> Session:
    if s.role != "owner":
        raise HTTPException(403, "This screen is for the owner.")
    return s


@router.get("/api/owner/today")
def owner_today(s: Session = Depends(owner_only), conn=Depends(get_conn)) -> dict:
    return today_feed(conn, s.person_id)


@router.post("/api/owner/cards/{action_id}/apply")
def owner_apply(action_id: int, body: DecideIn, s: Session = Depends(owner_only), conn=Depends(get_conn)) -> dict:
    return apply(conn, s.person_id, action_id, body)


@router.post("/api/owner/cards/{action_id}/reject")
def owner_reject(action_id: int, body: DecideIn, s: Session = Depends(owner_only), conn=Depends(get_conn)) -> dict:
    return reject(conn, s.person_id, action_id, body.reason)


@router.get("/api/owner/cards/{action_id}/why")
def owner_why(action_id: int, lang: str = "en", s: Session = Depends(owner_only), conn=Depends(get_conn)) -> dict:
    return explain(conn, action_id, "ru" if lang == "ru" else "en")


@router.post("/api/owner/advice/{kind}/enable")
def owner_enable(kind: str, s: Session = Depends(owner_only), conn=Depends(get_conn)) -> dict:
    return enable_kind(conn, kind)


class AskIn(BaseModel):
    text: str
    lang: str = "en"


@router.post("/api/owner/ask")
def owner_ask(body: AskIn, s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    return askmod.ask(conn, s.role, body.text, "ru" if body.lang == "ru" else "en")


@router.get("/api/owner/morning")
def owner_morning(lang: str = "en", s: Session = Depends(session), conn=Depends(get_conn)) -> dict:
    return morning(conn, s.person_id, "ru" if lang == "ru" else "en", s.tablet)


@router.get("/api/owner/outbox")
def owner_outbox(s: Session = Depends(office), conn=Depends(get_conn)) -> dict:
    rows = conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 50").fetchall()
    return {"rows": [dict(r) for r in rows]}


# --- demo data: a history of proposals so the shadow has something to show ----

def _seed_old_credit(conn, rnd, today: date) -> None:
    """Two orders from seven weeks ago, made, shipped on credit and still unpaid -
    so 'who owes us for more than 30 days?' has an answer. Numbers below 100 keep
    them out of the way of the live order numbers."""
    st = domain.stages(conn)
    manager = conn.execute("SELECT id FROM people WHERE role = 'manager'").fetchone()[0]
    customers = [r["id"] for r in conn.execute("SELECT id FROM customers ORDER BY id LIMIT 2")]
    products = [r["id"] for r in conn.execute("SELECT id FROM products WHERE kind IN ('sofa', 'armchair') "
                                              "ORDER BY price DESC LIMIT 2")]
    for i, (cust, prod) in enumerate(zip(customers, products)):
        created = today - timedelta(days=52 - i)
        oid = conn.execute("INSERT INTO orders (number, product_id, variant, qty, customer_id, due_date, source, "
                           "created_at) VALUES (?, ?, 'linen sand · oak', 2, ?, ?, 'manual', ?)",
                           (91 + i, prod, cust, (created + timedelta(days=8)).isoformat(),
                            created.isoformat() + "T10:00:00")).lastrowid
        for seq in (1, 2):
            uid = conn.execute("INSERT INTO units (order_id, seq) VALUES (?, ?)", (oid, seq)).lastrowid
            for k, s in enumerate(st):
                who = conn.execute("SELECT id FROM people WHERE stage_id = ? LIMIT 1", (s["id"],)).fetchone()[0]
                d = created + timedelta(days=1 + k)
                conn.execute("INSERT INTO work_events (kind, unit_id, stage_id, person_id, work_date, created_at, "
                             "method) VALUES ('done', ?, ?, ?, ?, ?, 'scan')",
                             (uid, s["id"], who, d.isoformat(), d.isoformat() + "T14:00:00"))
        ship_day = created + timedelta(days=7)
        sid = conn.execute("INSERT INTO shipments (customer_id, payment, paid_amount, created_at, created_by) "
                           "VALUES (?, 'credit', 0, ?, ?)", (cust, ship_day.isoformat() + "T11:00:00", manager)).lastrowid
        conn.execute("INSERT INTO shipment_lines (shipment_id, unit_id, price) "
                     "SELECT ?, u.id, p.price FROM units u JOIN orders o ON o.id = u.order_id "
                     "JOIN products p ON p.id = o.product_id WHERE u.order_id = ?", (sid, oid))
        # The fabric for them was bought back then: a receipt that matches the write-off.
        fabric = conn.execute("SELECT b.qty, m.id AS material_id FROM bom b JOIN materials m "
                              "ON m.name = 'linen sand' WHERE b.product_id = ? AND b.by_variant = 1",
                              (prod,)).fetchone()
        if fabric:
            conn.execute("INSERT INTO stock_moves (material_id, kind, qty, at, by_person, note) "
                         "VALUES (?, 'receipt', ?, ?, NULL, 'delivery Loomhouse Textiles')",
                         (fabric["material_id"], fabric["qty"] * 2,
                          (created - timedelta(days=2)).isoformat() + "T09:00:00"))


def seed(conn: sqlite3.Connection, rnd) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('advice_enabled', ?)",
                 (json.dumps(ENABLED_DEFAULT),))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('workdays', '12345')")
    today = date.fromisoformat(domain.today(conn))
    owner = conn.execute("SELECT id FROM people WHERE role = 'owner'").fetchone()[0]
    _seed_old_credit(conn, rnd, today)
    days = sorted({r["work_date"] for r in conn.execute(f"SELECT DISTINCT work_date FROM ({LIVE_DONE})")
                   if r["work_date"] < today.isoformat()})[-8:]
    all_units = [r["unit_id"] for r in conn.execute(f"SELECT DISTINCT unit_id FROM ({LIVE_DONE})")]
    for i, d in enumerate(days):
        # The plan "for that day": most of what was really done, plus some noise -
        # a shadow plan is scored by the real evaluator against the real events.
        done = conn.execute(f"SELECT e.unit_id, e.stage_id, e.person_id FROM ({LIVE_DONE}) e "
                            "WHERE e.work_date = ? ORDER BY e.id", (d,)).fetchall()
        lines = [{"unit_id": r["unit_id"], "stage_id": r["stage_id"], "person_id": r["person_id"],
                  "label": domain.unit_label(conn, r["unit_id"]), "options": [r["person_id"]]}
                 for r in done if rnd.random() < 0.8]
        noise = rnd.sample(all_units, k=max(1, len(lines) // 5))
        first_stage = conn.execute("SELECT id FROM stages ORDER BY seq LIMIT 1").fetchone()[0]
        lines += [{"unit_id": u, "stage_id": first_stage, "person_id": 0,
                   "label": domain.unit_label(conn, u), "options": [0]} for u in noise]
        payload = {"kind": "plan", "dkey": f"plan:{d}", "target_date": d, "lines": lines, "why": [],
                   "headline": tt(f"Plan for {_wd(d, 'en')}: hand out {len(lines)} pcs.",
                                  f"План на {_wd(d, 'ru')}: выдать {len(lines)} шт."),
                   "numbers": [], "fingerprint": _fp(d)}
        status = "rejected" if i in (2, 5) else "applied"
        result = {"reason": "manager changed the list by hand"} if status == "rejected" else {"applied": len(lines)}
        conn.execute("INSERT INTO agent_actions (kind, payload, model, status, created_at, for_person, "
                     "decided_by, decided_at, result) VALUES ('plan', ?, 'planner-rules', ?, ?, ?, ?, ?, ?)",
                     (json.dumps(payload), status, d + "T06:30:00", owner, owner, d + "T07:10:00",
                      json.dumps(result)))
    # Four shadow "launch for stock" suggestions: three matched a stock order that
    # was really created soon after, one did not.
    stock_orders = conn.execute("SELECT product_id, substr(created_at, 1, 10) AS d, p.name FROM orders o "
                                "JOIN products p ON p.id = o.product_id WHERE o.customer_id IS NULL "
                                "ORDER BY o.created_at").fetchall()
    picks = [r for r in stock_orders if r["d"] < today.isoformat()][-3:]
    miss = conn.execute("SELECT id, name FROM products WHERE id NOT IN (SELECT product_id FROM orders "
                        "WHERE customer_id IS NULL) LIMIT 1").fetchone()
    for r in picks:
        d = (date.fromisoformat(r["d"]) - timedelta(days=1)).isoformat()
        payload = {"kind": "idle_capacity", "dkey": f"idle:{d}", "target_date": d, "why": [], "numbers": [],
                   "lines": [{"product_id": r["product_id"], "product": r["name"], "qty": 2}],
                   "headline": tt(f"Room at Cutting: launch {r['name']} x2 for stock.",
                                  f"Свободно на раскрое: запустить {r['name']} x2 на склад."),
                   "fingerprint": _fp(d)}
        conn.execute("INSERT INTO agent_actions (kind, payload, model, status, created_at, for_person, result) "
                     "VALUES ('idle_capacity', ?, 'planner-rules', 'rejected', ?, ?, ?)",
                     (json.dumps(payload), d + "T06:30:00", owner, json.dumps({"shadow": True, "expired": True})))
    if miss:
        d = (today - timedelta(days=6)).isoformat()
        payload = {"kind": "idle_capacity", "dkey": f"idle:{d}", "target_date": d, "why": [], "numbers": [],
                   "lines": [{"product_id": miss["id"], "product": miss["name"], "qty": 2}],
                   "headline": tt(f"Room at Cutting: launch {miss['name']} x2 for stock.",
                                  f"Свободно на раскрое: запустить {miss['name']} x2 на склад."),
                   "fingerprint": _fp(d)}
        conn.execute("INSERT INTO agent_actions (kind, payload, model, status, created_at, for_person, result) "
                     "VALUES ('idle_capacity', ?, 'planner-rules', 'rejected', ?, ?, ?)",
                     (json.dumps(payload), d + "T06:30:00", owner, json.dumps({"shadow": True, "expired": True})))
    score_proposals(conn, today)
