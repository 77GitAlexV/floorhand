// Sales snapshots and stored launch offers. Decisions carry only the offer id.
import { registerScreen, addStrings, api, apiFetch, t, esc, state, flash, toast } from "../core.js";
import { btn, empty, sectionTitle, stat } from "../ui.js";

addStrings({
  en: {
    chTitle: "Channels", chLead: "Keep the right pieces ready for the next sale.", chSuggest: "Suggest a stock launch",
    chCover: "Days of cover", chUnknown: "Unknown · no sales", chDays: "{n} days", chAt: "At channels",
    chReady: "Ready in shop", chWork: "In production", chNext: "Suggested next", chOffers: "Launch decisions",
    chNoOffers: "No launch decisions waiting. Suggest a launch to check demand and available capacity.",
    chNoItems: "No channel listings yet. Preview a sales CSV to bring in your first snapshot.",
    chTarget: "Target cover, days", chAvailable: "Launch capacity", chRule: "Counter: offline planning rules",
    chImport: "Preview sales CSV", chCsv: "Sales CSV", chCsvHint: "UTF-8 CSV with channel, sku and stock or date/qty. New channels also need kind and product_id.",
    chPreview: "Review import", chNew: "New", chChanged: "Changed", chSame: "Unchanged", chBefore: "Before", chAfter: "After",
    chPending: "Pending CSV imports", chResume: "Paste the same CSV to reopen its stored preview.",
    chNoPreview: "Nothing changes until you review the diff and choose Yes.", chLimited: "Limited by stage capacity",
    chStock: "Stock", chSales: "Sales", chChannel: "Channel", chPrice: "Price", chQty: "Quantity", chProduct: "Product",
    chVariant: "Fabric / frame", chKind: "Channel type", chKind_marketplace: "Marketplace", chKind_webshop: "Online shop", chKind_wholesale: "Wholesale",
  },
  ru: {
    chTitle: "Каналы", chLead: "Нужные изделия наготове к следующей продаже.", chSuggest: "Предложить запуск на склад",
    chCover: "Запас в днях", chUnknown: "Неизвестно · нет продаж", chDays: "{n} дн.", chAt: "В каналах",
    chReady: "Готово в цеху", chWork: "В производстве", chNext: "Предлагаемый запуск", chOffers: "Решения о запуске",
    chNoOffers: "Нет ожидающих решений. Запросите запуск, чтобы проверить спрос и свободную мощность.",
    chNoItems: "Пока нет товаров в каналах. Загрузите CSV продаж для проверки первого снимка.",
    chTarget: "Целевой запас, дней", chAvailable: "Мощность запуска", chRule: "Расчёт: офлайн-правила планирования",
    chImport: "Проверить CSV продаж", chCsv: "CSV продаж", chCsvHint: "CSV в UTF-8: channel, sku и stock либо date/qty. Для новых каналов нужны kind и product_id.",
    chPreview: "Проверить импорт", chNew: "Новые", chChanged: "Изменены", chSame: "Без изменений", chBefore: "Было", chAfter: "Стало",
    chPending: "Ожидающие импорты CSV", chResume: "Вставьте тот же CSV, чтобы открыть сохранённые изменения.",
    chNoPreview: "Изменения применятся после проверки и нажатия «Да».", chLimited: "Ограничено мощностью этапов",
    chStock: "Остаток", chSales: "Продажи", chChannel: "Канал", chPrice: "Цена", chQty: "Количество", chProduct: "Изделие",
    chVariant: "Ткань / каркас", chKind: "Тип канала", chKind_marketplace: "Маркетплейс", chKind_webshop: "Интернет-магазин", chKind_wholesale: "Опт",
  },
});

const local = r => state.lang === "ru" ? r.message_ru || r.message : r.message;
const metric = m => `<small class="evidence">${t(({ FACT: "fact", CALC: "calc", ESTIMATE: "est" })[m?.tag] || "fact")}</small> ${esc(m?.value ?? "—")}`;
let target = 14;

