"""Printable paper: order sheets, the office folder sheet and delivery notes.

Pure functions from data to HTML. Everything from the database is escaped.
Designed for a cheap black-and-white printer: urgency is a thick frame and a
boxed word, never a colour; the order number is readable from across the room;
the shop floor's paper never carries money, links or ads (marketplace boxes
travel with it).
"""
from collections import Counter
from html import escape

import segno

T = {
    "en": {
        "paperwork": "Paperwork", "print": "Print", "order": "Order", "item": "item",
        "of": "of", "customer": "Customer", "to_stock": "TO STOCK", "due": "Due",
        "stage": "Stage", "who": "Who", "date": "Date", "copy": "Copy", "printed": "printed",
        "urgent": "URGENT", "delivery_note": "Delivery note", "shop_copy": "Shop copy",
        "customer_copy": "Customer copy", "released_by": "Released by",
        "received_by": "Received by", "total": "Total", "paid": "Paid", "owed": "Owed",
        "previous": "Owed from earlier deliveries", "owed_all": "Owed in total",
        "signature": "signature", "product": "Product", "price": "Price", "done": "done",
        "reprint": "REPRINT", "lost": "lost", "damaged": "damaged", "changed": "order changed",
        "other": "other", "batch": "Batch", "sheets": "sheets", "order_sheets": "Order sheets",
        "no_code": "no code yet — print its sheet", "to": "To", "pieces": "Pieces",
        "payment": "Payment", "p_paid": "paid in full", "p_credit": "on credit",
        "p_partial": "part paid", "count_check": "Count the pieces on delivery.",
        "tax_id": "Tax ID", "layout": "Layout", "a5": "A5", "a4": "2 on A4", "a4x4": "4 on A4",
        "back": "Back", "by_product": "By product", "numbers": "Items", "not_found": "Paperwork",
        "copies_note": "Two copies: one stays with us, one goes with the goods.",
        "scan_hint": "Scan or type the code to mark work.",
    },
    "ru": {
        "paperwork": "Документы", "print": "Печать", "order": "Заказ", "item": "шт.",
        "of": "из", "customer": "Заказчик", "to_stock": "НА СКЛАД", "due": "Отдать к",
        "stage": "Этап", "who": "Кто", "date": "Дата", "copy": "Копия", "printed": "напечатан",
        "urgent": "СРОЧНО", "delivery_note": "Накладная", "shop_copy": "Экземпляр цеха",
        "customer_copy": "Экземпляр заказчика", "released_by": "Отпустил",
        "received_by": "Получил", "total": "Итого", "paid": "Оплачено", "owed": "К оплате",
        "previous": "Долг по прошлым поставкам", "owed_all": "Всего к оплате",
        "signature": "подпись", "product": "Изделие", "price": "Цена", "done": "сделано",
        "reprint": "ПОВТОР", "lost": "потерян", "damaged": "испорчен", "changed": "заказ изменён",
        "other": "другое", "batch": "Пачка", "sheets": "листков", "order_sheets": "Листки заказов",
        "no_code": "кода ещё нет — напечатайте листок", "to": "Кому", "pieces": "Штук",
        "payment": "Оплата", "p_paid": "оплачено полностью", "p_credit": "в долг",
        "p_partial": "частично", "count_check": "Пересчитайте штуки при получении.",
        "tax_id": "ИНН", "layout": "Раскладка", "a5": "A5", "a4": "2 на A4", "a4x4": "4 на A4",
        "back": "Назад", "by_product": "По изделиям", "numbers": "Штуки", "not_found": "Документы",
        "copies_note": "Два экземпляра: один остаётся у нас, второй едет с товаром.",
        "scan_hint": "Отсканируйте или введите код, чтобы отметить работу.",
    },
}

LAYOUTS = {  # sheets per page, page size, sheet box (mm), font scale, qr size (mm)
    "a5": (1, "A5 portrait", (136, 198), 1.0, 34),
    "a4": (2, "A4 landscape", (136, 198), 1.0, 34),
    "a4x4": (4, "A4 portrait", (96, 138), 0.7, 26),
}


