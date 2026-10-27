// A money-free floor projection. SSE invalidates snapshots; reconnect always resyncs.
import { registerScreen, addStrings, api, t, esc, name, state, fmtDate, fmtTime, flash, toast } from "../core.js";
import { icon, btn, empty, bar } from "../ui.js";

addStrings({
  en: {
    lvTitle: "Live shop", lvLead: "Every piece, moving forward.", lvConnected: "Live", lvReconnecting: "Reconnecting · last snapshot shown",
    lvConnecting: "Connecting", lvQueue: "waiting", lvDone: "done today", lvCapacity: "daily capacity", lvStage: "Stage",
    lvTicker: "Just recorded", lvWaiting: "The next shop-floor mark will appear here.", lvLate: "Needs attention", lvOnTime: "All open orders are on time.",
    lvLateDays: "{n} days late", lvDoneWord: "done", lvUndo: "undone", lvOrder: "Order created", lvShipment: "Shipment recorded",
    lvTv: "TV mode", lvExit: "Exit TV mode", lvSim: "Demo simulator", lvStart: "Start demo", lvStop: "Pause demo",
    lvSimOn: "Running · shop hours only", lvSimOff: "Paused", lvFact: "Live facts from the workshop", lvRefresh: "Refresh snapshot",
    lvAsOf: "Snapshot", lvNoStages: "No production stages yet. Seed the demo workshop to see its flow.", lvStale: "Connection interrupted. Keeping the last snapshot; retrying automatically.",
  },
  ru: {
    lvTitle: "Цех вживую", lvLead: "Каждое изделие движется вперёд.", lvConnected: "В эфире", lvReconnecting: "Переподключение · показан последний снимок",
    lvConnecting: "Подключение", lvQueue: "в очереди", lvDone: "сделано сегодня", lvCapacity: "мощность в день", lvStage: "Этап",
    lvTicker: "Только что отмечено", lvWaiting: "Здесь появится следующая отметка из цеха.", lvLate: "Требует внимания", lvOnTime: "Все открытые заказы идут в срок.",
    lvLateDays: "Опоздание: {n} дн.", lvDoneWord: "готово", lvUndo: "отменено", lvOrder: "Заказ создан", lvShipment: "Отгрузка записана",
    lvTv: "Режим ТВ", lvExit: "Выйти из режима ТВ", lvSim: "Демо-симулятор", lvStart: "Запустить демо", lvStop: "Приостановить демо",
    lvSimOn: "Работает · только в часы цеха", lvSimOff: "Приостановлен", lvFact: "Живые факты из мастерской", lvRefresh: "Обновить снимок",
    lvAsOf: "Снимок", lvNoStages: "Пока нет этапов производства. Загрузите демо-цех, чтобы увидеть движение.", lvStale: "Связь прервана. Сохраняем последний снимок и автоматически повторяем подключение.",
  },
});

