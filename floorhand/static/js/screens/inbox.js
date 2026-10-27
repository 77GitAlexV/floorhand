// Office "From the floor": what workers sent that needs a person (features/marking.py
// review): other-work claims to approve or reject with a reason, signals ("out of
// velvet beige", "set the rate"), marks dated late or adjusted, and who has not
// confirmed their day after 16:00. Nothing here pays by itself.
import { registerScreen, addStrings, api, t, esc, money, fmtDate, flash, toast } from "../core.js";
import { btn, empty, sectionTitle, sheet, stat } from "../ui.js";

addStrings({
  en: {
    ibTitle: "From the floor", ibClaims: "Other work to approve", ibSignals: "Signals", ibLate: "Late or adjusted marks",
    ibDay: "Day not confirmed", ibApprove: "Approve", ibReject: "Reject", ibDone: "Done", ibAmount: "Amount",
    ibReason: "Reason the worker will see", ibNeedReason: "Write the reason first.", ibNothing: "Nothing from the floor right now.",
    ibConfirmed: "Confirmed today", ibMarked: "Marked today", ibMin: "{n} min", ibAsked: "asked",
    tq_declared: "declared by worker", tq_adjusted: "adjusted", tq_phone: "phone time",
  },
  ru: {
    ibTitle: "Из цеха", ibClaims: "Другая работа — подтвердить", ibSignals: "Сигналы", ibLate: "Отметки задним числом",
    ibDay: "Не подтвердили день", ibApprove: "Принять", ibReject: "Отклонить", ibDone: "Сделано", ibAmount: "Сумма",
    ibReason: "Причина — её увидит работник", ibNeedReason: "Сначала напиши причину.", ibNothing: "Из цеха сейчас ничего нет.",
    ibConfirmed: "Подтвердили сегодня", ibMarked: "Отмечали сегодня", ibMin: "{n} мин", ibAsked: "просит",
    tq_declared: "со слов работника", tq_adjusted: "поправлено", tq_phone: "время телефона",
  },
});

registerScreen({
  id: "inbox", roles: ["office"], icon: "users", title: lang => lang === "ru" ? "Из цеха" : "From the floor",
  async render(root, ctx) {
    const r = await api("/api/marking/review");
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    const cur = r.currency;
    const dcs = r.day_checks;
    const nothing = !r.claims_pending.length && !r.signals.length && !r.late_marks.length;
    root.innerHTML = `
      <header class="head"><div><h1>${t("ibTitle")}</h1><p class="muted">${esc(fmtDate(r.date, { withDay: true }))}</p></div></header>
      <div class="stats">
        ${stat(t("ibClaims"), r.claims_pending.length, { kind: r.claims_pending.length ? "hot" : "" })}
        ${stat(t("ibSignals"), r.signals.length)}
        ${stat(t("ibMarked"), dcs.marked_today)}
        ${stat(t("ibConfirmed"), dcs.confirmed)}
      </div>
      ${nothing ? empty(t("ibNothing"), "check") : ""}
      ${r.claims_pending.length ? sectionTitle(t("ibClaims"), r.claims_pending.length) + `<ul class="plain">${r.claims_pending.map(c => `
        <li><span><b>${esc(c.person)}</b> · ${esc(fmtDate(c.work_date))}<br>${esc(c.description)}${c.minutes ? ` · ${t("ibMin", { n: c.minutes })}` : ""}${c.qty ? ` · ${c.qty}` : ""}
          ${c.asked_amount != null ? `<br><span class="muted">${t("ibAsked")} ${money(cur, c.asked_amount)}</span>` : ""}</span>
          <span>${btn({ label: t("ibApprove"), kind: "primary", attrs: `data-ok="${c.id}" data-amt="${c.asked_amount ?? ""}"` })}
          ${btn({ label: t("ibReject"), kind: "ghost", attrs: `data-no="${c.id}"` })}</span></li>`).join("")}</ul>` : ""}
      ${r.signals.length ? sectionTitle(t("ibSignals"), r.signals.length) + `<ul class="plain">${r.signals.map(s => `
        <li><span>${esc(s.text)}${s.person ? ` <span class="muted">· ${esc(s.person)}</span>` : ""}</span>
          ${btn({ label: t("ibDone"), kind: "secondary", attrs: `data-close="${s.id}"` })}</li>`).join("")}</ul>` : ""}
      ${r.late_marks.length ? sectionTitle(t("ibLate"), r.late_marks.length) + `<ul class="plain">${r.late_marks.map(m => `
        <li><span><b>${esc(m.unit)}</b> · ${esc(m.stage)} · ${esc(m.person)}<br><span class="muted">${esc(fmtDate(m.work_date))} · ${t("tq_" + m.time_quality)}${m.note ? ` — ${esc(m.note)}` : ""}</span></span></li>`).join("")}</ul>` : ""}
      ${dcs.due && dcs.not_confirmed.length ? sectionTitle(t("ibDay"), dcs.not_confirmed.length) + `<p>${dcs.not_confirmed.map(esc).join(", ")}</p>` : ""}`;

    root.querySelectorAll("[data-ok]").forEach(b => b.onclick = async () => {
      let amount = b.dataset.amt;
      const p = sheet({ title: t("ibApprove"), body: `<label class="muted">${t("ibAmount")} (${esc(cur)})</label>
        <input id="ibAmt" type="number" min="0" value="${esc(amount)}" style="width:100%;font-size:22px;min-height:52px">`,
        actions: [{ id: "yes", label: t("ibApprove"), kind: "primary" }, { id: "no", label: t("back") }] });
      document.getElementById("ibAmt")?.addEventListener("input", e => { amount = e.target.value; });
      if (await p !== "yes") return;
      const res = await api(`/api/marking/claims/${b.dataset.ok}/decide`, { approve: true, amount: amount === "" ? null : Number(amount), note: "" });
      if (res.silent) return;
      if (res.ok) toast(res.message); else flash(false, res.message);
      ctx.refresh();
    });
    root.querySelectorAll("[data-no]").forEach(b => b.onclick = async () => {
      let note = "";
      const p = sheet({ title: t("ibReject"), body: `<label class="muted">${t("ibReason")}</label>
        <input id="ibNote" autocomplete="off" style="width:100%;font-size:18px;min-height:48px">`,
        actions: [{ id: "yes", label: t("ibReject"), kind: "danger" }, { id: "no", label: t("back") }] });
      document.getElementById("ibNote")?.addEventListener("input", e => { note = e.target.value; });
      if (await p !== "yes") return;
      if (!note.trim()) { flash(false, t("ibNeedReason")); return; }
      const res = await api(`/api/marking/claims/${b.dataset.no}/decide`, { approve: false, note });
      if (res.silent) return;
      if (res.ok) toast(res.message); else flash(false, res.message);
      ctx.refresh();
    });
    root.querySelectorAll("[data-close]").forEach(b => b.onclick = async () => {
      const res = await api(`/api/marking/signals/${b.dataset.close}/close`, {});
      if (res.silent) return;
      if (res.ok) toast(res.message || t("ibDone")); else flash(false, res.message);
      ctx.refresh();
    });
  },
});
