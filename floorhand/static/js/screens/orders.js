// Office "Orders": intake box (paste anything -> proposal with quotes), the
// attention block, the table with stage lines, and the order card (#/orders/<id>).
import { registerScreen, addStrings, api, t, esc, name, state, money, fmtDate, fmtTime, flash, toast, go, idem } from "../core.js";
import { icon, btn, chip, pill, table, stageLine, statusPill, dueText, empty, sectionTitle, stat, comingSoon, bindSoon } from "../ui.js";
import * as OM from "../orders-more.js";

addStrings({
  en: {
    orders: "Orders", newOrder: "New order", intakeHint: "Paste an e-mail, a chat message or a table — or drop a .csv file here.",
    readIt: "Read it", reading: "Reading…", pickTable: "Choose a table file", trySample: "Try:", sampleMail: "an e-mail", sampleTable: "a table",
    source: "Source", readBy: "Read by", proposed: "Proposed orders", orderN: "Order {n}", nothingToCreate: "Everything here is created.",
    createN: "Create {n} order(s)", dropProposal: "Discard", f_product: "Product", f_qty: "Qty", f_customer: "Customer",
    f_due_date: "Due", f_variant: "Fabric · frame", checkWord: "Check", missing: "Missing", toCheck: "{n} field(s) to check",
    attention: "Needs attention", lateBy: "{n} d late", nowAt: "now at", search: "Search: customer, number, product",
    product: "Product", customer: "Customer", whereNow: "Where now", dueCol: "Due", nothingFound: "Nothing found.",
    v_open: "Open", v_in_work: "In work", v_ready: "Ready", v_shipped: "Shipped", v_all: "All",
    s_accepted: "Accepted", s_in_work: "In work", s_ready: "Ready", s_shipped: "Shipped", s_cancelled: "Cancelled",
    ship: "Ship", printSheets: "Print job sheets", editOrder: "Edit", orderSheet: "Order sheet", history: "History", undoneWord: "undone",
    ordered: "Ordered", inProd: "In production", readyQ: "Ready", shippedQ: "Shipped", spec: "Specification",
    commercial: "Customer & price", unitPrice: "Price / pc", created: "Created", sourceWord: "Source", noHistory: "No work recorded yet.",
    recordedAt: "{who} · {stage}{pc} · {t}", src_manual: "typed in", src_sheet: "table", src_email: "e-mail", src_chat: "chat", src_intake: "intake",
    offlineRules: "offline rules (no model spend)", draftSaved: "Text kept on this device.", showMore: "Show {n} more", showLess: "Show fewer",
  },
  ru: {
    orders: "Заказы", newOrder: "Новый заказ", intakeHint: "Вставь письмо, сообщение из чата или таблицу — или перетащи сюда файл .csv.",
    readIt: "Разобрать", reading: "Разбираю…", pickTable: "Выбрать файл таблицы", trySample: "Попробуй:", sampleMail: "письмо", sampleTable: "таблицу",
    source: "Исходник", readBy: "Разобрал", proposed: "Предложенные заказы", orderN: "Заказ {n}", nothingToCreate: "Всё отсюда уже создано.",
    createN: "Создать заказов: {n}", dropProposal: "Отбросить", f_product: "Изделие", f_qty: "Кол-во", f_customer: "Клиент",
    f_due_date: "Отдать к", f_variant: "Ткань · каркас", checkWord: "Проверь", missing: "Нет", toCheck: "Проверить полей: {n}",
    attention: "Требует внимания", lateBy: "опоздание {n} дн", nowAt: "сейчас", search: "Поиск: клиент, номер, изделие",
    product: "Изделие", customer: "Клиент", whereNow: "Где сейчас", dueCol: "Отдать к", nothingFound: "Ничего не найдено.",
    v_open: "Открытые", v_in_work: "В работе", v_ready: "Готовы", v_shipped: "Отгружены", v_all: "Все",
    s_accepted: "Принят", s_in_work: "В работе", s_ready: "Готов", s_shipped: "Отгружен", s_cancelled: "Отменён",
    ship: "Отгрузить", printSheets: "Напечатать листки", editOrder: "Править", orderSheet: "Лист заказа", history: "История", undoneWord: "отменено",
    ordered: "Заказано", inProd: "В работе", readyQ: "Готово", shippedQ: "Отгружено", spec: "Описание",
    commercial: "Клиент и цена", unitPrice: "Цена / шт", created: "Создан", sourceWord: "Откуда", noHistory: "Работа ещё не отмечалась.",
    recordedAt: "{who} · {stage}{pc} · {t}", src_manual: "вручную", src_sheet: "таблица", src_email: "письмо", src_chat: "чат", src_intake: "разбор",
    offlineRules: "офлайн-правила (без трат на модель)", draftSaved: "Текст сохранён на этом устройстве.", showMore: "Показать ещё {n}", showLess: "Свернуть",
  },
});

