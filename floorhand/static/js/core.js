// Floorhand front-end core: api, state, i18n, router by role, answer overlay.
// No build step: plain ES modules. Screens live in ./screens/*.js and register
// themselves through registerScreen(); see static/README.md.

export const state = {
  me: null,            // /api/me or null when signed out
  screen: null,        // current screen id
  params: {},          // route params, e.g. { id: 214 }
  lang: pref("lang", "en"),   // English for the demo; Russian via the switch
  size: pref("size", "a"),
  theme: pref("theme", "auto"),
};

export function pref(key, fallback) {
  try { return localStorage.getItem("fh_" + key) || fallback; } catch { return fallback; }
}
export function setPref(key, value) {
  try { localStorage.setItem("fh_" + key, value); } catch { /* private mode */ }
}

// --- i18n -------------------------------------------------------------------
const STRINGS = { en: {}, ru: {} };

/** Each screen adds its own strings: addStrings({ en: {...}, ru: {...} }). */
export function addStrings(bundle) {
  for (const lang of Object.keys(bundle)) Object.assign(STRINGS[lang] ||= {}, bundle[lang]);
}
export function t(key, vars) {
  let s = STRINGS[state.lang]?.[key] ?? STRINGS.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}
/** Objects from the API carry name_en / name_ru. */
export function name(o) {
  if (!o) return "";
  return (state.lang === "ru" ? o.name_ru : o.name_en) || o.name_en || o.name_ru || "";
}
export function setLang(lang) {
  state.lang = lang; setPref("lang", lang); render();
}

