// "Who is working?" — the sign-in board. Demo sign-in is picking a person;
// the shared tablet option hides money for everyone who marks on it.
import { registerScreen, addStrings, api, t, esc, name, state, setLang, flash, render, go } from "../core.js";
import { icon, avatar, sheet, brandMark } from "../ui.js";

addStrings({
  en: {
    who: "Who is working?",
    whoLead: "Tap your name. On the shared shop tablet, switch on “Shared tablet” — nobody’s money is shown there.",
    tablet: "Shared tablet (no money on screen)", officeTeam: "Office", working: "{n} people",
    startShift: "Start as {name}?", startShiftSub: "Your work, your money and your mistakes will be recorded under this name.",
    start: "Start", role_owner: "Owner", role_manager: "Manager", role_worker: "Worker",
    demoNote: "Demo workshop. Every person, customer and number is made up.",
  },
  ru: {
    who: "Кто работает?",
    whoLead: "Нажми своё имя. На общем планшете цеха включи «Общий планшет» — деньги там никому не показываются.",
    tablet: "Общий планшет (без денег на экране)", officeTeam: "Офис", working: "{n} чел.",
    startShift: "Работаешь как {name}?", startShiftSub: "Работа, деньги и отмены запишутся на это имя.",
    start: "Начать", role_owner: "Владелец", role_manager: "Менеджер", role_worker: "Рабочий",
    demoNote: "Демо-цех. Все люди, клиенты и цифры выдуманы.",
  },
});

registerScreen({
  id: "login", roles: [], nav: false, title: lang => lang === "ru" ? "Вход" : "Sign in",
  async render(root) {
    const r = await api("/api/demo/people");
    const people = r.list || [];
    const office = people.filter(p => !p.stage);
    const groups = [];
    for (const p of people.filter(p => p.stage)) {
      let g = groups.find(x => x.stage === p.stage);
      if (!g) groups.push(g = { stage: p.stage, stage_ru: p.stage_ru, people: [] });
      g.people.push(p);
    }
    const tile = p => `<button type="button" class="tile" data-id="${p.id}" data-name="${esc(p.name)}">
        ${avatar(p.name)}
        <span class="t-text"><b>${esc(p.name)}</b><span>${esc(p.stage ? (state.lang === "ru" ? p.stage_ru : p.stage) : t("role_" + p.role))}</span></span>
      </button>`;
    root.innerHTML = `<div class="login">
      <div class="login-top">
        <a class="brand" href="#">${brandMark()}<span>Floorhand</span></a>
        <button type="button" class="btn secondary sm" id="lang">${icon("globe")}<span>${state.lang === "en" ? "Русский" : "English"}</span></button>
      </div>
      <h1>${t("who")}</h1>
      <p class="lead">${t("whoLead")}</p>
      <div class="tablet-row">
        <label class="switch"><input type="checkbox" id="tablet"><span class="sw"></span><span>${t("tablet")}</span></label>
      </div>
      <div class="group-head"><h2>${t("officeTeam")}</h2><span>${t("working", { n: office.length })}</span></div>
      <div class="tiles">${office.map(tile).join("")}</div>
      ${groups.map(g => `<div class="group-head"><h2>${esc(state.lang === "ru" ? g.stage_ru : g.stage)}</h2><span>${t("working", { n: g.people.length })}</span></div>
        <div class="tiles">${g.people.map(tile).join("")}</div>`).join("")}
      <p class="muted" style="margin-top:32px">${t("demoNote")}</p>
    </div>`;
    root.querySelector("#lang").onclick = () => setLang(state.lang === "en" ? "ru" : "en");
    root.querySelectorAll(".tile").forEach(b => b.onclick = async () => {
      const tablet = root.querySelector("#tablet").checked;
      if (tablet) {  // a stray touch on the shared tablet must not start someone else's shift
        const ok = await sheet({
          title: t("startShift", { name: b.dataset.name }), body: t("startShiftSub"),
          actions: [{ id: "yes", label: t("start"), kind: "primary", icon: "check" }, { id: "no", label: t("back") }],
        });
        if (ok !== "yes") return;
      }
      const res = await api("/api/login", { person_id: Number(b.dataset.id), tablet });
      if (!res.ok) return flash(false, res.message);
      state.me = null; location.hash = ""; render();
    });
  },
});
