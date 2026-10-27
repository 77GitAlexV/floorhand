"""Questions in words: "how much did we earn in October?", "who owes us for
more than 30 days?", "what is late and why?", "how much velvet beige this week?"

Two paths, one set of tools:
- Muse Spark with function calling picks tools and puts the result into words.
- Offline rules match the question to the same tools and print the result.

The tools are read-only domain functions, cut by role (money only for the owner),
and they run with SQLite in query_only mode: a tool that tried to write would
fail. Every number in an answer is tagged FACT / CALC / ESTIMATE; the model
explains, it never invents figures - the figures are shown from the tool result.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta

from .. import domain, models
from ..domain import LIVE_DONE, DomainError
from ..models import BudgetStop
from . import materials, planner

OFFICE = ("manager", "owner")
OWNER = ("owner",)

MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTHS_RU = ["янв", "фев", "мар", "апр", "ма", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def tt(en: str, ru: str) -> dict:
    return {"en": en, "ru": ru}


def fact(en, ru):
    return {"tag": "FACT", **tt(en, ru)}


def calc(en, ru):
    return {"tag": "CALC", **tt(en, ru)}


def est(en, ru):
    return {"tag": "ESTIMATE", **tt(en, ru)}


@contextmanager
def read_only(conn: sqlite3.Connection):
    """Any write inside raises: the model's tools can only look."""
    conn.execute("PRAGMA query_only = 1")
    try:
        yield conn
    finally:
        conn.execute("PRAGMA query_only = 0")


# --- tools ------------------------------------------------------------------

def t_income(conn, month: str = "") -> dict:
    today = date.fromisoformat(domain.today(conn))
    month = month or today.strftime("%Y-%m")
    cur = domain.currency(conn)
    ships = conn.execute("SELECT id, paid_amount FROM shipments WHERE substr(created_at, 1, 7) = ?",
                         (month,)).fetchall()
    invoiced = sum(domain.shipment_total(conn, s["id"]) for s in ships)
    paid = sum(s["paid_amount"] for s in ships)
    pcs = conn.execute("SELECT COUNT(*) FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id "
                       "WHERE substr(s.created_at, 1, 7) = ?", (month,)).fetchone()[0]
    wages = _wages(conn, month)
    return {"month": month, "shipments": len(ships), "pieces": pcs, "invoiced": invoiced,
            "paid": paid, "owed": invoiced - paid, "wages": wages, "currency": cur,
            "lines": [
                fact(f"{month}: {len(ships)} shipments, {pcs} pcs, invoiced {cur}{invoiced}.",
                     f"{month}: отгрузок {len(ships)}, {pcs} шт, выставлено {cur}{invoiced}."),
                fact(f"Paid by customers {cur}{paid}; still owed {cur}{invoiced - paid}.",
                     f"Оплачено клиентами {cur}{paid}; ещё должны {cur}{invoiced - paid}."),
                calc(f"Piece-rate wages for work dated {month}: {cur}{wages}.",
                     f"Сдельная зарплата за работу датой {month}: {cur}{wages}."),
                calc(f"Invoiced minus wages: {cur}{invoiced - wages} (materials and overheads not included).",
                     f"Выставлено минус зарплата: {cur}{invoiced - wages} (без материалов и накладных).")]}


def _wages(conn, month: str) -> int:
    total = 0
    for ev in conn.execute(f"SELECT unit_id, stage_id FROM ({LIVE_DONE}) WHERE substr(work_date, 1, 7) = ?",
                           (month,)):
        total += domain.rate(conn, ev["unit_id"], ev["stage_id"]) or 0
    return total


