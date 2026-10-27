import { registerScreen, addStrings, api, t, esc, fmtDate, fmtTime } from "../core.js";
import { empty } from "../ui.js";

addStrings({
  en: { obTitle: "Outbox", obLead: "Purchase requests and customer updates prepared by confirmed decisions.", obEmpty: "No outgoing drafts yet. Confirm an owner decision to prepare one.", obDry: "Dry run · nothing sent", obTo: "To", obState: "State" },
  ru: { obTitle: "Исходящие", obLead: "Заявки на закупку и сообщения клиентам по подтверждённым решениям.", obEmpty: "Пока нет исходящих черновиков. Подтвердите решение владельца, чтобы подготовить сообщение.", obDry: "Пробный режим · ничего не отправлено", obTo: "Кому", obState: "Состояние" },
});

registerScreen({
  id: "outbox", roles: ["office"], icon: "paper", title: lang => lang === "ru" ? "Исходящие" : "Outbox",
  async render(root, ctx) {
    const r = await api("/api/owner/outbox");
    if (!ctx.isActive()) return;
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    root.innerHTML = `<header class="head"><div><h1>${t("obTitle")}</h1><p class="sub">${t("obLead")}</p></div></header><div class="stack">${r.rows.map(d => `<article class="panel stack"><p class="eyebrow">${t("obDry")}</p><h2>${esc(d.subject)}</h2><p class="muted">${t("obTo")}: ${esc(d.to_name)} · ${esc(fmtDate(d.created_at))} ${esc(fmtTime(d.created_at))}</p><p class="draft-body">${esc(d.body)}</p></article>`).join("") || empty(t("obEmpty"), "paper")}</div>`;
  },
});
