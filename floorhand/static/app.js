"use strict";

const state = { me: null, tab: "work", lang: pref("lang", "en"), size: pref("size", "a") };
const $app = document.getElementById("app");
const $tabs = document.getElementById("tabs");

function pref(key, fallback) {
  try { return localStorage.getItem("fh_" + key) || fallback; } catch { return fallback; }
}
function setPref(key, value) {
  try { localStorage.setItem("fh_" + key, value); } catch { /* private mode */ }
}
function t(key) { return (STRINGS[state.lang] || STRINGS.en)[key] || STRINGS.en[key] || key; }
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function stageName(o) { return state.lang === "ru" ? o.name_ru : o.name_en; }

async function api(path, body) {
  let res;
  try {
    res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return { ok: false, message: t("cantReach") };
  }
  if (res.status === 401) { state.me = null; render(); return { ok: false, silent: true }; }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, message: data.message || data.detail || res.statusText };
  return Array.isArray(data) ? { ok: true, list: data } : { ok: true, ...data };
}

// Every action gets an answer: green with words and a sound, or red with why.
function flash(ok, text) {
  const box = document.getElementById("flash");
  box.className = ok ? "good" : "bad";
  document.getElementById("flash-text").textContent = text;
  box.hidden = false;
  beep(ok);
  clearTimeout(flash.timer);
  if (ok) flash.timer = setTimeout(() => { box.hidden = true; }, 1800);
}
document.getElementById("flash-close").onclick = () => {
  document.getElementById("flash").hidden = true;
};

function beep(ok) {
  try {
    const ctx = beep.ctx || (beep.ctx = new AudioContext());
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = ok ? 880 : 220;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (ok ? 0.15 : 0.4));
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + (ok ? 0.15 : 0.4));
  } catch { /* no audio */ }
}

function idem() {
  return (crypto.randomUUID && crypto.randomUUID()) ||
    Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// --- screens -------------------------------------------------------------

async function render() {
  document.documentElement.dataset.size = state.size;
  document.documentElement.lang = state.lang;
  if (!state.me) {
    const me = await api("/api/me");
    if (!me.ok) return renderLogin();
    state.me = me;
  }
  const worker = state.me.stage_id !== null;
  const tabs = worker
    ? (state.me.tablet ? ["work"] : ["work", "money", "me"])
    : state.me.role === "owner" ? ["today", "floor", "orders", "shipping", "stock", "me"]
      : ["orders", "shipping", "stock", "me"];
  if (!tabs.includes(state.tab)) state.tab = tabs[0];
  document.body.classList.toggle("office", !worker);
  renderTabs(tabs);
  ({ work: renderWork, money: renderMoney, me: renderMe, floor: renderFloor,
     orders: renderOrders, shipping: renderShipping, today: renderToday,
     stock: renderStock })[state.tab]();
}

// Server texts come in both languages: {en, ru}.
function tl(obj) { return obj && typeof obj === "object" ? (obj[state.lang] || obj.en || "") : (obj ?? ""); }
function fmt(key, vars) {
  return Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(v), t(key));
}

function renderTabs(tabs) {
  $tabs.hidden = tabs.length < 2;
  $tabs.innerHTML = tabs.map(k =>
    `<button type="button" data-tab="${k}" class="${k === state.tab ? "on" : ""}">${t(k)}</button>`
  ).join("");
  $tabs.querySelectorAll("button").forEach(b => b.onclick = () => {
    state.tab = b.dataset.tab; render();
  });
}

async function renderLogin() {
  $tabs.hidden = true;
  const r = await api("/api/demo/people");
  const people = r.list || [];
  const floor = people.filter(p => p.stage);
  const office = people.filter(p => !p.stage);
  const tile = p => `<button type="button" class="tile" data-id="${p.id}">
      <b>${esc(p.name)}</b><span>${esc(p.stage ? (state.lang === "ru" ? p.stage_ru : p.stage) : p.role)}</span>
    </button>`;
  $app.innerHTML = `
    <header class="bar"><h1>Floorhand</h1>${langSwitch()}</header>
    <h2>${t("who")}</h2>
    <label class="check"><input type="checkbox" id="tablet"> ${t("tablet")}</label>
    <h3>${t("officeTeam")}</h3><div class="tiles">${office.map(tile).join("")}</div>
    <h3>${t("floor")}</h3><div class="tiles">${floor.map(tile).join("")}</div>`;
  bindLang();
  $app.querySelectorAll(".tile").forEach(b => b.onclick = async () => {
    const res = await api("/api/login", {
      person_id: Number(b.dataset.id), tablet: document.getElementById("tablet").checked,
    });
    if (!res.ok) return flash(false, res.message);
    state.me = null; state.tab = "work"; render();
  });
}

function langSwitch() {
  return `<button type="button" class="lang" id="lang">${state.lang === "en" ? "RU" : "EN"}</button>`;
}
function bindLang() {
  document.getElementById("lang").onclick = () => {
    state.lang = state.lang === "en" ? "ru" : "en"; setPref("lang", state.lang); render();
  };
}

function money(cur, n) { return n === null || n === undefined ? "" : `${cur}${n}`; }

