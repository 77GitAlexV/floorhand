"""Capacity shocks and stored responses. Models parse words; the queue does the math."""
import hashlib
import html
import json
import math
import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import domain, models
from ..domain import DomainError
from ..web import Session, get_conn, session
from . import materials, planner
from .ask import tt, fact, calc, est

SCHEMA = """
CREATE TABLE IF NOT EXISTS ripple_adjustments (
    action_id INTEGER PRIMARY KEY REFERENCES agent_actions(id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ripple_requests (
    person_id INTEGER NOT NULL REFERENCES people(id),
    kind TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    action_id INTEGER NOT NULL REFERENCES agent_actions(id),
    PRIMARY KEY(person_id, kind, request_key)
);
-- Explicit demo/office estimates, never silently substituted for unknown costs.
CREATE TABLE IF NOT EXISTS ripple_material_costs (
    material_id INTEGER PRIMARY KEY REFERENCES materials(id),
    unit_cost REAL NOT NULL CHECK(unit_cost >= 0)
);
"""
router = APIRouter()
KINDS = ("ripple", "rush")
HORIZON = 180


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def snapshot(conn):
    """Invalidate a decision when any input to its promises changes."""
    tables = ("settings", "stages", "people", "products", "rates", "orders", "units",
              "work_events", "assignments", "shipment_lines", "payout_lines", "bom",
              "materials", "stock_moves", "product_requirements", "ripple_material_costs", "ripple_adjustments")
    return digest({t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                   for t in tables})


def finance(s: Session = Depends(session)):
    if s.role not in ("owner", "manager") or s.tablet:
        raise HTTPException(403, "Use an owner/manager private screen. / Откройте личный экран владельца/менеджера.")
    return s


def adjustments(conn):
    return [json.loads(r[0]) for r in conn.execute("SELECT payload FROM ripple_adjustments ORDER BY action_id")]


def planning_context(conn, day, changes=None):
    """Capacity stays within its trade; absence never borrows another trade's people."""
    caps = {s["id"]: max(0, s["capacity"]) for s in domain.stages(conn)}
    absent, priorities = set(), []
    losses, extras = {}, {}
    for change in adjustments(conn) if changes is None else changes:
        shock = change.get("shock", {})
        if shock and shock["start"] <= day < shock["until"]:
            sid = shock["stage_id"]
            losses[sid] = max(losses.get(sid, 0), shock.get("lost_capacity", 0))
            if shock.get("person_id"):
                absent.add(shock["person_id"])
        for extra in change.get("overtime", []):
            if extra["date"] == day:
                extras[extra["stage_id"]] = extras.get(extra["stage_id"], 0) + extra["capacity"]
        if change.get("priority_until", "") >= day:
            priorities.extend(change.get("priority_orders", []))
    # Distinct absences add; a machine shutdown still takes precedence.
    for sid, cap in caps.items():
        crew = [r[0] for r in conn.execute("SELECT id FROM people WHERE role='worker' AND stage_id=?", (sid,))]
        loss = math.ceil(cap * len(absent.intersection(crew)) / len(crew)) if crew else cap
        caps[sid] = max(0, cap - max(loss, losses.get(sid, 0))) + extras.get(sid, 0)
    return caps, absent, priorities


def material_ready(conn, units, day):
    """Hand-out consumes no stock, but cannot promise pieces beyond delayed material on hand."""
    shocks = [c["shock"] for c in adjustments(conn)
              if c.get("shock", {}).get("material_id") and c["shock"]["start"] <= day < c["shock"]["until"]]
    mids = {s["material_id"] for s in shocks}
    if not mids:
        return {u.id for u in units}
    have, bom, fabrics = materials.on_hand(conn), materials._bom_lines(conn), materials._fabrics(conn)
    ready = set()
    # Already handed-out units reserve the material first, even if another order gets priority.
    for u in sorted(units, key=lambda u: u.assigned_to is None):
        need = demand_at_stage(materials._unit_material_needs(conn, u.product_id, u.variant, bom, fabrics), u.stage_id, mids)
        if any(have.get(mid, 0) < qty for mid, qty in need):
            continue
        ready.add(u.id)
        for mid, qty in need:
            have[mid] = have.get(mid, 0) - qty
    return ready


