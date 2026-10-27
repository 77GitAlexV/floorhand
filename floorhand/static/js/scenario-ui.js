// Shared decision rendering for Today, What if and urgent quotes.
import { api, esc, state, flash, fmtDate, money } from "./core.js";
import { btn, empty, icon, pill, stat, table } from "./ui.js";

export const L = (en, ru) => state.lang === "ru" ? ru : en;
const tl = x => x && typeof x === "object" ? (x[state.lang] ?? x.en ?? "") : (x ?? "");
const val = x => x == null ? L("unknown", "неизвестно") : esc(x);
const date = x => x ? esc(fmtDate(x, {withDay:true})) : val(null);
const cash = (x, currency) => x == null ? val(null) : esc(money(currency, x));
const tag = k => `<span class="tag ${k.toLowerCase()}">${k}</span>`;

export function shopClock(iso) {
  // Keep the API's shop wall clock even on a travelling phone.
  if (!iso) return val(null);
  const day = new Date(iso.slice(0, 10) + "T12:00:00Z").getUTCDay();
  const names = state.lang === "ru" ? ["вс", "пн", "вт", "ср", "чт", "пт", "сб"] : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return esc(`${names[day]} ${iso.slice(11, 16)}`);
}

function orderRows(moves) {
  return `<div class="scenario-orders">${moves.map(m => `<div class="scenario-order">
    <div><b>#${esc(m.number)}</b><small>${esc(m.customer || L("To stock", "На склад"))}</small></div>
    <div class="eta-change"><span>${date(m.old_eta)}</span>${icon("chevron")}<strong>${date(m.new_eta)}</strong></div>
    </div>`).join("")}</div>`;
}

function timeline(c) {
  const stages = c.stages || (c.starvation || []).map(g => ({id:g.stage_id, name:g.stage}));
  if (!stages.length) return "";
  return `<section><h4>${L("When stages run dry", "Когда этапам не хватит работы")} ${tag("ESTIMATE")}</h4>
    <p class="muted">${L("Shop time · first new gap compared with baseline", "Время цеха · первый новый простой относительно базового плана")}</p>
    <ol class="ripple-timeline">${stages.map(s => {
      const gap = (c.starvation || []).find(g => g.stage_id === s.id);
      const stopped = c.shock?.stage_id === s.id && c.shock.kind === "machine";
      return `<li class="${gap || stopped ? "affected" : ""}"><span class="timeline-dot"></span><div><b>${esc(tl(s.name))}</b>
        <p>${gap ? `${shopClock(gap.at)} · ${L("ready work runs out", "готовая работа закончится")}` : stopped ? `${L("Stopped", "Остановлен")}: ${date(c.shock.start)} → ${date(c.shock.until)}` : L("No additional idle gap", "Нового простоя нет")}</p>
        ${gap ? `<small>${tag("CALC")} ${gap.ready} / ${gap.capacity} ${L("pieces ready / daily capacity", "готово штук / мощность за день")}</small>` : ""}</div></li>`;
    }).join("")}</ol></section>`;
}

export function rushDetails(q, currency = "") {
  const pct = n => n == null ? val(null) : `${Math.round(n * 10000) / 100}%`;
  return `<section class="rush-verdict ${q.fits ? "fits" : "does-not-fit"}">
    ${pill(q.fits ? L("Fits the promise", "Можно обещать") : L("Does not fit yet", "Пока нельзя обещать"), q.fits ? "good" : "warn")}
    <p>${tag("ESTIMATE")} ${q.capacity_fits ? L("Capacity fits the requested date.", "Мощности хватает к нужному сроку.") : L("Capacity cannot meet the requested date.", "Мощности не хватает к нужному сроку.")}
    ${q.material_shortages.length || q.material_spec_unknown ? L("Confirm specifications and material supply before promising.", "Перед обещанием уточните параметры и поставку материалов.") : L("Materials are available in the recorded stock.", "Материалы есть по данным учёта.")}</p>
    <p>${L("Queue ETA → priority → overtime", "Срок в очереди → приоритет → сверхурочные")}: <b>${date(q.queue_eta)} → ${date(q.priority_eta)} → ${date(q.eta)}</b></p></section>
    <div class="scenario-metrics">
      ${stat(L("Overtime premium", "Доплата за сверхурочные"), cash(q.extra_cost, currency), {sub:tag("ESTIMATE")})}
      ${stat(L("Required surcharge", "Необходимая доплата"), cash(q.surcharge, currency), {sub:tag("CALC")})}
      ${stat(L("Margin before → after rush", "Маржа до → после срочности"), `${pct(q.margin_before)} → ${pct(q.margin_after)}`, {sub:tag("CALC")})}
      ${stat(L("Margin with surcharge", "Маржа с доплатой"), pct(q.margin_quoted), {sub:`${tag("CALC")} ${L("Target", "Цель")}: ${pct(q.target_margin)}`})}
    </div>
    <details class="disc"><summary>${L("Cost basis and trade rates", "Себестоимость и ставки по этапам")}</summary><div class="disc-body stack">
      <p>${tag("CALC")} ${L("Base cost / quote total", "Себестоимость / итог расчёта")}: ${cash(q.base_cost, currency)} / ${cash(q.required_revenue, currency)}</p>
      <p>${tag("ESTIMATE")} ${L("Cash in current WIP", "Деньги в текущем НЗП")}: ${cash(q.wip_cash.value, currency)} · ${L("Known subtotal", "Известная часть")}: ${cash(q.wip_cash.known_subtotal, currency)}. ${esc(tl(q.wip_cash.basis))}</p>
      ${table({cols:[{label:L("Trade", "Этап")},{label:`${tag("FACT")} ${L("Rate / piece", "Ставка / шт")}`}], rows:q.stage_rates.map(r => ({cells:[esc(tl(r.stage)), cash(r.rate, currency)]}))})}
      ${q.overtime.map(r => `<p>${tag("ESTIMATE")} ${esc(tl(r.stage))}: ${r.pieces} ${L("overtime pieces", "штук сверхурочно")} · ${cash(r.extra_cost, currency)}</p>`).join("")}
    </div></details>`;
}