function orderCard(o, w, kind) {
  const rate = !o.rate_set ? t("pricePending") : (o.rate !== null ? money(w.currency, o.rate) : "");
  const due = o.customer ? `${esc(o.customer)} · ${t("due")} ${esc(o.due_date || "—")}` : t("toStock");
  const button = kind === "mine"
    ? `<button type="button" class="big" data-done="${o.order_id}">${t("done1")}</button>`
    : `<button type="button" class="mid" data-take="${o.order_id}">${t("take")}</button>`;
  return `<article class="card">
    <div class="line1"><b>#${o.number}</b> ${esc(o.product)}${o.qty > 1 ? ` · ${o.qty} ${t("pcs")}` : ""}</div>
    <div class="line2">${esc(o.variant)}</div>
    <div class="line2">${due}</div>
    <div class="line3">${t("ready")}: ${o.done_here} ${t("of")} ${o.qty}${rate ? " · " + rate : ""}</div>
    ${button}
  </article>`;
}

async function renderWork() {
  const w = await api("/api/work");
  if (!w.ok) { if (!w.silent) flash(false, w.message); return; }
  const today = w.show_money
    ? `${w.today.count} ${t("pcs")} · ${money(w.currency, w.today.money)}`
    : `${w.today.count} ${t("pcs")}`;
  $app.innerHTML = `
    ${state.me.tablet ? `<div class="tabletbar">${t("markedBy")} <b>${esc(w.person.name)}</b>
      <button type="button" id="out">${t("signOut")}</button></div>` : ""}
    <header class="bar"><h1>${esc(stageName(w.stage))}</h1><span class="date">${esc(w.date)}</span></header>
    <p class="today">${t("today")}: <b>${today}</b>${w.today.price_pending ? ` · ${w.today.price_pending} ${t("pricePending")}` : ""}</p>
    <h2>${t("assigned")} · ${w.mine.length}</h2>
    ${w.mine.length ? w.mine.map(o => orderCard(o, w, "mine")).join("") : `<p class="empty">${t("nothingAssigned")}</p>`}
    <details ${w.mine.length ? "" : "open"}><summary>${t("free")} ${esc(stageName(w.stage))} · ${w.free.length}</summary>
      ${w.free.length ? w.free.map(o => orderCard(o, w, "free")).join("") : `<p class="empty">${t("nothingFree")}</p>`}
    </details>
    <details><summary>${t("doneToday")} · ${w.done_today.length}</summary>
      <ul class="done">${w.done_today.map(d => `<li><span>${esc(d.at)} ${esc(d.unit)}${d.amount != null ? " · " + money(w.currency, d.amount) : ""}</span>
        <button type="button" class="link" data-undo="${d.event_id}">${t("undo")}</button></li>`).join("")}</ul>
    </details>
    <div class="dock">
      <button type="button" class="mid" data-soon>${t("scan")}</button>
      <button type="button" class="mid" data-soon>${t("speak")}</button>
    </div>`;
  $app.querySelectorAll("[data-done]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    const r = await api("/api/work/done", { order_id: Number(b.dataset.done), idem_key: idem() });
    if (!r.silent) flash(r.ok, r.message);
    renderWork();
  });
  $app.querySelectorAll("[data-take]").forEach(b => b.onclick = async () => {
    const r = await api("/api/work/take", { order_id: Number(b.dataset.take), n: 1 });
    if (!r.ok) flash(false, r.message);
    renderWork();
  });
  $app.querySelectorAll("[data-undo]").forEach(b => b.onclick = async () => {
    const r = await api("/api/work/undo", { event_id: Number(b.dataset.undo) });
    if (!r.silent) flash(r.ok, r.message);
    renderWork();
  });
  $app.querySelectorAll("[data-soon]").forEach(b => b.onclick = () => flash(false, t("soon")));
  const out = document.getElementById("out");
  if (out) out.onclick = signOut;
}

async function renderMoney() {
  const m = await api("/api/money");
  if (!m.ok) { if (!m.silent) flash(false, m.message); return; }
  const max = Math.max(1, ...m.days.map(d => d.money));
  $app.innerHTML = `
    <header class="bar"><h1>${t("money")}</h1></header>
    <h2>${t("week")}</h2>
    <ul class="bars">${m.days.map(d => `<li>
      <span class="d">${esc(d.date.slice(5))}</span>
      <span class="b"><i style="width:${Math.round(100 * d.money / max)}%"></i></span>
      <span class="v">${money(m.currency, d.money)}</span></li>`).join("")}</ul>
    <p class="sum">${t("toBePaid")}: <b>${money(m.currency, m.to_be_paid)}</b></p>
    <p class="sum">${t("paid")}: <b>${money(m.currency, m.paid)}</b></p>
    ${m.price_pending ? `<p class="empty">${m.price_pending} ${t("pcs")} · ${t("pricePending")}</p>` : ""}`;
}

function renderMe() {
  const sizes = [["a", "A"], ["b", "A+"], ["c", "A++"]];
  $app.innerHTML = `
    <header class="bar"><h1>${esc(state.me.name)}</h1>${langSwitch()}</header>
    <h2>${t("textSize")}</h2>
    <div class="row">${sizes.map(([k, l]) =>
      `<button type="button" class="mid ${state.size === k ? "on" : ""}" data-size="${k}">${l}</button>`).join("")}</div>
    <button type="button" class="mid wide" id="out">${t("signOut")}</button>`;
  bindLang();
  $app.querySelectorAll("[data-size]").forEach(b => b.onclick = () => {
    state.size = b.dataset.size; setPref("size", state.size); render();
  });
  document.getElementById("out").onclick = signOut;
}

