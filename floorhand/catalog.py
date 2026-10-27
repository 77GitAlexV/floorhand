"""The catalog by words, instead of a settings screen.

In the workshop ERP the reference/settings screens were opened 3-11 times in
46 days: nobody goes there. So the catalog is changed by one sentence:

    "add chair Bruno, stages sewing and assembly, rate 3, price 160"
    "добавь стул Бруно, этапы шитьё и сборка, ставка 3"
    "add fabric velvet blue" · "add customer Blue Door Cafe, city Austin"
    "rate 5 for Harbor armchair at upholstery"

The sentence becomes a proposal card ("this is what will be created"), every
value with the words it came from; nothing is written until Yes. Muse Spark
reads free phrasing when it is on; simple rules cover the usual phrasing
offline. A stage that is not named gets no rate, which is not 0.
"""
import json
import re
import sqlite3

from . import domain, intake, models
from .domain import DomainError
from .models import BudgetStop

KIND_WORDS = {
    "rocking chair": "rocking chair", "armchair": "armchair", "lounge chair": "armchair",
    "chair": "chair", "pouf": "pouf", "footstool": "pouf", "stool": "chair", "sofa": "sofa",
    "daybed": "daybed", "bench": "bench", "table": "table",
    "кресло-качалка": "rocking chair", "качалка": "rocking chair", "кресло": "armchair",
    "стул": "chair", "пуф": "pouf", "диван": "sofa", "кушетка": "daybed", "банкетка": "bench",
    "стол": "table",
}
STAGE_STEMS = {  # stage code -> stems people use
    "cut": ("cut", "раскр", "крой"), "sew": ("sew", "шит", "шв", "пошив"),
    "uph": ("uph", "обив"), "asm": ("assem", "asm", "сбор"), "pack": ("pack", "упак"),
}
ADD = r"(?:add|create|new|добавь|добавить|заведи|создай)"


def _stages(conn) -> list[sqlite3.Row]:
    return domain.stages(conn)


def _stage_for(conn, word: str) -> sqlite3.Row | None:
    w = word.lower().strip()
    for s in _stages(conn):
        stems = STAGE_STEMS.get(s["code"], ()) + (s["name_en"].lower()[:5], s["name_ru"].lower()[:4])
        if any(w.startswith(x) for x in stems if x):
            return s
    return None


def _quote(source: str, piece: str) -> str | None:
    if not piece:
        return None
    m = re.search(re.escape(piece.strip()), source, re.I)
    return m[0] if m else None


