// Order depth on the office screens: edit any field with history, rework with
// who pays, cancel to stock with offers, comments; intake where the person can
// fix any field, add or remove lines, read a photo; the manager's sheet
// re-upload with its difference; the catalog by one sentence.
// Loaded after app.js; uses its helpers (api, flash, t, esc, state, stageName).
"use strict";

Object.assign(STRINGS.en, {
  oMore: "More: change, rework, cancel", oEdit: "Change", oSave: "Save", oCancelEdit: "Close",
  oReason: "Why (shown in the history)", oItems: "Items", oSendBack: "Send back",
  oToStage: "Back to", oCause: "What is wrong", oPayer: "Who pays", oWhatWrong: "Describe it for the worker",
  payer_worker: "The worker who made it", payer_shop: "The shop", payer_customer: "The customer",
  c_fabric_defect: "Fabric defect", c_sewing_error: "Sewing error", c_upholstery_error: "Upholstery error",
  c_assembly_error: "Assembly error", c_damaged: "Damaged", c_wrong_size: "Wrong size",
  c_customer_changed: "Customer changed it", c_other: "Other",
  oCancelOrder: "Cancel the order", oCancelWhy: "Why is it cancelled?",
  oUnfinishedStop: "Items in work stop", oUnfinishedStock: "Items in work go on, to stock",
  oCancelGo: "CANCEL ORDER", oStock: "In stock now, fits this order", oUseIt: "Use it",
  oComments: "Comments", oAddComment: "Add comment", oChanges: "Changes", oLocked: "locked",
  oNote: "Note for the shop", oShipped: "shipped", oFinished: "finished", oAt: "at",
  oDecide: "Decide first", f_note: "Note",
  oPhoto: "Photo of an order", oSheet: "Upload the orders sheet", oDemoSheet: "Demo sheet (.xlsx)",
  oCatalog: "Catalog", oCatalogHint: "add chair Milo, stages sewing and assembly, rate 3, price 160",
  oCatalogGo: "Read it", oYes: "YES, DO IT", oNo: "No", oPrice: "Price", oRate: "Rate",
  oAddLine: "+ Add a line", oRemoveLine: "Remove line", oFixHint: "Type to change, Enter to save",
  oSheetApply: "APPLY {n} ROW(S)", oAcceptDanger: "Accept all that touch done work",
  k_new: "New", k_changed: "Changed", k_link: "Typed earlier — link", k_cancel: "Cancelled in the sheet",
  k_suspect: "Renumbered?", k_missing: "Not in the sheet", k_conflict: "One number, two rows",
  k_check: "Cannot create", k_blocked: "Cannot apply", k_same: "Same", k_skip: "Nothing to do",
  ch_create: "Create", ch_apply: "Apply", ch_skip: "Skip", ch_keep: "Keep", ch_cancel: "Cancel it",
  ch_link: "Link", ch_new: "New order", sheetSkipped: "Rows skipped", photoBy: "Photo",
});
Object.assign(STRINGS.ru, {
  oMore: "Ещё: изменить, переделка, отмена", oEdit: "Изменить", oSave: "Сохранить", oCancelEdit: "Закрыть",
  oReason: "Почему (видно в истории)", oItems: "Штуки", oSendBack: "На переделку",
  oToStage: "Вернуть на", oCause: "Что не так", oPayer: "Кто платит", oWhatWrong: "Опиши для рабочего",
  payer_worker: "Рабочий, который ошибся", payer_shop: "Цех", payer_customer: "Клиент",
  c_fabric_defect: "Брак ткани", c_sewing_error: "Ошибка шитья", c_upholstery_error: "Ошибка обивки",
  c_assembly_error: "Ошибка сборки", c_damaged: "Повреждено", c_wrong_size: "Не тот размер",
  c_customer_changed: "Клиент передумал", c_other: "Другое",
  oCancelOrder: "Отменить заказ", oCancelWhy: "Почему отменён?",
  oUnfinishedStop: "Штуки в работе остановить", oUnfinishedStock: "Штуки в работе доделать на склад",
  oCancelGo: "ОТМЕНИТЬ ЗАКАЗ", oStock: "Есть на складе, подходит", oUseIt: "Взять",
  oComments: "Комментарии", oAddComment: "Добавить", oChanges: "Изменения", oLocked: "закрыто",
  oNote: "Заметка для цеха", oShipped: "отгружено", oFinished: "готово", oAt: "на",
  oDecide: "Сначала решить", f_note: "Заметка",
  oPhoto: "Фото заказа", oSheet: "Загрузить таблицу заказов", oDemoSheet: "Демо-таблица (.xlsx)",
  oCatalog: "Каталог", oCatalogHint: "добавь стул Мило, этапы шитьё и сборка, ставка 3, цена 160",
  oCatalogGo: "Разобрать", oYes: "ДА, СДЕЛАТЬ", oNo: "Нет", oPrice: "Цена", oRate: "Ставка",
  oAddLine: "+ Добавить строку", oRemoveLine: "Убрать строку", oFixHint: "Впиши, Enter — сохранить",
  oSheetApply: "ПРИМЕНИТЬ СТРОК: {n}", oAcceptDanger: "Принять всё, что задевает сделанную работу",
  k_new: "Новые", k_changed: "Изменены", k_link: "Заведён раньше — связать", k_cancel: "Отмена в таблице",
  k_suspect: "Перенумеровали?", k_missing: "Нет в таблице", k_conflict: "Один номер — две строки",
  k_check: "Не создать", k_blocked: "Не применить", k_same: "Без изменений", k_skip: "Нечего делать",
  ch_create: "Создать", ch_apply: "Применить", ch_skip: "Пропустить", ch_keep: "Оставить", ch_cancel: "Отменить",
  ch_link: "Связать", ch_new: "Новый заказ", sheetSkipped: "Пропущено строк", photoBy: "Фото",
});

