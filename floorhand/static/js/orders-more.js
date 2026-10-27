// Order depth for the office (features/orders.py, intake.py, reconcile.py, catalog.py):
// change any field with history, rework with who pays, cancel to stock with offers,
// comments; intake where the person fixes any field and adds or removes lines; a photo
// of an order; the manager's sheet re-upload with its difference; the catalog by one
// sentence. Every write is a server call that answers in words; a decision the server
// needs comes back as {needs, options} and nothing changes until it is chosen.
import { addStrings, api, t, esc, name, flash, idem, fmtDate, fmtTime } from "./core.js";
import { icon, btn, chip } from "./ui.js";

addStrings({
  en: {
    oMore: "Change, rework, cancel", oEdit: "Change", oSave: "Save", oCancelEdit: "Close",
    oReason: "Why (shown in the history)", oItems: "Items", oSendBack: "Send back",
    oToStage: "Back to", oCause: "What is wrong", oPayer: "Who pays", oWhatWrong: "Describe it for the worker",
    payer_worker: "The worker who made it", payer_shop: "The shop", payer_customer: "The customer",
    c_fabric_defect: "Fabric defect", c_sewing_error: "Sewing error", c_upholstery_error: "Upholstery error",
    c_assembly_error: "Assembly error", c_damaged: "Damaged", c_wrong_size: "Wrong size",
    c_customer_changed: "Customer changed it", c_other: "Other",
    oCancelOrder: "Cancel the order", oCancelWhy: "Why is it cancelled?",
    oUnfinishedStop: "Items in work stop", oUnfinishedStock: "Items in work go on, to stock",
    oCancelGo: "Cancel order", oStock: "In stock now, fits this order", oUseIt: "Use it",
    oComments: "Comments", oAddComment: "Add comment", oChanges: "Changes", oLocked: "locked",
    oNote: "Note for the shop", oShipped: "shipped", oFinished: "finished", oAt: "at",
    oDecide: "Decide first", f_note: "Note",
    oPhoto: "Photo of an order", oSheet: "Upload the orders sheet", oDemoSheet: "Demo sheet (.xlsx)",
    oCatalog: "Catalog in one sentence", oCatalogHint: "add chair Milo, stages sewing and assembly, rate 3, price 160",
    oCatalogGo: "Read it", oYes: "Yes, do it", oNo: "No", oPrice: "Price", oRate: "Rate",
    oAddLine: "Add a line", oRemoveLine: "Remove line", oFixHint: "Type to change, Enter to save",
    oSheetApply: "Apply {n} row(s)", oAcceptDanger: "Accept all that touch done work",
    k_new: "New", k_changed: "Changed", k_link: "Typed earlier — link", k_cancel: "Cancelled in the sheet",
    k_suspect: "Renumbered?", k_missing: "Not in the sheet", k_conflict: "One number, two rows",
    k_check: "Cannot create", k_blocked: "Cannot apply", k_same: "Same", k_skip: "Nothing to do",
    ch_create: "Create", ch_apply: "Apply", ch_skip: "Skip", ch_keep: "Keep", ch_cancel: "Cancel it",
    ch_link: "Link", ch_new: "New order", sheetSkipped: "Rows skipped", photoBy: "Photo", oDrop: "Discard",
    oMoreTools: "More ways in",
  },
  ru: {
    oMore: "Изменить, переделка, отмена", oEdit: "Изменить", oSave: "Сохранить", oCancelEdit: "Закрыть",
    oReason: "Почему (видно в истории)", oItems: "Штуки", oSendBack: "На переделку",
    oToStage: "Вернуть на", oCause: "Что не так", oPayer: "Кто платит", oWhatWrong: "Опиши для рабочего",
    payer_worker: "Рабочий, который ошибся", payer_shop: "Цех", payer_customer: "Клиент",
    c_fabric_defect: "Брак ткани", c_sewing_error: "Ошибка шитья", c_upholstery_error: "Ошибка обивки",
    c_assembly_error: "Ошибка сборки", c_damaged: "Повреждено", c_wrong_size: "Не тот размер",
    c_customer_changed: "Клиент передумал", c_other: "Другое",
    oCancelOrder: "Отменить заказ", oCancelWhy: "Почему отменён?",
    oUnfinishedStop: "Штуки в работе остановить", oUnfinishedStock: "Штуки в работе доделать на склад",
    oCancelGo: "Отменить заказ", oStock: "Есть на складе, подходит", oUseIt: "Взять",
    oComments: "Комментарии", oAddComment: "Добавить", oChanges: "Изменения", oLocked: "закрыто",
    oNote: "Заметка для цеха", oShipped: "отгружено", oFinished: "готово", oAt: "на",
    oDecide: "Сначала решить", f_note: "Заметка",
    oPhoto: "Фото заказа", oSheet: "Загрузить таблицу заказов", oDemoSheet: "Демо-таблица (.xlsx)",
    oCatalog: "Каталог одной фразой", oCatalogHint: "добавь стул Мило, этапы шитьё и сборка, ставка 3, цена 160",
    oCatalogGo: "Разобрать", oYes: "Да, сделать", oNo: "Нет", oPrice: "Цена", oRate: "Ставка",
    oAddLine: "Добавить строку", oRemoveLine: "Убрать строку", oFixHint: "Впиши, Enter — сохранить",
    oSheetApply: "Применить строк: {n}", oAcceptDanger: "Принять всё, что задевает сделанную работу",
    k_new: "Новые", k_changed: "Изменены", k_link: "Заведён раньше — связать", k_cancel: "Отмена в таблице",
    k_suspect: "Перенумеровали?", k_missing: "Нет в таблице", k_conflict: "Один номер — две строки",
    k_check: "Не создать", k_blocked: "Не применить", k_same: "Без изменений", k_skip: "Нечего делать",
    ch_create: "Создать", ch_apply: "Применить", ch_skip: "Пропустить", ch_keep: "Оставить", ch_cancel: "Отменить",
    ch_link: "Связать", ch_new: "Новый заказ", sheetSkipped: "Пропущено строк", photoBy: "Фото", oDrop: "Отбросить",
    oMoreTools: "Другие способы",
  },
});

