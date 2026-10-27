// Every way a worker marks work, on top of /api/marking (features/marking.py):
// scan or type the sheet number, speak, the offline queue, the end-of-day check and
// "did other work" claims. Voice only proposes; nothing is recorded before [Yes].
import { addStrings, api, t, esc, state, flash, toast, idem } from "./core.js";
import { btn, chip, sheet } from "./ui.js";

addStrings({
  en: {
    mkScanTitle: "Scan or type the sheet", mkScanHint: "Point a scanner at the QR, or type the number from the sheet: 214 or 214-2.",
    mkCode: "Sheet number or code", mkMark: "Mark", mkAskNext: "Mark the next piece too?",
    mkSpeakTitle: "Say what you did", mkSpeakHint: "For example: “214 done” or “made two poufs”. Nothing is recorded until you press Yes.",
    mkListening: "Listening…", mkSend: "Send", mkUnderstood: "Understood as", mkHeard: "Heard",
    mkNoSpeech: "Speech is not available here. Type the words instead.",
    mkQueued: "No connection: saved on this phone, will send by itself.", mkWaiting: "Waiting to send: {n}",
    mkNeedsLook: "Needs a look", mkRetry: "Send again", mkDismiss: "Dismiss",
    mkDayOk: "All right", mkAddMissing: "Add missing", mkConfirmed: "Day confirmed.",
    mkOther: "Did other work", mkOtherTitle: "Other work for the manager", mkMinutes: "Minutes", mkQty: "How many",
    mkWhat: "What was it (optional)", mkSendManager: "Send to manager", mkMyClaims: "My other work",
    mkSheetOld: "About this sheet", mkForDay: "For which day", mkPhoto: "Photo of sheet",
    cl_pending: "waiting", cl_approved: "approved", cl_rejected: "rejected", cl_withdrawn: "withdrawn",
  },
  ru: {
    mkScanTitle: "Скан или номер листка", mkScanHint: "Наведи сканер на QR или введи номер с листка: 214 или 214-2.",
    mkCode: "Номер или код листка", mkMark: "Отметить", mkAskNext: "Отметить и следующую штуку?",
    mkSpeakTitle: "Скажи, что сделал", mkSpeakHint: "Например: «214 готов» или «сделал два пуфа». Ничего не запишется, пока не нажмёшь «Да».",
    mkListening: "Слушаю…", mkSend: "Отправить", mkUnderstood: "Понял так", mkHeard: "Услышал",
    mkNoSpeech: "Голос здесь недоступен. Напиши словами.",
    mkQueued: "Нет связи: сохранено в телефоне, отправится само.", mkWaiting: "Ждёт отправки: {n}",
    mkNeedsLook: "Нужно посмотреть", mkRetry: "Отправить снова", mkDismiss: "Убрать",
    mkDayOk: "Всё так", mkAddMissing: "Добавить пропущенное", mkConfirmed: "День подтверждён.",
    mkOther: "Делал другую работу", mkOtherTitle: "Другая работа — менеджеру", mkMinutes: "Минут", mkQty: "Сколько",
    mkWhat: "Что именно (необязательно)", mkSendManager: "Отправить менеджеру", mkMyClaims: "Моя другая работа",
    mkSheetOld: "Про этот листок", mkForDay: "За какой день", mkPhoto: "Фото листка",
    cl_pending: "ждёт", cl_approved: "принято", cl_rejected: "отклонено", cl_withdrawn: "отозвано",
  },
});

const PENDING = "fh_code";

/** The QR on a printed sheet opens /?code=TOKEN; keep it until a worker is signed in. */
export function rememberCodeFromUrl() {
  const code = new URLSearchParams(location.search).get("code");
  if (!code) return;
  try { sessionStorage.setItem(PENDING, code); } catch { /* private mode */ }
  history.replaceState(null, "", location.pathname + location.hash);
}
export function takePendingCode() {
  try {
    const c = sessionStorage.getItem(PENDING);
    if (c) sessionStorage.removeItem(PENDING);
    return c;
  } catch { return null; }
}