registerScreen({
  id: "live", roles: ["owner"], icon: "layers", title: lang => lang === "ru" ? "Цех вживую" : "Live shop",
  async render(root, ctx) {
    const tv = ctx.query.get("tv") === "1";
    document.body.classList.toggle("live-tv", tv);
    let stream = null, disposed = false, loading = false, dirty = false, stages = [], marks = [], seen = new Set(), tickerKey = "";
    let poll;
    ctx.onDispose(() => { disposed = true; stream?.close(); clearInterval(poll); document.body.classList.remove("live-tv"); });
    root.classList.add("live-main");
    root.innerHTML = `<section class="live-hero"><header class="live-heading"><div><p class="eyebrow">Floorhand / ${t("lvFact")}</p><h1>${t("lvTitle")}</h1><p class="live-lead">${t("lvLead")}</p></div>
      <div class="live-tools"><span class="live-status" id="liveStatus" role="status">${t("lvConnecting")}</span><a class="btn secondary" href="${tv ? "#/live" : "#/live?tv=1"}">${icon("tablet")}${tv ? t("lvExit") : t("lvTv")}</a></div></header>
      <div class="live-totals" id="liveTotals">${t("loading")}</div><div class="live-rail" id="liveRail"></div>
      <footer class="live-foot"><span id="liveAsOf"></span>${btn({ label: t("lvRefresh"), kind: "ghost", attrs: 'id="liveRefresh"' })}</footer></section>
      <div class="live-bottom"><section class="panel live-records"><h2 class="sec">${t("lvTicker")}</h2><ol id="liveTicker" class="live-ticker" aria-live="polite"><li>${empty(t("lvWaiting"), "clock")}</li></ol></section>
      <section class="panel"><h2 class="sec">${t("lvLate")}</h2><div id="liveLate">${t("loading")}</div></section></div>
      <section class="live-simulator panel"><span>${icon("spark")} ${t("lvSim")} <small id="simStatus"></small></span><div id="simControls"></div></section>`;
    const status = (key, connected = false) => { if (!disposed) { const s = root.querySelector("#liveStatus"); s.textContent = t(key); s.classList.toggle("connected", connected); } };
    async function snapshot() {
      if (disposed) return;
      if (loading) { dirty = true; return; }
      loading = true;
      const [floor, sim] = await Promise.all([api("/api/live/snapshot"), api("/api/sim/status")]);
      if (disposed || !ctx.isActive()) return;
      if (floor.ok) {
        const old = new Map(stages.map(s => [s.id, s.queue])); stages = floor.stages;
        const done = stages.reduce((a, s) => a + s.done_today, 0), queued = stages.reduce((a, s) => a + s.queue, 0);
        root.querySelector("#liveTotals").innerHTML = `<div><b>${done}</b><span>${t("fact")} · ${t("lvDone")}</span></div><div><b>${queued}</b><span>${t("fact")} · ${t("lvQueue")}</span></div><p>${esc(fmtDate(floor.date, { withDay: true }))}</p>`;
        root.querySelector("#liveRail").innerHTML = stages.map((s, i) => `<article class="live-stage ${s.bottleneck ? "bottleneck" : ""} ${old.has(s.id) && old.get(s.id) !== s.queue ? "queue-changed" : ""}" data-live-stage="${s.id}">
          <div class="live-stage-top"><span>${t("lvStage")} ${i + 1}</span>${icon(s.bottleneck ? "flame" : "chevron")}</div><h2>${esc(name(s))}</h2>
          <div class="live-queue"><b>${s.queue}</b><span>${t("fact")} · ${t("lvQueue")}</span></div>
          <div class="flow-track" aria-hidden="true"><i></i><i></i><i></i></div>
          <div class="live-stage-done"><b>${s.done_today}</b> ${t("lvDone")}<small>${t("fact")} · ${s.capacity} ${t("lvCapacity")}</small>${bar(s.done_today / Math.max(1, s.capacity), s.bottleneck ? "hot" : "")}</div>
          <div class="live-crew">${s.people.map(p => `<span class="${p.today ? "active" : ""}" title="${esc(p.name)}">${esc(p.name.split(" ")[0])}<b>${p.today}</b></span>`).join("")}</div></article>`).join("") || empty(t("lvNoStages"), "floor");
        const late = floor.late;
        root.querySelector("#liveLate").innerHTML = late.length ? `<ul class="live-late-list">${late.map(o => `<li><a href="#/orders/${o.id}"><b>#${o.number} · ${esc(o.product)}</b><span>${t("fact")} · ${t("lvLateDays", { n: o.late_days })}</span></a></li>`).join("")}</ul>` : empty(t("lvOnTime"), "check");
        root.querySelector("#liveAsOf").textContent = `${t("lvAsOf")} · ${fmtDate(floor.date)} ${fmtTime(floor.as_of)}`;
        floor.marks.forEach(m => seen.add(`${m.type}:${m.id}`));
        while (seen.size > 100) seen.delete(seen.values().next().value);
        const merged = new Map([...marks, ...floor.marks].map(m => [`${m.type}:${m.id}`, m]));
        marks = [...merged.values()].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || "") || b.id - a.id).slice(0, 8);
        drawTicker();
        if (stream?.readyState === 1) status("lvConnected", true);
      } else if (!floor.silent) { status("lvReconnecting"); root.querySelector("#liveAsOf").textContent = t("lvStale"); }
      if (sim.ok) {
        root.querySelector("#simStatus").textContent = sim.error ? (state.lang === "ru" ? sim.message_ru : sim.message) : t(sim.running ? "lvSimOn" : "lvSimOff");
        root.querySelector("#simControls").innerHTML = btn({ label: t(sim.running ? "lvStop" : "lvStart"), kind: "secondary", attrs: `data-sim="${sim.running ? "stop" : "start"}"` });
        root.querySelector("[data-sim]").onclick = async e => {
          const b = e.currentTarget; b.disabled = true;
          const r = await api(`/api/sim/${b.dataset.sim}`, {});
          if (!r.silent) flash(r.ok, state.lang === "ru" ? r.message_ru || r.message : r.message);
          b.disabled = false; snapshot();
        };
      }
      loading = false;
      if (dirty) { dirty = false; snapshot(); }
      return floor;
    }
    function record(type, e) {
      let data; try { data = JSON.parse(e.data); } catch { snapshot(); return; }
      const key = `${type}:${data.id}`;
      if (seen.has(key)) return;
      seen.add(key); if (seen.size > 100) seen.delete(seen.values().next().value);
      marks.unshift({ type, ...data }); marks = marks.slice(0, 8);
      drawTicker();
      snapshot();
    }
    function drawTicker() {
      const key = marks.map(m => `${m.type}:${m.id}`).join("|");
      if (tickerKey === key) return;
      tickerKey = key;
      root.querySelector("#liveTicker").innerHTML = marks.map(m => `<li><span class="mark-symbol">${icon(m.type === "undo" ? "undo" : "check")}</span><div><b>${esc(m.type === "marked" || m.type === "undo" ? (state.lang === "ru" ? m.stage_ru : m.stage) : t(m.type === "shipment" ? "lvShipment" : "lvOrder"))}</b>
        <p>${m.product ? `${esc(m.product)} · ` : ""}${m.number ? `#${esc(m.number)}` : esc((m.numbers || []).join(", "))}${m.seq ? ` / ${esc(m.seq)}` : ""} · ${t("fact")} ${m.type === "marked" ? t("lvDoneWord") : m.type === "undo" ? t("lvUndo") : ""} ${esc(fmtTime(m.created_at))}</p></div></li>`).join("");
    }
    root.querySelector("#liveRefresh").onclick = async () => {
      const r = await snapshot();
      if (r && !r.silent) { if (r.ok) toast(r.message); else flash(false, r.message); }
    };
    // Native EventSource retries a dropped stream. onopen fetches a full snapshot
    // because the server intentionally does not replay missed events.
    stream = new EventSource("/api/live");
    stream.onopen = () => { status("lvConnected", true); snapshot(); };
    stream.onerror = () => { status("lvReconnecting"); api("/api/me"); };
    ["marked", "undo", "order_created", "shipment"].forEach(type => stream.addEventListener(type, e => { if (!disposed) record(type, e); }));
    stream.addEventListener("resync", snapshot);
    poll = setInterval(snapshot, 30000);
    await snapshot();
  },
});