const OFIELDS = ["product", "variant", "qty", "customer", "due_date", "note"];
let lists = null;
export async function catalogLists() {
  if (!lists) { const r = await api("/api/orders/catalog/lists"); if (r.ok) lists = r; }
  return lists || { products: [], customers: [], fabrics: [], frames: [], stages: [], causes: [], payers: [] };
}
export function datalists(L) {
  const dl = (id, values) => `<datalist id="${id}">${values.map(v => `<option value="${esc(v)}">`).join("")}</datalist>`;
  return dl("dl-products", L.products.map(p => p.label)) + dl("dl-customers", L.customers.map(c => c.label))
    + dl("dl-variants", L.fabrics.flatMap(f => [f, ...L.frames.map(fr => `${f} · ${fr}`)]));
}
function fileB64(file) {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",").pop());
    r.onerror = bad;
    r.readAsDataURL(file);
  });
}
const when = s => s ? `${esc(fmtDate(s))} ${esc(fmtTime(s))}` : "";

// --- order card ---------------------------------------------------------------------
/** The order card's depth: fields, items with rework, cancel, stock offers, comments, changes. */
export async function orderMore(host, o, ctx) {
  const s = await api(`/api/orders/${o.id}/story`);
  if (!s.ok || !host) return;
  const L = await catalogLists();
  const value = f => f === "product" ? o.product : f === "customer" ? (o.customer || t("toStock"))
    : f === "due_date" ? (o.due_date ? fmtDate(o.due_date, { withYear: true }) : "—") : f === "qty" ? o.qty
    : f === "variant" ? (o.variant || "—") : (s.note || "—");
  const offers = s.offers.length ? `<h2 class="sec">${t("oStock")} <b>${s.offers.length}</b></h2><ul class="plain">${s.offers.map(x =>
    `<li class="offer"><span><b>${esc(x.stock_label)}</b> ${esc(x.variant)} · ${esc(x.progress)}
      ${x.finished ? `· <b>${t("oFinished")}</b>` : ""} · <span class="muted">${esc(x.match)}</span></span>
      ${btn({ label: t("oUseIt"), kind: "primary", attrs: `data-take="${x.stock_unit_id}"` })}</li>`).join("")}</ul>` : "";
  const unitRow = u => `<li><div class="u-head"><b>${esc(u.label)}</b> · ${u.shipped ? t("oShipped") : u.finished ? t("oFinished")
      : `${t("oAt")} ${esc(name(u.at))}`}
      ${!u.shipped && u.started && !s.cancelled ? btn({ label: t("oSendBack"), icon: "undo", kind: "ghost", attrs: `data-rework="${u.unit_id}"` }) : ""}</div>
    <div class="u-steps">${u.steps.map(x => `<span class="step ${x.done ? "done" : ""}" title="${esc(x.by || "")} ${esc(x.date || "")}">${x.done ? icon("check") : ""}${esc(name(x))}${x.by ? ` <small>${esc(x.by)}</small>` : ""}</span>`).join("")}</div></li>`;
  host.innerHTML = `
    ${s.note ? `<p class="onote"><b>${t("oNote")}:</b> ${esc(s.note)}</p>` : ""}
    ${s.ext_book ? `<p class="muted">${esc(s.ext_book)} · № ${esc(s.ext_ref || "—")}</p>` : ""}
    ${offers}
    <details class="disc" id="omoreD"><summary>${icon("edit")} ${t("oMore")}${icon("chevron", "chev")}</summary><div class="disc-body">
      <dl class="ofields">${OFIELDS.map(f => `<dt>${t("f_" + f)}</dt><dd>
        <span>${esc(value(f))}</span>
        ${s.locks[f] ? `<small class="muted">${t("oLocked")}: ${esc(s.locks[f])}</small>` : ""}
        ${chip(t("oEdit"), { attrs: `data-edit="${f}" ${s.cancelled && f !== "note" ? "disabled" : ""}` })}
        <div class="oform" id="of-${f}" hidden></div></dd>`).join("")}</dl>
      <h2 class="sec">${t("oItems")} <b>${s.units.length}</b></h2>
      <ul class="units">${s.units.map(unitRow).join("")}</ul>
      <div class="oform panel" id="of-rework" hidden></div>
      ${s.cancelled ? "" : `<h2 class="sec">${t("oCancelOrder")}</h2>
      <div class="oform">
        <input class="input" id="cancelWhy" placeholder="${t("oCancelWhy")}">
        <div class="chips">${chip(t("oUnfinishedStop"), { on: true, attrs: 'data-unf="stop"' })}${chip(t("oUnfinishedStock"), { attrs: 'data-unf="to_stock"' })}</div>
        <div>${btn({ label: t("oCancelGo"), icon: "x", kind: "danger", attrs: 'id="cancelGo"' })}</div></div>`}
    </div></details>
    <details class="disc"><summary>${t("oComments")} <span class="count">${s.comments.length}</span>${icon("chevron", "chev")}</summary><div class="disc-body">
      <div class="ask" style="margin-top:0"><input class="input" id="cText">${btn({ label: t("oAddComment"), attrs: 'id="cAdd"' })}</div>
      <ul class="history">${s.comments.map(c => `<li><span class="h-t">${when(c.at)}</span><span><b>${esc(c.person)}</b>: ${esc(c.text)}</span></li>`).join("")}</ul>
    </div></details>
    <details class="disc"><summary>${t("oChanges")} <span class="count">${s.changes.length + s.reworks.length}</span>${icon("chevron", "chev")}</summary><div class="disc-body">
      <ul class="history">${s.changes.map(c => `<li><span class="h-t">${when(c.at)}</span><span><b>${esc(c.person)}</b> ·
        ${esc(c.field)}: ${esc(c.old ?? "—")} → <b>${esc(c.new ?? "—")}</b>${c.reason ? ` · <i>${esc(c.reason)}</i>` : ""}
        ${c.source !== "card" ? ` · <small>${esc(c.source)}</small>` : ""}</span></li>`).join("")}</ul>
    </div></details>
    ${datalists(L)}`;
  const again = () => ctx.refresh();
  const answer = (r, ok) => { if (r.silent) return; flash(r.ok, r.message); if (r.ok && ok !== false) again(); };

  host.querySelectorAll("[data-take]").forEach(b => b.onclick = async () =>
    answer(await api(`/api/orders/${o.id}/take-from-stock`, { unit_ids: [Number(b.dataset.take)] })));

  host.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => {
    const f = b.dataset.edit;
    const form = host.querySelector(`#of-${f}`);
    form.hidden = !form.hidden;
    if (form.hidden) return;
    const input = f === "due_date" ? `<input type="date" class="input ov" value="${esc(o.due_date || "")}">`
      : f === "qty" ? `<input type="number" min="1" max="500" class="input ov" value="${o.qty}" inputmode="numeric">`
      : f === "note" ? `<textarea class="input ov" rows="2">${esc(s.note)}</textarea>`
      : `<input class="input ov" list="${f === "product" ? "dl-products" : f === "customer" ? "dl-customers" : "dl-variants"}"
           value="${esc(f === "customer" ? (o.customer || "") : f === "product" ? o.product : o.variant)}">`;
    form.innerHTML = `${input}<input class="input or" placeholder="${t("oReason")}">
      <div class="oneeds"></div>
      <div class="d-acts">${btn({ label: t("oSave"), kind: "primary", cls: "os" })}${btn({ label: t("oCancelEdit"), kind: "ghost", cls: "oc" })}</div>`;
    form.querySelector(".oc").onclick = () => { form.hidden = true; };
    const send = async extra => {
      let v = form.querySelector(".ov").value;
      if (f === "customer" && !v) v = "stock";
      const r = await api(`/api/orders/${o.id}/edit`, { field: f, value: v, reason: form.querySelector(".or").value, ...extra });
      if (r.silent) return;
      if (r.ok) { flash(true, r.message); return again(); }
      if (!r.needs) return flash(false, r.message);
      // A decision is needed: show the choices; nothing was changed yet.
      const needs = form.querySelector(".oneeds");
      needs.innerHTML = `<p class="late">${esc(r.message)}</p><div class="chips">${r.options.map(op =>
        chip(esc(op.label), { kind: "warn", attrs: `data-op="${esc(op.id)}"` })).join("")}</div>
        ${r.needs === "rework" || r.payer_needed ? `<p class="muted">${t("oPayer")}</p><div class="chips">${L.payers.map(p =>
          chip(t("payer_" + p), { attrs: `data-payer="${p}"` })).join("")}</div>` : ""}`;
      let payer = null;
      needs.querySelectorAll("[data-payer]").forEach(x => x.onclick = () => {
        payer = x.dataset.payer;
        needs.querySelectorAll("[data-payer]").forEach(y => y.classList.toggle("on", y === x));
      });
      needs.querySelectorAll("[data-op]").forEach(x => x.onclick = () => {
        const op = x.dataset.op;
        if (r.needs === "rework") {
          return op === "keep" ? send({ keep_work: true }) : send({ rework_stage_id: Number(op), payer: payer || "customer" });
        }
        send({ started: op, payer: payer || undefined });
      });
    };
    form.querySelector(".os").onclick = () => send({});
  });

  host.querySelectorAll("[data-rework]").forEach(b => b.onclick = () => {
    const u = s.units.find(x => x.unit_id === Number(b.dataset.rework));
    const form = host.querySelector("#of-rework");
    form.hidden = false;
    let stageId = null, payer = null, cause = null;
    form.innerHTML = `<h3>${t("oSendBack")}: ${esc(u.label)}</h3>
      <p class="muted">${t("oToStage")}</p><div class="chips">${u.steps.filter(x => x.done).map(x =>
        chip(`${esc(name(x))} · ${esc(x.by || "")}`, { attrs: `data-st="${x.stage_id}"` })).join("")}</div>
      <p class="muted">${t("oCause")}</p><div class="chips">${L.causes.map(c => chip(t("c_" + c), { attrs: `data-cause="${c}"` })).join("")}</div>
      <p class="muted">${t("oPayer")}</p><div class="chips">${L.payers.map(p => chip(t("payer_" + p), { attrs: `data-payer="${p}"` })).join("")}</div>
      <input class="input rw" placeholder="${t("oWhatWrong")}">
      <div>${btn({ label: t("oSendBack"), icon: "undo", kind: "primary", attrs: 'id="rwGo"' })}</div>`;
    const pick = (attr, set) => form.querySelectorAll(`[data-${attr}]`).forEach(x => x.onclick = () => {
      set(x.dataset[attr]);
      form.querySelectorAll(`[data-${attr}]`).forEach(y => y.classList.toggle("on", y === x));
    });
    pick("st", v => { stageId = Number(v); });
    pick("cause", v => { cause = v; });
    pick("payer", v => { payer = v; });
    form.scrollIntoView({ block: "nearest", behavior: "smooth" });
    form.querySelector("#rwGo").onclick = async () => {
      if (!stageId || !payer) return flash(false, `${t("oDecide")}: ${t("oToStage")} · ${t("oPayer")}`);
      answer(await api(`/api/orders/${o.id}/rework`, {
        unit_ids: [u.unit_id], stage_id: stageId, payer, cause: cause || "other",
        reason: form.querySelector(".rw").value, idem_key: idem() }));
    };
  });

  let unfinished = "stop";
  host.querySelectorAll("[data-unf]").forEach(x => x.onclick = () => {
    unfinished = x.dataset.unf;
    host.querySelectorAll("[data-unf]").forEach(y => y.classList.toggle("on", y === x));
  });
  host.querySelector("#cancelGo")?.addEventListener("click", async () =>
    answer(await api(`/api/orders/${o.id}/cancel`, { reason: host.querySelector("#cancelWhy").value, unfinished })));
  host.querySelector("#cAdd").onclick = async () =>
    answer(await api(`/api/orders/${o.id}/comment`, { text: host.querySelector("#cText").value }));
}