export function whatIfCard(c) {
  const notices = (c.movements || []).filter(m => m.notify);
  return `<article class="decision ${c.kind === "ripple" ? "hot" : ""} scenario-card" data-whatif="${c.id}" data-kind="${esc(c.kind)}">
    <div class="d-kind">${icon(c.kind === "rush" ? "clock" : "layers")} ${c.kind === "rush" ? L("Rush quote", "Срочный расчёт") : L("Disruption ripple", "Последствия сбоя")} ${tag("ESTIMATE")}</div>
    <h3>${esc(tl(c.headline))}</h3><p class="d-why muted">${L("Currency", "Валюта")}: ${esc(c.currency)} · ${L("Compared with the current queue baseline.", "Сравнение с базовым планом текущей очереди.")}</p>
    ${c.quote ? rushDetails(c.quote, c.currency) : timeline(c)}
    <section><h4>${c.kind === "rush" ? L("Displaced orders", "Сдвинутые заказы") : L("Order ETA changes", "Изменения сроков заказов")} ${tag("ESTIMATE")}</h4>
      ${c.movements.length ? orderRows(c.movements) : empty(L("No existing order ETA moves.", "Сроки существующих заказов не сдвигаются."), "check")}</section>
    ${(c.stage_movements || []).length ? `<details class="disc"><summary>${L("Stage work moves, even where the final ETA holds", "Работа по этапам сдвигается, даже если итоговый срок прежний")} ${tag("ESTIMATE")}</summary><div class="disc-body stack">
      <p class="muted">${L("Downstream waiting time can absorb a delayed start.", "Ожидание на следующих этапах может поглотить задержку старта.")}</p>
      ${c.stage_movements.map(m => `<p>#${m.number} · ${esc(tl((c.stages || []).find(s => s.id === m.stage_id)?.name))}: ${date(m.old_date)} → ${date(m.new_date)}</p>`).join("")}</div></details>` : ""}
    <div class="scenario-options">${c.options.map((o, i) => `<section class="scenario-option"><h4>${esc(tl(o.label))}</h4>
      ${o.late_orders !== undefined ? `<p>${tag("ESTIMATE")} <b>${o.late_orders}</b> ${L("newly late because of this disruption", "новых опозданий из-за сбоя")} · ${o.saved_orders} ${L("saved from new lateness", "спасено от новых опозданий")}</p>
        <p class="muted">${tag("ESTIMATE")} ${L("Already late in baseline", "Уже опаздывали в базовом плане")}: ${val(o.baseline_late_orders)} · ${L("Total late", "Всего опозданий")}: ${val(o.total_late_orders)}</p>
        <p>${tag(o.cost_tag || "ESTIMATE")} ${L("Extra labour", "Дополнительный труд")}: <b>${cash(o.cost, c.currency)}</b> · ${L("Penalties", "Штрафы")}: ${cash(o.penalties, c.currency)}</p>
        ${o.movements.length ? `<details class="disc"><summary>${L("This option’s ETA changes", "Изменения сроков этого варианта")}</summary><div class="disc-body">${orderRows(o.movements)}</div></details>` : ""}` : ""}
      ${btn({label:c.kind === "rush" ? esc(tl(o.label)) : L("Choose this option", "Выбрать этот вариант"), kind:i === 0 ? "primary" : "secondary", attrs:`data-whatif-pick="${esc(o.id)}"`})}</section>`).join("")}</div>
    <details class="disc" data-customer-notices><summary>${L("Customer notice drafts", "Черновики сообщений клиентам")} ${tag("CALC")} · ${notices.length}</summary><div class="disc-body">${notices.length ? orderRows(notices) : empty(L("No customer notice needed for unchanged ETAs.", "При прежних сроках сообщения клиентам не нужны."))}<p class="muted">${c.kind === "rush" ? L("Save a conditional quote draft for review. No capacity is reserved.", "Сохраните предварительный расчёт для проверки. Мощности не бронируются.") : L("Choosing an option updates the plan and saves the corresponding customer notice drafts for review.", "Выбор варианта изменяет план и сохраняет соответствующие черновики сообщений клиентам для проверки.")} ${L("Nothing is sent automatically.", "Ничего не отправляется автоматически.")}</p></div></details>
    <details class="disc"><summary>${L("Why? Facts and assumptions", "Почему? Факты и допущения")}</summary><div class="disc-body stack">${c.why.map(w => `<p>${tag(w.tag)} ${esc(tl(w))}</p>`).join("")}</div></details>
    <div class="d-acts">${btn({label:L("Decline / dismiss stale card", "Отклонить / убрать устаревшую карточку"), kind:"ghost", attrs:"data-whatif-reject"})}
      <a class="btn ghost" href="#/outbox">${L("Review dry-run outbox", "Посмотреть черновики исходящих")}</a></div>
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
    const r = await api(`/api/${card.dataset.kind}/cards/${card.dataset.whatif}/apply`, {pick:b.dataset.whatifPick});
    b.disabled = false;
    if (!r.silent) flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  });
}