function answer(r) {
  if (r.silent) return;
  if (!r.ok) { flash(false, r.message); return; }
  flash(true, r.message, { title: r.already ? t("already") : t("recorded"), already: r.already });
  if (r.warnings && r.warnings.length) {
    // A green flash goes away by itself; an old or changed sheet must be read.
    setTimeout(() => sheet({
      title: t("mkSheetOld"), tone: "warn",
      body: r.warnings.map(w => `<p>${esc(w)}</p>`).join(""),
      actions: [{ id: "ok", label: t("ok"), kind: "primary" }],
    }), 1900);
  }
}

/** POST /api/marking/mark; answers "ask" with its own Yes / No. */
export async function mark(body, ctx) {
  const payload = { idem_key: idem(), created_at: new Date().toISOString(), ...body };
  let r = await api("/api/marking/mark", payload);
  if (!r.ok && r.offline && window.FloorhandQueue) {
    window.FloorhandQueue.add(queued(payload));
    toast(t("mkQueued"));
    return;
  }
  if (r.ok && r.status === "ask") {
    const yes = await sheet({ title: t("mkAskNext"), body: `<p>${esc(r.message)}</p>`,
      actions: [{ id: "yes", label: t("yes"), kind: "primary" }, { id: "no", label: t("no") }] });
    if (yes !== "yes") return;
    r = await api("/api/marking/mark", { ...payload, idem_key: idem(), confirm_next: true });
  }
  answer(r);
  if (r.ok && !r.already) try { sessionStorage.setItem("fh_bump", "1"); } catch { /* none */ }
  ctx?.refresh();
}

function queued(item) {
  const me = state.me;
  return me && me.tablet ? { ...item, person_id: me.id } : item;
}