/** "Edit" on the order card header opens the field list. */
export function openEdit(host) {
  const d = host.querySelector("#omoreD");
  if (d) { d.open = true; d.scrollIntoView({ behavior: "smooth", block: "start" }); }
}

// --- orders page: photo, sheet, catalog --------------------------------------------------
const sheetState = { p: null, dec: {} };
const KIND_ORDER = ["suspect", "conflict", "cancel", "changed", "link", "new", "missing", "check", "blocked", "skip", "same"];

/** Photo of an order, the manager's sheet, the catalog sentence. onProposal(p) shows intake. */
export function ordersExtras(host, ctx, onProposal) {
  if (!host) return;
  host.innerHTML = `
    <details class="disc"><summary>${icon("layers")} ${t("oMoreTools")}${icon("chevron", "chev")}</summary><div class="disc-body">
      <div class="intake-actions">
        <label class="btn secondary">${icon("camera")}<span>${t("oPhoto")}</span><input type="file" id="oPhoto" accept="image/jpeg,image/png,image/webp" capture="environment" hidden></label>
        <label class="btn secondary">${icon("file")}<span>${t("oSheet")}</span><input type="file" id="oSheet" accept=".xlsx,.csv,.tsv,.txt" hidden></label>
        <a class="btn ghost" href="/api/orders/sheet/demo.xlsx">${icon("file")}<span>${t("oDemoSheet")}</span></a>
      </div>
      <h3 class="sec">${t("oCatalog")}</h3>
      <div class="ask" style="margin-top:0"><input class="input" id="catText" placeholder="${t("oCatalogHint")}">${btn({ label: t("oCatalogGo"), icon: "spark", attrs: 'id="catGo"' })}</div>
      <div id="catOut"></div>
    </div></details>
    <div id="sheetOut"></div>`;
  if (sheetState.p) { host.querySelector("details").open = true; renderSheet(host); }

  host.querySelector("#oPhoto").onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    const r = await api("/api/orders/intake/photo", { data_b64: await fileB64(file), mime: file.type });
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    flash(r.items.length > 0, r.message);
    onProposal(r);
  };
  host.querySelector("#oSheet").onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    const r = await api("/api/orders/sheet", { data_b64: await fileB64(file), filename: file.name });
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    sheetState.p = r; sheetState.dec = {};
    renderSheet(host);
  };
  const catGo = async () => {
    const r = await api("/api/orders/catalog", { text: host.querySelector("#catText").value });
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    renderCatalogCard(host, r);
  };
  host.querySelector("#catGo").onclick = catGo;
  host.querySelector("#catText").onkeydown = e => { if (e.key === "Enter") catGo(); };
}