const OFIELDS = ["product", "variant", "qty", "customer", "due_date", "note"];
let lists = null;
async function catalogLists() {
  if (!lists) { const r = await api("/api/orders/catalog/lists"); if (r.ok) lists = r; }
  return lists || { products: [], customers: [], fabrics: [], frames: [], stages: [], causes: [], payers: [] };
}
function datalist(id, values) {
  return `<datalist id="${id}">${values.map(v => `<option value="${esc(v)}">`).join("")}</datalist>`;
}
function fileB64(file) {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",").pop());
    r.onerror = bad;
    r.readAsDataURL(file);
  });
}
function when(s) { return esc(String(s || "").replace("T", " ").slice(0, 16)); }

// --- order card -----------------------------------------------------------

window.FH_orderMore = async function (o) {
  const s = await api(`/api/orders/${o.id}/story`);
  if (!s.ok) return;
  const L = await catalogLists();
  const box = document.createElement("section");
  box.className = "omore";
  const value = f => f === "product" ? o.product : f === "customer" ? (o.customer || t("toStock"))
    : f === "due_date" ? (o.due_date || "—") : f === "qty" ? o.qty : f === "variant" ? (o.variant || "—") : (s.note || "—");
  const offers = s.offers.length ? `<h2>${t("oStock")}</h2><ul class="plain">${s.offers.map(x =>
    `<li class="offer"><span><b>${esc(x.stock_label)}</b> ${esc(x.variant)} · ${esc(x.progress)}
      ${x.finished ? `· <b>${t("oFinished")}</b>` : ""} · ${esc(x.match)}</span>
      <button type="button" class="chip" data-take="${x.stock_unit_id}">${t("oUseIt")}</button></li>`).join("")}</ul>` : "";
  const unitRow = u => `<li><div><b>${esc(u.label)}</b> · ${u.shipped ? t("oShipped") : u.finished ? t("oFinished")
      : `${t("oAt")} ${esc(stageName(u.at))}`}</div>
    <div class="line">${u.steps.map(x => `<span class="step ${x.done ? "done" : ""}" title="${esc(x.by || "")} ${esc(x.date || "")}">${esc(stageName(x))}${x.done ? " ✓" : ""}</span>`).join("")}</div>
    ${!u.shipped && u.started && !s.cancelled ? `<button type="button" class="link" data-rework="${u.unit_id}">${t("oSendBack")}</button>` : ""}</li>`;
  box.innerHTML = `
    ${s.note ? `<p class="onote"><b>${t("oNote")}:</b> ${esc(s.note)}</p>` : ""}
    ${s.ext_book ? `<p class="muted">${esc(s.ext_book)} · № ${esc(s.ext_ref || "—")}</p>` : ""}
    ${offers}
    <details class="omore-d"><summary>${t("oMore")}</summary>
      <dl class="ofields">${OFIELDS.map(f => `<dt>${t("f_" + f)}</dt><dd>
        <span>${esc(value(f))}</span>
        ${s.locks[f] ? `<small class="muted">${t("oLocked")}: ${esc(s.locks[f])}</small>` : ""}
        <button type="button" class="chip" data-edit="${f}" ${s.cancelled && f !== "note" ? "disabled" : ""}>${t("oEdit")}</button>
        <div class="oform" id="of-${f}" hidden></div></dd>`).join("")}</dl>
      <h2>${t("oItems")} · ${s.units.length}</h2>
      <ul class="units">${s.units.map(unitRow).join("")}</ul>
      <div class="oform" id="of-rework" hidden></div>
      ${s.cancelled ? "" : `<h2>${t("oCancelOrder")}</h2>
      <div class="oform">
        <input id="cancelWhy" placeholder="${t("oCancelWhy")}">
        <div class="chips"><button type="button" class="chip on" data-unf="stop">${t("oUnfinishedStop")}</button>
          <button type="button" class="chip" data-unf="to_stock">${t("oUnfinishedStock")}</button></div>
        <button type="button" class="mid danger" id="cancelGo">${t("oCancelGo")}</button></div>`}
    </details>
    <details><summary>${t("oComments")} · ${s.comments.length}</summary>
      <div class="row"><input id="cText" class="grow"><button type="button" class="chip" id="cAdd">${t("oAddComment")}</button></div>
      <ul class="history">${s.comments.map(c => `<li>${when(c.at)} · <b>${esc(c.person)}</b>: ${esc(c.text)}</li>`).join("")}</ul>
    </details>
    <details><summary>${t("oChanges")} · ${s.changes.length + s.reworks.length}</summary>
      <ul class="history">${s.changes.map(c => `<li>${when(c.at)} · <b>${esc(c.person)}</b> ·
        ${esc(c.field)}: ${esc(c.old ?? "—")} → <b>${esc(c.new ?? "—")}</b>${c.reason ? ` · <i>${esc(c.reason)}</i>` : ""}
        ${c.source !== "card" ? ` · <small>${esc(c.source)}</small>` : ""}</li>`).join("")}</ul>
    </details>
    ${datalist("dl-products", L.products.map(p => p.label))}
    ${datalist("dl-customers", L.customers.map(c => c.label))}
    ${datalist("dl-variants", L.fabrics.flatMap(f => [f, ...L.frames.map(fr => `${f} · ${fr}`)]))}`;
  const hist = $app.querySelector("details");
  $app.insertBefore(box, hist || null);
  const again = () => { if (state.order === o.id) renderOrder(o.id); };

  box.querySelectorAll("[data-take]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/orders/${o.id}/take-from-stock`, { unit_ids: [Number(b.dataset.take)] });
    if (!r.silent) flash(r.ok, r.message); if (r.ok) again();
  });

  box.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => {
    const f = b.dataset.edit;
    const form = box.querySelector(`#of-${f}`);
    form.hidden = !form.hidden;
    if (form.hidden) return;
    const input = f === "due_date" ? `<input type="date" class="ov" value="${esc(o.due_date || "")}">`
      : f === "qty" ? `<input type="number" min="1" max="500" class="ov" value="${o.qty}" inputmode="numeric">`
      : f === "note" ? `<textarea class="ov" rows="2">${esc(s.note)}</textarea>`
      : `<input class="ov" list="${f === "product" ? "dl-products" : f === "customer" ? "dl-customers" : "dl-variants"}"
           value="${esc(f === "customer" ? (o.customer || "") : f === "product" ? o.product : o.variant)}">`;
    form.innerHTML = `${input}<input class="or" placeholder="${t("oReason")}">
      <div class="oneeds"></div>
      <div class="row"><button type="button" class="mid os">${t("oSave")}</button>
      <button type="button" class="mid alt oc">${t("oCancelEdit")}</button></div>`;
    form.querySelector(".oc").onclick = () => { form.hidden = true; };
    const send = async extra => {
      let v = form.querySelector(".ov").value;
      if (f === "customer" && !v) v = "stock";
      const r = await api(`/api/orders/${o.id}/edit`, {
        field: f, value: v, reason: form.querySelector(".or").value, ...extra });
      if (r.silent) return;
      if (r.ok) { flash(true, r.message); return again(); }
      if (!r.needs) return flash(false, r.message);
      // a decision is needed: show the choices, nothing was changed yet
      const needs = form.querySelector(".oneeds");
      needs.innerHTML = `<p class="late">${esc(r.message)}</p><div class="chips">${r.options.map(op =>
        `<button type="button" class="chip" data-op="${esc(op.id)}">${esc(op.label)}</button>`).join("")}</div>
        ${r.needs === "rework" || r.payer_needed ? `<div class="chips">${L.payers.map(p =>
          `<button type="button" class="chip" data-payer="${p}">${t("payer_" + p)}</button>`).join("")}</div>` : ""}`;
      let payer = null;
      needs.querySelectorAll("[data-payer]").forEach(x => x.onclick = () => {
        payer = x.dataset.payer;
        needs.querySelectorAll("[data-payer]").forEach(y => y.classList.toggle("on", y === x));
      });
      needs.querySelectorAll("[data-op]").forEach(x => x.onclick = () => {
        const op = x.dataset.op;
        if (r.needs === "rework") {
          return op === "keep" ? send({ keep_work: true })
            : send({ rework_stage_id: Number(op), payer: payer || "customer" });
        }
        send({ started: op, payer: payer || undefined });
      });
    };
    form.querySelector(".os").onclick = () => send({});
  });

  box.querySelectorAll("[data-rework]").forEach(b => b.onclick = () => {
    const u = s.units.find(x => x.unit_id === Number(b.dataset.rework));
    const form = box.querySelector("#of-rework");
    form.hidden = false;
    let stageId = null, payer = null, cause = null;
    form.innerHTML = `<h3>${t("oSendBack")}: ${esc(u.label)}</h3>
      <p>${t("oToStage")}:</p><div class="chips">${u.steps.filter(x => x.done).map(x =>
        `<button type="button" class="chip" data-st="${x.stage_id}">${esc(stageName(x))} · ${esc(x.by)}</button>`).join("")}</div>
      <p>${t("oCause")}:</p><div class="chips">${L.causes.map(c =>
        `<button type="button" class="chip" data-cause="${c}">${t("c_" + c)}</button>`).join("")}</div>
      <p>${t("oPayer")}:</p><div class="chips">${L.payers.map(p =>
        `<button type="button" class="chip" data-payer="${p}">${t("payer_" + p)}</button>`).join("")}</div>
      <input class="rw" placeholder="${t("oWhatWrong")}">
      <button type="button" class="mid" id="rwGo">${t("oSendBack")}</button>`;
    const pick = (attr, set) => form.querySelectorAll(`[data-${attr}]`).forEach(x => x.onclick = () => {
      set(x.dataset[attr]);
      form.querySelectorAll(`[data-${attr}]`).forEach(y => y.classList.toggle("on", y === x));
    });
    pick("st", v => { stageId = Number(v); });
    pick("cause", v => { cause = v; });
    pick("payer", v => { payer = v; });
    form.scrollIntoView({ block: "nearest" });
    form.querySelector("#rwGo").onclick = async () => {
      if (!stageId || !payer) return flash(false, t("oDecide") + ": " + t("oToStage") + " · " + t("oPayer"));
      const r = await api(`/api/orders/${o.id}/rework`, {
        unit_ids: [u.unit_id], stage_id: stageId, payer, cause: cause || "other",
        reason: form.querySelector(".rw").value, idem_key: idem() });
      if (!r.silent) flash(r.ok, r.message); if (r.ok) again();
    };
  });

  let unfinished = "stop";
  box.querySelectorAll("[data-unf]").forEach(x => x.onclick = () => {
    unfinished = x.dataset.unf;
    box.querySelectorAll("[data-unf]").forEach(y => y.classList.toggle("on", y === x));
  });
  const cg = box.querySelector("#cancelGo");
  if (cg) cg.onclick = async () => {
    const r = await api(`/api/orders/${o.id}/cancel`, {
      reason: box.querySelector("#cancelWhy").value, unfinished });
    if (!r.silent) flash(r.ok, r.message); if (r.ok) again();
  };
  box.querySelector("#cAdd").onclick = async () => {
    const r = await api(`/api/orders/${o.id}/comment`, { text: box.querySelector("#cText").value });
    if (!r.silent) flash(r.ok, r.message); if (r.ok) again();
  };
};