// --- formatting ---------------------------------------------------------------
export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
export function money(cur, n) {
  if (n === null || n === undefined) return "";
  const num = Number(n).toLocaleString(state.lang === "ru" ? "ru-RU" : "en-US");
  return `${cur}${num}`;
}
const MONTHS = {
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
  ru: ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
};
const DAYS = {
  en: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
  ru: ["вс", "пн", "вт", "ср", "чт", "пт", "сб"],
};
/** "2026-10-27" -> "27 Oct"; withDay -> "Tue 27 Oct". */
export function fmtDate(iso, { withDay = false, withYear = false } = {}) {
  if (!iso) return "—";
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  const L = state.lang === "ru" ? "ru" : "en";
  let s = `${d.getDate()} ${MONTHS[L][d.getMonth()]}`;
  if (withYear) s += ` ${d.getFullYear()}`;
  if (withDay) s = `${DAYS[L][d.getDay()]} ${s}`;
  return s;
}
export function fmtTime(iso) { return iso ? iso.slice(11, 16) : ""; }
/** "1 pc", "6 pcs", "6 шт". */
export function pcs(n) { return `${n} ${state.lang === "ru" ? "шт" : (Number(n) === 1 ? "pc" : "pcs")}`; }
export function idem() {
  return (crypto.randomUUID && crypto.randomUUID()) ||
    Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// --- api ----------------------------------------------------------------------
export async function api(path, body, { method } = {}) {
  let res;
  try {
    res = await fetch(path, body === undefined && !method ? {} : {
      method: method || "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    return { ok: false, message: t("cantReach") };
  }
  if (res.status === 401) {  // session gone: back to the board (unless we were only checking)
    const had = state.me !== null;
    state.me = null;
    if (had) render();
    return { ok: false, silent: true };
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, message: data.message || data.detail || res.statusText };
  return Array.isArray(data) ? { ok: true, list: data } : { ok: true, ...data };
}

// --- answers: every action answers in words --------------------------------------
let audio;
export function beep(kind) {
  try {
    const ctx = audio || (audio = new AudioContext());
    if (ctx.state === "suspended") ctx.resume();
    const t0 = ctx.currentTime;
    const tone = (freq, start, len, gain = 0.12, type = "sine") => {
      const o = ctx.createOscillator(); const g = ctx.createGain();
      o.type = type; o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t0 + start);
      g.gain.exponentialRampToValueAtTime(gain, t0 + start + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + start + len);
      o.connect(g).connect(ctx.destination); o.start(t0 + start); o.stop(t0 + start + len + 0.02);
    };
    // Two soft notes, like a small affirmative instrument - not a cash register.
    if (kind === "good") { tone(660, 0, 0.09); tone(880, 0.11, 0.15); }
    else if (kind === "soft") { tone(880, 0, 0.08, 0.06); }
    else { tone(180, 0, 0.35, 0.14, "square"); }
  } catch { /* no audio */ }
}
export function haptic(kind) {
  try { navigator.vibrate?.(kind === "good" ? [35, 45, 70] : kind === "soft" ? [20] : [90, 60, 90]); } catch { /* none */ }
}

/**
 * Full-screen answer. Green with a big check for success (goes away by itself),
 * red for a refusal (stays until the person reads it).
 * flash(true, "Recorded: #214, Upholstery, $12", { sub: "Today 7 pcs" })
 */
export function flash(ok, text, { title = "", sub = "", big = "", already = false } = {}) {
  const box = document.getElementById("answer");
  box.className = ok ? (already ? "good already" : "good") : "bad";
  box.innerHTML = `
    <div class="answer-inner">
      <svg class="answer-mark" viewBox="0 0 96 96" aria-hidden="true">
        <circle cx="48" cy="48" r="42" class="ring"/>
        ${ok
          ? `<path class="tick" d="M28 50 L42 64 L70 34"/>`
          : `<path class="cross" d="M34 34 L62 62 M62 34 L34 62"/>`}
      </svg>
      ${title ? `<p class="answer-title">${esc(title)}</p>` : ""}
      <p class="answer-text">${esc(text)}</p>
      ${big ? `<p class="answer-money">${esc(big)}</p>` : ""}
      ${sub ? `<p class="answer-sub">${esc(sub)}</p>` : ""}
      ${ok ? "" : `<button type="button" class="btn ghost-light" id="answer-close">${t("ok")}</button>`}
    </div>`;
  box.hidden = false;
  requestAnimationFrame(() => box.classList.add("in"));
  beep(ok ? (already ? "soft" : "good") : "bad");
  haptic(ok ? (already ? "soft" : "good") : "bad");
  clearTimeout(flash.timer);
  const close = () => { box.classList.remove("in"); setTimeout(() => { box.hidden = true; }, 180); };
  box.onclick = close;
  if (ok) flash.timer = setTimeout(close, already ? 1400 : 1800);
}

/** Small, quiet note at the bottom: "Taken 1", "List updated". */
export function toast(text, { kind = "" } = {}) {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast " + kind; el.textContent = text;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(() => { el.classList.remove("in"); setTimeout(() => el.remove(), 250); }, 2200);
}

// --- screens & routing -------------------------------------------------------------
// A screen: { id, roles: ["worker"|"office"|"owner"|"manager"|"tablet"], icon, title(lang),
//             nav: true|false, render(root, ctx) }
const SCREENS = new Map();
export function registerScreen(screen) { SCREENS.set(screen.id, screen); }
export function screenList() { return [...SCREENS.values()]; }

export function roleOf(me) {
  if (!me) return null;
  if (me.stage_id !== null && me.stage_id !== undefined) return me.tablet ? "tablet" : "worker";
  return me.role;  // "owner" | "manager"
}
function allowed(screen, me) {
  const role = roleOf(me);
  return screen.roles.includes(role) ||
    (screen.roles.includes("office") && (role === "owner" || role === "manager"));
}
export function navFor(me) {
  return screenList().filter(s => s.nav !== false && allowed(s, me));
}

/** go("orders", { id: 214 }) -> #/orders/214 */
export function go(id, params = {}) {
  const parts = [id, ...Object.values(params).filter(v => v !== undefined && v !== null)];
  const hash = "#/" + parts.map(encodeURIComponent).join("/");
  if (location.hash === hash) render(); else location.hash = hash;
}
function parseHash() {
  const m = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const id = m[0] || null;
  const screen = id && SCREENS.get(id);
  const params = {};
  if (screen && screen.params) screen.params.forEach((k, i) => { if (m[i + 1] !== undefined) params[k] = decodeURIComponent(m[i + 1]); });
  return { id, params };
}
addEventListener("hashchange", () => render());

export async function signOut() {
  await api("/api/logout", {});
  state.me = null; location.hash = ""; render();
}
export async function refreshMe() {
  const me = await api("/api/me");
  state.me = me.ok ? me : null;
  return state.me;
}

// --- rendering -------------------------------------------------------------------
let shell = null;     // set by app.js: shell.render(root, { me, screen, nav })
let renderSeq = 0;
export function setShell(s) { shell = s; }
export function applyPrefs() {
  const html = document.documentElement;
  html.dataset.size = state.size;
  html.lang = state.lang;
  if (state.theme === "auto") delete html.dataset.theme; else html.dataset.theme = state.theme;
}

export async function render() {
  const seq = ++renderSeq;
  applyPrefs();
  if (!state.me) await refreshMe();
  if (seq !== renderSeq) return;
  const root = document.getElementById("app");
  const me = state.me;
  const role = roleOf(me);
  document.body.dataset.role = role || "guest";
  const nav = me ? navFor(me) : [];
  let { id, params } = parseHash();
  let screen = id && SCREENS.get(id);
  if (!me) screen = SCREENS.get("login");
  else if (!screen || !allowed(screen, me)) screen = nav[0];
  state.screen = screen.id; state.params = params;
  document.title = `${screen.title ? screen.title(state.lang) : "Floorhand"} · Floorhand`;
  const ctx = { me, role, params, go, refresh: () => render(), t, lang: state.lang };
  const main = shell.render(root, { me, role, screen, nav, ctx });
  main.classList.add("screen-enter");
  await screen.render(main, ctx);
  if (seq !== renderSeq) return;
  requestAnimationFrame(() => main.classList.remove("screen-enter"));
}

addStrings({
  en: {
    ok: "OK", cantReach: "No connection. Try again.", soon: "Coming in the next build.",
    signOut: "Sign out", back: "Back", more: "More", close: "Close", yes: "Yes", no: "No",
    pcs: "pc(s)", pc: "pc", of: "of", today: "Today", search: "Search",
    nothing: "Nothing here yet.", loading: "Loading…", fact: "FACT", calc: "CALC", est: "ESTIMATE",
    toStock: "to stock", due: "due", lang: "Language",
  },
  ru: {
    ok: "OK", cantReach: "Нет связи. Попробуй ещё раз.", soon: "Будет в следующей сборке.",
    signOut: "Выйти", back: "Назад", more: "Ещё", close: "Закрыть", yes: "Да", no: "Нет",
    pcs: "шт", pc: "шт", of: "из", today: "Сегодня", search: "Поиск",
    nothing: "Пока пусто.", loading: "Загрузка…", fact: "ФАКТ", calc: "РАСЧЁТ", est: "ОЦЕНКА",
    toStock: "на склад", due: "отдать к", lang: "Язык",
  },
});
