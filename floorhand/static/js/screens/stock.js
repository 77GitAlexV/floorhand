// Office "Stock": materials on hand, what open work needs, what is short and whether it
// stops the next marks (features/materials.py). Stock is derived from receipts minus the
// consumption that follows work events, so an undo returns the material by itself.
import { registerScreen, addStrings, api, t, esc, fmtDate, fmtTime, flash } from "../core.js";
import { btn, empty, table, stat, sectionTitle } from "../ui.js";

addStrings({
  en: {
    stockTitle: "Stock", material: "Material", onHand: "On hand", need: "Needed by open work", short: "Short",
    nextMarks: "stops the next marks", low: "below reorder point", receive: "Record a receipt",
    qtyW: "Quantity", noteW: "Note (supplier, invoice)", recordIt: "Record", journal: "Journal",
    m_fabric: "fabric", m_foam: "foam", m_frame: "frames", m_fittings: "fittings", m_other: "other",
    j_receipt: "receipt", j_adjust: "correction", j_used: "used", pickRow: "Tap a material to see its journal.",
    nShort: "Short now", nStops: "Stops the next marks", nMats: "Materials",
  },
  ru: {
    stockTitle: "Склад", material: "Материал", onHand: "Есть", need: "Нужно под открытую работу", short: "Не хватает",
    nextMarks: "остановит следующие отметки", low: "ниже точки заказа", receive: "Записать приход",
    qtyW: "Количество", noteW: "Примечание (поставщик, накладная)", recordIt: "Записать", journal: "Журнал",
    m_fabric: "ткань", m_foam: "поролон", m_frame: "каркасы", m_fittings: "фурнитура", m_other: "другое",
    j_receipt: "приход", j_adjust: "поправка", j_used: "расход", pickRow: "Нажми на материал, чтобы увидеть журнал.",
    nShort: "Не хватает сейчас", nStops: "Остановят отметки", nMats: "Материалов",
  },
});

const KINDS = ["fabric", "foam", "frame", "fittings", "other"];

registerScreen({
  id: "stock", roles: ["office"], icon: "box", title: lang => lang === "ru" ? "Склад" : "Stock",
  async render(root, ctx) {
    const r = await api("/api/materials");
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    const stops = new Set(r.shortages.filter(s => s.stops_next).map(s => s.material_id));
    const mats = KINDS.flatMap(k => r.materials.filter(m => m.kind === k));
    const rows = mats.map(m => ({
      attrs: `data-mat="${m.id}" data-name="${esc(m.name)}" style="cursor:pointer"`, cls: m.short ? "short" : "",
      cells: [
        `<b>${esc(m.name)}</b><br><small class="muted">${t("m_" + m.kind)}${m.supplier ? " · " + esc(m.supplier) : ""}</small>`,
        `${m.on_hand} ${esc(m.unit)}${m.low ? `<br><small class="late">${t("low")}</small>` : ""}`,
        `${m.need} ${esc(m.unit)}`,
        `${m.short ? `<b class="late">${m.short} ${esc(m.unit)}</b>` : "—"}${stops.has(m.id) ? `<br><small class="late">${t("nextMarks")}</small>` : ""}`,
      ],
    }));
    root.innerHTML = `
      <header class="head"><div><h1>${t("stockTitle")}</h1></div></header>
      <div class="stats">
        ${stat(t("nMats"), r.materials.length)}
        ${stat(t("nShort"), r.shortages.length, { kind: r.shortages.length ? "bad" : "" })}
        ${stat(t("nStops"), stops.size, { kind: stops.size ? "hot" : "" })}
      </div>
      <div class="order-grid">
        <section>${table({ cols: [{ label: t("material") }, { label: t("onHand"), cls: "num" }, { label: t("need"), cls: "num" }, { label: t("short"), cls: "num" }], rows })}</section>
        <aside class="order-side">
          <div class="panel">
            <h2 class="sec" style="margin-top:0">${t("receive")}</h2>
            <select class="input" id="recMat">${r.materials.map(m => `<option value="${m.id}">${esc(m.name)} (${esc(m.unit)})</option>`).join("")}</select>
            <input class="input" id="recQty" type="number" step="0.1" min="0" inputmode="decimal" placeholder="${t("qtyW")}" style="margin-top:8px">
            <input class="input" id="recNote" type="text" placeholder="${t("noteW")}" style="margin-top:8px">
            <div style="margin-top:12px">${btn({ label: t("recordIt"), icon: "plus", kind: "primary", size: "lg", cls: "wide", attrs: 'id="recGo"' })}</div>
          </div>
          <div class="panel" id="journal" style="margin-top:16px"><p class="muted">${t("pickRow")}</p></div>
        </aside>
      </div>`;
    root.querySelector("#recGo").onclick = async () => {
      const res = await api("/api/materials/receive", {
        material_id: Number(root.querySelector("#recMat").value),
        qty: Number(root.querySelector("#recQty").value), note: root.querySelector("#recNote").value,
      });
      if (res.silent) return;
      flash(res.ok, res.message);
      if (res.ok) ctx.refresh();
    };
    root.querySelectorAll("[data-mat]").forEach(tr => tr.onclick = async () => {
      const j = await api(`/api/materials/${tr.dataset.mat}/journal`);
      if (!j.ok) return;
      const el = root.querySelector("#journal");
      el.innerHTML = `${sectionTitle(`${t("journal")} · ${tr.dataset.name}`)}
        <ul class="plain journal">${j.rows.map(x => `<li><span class="muted">${esc(fmtDate(x.at))} ${esc(fmtTime(x.at))}</span>
          <span>${t("j_" + x.kind)}${x.unit ? " " + esc(x.unit) : ""}${x.note ? ` · ${esc(x.note)}` : ""}</span>
          <b class="${x.qty < 0 ? "late" : ""}">${x.qty > 0 ? "+" : ""}${Math.round(x.qty * 100) / 100}</b></li>`).join("")}</ul>`;
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  },
});
