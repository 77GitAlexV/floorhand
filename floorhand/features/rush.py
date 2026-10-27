"""Rush feasibility and margin bridge, computed from the same queue as ripple."""
import json
import math
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import domain, models
from ..domain import DomainError
from ..web import Session, get_conn
from . import materials, planner, ripple
from .ask import tt, est, calc, fact

router = APIRouter()


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=100)
    text: str = Field(default="", max_length=500)
    product_id: int | None = None
    qty: int | None = Field(default=None, ge=1, le=100)
    due_date: str | None = None
    customer_id: int | None = None
    variant: str = Field(default="", max_length=300)
    target_margin: float = Field(default=0.35, ge=0, lt=1, allow_inf_nan=False)


def resolve(conn, body):
    data = {"product_id": body.product_id, "qty": body.qty, "due_date": body.due_date}
    model = "offline-rules"
    products = [dict(r) for r in conn.execute("SELECT id,name FROM products")]
    aliases = [dict(r) for r in conn.execute("SELECT alias,target_id FROM synonyms WHERE kind='product'")]
    if body.text and not all(data.values()):
        try:
            from ..intake import _output_text
            raw = models.respond(conn, "rush-parse", "Extract an urgent order as JSON: product_id, qty, due_date (ISO). "
                                 "Use only known products/aliases. Ambiguous fields null; no prices or advice.\n" +
                                 json.dumps({"text": body.text, "today": domain.today(conn), "products": products, "aliases": aliases}, ensure_ascii=False),
                                 max_output_tokens=250)
            parsed = json.loads(_output_text(raw))
            if isinstance(parsed, dict):
                for k in data:
                    if data[k] is None:
                        data[k] = parsed.get(k)
                model = models.SPARK
        except (models.BudgetStop, ValueError, TypeError):
            pass
        low = body.text.casefold()
        if data["product_id"] is None:
            hits = {p["id"] for p in products if p["name"].casefold() in low}
            hits |= {a["target_id"] for a in aliases if re.search(r"(?<!\w)" + re.escape(a["alias"].casefold()) + r"(?!\w)", low)}
            if len(hits) == 1:
                data["product_id"] = hits.pop()
        if data["qty"] is None:
            match = re.search(r"(?<![\d-])\b(\d{1,3})\b(?!-)", low)
            if match:
                data["qty"] = int(match[1])
        if data["due_date"] is None:
            match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", low)
            if match:
                data["due_date"] = match[0]
            elif "friday" in low or "пятниц" in low:
                today = date.fromisoformat(domain.today(conn))
                data["due_date"] = (today + timedelta(days=(4 - today.weekday()) % 7)).isoformat()
    try:
        if type(data["qty"]) is not int or not 1 <= data["qty"] <= 100:
            raise ValueError()
        if type(data["product_id"]) is not int or not any(p["id"] == data["product_id"] for p in products):
            raise ValueError()
        due = date.fromisoformat(data["due_date"])
        if due < date.fromisoformat(domain.today(conn)):
            raise ValueError()
        if body.customer_id is not None and not conn.execute("SELECT 1 FROM customers WHERE id=?", (body.customer_id,)).fetchone():
            raise ValueError()
    except (ValueError, TypeError):
        raise DomainError("Choose one product, quantity and future due date. / Выберите изделие, количество и дату в будущем.")
    return data, model


def material_cost(conn, pid, variant):
    lines = materials._bom_lines(conn).get(pid, [])
    if not lines:
        return None
    total = 0
    for line in lines:
        mid = materials.fabric_for_variant(conn, variant) if line["by_variant"] else line["material_id"]
        row = conn.execute("SELECT unit_cost FROM ripple_material_costs WHERE material_id=?", (mid,)).fetchone()
        if not row:
            return None
        total += line["qty"] * row[0]
    return round(total, 2)


