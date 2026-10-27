"""Orders from whatever the customer sent: an e-mail, a chat message, a table.

1. extract  - Muse Spark (or the offline rules when the model is off) pulls
              candidate orders; every field carries a quote from the source.
2. ground   - the server checks each quote is really in the source (no quote,
              no field), matches names against the catalog and learned
              synonyms, and decides per field: ok / check (with 1-3 options).
              Confidence comes from these signals, never from the model's
              opinion of itself.
3. propose  - the result is stored as an agent action. Nothing else is written.
4. apply    - a person presses "Create": the server re-reads the stored
              proposal by id, applies the person's picks (only from the
              offered options), creates orders and remembers corrections as
              synonyms.
"""
import csv
import difflib
import io
import json
import re
import sqlite3
from datetime import date, timedelta

from . import domain, models
from .domain import DomainError
from .models import BudgetStop

REQUIRED = ("product", "qty", "customer")
STOCK = "stock"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                "pair": 2, "couple": 2}
STOCK_WORDS = ("to stock", "for stock", "for the warehouse", "на склад")


# --- catalog --------------------------------------------------------------

def norm(text: str) -> str:
    words = re.findall(r"[a-zа-яё0-9]+", text.lower().replace("-", " "))
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
                    for w in words)


def _catalog(conn: sqlite3.Connection) -> dict:
    get = lambda key: json.loads(conn.execute(  # noqa: E731
        "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()["value"])
    products = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM products")}
    customers = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM customers")}
    syn = {"product": {}, "customer": {}}
    for r in conn.execute("SELECT kind, alias, target_id FROM synonyms"):
        syn[r["kind"]][r["alias"]] = r["target_id"]
    return {"product": products, "customer": customers, "synonyms": syn,
            "fabrics": get("fabrics"), "frames": get("frames")}


def _match(kind: str, text: str, cat: dict) -> tuple[int | None, list[dict]]:
    """(id if certain, options otherwise). Certain = exact name, learned
    synonym, or the only catalog name whose words all appear in the text."""
    names = cat[kind]
    t = norm(text)
    if not t:
        return None, []
    if t in cat["synonyms"][kind]:
        return cat["synonyms"][kind][t], []
    exact = [i for i, n in names.items() if norm(n) == t]
    if exact:
        return exact[0], []
    tw = set(t.split())
    contained = [i for i, n in names.items() if set(norm(n).split()) <= tw]
    if len(contained) == 1:
        return contained[0], []
    if len(contained) > 1:  # "lord rocking chair" contains "lord ... chair" too: longest wins
        best = max(contained, key=lambda i: len(norm(names[i]).split()))
        if sum(len(norm(names[i]).split()) == len(norm(names[best]).split())
               for i in contained) == 1:
            return best, []
    # Not certain: offer the closest names. Names sharing a real word with the
    # text ("pouf", "lord") go first and push out mere look-alikes; digits do
    # not count as shared ("3 poufs" is not "3-seat").
    words = {w for w in tw if not w.isdigit()}
    scored = []
    for i, n in names.items():
        shared = len(set(norm(n).split()) & words)
        ratio = difflib.SequenceMatcher(None, t, norm(n)).ratio()
        if shared or ratio > 0.6:
            scored.append((shared, ratio, i))
    if any(s[0] for s in scored):
        scored = [s for s in scored if s[0]]
    scored.sort(reverse=True)
    return None, [{"id": i, "label": names[i]} for _, _, i in scored[:3]]


def _variant(text: str, cat: dict) -> dict:
    t = set(norm(text).split())
    fabrics = [f for f in cat["fabrics"] if set(norm(f).split()) <= t]
    frames = [f for f in cat["frames"] if norm(f) in t]
    partial = [f for f in cat["fabrics"] if set(norm(f).split()) & t]
    return {"fabric": fabrics[0] if len(fabrics) == 1 else None,
            "fabric_options": fabrics if len(fabrics) > 1 else ([] if fabrics else partial[:3]),
            "frame": frames[0] if len(frames) == 1 else None}


def _parse_date(text: str, today: date) -> date | None:
    t = text.lower()
    if m := re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\b", t)
    m2 = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    day = month = None
    if m and m[2] in MONTHS:
        day, month = int(m[1]), MONTHS[m[2]]
    elif m2 and m2[1] in MONTHS:
        day, month = int(m2[2]), MONTHS[m2[1]]
    if not month:
        return None
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    return d if d >= today - timedelta(days=30) else d.replace(year=today.year + 1)


# --- offline extraction (no model) ---------------------------------------

HEADER_WORDS = {
    "product": ("product", "item", "model", "article", "изделие", "модель", "товар"),
    "qty": ("qty", "quantity", "pcs", "count", "amount", "кол", "шт"),
    "customer": ("customer", "client", "store", "shop", "buyer", "клиент", "покупатель"),
    "due_date": ("due", "deadline", "date", "ship by", "срок", "дата"),
    "variant": ("fabric", "color", "colour", "variant", "upholstery", "ткань", "цвет"),
    "frame": ("frame", "legs", "wood", "каркас", "ножки"),
}


def _field(value, quote) -> dict:
    return {"value": value, "quote": quote}


def _table(source: str) -> list[dict] | None:
    lines = [ln for ln in source.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    delim = max("\t;,|", key=lines[0].count)
    if lines[0].count(delim) < 1:
        return None
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    cols = {}
    for idx, h in enumerate(rows[0]):  # columns by header, never by position
        h = h.strip().lower()
        for key, words in HEADER_WORDS.items():
            if key not in cols and any(w in h for w in words):
                cols[key] = idx
                break
    if "product" not in cols:
        return None
    out = []
    for row in rows[1:]:
        cell = lambda k: row[cols[k]].strip() if k in cols and cols[k] < len(row) else ""  # noqa
        if not cell("product"):
            continue
        variant = " ".join(x for x in (cell("variant"), cell("frame")) if x)
        out.append({"product": _field(cell("product"), cell("product")),
                    "qty": _field(cell("qty"), cell("qty")),
                    "customer": _field(cell("customer"), cell("customer")),
                    "due_date": _field(cell("due_date"), cell("due_date")),
                    "variant": _field(variant, variant),
                    "line": delim.join(row)})
    return out


def _mentions_product(text: str, cat: dict) -> bool:
    t = set(norm(text).split())
    kinds = {"armchair", "chair", "pouf", "sofa", "daybed", "footstool", "stool", "rocker"}
    if t & kinds:
        return True
    return any(set(norm(n).split()) <= t for n in cat["product"].values()) or \
        any(a in norm(text) for a in cat["synonyms"]["product"])


def _qty(clause: str) -> tuple[str | None, str | None]:
    """(value, quote) of the amount in a clause, ignoring numbers in dates
    and in product names like '3-seat'."""
    cleaned = re.sub(r"\b\d+[- ]seat(?:er)?\b|\b20\d\d-\d\d-\d\d\b", " ", clause, flags=re.I)
    cleaned = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,}\.?(?=\s|$)", lambda m:
                     m[0] if m[0].split()[-1][:3].lower() not in MONTHS else " ", cleaned)
    if m := re.search(r"(?:x|×)\s*(\d{1,3})\b|\b(\d{1,3})\s*(?:x|×|pcs|pieces|units|шт)\b",
                      cleaned, re.I):
        return m[1] or m[2], m[0]
    if m := re.search(r"\b(\d{1,3})\b", cleaned):
        return m[1], m[0]
    for w in re.findall(r"[a-z]+", clause.lower()):
        if w in WORD_NUMBERS:
            q = re.search(rf"\b{w}\b", clause, re.I)
            return str(WORD_NUMBERS[w]), q[0]
    return None, None


def _text(source: str, cat: dict, today: date) -> list[dict]:
    # Customer and due date usually sit once in the message, not per line.
    customer = None
    low = norm(source)
    for i, n in cat["customer"].items():
        if norm(n) and norm(n) in low:
            m = re.search(re.escape(n), source, re.I)
            customer = _field(n, m[0] if m else n)
            break
    if customer is None:
        for alias in cat["synonyms"]["customer"]:
            if alias and alias in low:
                m = re.search(r"\b" + r"\W+".join(map(re.escape, alias.split())), source, re.I)
                if m:
                    customer = _field(m[0], m[0])
                    break
    if customer is None and any(w in source.lower() for w in STOCK_WORDS):
        w = next(w for w in STOCK_WORDS if w in source.lower())
        customer = _field(STOCK, source[source.lower().index(w):][:len(w)])
    due = None
    for m in re.finditer(r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,}\b|"
                         r"\b[A-Za-z]{3,}\.?\s+\d{1,2}(?:st|nd|rd|th)?\b", source):
        d = _parse_date(m[0], today)
        if d:
            due = _field(d.isoformat(), m[0])
            break

    clauses = []
    for line in source.splitlines():
        for part in re.split(r";|,\s+|\s+and\s+|\s+plus\s+|\.\s+", line):
            if not part.strip():
                continue
            if _mentions_product(part, cat) or not clauses:
                clauses.append(part.strip())
            else:  # "in velvet beige", "walnut legs" belong to the previous item
                clauses[-1] = clauses[-1] + " | " + part.strip()
    out = []
    for clause in clauses:
        if not _mentions_product(clause, cat):
            continue
        first = clause.split(" | ")[0]
        q, qq = _qty(first)
        v = _variant(clause.replace(" | ", " "), cat)
        variant_text = " ".join(x for x in (v["fabric"], v["frame"]) if x)
        out.append({"product": _field(first, first), "qty": _field(q, qq),
                    "customer": customer or _field(None, None),
                    "due_date": due or _field(None, None),
                    "variant": _field(variant_text, None), "line": clause.replace(" | ", ", ")})
    return out


