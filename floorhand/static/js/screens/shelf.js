// Normalize phone photos to oriented PNG so the dependency-free offline counter works.
import { registerScreen, addStrings, api, apiFetch, t, esc, state, idem, flash, toast } from "../core.js";
import { btn, empty, icon } from "../ui.js";

addStrings({
  en: {
    shTitle: "Shelf", shLead: "A photo. A count. Your decision.", shItem: "Stock item", shPrompt: "What should we count?", shTake: "Take a photo", shUpload: "Upload a photo",
    shHint: "Separate the parts on a plain background. Keep every part in view.", shPhoto: "Take or upload a photo",
    shCount: "Count the photo", shCounting: "Counting…", shEmpty: "Your photo and detection boxes will appear here.",
    shProposal: "Set stock of {item} from {before} to {after}?", shCounter: "Counter used", shOffline: "Offline counter",
    shReview: "Review every box before choosing Yes. The count is an estimate.", shConfidence: "Confidence (uncalibrated for offline counts)",
    shInvalid: "Choose a readable PNG or JPEG photo. Try a smaller image if decoding fails.", shNeed: "Choose a photo and stock item, and describe the parts first.",
    shNoItems: "No shelf items yet. Ask the office to seed the demo stock.", shClosed: "This decision is recorded. Take another photo for a new count.",
    shBoxes: "Detection boxes", shMax: "Photos are resized on this device to CALC: at most 1600 pixels on the longest side.", shReady: "Photo ready to count.",
  },
  ru: {
    shTitle: "Полка", shLead: "Фото. Подсчёт. Ваше решение.", shItem: "Складская позиция", shPrompt: "Что считаем?", shTake: "Снять фото", shUpload: "Загрузить фото",
    shHint: "Разложите детали отдельно на однотонном фоне. Все детали должны быть видны.", shPhoto: "Снять или загрузить фото",
    shCount: "Посчитать на фото", shCounting: "Считаю…", shEmpty: "Здесь появятся фото и рамки найденных деталей.",
    shProposal: "Изменить остаток «{item}» с {before} на {after}?", shCounter: "Использован счётчик", shOffline: "Офлайн-счётчик",
    shReview: "Проверьте каждую рамку перед нажатием «Да». Количество — оценка.", shConfidence: "Уверенность (для офлайн-подсчёта не калибрована)",
    shInvalid: "Выберите читаемое фото PNG или JPEG. Если оно не открывается, попробуйте уменьшить его.", shNeed: "Сначала выберите фото, складскую позицию и опишите детали.",
    shNoItems: "Пока нет складских позиций. Попросите офис загрузить демо-запасы.", shClosed: "Решение записано. Для нового подсчёта сделайте другое фото.",
    shBoxes: "Рамки найденных деталей", shMax: "Фото уменьшается на устройстве: РАСЧЁТ — до 1600 пикселей по длинной стороне.", shReady: "Фото готово к подсчёту.",
  },
});

async function normalize(file) {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  try {
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const c = canvas.getContext("2d"); c.fillStyle = "white"; c.fillRect(0, 0, canvas.width, canvas.height); c.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error()), "image/png"));
  } finally { bitmap.close(); }
}

