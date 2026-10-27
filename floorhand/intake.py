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
WORD_NUMBERS.update(dict(zip(
    "один одна одно два две три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать двадцать тридцать пара пару".split(),
    (1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 30, 2, 2))))
WORD_NUMBERS.update({"thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                     "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30})
STOCK_WORDS = ("to stock", "for stock", "for the warehouse", "на склад")

# Only the fictional demo catalog is translated; unknown names stay questions.
RU_WORDS = {
    "cozy": "кози", "harbor": "харбор", "nordic": "нордик", "bruno": "бруно",
    "oslo": "осло", "lina": "лина", "bunny": "банни",
    "armchair": "кресло кресла кресел креслу креслы", "chair": "стул стула стулья стульев",
    "rocking": "качалка качалки качалок", "sofa": "диван дивана диваны диванов",
    "daybed": "кушетка кушетки кушеток", "pouf": "пуф пуфа пуфы пуфов",
    "round": "круглый круглые круглых круглого", "square": "квадратный квадратные квадратных",
    "kids": "детское детские детских детский", "footstool": "подставка подставки подставок",
    "velvet": "велюр велюра бархат бархата", "boucle": "букле", "linen": "лен лён льна",
    "chenille": "шенилл шенилла", "beige": "бежевый бежевая бежевое бежевые беж",
    "graphite": "графит графитовый графитовая", "milk": "молоко молочный молочная молочное",
    "sand": "песочный песочная песочное", "emerald": "изумруд изумрудный изумрудная",
    "grey": "серый серая серое", "ash": "пепельный пепельная пепельное",
    "walnut": "орех ореховый ореховые", "oak": "дуб дубовый дубовые",
    "black": "черный чёрный черные чёрные", "natural": "натуральный натуральные натуральное",
}
RU_TOKENS = {ru: en for en, words in RU_WORDS.items() for ru in words.split()}
RU_PRODUCTS = {"Nordic lounge chair": ("кресло Нордик",),
               "Harbor rocking chair": ("качалка Харбор", "кресло качалка Харбор"),
               "Footstool": ("подставка для ног",)}


# --- catalog --------------------------------------------------------------

def norm(text: str) -> str:
    text = re.sub(r"(\d+)\s*[- ]местн[а-яё]*", r"\1 seat", text.lower())
    words = [RU_TOKENS.get(w, w) for w in re.findall(r"[a-zа-яё0-9]+", text.replace("-", " "))]
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
    labels = {i: [norm(n)] + ([norm(a) for a in RU_PRODUCTS.get(n, ())] if kind == "product" else [])
              for i, n in names.items()}
    exact = [i for i in names if t in labels[i]]
    if exact:
        return exact[0], []
    tw = set(t.split())
    contained = [i for i in names if any(set(label.split()) <= tw for label in labels[i])]
    if len(contained) == 1:
        return contained[0], []
    # Several complete names are competing evidence, not permission to pick one.
    if len(contained) > 1:
        return None, [{"id": i, "label": names[i]} for i in contained[:3]]
    # Not certain: offer the closest names. Names sharing a real word with the
    # text ("pouf", "harbor") go first and push out mere look-alikes; digits do
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
    unknown = bool(re.search(r"\b(?:fabric|upholstery|ткань|обивка)\s*[:=]\s*\S", text, re.I)) and not fabrics
    unknown_frame = bool(re.search(r"\b(?:frame|legs|каркас|ножки)\s*[:=]\s*\S", text, re.I)) and not frames
    return {"fabric": fabrics[0] if len(fabrics) == 1 else None,
            "fabric_options": fabrics if len(fabrics) > 1 else ([] if fabrics else partial[:3]),
            "frame": frames[0] if len(frames) == 1 else None,
            "needs_check": len(frames) > 1 or unknown or unknown_frame}