def wip_cash(conn):
    """Unshipped units: consumed materials plus earned labour, frozen payouts first."""
    rows = conn.execute(f"SELECT e.*,o.product_id,o.variant,pl.amount AS paid_rate FROM ({domain.LIVE_DONE}) e "
                        "JOIN units u ON u.id=e.unit_id JOIN orders o ON o.id=u.order_id "
                        "LEFT JOIN payout_lines pl ON pl.event_id=e.id "
                        "WHERE NOT EXISTS(SELECT 1 FROM shipment_lines sl WHERE sl.unit_id=e.unit_id)").fetchall()
    total, missing = 0, False
    bom, fabrics = materials._bom_lines(conn), materials._fabrics(conn)
    for row in rows:
        if not bom.get(row["product_id"]):
            missing = True
        rate = row["paid_rate"] if row["paid_rate"] is not None else domain.rate(conn, row["unit_id"], row["stage_id"])
        if rate is None:
            missing = True
        else:
            total += rate
        for ln in bom.get(row["product_id"], []):
            if ln["stage_id"] != row["stage_id"]:
                continue
            mid = materials.fabric_for_variant(conn, row["variant"], fabrics) if ln["by_variant"] else ln["material_id"]
            cost = conn.execute("SELECT unit_cost FROM ripple_material_costs WHERE material_id=?", (mid,)).fetchone()
            if cost is None:
                missing = True
            else:
                total += ln["qty"] * cost[0]
    return {"value": None if missing else round(total, 2), "known_subtotal": round(total, 2), "tag": "ESTIMATE",
            "basis": tt("Consumed material estimates + earned labour in unshipped units; excludes overhead and supplier payment timing.",
                        "Оценка израсходованных материалов + заработанный труд в неотгруженных изделиях; без накладных расходов и сроков оплаты поставщикам.")}


def margin_bridge(revenue, base_cost, extra, target):
    """Whole-currency quote rounds up so the target is never lost to rounding."""
    if base_cost is None or extra is None or revenue <= 0:
        return {"base_cost": base_cost, "extra_cost": extra,
                "margin_before": round((revenue - base_cost) / revenue, 4) if base_cost is not None and revenue > 0 else None, "margin_after": None,
                "required_revenue": None, "surcharge": None, "margin_quoted": None}
    required = max(revenue, math.ceil((base_cost + extra) / (1 - target) - 1e-9))
    return {"base_cost": base_cost, "extra_cost": extra, "margin_before": round((revenue - base_cost) / revenue, 4),
            "margin_after": round((revenue - base_cost - extra) / revenue, 4), "required_revenue": required,
            "surcharge": required - revenue, "margin_quoted": round((required - base_cost - extra) / required, 4)}


