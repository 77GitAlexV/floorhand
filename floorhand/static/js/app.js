// Entry point: the shell (sidebar / bottom tabs) around whichever screen is on.
import { state, t, esc, setShell, render, go, setLang, signOut, api, toast, pref } from "./core.js";
import { icon, avatar, brandMark } from "./ui.js";
import "./screens/index.js";
import { rememberCodeFromUrl } from "./marking.js";

const shell = {
  render(root, { me, role, screen, nav, ctx }) {
    const isOffice = role === "owner" || role === "manager";
    const showNav = me && nav.length > 1;
    root.className = `shell ${!me ? "guest" : isOffice ? "office" : "floor"} ${showNav ? "has-nav" : ""}`;
    const navItem = s => `<a href="#/${s.id}" class="nav-item ${s.id === screen.id ? "on" : ""}" data-nav="${s.id}">
        ${icon(s.icon || "dot")}<span>${esc(s.title(state.lang))}</span></a>`;
    root.innerHTML = `
      ${showNav && isOffice ? `<aside class="side">
        <a class="brand" href="#/${nav[0].id}">${brandMark()}<span>Floorhand</span></a>
        <nav class="side-nav">${nav.map(navItem).join("")}</nav>
        <div class="side-foot">
          <div class="side-me">${avatar(me.name, { size: "sm" })}<div><b>${esc(me.name)}</b><small>${esc(t("role_" + me.role))}${me.shop ? ` · ${esc(me.shop)}` : ""}</small></div></div>
          <div class="side-tools">
            <button type="button" class="tool" id="lang" title="${t("lang")}">${icon("globe")}<span>${state.lang === "en" ? "RU" : "EN"}</span></button>
            <button type="button" class="tool" id="out" title="${t("signOut")}">${icon("logout")}<span>${t("signOut")}</span></button>
          </div>
        </div>
      </aside>` : ""}
      ${me && me.tablet ? `<div class="tabletbar">${icon("tablet")}<span>${t("markedBy")} <b>${esc(me.name)}</b></span>
        <button type="button" id="tablet-out" class="btn ghost-light sm">${t("signOut")}</button></div>` : ""}
      <main class="main" id="main"></main>
      ${showNav ? `<nav class="tabs">${nav.map(navItem).join("")}</nav>` : ""}`;
    root.querySelector("#lang")?.addEventListener("click", () => setLang(state.lang === "en" ? "ru" : "en"));
    root.querySelector("#out")?.addEventListener("click", signOut);
    root.querySelector("#tablet-out")?.addEventListener("click", signOut);
    // Shared tablet: sign out by itself after two minutes of silence.
    clearTimeout(shell.idle);
    if (me && me.tablet) {
      const arm = () => { clearTimeout(shell.idle); shell.idle = setTimeout(signOut, 120000); };
      ["click", "touchstart", "keydown"].forEach(ev => root.addEventListener(ev, arm, { passive: true }));
      ctx.onDispose(() => {
        clearTimeout(shell.idle);
        ["click", "touchstart", "keydown"].forEach(ev => root.removeEventListener(ev, arm));
      });
      arm();
      if (pref("tablet_device", "0") === "1") {
        let lastPing = 0;
        const ping = async () => {
          if (Date.now() - lastPing < 10000) return;
          lastPing = Date.now();
          const p = await api("/api/marking/tablet/ping", {});
          if (p.ok && !p.acting) { state.me = null; render(); }
        };
        ["click", "touchstart", "keydown"].forEach(ev => root.addEventListener(ev, ping, { passive: true }));
        ctx.onDispose(() => ["click", "touchstart", "keydown"].forEach(ev => root.removeEventListener(ev, ping)));
      }
    }
    return root.querySelector("#main");
  },
};

setShell(shell);

// Mark the whole app with the colour scheme in the browser chrome too.
const meta = document.querySelector('meta[name="theme-color"]');
const updateTheme = () => {
  const dark = state.theme === "dark" || (state.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  if (meta) meta.content = dark ? "#1b1916" : "#f4efe6";
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateTheme);
updateTheme();
addEventListener("fh:prefs", updateTheme);

// Expose for the console and other modules that do not import.
window.fh = { state, go, render, api, toast };
rememberCodeFromUrl();   // a sheet QR lands on /?code=TOKEN
render();