RU_MONTHS = dict(zip("января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(), range(1, 13)))
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
            "saturday": 5, "sunday": 6, "понедельник": 0, "понедельнику": 0,
            "вторник": 1, "вторнику": 1, "среда": 2, "среду": 2, "среде": 2,
            "четверг": 3, "четвергу": 3, "пятница": 4, "пятницу": 4, "пятнице": 4,
            "суббота": 5, "субботу": 5, "субботе": 5, "воскресенье": 6, "воскресенью": 6}
MONTH_PATTERN = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_PATTERN = re.compile(
    r"\b20\d\d-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.20\d\d\b|"
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_PATTERN}|{'|'.join(RU_MONTHS)})(?:\s+20\d\d)?\b|"
    rf"\b{MONTH_PATTERN}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d\d)?\b|"
    r"\b(?:к\s+)?\d{1,2}-(?:му|го)\b|"
    rf"\b(?:(?:next|следующую|следующий|следующему)\s+)?(?:{'|'.join(WEEKDAYS)})\b|"
    r"\b(?:today|tomorrow|tmrw|сегодня|завтра|послезавтра)\b", re.I)


def _one_date(text: str, today: date) -> date | None:
    t = text.lower()
    try:
        if re.fullmatch(r"20\d\d-\d{2}-\d{2}", t):
            return date.fromisoformat(t)
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.20\d\d", t):
            day, month, year = map(int, t.split("."))
            return date(year, month, day)
        offsets = {"today": 0, "сегодня": 0, "tomorrow": 1, "tmrw": 1, "завтра": 1, "послезавтра": 2}
        if t in offsets:
            return today + timedelta(days=offsets[t])
        weekday = t.split()[-1]
        if weekday in WEEKDAYS:
            delta = (WEEKDAYS[weekday] - today.weekday()) % 7
            if delta == 0 and len(t.split()) > 1:
                delta = 7
            return today + timedelta(days=delta)
        if m := re.fullmatch(r"(?:к\s+)?(\d{1,2})-(?:му|го)", t):
            day = int(m[1])
            month = today.month + (day < today.day)
            return date(today.year + (month > 12), (month - 1) % 12 + 1, day)
        words = t.replace(".", "").replace(",", "").split()
        if words[0][0].isdigit():
            day = int(re.sub(r"\D", "", words[0]))
            month = RU_MONTHS.get(words[1]) or MONTHS.get(words[1][:3])
        else:
            month = MONTHS.get(words[0][:3])
            day = int(re.sub(r"\D", "", words[1]))
        if len(words) > 2:
            return date(int(words[2]), month, day)
        d = date(today.year, month, day)
        # Select the year before constructing the final date (safe across leap years).
        return date(today.year + (d < today - timedelta(days=30)), month, day)
    except (ValueError, TypeError, IndexError):
        return None


def _parse_date(text: str, today: date) -> date | None:
    dates = [_one_date(m[0], today) for m in DATE_PATTERN.finditer(text)]
    return dates[0] if dates and dates[0] is not None and len(set(dates)) == 1 else None


def _date_field(text: str, today: date) -> dict | None:
    matches = list(DATE_PATTERN.finditer(text))
    if not matches:
        return None
    quote = matches[0][0] if len(matches) == 1 else text
    d = _parse_date(quote, today)
    return _field(d.isoformat() if d else quote, quote)


# --- offline extraction (no model) ---------------------------------------