/** "Scan sheet": a hardware scanner types into the field and presses Enter. */
export async function scanSheet(ctx, code) {
  if (code) return mark({ code, method: "scan" }, ctx);
  const body = `<p class="muted">${t("mkScanHint")}</p>
    <input id="mkCode" class="big-input" inputmode="text" autocomplete="off" autocapitalize="characters"
      aria-label="${t("mkCode")}" placeholder="214-2" style="width:100%;font-size:28px;min-height:56px;margin-top:8px">`;
  const p = sheet({ title: t("mkScanTitle"), body,
    actions: [{ id: "go", label: t("mkMark"), kind: "primary", icon: "check" }, { id: "no", label: t("back") }] });
  const input = document.getElementById("mkCode");
  let typed = "";
  input?.focus();
  input?.addEventListener("input", () => { typed = input.value; });
  input?.addEventListener("keydown", e => {
    if (e.key === "Enter") { typed = input.value; document.querySelector('#sheet [data-act="go"]')?.click(); }
  });
  if (await p !== "go" || !typed.trim()) return;
  const v = typed.trim();
  return mark({ code: v, method: /^[#№]?\s*\d/.test(v) ? "number" : "scan" }, ctx);
}

/** "Speak": the phone's own recognition, or typed words; the server proposes. */
export async function speak(ctx) {
  let words = "";
  const V = window.FloorhandVoice;
  if (V && V.available()) {
    toast(t("mkListening"));
    try { words = await V.listen(state.lang); } catch { words = ""; }
  }
  if (!words) {
    const p = sheet({ title: t("mkSpeakTitle"),
      body: `<p class="muted">${V && V.available() ? t("mkSpeakHint") : t("mkNoSpeech")}</p>
        <input id="mkWords" autocomplete="off" style="width:100%;font-size:20px;min-height:52px;margin-top:8px">`,
      actions: [{ id: "go", label: t("mkSend"), kind: "primary" }, { id: "no", label: t("back") }] });
    const input = document.getElementById("mkWords");
    input?.focus();
    input?.addEventListener("input", () => { words = input.value; });
    input?.addEventListener("keydown", e => { if (e.key === "Enter") document.querySelector('#sheet [data-act="go"]')?.click(); });
    if (await p !== "go" || !words.trim()) return;
  }
  const r = await api("/api/marking/voice/text", { transcript: words, created_at: new Date().toISOString() });
  return proposal(r, ctx);
}

/** A photo of the paper sheet: Spark reads it when live; offline it says "type the number". */
export function photo(ctx) {
  const input = document.createElement("input");
  input.type = "file"; input.accept = "image/*"; input.capture = "environment";
  input.onchange = async () => {
    const f = input.files && input.files[0];
    if (!f) return;
    let res;
    try {
      res = await fetch("/api/marking/photo", { method: "POST", headers: { "Content-Type": f.type || "image/jpeg" }, body: f });
    } catch { flash(false, t("cantReach")); return; }
    const data = await res.json().catch(() => ({}));
    proposal(res.ok ? { ok: true, ...data } : { ok: false, message: data.message || data.detail || res.statusText }, ctx);
  };
  input.click();
}

async function proposal(r, ctx) {
  if (r.silent) return;
  if (!r.ok) { flash(false, r.message); return; }
  if (r.status !== "proposal") { flash(false, r.message); return; }
  const items = (r.items || []).map(i =>
    `<li class="${i.type === "problem" ? "muted" : ""}">${esc(i.say)}</li>`).join("");
  const yes = await sheet({ title: t("mkUnderstood"), body: `<ul class="plain">${items}</ul>`,
    actions: [{ id: "yes", label: t("yes"), kind: "primary" }, { id: "no", label: t("no") }] });
  if (yes === null) return;
  const d = await api(`/api/marking/proposals/${r.id}/decide`, { yes: yes === "yes" });
  if (d.silent) return;
  if (!d.ok) { flash(false, d.message); return; }
  if (yes === "yes") { flash(true, d.message, { already: d.status === "already" }); ctx?.refresh(); }
  else toast(d.message);
}

/** End-of-day plaque from the server (from 16:00 shop time, until confirmed). */
export async function dayCheck() {
  const r = await api("/api/marking/day-check");
  return r.ok && r.due && !r.confirmed && (r.pieces > 0 || (r.claims || []).length) ? r : null;
}
export async function confirmDay(ctx, day) {
  const r = await api("/api/marking/day-check/confirm", { day });
  if (r.silent) return;
  if (r.ok) toast(r.message || t("mkConfirmed")); else flash(false, r.message);
  ctx?.refresh();
}

/** A missed piece: number entry with a day picker, today and up to N days back. */
export async function addMissing(ctx, backDays = 3) {
  const days = [...Array(backDays + 1).keys()].map(n => {
    const d = new Date(); d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
  });
  let day = days[0];
  const p = sheet({ title: t("mkAddMissing"),
    body: `<p class="muted">${t("mkForDay")}</p><div class="chips" id="mkDays">${days.map((d, i) =>
      chip(i === 0 ? t("today") : d.slice(5), { on: i === 0, attrs: `data-day="${d}"` })).join("")}</div>
      <input id="mkCode2" autocomplete="off" placeholder="214-2" style="width:100%;font-size:28px;min-height:56px;margin-top:12px">`,
    actions: [{ id: "go", label: t("mkMark"), kind: "primary" }, { id: "no", label: t("back") }] });
  let code = "";
  document.querySelectorAll("#mkDays [data-day]").forEach(b => b.onclick = () => {
    day = b.dataset.day;
    document.querySelectorAll("#mkDays [data-day]").forEach(x => x.classList.toggle("on", x === b));
  });
  const input = document.getElementById("mkCode2");
  input?.addEventListener("input", () => { code = input.value; });
  if (await p !== "go" || !code.trim()) return;
  return mark({ code: code.trim(), method: "number", for_date: day === days[0] ? undefined : day }, ctx);
}

/** "Did other work": presets, minutes or quantity, a description; the manager approves. */
export async function otherWork(ctx) {
  const c = await api("/api/marking/claims");
  if (!c.ok) { if (!c.silent) flash(false, c.message); return; }
  let pick = null;
  const mine = (c.claims || []).slice(0, 5).map(x => `<li><span>${esc(x.description)} · ${esc(x.work_date)}</span>
    <span class="muted">${t("cl_" + x.status)}${x.decision_note ? ` — ${esc(x.decision_note)}` : ""}</span></li>`).join("");
  const p = sheet({ title: t("mkOtherTitle"),
    body: `<div class="chips" id="mkPresets">${(c.presets || []).map(x => chip(esc(x.name), { attrs: `data-preset="${x.id}" data-min="${x.by_minutes ? 1 : 0}"` })).join("")}</div>
      <label class="muted" id="mkNLabel" style="display:block;margin-top:12px">${t("mkMinutes")}</label>
      <input id="mkN" type="number" min="1" value="30" style="width:100%;font-size:22px;min-height:52px">
      <label class="muted" style="display:block;margin-top:12px">${t("mkWhat")}</label>
      <input id="mkDesc" autocomplete="off" style="width:100%;font-size:18px;min-height:48px">
      ${mine ? `<h3 class="sec">${t("mkMyClaims")}</h3><ul class="plain">${mine}</ul>` : ""}`,
    actions: [{ id: "go", label: t("mkSendManager"), kind: "primary" }, { id: "no", label: t("back") }] });
  let byMin = true;
  const vals = { n: 30, desc: "" };
  document.querySelectorAll("#mkPresets [data-preset]").forEach(b => b.onclick = () => {
    pick = Number(b.dataset.preset); byMin = b.dataset.min === "1";
    document.getElementById("mkNLabel").textContent = byMin ? t("mkMinutes") : t("mkQty");
    document.querySelectorAll("#mkPresets [data-preset]").forEach(x => x.classList.toggle("on", x === b));
  });
  document.getElementById("mkN")?.addEventListener("input", e => { vals.n = Number(e.target.value) || 0; });
  document.getElementById("mkDesc")?.addEventListener("input", e => { vals.desc = e.target.value; });
  if (await p !== "go") return;
  const body = { preset_id: pick, description: vals.desc, idem_key: idem() };
  if (byMin) body.minutes = vals.n; else body.qty = vals.n;
  const r = await api("/api/marking/claims", body);
  if (r.silent) return;
  if (r.ok) toast(r.message); else flash(false, r.message);
  ctx?.refresh();
}

/** "Waiting to send: N" and the marks the server refused while offline. */
export function queueBar(host) {
  const Q = window.FloorhandQueue;
  if (!Q || !host) return;
  const draw = s => {
    if (!host.isConnected) return;
    host.innerHTML = (s.waiting ? `<p class="pill warn">${t("mkWaiting", { n: s.waiting })}</p>` : "") +
      (s.attention.length ? `<h2 class="sec">${t("mkNeedsLook")} <b>${s.attention.length}</b></h2><ul class="plain">${s.attention.map(a =>
        `<li><span>${esc(a.message)}</span><span>${btn({ label: t("mkRetry"), kind: "secondary", attrs: `data-retry="${a.item.idem_key}"` })}
        ${btn({ label: t("mkDismiss"), kind: "ghost", attrs: `data-dismiss="${a.item.idem_key}"` })}</span></li>`).join("")}</ul>` : "");
    host.querySelectorAll("[data-retry]").forEach(b => b.onclick = () => Q.retry(b.dataset.retry));
    host.querySelectorAll("[data-dismiss]").forEach(b => b.onclick = () => Q.dismiss(b.dataset.dismiss));
  };
  const off = Q.onChange(draw);
  const stop = new MutationObserver(() => { if (!host.isConnected) { off(); stop.disconnect(); } });
  stop.observe(document.body, { childList: true, subtree: true });
}
