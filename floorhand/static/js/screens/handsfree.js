// A palm-sized voice surface; tap marking remains on the Work screen.
import { registerScreen, api, state, esc, go } from "../core.js";
import { empty } from "../ui.js";

const words = (en, ru) => state.lang === "ru" ? ru : en;

registerScreen({
  id: "handsfree", roles: ["worker", "tablet", "office"], nav: false, icon: "mic",
  title: lang => lang === "ru" ? "Руки в клею" : "Glue mode",
  async render(root) {
    if (!["worker"].includes(state.me.role) || state.me.stage_id == null) {
      root.innerHTML = `<section class="panel stack"><h1>${words("Hands covered in glue", "Руки в клею")}</h1>
        ${empty(words("Voice marking belongs to a worker and their stage. Sign in as a demo worker on a phone, then open Glue mode from Work.", "Голосовые отметки относятся к работнику и его этапу. Войдите на телефоне как демо-работник и откройте режим «Руки в клею» из работы."), "mic")}
        <a class="btn secondary" href="#/today">${words("Back to Today", "На экран «Сегодня»")}</a></section>`;
      return;
    }
    const cat = await api("/api/slang/catalog");
    if (!cat.ok) { root.textContent = cat.message; return; }
    let proposal = null, recognition = null, listening = false, busy = false, typedOnly = false;
    root.innerHTML = `
      <style>
        .glue-mode {max-width:760px;margin:auto;text-align:center;padding-bottom:40px}
        .glue-mode button,.glue-mode select,.glue-mode input {min-height:72px;font-size:1.4rem;width:100%;touch-action:manipulation;margin:8px 0}
        .glue-mode .palm {min-height:240px;font-size:clamp(1.4rem,6vw,2.2rem);border-radius:32px;user-select:none;-webkit-user-select:none;overflow-wrap:anywhere}
        .glue-mode .sentence {font-size:clamp(1.7rem,5vw,3rem);line-height:1.25;min-height:160px;padding:20px 0}
        .glue-mode .yes-no {display:grid;grid-template-columns:1fr 1fr;gap:20px}
        .glue-mode [hidden] {display:none !important}
        .glue-mode .yes-no button {min-height:140px;font-size:2.4rem}
        .glue-mode summary {padding:24px;font-size:1.4rem;cursor:pointer}
        .glue-mode label {display:block;text-align:left;font-size:1.2rem}
      </style>
      <section class="glue-mode">
        <h1>${words("Hands covered in glue", "Руки в клею")}</h1>
        <p>${words("Hold, speak, release. Listen, then say yes or no.", "Держите, говорите, отпустите. Послушайте и скажите да или нет.")}</p>
        <button class="btn primary palm" id="glue-talk">${words("HOLD TO TALK", "ДЕРЖИТЕ И ГОВОРИТЕ")}</button>
        <div class="sentence" id="glue-sentence" aria-live="polite">${words("Say the work in your own words.", "Скажите своими словами, что сделали.")}</div>
        <div class="yes-no" hidden id="glue-choice">
          <button class="btn primary" id="glue-yes">${words("YES", "ДА")}</button>
          <button class="btn secondary" id="glue-no">${words("NO", "НЕТ")}</button>
        </div>
        <details id="glue-correction"><summary>${words("Correct what I meant", "Исправить смысл")}</summary>
          <form id="glue-fix">
            <label>${words("Item", "Изделие")}<select class="input" name="product_id">${cat.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
            <label>${words("Quantity", "Количество")}<input name="qty" type="number" min="1" max="100" value="1" required></label>
            <label>${words("Stage", "Этап")}<select name="stage_id">${cat.stages.filter(s => s.id === state.me.stage_id).map(s => `<option value="${s.id}">${esc(state.lang === "ru" ? s.name_ru : s.name_en)}</option>`).join("")}</select></label>
            <label>${words("Option", "Вариант")}<select name="option"><option value="">${words("No option said", "Без уточнения варианта")}</option>${cat.options.map(o => `<option>${esc(o)}</option>`).join("")}</select></label>
            <label>${words("Whole item or part work", "Изделие целиком или детали")}<select name="component"><option value="">${words("Whole item", "Изделие целиком")}</option><option value="seat cushions">${words("Seat cushions (part count only)", "Сидушки (только учёт деталей)")}</option></select></label>
            <button class="btn secondary">${words("Read corrected proposal", "Прочитать исправление")}</button>
          </form>
        </details>
        <details id="glue-typed"><summary>${words("Type the words instead", "Ввести фразу текстом")}</summary>
          <form id="glue-type"><label>${words("Your words", "Ваши слова")}<input class="input" name="text" maxlength="500" required autocomplete="off"></label>
            <button class="btn secondary">${words("Understand", "Разобрать")}</button></form>
        </details>
        <button class="btn secondary" id="glue-back">${words("Back to tap marking", "К обычным отметкам")}</button>
      </section>`;
    const sentence = root.querySelector("#glue-sentence");
    const palm = root.querySelector("#glue-talk");
    const choice = root.querySelector("#glue-choice");
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    function typedFallback(text) {
      typedOnly = true;
      root.querySelector("#glue-typed").open = true;
      sentence.textContent = text;
      palm.textContent = words("TYPE BELOW", "ВВЕДИТЕ ТЕКСТ НИЖЕ");
    }
    const live = () => root.isConnected && state.screen === "handsfree";
    function stop() { recognition?.abort(); recognition = null; listening = false; }
    const observer = new MutationObserver(() => {
      if (!live()) { stop(); window.speechSynthesis?.cancel(); observer.disconnect(); }
    });
    observer.observe(document.getElementById("app"), { childList: true, subtree: true });
    function say(text, waitForYes = false) {
      if (!live()) return;
      sentence.textContent = text;
      if (!window.speechSynthesis) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = state.lang === "ru" ? "ru-RU" : "en-US";
      // Recognition starts after read-back finishes, so the app cannot confirm itself.
      utterance.onend = () => { if (live() && waitForYes && proposal) listen(true); };
      window.speechSynthesis.speak(utterance);
    }
    function show(r) {
      busy = false;
      if (!live()) return;
      proposal = r.id ? r : null;
      choice.hidden = !proposal;
      root.querySelector("#glue-yes").disabled = !r.can_apply;
      root.querySelector("#glue-no").disabled = false;
      if (r.meaning) {
        const form = root.querySelector("#glue-fix");
        ["product_id", "qty", "stage_id", "option", "component"].forEach(k => { form.elements[k].value = r.meaning[k] ?? ""; });
      }
      say(r.message, !!r.can_apply);
    }
    async function understand(text) {
      if (busy || !live()) return;
      busy = true; stop(); window.speechSynthesis?.cancel();
      choice.hidden = true;
      sentence.textContent = words("Understanding…", "Разбираю…");
      show(await api("/api/slang/propose", { text }));
    }
    async function decide(yes, spoken = null) {
      if (busy || !proposal || !live()) return;
      busy = true; stop(); window.speechSynthesis?.cancel();
      root.querySelectorAll(".yes-no button").forEach(b => { b.disabled = true; });
      const r = await api(`/api/slang/${proposal.id}/decide`, spoken ? { spoken } : { yes });
      busy = false;
      if (!live()) return;
      // Keep a failed request attached to its original id so a retry records once.
      if (r.ok) { proposal = null; choice.hidden = true; }
      else root.querySelectorAll(".yes-no button").forEach(b => { b.disabled = false; });
      say(r.message);
    }
    function listen(confirmation = false) {
      if (!SpeechRecognition || busy || !live()) return;
      stop();
      typedOnly = false;
      recognition = new SpeechRecognition();
      recognition.lang = state.lang === "ru" ? "ru-RU" : "en-US";
      recognition.interimResults = false;
      recognition.onresult = event => {
        if (!live()) return;
        const text = event.results[0][0].transcript.trim();
        if (confirmation && proposal) {
          if (/^(yes|да|no|нет)[.!]?$/i.test(text)) decide(/^(yes|да)/i.test(text), text.replace(/[.!]$/, ""));
          else say(words("Say yes or no, or use the large buttons.", "Скажите да или нет или нажмите большую кнопку."));
        } else understand(text);
      };
      recognition.onerror = () => {
        if (live()) typedFallback(words("Microphone unavailable. Type the phrase below, or return to tap marking.", "Микрофон недоступен. Введите фразу ниже или вернитесь к обычным отметкам."));
      };
      recognition.onend = () => { listening = false; if (live() && !typedOnly) palm.textContent = words("HOLD TO TALK", "ДЕРЖИТЕ И ГОВОРИТЕ"); };
      try { recognition.start(); listening = true; }
      catch { typedFallback(words("Try the microphone again or type below.", "Попробуйте микрофон ещё раз или введите текст ниже.")); }
    }
    palm.onpointerdown = event => {
      if (busy) return;
      if (!SpeechRecognition) { root.querySelector("#glue-type input").focus(); return; }
      event.preventDefault(); palm.setPointerCapture(event.pointerId);
      window.speechSynthesis?.cancel();
      listen(!!proposal);
      palm.textContent = words("LISTENING…", "СЛУШАЮ…");
    };
    palm.onpointerup = () => { if (listening) recognition?.stop(); };
    palm.onpointercancel = stop;
    palm.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); listen(!!proposal); } };
    root.querySelector("#glue-yes").onclick = () => decide(true);
    root.querySelector("#glue-no").onclick = () => decide(false);
    root.querySelector("#glue-type").onsubmit = event => { event.preventDefault(); understand(event.target.elements.text.value); };
    root.querySelector("#glue-fix").onsubmit = async event => {
      event.preventDefault();
      if (!proposal || busy) return;
      stop(); window.speechSynthesis?.cancel(); busy = true;
      const f = event.target.elements;
      show(await api(`/api/slang/${proposal.id}/fix`, { product_id: Number(f.product_id.value), qty: Number(f.qty.value),
        stage_id: Number(f.stage_id.value), option: f.option.value || null, component: f.component.value || null }));
    };
    root.querySelector("#glue-back").onclick = () => { stop(); go("work"); };
    if (!SpeechRecognition) typedFallback(words("This browser has no speech recognition. Type below or use tap marking.", "В этом браузере нет распознавания речи. Введите текст ниже или используйте обычные отметки."));
    try {
      const saved = JSON.parse(sessionStorage.getItem('fh_glue_proposal') || 'null');
      sessionStorage.removeItem('fh_glue_proposal');
      if (saved && saved.person === state.me.id) show(saved.proposal);
    } catch { /* storage is optional; voice and typed entry still work */ }
  },
});