def demand_at_stage(needs, stage_id, material_ids):
    """Two BOM lines for one material draw on one stock balance."""
    grouped = {}
    for mid, sid, qty in needs:
        if sid == stage_id and mid in material_ids:
            grouped[mid] = grouped.get(mid, 0) + qty
    return list(grouped.items())


def simulate(conn, changes=None, extra_units=None):
    """One stage per workday, finite horizon. Unknown ETA stays unknown, never a fake date."""
    today = date.fromisoformat(domain.today(conn))
    stages = domain.stages(conn)
    changes = adjustments(conn) if changes is None else changes
    units = planner.open_units(conn, today) + (extra_units or [])
    progress = {u.id: u.stage_idx for u in units}
    done = {(r["unit_id"], r["stage_id"]) for r in conn.execute(f"SELECT unit_id, stage_id FROM ({domain.LIVE_DONE})")}
    completion, tasks, gaps = {}, [], []
    bom, fabrics = materials._bom_lines(conn), materials._fabrics(conn)
    needs = {u.id: materials._unit_material_needs(conn, u.product_id, u.variant, bom, fabrics) for u in units}
    shocks = [c["shock"] for c in changes if c.get("shock", {}).get("material_id")]
    stock = materials.on_hand(conn)
    blocked_materials = {s["material_id"] for s in shocks}
    day = today
    for _ in range(HORIZON):
        day += timedelta(days=1)
        ds = day.isoformat()
        if day.isoweekday() not in planner.workdays(conn):
            continue
        caps, _, priorities = planning_context(conn, ds, changes)
        rank = lambda u: (u.order_id not in priorities, planner.priority(u))
        # Reverse order prevents newly completed units advancing again on the same day.
        for idx in reversed(range(len(stages))):
            stage = stages[idx]
            sid, cap = stage["id"], caps[stage["id"]]
            queue = sorted((u for u in units if progress[u.id] == idx), key=rank)
            selected = []
            for u in queue:
                if len(selected) >= cap:
                    break
                required = demand_at_stage(needs[u.id], sid, blocked_materials)
                def unavailable(mid, qty):
                    until = max(s["until"] for s in shocks if s["material_id"] == mid)
                    return ds < until and stock.get(mid, 0) < qty
                if any(unavailable(mid, qty) for mid, qty in required):
                    continue
                selected.append(u)
                for mid, qty in required:
                    stock[mid] = stock.get(mid, 0) - qty
            # An exact clock is an estimate from an even eight-hour workday, not telemetry.
            upstream = any(progress[u.id] < idx for u in units)
            if cap and len(selected) < cap and (queue or upstream):
                at = datetime.combine(day, time(8), domain.shop_tz(conn)) + timedelta(minutes=480 * len(selected) / cap)
                gaps.append({"stage_id": sid, "stage": tt(stage["name_en"], stage["name_ru"]),
                             "at": at.isoformat(timespec="minutes"), "ready": len(selected), "capacity": cap,
                             "tag": "ESTIMATE"})
            for u in selected:
                tasks.append({"unit_id": u.id, "product_id": u.product_id, "stage_id": sid, "date": ds,
                              "order_id": u.order_id})
                progress[u.id] += 1
                while progress[u.id] < len(stages) and (u.id, stages[progress[u.id]]["id"]) in done:
                    progress[u.id] += 1
                if progress[u.id] == len(stages):
                    completion[u.id] = ds
        if len(completion) == len(units):
            break
    orders = {}
    for u in units:
        o = orders.setdefault(u.order_id, {"order_id": u.order_id, "number": u.number,
                                           "customer": u.customer, "due": u.due, "dates": []})
        o["dates"].append(completion.get(u.id))
    for o in orders.values():
        dates = o.pop("dates")
        o["eta"] = max(dates) if all(dates) else None
    return {"orders": orders, "tasks": tasks, "gaps": gaps, "units": units}


def movements(before, after):
    out = []
    for oid, old in before["orders"].items():
        new = after["orders"].get(oid)
        if not new or old["eta"] == new["eta"]:
            continue
        days = (date.fromisoformat(new["eta"]) - date.fromisoformat(old["eta"])).days if old["eta"] and new["eta"] else None
        out.append({**new, "old_eta": old["eta"], "new_eta": new["eta"], "days": days,
                    "notify": bool(new["customer"] and (days is None or days > 0)), "tag": "ESTIMATE"})
    return out


def late_count(plan):
    return len(late_orders(plan))


