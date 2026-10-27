// Owner "Today": the agentic core (features/owner.py, planner.py, materials.py, ask.py).
// A feed of decisions. Every card is a stored proposal; the buttons send only its id
// (plus what was kept or picked from what was offered). Nothing is preselected;
// "Why?" shows the numbers tagged FACT / CALC / ESTIMATE. A new kind of advice runs
// in the shadow first and only works after its hit rate is proven AND switched on.
import { registerScreen, addStrings, api, t, esc, name, state, fmtDate, flash, toast } from "../core.js";
import { icon, stat, btn, empty } from "../ui.js";

addStrings({
  en: {
    todayTitle: "Today", openOrders: "Open orders", readyShip: "Ready to ship", lateOrders: "Late",
    shortMat: "Short materials", doneCap: "Done today / capacity", bottleneckIs: "Bottleneck", noBottleneck: "No bottleneck",
    morning: "Morning summary", decisions: "Decisions waiting for you", noDecisions: "Nothing to decide right now. The floor runs by itself.",
    yesDo: "Yes", change: "Change", noSkip: "No", why: "Why?", applyChanges: "Apply what is checked", cancelW: "Cancel",
    reasonHint: "Why not? (optional, it is remembered)", recordNo: "Record: no", pickOne: "Pick one of the offered options.",
    k_plan: "Plan", k_shortage: "Material", k_late_risk: "Late orders", k_idle_capacity: "Work for stock",
    forDay: "for", shadow: "Learning in the shadow",
    shadowHint: "{hits} of {total} matched what the floor did · needs {n} scored at {rate}% before Yes works",
    shadowReady: "Hit rate proven: {rate}% over {n}", switchOn: "Switch this advice on",
    acceptedW: "accepted", rejectedW: "declined", followed: "plan followed by the floor",
    explainedBy: "Explained by", computedBy: "Numbers by", decidedToday: "Decided today",
    d_applied: "yes", d_rejected: "no", onHands: "on hands", lateByD: "late {n} d",
    ask: "Ask", askHint: "Ask in words: money, debts, late orders, materials, an order number…",
    asking: "Asking…", answeredBy: "Answered by", tryAsk: "Try:",
    outboxN: "{n} draft(s) in the outbox; nothing was sent (dry run).",
  },
  ru: {
    todayTitle: "Сегодня", openOrders: "Открытых заказов", readyShip: "Готово к отгрузке", lateOrders: "Опаздывают",
    shortMat: "Не хватает материалов", doneCap: "Сделано / мощность", bottleneckIs: "Узкое место", noBottleneck: "Узкого места нет",
    morning: "Утренняя сводка", decisions: "Решения ждут тебя", noDecisions: "Решать пока нечего. Цех идёт сам.",
    yesDo: "Да", change: "Изменить", noSkip: "Нет", why: "Почему?", applyChanges: "Применить отмеченное", cancelW: "Отмена",
    reasonHint: "Почему нет? (не обязательно, запомним)", recordNo: "Записать: нет", pickOne: "Выбери один из предложенных вариантов.",
    k_plan: "План", k_shortage: "Материал", k_late_risk: "Опоздания", k_idle_capacity: "Работа на склад",
    forDay: "на", shadow: "Учится в тени",
    shadowHint: "{hits} из {total} совпали с тем, что сделал цех · нужно {n} оценённых при {rate}%, чтобы «Да» заработало",
    shadowReady: "Точность доказана: {rate}% на {n}", switchOn: "Включить этот совет",
    acceptedW: "принято", rejectedW: "отклонено", followed: "план совпал с цехом",
    explainedBy: "Объяснил", computedBy: "Цифры", decidedToday: "Решено сегодня",
    d_applied: "да", d_rejected: "нет", onHands: "на руках", lateByD: "опоздание {n} дн",
    ask: "Спросить", askHint: "Спроси словами: деньги, долги, опоздания, материалы, номер заказа…",
    asking: "Спрашиваю…", answeredBy: "Ответил", tryAsk: "Попробуй:",
    outboxN: "В исходящих черновиков: {n}; ничего не отправлено (тренировка).",
  },
});

// Server texts come in both languages: {en, ru}.
const tl = o => o && typeof o === "object" ? (o[state.lang] || o.en || "") : (o ?? "");
const TAGS = { FACT: "fact", CALC: "calc", ESTIMATE: "est" };
const tag = k => `<i class="tag ${String(k).toLowerCase()}">${esc(t(TAGS[k] || k))}</i>`;
let askText = "";

