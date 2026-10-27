// Office "Paperwork": what needs paper today (new items without a sheet, sheets out of
// date, cancelled orders whose sheet is still on the floor) and the way into the full
// paperwork page (/paperwork), which prints sheets, delivery notes and keeps the outbox.
import { registerScreen, addStrings, api, t, esc, state } from "../core.js";
import { icon, stat, empty, sectionTitle } from "../ui.js";

addStrings({
  en: {
    pwTitle: "Paperwork", pwOpen: "Open paperwork", pwNew: "New items without a sheet", pwChanged: "Sheets out of date",
    pwToday: "From today's orders", pwCancelled: "Cancelled, sheet still on the floor", 
    pwAllGood: "Nothing needs attention on paper.", pwWas: "was", pwNow: "now",
    pwHint: "Order sheets carry a permanent QR code; every print is a numbered copy. Delivery notes and e-mails go through the outbox.",
  },
  ru: {
    pwTitle: "Документы", pwOpen: "Открыть документы", pwNew: "Новые изделия без листка", pwChanged: "Листки устарели",
    pwToday: "Из сегодняшних заказов", pwCancelled: "Отменён, листок ещё в цеху", 
    pwAllGood: "По бумагам всё в порядке.", pwWas: "было", pwNow: "стало",
    pwHint: "На листке заказа постоянный QR-код; каждая печать — нумерованная копия. Накладные и письма идут через исходящие.",
  },
});

registerScreen({
  id: "paperwork", roles: ["office"], icon: "paper", title: lang => lang === "ru" ? "Документы" : "Paperwork",
  async render(root) {
    const a = await api("/api/paperwork/sheets/attention");
    if (!a.ok) { if (!a.silent) root.innerHTML = empty(a.message, "alert"); return; }
    const c = a.counts || {};
    const cancelled = a.cancelled_sheets || [];
    const changed = a.changed || [];
    const lines = ch => (ch.changes || []).map(x =>
      `<small class="muted">${esc((state.lang === "ru" && x.field_ru) || x.field || "")}: ${t("pwWas")} ${esc(x.was ?? "")} → ${t("pwNow")} ${esc(x.now ?? "")}</small>`).join("<br>");
    root.innerHTML = `
      <header class="head"><div><h1>${t("pwTitle")}</h1><p class="muted">${t("pwHint")}</p></div>
        <div class="actions"><a class="btn primary md" href="/paperwork?lang=${state.lang}" target="_blank" rel="noopener">${icon("print")}<span>${t("pwOpen")}</span></a></div></header>
      <div class="stats">
        ${stat(t("pwNew"), c.new ?? 0)}
        ${stat(t("pwChanged"), c.changed ?? 0, { kind: c.changed ? "bad" : "" })}
        ${stat(t("pwToday"), c.today ?? 0)}
        ${stat(t("pwCancelled"), cancelled.length, { kind: cancelled.length ? "bad" : "" })}
      </div>
      ${!changed.length && !cancelled.length ? empty(t("pwAllGood"), "check") : ""}
      ${changed.length ? sectionTitle(t("pwChanged"), changed.length) + `<ul class="plain">${changed.slice(0, 20).map(ch =>
        `<li><span><b>${esc(ch.label || "#" + ch.order_number)}</b><br>${lines(ch)}</span></li>`).join("")}</ul>` : ""}
      ${cancelled.length ? sectionTitle(t("pwCancelled"), cancelled.length) + `<ul class="plain">${cancelled.slice(0, 20).map(x =>
        `<li><span><b>${esc(x.label)}</b></span></li>`).join("")}</ul>` : ""}`;
  },
});
