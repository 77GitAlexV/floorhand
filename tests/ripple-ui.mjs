// Pure rendering and event contract checks; no DOM driver or network.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const state = {lang: "en"};
const requests = [];
let refreshes = 0, registered;
const context = vm.createContext({console});
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const core = new vm.SyntheticModule(["api", "esc", "state", "idem", "flash", "registerScreen", "name", "t", "money", "fmtDate"], function () {
  this.setExport("api", async (path, body) => {requests.push({path, body}); return {ok: true};});
  this.setExport("esc", esc); this.setExport("state", state);
  this.setExport("idem", () => "test-key"); this.setExport("flash", () => {});
  this.setExport("registerScreen", screen => {registered = screen;});
  this.setExport("name", x => x?.name_en || ""); this.setExport("t", x => x);
  this.setExport("money", (cur, n) => `${cur}${n}`); this.setExport("fmtDate", x => x?.slice(0, 10) || "—");
}, {context});
const modules = {};
const ui = new vm.SourceTextModule(fs.readFileSync("floorhand/static/js/ui.js", "utf8"), {context});
await ui.link(() => core);
async function read(name) {
  if (modules[name]) return modules[name];
  const module = new vm.SourceTextModule(fs.readFileSync(`floorhand/static/js/screens/${name}.js`, "utf8"), {context});
  modules[name] = module;
  await module.link(async specifier => /(?:^|\/)core.js$/.test(specifier) ? core : /(?:^|\/)ui.js$/.test(specifier) ? ui : read(specifier.replace(/^\.\//, "").replace(/\.js$/, "")));
  return module;
}
const ripple = await read("ripple"); await ripple.evaluate();
const q = {capacity_fits: false, material_shortages: [], material_spec_unknown: true, queue_eta: null,
  priority_eta: null, eta: null, margin_before: null, margin_after: null, margin_quoted: null,
  target_margin: .35, base_cost: null, extra_cost: null, surcharge: null, required_revenue: null,
  wip_cash: {value: null, known_subtotal: 12, basis: {en: "known costs", ru: "известные затраты"}},
  incremental_wip: null, stage_rates: [], overtime: []};
const card = {id: 7, kind: "rush", currency: "$", headline: {en: "<script>demo</script>", ru: "<b>Демо</b>"}, quote: q,
  movements: [], starvation: [], options: [{id: "draft_quote", label: {en:"Save draft", ru:"Черновик"}}], why: []};
let html = ripple.namespace.whatIfCard(card);
assert(html.includes("&lt;script&gt;demo&lt;/script&gt;"));
assert(!html.includes("<script>")); assert(html.includes("unknown"));
assert(html.includes("data-whatif-pick=\"draft_quote\""));
state.lang = "ru";
html = ripple.namespace.whatIfCard(card);
assert(html.includes("неизвестно")); assert(html.includes("&lt;b&gt;Демо&lt;/b&gt;"));
const disruption = {...card, kind:"ripple", quote:undefined, shock:{kind:"machine", stage_id:1, start:"2026-10-28", until:"2026-10-30"},
  stages:[{id:1, name:{en:"Cutting", ru:"Раскрой"}}, {id:2, name:{en:"Sewing", ru:"Шитьё"}}],
  starvation:[{stage_id:2, at:"2026-10-29T14:46+07:00", ready:2, capacity:3}],
  movements:[{number:211, customer:"Demo <customer>", old_eta:"2026-10-29", new_eta:"2026-11-02", notify:true}],
  options:[{id:"wait", label:{en:"Wait", ru:"Ждать"}, late_orders:1, baseline_late_orders:64, total_late_orders:65, saved_orders:0, cost:0, movements:[]},
    {id:"half_shift", label:{en:"Overtime", ru:"Сверхурочно"}, late_orders:0, baseline_late_orders:64, total_late_orders:64, saved_orders:1, cost:15, movements:[]}]};
for (const lang of ["en", "ru"]) {
  state.lang = lang;
  html = ripple.namespace.whatIfCard(disruption);
  assert(html.includes(lang === "en" ? "Thu 14:46" : "чт 14:46"));
  assert(!html.includes("14:46+07:00"), "timeline shows the shop clock without ISO noise");
  assert(html.includes('class="ripple-timeline"'));
  assert(html.includes(lang === "en" ? "Already late in baseline" : "Уже опаздывали в базовом плане"));
  assert(html.includes("64") && html.includes("65"));
  assert(html.includes("Demo &lt;customer&gt;"));
  assert.equal((html.match(/class="btn primary/g) || []).length, 1);
  assert(html.includes('<details class="disc" data-customer-notices>'), "notices start collapsed");
  assert(html.includes(lang === "en" ? "updates the plan" : "изменяет план"));
  assert(html.includes('class="tag calc"') && html.includes('class="tag estimate"'));
}
const container = {dataset: {kind:"rush", whatif:"7"}};
const pick = {dataset: {whatifPick:"draft_quote"}, closest: () => container};
const reject = {closest: () => container};
const root = {querySelectorAll: selector => selector === "[data-whatif-pick]" ? [pick] : [reject]};
ripple.namespace.bindWhatIfCards(root, {refresh: () => {refreshes++;}});
await pick.onclick(); await reject.onclick();
assert.equal(requests[0].path, "/api/rush/cards/7/apply");
assert.equal(JSON.stringify(requests[0].body), '{"pick":"draft_quote"}');
assert.equal(requests[1].path, "/api/ripple/cards/7/reject");
assert.equal(refreshes, 2);
assert.equal(pick.disabled, false);
const report = await read("ripple-report"); await report.evaluate();
assert(registered.roles.includes("tablet"));
const button = {};
const form = {elements:{report:{value:"Saw broken"}}, querySelector: () => button, reset: () => {}};
const reportRoot = {querySelector: () => form, querySelectorAll: () => []};
await registered.render(reportRoot, {lang:"ru", me:{role:"worker", name:"<Demo>"}});
assert(reportRoot.innerHTML.includes("&lt;Demo&gt;"));
assert(reportRoot.innerHTML.includes("Сообщить о сбое"));
await form.onsubmit({preventDefault: () => {}});
assert.equal(requests[2].path, "/api/ripple/report");
assert.deepEqual(Object.keys(requests[2].body).sort(), ["request_key", "text"]);
console.log("Ripple UI rendering and handlers passed in English and Russian.");
