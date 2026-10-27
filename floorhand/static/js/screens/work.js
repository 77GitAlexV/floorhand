// Worker "Work": one big button on the work handed to you; money for today on
// the same screen; free work to take; done today with a visible undo.
import { registerScreen, addStrings, api, t, esc, name, state, money, fmtDate, pcs, flash, toast, idem } from "../core.js";
import { icon, btn, sheet, empty, bindSoon } from "../ui.js";
import * as M from "../marking.js";

addStrings({
  en: {
    work: "Work", assigned: "Assigned to me", free: "Free at {stage}", doneToday: "Done today",
    done1: "DONE 1 PC", take: "Take 1", doneHere: "{a} of {b} done", perPc: "/ pc",
    pricePending: "price pending", undo: "Made a mistake — undo",
    undoQ: "Undo {unit}?", undoSub: "The record stays in the log as undone. Money for it is removed.",
    scan: "Scan sheet", speak: "Speak", nothingAssigned: "Nothing assigned to you. Take work from the free list below.",
    nothingFree: "Nothing free at your stage right now.", nothingDone: "Nothing recorded yet today.",
    recorded: "Recorded", already: "Already recorded", undone: "Undone", evening: "Recorded today: {n} pcs. All correct?",
    eveningYes: "Yes", eveningAdd: "Add other work", taken: "Taken: {n}", waiting: "{n} waiting",
    markedBy: "Marking:",
  },
  ru: {
    work: "Работа", assigned: "Мне выдано", free: "Свободное на {stage}", doneToday: "Сегодня сделал",
    done1: "ГОТОВО 1 ШТ", take: "Взять 1", doneHere: "готово {a} из {b}", perPc: "/ шт",
    pricePending: "цена уточняется", undo: "Ошибся — отменить",
    undoQ: "Отменить {unit}?", undoSub: "Запись останется в журнале как отменённая. Деньги за неё снимутся.",
    scan: "Скан листка", speak: "Сказать", nothingAssigned: "Тебе ничего не выдано. Возьми работу из свободного ниже.",
    nothingFree: "Свободной работы на твоём этапе нет.", nothingDone: "Сегодня ещё ничего не записано.",
    recorded: "Записал", already: "Уже записано", undone: "Отменено", evening: "Сегодня записано {n} шт. Всё так?",
    eveningYes: "Да", eveningAdd: "Добавить другое", taken: "Взято: {n}", waiting: "{n} ждёт",
    markedBy: "Отмечает:",
  },
});

function card(o, w, kind) {
  const rate = !o.rate_set ? `<span class="pill warn">${t("pricePending")}</span>`
    : (o.rate !== null ? `<b>${money(w.currency, o.rate)}</b> <span class="muted">${t("perPc")}</span>` : "");
  const late = o.customer && o.due_date && o.due_date < w.date;
  const where = o.customer ? `${esc(o.customer)} · <span class="${late ? "late" : ""}">${t("due")} ${fmtDate(o.due_date)}</span>` : t("toStock");
  return `<article class="card sheet-edge" data-card="${o.order_id}">
    <div class="c-title"><span class="c-num">#${o.number}</span> · ${esc(o.product)}${o.qty > 1 ? ` <span class="muted">× ${o.qty}</span>` : ""}</div>
    <div class="c-sub">${esc(o.variant)}</div>
    <div class="c-meta">${where}</div>
    <hr class="c-rule">
    <div class="c-foot"><span>${t("doneHere", { a: o.done_here, b: o.qty })}${o.qty > 1 ? ` · ${t("waiting", { n: o.waiting })}` : ""}</span><span>${rate}</span></div>
    ${kind === "mine"
      ? `<button type="button" class="btn hero" data-done="${o.order_id}">${icon("plus")}<span>${t("done1")}</span></button>`
      : btn({ label: t("take"), icon: "plus", kind: "secondary", size: "lg", cls: "wide", attrs: `data-take="${o.order_id}" style="margin-top:12px"` })}
  </article>`;
}

