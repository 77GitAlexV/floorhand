import { registerScreen, api, esc, state, idem, flash } from "../core.js";
import { rushDetails, mountRush } from "./rush.js";
const L = (en, ru) => state.lang === "ru" ? ru : en;
const tl = x => x && typeof x === "object" ? (x[state.lang] ?? x.en ?? "") : (x ?? "");
const val = x => x === null || x === undefined ? L("unknown", "неизвестно") : esc(x);

export function whatIfCard(c) {
  return `<article class="decision hot" data-whatif="${c.id}" data-kind="${esc(c.kind)}">
    <span class="d-kind">${c.kind === "rush" ? L("Rush quote", "Срочный расчёт") : L("Disruption ripple", "Последствия сбоя")}</span>
    <h3>${esc(tl(c.headline))}</h3><p class="muted">${L("Currency", "Валюта")}: ${esc(c.currency)} · ${L("Forecasts are estimates.", "Прогнозы — оценки.")}</p>
    ${c.quote ? rushDetails(c.quote) : ""}
    ${(c.starvation || []).length ? `<ul>${c.starvation.map(g => `<li><b>ESTIMATE</b> ${esc(tl(g.stage))}: ${L("runs out of ready work at", "закончится готовая работа в")} ${esc(g.at.replace("T", " "))} (${g.ready}/${g.capacity})</li>`).join("")}</ul>` : ""}
    <details open><summary>${L("Orders affected and customer notices", "Затронутые заказы и сообщения клиентам")} <b>ESTIMATE</b></summary>
    <ul>${c.movements.map(m => `<li>#${m.number} · ${esc(m.customer || L("stock", "склад"))}: ${val(m.old_eta)} → ${val(m.new_eta)} · ${val(m.days)} ${L("calendar days", "календарных дней")}${m.notify ? ` · ${L("draft notice needed", "нужен черновик клиенту")}` : ""}</li>`).join("") || `<li>${L("No existing order ETA moves.", "Сроки существующих заказов не сдвигаются.")}</li>`}</ul></details>
    ${c.options.map(o => `<section class="panel"><h4>${esc(tl(o.label))}</h4>
      ${o.late_orders !== undefined ? `<p><b>ESTIMATE</b> ${o.late_orders} ${L("orders late", "заказов опоздают")} · ${o.saved_orders} ${L("saved from lateness", "спасены от опоздания")} · ${L("extra labour", "дополнительный труд")}: ${val(o.cost)} · ${L("penalties", "штрафы")}: ${val(o.penalties)}</p>
      <details><summary>${L("This option's ETA changes", "Сдвиги сроков этого варианта")}</summary><ul>${o.movements.map(m => `<li><b>ESTIMATE</b> #${m.number}: ${val(m.old_eta)} → ${val(m.new_eta)}</li>`).join("")}</ul></details>` : ""}
      <button type="button" class="btn primary" data-whatif-pick="${esc(o.id)}">${L("Choose this option", "Выбрать этот вариант")}</button></section>`).join("")}
    <details><summary>${L("Why? Facts and assumptions", "Почему? Факты и допущения")}</summary><ul>${c.why.map(w => `<li><b>${w.tag}</b> ${esc(tl(w))}</li>`).join("")}</ul></details>
    <button type="button" class="btn ghost" data-whatif-reject>${L("Decline / dismiss stale card", "Отклонить / убрать устаревшую карточку")}</button>
    <a class="btn ghost" href="/paperwork?lang=${state.lang}">${L("Review dry-run outbox", "Посмотреть черновики в исходящих")}</a>
  </article>`;
}

export function bindWhatIfCards(root, ctx) {
  root.querySelectorAll("[data-whatif-reject]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    const r = await api(`/api/ripple/cards/${b.closest("[data-whatif]").dataset.whatif}/reject`, {});
    b.disabled = false;
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  });
  root.querySelectorAll("[data-whatif-pick]").forEach(b => b.onclick = async () => {
    const card = b.closest("[data-whatif]");
    b.disabled = true;
    const r = await api(`/api/${card.dataset.kind}/cards/${card.dataset.whatif}/apply`, { pick: b.dataset.whatifPick });
    b.disabled = false;
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  });
}

export async function mountWhatIf(root, ctx, showCards = false) {
  if (ctx.me.tablet || !["owner", "manager"].includes(ctx.me.role)) return;
  const box = document.createElement("section"); box.className = "panel";
  box.innerHTML = `<h2>${L("What if…", "А если…")}</h2><p>${L("Report one disruption, or test an urgent order against the queue.", "Сообщите об одном сбое или проверьте срочный заказ по очереди.")}</p>
    <div class="chips"><button class="btn" type="button" data-preset="Saw is broken for 2 days / Пила сломана на 2 дня">${L("Saw broken", "Пила сломана")}</button>
    <button class="btn" type="button" data-preset="Foam delivery is 2 days late / Поролон задержится на 2 дня">${L("Foam late", "Поролон задерживается")}</button></div>
    <form data-report><label>${L("Worker, material or machine + duration", "Работник, материал или станок + срок")}<input name="text" class="input" maxlength="500" required placeholder="${L("Worker short name is sick for 2 days", "Короткое имя работника: болеет 2 дня")}"></label>
    <button class="btn primary" type="submit">${L("Show ripple", "Показать последствия")}</button></form><div data-rush></div><div data-scenario-cards></div>`;
  root.appendChild(box);
  const form = box.querySelector("form");
  let requestKey = idem();
  form.oninput = () => { requestKey = idem(); };
  form.onsubmit = async e => {
    e.preventDefault(); const b = form.querySelector("button"); b.disabled = true;
    const r = await api("/api/ripple/report", { text: form.elements.text.value, request_key: requestKey });
    b.disabled = false;
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  };
  box.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => {
    requestKey = idem(); form.elements.text.value = b.dataset.preset; form.requestSubmit();
  });
  await mountRush(box.querySelector("[data-rush]"), ctx);
  if (showCards) {
    const f = await api("/api/ripple/cards");
    if (f.ok && box.isConnected) {
      box.querySelector("[data-scenario-cards]").innerHTML = f.cards.map(whatIfCard).join("");
      bindWhatIfCards(box, ctx);
    }
  }
}

registerScreen({
  id: "ripple", roles: ["office"], nav: false, icon: "floor",
  title: lang => lang === "ru" ? "А если…" : "What if…",
  render: (root, ctx) => mountWhatIf(root, ctx, true),
});