HEADER_WORDS = {
    "product": ("product", "item", "model", "article", "издели", "модель", "товар", "наименование"),
    "qty": ("qty", "quantity", "pcs", "count", "amount", "pieces", "units", "кол", "шт", "единиц"),
    "customer": ("customer", "client", "store", "shop", "buyer", "клиент", "покупатель"),
    "due_date": ("due", "deadline", "date", "ship by", "срок", "дата"),
    "variant": ("fabric", "color", "colour", "variant", "upholstery", "ткань", "цвет", "обивка", "вариант"),
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
    original = source.strip().splitlines(keepends=True)
    reader = csv.reader(io.StringIO(source.strip()), delimiter=delim)
    headers = next(reader)
    cols = {}
    for idx, h in enumerate(headers):  # columns by header, never by position
        h = h.strip().lower()
        for key, words in HEADER_WORDS.items():
            if key not in cols and any(w in h for w in words):
                cols[key] = idx
                break
    if "product" not in cols:
        return None
    out = []
    previous = reader.line_num
    for row in reader:
        quote = "".join(original[previous:reader.line_num]).strip()
        previous = reader.line_num
        cell = lambda k: row[cols[k]].strip() if k in cols and cols[k] < len(row) else ""  # noqa
        if not cell("product"):
            continue
        variant = " ".join(x for x in (("fabric: " + cell("variant")) if cell("variant") else "",
                                       ("frame: " + cell("frame")) if cell("frame") else "") if x)
        q, qq = _qty(cell("qty"))
        out.append({"product": _field(cell("product"), cell("product")),
                    "qty": _field(q, qq or cell("qty")),
                    "customer": _field(cell("customer"), cell("customer")),
                    "due_date": _field(cell("due_date"), cell("due_date")),
                    "variant": _field(variant, quote if variant else None),
                    "line": quote})
    return out


def _mentions_product(text: str, cat: dict) -> bool:
    t = set(norm(text).split())
    kinds = {"armchair", "chair", "pouf", "sofa", "daybed", "footstool", "stool", "rocker", "rocking"}
    if t & kinds:
        return True
    return any(set(norm(n).split()) <= t for n in cat["product"].values()) or \
        any(set(a.split()) <= t for a in cat["synonyms"]["product"])


def _qty(clause: str) -> tuple[str | None, str | None]:
    """(value, quote) of the amount in a clause, ignoring numbers in dates
    and in product names like '3-seat'."""
    cleaned = DATE_PATTERN.sub(lambda m: " " * len(m[0]), clause)
    cleaned = re.sub(r"\b\d+[- ](?:seat(?:er)?|местн[а-яё]*)\b", lambda m: " " * len(m[0]), cleaned, flags=re.I)
    if cleaned.strip().lower() in WORD_NUMBERS:
        return str(WORD_NUMBERS[cleaned.strip().lower()]), clause.strip()
    correction = list(re.finditer(
        r"\b(?:actually\s+make\s+it|correction\s*:?|поправка\s*:?\s*(?:сделайте)?|нет\s*,?\s*лучше)\s+",
        cleaned, re.I))
    if correction:
        return _qty(clause[correction[-1].end():])
    amounts = []
    for m in re.finditer(r"(?<![\w-])(?:[x×]\s*)?(\d+)(?:\s*(?:[x×]|pcs|pieces|units|шт)\b)?(?![\w-])", cleaned, re.I):
        amounts.append((int(m[1]), clause[m.start():m.end()]))
    words = list(re.finditer(r"\b[a-zа-яё]+\b", cleaned, re.I))
    skip = -1
    for index, m in enumerate(words):
        w = m[0].lower()
        if index == skip or w not in WORD_NUMBERS:
            continue
        n, end = WORD_NUMBERS[w], m.end()
        if n >= 20 and index + 1 < len(words):
            following = words[index + 1]
            one = WORD_NUMBERS.get(following[0].lower(), 0)
            if 0 < one < 10 and re.fullmatch(r"[\s-]*", cleaned[m.end():following.start()]):
                n, end, skip = n + one, following.end(), index + 1
        # "a pair" is two, and the article in "a deadline" is not an amount.
        if w not in ("a", "an"):
            amounts.append((n, clause[m.start():end]))
    if not amounts:
        article = re.search(r"\b(?:a|an)\s+(?:Cozy|Harbor|Nordic|Bruno|Round|Square|Footstool|Sofa|Daybed|Kids)\b", cleaned, re.I)
        if article:
            return "1", article[0].split()[0]
    if amounts and len({n for n, _ in amounts}) == 1:
        return str(amounts[0][0]), amounts[0][1]
    return None, None


def _customer_field(text: str, cat: dict) -> dict | None:
    found = []
    for name in cat["customer"].values():
        if m := re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
            found.append(_field(name, m[0]))
    for alias in cat["synonyms"]["customer"]:
        if m := re.search(r"\b" + r"\W+".join(map(re.escape, alias.split())) + r"\b", text, re.I):
            found.append(_field(m[0], m[0]))
    for word in STOCK_WORDS:
        if m := re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.I):
            found.append(_field(STOCK, m[0]))
    if found:
        unique = {norm(f["value"]) for f in found}
        return found[0] if len(unique) == 1 else _field(None, None)
    if m := re.search(r"\b(?:customer|client|клиент|покупатель)\s*:\s*([^\n;]+)", text, re.I):
        return _field(m[1].strip(), m[1].strip())
    return None


