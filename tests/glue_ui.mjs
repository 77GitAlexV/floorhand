// Exercise voice/read-back ordering without a microphone, browser or network.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../floorhand/static/js/screens/handsfree.js", import.meta.url), "utf8")
  .replace(/^import .*?;$/gm, "") + "";
const tick = () => new Promise(resolve => setImmediate(resolve));

async function surface(lang = "en", withSpeech = true) {
  const calls = [], recognizers = [], utterances = [], observers = [];
  let screen;
  const state = { lang, screen: "handsfree", me: { role: "worker", stage_id: 4 } };
  const meaning = { product_id: 2, qty: 2, stage_id: 4, option: "curved legs" };
  let failDecision = false;
  const api = async (path, body) => {
    calls.push({ path, body });
    if (path.endsWith("/catalog")) return { ok: true, products: [{ id: 2, name: "Harbor armchair" }],
      stages: [{ id: 4, name_en: "Assembly", name_ru: "Сборка" }], options: ["curved legs"] };
    if (path.endsWith("/propose") || path.endsWith("/fix")) return { ok: true, id: 7, meaning, can_apply: 2,
      message: "CALC: two Harbor armchairs, curved legs, assembly." };
    if (failDecision) return { ok: false, offline: true, message: "No connection" };
    return { ok: true, status: "applied", message: "Recorded" };
  };
  const elements = new Map();
  function element(key) {
    if (!elements.has(key)) elements.set(key, { hidden: key === "#glue-choice", disabled: false, textContent: "",
      setPointerCapture() {}, elements: Object.fromEntries(["text", "product_id", "qty", "stage_id", "option", "component"].map(k => [k, { value: "" }])) });
    return elements.get(key);
  }
  const root = { isConnected: true, innerHTML: "", querySelector: element,
    querySelectorAll() { return [element("#glue-yes"), element("#glue-no")]; } };
  class Recognition {
    constructor() { this.starts = 0; this.stops = 0; this.aborts = 0; recognizers.push(this); }
    start() { this.starts++; }
    stop() { this.stops++; }
    abort() { this.aborts++; }
    hear(text) { this.onresult({ results: [[{ transcript: text }]] }); }
  }
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
  }
  vm.runInNewContext("const { registerScreen, api, state, esc, go, empty } = core;\n" + source, {
    core: { registerScreen(value) { screen = value; }, api, state, esc: String, go(id) { state.screen = id; }, empty: String },
    window: { SpeechRecognition: withSpeech ? Recognition : undefined,
      speechSynthesis: { cancel() {}, speak(u) { utterances.push(u); } } },
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    MutationObserver: Observer,
    document: { getElementById() { return {}; } },
  });
  await screen.render(root);
  return { root, element, calls, recognizers, utterances, observers, state,
    failDecisions(value) { failDecision = value; } };
}

const s = await surface();
assert(s.root.innerHTML.includes("[hidden] {display:none !important}"));
assert(s.root.innerHTML.includes("min-height:240px"));
assert(s.root.innerHTML.includes("min-height:140px"));
assert.equal(s.element("#glue-choice").hidden, true);
s.element("#glue-talk").onpointerdown({ preventDefault() {}, pointerId: 1 });
assert.equal(s.recognizers.length, 1);
s.element("#glue-talk").onpointerup();
assert.equal(s.recognizers[0].stops, 1);
s.recognizers[0].hear("assembled two harbor on curved legs");
await tick();
assert.equal(s.recognizers.length, 1, "do not listen while reading the proposal aloud");
assert.equal(s.element("#glue-choice").hidden, false);
assert.equal(s.element("#glue-yes").disabled, false);
s.utterances.at(-1).onend();
assert.equal(s.recognizers.length, 2);
s.recognizers.at(-1).hear("maybe");
await tick();
assert(!s.calls.some(c => c.path.endsWith("/decide")), "unclear speech cannot confirm");
s.failDecisions(true);
await s.element("#glue-yes").onclick();
assert.equal(s.element("#glue-choice").hidden, false, "failed confirmation retains its proposal id");
s.failDecisions(false);
await s.element("#glue-yes").onclick();
assert.equal(s.element("#glue-choice").hidden, true);
assert.equal(s.calls.filter(c => c.path.endsWith("/decide")).length, 2);
await s.element("#glue-yes").onclick();
assert.equal(s.calls.filter(c => c.path.endsWith("/decide")).length, 2, "extra click cannot mark another batch");

const ru = await surface("ru");
const form = ru.element("#glue-type");
form.elements.text.value = "два харбора на дугах";
form.onsubmit({ preventDefault() {}, target: form });
await tick();
const fix = ru.element("#glue-fix");
await fix.onsubmit({ preventDefault() {}, target: fix });
assert(ru.calls.some(c => c.path.endsWith("/fix") && c.body.product_id === 2 && c.body.qty === 2));
ru.utterances.at(-1).onend();
ru.recognizers.at(-1).hear("да");
await tick();
assert(ru.calls.some(c => c.path === "/api/slang/7/decide" && c.body.spoken === "да"));
ru.root.isConnected = false;
ru.observers[0].callback();
assert(ru.observers[0].disconnected);
assert(ru.recognizers.at(-1).aborts > 0);

const fallback = await surface("ru", false);
assert(fallback.element("#glue-sentence").textContent.includes("нет распознавания речи"));
console.log("FACT: hands-free UI event checks passed (read-back ordering, speech confirmation, correction, retries, cleanup, fallback).");
