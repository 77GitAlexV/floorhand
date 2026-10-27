// Office "Shipping": the most used office screen. Customer -> what goes ->
// payment -> SHIP AND PRINT. Only finished items can go; the rest shows why.
import { registerScreen, addStrings, api, t, esc, state, money, flash, go } from "../core.js";
import { icon, btn, empty } from "../ui.js";

addStrings({
  en: {
    shipping: "Shipping", readyToShip: "Ready to ship", nothingReady: "Nothing ready yet.", readyOf: "{a} of {b} ready",
    shippedToday: "Shipped today", owedToUs: "Owed to us", pickCustomer: "Pick a customer on the left.",
    alreadyOwes: "Already owes {m}", stillAt: "still at {stage}", total: "Total", payment: "Payment",
    p_paid: "Paid in full", p_credit: "On account", p_partial: "Part paid", paidNow: "Paid now",
    shipAndPrint: "SHIP AND PRINT", scope: "Shipping {n} piece(s) to {c}", pickItems: "Tick what goes.", pickPay: "Choose how it is paid.",
    noteN: "Note №{n}", invoice: "delivery note",
  },
  ru: {
    shipping: "Отгрузки", readyToShip: "Готово к отгрузке", nothingReady: "Пока ничего не готово.", readyOf: "{a} из {b} готово",
    shippedToday: "Сегодня отгружено", owedToUs: "Должны нам", pickCustomer: "Выбери клиента слева.",
    alreadyOwes: "Уже должен {m}", stillAt: "ещё на {stage}", total: "Итого", payment: "Оплата",
    p_paid: "Всё оплачено", p_credit: "В долг", p_partial: "Часть", paidNow: "Оплачено сейчас",
    shipAndPrint: "ОТГРУЗИТЬ И НАПЕЧАТАТЬ", scope: "Отгружаем {n} шт — {c}", pickItems: "Отметь, что едет.", pickPay: "Выбери, как оплачено.",
    noteN: "Накладная №{n}", invoice: "накладная",
  },
});

registerScreen({
  id: "shipping", roles: ["office"], icon: "shipping", params: ["id"], title: lang => lang === "ru" ? "Отгрузки" : "Shipping",
  async render(root, ctx) {
    const b = await api("/api/shipping");
    if (!b.ok) { if (!b.silent) root.innerHTML = empty(b.message, "alert"); return; }
    const cur = b.currency;
    const sel = ctx.params.id ? Number(ctx.params.id) : null;
    root.innerHTML = `
      <header class="head"><div><h1>${t("shipping")}</h1></div></header>
      <div class="ship ${sel ? "focus" : ""}">
        <aside class="ship-left">
          <h2 class="sec" style="margin-top:0">${t("readyToShip")} <b>${b.ready.length}</b></h2>
          <ul class="pick">${b.ready.map(c => `<li><button type="button" class="${c.customer_id === sel ? "on" : ""}" data-cust="${c.customer_id}">
            ${icon(c.ready === c.open ? "check" : "box")}<span class="p-main"><b>${esc(c.name)}</b><small>${esc(c.city || "")}</small></span>
            <span class="p-ready ${c.ready === c.open ? "all" : ""}">${t("readyOf", { a: c.ready, b: c.open })}</span></button></li>`).join("")
            || `<li>${empty(t("nothingReady"), "truck")}</li>`}</ul>
          <h2 class="sec">${t("shippedToday")} <b>${b.today.length}</b></h2>
          <ul class="plain">${b.today.map(s => `<li><a href="/invoice/${s.id}" target="_blank">${icon("print")} ${t("noteN", { n: s.id })}</a><span>${esc(s.customer)} · <b>${money(cur, s.total)}</b></span></li>`).join("") || `<li class="muted">—</li>`}</ul>
          <h2 class="sec">${t("owedToUs")} <b>${b.debts.length}</b><span class="muted" style="margin-left:auto;text-transform:none;letter-spacing:0">${money(cur, b.owed_total)}</span></h2>
          <ul class="plain">${b.debts.slice(0, 6).map(d => `<li><span>${esc(d.name)}</span><b>${money(cur, d.owed)}</b></li>`).join("")}</ul>
        </aside>
        <section class="ship-right">
          ${sel ? btn({ label: t("readyToShip"), icon: "back", kind: "ghost", cls: "only-phone", attrs: 'id="shipBack"' }) : ""}
          <div id="shipPanel" class="panel">${empty(t("pickCustomer"), "truck")}</div>
        </section>
      </div>`;
    root.querySelectorAll("[data-cust]").forEach(el => el.onclick = () => go("shipping", { id: el.dataset.cust }));
    root.querySelector("#shipBack")?.addEventListener("click", () => go("shipping"));
    if (sel) renderPanel(root, ctx, sel);
  },
});