// --- orders page: photo, sheet, catalog ----------------------------------

window.FH_ordersExtras = function () {
  const host = $app.querySelector("section.new");
  if (!host) return;
  const bar = document.createElement("div");
  bar.className = "oextras";
  bar.innerHTML = `
    <div class="row">
      <label class="mid alt file">${t("oPhoto")}<input type="file" id="oPhoto" accept="image/jpeg,image/png,image/webp" capture="environment" hidden></label>
      <label class="mid alt file">${t("oSheet")}<input type="file" id="oSheet" accept=".xlsx,.csv,.tsv,.txt" hidden></label>
    </div>
    <p class="muted"><a href="/api/orders/sheet/demo.xlsx">${t("oDemoSheet")}</a></p>
    <div id="sheetOut"></div>
    <h2>${t("oCatalog")}</h2>
    <div class="row"><input id="catText" class="grow" placeholder="${t("oCatalogHint")}">
      <button type="button" class="chip" id="catGo">${t("oCatalogGo")}</button></div>
    <div id="catOut"></div>`;
  host.appendChild(bar);
  if (state.sheet) renderSheet(state.sheet);

  bar.querySelector("#oPhoto").onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    const r = await api("/api/orders/intake/photo", { data_b64: await fileB64(file), mime: file.type });
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    flash(r.items.length > 0, r.message);
    state.proposal = r; state.picks = {};
    renderProposal(r);
  };
  bar.querySelector("#oSheet").onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    const r = await api("/api/orders/sheet", { data_b64: await fileB64(file), filename: file.name });
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    state.sheet = r; state.sheetDec = {};
    renderSheet(r);
  };
  const catGo = async () => {
    const r = await api("/api/orders/catalog", { text: bar.querySelector("#catText").value });
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    renderCatalogCard(r);
  };
  bar.querySelector("#catGo").onclick = catGo;
  bar.querySelector("#catText").onkeydown = e => { if (e.key === "Enter") catGo(); };
};