def _extract_offline(conn: sqlite3.Connection, source: str) -> list[dict]:
    cat = _catalog(conn)
    return _table(source) or _text(source, cat, date.fromisoformat(domain.today(conn)))


# --- model extraction -----------------------------------------------------

PROMPT = """You turn a customer's message or table into furniture orders for a workshop.
Return only JSON: {"orders": [{"product": {"value": "", "quote": ""}, "qty": {...},
"customer": {...}, "due_date": {...}, "variant": {...}}]}
Rules:
- "quote" is copied character for character from the source. If the source does not say it,
  leave both value and quote empty. Never guess.
- product: the closest catalog product name as value. qty: a whole number.
- customer: catalog name if it is one of them, otherwise the name as written; "stock" if
  the message says it is for stock.
- due_date: YYYY-MM-DD. Today is {today}.
- variant: fabric and frame as written in the catalog lists, separated by a space.
Catalog products: {products}
Known customers: {customers}
Fabrics: {fabrics}
Frames: {frames}
Source:
<<<
{source}
>>>"""


def _output_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    parts = []
    for item in data.get("output", []):
        for c in item.get("content", []) or []:
            if c.get("text"):
                parts.append(c["text"])
    return "".join(parts)


def _extract_with_spark(conn: sqlite3.Connection, source: str) -> list[dict]:
    cat = _catalog(conn)
    prompt = (PROMPT.replace("{today}", domain.today(conn))
              .replace("{products}", "; ".join(cat["product"].values()))
              .replace("{customers}", "; ".join(cat["customer"].values()))
              .replace("{fabrics}", "; ".join(cat["fabrics"]))
              .replace("{frames}", "; ".join(cat["frames"]))
              .replace("{source}", source))
    data = models.respond(conn, "intake", prompt, max_output_tokens=3000)
    text = _output_text(data)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise BudgetStop("Model answer had no JSON; using the offline path.")
    orders = json.loads(m[0]).get("orders", [])
    return [{k: _field((o.get(k) or {}).get("value") or None, (o.get(k) or {}).get("quote") or None)
             for k in ("product", "qty", "customer", "due_date", "variant")} for o in orders]