def t_debtors(conn, min_days: int = 30) -> dict:
    today = date.fromisoformat(domain.today(conn))
    cur = domain.currency(conn)
    by_cust: dict[int, dict] = {}
    for sh in conn.execute("SELECT s.*, c.name FROM shipments s JOIN customers c ON c.id = s.customer_id"):
        owed = domain.shipment_total(conn, sh["id"]) - sh["paid_amount"]
        if owed <= 0:
            continue
        age = (today - date.fromisoformat(sh["created_at"][:10])).days
        d = by_cust.setdefault(sh["customer_id"], {"customer": sh["name"], "owed": 0, "oldest": 0,
                                                    "overdue": 0})
        d["owed"] += owed
        d["oldest"] = max(d["oldest"], age)
        if age > min_days:
            d["overdue"] += owed
    rows = sorted((d for d in by_cust.values() if d["overdue"]), key=lambda d: -d["overdue"])
    total = sum(d["overdue"] for d in rows)
    lines = [fact(f"{len(rows)} customers owe {cur}{total} on shipments older than {min_days} days.",
                  f"{len(rows)} клиентов должны {cur}{total} по отгрузкам старше {min_days} дн.")]
    for d in rows[:8]:
        lines.append(fact(f"{d['customer']}: {cur}{d['overdue']} overdue, oldest {d['oldest']} days.",
                          f"{d['customer']}: {cur}{d['overdue']} просрочено, самому старому {d['oldest']} дн."))
    return {"min_days": min_days, "rows": rows, "total": total, "currency": cur, "lines": lines}


def t_late_orders(conn) -> dict:
    today = date.fromisoformat(domain.today(conn))
    units = planner.open_units(conn, today)
    floor = {s["id"]: s for s in domain.floor_overview(conn)}
    orders: dict[int, dict] = {}
    for u in units:
        if u.risk == planner.RISK_OK:
            continue
        o = orders.setdefault(u.number, {"number": u.number, "product": u.product,
                                         "customer": u.customer, "due": u.due, "risk": u.risk,
                                         "late_days": u.late_days, "stages": {}, "units": 0})
        o["units"] += 1
        o["stages"][u.stage_id] = max(o["stages"].get(u.stage_id, 0), u.days_at_stage)
    rows = sorted(orders.values(), key=lambda o: (o["risk"] != "late", -o["late_days"], o["due"] or ""))
    late = [o for o in rows if o["risk"] == "late"]
    risk = [o for o in rows if o["risk"] == "risk"]
    lines = [fact(f"{len(late)} orders are late, {len(risk)} more will be late at the current pace.",
                  f"Опаздывают {len(late)} заказов, ещё {len(risk)} не успеют при текущем темпе.")]
    for o in rows[:8]:
        where = "; ".join(
            f"{floor[sid]['name_en']} {days} d (queue {floor[sid]['queue']}, {floor[sid]['capacity']}/day)"
            for sid, days in o["stages"].items())
        where_ru = "; ".join(
            f"{floor[sid]['name_ru']} {days} дн (очередь {floor[sid]['queue']}, {floor[sid]['capacity']}/день)"
            for sid, days in o["stages"].items())
        head = f"#{o['number']} {o['product']} · {o['customer'] or 'stock'} · due {o['due']}"
        tag = f"late {o['late_days']} d" if o["risk"] == "late" else "at risk"
        tag_ru = f"опоздание {o['late_days']} дн" if o["risk"] == "late" else "под угрозой"
        lines.append(fact(f"{head} · {tag} · at {where}.", f"{head} · {tag_ru} · на {where_ru}."))
        o["where"] = where
    bn = [s for s in floor.values() if s["bottleneck"]]
    if bn:
        lines.append(calc(f"Why: {bn[0]['name_en']} holds {bn[0]['queue']} pcs at {bn[0]['capacity']}/day "
                          f"= {bn[0]['queue'] / max(bn[0]['capacity'], 1):.1f} days to clear.",
                          f"Причина: {bn[0]['name_ru']} держит {bn[0]['queue']} шт при {bn[0]['capacity']}/день "
                          f"= {bn[0]['queue'] / max(bn[0]['capacity'], 1):.1f} дня очереди."))
    lines.append(est("At risk = stages left exceed working days to the due date, one stage per day.",
                     "Под угрозой = этапов осталось больше, чем рабочих дней до срока, этап в день."))
    return {"late": late, "risk": risk, "lines": lines}


