import { registerScreen, api, idem, flash } from "../core.js";
import { btn, chip, empty, icon } from "../ui.js";
import { L, whatIfCard, bindWhatIfCards } from "../scenario-ui.js";
export { whatIfCard, bindWhatIfCards } from "../scenario-ui.js";

export async function mountWhatIf(root, ctx, showCards = false) {
  if (ctx.me.tablet || !["owner", "manager"].includes(ctx.me.role)) return;
  const box = document.createElement("section"); box.className = "scenario-workbench stack";
  box.innerHTML = `<section class="panel stack"><h2>${icon("layers")} ${L("What if…", "А если…")}</h2>
    <p class="muted">${L("Test a disruption against the current queue. Review the consequences before choosing an action.", "Проверьте сбой по текущей очереди. Изучите последствия перед выбором действия.")}</p>
    <div class="chips">${chip(L("Saw broken · 2 days", "Пила сломана · 2 дня"), {attrs:'data-preset="Saw is broken for 2 days / Пила сломана на 2 дня"'})}
      ${chip(L("Foam late · 2 days", "Поролон задержится · 2 дня"), {attrs:'data-preset="Foam delivery is 2 days late / Поролон задержится на 2 дня"'})}</div>
    <form class="stack" data-report><label class="scenario-field">${L("Worker, material or machine + duration", "Работник, материал или станок + срок")}
      <input name="text" class="input" maxlength="500" required placeholder="${L("Saw is broken for 2 days", "Пила сломана на 2 дня")}"></label>
      ${btn({label:L("Show ripple", "Показать последствия"), icon:"layers", kind:"primary"})}</form>
    <a class="btn secondary" href="#/rush">${L("Urgent order: can we promise it?", "Срочный заказ: можем обещать?")}</a></section>
    <div class="feed" data-scenario-cards></div>`;
  root.appendChild(box);
  const form = box.querySelector("form"), submit = form.querySelector("button");
  submit.type = "submit";
  let requestKey = idem();
  form.oninput = () => { requestKey = idem(); };
  form.onsubmit = async e => {
    e.preventDefault(); submit.disabled = true;
    const r = await api("/api/ripple/report", {text:form.elements.text.value, request_key:requestKey});
    submit.disabled = false;
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  };
  box.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => {
    requestKey = idem(); form.elements.text.value = b.dataset.preset;
    box.querySelectorAll("[data-preset]").forEach(x => x.classList.toggle("on", x === b));
    form.requestSubmit();
  });
  if (showCards) {
    const f = await api("/api/ripple/cards");
    if (!box.isConnected) return;
    box.querySelector("[data-scenario-cards]").innerHTML = f.ok ? (f.cards.map(whatIfCard).join("") || empty(L("Choose a preset to explore a scenario.", "Выберите вариант для проверки сценария."))) : empty(f.message || "", "alert");
    bindWhatIfCards(box, ctx);
  }
}

registerScreen({
  id:"ripple", roles:["office"], nav:false, icon:"floor",
  title:lang => lang === "ru" ? "А если…" : "What if…",
  render:(root, ctx) => mountWhatIf(root, ctx, true),
});