const KIND_ORDER = ["suspect", "conflict", "cancel", "changed", "link", "new", "missing", "check", "blocked", "skip", "same"];

function renderSheet(p) {
  const out = document.getElementById("sheetOut");
  if (!out) return;
  const dec = state.sheetDec || (state.sheetDec = {});
  const choice = r => dec[r.key] !== undefined ? dec[r.key] : r.default;
  const acting = p.rows.filter(r => { const c = choice(r); return c && !["skip", "keep"].includes(c); });
  const danger = p.rows.filter(r => (r.danger || r.kind === "cancel") && !choice(r));
  const row = r => `<li class="srow k-${r.kind}">
    <div><b>${r.number ? "#" + r.number : esc(r.ref || "")}</b> <small class="muted">${esc(r.where || "")}${r.ref && r.number ? " · № " + esc(r.ref) : ""}</small></div>
    ${r.words ? `<div>${esc(r.words)}</div>` : ""}
    ${(r.changes || []).length ? `<ul class="plain">${r.changes.map(c => `<li class="lvl-${c.level}">${esc(c.field)}: ${esc(c.old ?? "—")} → <b>${esc(c.new)}</b>${c.words ? ` · <small>${esc(c.words)}</small>` : ""}</li>`).join("")}</ul>` : ""}
    ${(r.info || []).map(i => `<div class="muted">${esc(i)}</div>`).join("")}
    ${!p.done && (r.choices || []).length > 1 ? `<div class="chips">${r.choices.map(c =>
      `<button type="button" class="chip ${choice(r) === c ? "on" : ""}" data-key="${esc(r.key)}" data-ch="${c}">${t("ch_" + c)}</button>`).join("")}</div>` : ""}
    ${r.outcome ? `<div class="muted">${esc(r.outcome)}</div>` : ""}</li>`;
  const groups = KIND_ORDER.map(k => [k, p.rows.filter(r => r.kind === k)]).filter(([, rs]) => rs.length);
  out.innerHTML = `<div class="panel sheet">
    <h3>${esc(p.book)} · ${esc(p.message || p.summary.words)}</h3>
    ${groups.map(([k, rs]) => k === "same"
      ? `<details><summary>${t("k_same")} · ${rs.length}</summary><ul class="plain">${rs.map(row).join("")}</ul></details>`
      : `<h2>${t("k_" + k)} · ${rs.length}</h2><ul class="plain">${rs.map(row).join("")}</ul>`).join("")}
    ${p.skipped.length ? `<details><summary>${t("sheetSkipped")} · ${p.skipped.length}</summary><ul class="plain">${p.skipped.map(s =>
      `<li class="muted">${esc(s.where)} · ${esc(s.why)}</li>`).join("")}</ul></details>` : ""}
    ${p.done ? "" : `${danger.length ? `<button type="button" class="mid alt" id="sheetDanger">${t("oAcceptDanger")} · ${danger.length}</button>` : ""}
    <button type="button" class="big" id="sheetApply" ${acting.length ? "" : "disabled"}>${t("oSheetApply").replace("{n}", acting.length)}</button>
    <button type="button" class="link" id="sheetDrop">${t("dropProposal")}</button>`}</div>`;
  out.querySelectorAll("[data-ch]").forEach(b => b.onclick = () => { dec[b.dataset.key] = b.dataset.ch; renderSheet(p); });
  const dd = document.getElementById("sheetDanger");
  if (dd) dd.onclick = () => { danger.forEach(r => { dec[r.key] = r.choices[0]; }); renderSheet(p); };
  const drop = document.getElementById("sheetDrop");
  if (drop) drop.onclick = () => { state.sheet = null; out.innerHTML = ""; };
  const go = document.getElementById("sheetApply");
  if (go) go.onclick = async () => {
    go.disabled = true;
    const r = await api(`/api/orders/sheet/${p.id}/apply`, { decisions: dec });
    if (r.silent) return;
    flash(r.ok && !r.errors?.length, r.message);
    if (!r.ok) { go.disabled = false; return; }
    p.done = true;
    state.sheet = null;
    renderSheet({ ...p, message: r.message });
  };
}

