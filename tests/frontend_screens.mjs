// Run with node --experimental-vm-modules tests/frontend_screens.mjs.
// DOM and transport doubles exercise the real modules without a browser or packages.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";

class Classes {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach(n => this.values.add(n)); }
  remove(...names) { names.forEach(n => this.values.delete(n)); }
  contains(n) { return this.values.has(n); }
  toggle(n, on = !this.contains(n)) { on ? this.add(n) : this.remove(n); }
}
const decode = s => s.replace(/&(amp|quot|lt|gt|#39);/g, (_, k) => ({ amp: "&", quot: '"', lt: "<", gt: ">", "#39": "'" })[k]);
class Element {
  constructor(tag = "div", attrs = {}) {
    this.tag = tag; this.attrs = attrs; this.children = []; this.classList = new Classes(); this.listeners = new Map();
    this.classList.add(...(attrs.class || "").split(" ")); this.dataset = {};
    for (const [k, v] of Object.entries(attrs)) if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())] = v;
    this.value = attrs.value || ""; this.disabled = "disabled" in attrs; this.hidden = false; this.isConnected = true;
  }
  set innerHTML(html) {
    this.html = html; this.children = []; const stack = [this];
    for (const m of html.matchAll(/<\/?([\w:-]+)([^>]*?)>|([^<]+)/g)) {
      if (m[3]) { stack.at(-1).children.push(decode(m[3])); continue; }
      if (m[0].startsWith("</")) { if (stack.length > 1) stack.pop(); continue; }
      const attrs = Object.fromEntries([...m[2].matchAll(/([\w:-]+)(?:="([^"]*)")?/g)].map(a => [a[1], decode(a[2] || "")]));
      const el = new Element(m[1], attrs); stack.at(-1).children.push(el);
      if (!/^(input|img|br|hr|meta|link)$/.test(m[1]) && !m[0].endsWith("/>")) stack.push(el);
    }
  }
  get innerHTML() { return this.html || ""; }
  set textContent(s) { this.children = [String(s)]; }
  get textContent() { return this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); }
  set className(s) { this.classList = new Classes(); this.classList.add(...s.split(" ")); }
  matches(selector) {
    selector = selector.split(" ").at(-1);
    if (selector.startsWith("#")) return this.attrs.id === selector.slice(1);
    const attr = selector.match(/^(\w+)?\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attr) return (!attr[1] || this.tag === attr[1]) && attr[2] in this.attrs && (attr[3] === undefined || this.attrs[attr[2]] === attr[3]);
    return selector.startsWith(".") ? this.classList.contains(selector.slice(1)) : this.tag === selector;
  }
  querySelectorAll(s) { return this.children.flatMap(c => typeof c === "string" ? [] : [...(c.matches(s) ? [c] : []), ...c.querySelectorAll(s)]); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  addEventListener(type, callback) { this["on" + type] = callback; if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(callback); }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  appendChild(c) { this.children.push(c); }
  get elements() { return Object.fromEntries(this.querySelectorAll("[name]").map(el => [el.attrs.name, el])); }
  reportValidity() { return this.value !== "" && Number(this.value) >= 1 && Number(this.value) <= 90; }
  focus() {}
  remove() {}
  click() { return this.onclick?.({ currentTarget: this, target: this }); }
}

let calls = [], answer = [], transport, bitmap, revocations = [], intervals = new Map(), timerId = 0;
const document = {
  body: new Element(), documentElement: new Element("html"), title: "",
  getElementById(id) { return this.body.querySelector("#" + id); },
  querySelectorAll(s) { return this.body.querySelectorAll(s); },
  querySelector(s) { return this.body.querySelector(s); },
  createElement(tag) {
    const el = new Element(tag);
    this.lastCreated = el;
    if (tag === "canvas") { el.getContext = () => ({ fillRect() {}, drawImage() {} }); el.toBlob = cb => cb(new Blob(["normalized"], { type: "image/png" })); }
    return el;
  },
};
document.body.innerHTML = '<main id="app"></main><div id="sheet"></div><div id="answer"></div><div id="toasts"></div>';
const storage = new Map();
class Events {
  static all = [];
  constructor(url) { this.url = url; this.handlers = {}; this.readyState = 0; Events.all.push(this); }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  emit(type, data) { this.handlers[type]?.({ data: JSON.stringify(data) }); }
  close() { this.closed = true; }
}
const sandbox = vm.createContext({
  document, console, Blob, URLSearchParams,
  FormData: class extends FormData {
    constructor(form) {
      super();
      if (form) for (const [name, el] of Object.entries(form.elements)) this.set(name, el.value);
    }
  },
  URL: { createObjectURL: () => "blob:test", revokeObjectURL: url => revocations.push(url) },
  crypto: { randomUUID: () => `retry-${++timerId}` },
  localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v) },
  location: { hash: "" }, navigator: {}, window: {},
  addEventListener() {}, requestAnimationFrame: fn => fn(),
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  setTimeout: () => ++timerId, clearTimeout() {},
  setInterval: fn => { const id = ++timerId; intervals.set(id, fn); return id; }, clearInterval: id => intervals.delete(id),
  EventSource: Events, createImageBitmap: async () => bitmap || { width: 3000, height: 1500, close() {} },
  fetch: async (url, options = {}) => { calls.push({ url, ...options }); return transport(url, options); },
});
const rootPath = path.resolve("floorhand/static/js");
const modules = new Map();
async function moduleAt(file) {
  file = path.resolve(file);
  if (modules.has(file)) return modules.get(file);
  const mod = new vm.SourceTextModule(await readFile(file, "utf8"), { context: sandbox, identifier: file });
  modules.set(file, mod);
  await mod.link((s, parent) => moduleAt(path.resolve(path.dirname(parent.identifier), s)));
  return mod;
}
const coreModule = await moduleAt(path.join(rootPath, "core.js")); await coreModule.evaluate();
const core = coreModule.namespace;
const reply = (data, status = 200) => ({ status, ok: status < 400, statusText: "Response", json: async () => data });
const tick = () => new Promise(resolve => setImmediate(resolve));