async function renderFloor() {
  const r = await api("/api/floor");
  if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
  $app.innerHTML = `
    <header class="bar"><h1>${t("floor")}</h1>${langSwitch()}</header>
    <div class="stages">${r.list.map(s => `<section class="stage ${s.bottleneck ? "hot" : ""}">
      <h3>${esc(stageName(s))}${s.bottleneck ? ` · ${t("bottleneck")}` : ""}</h3>
      <p>${t("queue")} <b>${s.queue}</b></p>
      <p>${t("todayOf")} <b>${s.done_today}</b> / ${s.capacity}</p>
      <ul>${s.people.map(p => `<li>${esc(p.name)} <b>${p.today}</b></li>`).join("")}</ul>
    </section>`).join("")}</div>`;
  bindLang();
}

// --- office: orders ------------------------------------------------------

const VIEWS = ["open", "in_work", "ready", "shipped", "all"];

function stageLine(line) {
  return `<span class="line">${line.map(s =>
    `<span class="step ${s.state}" title="${esc(stageName(s))}: ${s.done}">${esc(stageName(s))}${
      s.state === "active" && s.here ? ` ${s.here}` : ""}</span>`).join("")}</span>`;
}

function dueCell(o) {
  if (!o.customer) return t("toStock");
  return o.late_days ? `<b class="late">${esc(o.due_date)} · ${t("lateBy")} ${o.late_days} ${t("days")}</b>`
    : esc(o.due_date || "—");
}

async function renderOrders() {
  if (state.order) return renderOrder(state.order);
  const view = state.view || "open";
  const r = await api(`/api/orders?view=${view}&q=${encodeURIComponent(state.q || "")}`);
  if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
  $app.innerHTML = `
    <header class="bar"><h1>${t("orders")}</h1>${langSwitch()}</header>
    <section class="new">
      <h2>${t("newOrder")}</h2>
      <textarea id="intakeText" class="drop" rows="4" placeholder="${t("newOrderHint")}">${esc(state.intakeText || "")}</textarea>
      <div class="row">
        <button type="button" class="mid" id="intakeGo">${t("readIt")}</button>
        <label class="mid alt file">${t("pickTable")}<input type="file" id="intakeFile" accept=".csv,.tsv,.txt" hidden></label>
      </div>
      <div id="intakeOut"></div>
    </section>
    ${r.attention.length ? `<h2 class="hot">${t("attention")} · ${r.attention.length}</h2>
      <ul class="attention">${r.attention.slice(0, 8).map(o => `<li><button type="button" class="row" data-order="${o.id}">
        <b>#${o.number}</b> ${esc(o.product)} · ${esc(o.customer || t("toStock"))} ·
        <b class="late">${t("lateBy")} ${o.late_days} ${t("days")}</b> · ${t("nowAt")} ${esc(nowAt(o.line))}</button></li>`).join("")}
      ${r.attention.length > 8 ? `<li class="muted">+ ${r.attention.length - 8}</li>` : ""}</ul>` : ""}
    <div class="filters">
      <input id="q" type="search" placeholder="${t("search")}" value="${esc(state.q || "")}">
      <div class="chips">${VIEWS.map(v =>
        `<button type="button" class="chip ${v === view ? "on" : ""}" data-view="${v}">${t("v_" + v)}</button>`).join("")}</div>
    </div>
    <table class="orders"><thead><tr><th>№</th><th>${t("product")}</th><th>${t("customer")}</th>
      <th>${t("whereNow")}</th><th>${t("dueCol")}</th></tr></thead>
    <tbody>${r.orders.map(o => `<tr data-order="${o.id}">
      <td><b>${o.number}</b></td>
      <td>${esc(o.product)}${o.qty > 1 ? ` ×${o.qty}` : ""}<br><small>${esc(o.variant)}</small></td>
      <td>${esc(o.customer || t("toStock"))}</td>
      <td>${o.status === "shipped" ? t("s_shipped") : o.status === "ready" ? `<b>${t("s_ready")}</b>` : stageLine(o.line)}</td>
      <td>${dueCell(o)}</td></tr>`).join("")}</tbody></table>
    ${r.orders.length ? "" : `<p class="empty">${t("nothingFound")}</p>`}`;
  bindLang();
  bindIntake();
  if (state.proposal) renderProposal(state.proposal);
  $app.querySelectorAll("[data-order]").forEach(el => el.onclick = () => {
    state.order = Number(el.dataset.order); renderOrders();
  });
  $app.querySelectorAll("[data-view]").forEach(b => b.onclick = () => {
    state.view = b.dataset.view; renderOrders();
  });
  const q = document.getElementById("q");
  q.oninput = () => {
    clearTimeout(renderOrders.timer);
    renderOrders.timer = setTimeout(async () => {
      state.q = q.value; await renderOrders();
      const again = document.getElementById("q");
      again.focus(); again.setSelectionRange(again.value.length, again.value.length);
    }, 250);
  };
}

// --- office: order intake ------------------------------------------------