# --- grounding ------------------------------------------------------------

def _in_source(quote: str | None, source: str) -> bool:
    return bool(quote) and quote.strip().lower() in source.lower()


def ground(conn: sqlite3.Connection, source: str, raw: dict, cat: dict, today: date) -> dict:
    """Turn one raw candidate into fields with a status each."""
    out = {}

    def take(name):
        f = raw.get(name) or {}
        value, quote = f.get("value"), f.get("quote")
        if quote and not _in_source(quote, source):
            return None, None, "The quoted text is not in the source, so this field is left empty."
        return value, quote, ""

    # product
    value, quote, note = take("product")
    pid, options = _match("product", value or quote or "", cat) if (value or quote) else (None, [])
    out["product"] = {"id": pid, "value": cat["product"].get(pid) if pid else (value or ""),
                      "quote": quote, "status": "ok" if pid else ("check" if options else "empty"),
                      "options": options, "note": note}

    # qty
    value, quote, note = take("qty")
    try:
        n = int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        n = None
    if n and 0 < n <= 500 and quote:
        out["qty"] = {"id": n, "value": n, "quote": quote, "status": "ok", "options": [], "note": ""}
    else:
        out["qty"] = {"id": None, "value": n or 1, "quote": quote, "status": "check",
                      "options": [{"id": k, "label": str(k)} for k in (1, 2, 3)],
                      "note": note or "The amount is not written; 1 is a guess."}

    # customer
    value, quote, note = take("customer")
    if value == STOCK or (quote and quote.lower() in STOCK_WORDS):
        out["customer"] = {"id": STOCK, "value": "to stock", "quote": quote, "status": "ok",
                           "options": [], "note": ""}
    else:
        cid, options = _match("customer", value or quote or "", cat) if (value or quote) \
            else (None, [])
        if cid:
            out["customer"] = {"id": cid, "value": cat["customer"][cid], "quote": quote,
                               "status": "ok", "options": [], "note": note}
        else:
            if value and quote:
                options = options[:2] + [{"id": "new:" + value, "label": f"New customer: {value}"}]
            options.append({"id": STOCK, "label": "To stock"})
            out["customer"] = {"id": None, "value": value or "", "quote": quote,
                               "status": "check", "options": options,
                               "note": note or ("Not a known customer." if value
                                                else "The message does not say who it is for.")}

    # due date (optional)
    value, quote, note = take("due_date")
    d = None
    if value:
        try:
            d = date.fromisoformat(str(value))
        except ValueError:
            d = _parse_date(str(value), today)
    if d is None and quote:
        d = _parse_date(quote, today)
    if d and today - timedelta(days=1) <= d <= today + timedelta(days=365):
        out["due_date"] = {"id": d.isoformat(), "value": d.isoformat(), "quote": quote,
                           "status": "ok", "options": [], "note": ""}
    elif value or quote:
        out["due_date"] = {"id": None, "value": str(value or quote), "quote": quote,
                           "status": "check", "options": [], "note": "Date looks wrong; type it."}
    else:
        out["due_date"] = {"id": None, "value": "", "quote": None, "status": "empty",
                           "options": [], "note": note}

    # variant (optional, but a half-known fabric must be checked)
    value, quote, note = take("variant")
    v = _variant(value or quote or "", cat) if (value or quote) else \
        {"fabric": None, "fabric_options": [], "frame": None}
    text = " · ".join(x for x in (v["fabric"], v["frame"]) if x)
    if v["fabric_options"] and not v["fabric"]:
        out["variant"] = {"id": None, "value": value or "", "quote": quote, "status": "check",
                          "options": [{"id": f + (" · " + v["frame"] if v["frame"] else ""),
                                       "label": f} for f in v["fabric_options"]],
                          "note": "Which fabric exactly?"}
    else:
        out["variant"] = {"id": text, "value": text, "quote": quote,
                          "status": "ok" if text else "empty", "options": [], "note": note}

    ready = all(out[k]["status"] == "ok" for k in REQUIRED) and \
        all(out[k]["status"] != "check" for k in ("due_date", "variant"))
    return {"fields": out, "line": raw.get("line", ""), "ready": ready, "order_id": None}