def _parse_offline(conn, text: str) -> dict:
    """The usual phrasings. Returns a raw proposal with quotes."""
    t = text.strip()
    low = t.lower()
    # add fabric / frame
    if m := re.search(ADD + r"\s+(fabric|ткань|frame|каркас|legs|ножки)\s+(.+)$", t, re.I):
        what = "fabric" if m[1].lower() in ("fabric", "ткань") else "frame"
        name = m[2].strip(" .").lower()
        return {"action": f"add_{what}", "name": {"value": name, "quote": m[2].strip(" .")}}
    # add customer
    if m := re.search(ADD + r"\s+(?:customer|client|клиента|клиент|покупателя)\s+([^,;]+)"
                      r"(?:[,;]\s*(?:city|город)\s+([^,;]+))?", t, re.I):
        return {"action": "add_customer", "name": {"value": m[1].strip(" ."), "quote": m[1].strip(" .")},
                "city": {"value": (m[2] or "").strip(" .") or None,
                         "quote": (m[2] or "").strip(" .") or None}}
    # rate N for PRODUCT at STAGE
    if m := re.search(r"(?:rate|ставка)\s+(\d+)\s+(?:for|для)\s+(.+?)\s+(?:at|on|на)\s+(\S+)", t, re.I):
        return {"action": "set_rates", "name": {"value": m[2], "quote": m[2]},
                "stages": [{"stage": {"value": m[3], "quote": m[3]},
                            "rate": {"value": m[1], "quote": m[0]}}]}
    # add product: "add chair Bruno, stages sewing and assembly, rate 3, price 160"
    m = re.search(ADD + r"\s+(.+)$", t, re.I | re.S)
    if not m:
        raise DomainError("Start with “add …”: add chair Bruno, stages sewing and assembly, "
                          "rate 3, price 160 — or add fabric …, add customer …")
    parts = [p.strip() for p in re.split(r"[,;]\s*|\.\s+", m[1]) if p.strip()]
    head = parts[0]
    kind, name = None, head
    for word in sorted(KIND_WORDS, key=len, reverse=True):
        if re.match(rf"(?:an?\s+)?{re.escape(word)}\b", head, re.I):
            kind = KIND_WORDS[word]
            name = re.sub(rf"^(?:an?\s+)?{re.escape(word)}\s*", "", head, flags=re.I).strip(" «»\"'")
            break
    if not name:
        raise DomainError("Say the name: add chair Bruno, …")
    out = {"action": "add_product", "name": {"value": name, "quote": _quote(t, name)},
           "kind": {"value": kind, "quote": _quote(t, head.split()[0]) if kind else None},
           "price": {"value": None, "quote": None}, "stages": []}
    named, rate_all, rate_list = [], None, []
    for p in parts[1:]:
        pl = p.lower()
        if m2 := re.search(r"(?:price|цена|sells? (?:for|at))\s*[:=]?\s*\$?(\d+)", pl):
            out["price"] = {"value": m2[1], "quote": _quote(t, p)}
            continue
        if m2 := re.search(r"(?:rates?|ставк[аи]|расценк[аи])\s*[:=]?\s*([\d/\s]+)", pl):
            nums = re.findall(r"\d+", m2[1])
            if len(nums) == 1:
                rate_all = (nums[0], _quote(t, p))
            else:
                rate_list = [(n, _quote(t, p)) for n in nums]
            continue
        if re.fullmatch(r"(?:all|every|все|всех)\s+(?:stages?|steps?|этап\w*)", pl):
            named += [(s["name_en"], s["name_en"], None) for s in _stages(conn)]
            continue
        if re.match(r"(?:stages?|этап[ыа]?|steps?)\b", pl) or _stage_for(conn, pl.split()[0]):
            body = re.sub(r"^(?:stages?|этап[ыа]?|steps?)\s*[:=]?\s*", "", p, flags=re.I)
            if re.search(r"\b(?:all|все)\b", body, re.I):
                named += [(s["name_en"], body, None) for s in _stages(conn)]
                continue
            for piece in re.split(r"\s+(?:and|и)\s+|\s*\+\s*|/|\s{2,}", body):
                piece = piece.strip()
                if not piece:
                    continue
                words = piece.split()
                inline = next((w for w in words if w.isdigit()), None)
                named.append((words[0], piece, inline))
    for i, (word, quote, inline) in enumerate(named):
        rate = (inline, _quote(t, quote)) if inline else (
            rate_list[i] if i < len(rate_list) else (rate_all or (None, None)))
        out["stages"].append({"stage": {"value": word, "quote": _quote(t, word)},
                              "rate": {"value": rate[0], "quote": rate[1]}})
    return out


PROMPT = """You turn one sentence from a furniture workshop owner into a catalog change.
Return only JSON. Copy every "quote" character for character from the sentence; if the
sentence does not say it, leave value and quote empty. Never guess numbers.
Shapes:
{"action": "add_product", "name": {"value": "", "quote": ""}, "kind": {...}, "price": {...},
 "stages": [{"stage": {"value": "<stage name>", "quote": ""}, "rate": {"value": "", "quote": ""}}]}
{"action": "set_rates", "name": {...product...}, "stages": [...]}
{"action": "add_fabric" | "add_frame", "name": {...}}
{"action": "add_customer", "name": {...}, "city": {...}}
Stages of this shop: {stages}. Kinds: armchair, chair, rocking chair, pouf, sofa, daybed.
Sentence:
<<<
{text}
>>>"""


def _parse_spark(conn, text: str) -> dict:
    stages = ", ".join(f"{s['name_en']} ({s['name_ru']})" for s in _stages(conn))
    data = models.respond(conn, "catalog", PROMPT.replace("{stages}", stages)
                          .replace("{text}", text), max_output_tokens=800)
    m = re.search(r"\{.*\}", intake._output_text(data), re.S)
    if not m:
        raise BudgetStop("Model answer had no JSON; using the offline rules.")
    return json.loads(m[0])