def _active_source(source: str, cat: dict) -> str:
    """A current order supersedes quoted history; a forwarded-only order still works."""
    lines = source.splitlines(keepends=True)
    history = r"forwarded message|original message|пересланное сообщение|исходное сообщение"
    prefix = re.split(history, source, maxsplit=1, flags=re.I)[0]
    has_current = _mentions_product("\n".join(ln for ln in prefix.splitlines()
                                             if not ln.lstrip().startswith(">")), cat)
    kept = []
    for line in lines:
        if re.search(history, line, re.I):
            if has_current:
                break
            continue
        if has_current and line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "".join(kept)


def _text(source: str, cat: dict, today: date) -> list[dict]:
    source = _active_source(source, cat)
    customer = _customer_field(source, cat)
    due = _date_field(source, today) if customer and customer["value"] else None
    # Keep contiguous source slices so every quote survives grounding, including
    # variants appended after a comma and metadata on the following line.
    clauses = []
    end = 0
    for boundary in list(re.finditer(r"\n|;|,\s+|\s+(?:and|plus|и)\s+|\s*\+\s*|\.\s+", source, re.I)) + [None]:
        stop = boundary.start() if boundary else len(source)
        part = source[end:stop]
        if part.strip():
            if _mentions_product(part, cat):
                clauses.append([end, stop, part.strip()])
            elif clauses:
                clauses[-1][1] = stop
        end = boundary.end() if boundary else stop
    out = []
    current_customer = customer
    ambiguous_correction = len(clauses) > 1 and re.search(
        r"actually\s+make\s+it|correction\s*:|поправка\s*:|нет\s*,?\s*лучше", source, re.I)
    for start, stop, first in clauses:
        clause = source[start:stop].strip()
        local_customer = _customer_field(clause, cat)
        if local_customer:
            current_customer = local_customer
        local_due = _date_field(clause, today)
        q, qq = _qty(clause)
        quantity = _field(q, qq)
        if ambiguous_correction:
            quantity = {**_field(None, None), "note": "Which order does the correction change? / К какому заказу относится поправка?"}
        out.append({"product": _field(first, first), "qty": quantity,
                    "customer": current_customer or _field(None, None),
                    "due_date": local_due or due or _field(None, None),
                    "variant": _field(clause, clause), "line": clause})
    return out


def _extract_offline(conn: sqlite3.Connection, source: str) -> list[dict]:
    cat = _catalog(conn)
    table = _table(source)
    return table if table is not None else _text(source, cat, date.fromisoformat(domain.today(conn)))


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
        if not _in_source(quote, source):
            return None, None, f.get("note") or ("No source quote; check the field. / Нет цитаты из источника; проверьте поле."
                                                 if value or quote else "")
        return value, quote, f.get("note", "")

    # product
    value, quote, note = take("product")
    pid, options = _match("product", quote or "", cat) if quote else (None, [])
    out["product"] = {"id": pid, "value": cat["product"].get(pid) if pid else (value or ""),
                      "quote": quote, "status": "ok" if pid else ("check" if options else "empty"),
                      "options": options, "note": note}

    # qty
    value, quote, note = take("qty")
    try:
        n = int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        n = None
    quoted_qty, _ = _qty(quote or "")
    if n and 0 < n <= 500 and quoted_qty == str(n):
        out["qty"] = {"id": n, "value": n, "quote": quote, "status": "ok", "options": [], "note": ""}
    else:
        out["qty"] = {"id": None, "value": n or 1, "quote": quote, "status": "check",
                      "options": [{"id": k, "label": str(k)} for k in (1, 2, 3)],
                      "note": note or "Confirm the amount; the source is missing or unclear. / Подтвердите количество: оно не указано или неясно."}

    # customer
    value, quote, note = take("customer")
    if quote and (quote.lower() in STOCK_WORDS or quote.lower() == STOCK):
        out["customer"] = {"id": STOCK, "value": "to stock", "quote": quote, "status": "ok",
                           "options": [], "note": ""}
    else:
        cid, options = _match("customer", quote or "", cat) if quote \
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
    d = _parse_date(quote, today) if quote else None
    if d and today - timedelta(days=1) <= d <= today + timedelta(days=365):
        out["due_date"] = {"id": d.isoformat(), "value": d.isoformat(), "quote": quote,
                           "status": "ok", "options": [], "note": ""}
    elif value or quote:
        out["due_date"] = {"id": None, "value": str(value or quote), "quote": quote,
                           "status": "check", "options": [], "note": "Confirm the date; it is invalid or conflicting. / Подтвердите дату: она неверна или неоднозначна."}
    else:
        out["due_date"] = {"id": None, "value": "", "quote": None, "status": "empty",
                           "options": [], "note": note}

    # variant (optional, but a half-known fabric must be checked)
    value, quote, note = take("variant")
    v = _variant(quote or "", cat) if quote else \
        {"fabric": None, "fabric_options": [], "frame": None}
    if value and _variant(str(value), cat).get("needs_check"):
        v["needs_check"] = True
    text = " · ".join(x for x in (v["fabric"], v["frame"]) if x)
    if (v["fabric_options"] and not v["fabric"]) or v.get("needs_check"):
        out["variant"] = {"id": None, "value": value or "", "quote": quote, "status": "check",
                          "options": [{"id": f + (" · " + v["frame"] if v["frame"] else ""),
                                       "label": f} for f in v["fabric_options"]],
                          "note": "Which fabric and frame exactly? / Уточните ткань и каркас."}
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
        if item.get("order_id") or item.get("removed"):
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
            f["customer"]["id"] = customer_id  # so the quote is learned for the new name
        oid = domain.create_order(conn, product_id=int(f["product"]["id"]),
                                  qty=int(f["qty"]["id"]), customer_id=customer_id,
                                  due_date=f["due_date"]["id"], variant=f["variant"]["id"] or "",
                                  source="agent", commit=False)
        item["order_id"] = oid
        conn.execute('INSERT OR REPLACE INTO gate_sources (order_id, quote) VALUES (?, ?)',
                     (oid, item.get('line') or payload['source']))
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