# --- propose / apply ------------------------------------------------------

def propose(conn: sqlite3.Connection, person_id: int, source: str) -> dict:
    source = source.strip()
    if not source:
        raise DomainError("Paste the message or the table first.")
    if len(source) > 20000:
        raise DomainError("That is too long for one go. Split it into parts.")
    try:
        raw, model = _extract_with_spark(conn, source), models.SPARK
    except BudgetStop:
        raw, model = _extract_offline(conn, source), "offline-rules"
    cat = _catalog(conn)
    today = date.fromisoformat(domain.today(conn))
    items = [ground(conn, source, r, cat, today) for r in raw]
    payload = {"source": source, "items": items}
    cur = conn.execute(
        "INSERT INTO agent_actions (kind, payload, model, created_at, for_person) "
        "VALUES ('intake', ?, ?, ?, ?)",
        (json.dumps(payload), model, domain.now(conn).isoformat(timespec="seconds"), person_id))
    conn.commit()
    return {"id": cur.lastrowid, "model": model, **payload}


def apply(conn: sqlite3.Connection, person_id: int, action_id: int,
          picks: dict[str, dict[str, object]]) -> dict:
    """Create the orders a person confirmed. picks = {item index: {field: option id}};
    a pick must be one of the options the proposal offered (or a typed date)."""
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind = 'intake'",
                       (action_id,)).fetchone()
    if row is None:
        raise DomainError("No such proposal.")
    if row["status"] != "proposed":
        raise DomainError("This proposal is already closed.")
    payload = json.loads(row["payload"])
    created, waiting = [], 0
    for idx, item in enumerate(payload["items"]):
        if item.get("order_id"):
            continue
        f = item["fields"]
        for field, choice in (picks.get(str(idx)) or {}).items():
            if field not in f or f[field]["status"] != "check":
                continue
            if field == "due_date":
                try:
                    d = date.fromisoformat(str(choice))
                except ValueError:
                    raise DomainError(f"Order {idx + 1}: date must look like 2026-11-05.")
                f[field].update(id=d.isoformat(), value=d.isoformat(), status="ok", learned=False)
                continue
            allowed = {str(o["id"]): o for o in f[field]["options"]}
            if str(choice) not in allowed:
                raise DomainError(f"Order {idx + 1}: pick one of the offered options.")
            opt = allowed[str(choice)]
            f[field].update(id=opt["id"], value=opt["label"], status="ok", learned=True)
        if not (all(f[k]["status"] == "ok" for k in REQUIRED)
                and all(f[k]["status"] != "check" for k in ("due_date", "variant"))):
            waiting += 1
            continue
        customer_id = f["customer"]["id"]
        if customer_id == STOCK:
            customer_id = None
        elif isinstance(customer_id, str) and customer_id.startswith("new:"):
            customer_id = conn.execute("INSERT INTO customers (name) VALUES (?)",
                                       (customer_id[4:].strip(),)).lastrowid
        oid = domain.create_order(conn, product_id=int(f["product"]["id"]),
                                  qty=int(f["qty"]["id"]), customer_id=customer_id,
                                  due_date=f["due_date"]["id"], variant=f["variant"]["id"] or "",
                                  source="agent", commit=False)
        item["order_id"] = oid
        created.append(oid)
        when = domain.now(conn).isoformat(timespec="seconds")
        for kind in ("product", "customer"):  # the person's correction becomes a synonym
            fk = f[kind]
            if fk.get("learned") and fk.get("quote") and isinstance(fk["id"], int):
                conn.execute("INSERT OR REPLACE INTO synonyms VALUES (?, ?, ?, ?)",
                             (kind, norm(fk["quote"]), fk["id"], when))
    status = "applied" if waiting == 0 else "proposed"
    conn.execute("UPDATE agent_actions SET payload = ?, status = ?, decided_by = ?, "
                 "decided_at = ?, result = ? WHERE id = ?",
                 (json.dumps(payload), status, person_id,
                  domain.now(conn).isoformat(timespec="seconds"),
                  json.dumps({"created": created, "waiting": waiting}), action_id))
    conn.commit()
    numbers = [conn.execute("SELECT number FROM orders WHERE id = ?", (o,)).fetchone()[0]
               for o in created]
    return {"ok": True, "created": numbers, "waiting": waiting,
            "message": (f"Created {len(numbers)} order(s): " + ", ".join(f"#{n}" for n in numbers)
                        if numbers else "Nothing created yet.")
            + (f" {waiting} still to check." if waiting else "")}
