// Owner "Today": headline numbers now, and the feed of agent decisions.
// The decision backend is being built in parallel: this screen reads
// /api/today when it exists and shows an honest placeholder until then.
import { registerScreen, addStrings, api, t, esc, name, fmtDate, toast, go } from "../core.js";
import { icon, stat, btn, bindSoon } from "../ui.js";

addStrings({
  en: {
    todayTitle: "Today", openOrders: "Open orders", readyToShip: "Ready to ship", lateOrders: "Late",
    feedTitle: "Decisions waiting for you", feedEmpty: "The morning plan and alerts will appear here as cards: what to do, why, and Yes / Change / No. Nothing is chosen for you in advance.",
    askPlaceholder: "Ask: how much did we earn in October?", ask: "Ask", plan: "Plan for tomorrow", why: "Why?",
    change: "Change", yesDo: "Yes", noSkip: "No", agentSoon: "Agent decisions are coming in the next build.",
  },
  ru: {
    todayTitle: "Сегодня", openOrders: "Открытых заказов", readyToShip: "Готово к отгрузке", lateOrders: "Опаздывают",
    feedTitle: "Решения ждут тебя", feedEmpty: "Утренний план и тревоги появятся здесь карточками: что сделать, почему, и Да / Изменить / Нет. Ничего не выбрано заранее.",
    askPlaceholder: "Спроси: сколько заработали в октябре?", ask: "Спросить", plan: "План на завтра", why: "Почему?",
    change: "Изменить", yesDo: "Да", noSkip: "Нет", agentSoon: "Решения агента — в следующей сборке.",
  },
});

function decisionCard(d) {
  // Shape expected from /api/today (built by another module): {id, kind, title, why, facts:[{label,value,mark}], hot}
  return `<article class="decision ${d.hot ? "hot" : ""}">
    <span class="d-kind">${icon("spark")}${esc(d.kind || "")}</span>
    <h3>${esc(d.title)}</h3>
    ${d.why ? `<p class="d-why">${esc(d.why)}</p>` : ""}
    ${(d.facts || []).length ? `<p class="d-why">${d.facts.map(f => `${esc(f.label)}: <b>${esc(f.value)}</b> <span class="tag">${esc(f.mark || "")}</span>`).join(" · ")}</p>` : ""}
    <div class="d-acts">
      ${btn({ label: t("yesDo"), icon: "check", kind: "primary", attrs: `data-decide="${d.id}" data-a="yes"` })}
      ${btn({ label: t("change"), icon: "edit", attrs: `data-decide="${d.id}" data-a="change"` })}
      ${btn({ label: t("noSkip"), icon: "x", attrs: `data-decide="${d.id}" data-a="no"` })}
      ${btn({ label: t("why"), icon: "question", kind: "ghost", attrs: `data-decide="${d.id}" data-a="why"` })}
    </div>
  </article>`;
}

registerScreen({
  id: "today", roles: ["owner"], icon: "today", title: lang => lang === "ru" ? "Сегодня" : "Today",
  async render(root) {
    const [orders, ship, feed] = await Promise.all([api("/api/orders?view=open"), api("/api/shipping"), api("/api/today")]);
    if (orders.silent || ship.silent) return;
    const ready = ship.ok ? ship.ready.reduce((s, c) => s + c.ready, 0) : 0;
    const late = orders.ok ? orders.attention.length : 0;
    const cards = feed.ok && Array.isArray(feed.cards) ? feed.cards : [];
    root.innerHTML = `
      <header class="head"><div><h1>${t("todayTitle")}</h1><p class="sub">${esc(fmtDate(new Date().toISOString(), { withDay: true }))}</p></div></header>
      <div class="stats">
        ${stat(t("openOrders"), orders.ok ? orders.orders.length : "—")}
        ${stat(t("readyToShip"), ready, { kind: ready ? "good" : "" })}
        ${stat(t("lateOrders"), late, { kind: late ? "bad" : "" })}
      </div>
      <h2 class="sec">${t("feedTitle")} <b>${cards.length}</b></h2>
      <div class="feed">${cards.length ? cards.map(decisionCard).join("") : `
        <article class="decision"><span class="d-kind">${icon("spark")}${t("plan")}</span>
          <p class="d-why">${t("feedEmpty")}</p>
          <div class="d-acts">${btn({ label: t("yesDo"), icon: "check", kind: "primary", disabled: true })}${btn({ label: t("change"), icon: "edit", disabled: true })}${btn({ label: t("noSkip"), icon: "x", disabled: true })}${btn({ label: t("why"), icon: "question", kind: "ghost", disabled: true })}</div>
        </article>`}</div>
      <div class="ask"><input class="input" id="ask" placeholder="${t("askPlaceholder")}">${btn({ label: t("ask"), icon: "spark", kind: "primary", attrs: "data-soon" })}</div>`;
    root.querySelectorAll("[data-decide]").forEach(b => b.onclick = () => toast(t("agentSoon")));
    root.querySelector("[data-order]")?.addEventListener("click", e => go("orders", { id: e.currentTarget.dataset.order }));
    bindSoon(root, toast);
  },
});