function renderCatalogCard(p) {
  const out = document.getElementById("catOut");

  const askPrice = p.questions.includes("price");
  const askRates = p.rates ? p.rates.filter(r => r.amount === null) : [];
  out.innerHTML = `<article class="card catcard">
    <p class="big-facts">${esc(p.card)}</p>
    <p class="muted">“${esc(p.source)}” · ${t("readBy")} ${esc(p.model)}</p>
    ${askPrice ? `<label>${t("oPrice")} <input type="number" min="0" id="catPrice" inputmode="numeric"></label>` : ""}
    ${askRates.map(r => `<label>${t("oRate")} · ${esc(stageName(r))} <input type="number" min="0" data-rate="${r.stage_id}" inputmode="numeric"></label>`).join("")}
    <div class="row"><button type="button" class="mid" id="catYes">${t("oYes")}</button>
    <button type="button" class="mid alt" id="catNo">${t("oNo")}</button></div></article>`;
  document.getElementById("catNo").onclick = () => { out.innerHTML = ""; };
  document.getElementById("catYes").onclick = async () => {
    const rates = {};
    out.querySelectorAll("[data-rate]").forEach(i => { if (i.value !== "") rates[i.dataset.rate] = Number(i.value); });
    const pr = document.getElementById("catPrice");
    const r = await api(`/api/orders/catalog/${p.id}/apply`, {
      price: pr && pr.value !== "" ? Number(pr.value) : null, rates });
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) { out.innerHTML = ""; lists = null; }
  };
}

