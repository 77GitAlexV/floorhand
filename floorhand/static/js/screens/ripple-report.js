// A worker reports a problem without receiving the owner's financial analysis.
import { registerScreen, api, esc, idem, flash } from "../core.js";

registerScreen({
  id: "disruption", roles: ["worker", "tablet", "manager"], icon: "alert",
  title: lang => lang === "ru" ? "Сообщить о сбое" : "Report disruption",
  async render(root, ctx) {
    const L = (en, ru) => ctx.lang === "ru" ? ru : en;
    root.innerHTML = `<header class="head"><h1>${L("Report disruption", "Сообщить о сбое")}</h1></header>
      <section class="panel"><p>${L("The owner will see the effect on the queue and choose a response.", "Владелец увидит последствия для очереди и выберет решение.")}</p>
      <div class="chips"><button class="btn hero" type="button" data-report-preset="Saw is broken for 2 days / Пила сломана на 2 дня">${L("Saw broken", "Пила сломана")}</button>
      <button class="btn hero" type="button" data-report-preset="Foam delivery is 2 days late / Поролон задержится на 2 дня">${L("Foam late", "Поролон задерживается")}</button>
      ${ctx.me.role === "worker" ? `<button class="btn hero" type="button" data-report-preset="${esc(ctx.me.name)} is sick for 1 day / болеет 1 день">${L("Absent next shift", "Не выйду в следующую смену")}</button>` : ""}</div>
      <form><label>${L("One problem and how long", "Одна проблема и срок")}<input class="input" name="report" required maxlength="500"></label>
      <button class="btn primary" type="submit">${L("Report to owner", "Сообщить владельцу")}</button></form></section>`;
    const form = root.querySelector("form");
    let requestKey = idem();
    form.oninput = () => { requestKey = idem(); };
    form.onsubmit = async event => {
      event.preventDefault(); const button = form.querySelector("button"); button.disabled = true;
      const r = await api("/api/ripple/report", { request_key: requestKey, text: form.elements.report.value });
      button.disabled = false;
      if (!r.silent) flash(r.ok, r.message);
      if (r.ok) { requestKey = idem(); form.reset(); }
    };
    root.querySelectorAll("[data-report-preset]").forEach(button => button.onclick = () => {
      requestKey = idem(); form.elements.report.value = button.dataset.reportPreset; form.requestSubmit();
    });
  },
});