function renderSheet(host) {
  const out = host.querySelector("#sheetOut");
  const p = sheetState.p;
  if (!out || !p) return;
  const dec = sheetState.dec;
  const choice = r => dec[r.key] !== undefined ? dec[r.key] : r.default;
  const acting = p.rows.filter(r => { const c = choice(r); return c && !["skip", "keep"].includes(c); });
  const danger = p.rows.filter(r => (r.danger || r.kind === "cancel") && !choice(r));
  const row = r => `<li class="srow k-${r.kind}">
    <div><b>${r.number ? "#" + r.number : esc(r.ref || "")}</b> <small class="muted">${esc(r.where || "")}${r.ref && r.number ? " · № " + esc(r.ref) : ""}</small></div>
    ${r.words ? `<div>${esc(r.words)}</div>` : ""}
    ${(r.changes || []).length ? `<ul class="plain">${r.changes.map(c => `<li class="lvl-${c.level}">${esc(c.field)}: ${esc(c.old ?? "—")} → <b>${esc(c.new)}</b>${c.words ? ` · <small>${esc(c.words)}</small>` : ""}</li>`).join("")}</ul>` : ""}
    ${(r.info || []).map(i => `<div class="muted">${esc(i)}</div>`).join("")}
    ${!p.done && (r.choices || []).length > 1 ? `<div class="chips">${r.choices.map(c =>
      chip(t("ch_" + c), { on: choice(r) === c, attrs: `data-key="${esc(r.key)}" data-ch="${c}"` })).join("")}</div>` : ""}
    ${r.outcome ? `<div class="muted">${esc(r.outcome)}</div>` : ""}</li>`;
  const groups = KIND_ORDER.map(k => [k, p.rows.filter(r => r.kind === k)]).filter(([, rs]) => rs.length);
  out.innerHTML = `<div class="panel sheetdiff">
    <h3>${esc(p.book)} · ${esc(p.message || p.summary.words)}</h3>
    ${groups.map(([k, rs]) => k === "same"
      ? `<details class="disc"><summary>${t("k_same")} <span class="count">${rs.length}</span>${icon("chevron", "chev")}</summary><div class="disc-body"><ul class="plain">${rs.map(row).join("")}</ul></div></details>`
      : `<h2 class="sec">${t("k_" + k)} <b>${rs.length}</b></h2><ul class="plain">${rs.map(row).join("")}</ul>`).join("")}
    ${p.skipped.length ? `<details class="disc"><summary>${t("sheetSkipped")} <span class="count">${p.skipped.length}</span>${icon("chevron", "chev")}</summary><div class="disc-body"><ul class="plain">${p.skipped.map(s =>
      `<li class="muted">${esc(s.where)} · ${esc(s.why)}</li>`).join("")}</ul></div></details>` : ""}
    ${p.done ? "" : `<div class="review-foot">
      ${danger.length ? btn({ label: `${t("oAcceptDanger")} · ${danger.length}`, kind: "secondary", attrs: 'id="sheetDanger"' }) : ""}
      ${btn({ label: t("oDrop"), kind: "ghost", attrs: 'id="sheetDrop"' })}
      ${btn({ label: t("oSheetApply", { n: acting.length }), icon: "check", kind: "primary", size: "lg", attrs: 'id="sheetApply"', disabled: !acting.length })}</div>`}</div>`;
  out.querySelectorAll("[data-ch]").forEach(b => b.onclick = () => { dec[b.dataset.key] = b.dataset.ch; renderSheet(host); });
  out.querySelector("#sheetDanger")?.addEventListener("click", () => { danger.forEach(r => { dec[r.key] = r.choices[0]; }); renderSheet(host); });
  out.querySelector("#sheetDrop")?.addEventListener("click", () => { sheetState.p = null; out.innerHTML = ""; });
  out.querySelector("#sheetApply")?.addEventListener("click", async e => {
    const go = e.currentTarget;
    go.disabled = true;
    const r = await api(`/api/orders/sheet/${p.id}/apply`, { decisions: dec });
    if (r.silent) return;
    flash(r.ok && !(r.errors || []).length, r.message);
    if (!r.ok) { go.disabled = false; return; }
    sheetState.p = { ...p, done: true, message: r.message };
    renderSheet(host);
    sheetState.p = null;
  });
}

