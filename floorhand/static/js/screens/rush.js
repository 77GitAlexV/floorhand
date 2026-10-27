// Financial fields are served only to private owner/manager sessions.
import { registerScreen, api, esc, state, idem, flash } from "../core.js";
const L = (en, ru) => state.lang === "ru" ? ru : en;
const tl = x => x && typeof x === "object" ? (x[state.lang] ?? x.en ?? "") : (x ?? "");
const val = x => x === null || x === undefined ? L("unknown", "неизвестно") : esc(x);

export function rushDetails(q) {
  const pct = n => n === null ? L("unknown", "неизвестно") : `${Math.round(n * 10000) / 100}%`;
  return `<p><b>ESTIMATE</b> ${L("Capacity fits", "Мощности хватает")}: ${q.capacity_fits ? L("yes", "да") : L("no", "нет")} ·
    ${L("Materials confirmed", "Материалы подтверждены")}: ${q.material_shortages.length || q.material_spec_unknown ? L("no — confirm specification and supply", "нет — уточните состав и поставку") : L("yes", "да")}</p>
    <p><b>ESTIMATE</b> ${L("Queue ETA → priority ETA → with overtime", "Срок в очереди → с приоритетом → со сверхурочными")}: ${val(q.queue_eta)} → ${val(q.priority_eta)} → ${val(q.eta)}</p>
    <p><b>CALC</b> ${L("Contribution margin: before → after rush → with surcharge", "Маржа: до → после срочности → с доплатой")}: ${pct(q.margin_before)} → ${pct(q.margin_after)} → ${pct(q.margin_quoted)}.
    ${L("Target", "Цель")}: ${pct(q.target_margin)}.</p>
    <p><b>CALC</b> ${L("Base cost / extra / surcharge / quote total", "Базовая себестоимость / добавка / доплата / итог")}: ${val(q.base_cost)} / ${val(q.extra_cost)} / ${val(q.surcharge)} / ${val(q.required_revenue)}</p>
    <p><b>ESTIMATE</b> ${L("Cash frozen in current WIP", "Деньги в текущем НЗП")}: ${val(q.wip_cash.value)} · ${L("known subtotal", "известная часть")}: ${val(q.wip_cash.known_subtotal)}.
    ${L("Rush cost added before shipment", "Затраты срочного заказа до отгрузки")}: ${val(q.incremental_wip)}. ${esc(tl(q.wip_cash.basis))}</p>
    <details><summary>${L("Rates and overtime by trade", "Ставки и сверхурочные по профессиям")}</summary>
    <ul>${q.stage_rates.map(r => `<li><b>FACT</b> ${esc(tl(r.stage))}: ${val(r.rate)} ${L("per piece", "за штуку")}</li>`).join("")}
    ${q.overtime.map(r => `<li><b>ESTIMATE</b> ${esc(tl(r.stage))}: ${r.pieces} ${L("overtime pieces; premium", "штук сверхурочно; доплата")}: ${val(r.extra_cost)}</li>`).join("")}</ul></details>`;
}

export async function mountRush(host, ctx) {
  const r = await api("/api/rush/catalog");
  if (!r.ok || !host.isConnected) return;
  host.innerHTML = `<details class="disc"><summary>${L("Urgent order: can we promise it?", "Срочный заказ: можем обещать?")}</summary><form class="disc-body">
    <label>${L("Describe the order (optional)", "Опишите заказ (необязательно)")}<input class="input" name="text" placeholder="${L("4 Harbor armchairs by Friday", "4 кресла Harbor к пятнице")}"></label>
    <label>${L("Product (or parse text)", "Изделие (или разбор текста)")}<select class="input" name="product"><option value="">${L("From text", "Из текста")}</option>${r.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
    <label>${L("Quantity", "Количество")}<input class="input" name="qty" type="number" min="1" max="100"></label>
    <label>${L("Due date", "Срок")}<input class="input" name="due" type="date"></label>
    <label>${L("Customer", "Клиент")}<select class="input" name="customer"><option value="">${L("To confirm", "Уточнить")}</option>${r.customers.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
    <label>${L("Fabric and variant (needed for material cost)", "Ткань и вариант (для стоимости материалов)")}<input class="input" name="variant"></label>
    <label>${L("Target contribution margin %", "Целевая маржа %")}<input class="input" name="margin" type="number" min="0" max="99" value="35" required></label>
    <button class="btn primary" type="submit">${L("Calculate rush quote", "Рассчитать срочный заказ")}</button></form></details>`;
  const form = host.querySelector("form");
  let requestKey = idem();
  form.oninput = () => { requestKey = idem(); };
  form.onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(form);
    const button = form.querySelector("button"); button.disabled = true;
    const result = await api("/api/rush/quote", { request_key: requestKey, text: f.get("text"),
      product_id: f.get("product") ? Number(f.get("product")) : null, qty: f.get("qty") ? Number(f.get("qty")) : null,
      due_date: f.get("due") || null, customer_id: f.get("customer") ? Number(f.get("customer")) : null,
      variant: f.get("variant"), target_margin: Number(f.get("margin")) / 100 });
    button.disabled = false;
    if (!result.silent) flash(result.ok, result.message);
    if (result.ok) ctx.refresh();
  };
}

registerScreen({
  id: "rush", roles: ["office"], nav: false, icon: "clock",
  title: lang => lang === "ru" ? "Срочный расчёт" : "Rush quote",
  render: (root, ctx) => mountRush(root, {...ctx, refresh: () => ctx.go("ripple")}),
});