registerScreen({
  id: "work", roles: ["worker", "tablet"], icon: "work", title: lang => lang === "ru" ? "Работа" : "Work",
  async render(root, ctx) {
    const w = await api("/api/work");
    if (!w.ok) { if (!w.silent) root.innerHTML = empty(w.message, "alert"); return; }
    const todayLine = w.show_money && w.today.money !== null
      ? `<span class="big">${pcs(w.today.count)} · ${money(w.currency, w.today.money)}</span>`
      : `<span class="big">${pcs(w.today.count)}</span>`;
    const bump = sessionStorage.getItem("fh_bump") === "1";
    if (bump) sessionStorage.removeItem("fh_bump");
    // End-of-day check from the server (shop time, 16:00), not from the phone clock.
    const dc = await M.dayCheck();
    const evening = !!dc;
    root.innerHTML = `
      <header class="work-head"><h1>${esc(name(w.stage))}</h1><div class="date">${esc(fmtDate(w.date, { withDay: true }))}<br><span class="muted">${esc(w.person.name)}</span></div></header>
      <div class="work-today"><span class="lbl">${t("today")}</span>${bump ? todayLine.replace('class="big"', 'class="big bump"') : todayLine}${w.today.price_pending ? `<span class="pend">· ${w.today.price_pending} ${t("pricePending")}</span>` : ""}</div>
      ${evening ? `<div class="evening">${icon("clock")}<span class="grow">${esc(dc.message)}</span>
        ${btn({ label: t("eveningYes"), kind: "primary", attrs: 'id="evYes"' })}${btn({ label: t("mkAddMissing"), kind: "secondary", attrs: 'id="evAdd"' })}</div>` : ""}
      <h2 class="sec">${t("assigned")} <b>${w.mine.length}</b></h2>
      ${w.mine.length ? w.mine.map(o => card(o, w, "mine")).join("") : empty(t("nothingAssigned"), "work")}
      <details class="disc" ${w.mine.length ? "" : "open"}><summary>${t("free", { stage: name(w.stage) })} <span class="count">${w.free.length}</span>${icon("chevron", "chev")}</summary>
        <div class="disc-body">${w.free.length ? w.free.map(o => card(o, w, "free")).join("") : `<p class="muted">${t("nothingFree")}</p>`}</div>
      </details>
      <details class="disc" id="doneDisc"><summary>${t("doneToday")} <span class="count">${w.done_today.length}</span>${icon("chevron", "chev")}</summary>
        <div class="disc-body">${w.done_today.length ? `<ul class="done-list">${w.done_today.map(d => `<li>
          <span class="stamp">${icon("check")}</span><span class="time">${esc(d.at)}</span><span class="unit">${esc(d.unit)}</span>
          ${d.amount != null ? `<span class="amt">${money(w.currency, d.amount)}</span>` : `<span class="amt muted"></span>`}
          <button type="button" class="link bad" data-undo="${d.event_id}" data-unit="${esc(d.unit)}">${t("undo")}</button></li>`).join("")}</ul>` : `<p class="muted">${t("nothingDone")}</p>`}</div>
      </details>
      <div id="mkQueue"></div>
      <div class="row-actions">
        <a class="btn secondary" href="#/handsfree">${icon("mic")}${state.lang === "ru" ? "Руки в клею" : "Glue mode"}</a>
        ${btn({ label: t("eveningAdd"), icon: "plus", kind: "ghost", attrs: 'id="mkOther"' })}
        ${btn({ label: t("mkAddMissing"), icon: "clock", kind: "ghost", attrs: 'id="mkMissing"' })}
        ${btn({ label: t("mkPhoto"), icon: "camera", kind: "ghost", attrs: 'id="mkPhoto"' })}
        ${btn({ label: t("mkAudio"), icon: "mic", kind: "ghost", attrs: 'id="mkAudio"' })}
      </div>
      <div class="dock"><div class="dock-inner">
        ${btn({ label: t("scan"), icon: "scan", kind: "secondary", attrs: 'id="mkScan"' })}
        ${btn({ label: t("speak"), icon: "mic", kind: "secondary", attrs: 'id="mkSpeak"' })}
      </div></div>`;

    root.querySelectorAll("[data-done]").forEach(b => b.onclick = async () => {
      b.disabled = true; b.classList.add("busy");
      const key = idem();
      const r = await api("/api/work/done", { order_id: Number(b.dataset.done), idem_key: key });
      if (r.silent) return;
      if (!r.ok && r.offline && window.FloorhandQueue) {
        // No network: keep the tap on the phone with its own key and time; it sends itself.
        const item = { order_id: Number(b.dataset.done), idem_key: key, method: "button" };
        if (state.me && state.me.tablet) item.person_id = state.me.id;
        window.FloorhandQueue.add(item);
        toast(t("mkQueued")); b.disabled = false; b.classList.remove("busy"); return;
      }
      if (!r.ok) { flash(false, r.message); b.disabled = false; b.classList.remove("busy"); return; }
      const stage = name(w.stage);
      const plus = r.amount != null ? money(w.currency, r.amount) : (w.show_money ? t("pricePending") : "");
      const count = w.today.count + (r.already ? 0 : 1);
      const sum = w.show_money && w.today.money !== null ? w.today.money + (r.amount || 0) : null;
      flash(true, `${r.unit_label} · ${stage}`, {
        title: r.already ? t("already") : t("recorded"), already: r.already,
        sub: sum !== null ? `${t("today")}: ${pcs(count)} · ${money(w.currency, sum)}` : `${t("today")}: ${pcs(count)}`,
        big: r.already ? "" : plus ? `+${plus}` : "",
      });
      if (!r.already) sessionStorage.setItem("fh_bump", "1");
      ctx.refresh();
    });
    root.querySelectorAll("[data-take]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      const r = await api("/api/work/take", { order_id: Number(b.dataset.take), n: 1 });
      if (r.silent) return;
      if (!r.ok) { flash(false, r.message); b.disabled = false; return; }
      toast(t("taken", { n: r.taken }));
      ctx.refresh();
    });
    root.querySelectorAll("[data-undo]").forEach(b => b.onclick = async () => {
      const yes = await sheet({
        title: t("undoQ", { unit: b.dataset.unit }), body: t("undoSub"), tone: "bad",
        actions: [{ id: "yes", label: t("undo"), kind: "danger", icon: "undo" }, { id: "no", label: t("back") }],
      });
      if (yes !== "yes") return;
      const r = await api("/api/work/undo", { event_id: Number(b.dataset.undo) });
      if (r.silent) return;
      flash(r.ok, r.ok ? `${t("undone")}: ${r.unit_label}` : r.message);
      ctx.refresh();
    });
    root.querySelector("#evYes")?.addEventListener("click", () => M.confirmDay(ctx, dc.date));
    root.querySelector("#evAdd")?.addEventListener("click", () => M.addMissing(ctx, dc.can_add_back_days));
    root.querySelector("#mkMissing")?.addEventListener("click", () => M.addMissing(ctx));
    root.querySelector("#mkOther")?.addEventListener("click", () => M.otherWork(ctx));
    root.querySelector("#mkPhoto")?.addEventListener("click", () => M.photo(ctx));
    root.querySelector("#mkAudio")?.addEventListener("click", () => M.audio(ctx));
    root.querySelector("#mkScan")?.addEventListener("click", () => M.scanSheet(ctx));
    root.querySelector("#mkSpeak")?.addEventListener("click", () => M.speak(ctx));
    M.queueBar(root.querySelector("#mkQueue"));
    // A hardware scanner types fast and ends with Enter: catch it anywhere on this screen.
    let buf = "", last = 0;
    onkeydown = e => {
      if (state.screen !== "work" || document.getElementById("sheet").hidden === false) return;
      const now = Date.now();
      if (now - last > 80) buf = "";
      last = now;
      if (e.key === "Enter" && buf.length >= 3) { const c = buf; buf = ""; M.scanSheet(ctx, c); }
      else if (e.key.length === 1) buf += e.key;
    };
    const pending = M.takePendingCode();
    if (pending) M.scanSheet(ctx, pending);
    bindSoon(root, toast);
  },
});