def late_orders(plan):
    """Compare order identities: an existing backlog is not caused by a new shock."""
    return {oid for oid, o in plan["orders"].items()
            if o["due"] and (o["eta"] is None or o["eta"] > o["due"])}


def stage_movements(before, after):
    """A downstream buffer can absorb a delayed start without moving the final ETA."""
    def dates(plan):
        grouped = {}
        for task in plan["tasks"]:
            key = (task["order_id"], task["stage_id"])
            grouped[key] = max(grouped.get(key, ""), task["date"])
        return grouped
    old, new = dates(before), dates(after)
    return [{"order_id": oid, "number": before["orders"][oid]["number"], "stage_id": sid,
             "old_date": at, "new_date": new.get((oid, sid)), "tag": "ESTIMATE"}
            for (oid, sid), at in old.items() if new.get((oid, sid)) != at]


def parse(conn, text, use_model=True):
    """Resolve short names locally; Spark can only select existing ids and a bounded duration."""
    stage_rows = [dict(s) for s in domain.stages(conn)]
    people = [dict(r) for r in conn.execute("SELECT id,name,stage_id FROM people WHERE role='worker'")]
    mats = [dict(r) for r in conn.execute("SELECT id,name,kind FROM materials")]
    candidate, model = None, "offline-rules"
    try:
        if not use_model:
            raise models.BudgetStop("Seed uses rules")
        from ..intake import _output_text
        result = models.respond(conn, "ripple-parse", "Parse a disruption. Return JSON only: kind (machine/absence/material), "
                                "stage_id, person_id or material_id when relevant, days (calendar days, 1..30). "
                                "Use only supplied ids. If ambiguous return {}. No advice or SQL.\n" +
                                json.dumps({"text": text, "stages": stage_rows, "people": people, "materials": mats}, ensure_ascii=False),
                                max_output_tokens=300)
        candidate = json.loads(_output_text(result))
        model = models.SPARK
    except (models.BudgetStop, ValueError, TypeError):
        pass
    low = text.casefold()
    if not isinstance(candidate, dict) or not candidate.get("kind"):
        model = "offline-rules"
        num = re.search(r"\b(\d+)\s*(?:days?|дн|день|дня)", low)
        days = int(num[1]) if num else 1
        matches = [p for p in people if re.search(r"(?<!\w)" + re.escape(p["name"].casefold()) + r"(?!\w)", low)]
        material_matches = [m for m in mats if m["name"].casefold() in low or
                            (m["kind"] == "foam" and any(w in low for w in ("foam", "поролон")))]
        if len(matches) == 1:
            candidate = {"kind": "absence", "person_id": matches[0]["id"], "days": days}
        elif len(material_matches) == 1:
            candidate = {"kind": "material", "material_id": material_matches[0]["id"], "days": days}
        elif any(w in low for w in ("saw", "пила", "пилы")):
            cut = next((s for s in stage_rows if s["code"] == "cut"), None)
            candidate = {"kind": "machine", "stage_id": cut["id"] if cut else None, "days": days}
        else:
            raise DomainError("Name one worker, material or saw and a duration. / Укажите работника, материал или пилу и срок.")
    try:
        kind, days = candidate["kind"], candidate.get("days", 1)
        if type(days) is not int or not 1 <= days <= 30:
            raise ValueError()
        if kind == "absence":
            person = next(p for p in people if p["id"] == candidate.get("person_id"))
            sid = person["stage_id"]
            lost = 0  # planning_context adds distinct absent workers once
        elif kind == "machine":
            sid = candidate.get("stage_id")
            lost = next(s["capacity"] for s in stage_rows if s["id"] == sid)
        elif kind == "material":
            mid = next(m["id"] for m in mats if m["id"] == candidate.get("material_id"))
            consuming = conn.execute("SELECT stage_id FROM bom WHERE material_id=? ORDER BY stage_id LIMIT 1", (mid,)).fetchone()
            if not consuming:
                raise ValueError()
            sid = consuming[0]
            lost = 0
        else:
            raise ValueError()
        if sid not in {s["id"] for s in stage_rows}:
            raise ValueError()
    except (KeyError, StopIteration, TypeError, ValueError):
        raise DomainError("Cannot resolve this report; choose a preset. / Не удалось разобрать; выберите готовый вариант.")
    start = planner.next_workday(conn, date.fromisoformat(domain.today(conn)))
    return {"kind": kind, "stage_id": sid, "lost_capacity": lost, "start": start.isoformat(),
            "until": (start + timedelta(days=days)).isoformat(),
            "person_id": candidate.get("person_id") if kind == "absence" else None,
            "material_id": candidate.get("material_id") if kind == "material" else None}, model