function bindIntake() {
  const box = document.getElementById("intakeText");
  box.oninput = () => { state.intakeText = box.value; };
  const go = async text => {
    const btn = document.getElementById("intakeGo");
    btn.disabled = true; btn.textContent = t("reading");
    const r = await api("/api/intake", { text });
    btn.disabled = false; btn.textContent = t("readIt");
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    state.proposal = r; state.picks = {};
    renderProposal(r);
  };
  document.getElementById("intakeGo").onclick = () => go(box.value);
  document.getElementById("intakeFile").onchange = e => {
    const file = e.target.files[0];
    if (!file) return;
    file.text().then(text => { box.value = text; state.intakeText = text; go(text); });
  };
  // Drop a table file straight onto the box.
  box.ondragover = e => e.preventDefault();
  box.ondrop = e => {
    const file = e.dataTransfer.files[0];
    if (!file) return;
    e.preventDefault();
    file.text().then(text => { box.value = text; state.intakeText = text; go(text); });
  };
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

const FIELD_ORDER = ["product", "qty", "customer", "due_date", "variant"];

function itemReady(item, picks) {
  return FIELD_ORDER.every(k => {
    const f = item.fields[k];
    const required = ["product", "qty", "customer"].includes(k);
    if (f.status === "ok") return true;
    if (f.status === "check") return picks && picks[k] !== undefined && picks[k] !== "";
    return !required;  // empty optional field is fine
  });
}

function renderProposal(p) {
  const out = document.getElementById("intakeOut");
  if (!out) return;
  const quotes = p.items.flatMap(i => FIELD_ORDER.map(k => i.fields[k].quote));
  const open = p.items.map((item, idx) => ({ item, idx })).filter(x => !x.item.order_id);
  const count = open.filter(x => itemReady(x.item, state.picks[x.idx])).length;
  const card = ({ item, idx }) => `<article class="card intake ${itemReady(item, state.picks[idx]) ? "" : "needs"}">
    <div class="line2">${t("orderN")} ${idx + 1} · <i>${esc(item.line || "")}</i></div>
    <dl>${FIELD_ORDER.map(k => {
      const f = item.fields[k];
      const pick = (state.picks[idx] || {})[k];
      let val;
      if (f.status === "check" && k === "due_date") {
        val = `<input type="date" data-idx="${idx}" data-field="${k}" value="${esc(pick || "")}">`;
      } else if (f.status === "check") {
        val = `<span class="opts">${f.options.map(o => `<button type="button" class="chip ${String(pick) === String(o.id) ? "on" : ""}"
          data-idx="${idx}" data-field="${k}" data-val="${esc(o.id)}">${esc(o.label)}</button>`).join("")}</span>`;
      } else {
        val = f.status === "ok" ? `<b>${esc(f.value)}</b>` : `<span class="muted">—</span>`;
      }
      return `<dt class="st-${f.status}">${t("f_" + k)}</dt><dd>${val}
        ${f.quote ? `<small class="quote">“${esc(f.quote)}”</small>` : ""}
        ${f.status === "check" && f.note ? `<small class="late">${esc(f.note)}</small>` : ""}</dd>`;
    }).join("")}</dl></article>`;
  out.innerHTML = `
    <div class="split intake-split">
      <aside><h3>${t("source")}</h3><pre class="source">${markQuotes(p.source, quotes)}</pre>
        <p class="muted">${t("readBy")} ${esc(p.model)}</p></aside>
      <div>${open.map(card).join("") || `<p class="empty">${t("nothingToCreate")}</p>`}
        <button type="button" class="big" id="intakeApply" ${count ? "" : "disabled"}>${t("createN").replace("{n}", count)}</button>
        <button type="button" class="link" id="intakeDrop">${t("dropProposal")}</button>
      </div>
    </div>`;
  out.querySelectorAll("[data-val]").forEach(b => b.onclick = () => {
    const idx = b.dataset.idx;
    state.picks[idx] = { ...(state.picks[idx] || {}), [b.dataset.field]: b.dataset.val };
    renderProposal(p);
  });
  out.querySelectorAll("input[data-field]").forEach(inp => inp.onchange = () => {
    const idx = inp.dataset.idx;
    state.picks[idx] = { ...(state.picks[idx] || {}), [inp.dataset.field]: inp.value };
    renderProposal(p);
  });
  document.getElementById("intakeDrop").onclick = () => { state.proposal = null; out.innerHTML = ""; };
  document.getElementById("intakeApply").onclick = async () => {
    // Option ids are numbers for catalog items, strings for "new:..." / "stock".
    const picks = {};
    for (const [idx, fields] of Object.entries(state.picks)) {
      picks[idx] = {};
      for (const [k, v] of Object.entries(fields)) picks[idx][k] = /^\d+$/.test(v) ? Number(v) : v;
    }
    const r = await api(`/api/intake/${p.id}/apply`, { picks });
    if (!r.silent) flash(r.ok, r.message);
    if (!r.ok) return;
    if (r.waiting) {
      p.items.forEach((item, idx) => {  // mark what was created, keep the rest on screen
        if (itemReady(item, state.picks[idx]) && !item.order_id) item.order_id = true;
      });
      renderOrders();
    } else {
      state.proposal = null; state.intakeText = ""; renderOrders();
    }
  };
}

function nowAt(line) {
  const active = line.filter(s => s.state === "active");
  return active.length ? active.map(stageName).join(", ") : t("s_ready");
}

async function renderOrder(id) {
  const o = await api(`/api/orders/${id}`);
  if (!o.ok) { if (!o.silent) flash(false, o.message); state.order = null; return; }
  const main = o.status === "ready" && o.customer_id
    ? `<button type="button" class="big" id="toShip">${t("ship")}</button>` : "";
  $app.innerHTML = `
    <button type="button" class="back" id="back">← ${t("orders")}</button>
    <header class="bar"><h1>#${o.number} ${esc(o.product)}${o.qty > 1 ? ` ×${o.qty}` : ""}</h1>
      <span class="status s-${o.status}">${t("s_" + o.status)}</span></header>
    <p class="big-facts">${esc(o.variant)}<br>
      ${o.customer ? `${esc(o.customer)}, ${esc(o.city || "")} · ${t("due")} ${dueCell(o)}` : t("toStock")}</p>
    <ol class="stages-line">${o.line.map(s => `<li class="${s.state}">
      <b>${esc(stageName(s))}</b> <span>${s.done}/${o.qty}</span>
      <small>${esc(s.people.join(", "))}</small></li>`).join("")}</ol>
    ${main}
    <details><summary>${t("history")} · ${o.history.length}</summary>
      <ul class="history">${o.history.map(h => `<li>${esc(h.created_at.replace("T", " ").slice(0, 16))} ·
        ${esc(state.lang === "ru" ? h.stage_ru : h.stage)}${o.qty > 1 ? ` · ${t("pc")} ${h.seq}` : ""} ·
        ${esc(h.person)}${h.kind === "undo" ? ` · <b>${t("undone")}</b>` : ""}</li>`).join("")}</ul>
    </details>`;
  document.getElementById("back").onclick = () => { state.order = null; renderOrders(); };
  const toShip = document.getElementById("toShip");
  if (toShip) toShip.onclick = () => {
    state.order = null; state.shipTo = o.customer_id; state.tab = "shipping"; render();
  };
}

// --- office: shipping ----------------------------------------------------

async function renderShipping() {
  const b = await api("/api/shipping");
  if (!b.ok) { if (!b.silent) flash(false, b.message); return; }
  const cur = b.currency;
  $app.innerHTML = `
    <header class="bar"><h1>${t("shipping")}</h1>${langSwitch()}</header>
    <div class="split">
      <aside>
        <h2>${t("readyToShip")}</h2>
        <ul class="pick">${b.ready.map(c => `<li><button type="button" class="row ${c.customer_id === state.shipTo ? "on" : ""}"
          data-cust="${c.customer_id}"><b>${esc(c.name)}</b> · ${c.ready} ${t("of")} ${c.open} ${t("readyWord")}</button></li>`).join("")
          || `<li class="muted">${t("nothingReady")}</li>`}</ul>
        <h2>${t("shippedToday")} · ${b.today.length}</h2>
        <ul class="plain">${b.today.map(s => `<li><a href="/invoice/${s.id}" target="_blank">№${s.id}</a>
          ${esc(s.customer)} · ${money(cur, s.total)}</li>`).join("")}</ul>
        <h2>${t("owedToUs")} · ${b.debts.length} · ${money(cur, b.owed_total)}</h2>
        <ul class="plain">${b.debts.slice(0, 6).map(d => `<li>${esc(d.name)} · ${money(cur, d.owed)}</li>`).join("")}</ul>
      </aside>
      <section id="shipPanel" class="panel"><p class="empty">${t("pickCustomer")}</p></section>
    </div>`;
  bindLang();
  $app.querySelectorAll("[data-cust]").forEach(el => el.onclick = () => {
    state.shipTo = Number(el.dataset.cust); renderShipping();
  });
  if (state.shipTo) renderShipPanel(state.shipTo);
}

async function renderShipPanel(customerId) {
  const c = await api(`/api/shipping/${customerId}`);
  const panel = document.getElementById("shipPanel");
  if (!c.ok) { if (!c.silent) flash(false, c.message); return; }
  const cur = c.currency;
  let payment = null;
  panel.innerHTML = `
    <h2>${esc(c.name)}, ${esc(c.city || "")}</h2>
    ${c.owed ? `<p class="late">${t("alreadyOwes")} ${money(cur, c.owed)}</p>` : ""}
    <ul class="items">${c.items.map(i => `<li class="${i.ready ? "" : "notready"}">
      <label><input type="checkbox" data-unit="${i.unit_id}" data-price="${i.price}" ${i.ready ? "checked" : "disabled"}>
      <b>${esc(i.label)}</b> ${esc(i.product)}</label>
      <span>${i.ready ? money(cur, i.price) : `${t("stillAt")} ${esc(state.lang === "ru" ? i.at_ru : i.at)}`}</span></li>`).join("")}</ul>
    <p class="sum">${t("total")}: <b id="total"></b></p>
    <p>${t("payment")}:</p>
    <div class="row">${["paid", "credit", "partial"].map(p =>
      `<button type="button" class="mid alt" data-pay="${p}">${t("p_" + p)}</button>`).join("")}</div>
    <label id="partialBox" hidden>${t("paidNow")} <input id="paidAmount" type="number" min="0" inputmode="numeric"></label>
    <button type="button" class="big" id="doShip" disabled>${t("shipAndPrint")}</button>`;
  const boxes = [...panel.querySelectorAll("[data-unit]")];
  const update = () => {
    const picked = boxes.filter(x => x.checked);
    document.getElementById("total").textContent =
      money(cur, picked.reduce((s, x) => s + Number(x.dataset.price), 0));
    document.getElementById("doShip").disabled = !picked.length || !payment;
  };
  boxes.forEach(x => x.onchange = update);
  panel.querySelectorAll("[data-pay]").forEach(btn => btn.onclick = () => {
    payment = btn.dataset.pay;
    panel.querySelectorAll("[data-pay]").forEach(x => x.classList.toggle("on", x === btn));
    document.getElementById("partialBox").hidden = payment !== "partial";
    update();
  });
  update();
  document.getElementById("doShip").onclick = async () => {
    const win = window.open("", "_blank");  // opened inside the click, so it is not blocked
    const r = await api("/api/ship", {
      customer_id: customerId, payment,
      unit_ids: boxes.filter(x => x.checked).map(x => Number(x.dataset.unit)),
      paid_amount: Number(document.getElementById("paidAmount").value || 0),
    });
    if (r.silent) { if (win) win.close(); return; }
    flash(r.ok, r.message);
    if (!r.ok) { if (win) win.close(); return; }
    if (win) win.location = `/invoice/${r.shipment_id}`;
    renderShipping();
  };
}

// --- owner: Today ----------------------------------------------------------
// A feed of decisions. Every card is a stored proposal; the buttons send only
// its id (plus what was kept or picked from what was offered). Nothing is
// preselected; "Why?" shows the numbers tagged FACT / CALC / ESTIMATE.

function tag(kind) { return `<i class="tag ${kind.toLowerCase()}">${kind}</i>`; }

async function renderToday() {
  const [f, m] = await Promise.all([api("/api/owner/today"), api(`/api/owner/morning?lang=${state.lang}`)]);
  if (!f.ok) { if (!f.silent) flash(false, f.message); return; }
  const s = f.summary;
  const bn = s.bottleneck;
  $app.innerHTML = `
    <header class="bar"><h1>${t("today")}</h1><span class="date">${esc(f.date)}</span>${langSwitch()}</header>
    <ul class="kpis">
      <li><b>${s.open_orders}</b><span>${t("openOrders")}</span></li>
      <li><b>${s.ready}</b><span>${t("readyShip")}</span></li>
      <li class="${s.late ? "hot" : ""}"><b>${s.late}</b><span>${t("lateOrders")}</span></li>
      <li class="${s.shortages ? "hot" : ""}"><b>${s.shortages}</b><span>${t("shortMat")}</span></li>
      <li><b>${s.done_today}<small>/${s.capacity}</small></b><span>${t("doneCap")}</span></li>
      <li class="${bn ? "hot" : ""}"><b>${bn ? esc(stageName(bn)) : "—"}</b>
        <span>${bn ? `${t("bottleneckIs")} · ${bn.queue} / ${bn.capacity}` : t("noBottleneck")}</span></li>
    </ul>
    ${m.ok ? `<details class="morning"><summary>${t("morning")} · ${esc(m.head)}</summary>
      <ul class="plain">${m.lines.map(l => `<li>${esc(l)}</li>`).join("")}</ul></details>` : ""}
    <h2>${t("decisions")} · ${f.cards.length}</h2>
    <div id="cards">${f.cards.length ? f.cards.map(decisionCard).join("") : `<p class="empty">${t("noDecisions")}</p>`}</div>
    ${f.outbox ? `<p class="muted">${fmt("outboxN", { n: f.outbox })}</p>` : ""}
    <h2>${t("ask")}</h2>
    <section class="askbox">
      <div class="row"><input id="askText" type="text" placeholder="${t("askHint")}" value="${esc(state.askText || "")}">
        <button type="button" class="mid" id="askGo">${t("ask")}</button></div>
      <p class="muted">${t("tryAsk")} ${f.examples.map(e => `<button type="button" class="chip" data-ask="${esc(tl(e))}">${esc(tl(e))}</button>`).join(" ")}</p>
      <div id="askOut"></div>
    </section>
    ${f.decided.length ? `<h2>${t("decidedToday")} · ${f.decided.length}</h2>
      <ul class="plain decided">${f.decided.map(c => `<li><b class="st-${c.status}">${t("s_" + c.status)}</b> ${esc(tl(c.headline))}
        ${c.result && c.result.reason ? `<small class="muted">— ${esc(c.result.reason)}</small>` : ""}</li>`).join("")}</ul>` : ""}`;
  bindLang();
  bindCards(f);
  bindAsk();
}

function kindStats(c) {
  const st = c.stats;
  if (c.shadow) {
    return `<span class="shadow">${t("shadow")} · ${st.ready
      ? fmt("shadowReady", { rate: Math.round(st.rate * 100), n: st.scored })
      : fmt("shadowHint", { hits: st.hits, total: st.total, n: st.min_scored, rate: Math.round(st.min_rate * 100) })}</span>
      ${st.ready ? `<button type="button" class="chip" data-enable="${c.kind}">${t("switchOn")}</button>` : ""}`;
  }
  const parts = [`${st.accepted} ${t("accepted")}`, `${st.rejected} ${t("rejectedW")}`];
  if (st.rate !== null) parts.push(`${Math.round(st.rate * 100)}% ${t("followed")}`);
  return `<span class="muted">${parts.join(" · ")}</span>`;
}

function decisionCard(c) {
  return `<article class="card decision k-${c.kind} ${c.shadow ? "shadowed" : ""}" data-card="${c.id}">
    <div class="kind">${t("k_" + c.kind)} · ${t("forDay")} ${esc(c.target_date)} ${kindStats(c)}</div>
    <h3>${esc(tl(c.headline))}</h3>
    <ul class="nums">${c.numbers.map(n => `<li><b>${esc(tl(n.value))}</b><span>${esc(tl(n.label))} ${tag(n.tag)}</span></li>`).join("")}</ul>
    <div class="actions">
      <button type="button" class="mid" data-yes="${c.id}" ${c.shadow ? "disabled" : ""}>${esc(tl(c.apply_label) || t("yes"))}</button>
      <button type="button" class="mid alt" data-change="${c.id}" ${c.shadow ? "disabled" : ""}>${t("change")}</button>
      <button type="button" class="mid alt" data-no="${c.id}">${t("no")}</button>
      <button type="button" class="mid alt" data-why="${c.id}">${t("why")}</button>
    </div>
    <div class="why" hidden></div>
    <div class="change" hidden></div>
    <div class="noform" hidden>
      <input type="text" placeholder="${t("reasonHint")}" data-reason="${c.id}">
      <div class="row"><button type="button" class="mid alt" data-noyes="${c.id}">${t("recordNo")}</button>
        <button type="button" class="mid alt" data-cancel="${c.id}">${t("cancel")}</button></div>
    </div>
  </article>`;
}

function changeForm(c) {
  if (c.kind === "plan") {
    const byStage = {};
    c.lines.forEach(l => (byStage[l.stage_id] = byStage[l.stage_id] || []).push(l));
    const stage = id => c.stages.find(s => s.stage_id === id);
    return Object.entries(byStage).map(([sid, lines]) => `<h4>${esc(stageName(stage(Number(sid))))} · ${lines.length}</h4>
      <ul class="lines">${lines.map(l => `<li class="${l.risk !== "ok" ? "risky" : ""}">
        <label><input type="checkbox" checked data-keep="${l.unit_id}"> <span><b>${esc(l.label)}</b> ${esc(l.product)}
          <small>${esc(l.variant)} · ${l.customer ? esc(l.customer) + " · " + esc(l.due || "") : t("toStock")}${l.late_days ? ` · <span class="late">${t("lateBy")} ${l.late_days} ${t("days")}</span>` : ""}</small></span></label>
        <select data-move="${l.unit_id}">${stage(Number(sid)).workers.map(p =>
          `<option value="${p.id}" ${p.id === l.person_id ? "selected" : ""}>${esc(p.name)} · ${p.on_hands} ${t("onHands")}</option>`).join("")}</select>
      </li>`).join("")}</ul>`).join("");
  }
  if (c.kind === "shortage") {
    return `<ul class="lines">${c.options.map((o, i) => `<li><label><input type="radio" name="pick-${c.id}" value="${o.id}" ${i === 0 ? "checked" : ""}> <span>${esc(tl(o.label))}</span></label></li>`).join("")}</ul>
      <p class="muted">${c.lines.map(l => `#${l.order} ${l.qty}`).join(" · ")}</p>`;
  }
  if (c.kind === "late_risk") {
    return `<ul class="lines">${c.lines.map(o => `<li class="${o.risk === "late" ? "risky" : ""}"><label>
      <input type="checkbox" checked data-keep="${o.number}" ${o.customer ? "" : "disabled"}> <span><b>#${o.number}</b> ${esc(o.product)}
      <small>${esc(o.customer || t("toStock"))} · ${t("due")} ${esc(o.due)} → ${esc(o.estimate)} ${tag("ESTIMATE")}${o.late_days ? ` · <span class="late">${t("lateBy")} ${o.late_days} ${t("days")}</span>` : ""}</small></span></label></li>`).join("")}</ul>`;
  }
  return `<ul class="lines">${c.lines.map(l => `<li><label><input type="checkbox" checked data-keep="${l.product_id}"> <span><b>${esc(l.product)}</b> × ${l.qty}
    <small>${l.sold_30d} / 30 d · ${l.cover_days ?? "—"} d</small></span></label></li>`).join("")}</ul>`;
}

function bindCards(f) {
  const byId = Object.fromEntries(f.cards.map(c => [c.id, c]));
  const box = id => $app.querySelector(`[data-card="${id}"]`);
  const decide = async (id, body) => {
    const r = await api(`/api/owner/cards/${id}/apply`, body);
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) renderToday();
  };
  $app.querySelectorAll("[data-yes]").forEach(b => b.onclick = () => { b.disabled = true; decide(Number(b.dataset.yes), {}); });
  $app.querySelectorAll("[data-change]").forEach(b => b.onclick = () => {
    const c = byId[Number(b.dataset.change)];
    const el = box(c.id).querySelector(".change");
    el.hidden = !el.hidden;
    if (el.hidden) return;
    el.innerHTML = changeForm(c) + `<div class="row"><button type="button" class="mid" data-apply="${c.id}">${t("applyChanges")}</button>
      <button type="button" class="mid alt" data-cancel="${c.id}">${t("cancel")}</button></div>`;
    el.querySelector("[data-apply]").onclick = () => {
      const body = {};
      if (el.querySelector("[data-keep]")) body.keep = [...el.querySelectorAll("[data-keep]:checked")].map(x => Number(x.dataset.keep));
      const moves = {};
      el.querySelectorAll("[data-move]").forEach(sel => { moves[sel.dataset.move] = Number(sel.value); });
      if (Object.keys(moves).length) body.moves = moves;
      const pick = el.querySelector("input[type=radio]:checked");
      if (pick) body.pick = Number(pick.value);
      decide(c.id, body);
    };
    el.querySelector("[data-cancel]").onclick = () => { el.hidden = true; };
  });
  $app.querySelectorAll("[data-no]").forEach(b => b.onclick = () => {
    const el = box(Number(b.dataset.no)).querySelector(".noform");
    el.hidden = !el.hidden;
    if (!el.hidden) el.querySelector("input").focus();
  });
  $app.querySelectorAll("[data-noyes]").forEach(b => b.onclick = async () => {
    const id = Number(b.dataset.noyes);
    const r = await api(`/api/owner/cards/${id}/reject`, { reason: box(id).querySelector("[data-reason]").value });
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) renderToday();
  });
  $app.querySelectorAll(".noform [data-cancel]").forEach(b => b.onclick = () => {
    box(Number(b.dataset.cancel)).querySelector(".noform").hidden = true;
  });
  $app.querySelectorAll("[data-why]").forEach(b => b.onclick = async () => {
    const id = Number(b.dataset.why);
    const el = box(id).querySelector(".why");
    if (!el.hidden) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<p class="muted">…</p>`;
    const r = await api(`/api/owner/cards/${id}/why?lang=${state.lang}`);
    if (!r.ok) { el.innerHTML = `<p class="late">${esc(r.message)}</p>`; return; }
    el.innerHTML = `<p>${esc(r.text)}</p><ul class="facts">${r.lines.map(l => `<li>${tag(l.tag)} ${esc(tl(l))}</li>`).join("")}</ul>
      <p class="muted">${t("computedBy")} planner-rules · ${t("explainedBy")} ${esc(r.model)}</p>`;
  });
  $app.querySelectorAll("[data-enable]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/owner/advice/${b.dataset.enable}/enable`, {});
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) renderToday();
  });
}

