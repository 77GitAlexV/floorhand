// Floorhand UI components. Every function returns an HTML string unless it says
// otherwise; screens bind handlers after inserting. Icons are inline SVG, 24px grid.
import { esc, name, t, state, money, fmtDate } from "./core.js";

// --- icons ------------------------------------------------------------------------
const PATHS = {
  check: "M5 12.5 10 17.5 19 7",
  plus: "M12 5v14M5 12h14",
  undo: "M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3",
  scan: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M3 12h18",
  mic: "M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zM6 11a6 6 0 0 0 12 0M12 17v4M9 21h6",
  work: "M3 20h18M5 20V9l7-5 7 5v11M10 20v-6h4v6",
  money: "M3 7h18v10H3zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 10h.01M18 14h.01",
  me: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  floor: "M3 21V11l5-3v4l5-3v4l5-3v11zM7 17h2M11 17h2M15 17h2",
  orders: "M8 4h8l3 3v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM9 10h6M9 14h6M9 18h3",
  shipping: "M3 7h11v9H3zM14 10h4l3 3v3h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  today: "M4 5h16v15H4zM4 10h16M8 3v4M16 3v4",
  print: "M6 9V3h12v6M6 17H4a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-2M6 14h12v7H6z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
  back: "M15 5l-7 7 7 7",
  chevron: "M9 6l6 6-6 6",
  down: "M6 9l6 6 6-6",
  alert: "M12 3 2 20h20zM12 10v4M12 17h.01",
  flame: "M12 22c4 0 7-3 7-7 0-3-2-5-3-7-1 3-2 4-3 4 0-3-1-6-4-9 0 4-4 6-4 11 0 4 3 8 7 8z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  edit: "M4 20h4l11-11-4-4L4 16zM13 7l4 4",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
  logout: "M10 17l5-5-5-5M15 12H4M14 4h5v16h-5",
  tablet: "M5 3h14v18H5zM12 18h.01",
  spark: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
  box: "M3 8l9-5 9 5v8l-9 5-9-5zM3 8l9 5 9-5M12 13v8",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  users: "M9 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-5-6.7",
  paper: "M7 3h7l4 4v14H7zM14 3v4h4M10 12h6M10 16h6",
  camera: "M4 8h3l2-3h6l2 3h3v12H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  truck: "M3 7h11v9H3zM14 10h4l3 3v3h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  cash: "M3 7h18v10H3zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  x: "M6 6l12 12M18 6 6 18",
  minus: "M5 12h14",
  dot: "M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0",
  question: "M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7v.5M12 17h.01M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  file: "M14 3H7v18h10V6zM14 3v3h3M10 13h4",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
};
export function icon(nm, cls = "") {
  const d = PATHS[nm] || PATHS.dot;
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
}

/** Three stacked boards: the floor. Same mark as the favicon. */
export function brandMark() {
  return `<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
    <rect x="4" y="6" width="24" height="5" rx="1.5"/>
    <rect x="4" y="13.5" width="24" height="5" rx="1.5" opacity=".75"/>
    <rect x="4" y="21" width="15" height="5" rx="1.5" opacity=".5"/></svg>`;
}

// --- buttons -------------------------------------------------------------------------
/** btn({ label, icon, kind: "primary"|"secondary"|"ghost"|"danger"|"hero", size: "md"|"lg", attrs }) */
export function btn({ label, icon: ic, kind = "secondary", size = "md", attrs = "", cls = "", disabled = false }) {
  return `<button type="button" class="btn ${kind} ${size} ${cls}" ${attrs} ${disabled ? "disabled" : ""}>${ic ? icon(ic) : ""}<span>${label}</span></button>`;
}

// --- small things ------------------------------------------------------------------
export function chip(label, { on = false, kind = "", attrs = "" } = {}) {
  return `<button type="button" class="chip ${kind} ${on ? "on" : ""}" ${attrs}>${label}</button>`;
}
export function pill(label, kind = "") { return `<span class="pill ${kind}">${label}</span>`; }
export function empty(text, ic = "box") {
  return `<div class="empty">${icon(ic)}<p>${esc(text)}</p></div>`;
}
export function sectionTitle(text, count, extra = "") {
  return `<h2 class="sec">${esc(text)}${count !== undefined ? ` <b>${count}</b>` : ""}${extra}</h2>`;
}
export function stat(label, value, { kind = "", sub = "" } = {}) {
  return `<div class="stat ${kind}"><span class="stat-v">${value}</span><span class="stat-l">${esc(label)}</span>${sub ? `<span class="stat-s">${sub}</span>` : ""}</div>`;
}
/** Progress ring in SVG (0..1). */
export function ring(frac, { size = 44, stroke = 5, cls = "" } = {}) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, frac));
  return `<svg class="ring ${cls}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" class="ring-bg" stroke-width="${stroke}"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" class="ring-fg" stroke-width="${stroke}"
      stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - f)).toFixed(1)}"
      transform="rotate(-90 ${size / 2} ${size / 2})"/></svg>`;
}
export function bar(frac, cls = "") {
  return `<span class="bar ${cls}"><i style="width:${Math.round(100 * Math.max(0, Math.min(1, frac)))}%"></i></span>`;
}