def _ground(conn, text: str, raw: dict) -> dict:
    """Quotes must be in the sentence, stages must exist, numbers must be numbers.
    Anything that fails is left empty and asked."""
    def take(f):
        f = f or {}
        v, q = f.get("value"), f.get("quote")
        if q and q.lower() not in text.lower():
            return None, None
        return v, q
    action = raw.get("action")
    if action not in ("add_product", "set_rates", "add_fabric", "add_frame", "add_customer"):
        raise DomainError("I did not understand what to add. Example: add chair Bruno, stages "
                          "sewing and assembly, rate 3, price 160.")
    name, nq = take(raw.get("name"))
    if not name:
        raise DomainError("Say the name of what to add.")
    out = {"action": action, "name": {"value": str(name).strip(), "quote": nq}, "questions": []}
    if action in ("add_fabric", "add_frame"):
        key = "fabrics" if action == "add_fabric" else "frames"
        have = json.loads(conn.execute("SELECT value FROM settings WHERE key = ?",
                                       (key,)).fetchone()["value"])
        if out["name"]["value"].lower() in (h.lower() for h in have):
            raise DomainError(f"“{out['name']['value']}” is already in the catalog.")
        return out
    if action == "add_customer":
        city, cq = take(raw.get("city"))
        out["city"] = {"value": city, "quote": cq}
        cat = intake._catalog(conn)
        if any(intake.norm(n) == intake.norm(name) for n in cat["customer"].values()):
            raise DomainError(f"Customer “{name}” already exists.")
        return out
    product_id = None
    if action == "set_rates" or action == "add_product":
        row = conn.execute("SELECT id, name, price FROM products WHERE lower(name) = lower(?)",
                           (out["name"]["value"],)).fetchone()
        if row is None and action == "set_rates":
            pid, opts = intake._match("product", out["name"]["value"], intake._catalog(conn))
            if pid is None:
                raise DomainError(f"No product “{name}”. Closest: "
                                  + (", ".join(o["label"] for o in opts) or "none") + ".")
            row = conn.execute("SELECT id, name, price FROM products WHERE id = ?",
                               (pid,)).fetchone()
        if row is not None:  # adding something that exists = changing its rates
            product_id = row["id"]
            out["action"] = "set_rates"
            out["name"]["value"] = row["name"]
            out["old_price"] = row["price"]
    out["product_id"] = product_id
    if out["action"] == "add_product":
        kind, kq = take(raw.get("kind"))
        out["kind"] = {"value": kind or "other", "quote": kq}
        words = set(intake.norm(out["name"]["value"]).split())
        out["similar"] = [n for n in intake._catalog(conn)["product"].values()
                          if words & set(intake.norm(n).split())][:3]
    price, pq = take(raw.get("price"))
    try:
        price = int(price) if price not in (None, "") else None
    except ValueError:
        price = None
    out["price"] = {"value": price, "quote": pq}
    if out["action"] == "add_product" and price is None:
        out["questions"].append("price")
    rates, seen = [], set()
    for s in raw.get("stages") or []:
        sv, sq = take(s.get("stage"))
        st = _stage_for(conn, str(sv or "")) or next(
            (x for x in _stages(conn) if str(sv or "").lower() in
             (x["name_en"].lower(), x["name_ru"].lower())), None)
        if st is None or st["id"] in seen:
            continue
        seen.add(st["id"])
        rv, rq = take(s.get("rate"))
        try:
            amount = int(rv) if rv not in (None, "") else None
        except ValueError:
            amount = None
        old = conn.execute("SELECT amount FROM rates WHERE product_id = ? AND stage_id = ?",
                           (product_id, st["id"])).fetchone() if product_id else None
        rates.append({"stage_id": st["id"], "name_en": st["name_en"], "name_ru": st["name_ru"],
                      "amount": amount, "quote": rq or sq, "old": old["amount"] if old else None})
    out["rates"] = rates
    if any(r["amount"] is None for r in rates):
        out["questions"].append("rates")
    named = {r["stage_id"] for r in rates}
    out["no_rate"] = [{"stage_id": s["id"], "name_en": s["name_en"], "name_ru": s["name_ru"]}
                      for s in _stages(conn) if s["id"] not in named]
    return out


def _card(p: dict, cur: str) -> str:
    """The proposal in one or two plain sentences."""
    a = p["action"]
    if a == "add_fabric":
        return f"Add fabric “{p['name']['value']}” to the catalog."
    if a == "add_frame":
        return f"Add frame “{p['name']['value']}” to the catalog."
    if a == "add_customer":
        city = p.get("city", {}).get("value")
        return f"Add customer “{p['name']['value']}”" + (f", {city}." if city else ".")
    rates = ", ".join(f"{r['name_en']} {cur}{r['amount']}" if r["amount"] is not None
                      else f"{r['name_en']} (rate?)" for r in p["rates"])
    if a == "add_product":
        price = (f"sells for {cur}{p['price']['value']}" if p["price"]["value"] is not None
                 else "price not given: type it below")
        text = f"Add {p['kind']['value']} “{p['name']['value']}”, {price}."
        if rates:
            text += f" Rates: {rates}."
    else:
        text = f"Change {p['name']['value']}: " + (rates or "no rates named") + "."
        if p["price"]["value"] is not None:
            text += f" Price {cur}{p.get('old_price')} → {cur}{p['price']['value']}."
    if p.get("similar"):
        text += (f" Careful: {', '.join('“' + n + '”' for n in p['similar'])} already exists; "
                 "to change it, say “rate … for <name> at <stage>”.")
    if p.get("no_rate") and a in ("add_product",):
        text += (" No rate yet (not 0) for " + ", ".join(s["name_en"] for s in p["no_rate"])
                 + ": every item still passes the whole line, and workers there see "
                   "“price pending” until a rate is set.")
    return text