function renderCatalogCard(host, p) {
  const out = host.querySelector("#catOut");
  const askPrice = p.questions.includes("price");
  const askRates = p.rates ? p.rates.filter(r => r.amount === null) : [];
  out.innerHTML = `<article class="decision catcard">
    <span class="d-kind">${icon("spark")}${t("oCatalog")}</span>
    <h3>${esc(p.card)}</h3>
    <p class="muted">“${esc(p.source)}” · ${esc(p.model)}</p>
    ${askPrice ? `<label>${t("oPrice")} <input class="input" type="number" min="0" id="catPrice" inputmode="numeric"></label>` : ""}
    ${askRates.map(r => `<label>${t("oRate")} · ${esc(name(r))} <input class="input" type="number" min="0" data-rate="${r.stage_id}" inputmode="numeric"></label>`).join("")}
    <div class="d-acts">${btn({ label: t("oYes"), icon: "check", kind: "primary", attrs: 'id="catYes"' })}${btn({ label: t("oNo"), icon: "x", attrs: 'id="catNo"' })}</div></article>`;
  out.querySelector("#catNo").onclick = () => { out.innerHTML = ""; };
  out.querySelector("#catYes").onclick = async () => {
    const rates = {};
    out.querySelectorAll("[data-rate]").forEach(i => { if (i.value !== "") rates[i.dataset.rate] = Number(i.value); });
    const pr = out.querySelector("#catPrice");
    const r = await api(`/api/orders/catalog/${p.id}/apply`, { price: pr && pr.value !== "" ? Number(pr.value) : null, rates });
    if (r.silent) return;
    flash(r.ok, r.message);
    if (r.ok) { out.innerHTML = ""; lists = null; }
  };
}