def labour_cost(conn, tasks, multiplier=1.5):
    amounts = []
    for task in tasks:
        row = conn.execute("SELECT amount FROM rates WHERE product_id=? AND stage_id=?",
                           (task["product_id"], task["stage_id"])).fetchone()
        amounts.append(row[0] * multiplier if row else None)
    return None if None in amounts else round(sum(amounts), 2)


def build_card(conn, text, use_model=True):
    shock, model = parse(conn, text, use_model)
    fingerprint = snapshot(conn)
    base = adjustments(conn)
    before = simulate(conn, base)
    change = {"shock": shock}
    after = simulate(conn, base + [change])
    baseline_late = late_orders(before)
    outage_late = late_orders(after) - baseline_late
    options = []
    def option(oid, label, adjustment, plan, cost):
        moves = movements(before, plan)
        return {"id": oid, "label": label, "adjustment": adjustment, "movements": moves,
                "late_orders": len(late_orders(plan) - baseline_late),
                "baseline_late_orders": len(baseline_late), "total_late_orders": late_count(plan),
                "saved_orders": len(outage_late - late_orders(plan)),
                "cost": cost, "cost_tag": "ESTIMATE", "penalties": None,
                "notices": [m for m in moves if m["notify"]]}
    options.append(option("wait", tt("Wait for recovery", "Дождаться восстановления"), change, after, 0))
    recovery = date.fromisoformat(shock["until"])
    while recovery.isoweekday() not in planner.workdays(conn):
        recovery += timedelta(days=1)
    sid = shock["stage_id"]
    stage = next(s for s in domain.stages(conn) if s["id"] == sid)
    recovery_caps, absent, _ = planning_context(conn, recovery.isoformat(), base + [change])
    crew = [r[0] for r in conn.execute("SELECT id FROM people WHERE role='worker' AND stage_id=?", (sid,)) if r[0] not in absent]
    extra = math.floor(recovery_caps[sid] / 2) if crew else 0
    if extra:
        ot = {"date": recovery.isoformat(), "stage_id": sid, "capacity": extra, "person_ids": crew, "hours": 4}
        overtime = {"shock": shock, "overtime": [ot]}
        recovered = simulate(conn, base + [overtime])
        day_tasks = [t for t in recovered["tasks"] if t["stage_id"] == sid and t["date"] == ot["date"]]
        # Normal capacity is paid normally; only pieces beyond it use the overtime rate.
        cost = labour_cost(conn, day_tasks[stage["capacity"]:])
        options.append(option("half_shift", tt(f"Four extra hours at {stage['name_en']} on {ot['date']}",
                                                f"Четыре дополнительных часа: {stage['name_ru']}, {ot['date']}"), overtime, recovered, cost))
    moved = movements(before, after)
    urgent = next((m for m in moved if m["customer"]), None)
    if urgent:
        priority = {"shock": shock, "priority_orders": [urgent["order_id"]],
                    "priority_until": (recovery + timedelta(days=30)).isoformat()}
        options.append(option("prioritize", tt(f"Put order #{urgent['number']} first", f"Поставить заказ №{urgent['number']} первым"),
                              priority, simulate(conn, base + [priority]), 0))
    base_gaps = {(g["stage_id"], g["at"]) for g in before["gaps"]}
    gaps = [g for g in after["gaps"] if (g["stage_id"], g["at"]) not in base_gaps]
    # First affected starvation per stage, not hundreds of repeated empty days.
    gaps = list({g["stage_id"]: next(x for x in gaps if x["stage_id"] == g["stage_id"]) for g in gaps}.values())
    if fingerprint != snapshot(conn):
        raise DomainError("The queue changed during calculation; run again. / Очередь изменилась во время расчёта; повторите.")
    card = {"kind": "ripple", "currency": domain.currency(conn), "target_date": shock["start"], "headline": tt("Disruption: " + text, "Сбой: " + text),
            "shock": shock, "movements": moved, "stage_movements": stage_movements(before, after),
            "stages": [{"id": s["id"], "name": tt(s["name_en"], s["name_ru"])} for s in domain.stages(conn)],
            "starvation": gaps, "options": options,
            "numbers": [{"label": tt("orders move", "заказов сдвинуто"), "value": len(moved), "tag": "ESTIMATE"}],
            "why": [fact("Queue, live work marks and capacity are read from the shop records.", "Очередь, действующие отметки и мощность взяты из записей цеха."),
                    est("Forecast starts next workday: one stage per workday, even 08:00–16:00 shift. Late batch marks may change it. Other materials are assumed available.",
                        "Прогноз со следующего рабочего дня: один этап за день, равномерная смена 08:00–16:00. Поздние отметки меняют его. Остальные материалы считаются доступными."),
                    est("Delayed material uses current stock first; the unmet quantity is assumed delivered on recovery day. No workers change trade.",
                        "Сначала расходуется остаток задержанного материала; недостающее количество ожидается в день восстановления. Работники не меняют профессию."),
                    fact("Penalty contracts are not recorded: penalty exposure is unknown.", "Условия штрафов не записаны: сумма штрафов неизвестна.")],
            "fingerprint": fingerprint, "date": domain.today(conn)}
    return card, model