def propose(conn: sqlite3.Connection, person_id: int, text: str) -> dict:
    who = domain.person(conn, person_id)
    if who["role"] not in ("manager", "owner"):
        raise DomainError("Only the office changes the catalog.")
    text = (text or "").strip()
    if not text:
        raise DomainError("Write what to add, e.g. “add chair Bruno, stages sewing and "
                          "assembly, rate 3, price 160”.")
    if len(text) > 500:
        raise DomainError("One change per sentence, please.")
    try:
        raw, model = _parse_spark(conn, text), models.SPARK
    except BudgetStop:
        raw, model = _parse_offline(conn, text), "offline-rules"
    p = _ground(conn, text, raw)
    p["source"] = text
    p["card"] = _card(p, domain.currency(conn))
    cur = conn.execute("INSERT INTO agent_actions (kind, payload, model, created_at, for_person) "
                       "VALUES ('catalog', ?, ?, ?, ?)",
                       (json.dumps(p), model, domain.now(conn).isoformat(timespec="seconds"),
                        person_id))
    conn.commit()
    return {"id": cur.lastrowid, "model": model, **p}


def apply(conn: sqlite3.Connection, person_id: int, action_id: int, price: int | None = None,
          rates: dict[str, int] | None = None) -> dict:
    """Write exactly what the card showed. The person may fill the asked
    values (price, missing rates), nothing else."""
    who = domain.person(conn, person_id)
    if who["role"] not in ("manager", "owner"):
        raise DomainError("Only the office changes the catalog.")
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind = 'catalog'",
                       (action_id,)).fetchone()
    if row is None:
        raise DomainError("No such proposal.")
    if row["status"] != "proposed":
        raise DomainError("This proposal is already closed.")
    p = json.loads(row["payload"])
    a = p["action"]
    if price is not None:
        if a not in ("add_product", "set_rates") or price < 0:
            raise DomainError("A price fits only a product, and it cannot be negative.")
        p["price"]["value"] = int(price)
    for sid, amount in (rates or {}).items():
        r = next((x for x in p.get("rates", []) if str(x["stage_id"]) == str(sid)), None)
        if r is None or r["amount"] is not None:
            raise DomainError("Only the rates the card asked for can be filled in.")
        if int(amount) < 0:
            raise DomainError("A rate cannot be negative.")
        r["amount"] = int(amount)
    if a == "add_product" and p["price"]["value"] is None:
        raise DomainError("Type the price first: what the product sells for.")
    if any(r["amount"] is None for r in p.get("rates", [])):
        raise DomainError("Some rates are missing: type them, or say the sentence again "
                          "without those stages.")
    name = p["name"]["value"]
    if a in ("add_fabric", "add_frame"):
        key = "fabrics" if a == "add_fabric" else "frames"
        have = json.loads(conn.execute("SELECT value FROM settings WHERE key = ?",
                                       (key,)).fetchone()["value"])
        if name.lower() not in (h.lower() for h in have):
            have.append(name)
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps(have), key))
        msg = f"Added {key[:-1]} “{name}”."
    elif a == "add_customer":
        conn.execute("INSERT INTO customers (name, city) VALUES (?, ?)",
                     (name, (p.get("city") or {}).get("value")))
        msg = f"Added customer “{name}”."
    else:
        pid = p.get("product_id")
        if a == "add_product":
            if conn.execute("SELECT 1 FROM products WHERE lower(name) = lower(?)",
                            (name,)).fetchone():
                raise DomainError(f"“{name}” was added meanwhile. Say the sentence again.")
            pid = conn.execute("INSERT INTO products (name, kind, price) VALUES (?, ?, ?)",
                               (name, p["kind"]["value"], p["price"]["value"])).lastrowid
            msg = f"Added {p['kind']['value']} “{name}”."
        else:
            if p["price"]["value"] is not None:
                conn.execute("UPDATE products SET price = ? WHERE id = ?",
                             (p["price"]["value"], pid))
            msg = f"Changed “{name}”."
        for r in p.get("rates", []):
            conn.execute("INSERT OR REPLACE INTO rates (product_id, stage_id, amount) "
                         "VALUES (?, ?, ?)", (pid, r["stage_id"], r["amount"]))
        if p.get("rates"):
            cur = domain.currency(conn)
            msg += " Rates: " + ", ".join(f"{r['name_en']} {cur}{r['amount']}"
                                          for r in p["rates"]) + "."
    conn.execute("UPDATE agent_actions SET payload = ?, status = 'applied', decided_by = ?, "
                 "decided_at = ?, result = ? WHERE id = ?",
                 (json.dumps(p), person_id, domain.now(conn).isoformat(timespec="seconds"),
                  json.dumps({"message": msg}), action_id))
    conn.commit()
    return {"ok": True, "message": msg}
