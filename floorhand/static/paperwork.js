"use strict";
// Office page for paper: order sheets, delivery notes, the e-mail outbox.
// Every write is an explicit POST with an idempotency key; every answer is shown in words.
(() => {
  const root = document.getElementById("paperwork");
  const WORDS = {
    en: {
      title: "Paperwork", back: "← Back", sheets: "Order sheets", notes: "Delivery notes", outbox: "Outbox",
      new: "new items without a sheet", changed: "sheets out of date — reprint", today: "from today's orders",
      printOrders: "Print these orders", reasonNone: "First print", lost: "Reprint: lost",
      damaged: "Reprint: damaged", changedR: "Reprint: order changed", other: "Reprint: other",
      nothing: "Nothing needs attention on paper.", was: "was", now: "now",
      pull: "Cancelled — pull the sheet from the floor", void: "Void sheet", voidReason: "order cancelled",
      open: "Open", email: "E-mail", save: "Save", addEmail: "customer e-mail", preview: "Preview",
      send: "Send", off: "Sending is off — messages wait here until the shop sets up e-mail.",
      on: "Sending is on.", pcs: "pcs", owed: "owed", noNotes: "No deliveries yet.",
      noMail: "Outbox is empty.", draft: "draft", sent: "sent", failed: "failed",
      badNumbers: "Type order numbers separated by commas, e.g. 214, 215.", unknown: "No order",
      loading: "Loading…", cantReach: "No connection. Try again.", popup: "Open the sheets",
    },
    ru: {
      title: "Документы", back: "← Назад", sheets: "Листки заказов", notes: "Накладные", outbox: "Исходящие",
      new: "новых штук без листка", changed: "листков устарело — перепечатать", today: "из сегодняшних заказов",
      printOrders: "Напечатать эти заказы", reasonNone: "Первая печать", lost: "Повтор: потерян",
      damaged: "Повтор: испорчен", changedR: "Повтор: заказ изменён", other: "Повтор: другое",
      nothing: "С бумагой всё в порядке.", was: "было", now: "стало",
      pull: "Отменён — уберите листок из цеха", void: "Аннулировать", voidReason: "заказ отменён",
      open: "Открыть", email: "Отправить", save: "Сохранить", addEmail: "почта заказчика", preview: "Просмотр",
      send: "Отправить", off: "Отправка выключена — письма ждут здесь, пока цех не настроит почту.",
      on: "Отправка включена.", pcs: "шт", owed: "долг", noNotes: "Отгрузок пока нет.",
      noMail: "Исходящих нет.", draft: "черновик", sent: "отправлено", failed: "ошибка",
      badNumbers: "Введите номера заказов через запятую, например 214, 215.", unknown: "Нет заказа",
      loading: "Загрузка…", cantReach: "Нет связи. Попробуйте ещё раз.", popup: "Открыть листки",
    },
  };
  const lang = root.dataset.lang === "ru" ? "ru" : "en";
  const t = k => WORDS[lang][k] ?? WORDS.en[k] ?? k;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (cur, n) => `${n < 0 ? "-" : ""}${cur}${Math.abs(n).toLocaleString("en-US")}`;
  const key = () => (crypto.randomUUID ? crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2));

  function tell(ok, text) {
    const box = $("message");
    box.className = ok ? "ok" : "bad";
    box.textContent = text || "";
  }

  async function api(path, method = "GET", body) {
    let res;
    try {
      res = await fetch(path, {
        method, credentials: "same-origin",
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch { throw new Error(t("cantReach")); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || data.detail || res.statusText);
    return data;
  }

  // Disable the button while the request runs: a double click is one action.
  async function act(button, task) {
    button.disabled = true;
    try { await task(); } catch (err) { tell(false, err.message); } finally { button.disabled = false; }
  }

  // --- static words ---
  document.querySelectorAll("[data-t]").forEach(el => { el.textContent = t(el.dataset.t); });
  $("reason").innerHTML = [["", "reasonNone"], ["lost", "lost"], ["damaged", "damaged"],
    ["changed", "changedR"], ["other", "other"]]
    .map(([v, k]) => `<option value="${v}">${esc(t(k))}</option>`).join("");

  // --- order sheets ---
  async function print(body) {
    const tab = window.open("about:blank", "_blank");  // inside the click: not blocked
    try {
      const r = await api("/api/paperwork/sheets/print", "POST",
        { ...body, reason: body.reason ?? ($("reason").value || null), idem_key: key() });
      tell(true, r.message);
      const url = `${r.url}?layout=${encodeURIComponent($("layout").value)}&lang=${lang}`;
      if (tab) tab.location.href = url;
      else $("message").insertAdjacentHTML("beforeend", ` <a href="${esc(url)}" target="_blank">${t("popup")}</a>`);
      await loadAttention();
    } catch (err) { if (tab) tab.close(); throw err; }
  }

  document.querySelectorAll("[data-scope]").forEach(b => b.onclick = () =>
    act(b, () => print({ scope: b.dataset.scope, reason: b.dataset.scope === "changed" ? "changed" : null })));

  $("print-orders").onclick = e => act(e.currentTarget, async () => {
    const text = $("orders").value.trim();
    if (!/^#?\d+(\s*,\s*#?\d+)*$/.test(text)) throw new Error(t("badNumbers"));
    const numbers = [...new Set(text.split(",").map(s => Number(s.trim().replace("#", ""))))];
    const list = await api("/api/orders?view=all");
    const ids = numbers.map(n => {
      const o = list.orders.find(x => x.number === n);
      if (!o) throw new Error(`${t("unknown")} #${n}.`);
      return o.id;
    });
    await print({ scope: "orders", order_ids: ids });
  });

  async function loadAttention() {
    const a = await api("/api/paperwork/sheets/attention");
    document.querySelectorAll("[data-scope]").forEach(b => {
      const n = a.counts[b.dataset.scope] || 0;
      b.querySelector("b").textContent = n;
      b.classList.toggle("hot", b.dataset.scope === "changed" && n > 0);
    });
    const items = a.changed.map(c => `<li><span class="grow"><b>${esc(c.label)}</b> · ${
      c.changes.map(x => `${esc(lang === "ru" ? x.field_ru : x.field)}: ${t("was")} ${esc(x.was ?? "—")}, ${t("now")} <b>${esc(x.now ?? "—")}</b>`).join("; ")
    }</span></li>`).concat(a.cancelled_sheets.map(c => `<li><span class="grow warn"><b>${esc(c.label)}</b> · ${t("pull")}</span>
      <button type="button" class="btn ghost" data-void="${c.unit_id}">${t("void")}</button></li>`));
    $("attention").innerHTML = items.join("") || `<li class="muted">${t("nothing")}</li>`;
    $("attention").querySelectorAll("[data-void]").forEach(b => b.onclick = () => act(b, async () => {
      const r = await api("/api/paperwork/sheets/void", "POST", { unit_id: Number(b.dataset.void), reason: t("voidReason") });
      tell(true, r.message);
      await loadAttention();
    }));
  }

  // --- delivery notes ---
  async function loadNotes() {
    const notes = await api("/api/paperwork/notes");
    $("notes").innerHTML = notes.map(n => `<li>
      <span class="grow"><b>${esc(n.number)}</b> · ${esc(n.date)} · ${esc(n.customer)}<br>
        <small>${n.items} ${t("pcs")} · ${esc(money(n.currency, n.total))}${n.owed ? ` · <span class="warn">${t("owed")} ${esc(money(n.currency, n.owed))}</span>` : ""}
        ${n.email ? ` · ${esc(n.email)}` : ""}${n.last_outbox_status ? ` · <span class="chip ${esc(n.last_outbox_status)}">${t(n.last_outbox_status)}</span>` : ""}</small></span>
      <a class="btn ghost" target="_blank" rel="noopener" href="/paperwork/note/${n.id}?copies=2&lang=${lang}">${t("open")}</a>
      ${n.email ? `<button type="button" class="btn" data-mail="${n.id}">${t("email")}</button>`
        : `<input type="email" placeholder="${t("addEmail")}" data-for="${n.customer_id}" aria-label="${t("addEmail")}">
           <button type="button" class="btn ghost" data-save="${n.customer_id}">${t("save")}</button>`}
    </li>`).join("") || `<li class="muted">${t("noNotes")}</li>`;
    $("notes").querySelectorAll("[data-mail]").forEach(b => b.onclick = () => act(b, async () => {
      const r = await api(`/api/paperwork/notes/${b.dataset.mail}/email`, "POST", { idem_key: key() });
      tell(r.ok, r.message);
      await Promise.all([loadNotes(), loadOutbox()]);
    }));
    $("notes").querySelectorAll("[data-save]").forEach(b => b.onclick = () => act(b, async () => {
      const input = $("notes").querySelector(`input[data-for="${b.dataset.save}"]`);
      const r = await api(`/api/paperwork/customers/${b.dataset.save}/email`, "PUT", { email: input.value });
      tell(true, r.message);
      await loadNotes();
    }));
  }

  // --- outbox ---
  async function loadOutbox() {
    const o = await api("/api/paperwork/outbox");
    $("sending").textContent = o.sending ? t("on") : t("off");
    $("outbox").innerHTML = o.rows.map(r => `<li>
      <span class="grow"><b>${esc(r.subject)}</b><br><small>${esc(r.to_addr)} · ${esc((r.created_at || "").slice(0, 16).replace("T", " "))}
        ${r.error ? ` · <span class="warn">${esc(r.error)}</span>` : ""}</small></span>
      <span class="chip ${esc(r.status)}">${t(r.status)}</span>
      <a class="btn ghost" target="_blank" rel="noopener" href="/paperwork/outbox/${r.id}">${t("preview")}</a>
      ${r.status !== "sent" ? `<button type="button" class="btn" data-send="${r.id}">${t("send")}</button>` : ""}
    </li>`).join("") || `<li class="muted">${t("noMail")}</li>`;
    $("outbox").querySelectorAll("[data-send]").forEach(b => b.onclick = () => act(b, async () => {
      const r = await api(`/api/paperwork/outbox/${b.dataset.send}/send`, "POST");
      tell(r.ok, r.message);
      await loadOutbox();
    }));
  }

  tell(true, t("loading"));
  Promise.all([loadAttention(), loadNotes(), loadOutbox()])
    .then(() => tell(true, ""))
    .catch(err => tell(false, err.message));
})();