def existing_request(conn, person_id, kind, key, request):
    row = conn.execute("SELECT * FROM ripple_requests WHERE person_id=? AND kind=? AND request_key=?", (person_id, kind, key)).fetchone()
    if row and row["request_hash"] != digest(request):
        raise DomainError("This retry key has different input. Start a new report. / Ключ повтора занят; создайте новый запрос.")
    return row["action_id"] if row else None


def store(conn, person_id, kind, key, request, card, model):
    try:
        conn.execute("BEGIN IMMEDIATE")
        found = existing_request(conn, person_id, kind, key, request)
        if found:
            conn.rollback()
            return found
        if card["fingerprint"] != snapshot(conn):
            raise DomainError("The queue changed before saving; run again. / Очередь изменилась до сохранения; повторите.")
        aid = conn.execute("INSERT INTO agent_actions(kind,payload,model,created_at,for_person) VALUES(?,?,?,?,?)",
                           (kind, json.dumps(card), model, domain.now(conn).isoformat(timespec="seconds"), person_id)).lastrowid
        conn.execute("INSERT INTO ripple_requests VALUES(?,?,?,?,?)", (person_id, kind, key, digest(request), aid))
        conn.commit()
        return aid
    except Exception:
        conn.rollback()
        raise


def cards(conn):
    return [{**json.loads(r["payload"]), "id": r["id"], "model": r["model"], "status": r["status"]}
            for r in conn.execute("SELECT * FROM agent_actions WHERE kind IN ('ripple','rush') AND status='proposed' ORDER BY id DESC")]


def draft(conn, person_id, aid, to, subject, body):
    preview = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>' + html.escape(subject) +
               '</title><body><h1>' + html.escape(subject) + '</h1><p>' + html.escape(to or "") +
               '</p><p style="white-space:pre-wrap">' + html.escape(body) + '</p></body></html>')
    conn.execute("INSERT INTO outbox(kind,to_name,subject,body,attachment_html,created_at,created_by,action_id) VALUES('email',?,?,?,?,?,?,?)",
                 (to, subject, body, preview, domain.now(conn).isoformat(timespec="seconds"), person_id, aid))


class PickIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pick: str


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


@router.post("/api/ripple/cards/{aid}/reject")
def reject_route(aid: int, body: RejectIn, s: Session = Depends(finance), conn=Depends(get_conn)):
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM agent_actions WHERE id=? AND kind IN ('ripple','rush')", (aid,)).fetchone()
        if not row:
            raise DomainError("No such card. / Нет такой карточки.")
        if row["status"] != "proposed":
            conn.rollback()
            return {"ok": True, "already": True, "message": "Already recorded. / Уже записано."}
        conn.execute("UPDATE agent_actions SET status='rejected',decided_by=?,decided_at=?,result=? WHERE id=?",
                     (s.person_id, domain.now(conn).isoformat(timespec="seconds"), json.dumps({"reason": body.reason.strip()}), aid))
        conn.commit()
        return {"ok": True, "message": "Declined; nothing applied. / Отклонено; ничего не применено."}
    except Exception:
        conn.rollback()
        raise