function bindAsk() {
  const input = document.getElementById("askText");
  const out = document.getElementById("askOut");
  const go = async text => {
    if (!text.trim()) return;
    state.askText = text;
    const btn = document.getElementById("askGo");
    btn.disabled = true; btn.textContent = t("asking");
    const r = await api("/api/owner/ask", { text, lang: state.lang });
    btn.disabled = false; btn.textContent = t("ask");
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    out.innerHTML = `<div class="answer">
      ${r.answer ? `<p>${esc(r.answer)}</p>` : ""}
      ${r.lines.length ? `<ul class="facts">${r.lines.map(l => `<li>${tag(l.tag)} ${esc(tl(l))}</li>`).join("")}</ul>` : ""}
      ${r.examples ? `<p>${r.examples.map(e => `<button type="button" class="chip" data-ask="${esc(tl(e))}">${esc(tl(e))}</button>`).join(" ")}</p>` : ""}
      <p class="muted">${t("answeredBy")} ${esc(r.model)}${r.tools.length ? ` · ${r.tools.join(", ")}` : ""}</p></div>`;
    out.querySelectorAll("[data-ask]").forEach(b => b.onclick = () => { input.value = b.dataset.ask; go(b.dataset.ask); });
  };
  document.getElementById("askGo").onclick = () => go(input.value);
  input.onkeydown = e => { if (e.key === "Enter") go(input.value); };
  $app.querySelectorAll(".askbox [data-ask]").forEach(b => b.onclick = () => { input.value = b.dataset.ask; go(b.dataset.ask); });
}