function kindStats(c) {
  const st = c.stats || {};
  if (c.shadow) {
    return `<span class="shadow">${t("shadow")} · ${st.ready
      ? t("shadowReady", { rate: Math.round(st.rate * 100), n: st.scored })
      : t("shadowHint", { hits: st.hits, total: st.total, n: st.min_scored, rate: Math.round(st.min_rate * 100) })}</span>
      ${st.ready ? btn({ label: t("switchOn"), kind: "secondary", attrs: `data-enable="${c.kind}"` }) : ""}`;
  }
  const parts = [`${st.accepted ?? 0} ${t("acceptedW")}`, `${st.rejected ?? 0} ${t("rejectedW")}`];
  if (st.rate !== null && st.rate !== undefined) parts.push(`${Math.round(st.rate * 100)}% ${t("followed")}`);
  return `<span class="muted">${parts.join(" · ")}</span>`;
}

function decisionCard(c) {
  const hot = c.kind === "late_risk" || c.kind === "shortage";
  const ic = { shortage: "box", late_risk: "clock", plan: "layers" }[c.kind] || "spark";
  return `<article class="decision k-${c.kind} ${hot ? "hot" : ""} ${c.shadow ? "shadowed" : ""}" data-card="${c.id}">
    <span class="d-kind">${icon(ic)}${t("k_" + c.kind)} · ${t("forDay")} ${esc(fmtDate(c.target_date, { withDay: true }))}</span>
    <div class="d-stats">${kindStats(c)}</div>
    <h3>${esc(tl(c.headline))}</h3>
    <ul class="nums">${(c.numbers || []).map(n => `<li><b>${esc(tl(n.value))}</b><span>${esc(tl(n.label))} ${tag(n.tag)}</span></li>`).join("")}</ul>
    <div class="d-acts">
      ${btn({ label: esc(tl(c.apply_label) || t("yesDo")), icon: "check", kind: "primary", attrs: `data-yes="${c.id}"`, disabled: c.shadow })}
      ${btn({ label: t("change"), icon: "edit", attrs: `data-change="${c.id}"`, disabled: c.shadow })}
      ${btn({ label: t("noSkip"), icon: "x", attrs: `data-no="${c.id}"` })}
      ${btn({ label: t("why"), icon: "question", kind: "ghost", attrs: `data-why="${c.id}"` })}
    </div>
    <div class="d-more why" hidden></div>
    <div class="d-more change" hidden></div>
    <div class="d-more noform" hidden>
      <input class="input" type="text" placeholder="${t("reasonHint")}" data-reason="${c.id}">
      <div class="d-acts">${btn({ label: t("recordNo"), kind: "danger", attrs: `data-noyes="${c.id}"` })}${btn({ label: t("cancelW"), kind: "ghost", attrs: `data-cancel="${c.id}"` })}</div>
    </div>
  </article>`;
}

function changeForm(c) {
  if (c.kind === "plan") {
    const byStage = {};
    c.lines.forEach(l => (byStage[l.stage_id] = byStage[l.stage_id] || []).push(l));
    const stage = id => c.stages.find(s => s.stage_id === id) || { workers: [] };
    return Object.entries(byStage).map(([sid, lines]) => `<h4>${esc(name(stage(Number(sid))))} · ${lines.length}</h4>
      <ul class="lines">${lines.map(l => `<li class="${l.risk !== "ok" ? "risky" : ""}">
        <label><input type="checkbox" checked data-keep="${l.unit_id}"> <span><b>${esc(l.label)}</b> ${esc(l.product)}
          <small>${esc(l.variant)} · ${l.customer ? esc(l.customer) + " · " + esc(fmtDate(l.due)) : t("toStock")}${l.late_days ? ` · <span class="late">${t("lateByD", { n: l.late_days })}</span>` : ""}</small></span></label>
        <select class="input" data-move="${l.unit_id}">${stage(Number(sid)).workers.map(p =>
          `<option value="${p.id}" ${p.id === l.person_id ? "selected" : ""}>${esc(p.name)} · ${p.on_hands} ${t("onHands")}</option>`).join("")}</select>
      </li>`).join("")}</ul>`).join("");
  }
  if (c.kind === "shortage") {
    return `<ul class="lines">${c.options.map(o => `<li><label><input type="radio" name="pick-${c.id}" value="${o.id}"> <span>${esc(tl(o.label))}</span></label></li>`).join("")}</ul>
      <p class="muted">${c.lines.map(l => `#${l.order} ${l.qty}`).join(" · ")}</p>`;
  }
  if (c.kind === "late_risk") {
    return `<ul class="lines">${c.lines.map(o => `<li class="${o.risk === "late" ? "risky" : ""}"><label>
      <input type="checkbox" checked data-keep="${o.number}" ${o.customer ? "" : "disabled"}> <span><b>#${o.number}</b> ${esc(o.product)}
      <small>${esc(o.customer || t("toStock"))} · ${t("due")} ${esc(fmtDate(o.due))} → ${esc(fmtDate(o.estimate))} ${tag("ESTIMATE")}${o.late_days ? ` · <span class="late">${t("lateByD", { n: o.late_days })}</span>` : ""}</small></span></label></li>`).join("")}</ul>`;
  }
  return `<ul class="lines">${c.lines.map(l => `<li><label><input type="checkbox" checked data-keep="${l.product_id}"> <span><b>${esc(l.product)}</b> × ${l.qty}
    <small>${l.sold_30d} / 30 d · ${l.cover_days ?? "—"} d</small></span></label></li>`).join("")}</ul>`;
}