def e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def words(lang: str) -> dict:
    return T.get(lang, T["en"])


def fmt_money(currency: str, amount: int) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}{currency}{abs(amount):,}"


def fmt_when(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")


def qr_svg(text: str) -> str:
    # Medium error correction survives a crease or a bit of tape.
    return segno.make(text, error="m").svg_inline(scale=4, border=2, dark="#000")


def page(title: str, body: str, lang: str = "en", css: str = "") -> str:
    lang = lang if lang in T else "en"
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{e(title)}</title><style>{BASE_CSS}{css}</style></head><body>{body}</body></html>")


BASE_CSS = """
*{box-sizing:border-box}
html,body{margin:0}
body{background:#e9e6df;color:#000;font:14px/1.3 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-variant-numeric:tabular-nums}
.toolbar{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:8px;align-items:center;
  padding:10px 16px;background:#1d1d1b;color:#fff}
.toolbar a,.toolbar button{color:#fff;background:transparent;border:1px solid #ffffff55;border-radius:8px;
  padding:8px 12px;font:inherit;text-decoration:none;cursor:pointer;min-height:40px}
.toolbar a.on{background:#fff;color:#1d1d1b}
.toolbar .print{background:#1f5f4a;border-color:#1f5f4a;font-weight:700;margin-left:auto}
.toolbar .hint{opacity:.75;font-size:13px}
.paper{padding:16px;display:flex;flex-direction:column;align-items:center;gap:16px}
@media print{
  body{background:#fff}
  .toolbar{display:none!important}
  .paper{padding:0;display:block}
}
"""

SHEET_CSS = """
.page{display:grid;gap:0;background:#fff;box-shadow:0 2px 10px #0003}
.sheet{width:var(--w);height:var(--h);padding:calc(5mm*var(--k));border:.3mm solid #000;
  display:flex;flex-direction:column;overflow:hidden;background:#fff;font-size:calc(11pt*var(--k));
  break-inside:avoid;page-break-inside:avoid;position:relative}
.sheet.urgent{border:1.6mm solid #000}
.head{display:flex;justify-content:space-between;gap:3mm}
.num{font-weight:900;font-size:calc(48pt*var(--k));line-height:.95;letter-spacing:-.02em}
.piece{display:inline-block;margin-top:1.5mm;border:.5mm solid #000;padding:0 2mm;
  font-weight:800;font-size:calc(13pt*var(--k))}
.urgent-tag{display:inline-block;margin:1.5mm 0 0 1.5mm;border:.9mm solid #000;padding:0 2mm;
  font-weight:900;font-size:calc(13pt*var(--k));letter-spacing:.08em}
.qr{text-align:center;flex:none}
.qr svg{width:var(--q);height:var(--q);display:block}
.code{font:800 calc(14pt*var(--k)) ui-monospace,Consolas,monospace;letter-spacing:.06em;white-space:nowrap}
.copyno{font-size:calc(9.5pt*var(--k));font-weight:700}
.what{margin-top:2.5mm;font-weight:800;font-size:calc(17pt*var(--k));line-height:1.1}
.variant{font-size:calc(13pt*var(--k));margin-top:.8mm}
.for{display:flex;justify-content:space-between;align-items:baseline;gap:3mm;margin-top:2.5mm;
  padding:1.5mm 0;border-top:.3mm solid #000;border-bottom:.3mm solid #000;font-size:calc(11.5pt*var(--k))}
.stock{border:.7mm solid #000;padding:0 2mm;font-weight:900;letter-spacing:.08em}
.due b{font-size:calc(14pt*var(--k))}
.stages{flex:1;margin-top:2.5mm;width:100%;border-collapse:collapse;table-layout:fixed}
.stages th{font-size:calc(8pt*var(--k));text-transform:uppercase;letter-spacing:.05em;text-align:left;
  padding:.5mm 1.5mm;border-bottom:.4mm solid #000}
.stages td{border:.3mm solid #000;padding:0 1.5mm;font-size:calc(11pt*var(--k))}
.stages td:first-child{font-weight:700;border-left:0}
.stages td:last-child{border-right:0;text-align:center;font-size:calc(16pt*var(--k))}
.stages .prev{color:#555;font-style:italic;font-size:calc(9pt*var(--k))}
.foot{margin-top:2mm;display:flex;justify-content:space-between;gap:2mm;font-size:calc(7.5pt*var(--k));color:#222}
.foot b{color:#000}
.cover .num{font-size:calc(30pt*var(--k))}
.cover h3{margin:3mm 0 1mm;font-size:calc(9pt*var(--k));text-transform:uppercase;letter-spacing:.06em}
.cover ul{margin:0;padding-left:4mm;columns:2;font-size:calc(10pt*var(--k))}
.cover .labels{font:calc(10pt*var(--k))/1.5 ui-monospace,Consolas,monospace}
@media screen and (max-width:600px){
  .page{display:block;width:100%;box-shadow:none;background:transparent}
  .sheet{width:100%;height:auto;min-height:0;margin-bottom:12px;--k:.8}
  .stages td{height:11mm}
}
@media print{
  .page{box-shadow:none;break-after:page;page-break-after:always}
  .page:last-child{break-after:auto;page-break-after:auto}
}
"""


def _toolbar(t: dict, *, layouts: str | None = None, hint: str = "") -> str:
    links = ""
    if layouts is not None:
        links = "".join(f'<a href="?layout={k}" class="{"on" if k == layouts else ""}">{t[k]}</a>'
                        for k in LAYOUTS)
        links = f'<span class="hint">{t["layout"]}:</span>{links}'
    hint_html = f'<span class="hint">{e(hint)}</span>' if hint else ""
    return (f'<nav class="toolbar"><a href="/paperwork">← {t["paperwork"]}</a>{links}{hint_html}'
            f'<button type="button" class="print" onclick="window.print()">{t["print"]}</button></nav>')


# --- order sheet (one item) -----------------------------------------------

def render_sheet(item: dict, lang: str = "en", base: str = "") -> str:
    """One item's sheet as it was printed: the copy's own snapshot, not today's order."""
    from .paperwork import code_url, format_code
    t = words(lang)
    snap = item["snapshot"]
    done = {r["stage_id"]: r for r in item["done"]}
    rows = []
    for st in item["stages"]:
        name = st["name_ru"] if lang == "ru" else st["name_en"]
        d = done.get(st["id"])
        if d:  # reprint of an item in progress: show what is already done, greyed
            cells = (f'<td class="prev">{t["done"]} · {e(d["person"])}</td>'
                     f'<td class="prev">{e(d["work_date"])}</td><td>✓</td>')
        else:
            cells = "<td></td><td></td><td>☐</td>"
        rows.append(f'<tr data-stage="{e(st["id"])}"><td>{e(name)}</td>{cells}</tr>')
    piece = (f'<span class="piece">{e(snap["seq"])} {t["of"]} {e(snap["qty"])}</span>'
             if snap["qty"] > 1 else "")
    urgent = f'<span class="urgent-tag">{t["urgent"]}</span>' if item["urgent"] else ""
    who = (f'<span>{t["customer"]}: <b>{e(snap["customer"])}</b>'
           f'{", " + e(snap["city"]) if snap.get("city") else ""}</span>'
           if snap["customer"] else f'<span class="stock">{t["to_stock"]}</span>')
    due = f'<span class="due">{t["due"]} <b>{e(snap["due_date"])}</b></span>' if snap["due_date"] else ""
    reprint = (f' · <b>{t["reprint"]}: {e(t.get(item["reason"], item["reason"]))}</b>'
               if item.get("reason") else "")
    return f"""<article class="sheet{' urgent' if item['urgent'] else ''}">
<div class="head"><div><div class="num">#{e(snap['order_number'])}</div>{piece}{urgent}</div>
<div class="qr">{qr_svg(code_url(item['token'], item['copy'], base))}
<div class="code">{e(format_code(item['token']))}</div>
<div class="copyno">{t['copy']} {e(item['copy'])}</div></div></div>
<div class="what">{e(snap['product'])}</div><div class="variant">{e(snap['variant'])}</div>
<div class="for">{who}{due}</div>
<table class="stages"><thead><tr><th style="width:30%">{t['stage']}</th><th>{t['who']}</th>
<th style="width:24%">{t['date']}</th><th style="width:11%">✓</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="foot"><span>{e(item['shop'])}</span><span>{t['copy']} <b>{e(item['copy'])}</b> · {t['printed']}
{e(fmt_when(item['printed_at']))} · {e(item['printed_by_name'])}{reprint}</span></div>
</article>"""


def _cover(data: dict, t: dict) -> str:
    """The band on top of a pack: what is inside, what is urgent. Read without opening it."""
    sheets = data["sheets"]
    labels = [f"#{s['snapshot']['order_number']}" + (f"-{s['snapshot']['seq']}" if s["snapshot"]["qty"] > 1 else "")
              for s in sheets]
    urgent = [lab for lab, s in zip(labels, sheets) if s["urgent"]]
    products = Counter(s["snapshot"]["product"] for s in sheets)
    reason = f' · {t["reprint"]}: {e(t.get(data["reason"], data["reason"]))}' if data.get("reason") else ""
    return f"""<article class="sheet cover">
<div class="num">{t['batch']} {e(data['id'])}</div>
<div class="what">{len(sheets)} {t['sheets']}{reason}</div>
<div class="variant">{e(fmt_when(data['created_at']))} · {e(data['printed_by_name'])}</div>
{f'<h3>{t["urgent"]}</h3><div class="labels"><b>{e(", ".join(urgent))}</b></div>' if urgent else ''}
<h3>{t['by_product']}</h3><ul>{''.join(f'<li>{e(p)} × {n}</li>' for p, n in products.most_common())}</ul>
<h3>{t['numbers']}</h3><div class="labels">{e(", ".join(labels))}</div>
</article>"""


def render_batch(data: dict, layout: str = "a5", lang: str = "en", base: str = "") -> str:
    layout = layout if layout in LAYOUTS else "a5"
    per_page, size, (w, h), k, q = LAYOUTS[layout]
    t = words(lang)
    cards = ([_cover(data, t)] if len(data["sheets"]) > 1 else []) + \
        [render_sheet(s, lang, base) for s in data["sheets"]]
    cols = 1 if per_page == 1 else 2
    pages = "".join(f'<section class="page" style="grid-template-columns:repeat({cols},var(--w))">'
                    + "".join(cards[i:i + per_page]) + "</section>"
                    for i in range(0, len(cards), per_page))
    css = (SHEET_CSS + f":root{{--w:{w}mm;--h:{h}mm;--k:{k};--q:{q}mm}}"
           f"@page{{size:{size};margin:6mm}}")
    if per_page == 2:  # side by side on landscape A4, cut along the middle
        css += ".page .sheet+.sheet{border-left:.3mm dashed #000}"
    return page(f"{t['order_sheets']} · {t['batch']} {data['id']}",
                _toolbar(t, layouts=layout, hint=t["scan_hint"])
                + f'<main class="paper">{pages}</main>', lang, css)


# --- office folder sheet (whole order) ------------------------------------

ORDER_CSS = """
.folder{background:#fff;width:198mm;max-width:100%;padding:8mm;box-shadow:0 2px 10px #0003}
.folder .num{font-weight:900;font-size:34pt;line-height:1}
.folder .what{font-weight:800;font-size:15pt;margin-top:2mm}
.folder table{width:100%;border-collapse:collapse;margin-top:5mm;table-layout:fixed}
.folder th,.folder td{border:.3mm solid #000;padding:1.5mm;text-align:center;font-size:10pt;overflow-wrap:anywhere}
.folder th:first-child,.folder td:first-child{text-align:left;font-weight:700}
.folder .mini svg{width:18mm;height:18mm;display:block;margin:auto}
.folder .code{font:700 9pt ui-monospace,Consolas,monospace}
.folder .muted{color:#555;font-size:8.5pt}
@page{size:A4 portrait;margin:8mm}
@media print{.folder{box-shadow:none;width:auto;padding:0}}
"""


def render_order(data: dict, lang: str = "en", base: str = "") -> str:
    from .paperwork import code_url, format_code
    t = words(lang)
    units, stages = data["units"], data["stages"]
    snap = units[0]["snapshot"]
    heads = "".join(f'<th>{e(s["name_ru"] if lang == "ru" else s["name_en"])}</th>' for s in stages)
    rows = []
    for u in units:
        code = (f'<div class="mini">{qr_svg(code_url(u["token"], base=base))}</div>'
                f'<div class="code">{e(format_code(u["token"]))}</div>'
                if u["token"] else f'<span class="muted">{t["no_code"]}</span>')
        marks = "".join(f'<td>{"✓" if s["id"] in u["done"] else "☐"}</td>' for s in stages)
        rows.append(f"<tr><td>{e(u['label'])}</td><td>{code}</td>{marks}</tr>")
    who = (f'{e(snap["customer"])}{", " + e(snap["city"]) if snap.get("city") else ""}'
           if snap["customer"] else t["to_stock"])
    body = f"""<main class="paper"><article class="folder">
<div class="num">{t['order']} #{e(snap['order_number'])}</div>
<div class="what">{e(snap['product'])} × {e(snap['qty'])}</div><div>{e(snap['variant'])}</div>
<p>{t['customer']}: <b>{who}</b>{f" · {t['due']} <b>{e(snap['due_date'])}</b>" if snap['due_date'] else ''}</p>
<table><thead><tr><th style="width:14%">{t['item']}</th><th style="width:22%">QR</th>{heads}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></article></main>"""
    return page(f"{t['order']} #{snap['order_number']}", _toolbar(t) + body, lang, ORDER_CSS)


# --- delivery note --------------------------------------------------------

NOTE_CSS = """
.sheet-a4{background:#fff;width:198mm;max-width:100%;box-shadow:0 2px 10px #0003}
.note{padding:7mm 8mm;min-height:136mm;display:flex;flex-direction:column;font-size:10pt}
.note+.note{border-top:.4mm dashed #000}
.nh{display:flex;justify-content:space-between;gap:6mm;align-items:flex-start}
.shopname{font-size:15pt;font-weight:900}
.shop{font-size:8.5pt;line-height:1.35}
.doc{text-align:right}
.doc .label{display:inline-block;border:.5mm solid #000;padding:0 2mm;font-size:8pt;font-weight:800;
  text-transform:uppercase;letter-spacing:.06em}
.doc h1{margin:1.5mm 0 0;font-size:16pt}
.doc .dn{font:800 13pt ui-monospace,Consolas,monospace}
.to{margin:4mm 0 3mm;padding:2mm 0;border-top:.3mm solid #000;border-bottom:.3mm solid #000;font-size:11pt}
table.lines{width:100%;border-collapse:collapse}
.lines th{font-size:7.5pt;text-transform:uppercase;letter-spacing:.05em;text-align:left;border-bottom:.4mm solid #000;padding:1mm}
.lines th.r{text-align:right}
.lines td{border-bottom:.2mm solid #999;padding:1.2mm 1mm;vertical-align:top}
.lines small{color:#444}
.r{text-align:right;white-space:nowrap}
.sum{margin:2mm 0 0 auto;border-collapse:collapse;min-width:70mm}
.sum td{padding:.8mm 1mm}
.sum .big td{font-weight:900;font-size:12pt;border-top:.4mm solid #000}
.meta{margin-top:2mm;font-size:9pt}
.sig{margin-top:auto;padding-top:6mm;display:grid;grid-template-columns:1fr 1fr;gap:8mm;font-size:9pt}
.sig .line{border-bottom:.3mm solid #000;height:8mm}
.sig small{color:#444}
@page{size:A4 portrait;margin:8mm}
@media print{.sheet-a4{box-shadow:none;width:auto}.note.alone{break-after:page;page-break-after:always}
  .note.alone:last-child{break-after:auto}}
@media screen and (max-width:600px){.note{padding:12px;min-height:0}.nh{flex-direction:column}.doc{text-align:left}
  .sig{grid-template-columns:1fr}}
"""


def render_note(data: dict, copies: int = 2, lang: str = "en", toolbar: bool = True,
                autoprint: bool = False) -> str:
    """Delivery note. Two copies share one A4 (cut in half) while it fits; long notes
    put each copy on its own page so no line is ever cut off."""
    t = words(lang)
    cur = data["currency"]
    m = lambda n: e(fmt_money(cur, n))  # noqa: E731
    lines = "".join(
        f'<tr><td>{i}</td><td><b>{e(ln["label"])}</b></td><td>{e(ln["product"])}'
        f'<br><small>{e(ln["variant"])}</small></td><td class="r">{m(ln["price"])}</td></tr>'
        for i, ln in enumerate(data["lines"], 1))
    sums = (f'<tr><td>{t["total"]}</td><td class="r">{m(data["total"])}</td></tr>'
            f'<tr><td>{t["paid"]}</td><td class="r">{m(data["paid"])}</td></tr>'
            f'<tr class="big"><td>{t["owed"]}</td><td class="r">{m(data["owed"])}</td></tr>')
    if data.get("previous_owed", 0) > 0:
        sums += (f'<tr><td>{t["previous"]}</td><td class="r">{m(data["previous_owed"])}</td></tr>'
                 f'<tr class="big"><td>{t["owed_all"]}</td><td class="r">{m(data["owed_with_previous"])}</td></tr>')
    shop = data["shop_block"]
    shop_lines = "<br>".join(e(v) for v in (shop["shop_address"],
                                           " · ".join(x for x in (shop["shop_phone"], shop["shop_email"]) if x))
                             if v)
    if shop["shop_tax_id"]:
        shop_lines += f'<br>{t["tax_id"]}: {e(shop["shop_tax_id"])}'
    labels = ["shop_copy", "customer_copy"] if copies == 2 else ["customer_copy"]
    alone = " alone" if copies == 2 and len(data["lines"]) > 7 else ""
    blocks = []
    for label in labels:
        blocks.append(f"""<article class="note{alone}">
<header class="nh"><div><div class="shopname">{e(shop['shop_name'] or data['shop'])}</div>
<div class="shop">{shop_lines}</div></div>
<div class="doc"><span class="label">{t[label]}</span><h1>{t['delivery_note']}</h1>
<div class="dn">{e(data['number'])}</div><div>{e(data['date'])}</div></div></header>
<div class="to">{t['to']}: <b>{e(data['customer'])}</b>{', ' + e(data['city']) if data.get('city') else ''}
{' · ' + e(data['email']) if data.get('email') else ''}</div>
<table class="lines"><thead><tr><th style="width:6%">#</th><th style="width:16%">{t['item']}</th>
<th>{t['product']}</th><th class="r">{t['price']}</th></tr></thead><tbody>{lines}</tbody></table>
<table class="sum">{sums}</table>
<div class="meta">{t['pieces']}: <b>{len(data['lines'])}</b> · {t['payment']}: <b>{t.get('p_' + data['payment'], e(data['payment']))}</b>
· {t['count_check']}</div>
<div class="sig"><div>{t['released_by']}<div class="line"></div><small>{e(data['created_by'])} · {t['signature']} · {t['date']}</small></div>
<div>{t['received_by']}<div class="line"></div><small>{t['signature']} · {t['date']}</small></div></div>
</article>""")
    bar = _toolbar(t, hint=t["copies_note"] if copies == 2 else "") if toolbar else ""
    script = "<script>addEventListener('load',()=>print())</script>" if autoprint else ""
    return page(f"{t['delivery_note']} {data['number']}",
                bar + f'<main class="paper"><div class="sheet-a4">{"".join(blocks)}</div></main>{script}',
                lang, NOTE_CSS)


def error_page(message: str) -> str:
    return page("Paperwork", '<main class="paper"><div style="background:#fff;padding:24px;max-width:560px;'
                f'border-radius:12px"><h1>Paperwork</h1><p>{e(message)}</p>'
                '<p><a href="/paperwork">← Paperwork / Документы</a></p></div></main>')


# --- office hub -----------------------------------------------------------

HUB_CSS = """
body{background:#f6f4ef;color:#1d1d1b;font-size:16px}
.hub{max-width:1080px;margin:0 auto;padding:16px}
.hub header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 16px}
.hub h1{font-size:28px;margin:0}
.hub a{color:#1f5f4a}
.hub section{background:#fff;border:1px solid #ddd8cc;border-radius:16px;padding:20px;margin:0 0 16px}
.hub h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#5f5b53;margin:0 0 12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{display:flex;flex-direction:column;align-items:flex-start;gap:4px;text-align:left;border:1px solid #ddd8cc;
  border-radius:14px;padding:14px 16px;background:#fbfaf7;font:inherit;cursor:pointer;min-height:96px;color:inherit}
.tile:hover{border-color:#1f5f4a}
.tile b{font-size:34px;line-height:1}
.tile span{color:#5f5b53}
.tile.hot{background:#fff1e6;border-color:#e2b48f}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px}
.row input,.row select{font:inherit;padding:10px 12px;border:1px solid #ccc6b8;border-radius:10px;min-height:44px;
  background:#fff;max-width:100%}
.btn{font:inherit;font-weight:700;border:0;border-radius:10px;padding:10px 16px;min-height:44px;cursor:pointer;
  background:#1f5f4a;color:#fff;text-decoration:none;display:inline-flex;align-items:center}
.btn.ghost{background:transparent;color:#1f5f4a;border:1px solid #1f5f4a}
.btn:disabled{opacity:.5}
.list{list-style:none;margin:12px 0 0;padding:0}
.list li{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:12px 0;border-top:1px solid #eee9de}
.list .grow{flex:1;min-width:200px}
.list small{color:#5f5b53}
.chip{border-radius:999px;padding:2px 10px;font-size:13px;font-weight:700;background:#eee9de}
.chip.draft{background:#fff1cc}.chip.sent{background:#d9efe2;color:#1f7a3f}.chip.failed{background:#fde2df;color:#b3261e}
.warn{color:#b3261e}
.muted{color:#5f5b53}
#message{position:sticky;top:0;z-index:3;margin:0 0 12px;padding:12px 16px;border-radius:12px;font-weight:600}
#message:empty{display:none}
#message.ok{background:#d9efe2;color:#1f5f4a}#message.bad{background:#fde2df;color:#b3261e}
@media(max-width:600px){.hub{padding:12px}.hub section{padding:14px}.tile b{font-size:28px}}
"""


def render_hub(lang: str = "en") -> str:
    """The office page for paper. Its words live in paperwork.js (EN/RU)."""
    t = words(lang)
    body = f"""<main class="hub" id="paperwork" data-lang="{e(lang)}">
<header><h1 data-t="title">{t['paperwork']}</h1><a href="/" data-t="back">← {t['back']}</a></header>
<p id="message" role="status" aria-live="polite"></p>
<section><h2 data-t="sheets"></h2>
<div class="tiles">
<button type="button" class="tile" data-scope="new"><b>–</b><span data-t="new"></span></button>
<button type="button" class="tile" data-scope="changed"><b>–</b><span data-t="changed"></span></button>
<button type="button" class="tile" data-scope="today"><b>–</b><span data-t="today"></span></button>
</div>
<div class="row"><input id="orders" inputmode="numeric" placeholder="214, 215" aria-label="orders">
<select id="reason" aria-label="reason"></select>
<select id="layout" aria-label="layout"><option value="a5">A5</option><option value="a4">2 × A5 / A4</option>
<option value="a4x4">4 × A6 / A4</option></select>
<button type="button" class="btn" id="print-orders" data-t="printOrders"></button></div>
<ul class="list" id="attention"></ul></section>
<section><h2 data-t="notes"></h2><ul class="list" id="notes"></ul></section>
<section><h2 data-t="outbox"></h2><p id="sending" class="muted"></p><ul class="list" id="outbox"></ul></section>
</main><script src="/static/paperwork.js" defer></script>"""
    return page(t["paperwork"], body, lang, HUB_CSS)