// --- office: Stock (materials) --------------------------------------------

async function renderStock() {
  const r = await api("/api/materials");
  if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
  const kinds = ["fabric", "foam", "frame", "fittings", "other"];
  const stops = new Set(r.shortages.filter(s => s.stops_next).map(s => s.material_id));
  const row = m => `<tr data-mat="${m.id}" class="${m.short ? "short" : ""}">
    <td><b>${esc(m.name)}</b><br><small>${t("m_" + m.kind)}${m.supplier ? " · " + esc(m.supplier) : ""}</small></td>
    <td class="n">${m.on_hand} ${esc(m.unit)}${m.low ? `<br><small class="late">${t("low")}</small>` : ""}</td>
    <td class="n">${m.need} ${esc(m.unit)}</td>
    <td class="n">${m.short ? `<b class="late">${m.short} ${esc(m.unit)}</b>` : "—"}
      ${stops.has(m.id) ? `<br><small class="late">${t("nextMarks")}</small>` : ""}</td></tr>`;
  $app.innerHTML = `
    <header class="bar"><h1>${t("stock")}</h1>${langSwitch()}</header>
    <div class="split stock">
      <div>
        <table class="orders mats"><thead><tr><th>${t("material")}</th><th class="n">${t("onHand")}</th>
          <th class="n">${t("need")}</th><th class="n">${t("short")}</th></tr></thead>
          <tbody>${kinds.flatMap(k => r.materials.filter(m => m.kind === k)).map(row).join("")}</tbody></table>
      </div>
      <aside>
        <section class="panel">
          <h2>${t("receive")}</h2>
          <select id="recMat">${r.materials.map(m => `<option value="${m.id}">${esc(m.name)} (${esc(m.unit)})</option>`).join("")}</select>
          <input id="recQty" type="number" step="0.1" min="0" inputmode="decimal" placeholder="${t("qty")}">
          <input id="recNote" type="text" placeholder="${t("note")}">
          <button type="button" class="big" id="recGo">${t("recordIt")}</button>
        </section>
        <section class="panel" id="journal" hidden></section>
      </aside>
    </div>`;
  bindLang();
  document.getElementById("recGo").onclick = async () => {
    const res = await api("/api/materials/receive", {
      material_id: Number(document.getElementById("recMat").value),
      qty: Number(document.getElementById("recQty").value), note: document.getElementById("recNote").value,
    });
    if (!res.silent) flash(res.ok, res.message);
    if (res.ok) renderStock();
  };
  $app.querySelectorAll("[data-mat]").forEach(tr => tr.onclick = async () => {
    const j = await api(`/api/materials/${tr.dataset.mat}/journal`);
    if (!j.ok) return;
    const el = document.getElementById("journal");
    el.hidden = false;
    el.innerHTML = `<h2>${t("journal")} · ${esc(tr.querySelector("b").textContent)}</h2>
      <ul class="plain journal">${j.rows.map(x => `<li><span>${esc(x.at.replace("T", " ").slice(0, 16))}</span>
        <span>${t("j_" + x.kind)}${x.unit ? " " + esc(x.unit) : ""}${x.note ? ` · ${esc(x.note)}` : ""}</span>
        <b class="${x.qty < 0 ? "late" : ""}">${x.qty > 0 ? "+" : ""}${Math.round(x.qty * 100) / 100}</b></li>`).join("")}</ul>`;
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
}

async function signOut() {
  await api("/api/logout", {});
  state.me = null; render();
}

render();