def t_material_need(conn, name: str = "", days: int = 5) -> dict:
    mats = conn.execute("SELECT * FROM materials ORDER BY name").fetchall()
    needle = (name or "").lower().strip()
    m = next((x for x in mats if x["name"].lower() == needle), None) or \
        next((x for x in mats if needle and (needle in x["name"].lower() or x["name"].lower() in needle)), None)
    if m is None:
        return {"lines": [fact("Which material? Known: " + ", ".join(x["name"] for x in mats) + ".",
                               "Какой материал? Известны: " + ", ".join(x["name"] for x in mats) + ".")]}
    have = materials.on_hand(conn)[m["id"]]
    need = materials.requirement(conn).get(m["id"], {"total": 0, "next": 0, "orders": {}})
    stage = conn.execute("SELECT s.capacity, s.name_en, s.name_ru FROM bom b JOIN stages s ON s.id = b.stage_id "
                         "WHERE b.material_id = ? OR (b.by_variant = 1 AND ? = 'fabric') LIMIT 1",
                         (m["id"], m["kind"])).fetchone()
    u = m["unit"]
    lines = [fact(f"{m['name']}: {have:g} {u} on hand.", f"{m['name']}: на складе {have:g} {u}."),
             fact(f"Open work needs {need['total']:g} {u}; {need['next']:g} {u} of it is already waiting at "
                  f"{stage['name_en'] if stage else '-'} and goes next.",
                  f"Открытая работа требует {need['total']:g} {u}; из них {need['next']:g} {u} уже ждут на этапе "
                  f"{stage['name_ru'] if stage else '-'} и уйдут первыми."),
             calc(f"Short by {max(0.0, need['total'] - have):g} {u} for everything open; "
                  f"{'enough' if have >= need['next'] else 'NOT enough'} for the next marks.",
                  f"Не хватает {max(0.0, need['total'] - have):g} {u} на всё открытое; "
                  f"на ближайшие отметки {'хватает' if have >= need['next'] else 'НЕ хватает'}.")]
    if need["orders"]:
        top = ", ".join(f"#{n} {q:g}" for n, q in sorted(need["orders"].items(), key=lambda kv: -kv[1])[:5])
        lines.append(fact(f"Orders behind the need: {top}.", f"Заказы за потребностью: {top}."))
    lines.append(est(f"Reorder point {m['reorder_point']:g} {u}, supplier lead time {m['lead_days']} days.",
                     f"Точка заказа {m['reorder_point']:g} {u}, срок поставки {m['lead_days']} дн."))
    return {"material": m["name"], "on_hand": have, "need": need["total"], "need_next": need["next"],
            "unit": u, "lines": lines}


def t_floor_now(conn) -> dict:
    floor = domain.floor_overview(conn)
    lines = []
    for s in floor:
        tag = " · BOTTLENECK" if s["bottleneck"] else ""
        tag_ru = " · УЗКОЕ МЕСТО" if s["bottleneck"] else ""
        lines.append(fact(f"{s['name_en']}: queue {s['queue']}, done today {s['done_today']} of {s['capacity']}{tag}.",
                          f"{s['name_ru']}: очередь {s['queue']}, сегодня {s['done_today']} из {s['capacity']}{tag_ru}."))
    return {"stages": floor, "lines": lines}


def t_order_status(conn, number: int = 0) -> dict:
    row = conn.execute("SELECT id FROM orders WHERE number = ?", (number,)).fetchone()
    if row is None:
        return {"lines": [fact(f"No order #{number}.", f"Заказа #{number} нет.")]}
    o = domain.order_detail(conn, row["id"])
    where = ", ".join(f"{s['name_en']} {s['done']}/{o['qty']}" for s in o["line"])
    where_ru = ", ".join(f"{s['name_ru']} {s['done']}/{o['qty']}" for s in o["line"])
    lines = [fact(f"#{o['number']} {o['product']} ×{o['qty']} · {o['customer'] or 'stock'} · due {o['due_date'] or '-'} "
                  f"· status {o['status']}" + (f" · late {o['late_days']} d" if o["late_days"] else "") + ".",
                  f"#{o['number']} {o['product']} ×{o['qty']} · {o['customer'] or 'на склад'} · отдать к {o['due_date'] or '-'} "
                  f"· статус {o['status']}" + (f" · опоздание {o['late_days']} дн" if o["late_days"] else "") + "."),
             fact(f"Stages: {where}.", f"Этапы: {where_ru}.")]
    return {"order": {k: o[k] for k in ("number", "product", "qty", "customer", "due_date", "status", "late_days")},
            "lines": lines}