/** The stage line: the product's signature. steps = [{name_en,name_ru,state:done|active|todo,done,here}] */
export function stageLine(steps, { compact = false, qty = 1 } = {}) {
  return `<span class="stageline ${compact ? "compact" : ""}">${steps.map(s => `
    <span class="st ${s.state}" title="${esc(name(s))}: ${s.done}/${qty}">
      <i class="st-dot">${s.state === "done" ? icon("check") : ""}</i>
      <span class="st-name">${esc(name(s))}${s.state === "active" && s.here > 0 && qty > 1 ? ` <b>${s.here}</b>` : ""}</span>
    </span>`).join("")}</span>`;
}

export function statusPill(status) {
  return pill(t("s_" + status), "s-" + status);
}

/** Due cell: late in red with days; to stock in muted. */
export function dueText(o) {
  if (!o.customer) return `<span class="muted">${t("toStock")}</span>`;
  if (o.late_days) return `<span class="late">${icon("alert")} ${fmtDate(o.due_date)} · ${t("lateBy", { n: o.late_days })}</span>`;
  return esc(fmtDate(o.due_date));
}

export function avatar(nm, { size = "md", cls = "" } = {}) {
  const initials = String(nm || "?").split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const hue = [...String(nm)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return `<span class="avatar ${size} ${cls}" style="--h:${hue}">${esc(initials)}</span>`;
}

// --- table -----------------------------------------------------------------------
/** table({ cols: [{label, cls}], rows: [{attrs, cells: [html,...]}], empty }) */
export function table({ cols, rows, emptyText, cls = "" }) {
  if (!rows.length) return empty(emptyText || t("nothing"));
  return `<div class="table-wrap"><table class="tbl ${cls}">
    <thead><tr>${cols.map(c => `<th class="${c.cls || ""}">${c.label}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr ${r.attrs || ""} class="${r.cls || ""}">${r.cells.map((c, i) =>
      `<td class="${cols[i].cls || ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// --- sheet (modal) ----------------------------------------------------------------
/**
 * Own dialog instead of the browser's: big Yes / No. Returns a promise of the pressed action id.
 * sheet({ title, body, actions: [{id:"yes", label, kind:"primary"}, {id:"no", label}] })
 */
export function sheet({ title, body = "", actions = [], tone = "" }) {
  return new Promise(resolve => {
    const host = document.getElementById("sheet");
    host.innerHTML = `<div class="sheet-back"></div>
      <div class="sheet ${tone}" role="dialog" aria-modal="true">
        <div class="sheet-grip"></div>
        ${title ? `<h2 class="sheet-title">${title}</h2>` : ""}
        <div class="sheet-body">${body}</div>
        <div class="sheet-actions">${actions.map(a =>
          btn({ label: a.label, icon: a.icon, kind: a.kind || "secondary", size: "lg", attrs: `data-act="${a.id}"` })).join("")}</div>
      </div>`;
    host.hidden = false;
    requestAnimationFrame(() => host.classList.add("in"));
    const done = id => {
      host.classList.remove("in");
      setTimeout(() => { host.hidden = true; host.innerHTML = ""; }, 200);
      resolve(id);
    };
    host.querySelector(".sheet-back").onclick = () => done(null);
    host.querySelectorAll("[data-act]").forEach(b => b.onclick = () => done(b.dataset.act));
    const onKey = e => { if (e.key === "Escape") { removeEventListener("keydown", onKey); done(null); } };
    addEventListener("keydown", onKey);
  });
}

/** Placeholder for a feature another module is building: a disabled-looking but live entry point. */
export function comingSoon(label, ic) {
  return btn({ label, icon: ic, kind: "ghost", attrs: `data-soon title="${esc(t("soon"))}"` });
}
export function bindSoon(root, toastFn) {
  root.querySelectorAll("[data-soon]").forEach(b => b.onclick = () => toastFn(t("soon")));
}

export const fmt = { money, date: fmtDate };
export { state };