def decide(conn, person_id, aid, pick, kind="ripple"):
    """Lock, check freshness, then apply exactly one stored option atomically."""
    if domain.person(conn, person_id)["role"] not in ("owner", "manager"):
        raise DomainError("Owner/manager only. / Только владелец/менеджер.")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM agent_actions WHERE id=? AND kind=?", (aid, kind)).fetchone()
        if not row:
            raise DomainError("No such card. / Нет такой карточки.")
        if row["status"] == "applied":
            result = json.loads(row["result"])
            if result["pick"] != pick:
                raise DomainError("Already decided; do not apply another option. / Уже решено; другой вариант не применён.")
            conn.rollback()
            return {**result, "already": True, "message": "Already recorded. / Уже записано."}
        p = json.loads(row["payload"])
        selected = next((o for o in p["options"] if o["id"] == pick), None)
        if row["status"] != "proposed" or not selected:
            raise DomainError("Pick one stored option. / Выберите сохранённый вариант.")
        if p["date"] != domain.today(conn) or p["fingerprint"] != snapshot(conn):
            raise DomainError("The queue changed. Run What if again. / Очередь изменилась. Повторите расчёт.")
        if kind == "ripple":
            conn.execute("INSERT INTO ripple_adjustments VALUES(?,?)", (aid, json.dumps(selected["adjustment"])))
            notices = selected["notices"]
            for m in notices:
                body = f"[ESTIMATE / ОЦЕНКА] Order / Заказ #{m['number']}: {m['old_eta']} → {m['new_eta'] or 'unknown / неизвестно'}. Please agree a new date. / Согласуйте новую дату."
                draft(conn, person_id, aid, m["customer"], f"Order / Заказ #{m['number']}", body)
        else:
            q = p["quote"]
            notices = [q]
            draft(conn, person_id, aid, q["customer"], "Rush quote / Срочный расчёт",
                  selected["draft"])
        result = {"ok": True, "pick": pick, "drafts": len(notices),
                  "message": "Recorded; customer drafts are in the dry-run outbox. / Записано; черновики клиентам в исходящих, отправки нет."}
        conn.execute("UPDATE agent_actions SET status='applied',decided_by=?,decided_at=?,result=? WHERE id=?",
                     (person_id, domain.now(conn).isoformat(timespec="seconds"), json.dumps(result), aid))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    request_key: str = Field(min_length=1, max_length=100)


@router.post("/api/ripple/report")
def report(body: ReportIn, s: Session = Depends(session), conn=Depends(get_conn)):
    old = existing_request(conn, s.person_id, "ripple", body.request_key, body.text)
    if old:
        aid = old
    else:
        card, model = build_card(conn, body.text)
        aid = store(conn, s.person_id, "ripple", body.request_key, body.text, card, model)
    # Worker/tablet reports get a receipt only: never a financial payload.
    out = {"ok": True, "id": aid, "message": "Report saved for the owner. / Сообщение сохранено для владельца."}
    if s.role in ("owner", "manager") and not s.tablet:
        row = conn.execute("SELECT payload FROM agent_actions WHERE id=?", (aid,)).fetchone()
        out["card"] = {**json.loads(row[0]), "id": aid}
    return out


@router.get("/api/ripple/cards")
def feed(s: Session = Depends(finance), conn=Depends(get_conn)):
    return {"cards": cards(conn), "currency": domain.currency(conn)}


@router.post("/api/ripple/cards/{aid}/apply")
def apply_route(aid: int, body: PickIn, s: Session = Depends(finance), conn=Depends(get_conn)):
    return decide(conn, s.person_id, aid, body.pick)


def seed(conn, rnd):
    # Explicit fictional costs let the demo explain margins, rather than inventing them in answers.
    for m in conn.execute("SELECT id,kind FROM materials"):
        cost = {"foam": 7, "fabric": 9, "frame": 24, "fittings": 3}.get(m["kind"], 4)
        conn.execute("INSERT OR IGNORE INTO ripple_material_costs VALUES(?,?)", (m["id"], cost))


def seed_finalize(conn, rnd):
    """The saved scenario must include demo events added by later feature seeds."""
    who = conn.execute("SELECT id FROM people WHERE role='owner' LIMIT 1").fetchone()
    if who and not conn.execute("SELECT 1 FROM agent_actions WHERE kind='ripple'").fetchone():
        conn.commit()
        card, _ = build_card(conn, "Saw is broken for 2 days / Пила сломана на 2 дня", use_model=False)
        store(conn, who[0], "ripple", "demo-saw", "demo-saw", card, "offline-rules")