def t_stock_ready(conn) -> dict:
    shipped = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM shipment_lines")}
    last = domain.stages(conn)[-1]["id"]
    done = {r["unit_id"] for r in conn.execute(f"SELECT unit_id FROM ({LIVE_DONE}) WHERE stage_id = ?", (last,))}
    by_product: dict[str, int] = {}
    for r in conn.execute("SELECT u.id, p.name FROM units u JOIN orders o ON o.id = u.order_id "
                          "JOIN products p ON p.id = o.product_id WHERE o.customer_id IS NULL AND o.cancelled = 0"):
        if r["id"] in done and r["id"] not in shipped:
            by_product[r["name"]] = by_product.get(r["name"], 0) + 1
    total = sum(by_product.values())
    lines = [fact(f"Finished for stock, not yet sold: {total} pcs.", f"Готово на склад, не продано: {total} шт.")]
    for name, n in sorted(by_product.items(), key=lambda kv: -kv[1]):
        lines.append(fact(f"{name}: {n}.", f"{name}: {n}."))
    return {"by_product": by_product, "total": total, "lines": lines}


def t_payroll_due(conn) -> dict:
    cur = domain.currency(conn)
    by_person: dict[str, int] = {}
    pending = 0
    for ev in conn.execute(f"SELECT e.unit_id, e.stage_id, p.name FROM ({LIVE_DONE}) e "
                           "JOIN people p ON p.id = e.person_id "
                           "WHERE NOT EXISTS (SELECT 1 FROM payout_lines pl WHERE pl.event_id = e.id)"):
        amount = domain.rate(conn, ev["unit_id"], ev["stage_id"])
        if amount is None:
            pending += 1
        else:
            by_person[ev["name"]] = by_person.get(ev["name"], 0) + amount
    total = sum(by_person.values())
    lines = [fact(f"To be paid to workers: {cur}{total} for {sum(1 for _ in by_person)} people.",
                  f"К выплате рабочим: {cur}{total}, людей {sum(1 for _ in by_person)}.")]
    for name, amt in sorted(by_person.items(), key=lambda kv: -kv[1])[:6]:
        lines.append(fact(f"{name}: {cur}{amt}.", f"{name}: {cur}{amt}."))
    if pending:
        lines.append(fact(f"{pending} marks have no rate yet - not counted, not zero.",
                          f"{pending} отметок без ставки - не посчитаны, это не ноль."))
    return {"total": total, "by_person": by_person, "price_pending": pending, "currency": cur, "lines": lines}


def t_shortages(conn) -> dict:
    rows = materials.shortages(conn)
    if not rows:
        return {"rows": [], "lines": [fact("Nothing is short for the open work.", "На открытую работу всего хватает.")]}
    lines = [fact(f"{len(rows)} materials do not cover the open work.", f"{len(rows)} материалов не хватает на открытую работу.")]
    for s in rows[:6]:
        stop = " - stops the next marks" if s["stops_next"] else ""
        stop_ru = " - встанут ближайшие отметки" if s["stops_next"] else ""
        lines.append(fact(f"{s['name']}: need {s['need']:g} {s['unit']}, on hand {s['on_hand']:g}, short {s['short']:g}{stop}.",
                          f"{s['name']}: нужно {s['need']:g} {s['unit']}, есть {s['on_hand']:g}, не хватает {s['short']:g}{stop_ru}."))
    return {"rows": rows, "lines": lines}


