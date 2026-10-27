// Owner "Floor": the five stages as a pipeline. Queue, done today against
// capacity, who did how much; exactly one bottleneck highlighted with its reason.
import { registerScreen, addStrings, api, t, esc, name, fmtDate } from "../core.js";
import { icon, stat, bar, empty } from "../ui.js";

addStrings({
  en: {
    floor: "Floor", queue: "waiting", doneOf: "{a} done / {b} daily capacity", bottleneck: "Bottleneck",
    whyHot: "{q} waiting at {c} a day — about {d} days of work", inQueue: "In queues", doneToday: "Done today",
    capacity: "Capacity / day", noPeople: "Nobody on this stage", todayN: "today", capNote: "Capacity is the planning value per working day set for each stage.",
    handOut: "Hand out work", flowTitle: "Queue by stage",
  },
  ru: {
    floor: "Цех", queue: "в очереди", doneOf: "{a} сделано / {b} в день", bottleneck: "Узкое место",
    whyHot: "{q} в очереди при {c} в день — около {d} дн. работы", inQueue: "В очередях", doneToday: "Сделано сегодня",
    capacity: "Мощность / день", noPeople: "На этапе никого", todayN: "сегодня", capNote: "Мощность — плановая цифра на рабочий день для каждого этапа.",
    handOut: "Раздать работу", flowTitle: "Очередь по этапам",
  },
});

registerScreen({
  id: "floor", roles: ["owner"], icon: "floor", title: lang => lang === "ru" ? "Цех" : "Floor",
  async render(root) {
    const r = await api("/api/floor");
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    const st = r.list;
    const totals = st.reduce((a, s) => ({ q: a.q + s.queue, d: a.d + s.done_today, c: a.c + s.capacity }), { q: 0, d: 0, c: 0 });
    const maxQ = Math.max(1, ...st.map(s => s.queue));
    const hot = st.find(s => s.bottleneck);
    root.innerHTML = `
      <header class="head"><div><h1>${t("floor")}</h1><p class="sub">${esc(fmtDate(new Date().toISOString(), { withDay: true }))}</p></div></header>
      <div class="stats">
        ${stat(t("inQueue"), totals.q)}
        ${stat(t("doneToday"), totals.d, { sub: `${t("capacity")}: ${totals.c}` })}
        ${hot ? stat(t("bottleneck"), esc(name(hot)), { kind: "hot", sub: t("whyHot", { q: hot.queue, c: hot.capacity, d: Math.ceil(hot.queue / Math.max(hot.capacity, 1)) }) }) : ""}
      </div>
      <div class="pipeline">${st.map(s => `<section class="stage-col ${s.bottleneck ? "hot" : ""}">
        ${icon("chevron", "arrow")}
        <h3>${esc(name(s))}${s.bottleneck ? `<span class="hot-tag">${icon("flame")}${t("bottleneck")}</span>` : ""}</h3>
        <div class="queue"><b>${s.queue}</b><span>${t("queue")}</span></div>
        <div class="qbar" title="${t("flowTitle")}"><i style="width:${Math.round(100 * s.queue / maxQ)}%"></i></div>
        <div class="cap"><span>${t("doneOf", { a: s.done_today, b: s.capacity })}</span>${bar(s.done_today / Math.max(s.capacity, 1), s.bottleneck ? "hot" : "")}</div>
        ${s.bottleneck ? `<p class="why">${t("whyHot", { q: s.queue, c: s.capacity, d: Math.ceil(s.queue / Math.max(s.capacity, 1)) })}</p>` : ""}
        <ul class="people">${s.people.length ? s.people.map(p => `<li class="${p.today ? "" : "idle"}"><span>${esc(p.name)}</span><b>${p.today}</b></li>`).join("") : `<li class="idle">${t("noPeople")}</li>`}</ul>
      </section>`).join("")}</div>
      <p class="muted" style="margin-top:16px">${t("capNote")}</p>`;
  },
});