def build_card(conn, body):
    data, model = resolve(conn, body)
    fingerprint = ripple.snapshot(conn)
    pid, qty, due = data["product_id"], data["qty"], data["due_date"]
    product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    stages = domain.stages(conn)
    if not stages:
        raise DomainError("Set up stages first. / Сначала настройте этапы.")
    customer = conn.execute("SELECT name FROM customers WHERE id=?", (body.customer_id,)).fetchone()
    customer = customer[0] if customer else "Customer to confirm / Уточнить клиента"
    synthetic = [planner.Unit(id=-i-1, order_id=-1, number=0, label="Rush / Срочный", product=product["name"],
                              product_id=pid, variant=body.variant, customer=customer, due=due,
                              stage_id=stages[0]["id"], stage_idx=0, stages_left=len(stages), assigned_to=None,
                              days_at_stage=0) for i in range(qty)]
    base = ripple.adjustments(conn)
    before = ripple.simulate(conn, base)
    normal = ripple.simulate(conn, base, synthetic)
    prioritized = {"priority_orders": [-1], "priority_until": (date.fromisoformat(due) + timedelta(days=30)).isoformat()}
    priority = ripple.simulate(conn, base + [prioritized], synthetic)
    # Half-shift only at the same stage, with that trade's crew. Capacity cannot be borrowed.
    overtime = []
    day = date.fromisoformat(domain.today(conn))
    for _ in range(ripple.HORIZON):
        day += timedelta(days=1)
        if day.isoformat() > due:
            break
        if day.isoweekday() not in planner.workdays(conn):
            continue
        caps, absent, _ = ripple.planning_context(conn, day.isoformat(), base)
        for s in stages:
            crew = [r[0] for r in conn.execute("SELECT id FROM people WHERE role='worker' AND stage_id=?", (s["id"],)) if r[0] not in absent]
            extra = min(s["capacity"] // 2, caps[s["id"]] // 2)
            if crew and extra:
                overtime.append({"date": day.isoformat(), "stage_id": s["id"], "capacity": extra, "person_ids": crew, "hours": 4})
    recovery = ripple.simulate(conn, base + [dict(prioritized, overtime=overtime)], synthetic)
    eta = priority["orders"][-1]["eta"]
    chosen = priority if eta is not None and eta <= due else recovery
    chosen_ot = [] if chosen is priority else overtime
    rate_lines = []
    for s in stages:
        row = conn.execute("SELECT amount FROM rates WHERE product_id=? AND stage_id=?", (pid, s["id"])).fetchone()
        rate_lines.append({"stage_id": s["id"], "stage": tt(s["name_en"], s["name_ru"]),
                           "rate": row[0] if row else None, "tag": "FACT"})
    labour = None if any(s["rate"] is None for s in rate_lines) else qty * sum(s["rate"] for s in rate_lines)
    mat = material_cost(conn, pid, body.variant)
    base_cost = None if mat is None or labour is None else round(mat * qty + labour, 2)
    stage_costs = []
    for s in stages:
        extra_total = 0
        unknown = False
        pieces = 0
        for ot in (o for o in chosen_ot if o["stage_id"] == s["id"]):
            cap = ripple.planning_context(conn, ot["date"], base)[0][s["id"]]
            tasks = [t for t in chosen["tasks"] if t["date"] == ot["date"] and t["stage_id"] == s["id"]][cap:]
            # Incremental cost vs ordinary piecework is the 50% premium on ALL displaced/urgent OT pieces.
            amount = ripple.labour_cost(conn, tasks, 0.5)
            pieces += len(tasks)
            if amount is None:
                unknown = True
            else:
                extra_total += amount
        stage_costs.append({"stage": tt(s["name_en"], s["name_ru"]), "pieces": pieces,
                            "extra_cost": None if unknown else round(extra_total, 2), "tag": "ESTIMATE"})
    extra = None if any(s["extra_cost"] is None for s in stage_costs) else round(sum(s["extra_cost"] for s in stage_costs), 2)
    financial = margin_bridge(product["price"] * qty, base_cost, extra, body.target_margin)
    needs = materials._unit_material_needs(conn, pid, body.variant, materials._bom_lines(conn), materials._fabrics(conn))
    have = materials.on_hand(conn)
    queued_need = materials.requirement(conn)
    grouped = {}
    for mid, _, amount in needs:
        grouped[mid] = grouped.get(mid, 0) + amount
    shortages = [{"material_id": mid, "need": n * qty + queued_need.get(mid, {}).get("total", 0), "on_hand": have.get(mid, 0), "tag": "CALC"}
                 for mid, n in grouped.items() if n * qty + queued_need.get(mid, {}).get("total", 0) > have.get(mid, 0)]
    unresolved_bom = not materials._bom_lines(conn).get(pid) or any(
        (ln["by_variant"] and materials.fabric_for_variant(conn, body.variant) is None) or (not ln["by_variant"] and ln["material_id"] is None)
        for ln in materials._bom_lines(conn).get(pid, []))
    final_eta = chosen["orders"][-1]["eta"]
    fits = bool(final_eta and final_eta <= due and not shortages and not unresolved_bom)
    q = {**data, "customer": customer, "variant": body.variant, "product": product["name"],
         "queue_eta": normal["orders"][-1]["eta"], "priority_eta": eta, "eta": final_eta,
         "capacity_fits": bool(final_eta and final_eta <= due), "fits": fits,
         "material_shortages": shortages, "material_spec_unknown": unresolved_bom,
         "revenue": product["price"] * qty, "target_margin": body.target_margin,
         **financial, "stage_rates": rate_lines, "overtime": stage_costs,
         "wip_cash": wip_cash(conn), "incremental_wip": None if base_cost is None or extra is None else round(base_cost + extra, 2),
         "tag": "ESTIMATE"}
    draft = (f"[ESTIMATE / ОЦЕНКА] {qty} × {product['name']}, requested / нужно {due}; ETA / срок {final_eta}. "
             f"Capacity fits / мощности хватает: {q['capacity_fits']}; materials confirmed / материалы подтверждены: {not shortages and not unresolved_bom}. "
             f"[CALC / РАСЧЁТ] Total / сумма: {financial['required_revenue'] if financial['required_revenue'] is not None else 'unknown / неизвестно'} {domain.currency(conn)}; "
             "subject to materials, rates and overhead review. No capacity reserved. / Уточните материалы, ставки и накладные расходы. Мощности не забронированы.")
    if fingerprint != ripple.snapshot(conn):
        raise DomainError("The queue changed during calculation; run again. / Очередь изменилась во время расчёта; повторите.")
    card = {"kind": "rush", "currency": domain.currency(conn), "headline": tt(f"Rush: {qty} × {product['name']} by {due}", f"Срочно: {qty} × {product['name']} к {due}"),
            "target_date": due, "date": domain.today(conn), "fingerprint": fingerprint, "quote": q,
            "movements": ripple.movements(before, chosen), "starvation": [],
            "options": [{"id": "draft_quote", "label": tt("Save conditional quote draft", "Сохранить предварительный расчёт"), "draft": draft}],
            "numbers": [{"label": tt("surcharge", "доплата"), "value": financial["surcharge"], "tag": "CALC"},
                        {"label": tt("extra labour", "дополнительный труд"), "value": extra, "tag": "ESTIMATE"}],
            "why": [fact("Product prices and stage piece rates come from shop records; a missing rate is unknown.",
                         "Цены изделий и сдельные ставки взяты из записей; отсутствующая ставка неизвестна."),
                    calc("Required revenue = (materials + ordinary labour + overtime premium) / (1 − target margin), rounded up.",
                         "Нужная выручка = (материалы + обычный труд + доплата за сверхурочные) / (1 − целевая маржа), округление вверх."),
                    est("Contribution margin excludes overhead, taxes and penalties. Material costs are office estimates. Overtime pays 1.5× piece rates; extra cost is the 0.5× premium, including displaced work.",
                        "Маржа без накладных расходов, налогов и штрафов. Материалы по оценке офиса. Сверхурочные оплачиваются по ставке ×1,5; добавочная стоимость — доплата ×0,5, включая сдвинутые работы."),
                    est("One stage per workday. Material shortages require confirmation; no capacity is reserved by a quote draft.",
                        "Один этап за рабочий день. Нехватку материалов нужно подтвердить; черновик расчёта не бронирует мощности.")]}
    return card, model


@router.get("/api/rush/catalog")
def catalog(s: Session = Depends(ripple.finance), conn=Depends(get_conn)):
    return {"products": [dict(r) for r in conn.execute("SELECT id,name FROM products")],
            "customers": [dict(r) for r in conn.execute("SELECT id,name FROM customers")]}


@router.post("/api/rush/quote")
def quote(body: QuoteIn, s: Session = Depends(ripple.finance), conn=Depends(get_conn)):
    request = body.model_dump(exclude={"request_key"})
    aid = ripple.existing_request(conn, s.person_id, "rush", body.request_key, request)
    if not aid:
        card, model = build_card(conn, body)
        aid = ripple.store(conn, s.person_id, "rush", body.request_key, request, card, model)
    p = json.loads(conn.execute("SELECT payload FROM agent_actions WHERE id=?", (aid,)).fetchone()[0])
    return {"ok": True, "id": aid, "card": {**p, "id": aid},
            "message": "Quote calculated; choose to save a draft. / Расчёт готов; выберите сохранение черновика."}


@router.post("/api/rush/cards/{aid}/apply")
def apply_route(aid: int, body: ripple.PickIn, s: Session = Depends(ripple.finance), conn=Depends(get_conn)):
    return ripple.decide(conn, s.person_id, aid, body.pick, "rush")