// --- intake card: the person fixes any field --------------------------------
// Replaces app.js renderProposal: every field is editable, not only "check"
// ones; option chips and typed values both go through /fix, which checks them
// against the catalog. Lines can be added (a photo read by hand) or removed.

renderProposal = function (p) {  // eslint-disable-line no-global-assign
  const out = document.getElementById("intakeOut");
  if (!out) return;
  const quotes = p.items.flatMap(i => FIELD_ORDER.map(k => i.fields[k].quote));
  const open = p.items.map((item, idx) => ({ item, idx })).filter(x => !x.item.order_id && !x.item.removed);
  const count = open.filter(x => x.item.ready).length;
  const field = (item, idx, k) => {
    const f = item.fields[k];
    const shown = f.status === "ok" ? `<b>${esc(f.value)}</b>` : `<span class="muted">${esc(f.value || "—")}</span>`;
    const opts = f.status === "check" && f.options.length ? `<span class="opts">${f.options.map(o =>
      `<button type="button" class="chip" data-idx="${idx}" data-field="${k}" data-val="${esc(o.id)}">${esc(o.label)}</button>`).join("")}</span>` : "";
    const list = k === "product" ? "dl-products" : k === "customer" ? "dl-customers" : k === "variant" ? "dl-variants" : "";
    return `<dt class="st-${f.status}">${t("f_" + k)}</dt><dd>${shown}${opts}
      <input class="fixin" data-idx="${idx}" data-field="${k}" ${list ? `list="${list}"` : ""}
        ${k === "due_date" ? 'type="date"' : k === "qty" ? 'type="number" min="1" max="500"' : ""}
        placeholder="${t("oFixHint")}" aria-label="${t("f_" + k)}">
      ${f.quote ? `<small class="quote">“${esc(f.quote)}”</small>` : ""}
      ${f.status === "check" && f.note ? `<small class="late">${esc(f.note)}</small>` : ""}
      <small class="fixmsg late" id="fm-${idx}-${k}"></small></dd>`;
  };
  const card = ({ item, idx }) => `<article class="card intake ${item.ready ? "" : "needs"}">
    <div class="line2">${t("orderN")} ${idx + 1} · <i>${esc(item.line || "")}</i>
      <button type="button" class="link" data-remove="${idx}">${t("oRemoveLine")}</button></div>
    <dl>${FIELD_ORDER.map(k => field(item, idx, k)).join("")}</dl></article>`;
  const photo = p.photo_id ? `<img class="iphoto" src="/api/orders/intake/photo/${p.photo_id}" alt="${t("photoBy")}">` : "";
  out.innerHTML = `
    <div class="split intake-split">
      <aside><h3>${t("source")}</h3>${photo}
        ${p.source ? `<pre class="source">${markQuotes(p.source, quotes)}</pre>` : ""}
        ${p.note ? `<p class="late">${esc(p.note)}</p>` : ""}
        <p class="muted">${t("readBy")} ${esc(p.model)}</p></aside>
      <div>${open.map(card).join("") || `<p class="empty">${t("nothingToCreate")}</p>`}
        <button type="button" class="mid alt" id="intakeAdd">${t("oAddLine")}</button>
        <button type="button" class="big" id="intakeApply" ${count ? "" : "disabled"}>${t("createN").replace("{n}", count)}</button>
        <button type="button" class="link" id="intakeDrop">${t("dropProposal")}</button>
      </div>
    </div>`;
  catalogLists().then(L => {
    if (document.getElementById("dl-products")) return;
    out.insertAdjacentHTML("beforeend", datalist("dl-products", L.products.map(x => x.label))
      + datalist("dl-customers", L.customers.map(x => x.label))
      + datalist("dl-variants", L.fabrics.flatMap(f => [f, ...L.frames.map(fr => `${f} · ${fr}`)])));
  });
  const fix = async (idx, k, value) => {
    const r = await api(`/api/orders/intake/${p.id}/fix`, { idx: Number(idx), field: k, value });
    if (r.silent) return;
    if (!r.ok) {
      const msg = document.getElementById(`fm-${idx}-${k}`);
      if (msg) msg.innerHTML = esc(r.message) + (r.options || []).map(o =>
        ` <button type="button" class="chip" data-sugg="${esc(o.id)}">${esc(o.label)}</button>`).join("");
      if (msg) msg.querySelectorAll("[data-sugg]").forEach(b => b.onclick = () => fix(idx, k, b.dataset.sugg));
      return;
    }
    p.items[idx] = r.item;
    renderProposal(p);
  };
  out.querySelectorAll("[data-val]").forEach(b => b.onclick = () => fix(b.dataset.idx, b.dataset.field, b.dataset.val));
  out.querySelectorAll(".fixin").forEach(inp => {
    const go = () => { if (inp.value !== "") fix(inp.dataset.idx, inp.dataset.field, inp.value); };
    inp.onkeydown = e => { if (e.key === "Enter") go(); };
    if (inp.type === "date") inp.onchange = go;
  });
  out.querySelectorAll("[data-remove]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/orders/intake/${p.id}/line/${b.dataset.remove}/remove`, {});
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    p.items[Number(b.dataset.remove)].removed = true;
    renderProposal(p);
  });
  document.getElementById("intakeAdd").onclick = async () => {
    const r = await api(`/api/orders/intake/${p.id}/line`, {});
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    p.items.push(r.item);
    renderProposal(p);
  };
  document.getElementById("intakeDrop").onclick = () => { state.proposal = null; out.innerHTML = ""; };
  document.getElementById("intakeApply").onclick = async () => {
    const r = await api(`/api/intake/${p.id}/apply`, { picks: {} });
    if (!r.silent) flash(r.ok, r.message);
    if (!r.ok) return;
    if (r.waiting) {
      p.items.forEach(item => { if (item.ready && !item.removed) item.order_id = true; });
      renderOrders();
    } else {
      state.proposal = null; state.intakeText = ""; renderOrders();
    }
  };
};
