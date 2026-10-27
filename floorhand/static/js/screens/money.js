// Worker "Money": the last 7 days by work date, paid / to be paid. Nothing else.
import { registerScreen, addStrings, api, t, esc, money, fmtDate, pcs } from "../core.js";
import { icon, stat, empty } from "../ui.js";

addStrings({
  en: {
    money: "Money", week: "Last 7 days", earned7: "earned in 7 days", paid: "Paid", toBePaid: "To be paid",
    pendingN: "{n} pc(s) waiting for a price", todayShort: "today", piecesN: "{n} pcs", moneyNote: "Money follows the day the work was done. Paid work is frozen.",
  },
  ru: {
    money: "Деньги", week: "Последние 7 дней", earned7: "заработано за 7 дней", paid: "Выплачено", toBePaid: "К выплате",
    pendingN: "{n} шт ждут цену", todayShort: "сегодня", piecesN: "{n} шт", moneyNote: "Деньги идут за днём, когда сделана работа. Выплаченное заморожено.",
  },
});

registerScreen({
  id: "money", roles: ["worker"], icon: "money", title: lang => lang === "ru" ? "Деньги" : "Money",
  async render(root) {
    const m = await api("/api/money");
    if (!m.ok) { if (!m.silent) root.innerHTML = empty(m.message, "alert"); return; }
    const max = Math.max(1, ...m.days.map(d => d.money));
    const total = m.days.reduce((s, d) => s + d.money, 0);
    const last = m.days[m.days.length - 1].date;
    root.innerHTML = `
      <header class="head"><div><h1>${t("money")}</h1></div></header>
      <div class="money-hero"><div class="big">${money(m.currency, total)}</div><div class="lbl">${t("earned7")}</div></div>
      <h2 class="sec">${t("week")}</h2>
      <ul class="week">${m.days.map(d => `<li class="${d.date === last ? "today" : ""}">
        <span class="d ${d.date === last ? "today" : ""}">${esc(fmtDate(d.date, { withDay: true }).split(" ").slice(0, 2).join(" "))}</span>
        <span class="b"><i style="width:${Math.round(100 * d.money / max)}%"></i></span>
        <span class="v">${money(m.currency, d.money)}<small>${d.count ? pcs(d.count) : ""}</small></span></li>`).join("")}</ul>
      <div class="ledger">
        ${stat(t("toBePaid"), money(m.currency, m.to_be_paid), { kind: "good" })}
        ${stat(t("paid"), money(m.currency, m.paid))}
        ${m.price_pending ? `<p class="muted">${icon("clock")} ${t("pendingN", { n: m.price_pending })}</p>` : ""}
        <p class="muted">${t("moneyNote")}</p>
      </div>`;
  },
});