registerScreen({
  id: "today", roles: ["owner"], icon: "today", title: lang => lang === "ru" ? "Сегодня" : "Today",
  async render(root, ctx) {
    const [f, m] = await Promise.all([api("/api/owner/today"), api(`/api/owner/morning?lang=${state.lang}`)]);
    if (!f.ok) { if (!f.silent) root.innerHTML = empty(f.message, "alert"); return; }
    const s = f.summary;
    const bn = s.bottleneck;
    root.innerHTML = `
      <header class="head"><div><h1>${t("todayTitle")}</h1><p class="sub">${esc(fmtDate(f.date, { withDay: true }))}</p></div></header>
      <div class="stats">
        ${stat(t("openOrders"), s.open_orders)}
        ${stat(t("readyShip"), s.ready, { kind: s.ready ? "good" : "" })}
        ${stat(t("lateOrders"), s.late, { kind: s.late ? "bad" : "" })}
        ${stat(t("shortMat"), s.shortages, { kind: s.shortages ? "bad" : "" })}
        ${stat(t("doneCap"), `${s.done_today}<small class="muted"> / ${s.capacity}</small>`)}
        ${stat(bn ? t("bottleneckIs") : t("noBottleneck"), bn ? esc(name(bn)) : "—", { kind: bn ? "hot" : "", sub: bn ? `${bn.queue} / ${bn.capacity}` : "" })}
      </div>
      ${m.ok ? `<details class="disc morning"><summary>${t("morning")} <span class="muted">· ${esc(m.head)}</span>${icon("chevron", "chev")}</summary>
        <div class="disc-body"><ul class="plain">${m.lines.map(l => `<li>${esc(l)}</li>`).join("")}</ul></div></details>` : ""}
      <h2 class="sec">${t("decisions")} <b>${f.cards.length}</b></h2>
      <div class="feed">${f.cards.length ? f.cards.map(decisionCard).join("") : empty(t("noDecisions"), "check")}</div>
      ${f.outbox ? `<p class="muted">${t("outboxN", { n: f.outbox })}</p>` : ""}
      <h2 class="sec">${t("ask")}</h2>
      <section class="panel askbox">
        <div class="ask" style="margin-top:0"><input class="input" id="askText" placeholder="${t("askHint")}" value="${esc(askText)}">
          ${btn({ label: t("ask"), icon: "spark", kind: "primary", attrs: 'id="askGo"' })}</div>
        <p class="muted chips ask-ex">${t("tryAsk")} ${(f.examples || []).map(e => `<button type="button" class="chip" data-ask="${esc(tl(e))}">${esc(tl(e))}</button>`).join(" ")}</p>
        <div id="askOut"></div>
      </section>
      ${f.decided.length ? `<h2 class="sec">${t("decidedToday")} <b>${f.decided.length}</b></h2>
        <ul class="plain decided">${f.decided.map(c => `<li><span><b class="st-${c.status}">${t("d_" + c.status)}</b> ${esc(tl(c.headline))}
          ${c.result && c.result.reason ? `<small class="muted">— ${esc(c.result.reason)}</small>` : ""}</span></li>`).join("")}</ul>` : ""}`;
    bindCards(root, f, ctx);
    bindAsk(root);
  },
});