// Fictional samples for the demo. No real names anywhere.
const SAMPLES = {
  email: `Hi Kate,

Following our call: please put in production two Cozy armchairs in velvet beige with walnut frame for Home & Co, Austin. We need them by November 12.
Also one Round pouf, same fabric, for the same delivery.

Thanks,
Dana`,
  table: `customer,product,qty,fabric,frame,due
Nest Interiors,Lord rocking chair,1,velvet graphite,black,2026-11-10
Casa Verde,Sofa Oslo 2-seat,1,boucle milk,oak,2026-11-18
Urban Loft,Bruno chair,4,linen sand,natural,2026-11-05`,
};

const VIEWS = ["open", "in_work", "ready", "shipped", "all"];
const FIELD_ORDER = ["product", "qty", "customer", "due_date", "variant"];
const ui = { view: "open", q: "", text: "", proposal: null, picks: {} };

function nowAt(line) {
  const active = line.filter(s => s.state === "active");
  return active.length ? active.map(name).join(", ") : t("s_ready");
}

registerScreen({
  id: "orders", roles: ["office"], icon: "orders", params: ["id"], title: lang => lang === "ru" ? "Заказы" : "Orders",
  async render(root, ctx) {
    if (ctx.params.id) return renderOrder(root, ctx);
    const r = await api(`/api/orders?view=${ui.view}&q=${encodeURIComponent(ui.q)}`);
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    root.innerHTML = `
      <header class="head"><div><h1>${t("orders")}</h1><p class="sub">${esc(fmtDate(r.date, { withDay: true }))}</p></div></header>
      <section class="intake">
        <div class="intake-head"><h2>${icon("spark")}${t("newOrder")}</h2>
          <div class="samples"><span class="muted">${t("trySample")}</span>${chip(t("sampleMail"), { attrs: 'data-sample="email"' })}${chip(t("sampleTable"), { attrs: 'data-sample="table"' })}</div></div>
        <textarea id="intakeText" class="drop" rows="4" placeholder="${t("intakeHint")}">${esc(ui.text)}</textarea>
        <div class="intake-actions">
          ${btn({ label: t("readIt"), icon: "spark", kind: "primary", size: "lg", attrs: 'id="intakeGo"' })}
          <label class="btn secondary lg">${icon("file")}<span>${t("pickTable")}</span><input type="file" id="intakeFile" accept=".csv,.tsv,.txt" hidden></label>
          ${comingSoon(t("speak"), "mic")}
        </div>
        <div id="intakeOut"></div>
        <div id="ordersExtras"></div>
      </section>
      ${r.attention.length ? `<h2 class="sec hot">${icon("alert")} ${t("attention")} <b>${r.attention.length}</b></h2>
        <ul class="attention">${r.attention.slice(0, ui.allLate ? 999 : 4).map(o => `<li><button type="button" data-order="${o.id}">
          <span class="a-num">#${o.number}</span><span>${esc(o.product)} · ${esc(o.customer || t("toStock"))} · ${t("nowAt")} ${esc(nowAt(o.line))}</span>
          <span class="a-late">${t("lateBy", { n: o.late_days })}</span></button></li>`).join("")}
        ${r.attention.length > 4 ? `<li class="more"><button type="button" id="moreLate">${ui.allLate ? t("showLess") : t("showMore", { n: r.attention.length - 4 })}</button></li>` : ""}</ul>` : ""}
      <div class="filters">
        <div class="search">${icon("search")}<input id="q" class="input" type="search" placeholder="${t("search")}" value="${esc(ui.q)}"></div>
        <div class="chips">${VIEWS.map(v => chip(t("v_" + v), { on: v === ui.view, attrs: `data-view="${v}"` })).join("")}</div>
      </div>
      ${table({
        cls: "cards orders",
        cols: [{ label: "№", cls: "n" }, { label: t("product") }, { label: t("customer") }, { label: t("whereNow"), cls: "w-stage" }, { label: t("dueCol"), cls: "nowrap" }],
        rows: r.orders.map(o => ({
          attrs: `data-order="${o.id}"`,
          cells: [
            `#${o.number}`,
            `${esc(o.product)}${o.qty > 1 ? ` <span class="muted">× ${o.qty}</span>` : ""}<small>${esc(o.variant)}</small>`,
            esc(o.customer || t("toStock")),
            o.status === "shipped" || o.status === "ready" || o.status === "cancelled" ? statusPill(o.status) : stageLine(o.line, { qty: o.qty }),
            dueText(o),
          ],
        })),
        emptyText: t("nothingFound"),
      })}`;
    bindIntake(root, ctx);
    OM.ordersExtras(root.querySelector("#ordersExtras"), ctx, p => {
      ui.proposal = p; ui.picks = {};
      renderProposal(root, ctx, p);
      root.querySelector("#intakeOut").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    if (ui.proposal) renderProposal(root, ctx, ui.proposal);
    root.querySelectorAll("[data-order]").forEach(el => el.onclick = () => go("orders", { id: el.dataset.order }));
    root.querySelectorAll("[data-view]").forEach(b => b.onclick = () => { ui.view = b.dataset.view; ctx.refresh(); });
    root.querySelector("#moreLate")?.addEventListener("click", () => { ui.allLate = !ui.allLate; ctx.refresh(); });
    const q = root.querySelector("#q");
    q.oninput = () => {
      clearTimeout(ui.timer);
      ui.timer = setTimeout(async () => {
        ui.q = q.value; await ctx.refresh();
        const again = document.getElementById("q");
        if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
      }, 250);
    };
    bindSoon(root, toast);
  },
});

// --- intake ------------------------------------------------------------------------
function bindIntake(root, ctx) {
  const box = root.querySelector("#intakeText");
  box.oninput = () => { ui.text = box.value; };
  const go_ = async text => {
    const b = root.querySelector("#intakeGo");
    b.disabled = true; b.querySelector("span").textContent = t("reading");
    const r = await api("/api/intake", { text });
    b.disabled = false; b.querySelector("span").textContent = t("readIt");
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    ui.proposal = r; ui.picks = {};
    renderProposal(root, ctx, r);
    root.querySelector("#intakeOut").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  root.querySelector("#intakeGo").onclick = () => go_(box.value);
  root.querySelectorAll("[data-sample]").forEach(c => c.onclick = () => { box.value = SAMPLES[c.dataset.sample]; ui.text = box.value; box.focus(); });
  const fromFile = file => file && file.text().then(text => { box.value = text; ui.text = text; go_(text); });
  root.querySelector("#intakeFile").onchange = e => fromFile(e.target.files[0]);
  box.ondragover = e => { e.preventDefault(); box.classList.add("over"); };
  box.ondragleave = () => box.classList.remove("over");
  box.ondrop = e => { e.preventDefault(); box.classList.remove("over"); fromFile(e.dataTransfer.files[0]); };
}

function markQuotes(source, quotes) {
  let html = esc(source);
  [...new Set(quotes.filter(Boolean))].sort((a, b) => b.length - a.length).forEach(q => {
    const eq = esc(q);
    if (eq.length < 2) return;
    html = html.split(eq).join(`\u0001${eq}\u0002`);
  });
  return html.replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
}

function itemReady(item, picks) {
  return FIELD_ORDER.every(k => {
    const f = item.fields[k];
    const required = ["product", "qty", "customer"].includes(k);
    if (f.status === "ok") return true;
    if (f.status === "check") return picks && picks[k] !== undefined && picks[k] !== "";
    return !required;
  });
}

const FIX_LIST = { product: "dl-products", customer: "dl-customers", variant: "dl-variants" };
let datalistsHtml = "";
OM.catalogLists().then(L => { datalistsHtml = OM.datalists(L); });

function renderProposal(root, ctx, p) {
  const out = root.querySelector("#intakeOut");
  if (!out) return;
  const quotes = p.items.flatMap(i => FIELD_ORDER.map(k => i.fields[k].quote));
  const open = p.items.map((item, idx) => ({ item, idx })).filter(x => !x.item.order_id && !x.item.removed);
  const count = open.filter(x => itemReady(x.item, ui.picks[x.idx])).length;
  let toCheck = 0;
  const card = ({ item, idx }) => `<article class="rcard ${itemReady(item, ui.picks[idx]) ? "" : "needs"}">
    <div class="r-top"><b>${t("orderN", { n: idx + 1 })}</b>${item.line ? `<i>${esc(item.line)}</i>` : ""}
      <button type="button" class="link bad" data-remove="${idx}">${t("oRemoveLine")}</button></div>
    <dl>${FIELD_ORDER.map(k => {
      const f = item.fields[k];
      const pick = (ui.picks[idx] || {})[k];
      const unresolved = f.status === "check" && (pick === undefined || pick === "");
      if (unresolved) toCheck++;
      let val;
      if (f.status === "check" && k === "due_date") {
        val = `<input type="date" data-idx="${idx}" data-field="${k}" value="${esc(pick || "")}">`;
      } else if (f.status === "check") {
        val = `<span class="opts">${f.options.map(o => chip(esc(o.label), { kind: "warn", on: String(pick) === String(o.id), attrs: `data-idx="${idx}" data-field="${k}" data-val="${esc(o.id)}"` })).join("")}</span>`;
      } else {
        val = f.status === "ok" ? `<b>${esc(f.value)}</b>` : `<span class="muted">${t("missing")}</span>`;
      }
      return `<dt class="st-${unresolved ? "check" : f.status}">${t("f_" + k)}${unresolved ? ` · ${t("checkWord")}` : ""}</dt><dd>${val}
        ${f.quote ? `<small class="quote" data-quote="${esc(f.quote)}" title="${esc(f.quote)}">${icon("link")}<span>“${esc(f.quote)}”</span></small>` : ""}
        ${unresolved && f.note ? `<small class="note">${esc(f.note)}</small>` : ""}
        <input class="input fixin" data-fidx="${idx}" data-ffield="${k}" ${FIX_LIST[k] ? `list="${FIX_LIST[k]}"` : ""}
          ${k === "due_date" ? 'type="date"' : k === "qty" ? 'type="number" min="1" max="500"' : ""}
          placeholder="${t("oFixHint")}" aria-label="${t("f_" + k)}">
        <small class="fixmsg late" id="fm-${idx}-${k}"></small></dd>`;
    }).join("")}</dl></article>`;
  const cards = open.map(card).join("");
  out.innerHTML = `
    <div class="review">
      <aside><h3><span>${t("source")}</span><span class="muted" style="text-transform:none;letter-spacing:0">${t("readBy")}: ${esc(p.model === "offline-rules" ? t("offlineRules") : p.model)}</span></h3>${p.photo_id ? `<img class="iphoto" src="/api/orders/intake/photo/${p.photo_id}" alt="${t("photoBy")}">` : ""}
        ${p.source ? `<pre class="source" id="src">${markQuotes(p.source, quotes)}</pre>` : ""}${p.note ? `<p class="late">${esc(p.note)}</p>` : ""}${datalistsHtml}</aside>
      <div><h3><span>${t("proposed")}</span><span>${open.length}</span></h3>${cards || empty(t("nothingToCreate"), "check")}
        <div class="review-foot">
          <span class="summary">${t("createN", { n: count }).replace(/^(\D*)/, "")} ${toCheck ? `· <span class="hot-ink">${t("toCheck", { n: toCheck })}</span>` : ""}</span>
          ${btn({ label: t("oAddLine"), icon: "plus", kind: "secondary", attrs: 'id="intakeAdd"' })}
          ${btn({ label: t("dropProposal"), kind: "ghost", attrs: 'id="intakeDrop"' })}
          ${btn({ label: t("createN", { n: count }), icon: "check", kind: "primary", size: "lg", attrs: 'id="intakeApply"', disabled: !count })}
        </div>
      </div>
    </div>`;
  out.querySelectorAll("[data-val]").forEach(b => b.onclick = () => {
    const idx = b.dataset.idx;
    ui.picks[idx] = { ...(ui.picks[idx] || {}), [b.dataset.field]: b.dataset.val };
    renderProposal(root, ctx, p);
  });
  out.querySelectorAll("input[data-field]").forEach(inp => inp.onchange = () => {
    const idx = inp.dataset.idx;
    ui.picks[idx] = { ...(ui.picks[idx] || {}), [inp.dataset.field]: inp.value };
    renderProposal(root, ctx, p);
  });
  // Click a quote on the right: light it up in the source on the left.
  out.querySelectorAll("[data-quote]").forEach(q => q.onclick = () => {
    const src = out.querySelector("#src");
    if (!src) return;
    src.querySelectorAll("mark.on").forEach(m => m.classList.remove("on"));
    const m = [...src.querySelectorAll("mark")].find(x => x.textContent === q.dataset.quote);
    if (m) { m.classList.add("on"); m.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  });
  out.querySelector("#intakeDrop").onclick = () => { ui.proposal = null; out.innerHTML = ""; };
  // Any field can be fixed by typing; the server checks it against the catalog.
  const redraw = () => renderProposal(root, ctx, p);
  out.querySelectorAll(".fixin").forEach(inp => {
    const go_ = async () => {
      if (inp.value === "") return;
      const msg = out.querySelector(`#fm-${inp.dataset.fidx}-${inp.dataset.ffield}`);
      if (await OM.fixField(p, inp.dataset.fidx, inp.dataset.ffield, inp.value, msg)) redraw();
    };
    inp.onkeydown = e => { if (e.key === "Enter") go_(); };
    if (inp.type === "date") inp.onchange = go_;
  });
  if (out._fixed) out.removeEventListener("fixed", out._fixed);
  out._fixed = redraw; out.addEventListener("fixed", redraw);
  out.querySelectorAll("[data-remove]").forEach(b => b.onclick = async () => { if (await OM.removeLine(p, b.dataset.remove)) redraw(); });
  out.querySelector("#intakeAdd").onclick = async () => { if (await OM.addLine(p)) redraw(); };
  out.querySelector("#intakeApply").onclick = async () => {
    const picks = {};
    for (const [idx, fields] of Object.entries(ui.picks)) {
      picks[idx] = {};
      for (const [k, v] of Object.entries(fields)) picks[idx][k] = /^\d+$/.test(v) ? Number(v) : v;
    }
    const r = await api(`/api/intake/${p.id}/apply`, { picks });
    if (r.silent) return;
    flash(r.ok, r.message);
    if (!r.ok) return;
    if (r.waiting) {
      p.items.forEach((item, idx) => { if (itemReady(item, ui.picks[idx]) && !item.order_id) item.order_id = true; });
    } else {
      ui.proposal = null; ui.text = "";
    }
    ctx.refresh();
  };
}

