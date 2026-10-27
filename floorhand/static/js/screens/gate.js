// Office questions, quoted answers and the human-maintained shop dictionary.
import { registerScreen, api, state, esc, fmtDate, flash } from "../core.js";
import { btn, empty, pill, sectionTitle, table } from "../ui.js";
const words = (en, ru) => state.lang === "ru" ? ru : en;

registerScreen({
  id: "gate", roles: ["office"], icon: "orders",
  title: lang => lang === "ru" ? "Входной контроль" : "Gatekeeper",
  async render(root, ctx) {
    const [board, dictionary, catalog] = await Promise.all([api("/api/gate"), api("/api/slang/aliases"), api("/api/slang/catalog")]);
    if (!board.ok || !dictionary.ok) { root.innerHTML = empty(board.message || dictionary.message, "alert"); return; }
    const kindName = {product:words("Product", "Изделие"), stage:words("Stage", "Этап"), option:words("Option", "Вариант"), component:words("Part", "Деталь"), phrase:words("Correction", "Исправление")};
    const meaning = a => {
      if (a.description) return esc(a.description);
      const value = a.meaning.value;
      if (a.kind === "product") return esc(catalog.products?.find(p => p.id === value)?.name || value);
      if (a.kind === "stage") { const stage = catalog.stages?.find(s => s.id === value); return esc(stage ? (state.lang === "ru" ? stage.name_ru : stage.name_en) : value); }
      return esc(value === "seat cushions" ? words("Seat cushions", "Сидушки") : value);
    };
    const aliasTable = rows => table({cols:[{label:words("Words", "Фраза")}, {label:words("Meaning", "Значение")}, {label:words("Source", "Источник")}, {label:words("Action", "Действие")}],
      rows: rows.map(a => ({cells:[`<b>${esc(a.phrase)}</b><small class="muted">${esc(kindName[a.kind] || a.kind)}</small>`, meaning(a),
        `${esc(a.learned_by_name || words("Starter dictionary", "Начальный словарь"))}<small class="muted">${esc(fmtDate(a.learned_at))}</small>`,
        btn({label:words("Remove alias", "Удалить"), kind:"ghost", icon:"x", attrs:`data-delete-alias="${a.id}"`})]})),
      emptyText:words("No learned corrections yet. Confirm a corrected worker phrase to teach the dictionary.", "Исправлений пока нет. Подтвердите исправленную фразу работника, чтобы обучить словарь.")});
    root.innerHTML = `<header class="head"><div><h1>${words("Protect the floor", "Защитить цех")}</h1>
      <p class="sub">${esc(state.lang === "ru" ? board.message_ru : board.message)}</p></div></header>
      <div id="gate-result" role="status" aria-live="polite"></div><div class="stack">
      ${board.lines.map(line => `<article class="decision hot gate-card" data-gate="${line.order_id}">
        <div class="d-kind">${pill(words("Needs specifications", "Нужны параметры"), "warn")} <span class="tag fact">FACT</span></div>
        <h3>#${line.number} · ${esc(line.product)}</h3><p>${esc(state.lang === "ru" ? line.message_ru : line.message)}</p>
        <blockquote>${esc(line.source_quote || words("No source text saved.", "Исходный текст не сохранён."))}</blockquote>
        ${btn({label:words("Save customer question to outbox", "Сохранить вопрос клиенту в исходящие"), attrs:`data-question="${line.order_id}"`})}
        <div class="gate-question" data-question-body="${line.order_id}"></div>
        <form class="stack" data-answer="${line.order_id}">
          <label class="scenario-field">${words("Paste the customer answer", "Вставьте ответ клиента")}<textarea class="input" name="text" rows="3" maxlength="2000" required></textarea></label>
          <button type="submit" class="btn secondary">${words("Propose specifications", "Предложить параметры")}</button>
        </form><div data-proposal="${line.order_id}"></div></article>`).join("") || empty(words("All lines have the specifications the floor needs.", "Во всех строках указаны необходимые цеху параметры."), "check")}</div>
      ${sectionTitle(words("Shop dictionary", "Словарь цеха"))}
      <section class="panel stack"><p class="muted">${words("A correction is learned only after confirmed work. Deleting an alias keeps its audit history.", "Исправление запоминается только после подтверждения работы. История удалённого слова сохраняется.")}</p>
        <h3>${words("Learned corrections", "Выученные исправления")}</h3>
        ${aliasTable(dictionary.aliases.filter(a => a.kind === "phrase"))}
        <details class="disc"><summary>${words("Starter dictionary", "Начальный словарь")}</summary><div class="disc-body">${aliasTable(dictionary.aliases.filter(a => a.kind !== "phrase"))}</div></details></section>
      ${sectionTitle(words("Recorded part work", "Записанная работа над деталями"))}
      <section class="panel">${table({cols:[{label:words("Part work · FACT", "Работа над деталями · ФАКТ")},{label:words("Worker", "Работник")},{label:words("Work date", "Дата работы")}],
        rows:dictionary.components.map(c => ({cells:[`${c.qty} × ${esc(c.component === "seat cushions" ? words("Seat cushions", "Сидушки") : c.component)} · ${esc(c.product)}`, esc(c.person), esc(fmtDate(c.work_date))]})),
        emptyText:words("No part work recorded yet. Confirmed part counts will appear here; they do not finish a whole item.", "Работа над деталями пока не записана. Здесь появятся подтверждённые количества деталей; они не завершают изделие целиком.")})}</section>`;
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
      const button = form.querySelector("button"); button.disabled = true;
      const r = await api(`/api/gate/${form.dataset.answer}/answer`, {text:form.elements.text.value});
      button.disabled = false; result.textContent = r.message;
      const target = root.querySelector(`[data-proposal="${form.dataset.answer}"]`);
      if (!r.id) return;
      const labels = {fabric:words("Fabric", "Ткань"), leg_finish:words("Leg finish", "Отделка ножек"), frame_colour:words("Frame colour", "Цвет каркаса")};
      target.innerHTML = `<div class="stack">${Object.entries(r.values).map(([k,v]) => `<p>${esc(labels[k])}: <b>${esc(v.value)}</b><br>«${esc(v.quote)}»</p>`).join("")}
        ${btn({label:words("Confirm these specifications", "Подтвердить эти параметры"), kind:"primary", disabled:!r.can_apply})}</div>`;
      target.querySelector("button").onclick = async event => {
        const confirm = event.currentTarget; confirm.disabled = true;
        const applied = await api(`/api/gate/answers/${r.id}/confirm`, {});
        flash(applied.ok, applied.message);
        if (applied.ok && applied.ready) ctx.refresh();
        else confirm.disabled = false;
      };
    }; });
    root.querySelectorAll("[data-delete-alias]").forEach(button => { button.onclick = async () => {
      button.disabled = true;
      const r = await api(`/api/slang/aliases/${button.dataset.deleteAlias}`, null, {method:"DELETE"});
      flash(r.ok, r.message);
      if (r.ok) ctx.refresh();
      else button.disabled = false;
    }; });
  },
});