registerScreen({
  id: "channels", roles: ["office"], icon: "globe", title: lang => lang === "ru" ? "Каналы" : "Channels",
  async render(root, ctx) {
    const r = await api(`/api/channels/overview?target_days=${target}`);
    if (!ctx.isActive()) return;
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    root.innerHTML = `<header class="head"><div><p class="eyebrow">${t("chRule")}</p><h1>${t("chTitle")}</h1><p class="sub">${t("chLead")}</p></div></header>
      <div class="channel-toolbar panel"><label for="coverTarget">${t("chTarget")} · ${t("est")}</label><input class="input" id="coverTarget" type="number" min="1" max="90" required value="${target}">
        ${btn({ label: t("chSuggest"), icon: "spark", kind: "primary", attrs: 'id="suggestLaunch"' })}
        <span>${t("chAvailable")}: ${metric(r.capacity.available_launch_qty)}</span></div>
      <div class="channel-grid">${r.items.map(i => `<article class="panel channel-product ${i.days_of_cover.value !== null && i.days_of_cover.value < target ? "low-cover" : ""}">
        <div><h2>${esc(i.product)}</h2><p class="muted">${esc(i.variant)}</p></div>
        <div class="cover-number">${i.no_sales ? "—" : esc(i.days_of_cover.value)}<span>${t("chCover")} · ${t("est")}</span></div>
        ${i.no_sales ? `<p class="muted">${t("chUnknown")}</p>` : ""}
        <dl class="metric-list"><div><dt>${t("chAt")}</dt><dd>${metric(i.stock_at_channels)}</dd></div><div><dt>${t("chReady")}</dt><dd>${metric(i.shop_ready)}</dd></div>
        <div><dt>${t("chWork")}</dt><dd>${metric(i.shop_in_progress)}</dd></div><div><dt>${t("chNext")}</dt><dd>${metric(i.make_next)}</dd></div></dl>
        ${i.capacity_limited ? `<p class="hot-ink">${t("chLimited")}</p>` : ""}
        <ul class="channel-list">${i.listings.map(l => `<li><span>${esc(l.channel)}<small>${esc(l.sku)}</small></span><b>${metric(l.stock_at_channel)}</b></li>`).join("")}</ul></article>`).join("") || empty(t("chNoItems"), "globe")}</div>
      <details class="disc"><summary>${t("chCover")}</summary><p class="disc-body">${esc(r.assumptions[state.lang])}</p></details>
      ${sectionTitle(t("chOffers"))}<div class="stack" id="launchOffers"></div>
      ${sectionTitle(t("chCsv"))}<section class="panel stack"><p class="muted">${t("chCsvHint")}</p>
        <label for="channelCsv">${t("chCsv")}</label><textarea class="input" id="channelCsv" rows="5" spellcheck="false"></textarea>
        ${btn({ label: t("chImport"), icon: "file", kind: "secondary", attrs: 'id="previewCsv"' })}<p class="muted">${t("chNoPreview")}</p><div id="csvPreview"></div>
        ${r.imports.some(i => i.status === "proposed") ? `<p>${t("chPending")}: ${r.imports.filter(i => i.status === "proposed").map(i => `${t("fact")} #${i.id}`).join(", ")}</p><p class="muted">${t("chResume")}</p>` : ""}</section>`;
    drawOffers(r.proposals);
    root.querySelector("#coverTarget").onchange = e => {
      if (e.target.reportValidity()) { target = Number(e.target.value); ctx.refresh(); }
    };
    function drawOffers(cards) {
      const host = root.querySelector("#launchOffers");
      host.innerHTML = cards.length ? cards.map(p => `<article class="decision-panel panel"><div><span class="eyebrow">${t("chRule")}</span><h3>${esc(p.product)}</h3><p>${esc(local(p))}</p></div><div class="row">
        ${btn({ label: t("yes"), icon: "check", kind: "primary", attrs: `data-offer="${p.id}" data-decision="apply"` })}
        ${btn({ label: t("no"), kind: "ghost", attrs: `data-offer="${p.id}" data-decision="reject"` })}</div></article>`).join("") : empty(t("chNoOffers"), "check");
      host.querySelectorAll("[data-offer]").forEach(b => b.onclick = async () => {
        host.querySelectorAll(`[data-offer="${b.dataset.offer}"]`).forEach(x => { x.disabled = true; });
        const d = await api(`/api/channels/proposals/${b.dataset.offer}/${b.dataset.decision}`, {});
        if (!d.silent) flash(d.ok, local(d));
        if (d.ok) ctx.refresh(); else host.querySelectorAll("[data-offer]").forEach(x => { x.disabled = false; });
      });
    }
    root.querySelector("#suggestLaunch").onclick = async e => {
      const input = root.querySelector("#coverTarget");
      if (!input.reportValidity()) return;
      target = Number(input.value); e.currentTarget.disabled = true;
      const p = await api("/api/channels/suggest", { target_days: target });
      if (!ctx.isActive()) return;
      root.querySelector("#suggestLaunch").disabled = false;
      if (!p.ok) { if (!p.silent) flash(false, local(p)); return; }
      toast(local(p)); drawOffers(p.proposals);
      if (p.reason) root.querySelector("#launchOffers").innerHTML = empty(p.reason[state.lang], "check");
    };
    root.querySelector("#previewCsv").onclick = async e => {
      const button = e.currentTarget; button.disabled = true;
      // This endpoint consumes raw CSV, not a JSON string.
      const p = await apiFetch("/api/channels/import", { method: "POST", headers: { "Content-Type": "text/csv; charset=utf-8" }, body: root.querySelector("#channelCsv").value });
      button.disabled = false;
      if (!ctx.isActive()) return;
      if (!p.ok) { if (!p.silent) flash(false, local(p)); return; }
      const host = root.querySelector("#csvPreview");
      const labels = { stock_at_channel: "chStock", qty: "chQty", price: "chPrice", product_id: "chProduct", variant: "chVariant", kind: "chKind" };
      const values = obj => obj ? Object.entries(obj).map(([k, v]) => `${esc(t(labels[k] || k))}: ${v && typeof v === "object" ? metric(v) : esc(k === "kind" ? t("chKind_" + v) : v)}`).join(" · ") : "—";
      host.innerHTML = `<h3>${t("chPreview")}</h3><p>${esc(local(p))}</p><div class="stats">${stat(t("chNew"), metric(p.diff.new))}${stat(t("chChanged"), metric(p.diff.changed))}${stat(t("chSame"), metric(p.diff.unchanged))}</div>
        <ul class="plain csv-diff">${p.changes.map(c => `<li><b>${esc(c.key.channel)} ${esc(c.key.sku || "")} ${esc(c.key.date || "")}</b><p>${t("chBefore")}: ${values(c.before)}</p><p>${t("chAfter")}: ${values(c.after)}</p></li>`).join("")}</ul>
        ${p.status === "proposed" ? `<div class="row">${btn({ label: t("yes"), kind: "primary", attrs: 'data-import="apply"' })}${btn({ label: t("no"), attrs: 'data-import="reject"' })}</div>` : ""}`;
      toast(local(p));
      host.querySelectorAll("[data-import]").forEach(b => b.onclick = async () => {
        host.querySelectorAll("[data-import]").forEach(x => { x.disabled = true; });
        const path = b.dataset.import === "apply" ? `/api/channels/import/${p.id}/apply` : `/api/channels/proposals/${p.action_id}/reject`;
        const d = await api(path, {}); if (!d.silent) flash(d.ok, local(d));
        if (d.ok) ctx.refresh(); else host.querySelectorAll("[data-import]").forEach(x => { x.disabled = false; });
      });
    };
  },
});