registerScreen({
  id: "shelf", roles: ["office"], icon: "camera", title: lang => lang === "ru" ? "Полка" : "Shelf",
  async render(root, ctx) {
    const r = await api("/api/shelf/items");
    if (!ctx.isActive()) return;
    if (!r.ok) { if (!r.silent) root.innerHTML = empty(r.message, "alert"); return; }
    if (!r.items.length) { root.innerHTML = empty(t("shNoItems"), "box"); return; }
    let photo = null, url = null, retryKey = idem(), version = 0, photoVersion = 0;
    ctx.onDispose(() => { version++; photoVersion++; if (url) URL.revokeObjectURL(url); });
    const itemName = i => state.lang === "ru" ? i.name_ru : i.name;
    root.innerHTML = `<header class="head"><div><p class="eyebrow">${icon("camera")} ${t("est")}</p><h1>${t("shTitle")}</h1><p class="sub">${t("shLead")}</p></div></header>
      <div class="shelf-layout"><section><div class="shelf-photo" id="shelfPhoto">${empty(t("shEmpty"), "camera")}</div><p class="muted shelf-caption">${t("shMax")}</p></section>
      <section class="panel stack"><label for="shelfItem">${t("shItem")}</label><select id="shelfItem" class="input">${r.items.map(i => `<option value="${i.id}">${esc(itemName(i))}</option>`).join("")}</select>
        <label for="shelfPrompt">${t("shPrompt")}</label><input class="input" id="shelfPrompt" maxlength="200" value="${esc(itemName(r.items[0]))}">
        <p class="muted">${t("shHint")}</p><div class="shelf-pickers"><label class="photo-picker">${icon("camera")}<span>${t("shTake")}</span><input id="shelfCamera" type="file" accept="image/png,image/jpeg" capture="environment"></label>
        <label class="photo-picker">${icon("file")}<span>${t("shUpload")}</span><input id="shelfFile" type="file" accept="image/png,image/jpeg"></label></div>
        ${btn({ label: t("shCount"), icon: "scan", kind: "primary", size: "lg", attrs: 'id="countShelf" disabled' })}<div id="shelfDecision" aria-live="polite"></div></section></div>`;
    const countButton = root.querySelector("#countShelf"), decision = root.querySelector("#shelfDecision");
    const drawPhoto = p => {
      root.querySelector("#shelfPhoto").innerHTML = `<img src="${url}" alt="${t("shPhoto")}">${p ? `<svg class="shelf-overlay" viewBox="0 0 ${p.width} ${p.height}" role="img" aria-label="${t("shBoxes")}">${p.boxes.map(([x0, y0, x1, y1], i) => `<g><rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/><text x="${x0 + 3}" y="${y0 + Math.min(22, p.height / 20)}" font-size="${Math.max(10, p.width / 50)}">${i + 1}</text></g>`).join("")}</svg>` : ""}`;
    };
    const invalidate = () => { version++; retryKey = idem(); decision.innerHTML = ""; if (url) drawPhoto(null); };
    root.querySelector("#shelfItem").onchange = () => { root.querySelector("#shelfPrompt").value = itemName(r.items.find(i => i.id === Number(root.querySelector("#shelfItem").value))); invalidate(); };
    root.querySelector("#shelfPrompt").oninput = invalidate;
    const choosePhoto = async e => {
      const file = e.target.files?.[0]; if (!file) return;
      invalidate(); const revision = ++photoVersion;
      photo = null; countButton.disabled = true;
      if (url) URL.revokeObjectURL(url); url = null;
      root.querySelector("#shelfPhoto").innerHTML = empty(t("loading"), "camera");
      try {
        const image = await normalize(file);
        if (revision !== photoVersion || !ctx.isActive()) return;
        photo = image; url = URL.createObjectURL(photo); drawPhoto(null); countButton.disabled = false; toast(t("shReady"));
      } catch { if (revision === photoVersion && ctx.isActive()) { root.querySelector("#shelfPhoto").innerHTML = empty(t("shInvalid"), "alert"); flash(false, t("shInvalid")); } }
    };
    root.querySelector("#shelfFile").onchange = choosePhoto;
    root.querySelector("#shelfCamera").onchange = choosePhoto;
    countButton.onclick = async () => {
      const prompt = root.querySelector("#shelfPrompt").value.trim(), item = Number(root.querySelector("#shelfItem").value);
      if (!photo || !prompt || !item) { flash(false, t("shNeed")); return; }
      const revision = version; countButton.disabled = true; countButton.querySelector("span").textContent = t("shCounting");
      const form = new FormData(); form.append("image", photo, "shelf.png"); form.append("prompt", prompt); form.append("item_id", item); form.append("idem_key", retryKey);
      let p = await apiFetch("/api/shelf/count", { method: "POST", body: form });
      if (!ctx.isActive()) return;
      countButton.disabled = !photo; countButton.querySelector("span").textContent = t("shCount");
      if (revision !== version) return;
      if (!p.ok) { if (!p.silent) flash(false, p.message); return; }
      // Re-fetch the stored offer; no editable quantity is ever sent back.
      const proposal = await api(`/api/shelf/${p.id}`);
      if (revision !== version || !ctx.isActive()) return;
      if (!proposal.ok) { if (!proposal.silent) flash(false, proposal.message); return; }
      p = proposal; drawPhoto(p);
      decision.innerHTML = `<p class="eyebrow">${t("shCounter")}: ${p.model === "sam-3.1" ? "SAM 3.1" : t("shOffline")}</p>
        <h2 class="shelf-offer">${esc(t("shProposal", { item: state.lang === "ru" ? p.item_name_ru : p.item_name, before: `${t("fact")}: ${p.before}`, after: `${t("est")}: ${p.count}` }))}</h2>
        <p>${t("shConfidence")}: ${t("est")} ${Math.round(p.confidence * 100)}%</p><p class="muted">${esc(state.lang === "ru" ? p.note_ru : p.note)}</p><p>${t("shReview")}</p>
        ${p.status === "proposed" ? `<div class="row">${p.choices.map(c => btn({ label: state.lang === "ru" ? c.label_ru : c.label, kind: c.decision === "yes" ? "primary" : "secondary", attrs: `data-shelf-decision="${c.decision}"` })).join("")}</div>` : `<p>${t("shClosed")}</p>`}`;
      toast(state.lang === "ru" ? p.message_ru : p.message);
      decision.querySelectorAll("[data-shelf-decision]").forEach(b => b.onclick = async () => {
        decision.querySelectorAll("button").forEach(x => { x.disabled = true; });
        const d = await api(`/api/shelf/${p.id}/apply`, { decision: b.dataset.shelfDecision });
        if (!ctx.isActive() || revision !== version) return;
        if (!d.silent) flash(d.ok, state.lang === "ru" ? d.message_ru || d.message : d.message);
        if (d.ok) { decision.innerHTML = `<p>${esc(state.lang === "ru" ? d.message_ru : d.message)}</p><p class="muted">${t("shClosed")}</p>`; retryKey = idem(); }
        else decision.querySelectorAll("button").forEach(x => { x.disabled = false; });
      });
    };
  },
});