// --- intake: the person fixes any field --------------------------------------------------
/** /fix checks a typed value against the catalog; on a miss it offers the closest names. */
export async function fixField(p, idx, field, value, msgEl) {
  const r = await api(`/api/orders/intake/${p.id}/fix`, { idx: Number(idx), field, value });
  if (r.silent) return null;
  if (!r.ok) {
    if (msgEl) {
      msgEl.innerHTML = esc(r.message) + (r.options || []).map(o =>
        ` ${chip(esc(o.label), { kind: "warn", attrs: `data-sugg="${esc(o.id)}"` })}`).join("");
      msgEl.querySelectorAll("[data-sugg]").forEach(b => b.onclick = async () => {
        const again = await fixField(p, idx, field, b.dataset.sugg, msgEl);
        if (again) msgEl.dispatchEvent(new CustomEvent("fixed", { bubbles: true }));
      });
    } else flash(false, r.message);
    return null;
  }
  p.items[idx] = r.item;
  return r.item;
}
export async function addLine(p) {
  const r = await api(`/api/orders/intake/${p.id}/line`, {});
  if (!r.ok) { if (!r.silent) flash(false, r.message); return false; }
  p.items.push(r.item);
  return true;
}
export async function removeLine(p, idx) {
  const r = await api(`/api/orders/intake/${p.id}/line/${idx}/remove`, {});
  if (!r.ok) { if (!r.silent) flash(false, r.message); return false; }
  p.items[Number(idx)].removed = true;
  return true;
}