// The common API must keep offered edit choices, language and expired-session behavior.
core.state.lang = "ru";
transport = () => reply({ message: "Choose", message_ru: "Выберите", needs: "rework", options: [{ id: "keep" }] }, 409);
let r = await core.api("/api/edit", { field: "product" });
assert.equal(r.ok, false); assert.equal(r.message, "Выберите"); assert.equal(r.options[0].id, "keep");
transport = () => reply({ detail: [{ msg: "Invalid field" }] }, 422);
assert.equal((await core.api("/api/upload")).message, core.t("checkFields"));
transport = () => { throw new Error("offline"); };
assert.equal((await core.api("/api/upload")).offline, true);
let loginRenders = 0;
core.registerScreen({ id: "login", nav: false, roles: [], render() { loginRenders++; } });
core.setShell({ render: () => document.getElementById("app") });
core.state.me = { role: "owner", stage_id: null }; transport = () => reply({}, 401);
assert.equal((await core.apiFetch("/api/upload", { body: new FormData() })).silent, true);
await tick(); assert.equal(core.state.me, null); assert.equal(loginRenders, 1);

// Query strings and screen cleanup work through the actual router.
let query, stops = 0;
core.registerScreen({ id: "query", roles: ["owner"], render(root, ctx) { query = ctx.query.get("tv"); ctx.onDispose(() => stops++); } });
core.state.me = { role: "owner", stage_id: null }; sandbox.location.hash = "#/query?tv=1";
await core.render(); assert.equal(query, "1"); await core.render(); assert.equal(stops, 1);