# --- the person fixes anything ----------------------------------------------
# The model (or the offline rules) proposes; the person has the last word on
# every field, not only on the ones marked "check". A typed value is checked
# against the catalog and comes back with the closest names when it is not
# there. A fix of a quoted product or customer becomes a synonym on "Create",
# so the same wording is understood next time.

FIELDS = ("product", "qty", "customer", "due_date", "variant")


def _load(conn: sqlite3.Connection, action_id: int) -> dict:
    row = conn.execute("SELECT * FROM agent_actions WHERE id = ? AND kind = 'intake'",
                       (action_id,)).fetchone()
    if row is None:
        raise DomainError("No such proposal.")
    if row["status"] != "proposed":
        raise DomainError("This proposal is already closed.")
    return json.loads(row["payload"])


def _save(conn: sqlite3.Connection, action_id: int, payload: dict) -> None:
    conn.execute("UPDATE agent_actions SET payload = ? WHERE id = ?",
                 (json.dumps(payload), action_id))
    conn.commit()


def _ready(fields: dict) -> bool:
    return all(fields[k]["status"] == "ok" for k in REQUIRED) and \
        all(fields[k]["status"] != "check" for k in ("due_date", "variant"))


def _empty_field() -> dict:
    return {"id": None, "value": "", "quote": None, "status": "empty", "options": [], "note": ""}


def _suggest(message: str, options: list[dict]) -> dict:
    return {"ok": False, "message": message, "options": options}