TOOLS = {
    "income": {"fn": t_income, "roles": OWNER,
               "description": "Money for a month: shipments, invoiced, paid, owed, piece-rate wages.",
               "params": {"month": {"type": "string", "description": "YYYY-MM, empty = this month"}}},
    "debtors": {"fn": t_debtors, "roles": OFFICE,
                "description": "Customers who owe money on shipments older than N days.",
                "params": {"min_days": {"type": "integer", "description": "age in days, default 30"}}},
    "late_orders": {"fn": t_late_orders, "roles": OFFICE,
                    "description": "Orders that are late or will be late, where they are stuck and why.",
                    "params": {}},
    "material_need": {"fn": t_material_need, "roles": OFFICE,
                      "description": "One material: on hand, what open work needs, shortage.",
                      "params": {"name": {"type": "string", "description": "material name, e.g. velvet beige"},
                                 "days": {"type": "integer", "description": "horizon in working days"}}},
    "shortages": {"fn": t_shortages, "roles": OFFICE,
                  "description": "All materials that do not cover the open work.", "params": {}},
    "floor_now": {"fn": t_floor_now, "roles": OFFICE,
                  "description": "Queue, done today and capacity per stage; the bottleneck.", "params": {}},
    "order_status": {"fn": t_order_status, "roles": OFFICE,
                     "description": "Where one order is.",
                     "params": {"number": {"type": "integer", "description": "order number"}}},
    "stock_ready": {"fn": t_stock_ready, "roles": OFFICE,
                    "description": "Finished pieces made for stock and not sold yet.", "params": {}},
    "payroll_due": {"fn": t_payroll_due, "roles": OWNER,
                    "description": "Piece-rate money owed to workers, by person.", "params": {}},
}


def run_tool(conn: sqlite3.Connection, role: str, name: str, args: dict) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        raise DomainError(f"No such tool: {name}.")
    if role not in tool["roles"]:
        raise DomainError("Only the owner sees money." if "owner" in tool["roles"] and len(tool["roles"]) == 1
                          else "This question is for the office.")
    clean = {}
    for k, spec in tool["params"].items():
        if k in args and args[k] not in (None, ""):
            clean[k] = int(args[k]) if spec["type"] == "integer" else str(args[k])
    with read_only(conn):
        return tool["fn"](conn, **clean)


# --- offline path -----------------------------------------------------------

def _month_in(text: str, today: date) -> str:
    t = text.lower()
    if re.search(r"last month|прошл\w* месяц", t):
        first = today.replace(day=1) - timedelta(days=1)
        return first.strftime("%Y-%m")
    if m := re.search(r"\b(20\d\d)-(\d\d)\b", t):
        return f"{m[1]}-{m[2]}"
    for i, (en, ru) in enumerate(zip(MONTHS_EN, MONTHS_RU), 1):
        if re.search(rf"\b{en}", t) or re.search(rf"\b{ru}", t):
            year = today.year if i <= today.month else today.year - 1
            return f"{year}-{i:02d}"
    return ""


def route_offline(conn: sqlite3.Connection, text: str) -> tuple[str, dict] | None:
    """Which tool answers this question, by words. None = cannot answer offline."""
    t = text.lower()
    today = date.fromisoformat(domain.today(conn))
    if m := re.search(r"(?:#|order\s*|заказ\w*\s*)(\d{3,})", t):
        return "order_status", {"number": int(m[1])}
    if re.search(r"owe|debt|долж|долг", t) and not re.search(r"worker|people|люд|рабоч|зарплат|wage|salary", t):
        days = re.search(r"(\d+)\s*(?:days|дн|день|дня)", t)
        return "debtors", {"min_days": int(days[1]) if days else 30}
    if re.search(r"wage|salary|payroll|зарплат|рабочим|должны люд", t):
        return "payroll_due", {}
    if re.search(r"earn|income|revenue|made|turnover|зарабо|выручк|доход|оборот|заработ", t):
        return "income", {"month": _month_in(t, today)}
    if re.search(r"late|overdue|behind|опазд|просроч|задерж|не успе", t):
        return "late_orders", {}
    mats = [r["name"] for r in conn.execute("SELECT name FROM materials")]
    hit = next((n for n in sorted(mats, key=len, reverse=True) if n.lower() in t), None)
    if hit is None:  # the demo names materials in English; let Russian words find them
        words = {w for w in re.findall(r"[a-zа-яё]+", t)}
        stems = {en for ru, en in RU_WORDS.items() if any(w.startswith(ru) for w in words)}
        scored = sorted(((sum(1 for s in stems if s in n.lower()), n) for n in mats), reverse=True)
        if scored and scored[0][0] >= 2:
            hit = scored[0][1]
    if hit:
        return "material_need", {"name": hit, "days": 5}
    if re.search(r"short|run out|нехват|кончает|закончил|не хватает", t):
        return "shortages", {}
    if re.search(r"fabric|material|ткан|материал|поролон|foam", t):
        return "shortages", {}
    if re.search(r"in stock|on stock|warehouse|склад|готов", t):
        return "stock_ready", {}
    if re.search(r"queue|bottleneck|floor|stage|цех|очеред|узк|этап", t):
        return "floor_now", {}
    return None


