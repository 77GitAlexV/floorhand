// Persistent device mode signs the office out before offering worker names.
import { registerScreen, addStrings, api, t, esc, name, state, pref, setPref, render, flash } from "../core.js";
import { btn, sheet, avatar, empty } from "../ui.js";

addStrings({
  en: { tbTitle: "Shop tablet", tbLead: "Set up this device for workers. The office session will end; money stays hidden.", tbName: "Device name", tbStage: "Stage", tbAll: "All stages", tbSetup: "Set up this device", tbConfirm: "Make this the shop tablet?", tbChoose: "Who is working next?", tbLeave: "Return to demo sign-in", tbReady: "This device is now the shop tablet.", tbAct: "Marking as {name}." },
  ru: { tbTitle: "Планшет цеха", tbLead: "Настройте устройство для рабочих. Сеанс офиса завершится; деньги скрыты.", tbName: "Название устройства", tbStage: "Этап", tbAll: "Все этапы", tbSetup: "Настроить устройство", tbConfirm: "Сделать это устройство планшетом цеха?", tbChoose: "Кто работает следующим?", tbLeave: "Вернуться к демо-входу", tbReady: "Устройство настроено как планшет цеха.", tbAct: "Отметки от имени: {name}." },
});

registerScreen({
  id: "tablet", roles: ["office"], icon: "tablet", title: lang => lang === "ru" ? "Планшет цеха" : "Shop tablet",
  async render(root, ctx) {
    const r = await api("/api/orders/catalog/lists");
    if (!ctx.isActive()) return;
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    root.innerHTML = `<header class="head"><div><h1>${t("tbTitle")}</h1><p class="sub">${t("tbLead")}</p></div></header><section class="panel stack"><label for="tabletName">${t("tbName")}</label><input id="tabletName" class="input" maxlength="100" value="Floorhand"><label for="tabletStage">${t("tbStage")}</label><select id="tabletStage" class="input"><option value="">${t("tbAll")}</option>${r.stages.map(s => `<option value="${s.id}">${esc(name(s))}</option>`).join("")}</select>${btn({ label: t("tbSetup"), kind: "primary", attrs: 'id="setupTablet"' })}</section>`;
    root.querySelector("#setupTablet").onclick = async () => {
      if (await sheet({ title: t("tbConfirm"), body: t("tbLead"), actions: [{ id: "yes", label: t("yes"), kind: "primary" }, { id: "no", label: t("back") }] }) !== "yes") return;
      const r = await api("/api/marking/tablet/setup", { name: root.querySelector("#tabletName").value, stage_id: Number(root.querySelector("#tabletStage").value) || null });
      if (!r.ok) { if (!r.silent) flash(false, r.message); return; }
      setPref("tablet_device", "1"); state.me = null; location.hash = ""; flash(true, t("tbReady")); render();
    };
  },
});

export async function tabletLogin(root) {
  if (pref("tablet_device", "0") !== "1") return false;
  let r, response;
  try { response = await fetch("/api/marking/tablet"); r = await response.json(); }
  catch { root.innerHTML = empty(t("cantReach"), "alert"); return true; }
  if (!response.ok) {
    if (response.status === 401) { setPref("tablet_device", "0"); return false; }
    root.innerHTML = empty(r.message || r.detail, "alert"); return true;
  }
  root.innerHTML = `<header class="head"><div><p class="eyebrow">${esc(r.name)}</p><h1>${t("tbChoose")}</h1><p class="sub">${t("tablet")}</p></div></header><div class="tiles">${r.people.map(p => `<button type="button" class="tile" data-tablet-person="${p.id}">${avatar(p.name)}<span>${esc(p.name)}<small>${esc(state.lang === "ru" ? p.stage_ru : p.stage)}</small></span></button>`).join("")}</div>${btn({ label: t("tbLeave"), kind: "ghost", attrs: 'id="tabletDemo"' })}`;
  root.querySelectorAll("[data-tablet-person]").forEach(b => b.onclick = async () => {
    const p = r.people.find(p => p.id === Number(b.dataset.tabletPerson));
    if (await sheet({ title: t("startShift", { name: p.name }), body: t("tablet"), actions: [{ id: "yes", label: t("start"), kind: "primary" }, { id: "no", label: t("back") }] }) !== "yes") return;
    const d = await api("/api/marking/tablet/act", { person_id: p.id });
    if (!d.ok) { if (!d.silent) flash(false, d.message); return; }
    state.me = null; location.hash = "#/work"; flash(true, t("tbAct", { name: p.name })); render();
  });
  root.querySelector("#tabletDemo").onclick = async e => {
    e.currentTarget.disabled = true;
    const d = await api("/api/marking/tablet/leave", {});
    if (!d.ok) { if (!d.silent) flash(false, d.message); e.currentTarget.disabled = false; return; }
    setPref("tablet_device", "0"); state.me = null; render();
  };
  return true;
}