async function renderPanel(root, ctx, customerId) {
  const c = await api(`/api/shipping/${customerId}`);
  const panel = root.querySelector("#shipPanel");
  if (!c.ok) { if (!c.silent) panel.innerHTML = empty(c.message, "alert"); return; }
  const cur = c.currency;
  let payment = null;
  panel.innerHTML = `
    <h2>${esc(c.name)}${c.city ? `, ${esc(c.city)}` : ""}</h2>
    ${c.owed ? `<p class="owes">${icon("alert")} ${t("alreadyOwes", { m: money(cur, c.owed) })}</p>` : ""}
    <div style="margin-top:12px">${c.items.map(i => `<label class="check-row ${i.ready ? "" : "off"}">
      <input type="checkbox" data-unit="${i.unit_id}" data-price="${i.price}" ${i.ready ? "checked" : "disabled"}>
      <span class="cr-main"><b>${esc(i.label)}</b> ${esc(i.product)}${i.ready ? "" : `<br><small class="muted">${t("stillAt", { stage: esc(state.lang === "ru" ? i.at_ru : i.at) })}</small>`}</span>
      <span class="cr-right">${i.ready ? money(cur, i.price) : ""}</span></label>`).join("")}</div>
    <div class="ship-foot">
      <div class="total"><span>${t("total")}</span><b id="total"></b></div>
      <div class="pay">${["paid", "credit", "partial"].map(p => `<button type="button" data-pay="${p}">${t("p_" + p)}</button>`).join("")}</div>
      <label class="partial" id="partialBox" hidden>${t("paidNow")} <input id="paidAmount" type="number" min="0" inputmode="numeric"></label>
      <p class="scope" id="scope"></p>
      ${btn({ label: t("shipAndPrint"), icon: "truck", kind: "primary", cls: "hero", attrs: 'id="doShip"', disabled: true })}
    </div>`;
  const boxes = [...panel.querySelectorAll("[data-unit]")];
  const update = () => {
    const picked = boxes.filter(x => x.checked);
    panel.querySelector("#total").textContent = money(cur, picked.reduce((s, x) => s + Number(x.dataset.price), 0));
    panel.querySelector("#scope").textContent = !picked.length ? t("pickItems") : !payment ? t("pickPay") : t("scope", { n: picked.length, c: c.name });
    panel.querySelector("#doShip").disabled = !picked.length || !payment;
  };
  boxes.forEach(x => x.onchange = update);
  panel.querySelectorAll("[data-pay]").forEach(b => b.onclick = () => {
    payment = b.dataset.pay;
    panel.querySelectorAll("[data-pay]").forEach(x => x.classList.toggle("on", x === b));
    panel.querySelector("#partialBox").hidden = payment !== "partial";
    update();
  });
  update();
  panel.querySelector("#doShip").onclick = async () => {
    const btnEl = panel.querySelector("#doShip");
    btnEl.disabled = true;
    const win = window.open("", "_blank");  // opened inside the click, so it is not blocked
    const r = await api("/api/ship", {
      customer_id: customerId, payment,
      unit_ids: boxes.filter(x => x.checked).map(x => Number(x.dataset.unit)),
      paid_amount: Number(panel.querySelector("#paidAmount").value || 0),
    });
    if (r.silent) { if (win) win.close(); return; }
    flash(r.ok, r.message);
    if (!r.ok) { if (win) win.close(); btnEl.disabled = false; return; }
    if (win) win.location = `/invoice/${r.shipment_id}`;
    go("shipping");
  };
}
