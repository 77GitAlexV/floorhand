// Office queue of questions, pasted answers and human-learned shop vocabulary.
import { registerScreen, api, state, esc } from "../core.js";
const words = (en, ru) => state.lang === "ru" ? ru : en;

registerScreen({
  id: "gate", roles: ["office"], icon: "orders",
  title: lang => lang === "ru" ? "Входной контроль" : "Gatekeeper",
  async render(root) {
    const [board, dictionary] = await Promise.all([api("/api/gate"), api("/api/slang/aliases")]);
    if (!board.ok || !dictionary.ok) { root.textContent = board.message || dictionary.message; return; }
    root.innerHTML = `<header class="head"><div><h1>${words("Protect the floor", "Защитить цех")}</h1>
      <p class="sub">${esc(state.lang === "ru" ? board.message_ru : board.message)}</p></div></header>
      <div id="gate-result" aria-live="polite"></div>
      ${board.lines.map(line => `<article class="card" style="margin-bottom:20px" data-gate="${line.order_id}">
        <h2>#${line.number} · ${esc(line.product)}</h2><p>${esc(state.lang === "ru" ? line.message_ru : line.message)}</p>
        <blockquote>${esc(line.source_quote || words("No source text saved.", "Исходный текст не сохранён."))}</blockquote>
        <button class="btn secondary" data-question="${line.order_id}">${words("Save customer question to outbox", "Сохранить вопрос клиенту в исходящие")}</button>
        <div data-question-body="${line.order_id}" style="white-space:pre-wrap"></div>
        <form data-answer="${line.order_id}" style="margin-top:16px">
          <label>${words("Paste the customer answer", "Вставьте ответ клиента")}<textarea name="text" rows="3" maxlength="2000" required style="width:100%"></textarea></label>
          <button class="btn secondary">${words("Propose specifications", "Предложить параметры")}</button>
        </form><div data-proposal="${line.order_id}"></div></article>`).join("")}
      ${!board.lines.length ? `<p>${words("All lines have the specifications the floor needs.", "Во всех строках указаны необходимые цеху параметры.")}</p>` : ""}
      <h2>${words("Shop dictionary · learned corrections", "Словарь цеха · исправления людей")}</h2>
      <p>${words("A correction is learned only after confirmed work. Deleting an alias keeps its audit history.", "Исправление запоминается только после подтверждения работы. История удалённого слова сохраняется.")}</p>
      ${dictionary.aliases.filter(a => a.kind === "phrase").map(a => `<article class="card" style="margin-bottom:12px">
        <b>${esc(a.phrase)}</b><p>${esc(a.description)}</p>
        <p>${words("Learned by", "Кто обучил")}: ${esc(a.learned_by_name)} · ${esc(a.learned_at)}</p>
        <button class="btn secondary" data-delete-alias="${a.id}">${words("Remove alias", "Удалить из словаря")}</button></article>`).join("")}
      <details><summary>${words("Starter dictionary", "Начальный словарь")}</summary>
        <p>${dictionary.aliases.filter(a => a.kind !== "phrase").map(a => esc(a.phrase)).join(" · ")}</p></details>
      <h2>${words("Recorded part work", "Записанная работа над деталями")}</h2>
      ${dictionary.components.map(c => `<p>FACT / ФАКТ: ${c.qty} × ${words(c.component, c.component === "seat cushions" ? "сидушки" : c.component)} · ${esc(c.product)} · ${esc(c.person)} · ${esc(c.work_date)}</p>`).join("")}`;
    const result = root.querySelector("#gate-result");
    root.querySelectorAll("[data-question]").forEach(button => { button.onclick = async () => {
      button.disabled = true;
      const r = await api(`/api/gate/${button.dataset.question}/question`, {});
      result.textContent = r.message;
      root.querySelector(`[data-question-body="${button.dataset.question}"]`).textContent = r.body || "";
      button.disabled = false;
    }; });
    root.querySelectorAll("[data-answer]").forEach(form => { form.onsubmit = async event => {
      event.preventDefault();
      const r = await api(`/api/gate/${form.dataset.answer}/answer`, { text: form.elements.text.value });
      result.textContent = r.message;
      const target = root.querySelector(`[data-proposal="${form.dataset.answer}"]`);
      if (!r.id) return;
      const labels = { fabric: words("Fabric", "Ткань"), leg_finish: words("Leg finish", "Отделка ножек"), frame_colour: words("Frame colour", "Цвет каркаса") };
      target.innerHTML = `<p>${Object.entries(r.values).map(([k,v]) => `${esc(labels[k])}: <b>${esc(v.value)}</b><br>«${esc(v.quote)}»`).join("<br>")}</p>
        <button class="btn primary" ${r.can_apply ? "" : "disabled"}>${words("Confirm these specifications", "Подтвердить эти параметры")}</button>`;
      target.querySelector("button").onclick = async event => {
        event.target.disabled = true;
        const applied = await api(`/api/gate/answers/${r.id}/confirm`, {});
        result.textContent = state.lang === "ru" ? (applied.message_ru || applied.message) : applied.message;
        if (applied.ok && applied.ready) root.querySelector(`[data-gate="${form.dataset.answer}"]`).remove();
        else event.target.disabled = false;
      };
    }; });
    root.querySelectorAll("[data-delete-alias]").forEach(button => { button.onclick = async () => {
      const r = await api(`/api/slang/aliases/${button.dataset.deleteAlias}`, null, { method: "DELETE" });
      result.textContent = r.message;
      if (r.ok) button.closest("article").remove();
    }; });
  },
});