// --- order card -------------------------------------------------------------------
async function renderOrder(root, ctx) {
  const o = await api(`/api/orders/${ctx.params.id}`);
  if (!o.ok) { if (!o.silent) root.innerHTML = empty(o.message, "alert"); return; }
  const main = o.status === "ready" && o.customer_id
    ? btn({ label: t("ship"), icon: "truck", kind: "primary", size: "lg", attrs: 'id="toShip"' })
    : (o.status === "accepted" || o.status === "in_work") ? btn({ label: t("printSheets"), icon: "print", kind: "primary", attrs: 'id="printSheets"' }) : "";
  root.innerHTML = `
    ${btn({ label: t("orders"), icon: "back", kind: "ghost", attrs: 'id="back"' })}
    <header class="order-head">
      <div><h1>#${o.number} · ${esc(o.product)}${o.qty > 1 ? ` <span class="muted">× ${o.qty}</span>` : ""}</h1>
        <p class="facts">${esc(o.variant)}<br>${o.customer ? `${esc(o.customer)}, ${esc(o.city || "")} · ${t("due")} ${dueText(o)}` : t("toStock")}</p></div>
      <div class="actions">${statusPill(o.status)}${main}<a class="btn ghost md" href="/paperwork/sheets/order/${o.id}?lang=${state.lang}" target="_blank" rel="noopener">${icon("paper")}<span>${t("orderSheet")}</span></a>${btn({ label: t("editOrder"), icon: "edit", kind: "ghost", attrs: 'id="editOrder"' })}</div>
    </header>
    <div class="qtys">
      ${stat(t("ordered"), o.qty)}
      ${stat(t("inProd"), Math.max(0, o.qty - o.finished - (o.status === "accepted" ? o.qty : 0)))}
      ${stat(t("readyQ"), Math.max(0, o.finished - o.shipped), { kind: o.finished - o.shipped > 0 ? "good" : "" })}
      ${stat(t("shippedQ"), o.shipped)}
    </div>
    <ol class="rail">${o.line.map(s => `<li class="${s.state}">
      <span class="r-name">${s.state === "done" ? icon("check") : ""}${esc(name(s))}</span>
      <span class="r-n">${s.done}<span class="muted" style="font-size:.6em"> / ${o.qty}</span></span>
      <span class="r-who">${esc(s.people.join(", "))}</span></li>`).join("")}</ol>
    <div id="orderMore" class="omore"></div>
    <div class="order-grid">
      <section>
        <h2 class="sec">${t("history")} <b>${o.history.length}</b></h2>
        ${o.history.length ? `<ul class="history">${o.history.map(h => `<li class="${h.kind}">
          <span class="h-t">${esc(fmtDate(h.created_at))} ${esc(fmtTime(h.created_at))}</span>
          <span>${esc(h.person)} · ${esc(state.lang === "ru" ? h.stage_ru : h.stage)}${o.qty > 1 ? ` · ${t("pc")} ${h.seq}` : ""}${h.kind === "undo" ? ` · <b>${t("undoneWord")}</b>` : ""}</span></li>`).join("")}</ul>` : `<p class="muted">${t("noHistory")}</p>`}
      </section>
      <aside class="order-side">
        <div class="card"><h2 class="sec" style="margin-top:0">${t("commercial")}</h2>
          <dl><dt>${t("customer")}</dt><dd>${esc(o.customer || t("toStock"))}</dd>
            <dt>${t("unitPrice")}</dt><dd>${money(o.currency, o.price)}</dd>
            <dt>${t("dueCol")}</dt><dd>${o.customer ? esc(fmtDate(o.due_date, { withYear: true })) : "—"}</dd>
            <dt>${t("created")}</dt><dd>${esc(fmtDate(o.created_at, { withYear: true }))}</dd>
            <dt>${t("sourceWord")}</dt><dd>${esc(t("src_" + o.source) === "src_" + o.source ? o.source : t("src_" + o.source))}</dd></dl></div>
      </aside>
    </div>`;
  root.querySelector("#back").onclick = () => go("orders");
  root.querySelector("#toShip")?.addEventListener("click", () => go("shipping", { id: o.customer_id }));
  root.querySelector("#printSheets")?.addEventListener("click", async () => {
    // A first print gets copy 1; a repeat is refused in words until a reason is chosen
    // on the Paperwork page, so no second paper sheet appears by accident.
    const win = window.open("", "_blank");
    const r = await api("/api/paperwork/sheets/print", { scope: "orders", order_ids: [o.id], idem_key: idem() });
    if (r.silent) { if (win) win.close(); return; }
    flash(r.ok, r.message);
    if (!r.ok) { if (win) win.close(); return; }
    if (win) win.location = `${r.url}?lang=${state.lang}`;
  });
  const more = root.querySelector("#orderMore");
  root.querySelector("#editOrder").onclick = () => OM.openEdit(more);
  OM.orderMore(more, o, ctx);
  bindSoon(root, toast);
}