function bindCards(root, f, ctx) {
  const byId = Object.fromEntries(f.cards.map(c => [c.id, c]));
  const box = id => root.querySelector(`[data-card="${id}"]`);
  const decide = async (id, body) => {
    const r = await api(`/api/owner/cards/${id}/apply`, body);
    if (r.silent) return;
    flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  };
  root.querySelectorAll("[data-yes]").forEach(b => b.onclick = () => { b.disabled = true; decide(Number(b.dataset.yes), {}); });
  root.querySelectorAll("[data-change]").forEach(b => b.onclick = () => {
    const c = byId[Number(b.dataset.change)];
    const el = box(c.id).querySelector(".change");
    el.hidden = !el.hidden;
    if (el.hidden) return;
    el.innerHTML = changeForm(c) + `<div class="d-acts">${btn({ label: t("applyChanges"), kind: "primary", attrs: `data-apply="${c.id}"` })}
      ${btn({ label: t("cancelW"), kind: "ghost", attrs: "data-close" })}</div>`;
    el.querySelector("[data-apply]").onclick = () => {
      const body = {};
      if (el.querySelector("[data-keep]")) body.keep = [...el.querySelectorAll("[data-keep]:checked")].map(x => Number(x.dataset.keep));
      const moves = {};
      el.querySelectorAll("[data-move]").forEach(sel => { moves[sel.dataset.move] = Number(sel.value); });
      if (Object.keys(moves).length) body.moves = moves;
      const pick = el.querySelector("input[type=radio]:checked");
      if (c.kind === "shortage" && !pick) { toast(t("pickOne")); return; }
      if (pick) body.pick = Number(pick.value);
      decide(c.id, body);
    };
    el.querySelector("[data-close]").onclick = () => { el.hidden = true; };
  });
  root.querySelectorAll("[data-no]").forEach(b => b.onclick = () => {
    const el = box(Number(b.dataset.no)).querySelector(".noform");
    el.hidden = !el.hidden;
    if (!el.hidden) el.querySelector("input").focus();
  });
  root.querySelectorAll("[data-noyes]").forEach(b => b.onclick = async () => {
    const id = Number(b.dataset.noyes);
    const r = await api(`/api/owner/cards/${id}/reject`, { reason: box(id).querySelector("[data-reason]").value });
    if (r.silent) return;
    flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  });
  root.querySelectorAll(".noform [data-cancel]").forEach(b => b.onclick = () => {
    box(Number(b.dataset.cancel)).querySelector(".noform").hidden = true;
  });
  root.querySelectorAll("[data-why]").forEach(b => b.onclick = async () => {
    const id = Number(b.dataset.why);
    const el = box(id).querySelector(".why");
    if (!el.hidden) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<p class="muted">${t("loading")}</p>`;
    const r = await api(`/api/owner/cards/${id}/why?lang=${state.lang}`);
    if (!r.ok) { el.innerHTML = `<p class="late">${esc(r.message || "")}</p>`; return; }
    el.innerHTML = `<p>${esc(r.text)}</p><ul class="facts">${r.lines.map(l => `<li>${tag(l.tag)} ${esc(tl(l))}</li>`).join("")}</ul>
      <p class="muted">${t("computedBy")}: planner rules · ${t("explainedBy")}: ${esc(r.model)}</p>`;
  });
  root.querySelectorAll("[data-enable]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/owner/advice/${b.dataset.enable}/enable`, {});
    if (r.silent) return;
    flash(r.ok, r.message);
    if (r.ok) ctx.refresh();
  });
}

function bindAsk(root) {
  const input = root.querySelector("#askText");
  const out = root.querySelector("#askOut");
  const goBtn = root.querySelector("#askGo");
  const run = async text => {
    if (!text.trim()) return;
    askText = text;
    goBtn.disabled = true; goBtn.querySelector("span").textContent = t("asking");
    const r = await api("/api/owner/ask", { text, lang: state.lang });
    goBtn.disabled = false; goBtn.querySelector("span").textContent = t("ask");
    if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
    out.innerHTML = `<div class="answer">
      ${r.answer ? `<p>${esc(r.answer)}</p>` : ""}
      ${r.lines.length ? `<ul class="facts">${r.lines.map(l => `<li>${tag(l.tag)} ${esc(tl(l))}</li>`).join("")}</ul>` : ""}
      ${r.examples ? `<p class="chips">${r.examples.map(e => `<button type="button" class="chip" data-ask="${esc(tl(e))}">${esc(tl(e))}</button>`).join(" ")}</p>` : ""}
      <p class="muted">${t("answeredBy")}: ${esc(r.model)}${r.tools.length ? ` · ${esc(r.tools.join(", "))}` : ""}</p></div>`;
    out.querySelectorAll("[data-ask]").forEach(b => b.onclick = () => { input.value = b.dataset.ask; run(b.dataset.ask); });
  };
  goBtn.onclick = () => run(input.value);
  input.onkeydown = e => { if (e.key === "Enter") run(input.value); };
  root.querySelectorAll(".ask-ex [data-ask]").forEach(b => b.onclick = () => { input.value = b.dataset.ask; run(b.dataset.ask); });
}