def _fixed_value(conn: sqlite3.Connection, field: str, value, cat: dict,
                 today: date) -> dict:
    """{'id', 'value'} for a typed value, or a dict with ok False and options."""
    text = str(value if value is not None else "").strip()
    if field == "product":
        if isinstance(value, int) or text.isdigit():
            name = cat["product"].get(int(text))
            if name is None:
                return _suggest("Unknown product.", [])
            return {"id": int(text), "value": name}
        pid, options = _match("product", text, cat)
        if pid is None:
            return _suggest(f"No product “{text}” in the catalog. Pick one, or add it to the "
                            "catalog first (“add …”).", options)
        return {"id": pid, "value": cat["product"][pid]}
    if field == "customer":
        if text.lower() in (STOCK, "to stock", "на склад"):
            return {"id": STOCK, "value": "to stock"}
        if text.startswith("new:"):
            name = text[4:].strip()
            if not name:
                return _suggest("Type the new customer's name.", [])
            return {"id": "new:" + name, "value": f"New customer: {name}"}
        if isinstance(value, int) or text.isdigit():
            name = cat["customer"].get(int(text))
            if name is None:
                return _suggest("Unknown customer.", [])
            return {"id": int(text), "value": name}
        cid, options = _match("customer", text, cat)
        if cid is None:
            if not text:
                return _suggest("Type who it is for, or pick “To stock”.",
                                [{"id": STOCK, "label": "To stock"}])
            return _suggest(f"No customer “{text}” yet.",
                            options[:2] + [{"id": "new:" + text, "label": f"New customer: {text}"},
                                           {"id": STOCK, "label": "To stock"}])
        return {"id": cid, "value": cat["customer"][cid]}
    if field == "qty":
        try:
            n = int(text)
        except ValueError:
            return _suggest("Quantity must be a whole number.", [])
        if not 0 < n <= 500:
            return _suggest("Quantity must be between 1 and 500.", [])
        return {"id": n, "value": n}
    if field == "due_date":
        if not text:
            return {"id": None, "value": "", "empty": True}
        try:
            d = date.fromisoformat(text)
        except ValueError:
            d = _parse_date(text, today)
        if d is None:
            return _suggest("Date must look like 2026-11-05 or “Nov 5”.", [])
        return {"id": d.isoformat(), "value": d.isoformat()}
    # variant: fabric and frame from the catalog lists
    if not text:
        return {"id": "", "value": "", "empty": True}
    v = _variant(text.replace("·", " "), cat)
    if v["fabric"] is None and (v["fabric_options"] or not v["frame"]):
        close = v["fabric_options"] or difflib.get_close_matches(
            text.split("·")[0].strip().lower(), cat["fabrics"], n=3, cutoff=0.4)
        frame = f" · {v['frame']}" if v["frame"] else ""
        return _suggest(f"No fabric “{text}” in the catalog.",
                        [{"id": f + frame, "label": f + frame} for f in close])
    joined = " · ".join(x for x in (v["fabric"], v["frame"]) if x)
    return {"id": joined, "value": joined}


def fix(conn: sqlite3.Connection, person_id: int, action_id: int, idx: int, field: str,
        value) -> dict:
    """Set one field of one proposed order by hand."""
    if field not in FIELDS:
        raise DomainError("Unknown field.")
    payload = _load(conn, action_id)
    items = payload["items"]
    if not 0 <= idx < len(items) or items[idx].get("removed"):
        raise DomainError("No such line in this proposal.")
    item = items[idx]
    if item.get("order_id"):
        raise DomainError("This line is already created. Edit the order itself.")
    cat = _catalog(conn)
    res = _fixed_value(conn, field, value, cat, date.fromisoformat(domain.today(conn)))
    if res.get("ok") is False:
        return res
    f = item["fields"][field]
    was = f.get("value")
    if res.get("empty"):
        if field in REQUIRED:
            return _suggest("This field is needed.", [])
        f.update(id=res["id"], value="", status="empty", options=[], note="", learned=False)
    else:
        f.update(id=res["id"], value=res["value"], status="ok", options=[], note="",
                 learned=field in ("product", "customer") and bool(f.get("quote")), by_hand=True)
    item["ready"] = _ready(item["fields"])
    if str(was) != str(f["value"]):
        payload.setdefault("corrections", []).append(
            {"item": idx, "field": field, "was": was, "now": f["value"], "by": person_id})
    _save(conn, action_id, payload)
    label = field.replace("_", " ").capitalize()
    return {"ok": True, "item": item, "message": f"{label}: {f['value'] or 'empty'}."}


def add_line(conn: sqlite3.Connection, person_id: int, action_id: int) -> dict:
    """A line the reader missed (or a photo read by hand): all fields empty."""
    payload = _load(conn, action_id)
    if len(payload["items"]) >= 200:
        raise DomainError("Too many lines in one proposal. Split it.")
    payload["items"].append({"fields": {k: _empty_field() for k in FIELDS},
                             "line": "added by hand", "ready": False, "order_id": None,
                             "by_hand": True})
    _save(conn, action_id, payload)
    return {"ok": True, "idx": len(payload["items"]) - 1, "item": payload["items"][-1],
            "message": "Line added. Fill product, quantity and customer."}