// Use the real core registry/API with quiet feedback so interaction tests stay deterministic.
const quiet = new vm.SyntheticModule(Object.keys(core), function () {
  for (const k of Object.keys(core)) this.setExport(k, k === "flash" ? (ok, text) => answer.push({ ok, text }) : k === "toast" ? text => answer.push({ ok: true, text }) : k === "render" ? () => {} : core[k]);
}, { context: sandbox });
await quiet.link(() => {}); await quiet.evaluate();
modules.set(path.join(rootPath, "core.js"), quiet);
for (const screen of ["channels", "shelf", "live", "outbox", "tablet", "login", "me", "work"]) {
  const mod = await moduleAt(path.join(rootPath, "screens", screen + ".js")); await mod.evaluate();
}
// Evaluate the complete entry point so merged screens resolve their real exports.
const registry = await moduleAt(path.join(rootPath, "screens", "index.js"));
await registry.evaluate();
const screens = new Map(core.screenList().map(s => [s.id, s]));
for (const file of (await readFile(path.join(rootPath, "screens", "index.js"), "utf8")).matchAll(/import "\.\/([^\"]+)\.js"/g)) {
  const source = await readFile(path.join(rootPath, "screens", file[1] + ".js"), "utf8");
  const id = source.match(/registerScreen\(\{\s*id:\s*["']([^"']+)["']/)?.[1];
  assert(id && screens.has(id), `${file[1]} must register when the entry point loads`);
}
for (const role of ["owner", "manager"]) {
  const nav = core.navFor({ role, stage_id: null }).map(s => s.id);
  for (const id of ["channels", "shelf", "outbox", "tablet"]) assert(nav.includes(id));
  assert.equal(nav.includes("live"), role === "owner");
}
const tabletNav = core.navFor({ role: "worker", stage_id: 1, tablet: true }).map(s => s.id);
assert(tabletNav.includes("work") && tabletNav.includes("me"));
for (const id of ["channels", "shelf", "outbox", "tablet", "live"]) assert(!tabletNav.includes(id));

function context() {
  const disposers = []; let active = true;
  return { query: new URLSearchParams(), params: {}, me: {}, isActive: () => active, refresh() {},
    onDispose: fn => disposers.push(fn), dispose() { active = false; disposers.forEach(fn => fn()); } };
}
// Both integrations survive the complete screen graph in EN/RU and a gate outage.
const scenario = { id: 301, kind: "ripple", currency: "$", headline: { en: "Demo ripple", ru: "Демо-сбой" },
  movements: [], starvation: [], options: [{ id: "wait", label: { en: "Wait", ru: "Подождать" } }], why: [] };
const quote = {fits:true, capacity_fits:true, material_shortages:[], material_spec_unknown:false,
  queue_eta:"2026-11-02", priority_eta:"2026-10-30", eta:"2026-10-29", stage_rates:[], overtime:[],
  margin_before:.6, margin_after:.5, margin_quoted:.6, target_margin:.35, base_cost:80, extra_cost:20,
  surcharge:40, required_revenue:240, wip_cash:{value:12, known_subtotal:12, basis:{en:"Recorded", ru:"Учтено"}}};
const rushScenario = {...scenario, id:302, kind:"rush", quote,
  options:[{id:"draft_quote", label:{en:"Save draft", ru:"Сохранить черновик"}}]};
for (const lang of ["en", "ru"]) for (const gateOk of [true, false]) for (const id of ["floor", "today"]) {
  core.state.lang = lang; core.state.me = { role: "owner", stage_id: null };
  const root = new Element(), ctx = context(); ctx.me = core.state.me; calls = [];
  transport = url => {
    if (url === "/api/gate/summary") return reply({ message: "FACT: gate <demo>", message_ru: "ФАКТ: вход <демо>" }, gateOk ? 200 : 503);
    if (url === "/api/floor") return reply([]);
    if (url === "/api/owner/today") return reply({ date: "2026-10-27", summary: { open_orders: 0, ready: 0, late: 0, shortages: 0, done_today: 0, capacity: 0 }, cards: [scenario, rushScenario], decided: [] });
    if (url.startsWith("/api/owner/morning")) return reply({ head: "Demo", lines: [] });
    if (url === "/api/rush/catalog") return reply({ products: [{id:1, name:"Demo product"}], customers: [] });
    if (url === "/api/ripple/cards") return reply({ cards: [scenario] });
    throw new Error(`Unexpected integration request: ${url}`);
  };
  await screens.get(id).render(root, ctx);
  assert(calls.some(c => c.url === "/api/gate/summary"));
  assert.equal(root.innerHTML.includes('href="#/gate"'), gateOk);
  assert(root.querySelector("[data-report]"), `${id} retains the disruption form`);
  assert(root.querySelector("[data-whatif-pick]"), `${id} retains stored ripple decisions`);
  assert(root.querySelector('a[href="#/rush"]').textContent.includes(lang === "ru" ? "Срочный заказ" : "Urgent order"));
  if (id === "today") {
    assert.equal(root.querySelectorAll(".scenario-card").length, 2, "Today uses the shared decision components for both kinds");
    assert(root.querySelector(".rush-verdict"));
  }
  if (gateOk) assert(root.innerHTML.includes(lang === "ru" ? "ФАКТ: вход &lt;демо&gt;" : "FACT: gate &lt;demo&gt;"));
  ctx.dispose();
}

// The standalone rush screen renders and submits structured fields in both languages.
for (const lang of ["en", "ru"]) {
  core.state.lang = lang; const root = new Element(), ctx = context(); calls = [];
  let accepted = false;
  transport = url => url === "/api/rush/catalog" ? reply({products:[{id:1, name:"Demo <product>"}], customers:[]})
    : accepted ? reply({card:rushScenario, message:"Calculated", message_ru:"Рассчитано"})
    : reply({message:"Queue changed; retry", message_ru:"Очередь изменилась; повторите"}, 409);
  await screens.get("rush").render(root, ctx);
  assert(root.innerHTML.includes("Demo &lt;product&gt;"));
  const form = root.querySelector("[data-rush-form]");
  for (const [name, value] of Object.entries({product:"1", qty:"2", due:"2026-10-30", margin:"35", variant:"Demo fabric"})) form.elements[name].value = value;
  await form.onsubmit({preventDefault() {}});
  assert(root.querySelector("[data-rush-result]").textContent.includes(lang === "en" ? "retry" : "повторите"));
  assert.equal(form.querySelector("button").disabled, false);
  const first = JSON.parse(calls.at(-1).body);
  assert.deepEqual({product:first.product_id, qty:first.qty, due:first.due_date, margin:first.target_margin},
    {product:1, qty:2, due:"2026-10-30", margin:.35});
  accepted = true;
  await form.onsubmit({preventDefault() {}});
  assert.equal(JSON.parse(calls.at(-1).body).request_key, first.request_key, "retries keep their key");
  assert(root.querySelector(".rush-verdict"));
  assert(root.querySelector(".scenario-metrics").textContent.includes("60% → 50%"));
  assert(root.querySelector('[data-whatif-pick="draft_quote"]'));
  ctx.dispose();
}
for (const status of [200, 503]) {
  const root = new Element(); transport = () => reply({products:[], customers:[], message:"Catalog unavailable"}, status);
  await screens.get("rush").render(root, context());
  assert(root.querySelector(".empty"));
  assert(!root.querySelector("[data-rush-form]"), "unavailable catalog has actionable feedback");
}

// Gatekeeper keeps the dictionary usable, escapes source words, and exposes soft deletion.
for (const lang of ["en", "ru"]) {
  core.state.lang = lang; const root = new Element(), ctx = context(); calls = [];
  transport = url => reply(url === "/api/gate" ? {message:"FACT: 1 line", message_ru:"ФАКТ: 1 строка",
    lines:[{order_id:11, number:211, product:"Demo <chair>", source_quote:"<source>", message:"Needs fabric", message_ru:"Нужна ткань"}]} :
    url === "/api/slang/aliases" ? {aliases:[{id:8, kind:"product", phrase:"<chair>", meaning:{value:1}},
      {id:9, kind:"phrase", phrase:"demo correction", meaning:{}, description:"Demo meaning"}], components:[]} :
    url === "/api/slang/catalog" ? {products:[{id:1, name:"Demo product"}], stages:[]} : {message:"Removed", message_ru:"Удалено"});
  await screens.get("gate").render(root, ctx);
  assert(root.querySelector(".gate-card"));
  assert(root.innerHTML.includes("&lt;source&gt;") && !root.innerHTML.includes("<source>"));
  assert.equal(root.querySelectorAll(".tbl").length, 2);
  assert(root.innerHTML.includes(lang === "en" ? "No part work recorded yet" : "Работа над деталями пока не записана"));
  await root.querySelector('[data-delete-alias="9"]').click();
  assert.equal(calls.at(-1).url, "/api/slang/aliases/9");
  assert.equal(calls.at(-1).method, "DELETE");
  ctx.dispose();
}
core.state.lang = "ru";
const metric = value => ({ tag: "FACT", value });
const offer = { id: 91, product: "Demo cloud", message: "Launch ESTIMATE 2", message_ru: "Запустить ОЦЕНКА 2" };
const overview = { capacity: { available_launch_qty: metric(2) }, items: [], assumptions: { en: "Rules", ru: "Правила" }, imports: [], proposals: [offer] };
const shelfOffer = { id: 72, width: 1600, height: 800, boxes: [[10, 20, 60, 80]], count: 1, before: 25, confidence: .5,
  model: "offline-components", status: "proposed", message: "Count ready", message_ru: "Подсчёт готов", item_name: "Demo armrests", item_name_ru: "Демо-подлокотники", note: "Check boxes", note_ru: "Проверьте рамки", choices: [{ decision: "yes", label: "Yes", label_ru: "Да" }, { decision: "no", label: "No", label_ru: "Нет" }] };

// Both launch decisions carry only the stored id, including refusal/retry and CSV decisions.
for (const decision of ["apply", "reject"]) {
  const root = new Element(), ctx = context(); calls = [];
  transport = url => reply(url.includes("overview") ? overview : { message: "Recorded", message_ru: "Записано" });
  await screens.get("channels").render(root, ctx);
  assert(root.innerHTML.includes("Нет товаров") || root.innerHTML.includes("Пока нет товаров"));
  await root.querySelector(`[data-decision="${decision}"]`).click();
  assert.equal(calls.at(-1).url, `/api/channels/proposals/91/${decision}`); assert.equal(calls.at(-1).body, "{}");
}
{
  const root = new Element(), ctx = context(); calls = [];
  transport = url => reply(url.includes("overview") ? overview : url === "/api/channels/import" ? { id: 4, action_id: 92, status: "proposed", diff: { new: metric(1), changed: metric(0), unchanged: metric(0) }, changes: [] } : { message: "Applied" });
  await screens.get("channels").render(root, ctx); root.querySelector("#channelCsv").value = "channel,sku,stock\nDemo,DEMO-1,3";
  await root.querySelector("#previewCsv").click();
  assert.equal(calls.at(-1).headers["Content-Type"], "text/csv; charset=utf-8");
  assert(calls.at(-1).body.startsWith("channel,"));
  await root.querySelector('[data-import="reject"]').click(); assert.equal(calls.at(-1).url, "/api/channels/proposals/92/reject");
}

// Uploads normalize to PNG, show pixel boxes and preserve retry keys; Yes/No carry no quantities.
for (const decision of ["yes", "no"]) {
  const root = new Element(), ctx = context(); calls = []; revocations = [];
  transport = url => reply(url === "/api/shelf/items" ? { items: [{ id: 1, name: "Demo armrests", name_ru: "Демо-подлокотники", qty: 25 }] } : url === "/api/shelf/count" ? { id: 72 } : url.endsWith("/apply") ? { message: "Recorded", message_ru: "Записано" } : shelfOffer);
  await screens.get("shelf").render(root, ctx);
  root.querySelector("#shelfItem").value = "1";
  await root.querySelector("#shelfFile").onchange({ target: { files: [new Blob(["JPEG"])] } });
  await root.querySelector("#countShelf").click();
  assert(root.querySelector("#shelfPhoto").innerHTML.includes('viewBox="0 0 1600 800"'));
  assert(root.querySelector("#shelfPhoto").innerHTML.includes('width="50" height="60"'));
  assert(root.querySelector("#shelfDecision").textContent.includes("Офлайн-счётчик"));
  const firstUpload = calls.find(c => c.url === "/api/shelf/count");
  assert.equal(firstUpload.body.get("image").type, "image/png");
  await root.querySelector("#countShelf").click();
  assert.equal(calls.filter(c => c.url === "/api/shelf/count").at(-1).body.get("idem_key"), firstUpload.body.get("idem_key"));
  await root.querySelector(`[data-shelf-decision="${decision}"]`).click();
  assert.equal(calls.at(-1).url, "/api/shelf/72/apply"); assert.equal(calls.at(-1).body, JSON.stringify({ decision }));
  ctx.dispose(); assert.deepEqual(revocations, ["blob:test"]);
}

// A stale count cannot overwrite a newer photo/prompt or a disposed screen.
{
  const root = new Element(), ctx = context(); let finish;
  transport = url => url === "/api/shelf/count" ? new Promise(resolve => { finish = resolve; }) : reply({ items: [{ id: 1, name: "Demo", name_ru: "Демо", qty: 2 }] });
  await screens.get("shelf").render(root, ctx); root.querySelector("#shelfItem").value = "1";
  await root.querySelector("#shelfCamera").onchange({ target: { files: [new Blob(["JPEG"])] } });
  const counting = root.querySelector("#countShelf").click();
  root.querySelector("#shelfPrompt").oninput(); finish(reply({ id: 72 })); await counting;
  assert.equal(root.querySelector("#shelfDecision").innerHTML, ""); ctx.dispose();
}

// Initial ticker, SSE deduplication, drop/reconnect resync, TV query and cleanup.
{
  core.state.lang = "en"; const root = new Element(), ctx = context(); ctx.query.set("tv", "1"); calls = [];
  const mark = { type: "marked", id: 1, stage: "Demo sewing", stage_ru: "Демо шитьё", product: "Demo cloud", seq: 1, number: 101, created_at: "2026-10-27T14:02:00+07:00" };
  let snap = { date: "2026-10-27", as_of: mark.created_at, stages: [{ id: 1, name_en: "Demo sewing", name_ru: "Демо шитьё", queue: 5, done_today: 2, capacity: 4, people: [] }], late: [{ id: 1, number: 101, product: "Demo cloud", late_days: 2 }], marks: [mark] };
  transport = url => reply(url === "/api/live/snapshot" ? snap : { running: false });
  await screens.get("live").render(root, ctx); assert(document.body.classList.contains("live-tv"));
  assert(root.querySelector("#liveTicker").textContent.includes("14:02")); assert(root.querySelector("#liveLate").textContent.includes("2 days late"));
  const stream = Events.all.at(-1), before = root.querySelector("#liveTicker").innerHTML;
  stream.emit("marked", mark); assert.equal(root.querySelector("#liveTicker").innerHTML, before);
  stream.onerror(); assert(root.querySelector("#liveStatus").textContent.includes("Reconnecting"));
  const next = { ...mark, id: 2, created_at: "2026-10-27T14:03:00+07:00" };
  snap = { ...snap, stages: [{ ...snap.stages[0], queue: 4 }], marks: [next, mark] };
  stream.readyState = 1; stream.onopen(); await tick();
  assert(root.querySelector("#liveRail").innerHTML.includes("queue-changed")); assert(root.querySelector("#liveTicker").textContent.includes("14:03"));
  assert(!calls.some(c => /money|orders|shipping/.test(c.url)));
  ctx.dispose(); assert(stream.closed); assert.equal(intervals.size, 0); assert(!document.body.classList.contains("live-tv"));
}
{
  const root = new Element(); transport = () => reply({ rows: [{ subject: '<script>bad</script>', body: "Demo draft", to_name: "Demo workshop", created_at: "2026-10-27T14:02:00+07:00" }] });
  await screens.get("outbox").render(root, context()); assert(!root.innerHTML.includes("<script>")); assert(root.innerHTML.includes("Dry run"));
}
// Persistent tablet setup ends the office session; a worker explicitly starts a shift.
{
  const root = new Element(), ctx = context(); calls = [];
  transport = url => reply(url === "/api/orders/catalog/lists" ? { stages: [{ id: 1, name_en: "Demo sewing", name_ru: "Демо шитьё" }] } : url === "/api/marking/tablet" ? { name: "Demo device", people: [{ id: 11, name: "Demo maker", stage: "Demo sewing", stage_ru: "Демо шитьё" }] } : { message: "Recorded" });
  await screens.get("tablet").render(root, ctx); root.querySelector("#tabletStage").value = "1";
  const setup = root.querySelector("#setupTablet").click(); await tick();
  document.getElementById("sheet").querySelector('[data-act="yes"]').click(); await setup;
  assert.equal(calls.at(-1).url, "/api/marking/tablet/setup"); assert.equal(core.state.me, null); assert.equal(core.pref("tablet_device"), "1");
  const tabletModule = await moduleAt(path.join(rootPath, "screens/tablet.js"));
  assert(await tabletModule.namespace.tabletLogin(root));
  const act = root.querySelector("[data-tablet-person]").click(); await tick();
  document.getElementById("sheet").querySelector('[data-act="yes"]').click(); await act;
  assert.equal(calls.at(-1).url, "/api/marking/tablet/act"); assert.equal(calls.at(-1).body, '{"person_id":11}');
  assert.equal(sandbox.location.hash, "#/work");
  transport = () => reply({}, 401); assert.equal(await tabletModule.namespace.tabletLogin(root), false); assert.equal(core.pref("tablet_device"), "0");
}
// The worker material request is an actual action, including its typed text and feedback.
{
  const root = new Element(); calls = []; transport = () => reply({ message: "Office notified" });
  screens.get("me").render(root, { me: { name: "Demo maker", role: "worker" } });
  const request = root.querySelector("#materialRequest").click(); await tick();
  const input = document.getElementById("materialWords"); input.oninput({ target: { value: "demo foam" } });
  document.getElementById("sheet").querySelector('[data-act="yes"]').click(); await request;
  assert.equal(calls.at(-1).url, "/api/marking/material"); assert.equal(calls.at(-1).body, '{"text":"demo foam"}');
}
// Server voice uploads still require the person's stored-proposal decision.
{
  const marking = (await moduleAt(path.join(rootPath, "marking.js"))).namespace; calls = [];
  transport = url => reply(url === "/api/marking/voice/audio" ? { status: "proposal", id: 81, items: [{ say: "Demo sewing" }] } : { message: "Recorded" });
  marking.audio(context()); const input = document.lastCreated; input.files = [new Blob(["demo voice"], { type: "audio/webm" })];
  const upload = input.onchange(); await tick();
  assert.equal(calls.at(-1).url, "/api/marking/voice/audio"); assert.equal(calls.at(-1).body.type, "audio/webm");
  document.getElementById("sheet").querySelector('[data-act="no"]').click(); await upload;
  assert.equal(calls.at(-1).url, "/api/marking/proposals/81/decide"); assert.equal(calls.at(-1).body, '{"yes":false}');
}
// A worker can withdraw a pending claim without accidentally submitting another claim.
{
  const marking = (await moduleAt(path.join(rootPath, "marking.js"))).namespace; calls = [];
  transport = url => reply(url === "/api/marking/claims" ? { presets: [], claims: [{ id: 61, description: "Demo cleanup", work_date: "2026-10-27", status: "pending" }] } : { message: "Withdrawn" });
  const pending = marking.otherWork(context()); await tick();
  await document.getElementById("sheet").querySelector("[data-withdraw]").click(); await pending;
  assert.equal(calls.at(-1).url, "/api/marking/claims/61/withdraw");
  assert(!calls.some(c => c.url === "/api/marking/claims" && c.method === "POST"));
}
assert(answer.every(a => typeof a.text === "string" && a.text.length));
// Refreshing the real shell never accumulates tablet pings or idle listeners.
{
  transport = () => reply({ products: [], customers: [], stages: [], fabrics: [], frames: [] });
  const app = await moduleAt(path.join(rootPath, "app.js")); await app.evaluate();
  core.registerScreen({ id: "shellcheck", roles: ["tablet"], title: () => "Demo", render() {} });
  core.state.me = { role: "worker", stage_id: 1, tablet: true, name: "Demo maker" }; core.setPref("tablet_device", "1");
  sandbox.location.hash = "#/shellcheck";
  await core.render(); await core.render();
  const root = document.getElementById("app");
  for (const ev of ["click", "touchstart", "keydown"]) assert.equal(root.listeners.get(ev).size, 2);
  core.state.me = { role: "worker", stage_id: 1, tablet: false, name: "Demo maker" };
  sandbox.location.hash = "#/me"; await core.render();
  for (const ev of ["click", "touchstart", "keydown"]) assert.equal(root.listeners.get(ev).size, 0);
}
console.log("Front-end interaction contracts passed.");
