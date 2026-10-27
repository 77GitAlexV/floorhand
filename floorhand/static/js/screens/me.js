// "Me": name, language, text size, theme, sign out. Nothing a worker must learn.
import { registerScreen, addStrings, t, esc, state, setPref, setLang, render, signOut, toast, api, flash } from "../core.js";
import { icon, avatar, btn, bindSoon, sheet } from "../ui.js";

addStrings({
  en: { me: "Me", textSize: "Text size", theme: "Theme", th_auto: "Auto", th_light: "Light", th_dark: "Dark",
        askMaterial: "Ask for material", materialWords: "Which material is missing?", materialSend: "Send request", version: "Floorhand · demo build" },
  ru: { me: "Я", textSize: "Размер текста", theme: "Тема", th_auto: "Авто", th_light: "Светлая", th_dark: "Тёмная",
        askMaterial: "Попросить материал", materialWords: "Какого материала не хватает?", materialSend: "Отправить запрос", version: "Floorhand · демо-сборка" },
});

registerScreen({
  id: "me", roles: ["worker", "tablet", "office"], icon: "me", title: lang => lang === "ru" ? "Я" : "Me",
  render(root, { me }) {
    const seg = (id, opts, cur) => `<div class="seg" id="${id}">${opts.map(([k, l]) =>
      `<button type="button" class="${cur === k ? "on" : ""}" data-v="${k}">${l}</button>`).join("")}</div>`;
    root.innerHTML = `
      <div class="me-head">${avatar(me.name, { size: "lg" })}<div><b>${esc(me.name)}</b><span>${esc(t("role_" + me.role))}${me.shop ? ` · ${esc(me.shop)}` : ""}</span></div></div>
      <div class="pref"><span class="lbl">${t("lang")}</span>${seg("lang", [["en", "English"], ["ru", "Русский"]], state.lang)}</div>
      <div class="pref"><span class="lbl">${t("textSize")}</span>${seg("size", [["a", "A"], ["b", "A+"], ["c", "A++"]], state.size)}</div>
      <div class="pref"><span class="lbl">${t("theme")}</span>${seg("theme", [["auto", t("th_auto")], ["light", t("th_light")], ["dark", t("th_dark")]], state.theme)}</div>
      ${me.role === "worker" ? `<div class="pref"><span class="lbl">${t("askMaterial")}</span>${btn({ label: t("materialSend"), icon: "box", attrs: 'id="materialRequest"' })}</div>` : ""}
      <div class="pref"><span class="lbl"></span>${btn({ label: t("signOut"), icon: "logout", kind: "secondary", size: "lg", attrs: 'id="out"' })}</div>
      <p class="muted" style="margin-top:24px">${t("version")}</p>`;
    root.querySelector("#lang").onclick = e => { const v = e.target.closest("[data-v]")?.dataset.v; if (v) setLang(v); };
    root.querySelector("#size").onclick = e => { const v = e.target.closest("[data-v]")?.dataset.v; if (v) { state.size = v; setPref("size", v); render(); } };
    root.querySelector("#theme").onclick = e => { const v = e.target.closest("[data-v]")?.dataset.v; if (v) { state.theme = v; setPref("theme", v); dispatchEvent(new Event("fh:prefs")); render(); } };
    root.querySelector("#out").onclick = signOut;
    root.querySelector("#materialRequest")?.addEventListener("click", async () => {
      let text = "";
      const p = sheet({ title: t("askMaterial"), body: `<label for="materialWords">${t("materialWords")}</label><input id="materialWords" class="input" maxlength="300">`, actions: [{ id: "yes", label: t("materialSend"), kind: "primary" }, { id: "no", label: t("back") }] });
      document.getElementById("materialWords").oninput = e => { text = e.target.value; };
      if (await p !== "yes") return;
      if (!text.trim()) { flash(false, t("materialWords")); return; }
      const r = await api("/api/marking/material", { text });
      if (!r.silent) flash(r.ok, r.message);
    });
    bindSoon(root, toast);
  },
});