def remove_line(conn: sqlite3.Connection, person_id: int, action_id: int, idx: int) -> dict:
    """Not an order after all (a greeting, a duplicate). Kept in the proposal,
    marked removed, so the record shows what was dropped."""
    payload = _load(conn, action_id)
    items = payload["items"]
    if not 0 <= idx < len(items):
        raise DomainError("No such line in this proposal.")
    if items[idx].get("order_id"):
        raise DomainError("This line is already created. Cancel the order instead.")
    items[idx]["removed"] = True
    _save(conn, action_id, payload)
    return {"ok": True, "message": f"Line {idx + 1} removed from this proposal."}


# --- a photo of an order form or a chat screenshot --------------------------

PHOTO_PROMPT = """This is a photo of a furniture order: a paper order form or a chat screenshot.
1. Copy every line of text you can read into "transcript", exactly as written, line by line.
   Do not fix spelling. Skip what you cannot read; never invent text.
2. Then turn it into orders. Return only JSON:
{"transcript": "...", "orders": [{"product": {"value": "", "quote": ""}, "qty": {...},
"customer": {...}, "due_date": {...}, "variant": {...}}]}
Every "quote" is copied character for character from your transcript. If the photo does not
say it, leave value and quote empty. Never guess.
product: the closest catalog product name. qty: a whole number. customer: catalog name if it is
one of them, otherwise as written, "stock" if it says for stock. due_date: YYYY-MM-DD, today is
{today}. variant: fabric and frame as written in the catalog lists.
Catalog products: {products}
Known customers: {customers}
Fabrics: {fabrics}
Frames: {frames}"""

PHOTO_TYPES = {"image/jpeg": b"\xff\xd8", "image/png": b"\x89PNG", "image/webp": b"RIFF"}
PHOTO_MAX = 6 * 1024 * 1024


def propose_photo(conn: sqlite3.Connection, person_id: int, data: bytes, mime: str) -> dict:
    """Spark reads the photo into a transcript plus orders; the quote rule is
    the same as for text, checked against the transcript, which is shown next
    to the photo. With the model off, the photo is kept and the person types
    the order next to it."""
    import base64
    if mime not in PHOTO_TYPES or not data.startswith(PHOTO_TYPES[mime]):
        raise DomainError("Send a photo as JPEG, PNG or WEBP.")
    if len(data) > PHOTO_MAX:
        raise DomainError("The photo is larger than 6 MB. Take it again a bit smaller.")
    when = domain.now(conn).isoformat(timespec="seconds")
    photo_id = conn.execute("INSERT INTO intake_photos (mime, data, created_at, person_id) "
                            "VALUES (?, ?, ?, ?)", (mime, data, when, person_id)).lastrowid
    cat = _catalog(conn)
    today = date.fromisoformat(domain.today(conn))
    note = ""
    try:
        prompt = (PHOTO_PROMPT.replace("{today}", today.isoformat())
                  .replace("{products}", "; ".join(cat["product"].values()))
                  .replace("{customers}", "; ".join(cat["customer"].values()))
                  .replace("{fabrics}", "; ".join(cat["fabrics"]))
                  .replace("{frames}", "; ".join(cat["frames"])))
        out = models.vision(conn, "intake-photo", prompt, base64.b64encode(data).decode(), mime)
        m = re.search(r"\{.*\}", _output_text(out), re.S)
        if not m:
            raise BudgetStop("Model answer had no JSON.")
        parsed = json.loads(m[0])
        source = str(parsed.get("transcript") or "").strip()
        raw = [{k: _field((o.get(k) or {}).get("value") or None,
                          (o.get(k) or {}).get("quote") or None)
                for k in FIELDS} for o in parsed.get("orders", [])]
        model = models.SPARK
        items = [ground(conn, source, r, cat, today) for r in raw]
    except (BudgetStop, ValueError) as exc:
        source, items, model = "", [], "offline"
        why = ("Reading photos needs the Meta model, and it is off now"
               if isinstance(exc, BudgetStop) and not models.live()
               else f"The photo could not be read ({exc})")
        note = why + ". The photo is kept: add a line and type the order from it."
    payload = {"source": source, "items": items, "photo_id": photo_id, "note": note}
    cur = conn.execute(
        "INSERT INTO agent_actions (kind, payload, model, created_at, for_person) "
        "VALUES ('intake', ?, ?, ?, ?)", (json.dumps(payload), model, when, person_id))
    conn.commit()
    return {"id": cur.lastrowid, "model": model, **payload,
            "message": note or f"Read {len(items)} order(s) from the photo. Check them."}