RU_WORDS = {"велюр": "velvet", "букле": "boucle", "лён": "linen", "лен": "linen", "шенилл": "chenille",
            "беж": "beige", "графит": "graphite", "молоч": "milk", "песоч": "sand", "песок": "sand",
            "изумруд": "emerald", "сер": "grey", "пепел": "ash", "поролон": "foam", "каркас": "frame",
            "ножк": "legs", "молни": "zip", "скоб": "staples", "пуф": "pouf", "стул": "chair", "диван": "sofa"}

EXAMPLES = [
    tt("how much did we earn in October?", "сколько заработали в октябре?"),
    tt("who owes us for more than 30 days?", "кто должен нам больше 30 дней?"),
    tt("what is late and why?", "что опаздывает и почему?"),
    tt("how much velvet beige do we need this week?", "сколько нужно велюра беж на этой неделе?"),
    tt("where is order #214?", "где заказ #214?"),
    tt("what is short?", "чего не хватает?"),
]


# --- model path -------------------------------------------------------------

SYSTEM = ("You answer a furniture workshop owner's question using ONLY the tools. Call the tools "
          "you need, then answer in {lang} in 2-5 short sentences. Quote numbers exactly as the "
          "tools returned them and never add numbers of your own; the tool lines are shown to "
          "the person under your answer, so do not repeat every line. Today is {today}.")


def _tool_specs(role: str) -> list[dict]:
    return [{"type": "function", "name": name, "description": t["description"],
             "parameters": {"type": "object", "properties": t["params"], "required": []}}
            for name, t in TOOLS.items() if role in t["roles"]]


def ask_spark(conn: sqlite3.Connection, role: str, text: str, lang: str) -> dict:
    messages = [{"role": "system", "content": SYSTEM.format(
                    lang="Russian" if lang == "ru" else "English", today=domain.today(conn))},
                {"role": "user", "content": text}]
    used, lines = [], []
    for _ in range(4):
        data = models.respond(conn, "ask", messages, tools=_tool_specs(role), max_output_tokens=800)
        calls = [i for i in data.get("output", []) if i.get("type") == "function_call"]
        if not calls:
            break
        for c in calls:
            try:
                args = json.loads(c.get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                result = run_tool(conn, role, c["name"], args)
            except DomainError as exc:
                result = {"error": str(exc), "lines": []}
            used.append(c["name"])
            lines.extend(result.get("lines", []))
            messages.append(c)
            messages.append({"type": "function_call_output", "call_id": c.get("call_id"),
                             "output": json.dumps({k: v for k, v in result.items() if k != "lines"} |
                                                  {"lines": [ln[lang] for ln in result.get("lines", [])]},
                                                  ensure_ascii=False, default=str)})
    from ..intake import _output_text
    answer = _output_text(data).strip()
    if not answer:
        raise BudgetStop("Empty model answer; using the offline path.")
    return {"answer": answer, "lines": lines, "tools": used, "model": models.SPARK}


def ask(conn: sqlite3.Connection, role: str, text: str, lang: str = "en") -> dict:
    text = text.strip()
    if not text:
        raise DomainError("Type the question first.")
    if len(text) > 500:
        raise DomainError("Shorter, please: one question at a time.")
    try:
        return ask_spark(conn, role, text, lang)
    except BudgetStop:
        pass
    routed = route_offline(conn, text)
    if routed is None:
        return {"answer": tt("I can answer questions like these:", "Могу ответить на такие вопросы:")[lang],
                "lines": [], "examples": EXAMPLES, "tools": [], "model": "offline-rules"}
    name, args = routed
    result = run_tool(conn, role, name, args)
    return {"answer": "", "lines": result["lines"], "tools": [name], "model": "offline-rules"}
