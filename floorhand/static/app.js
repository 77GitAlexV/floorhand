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
    : ["floor", "me"];
  if (!tabs.includes(state.tab)) state.tab = tabs[0];
  renderTabs(tabs);
  ({ work: renderWork, money: renderMoney, me: renderMe, floor: renderFloor })[state.tab]();
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

async function signOut() {
  await api("/api/logout", {});
  state.me = null; render();
}

render();
