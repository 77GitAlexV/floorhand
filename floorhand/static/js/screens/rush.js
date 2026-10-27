// Financial fields are served only to private owner/manager sessions.
import { registerScreen, api, esc, idem, flash } from "../core.js";
import { btn, empty } from "../ui.js";
import { L, whatIfCard, bindWhatIfCards } from "../scenario-ui.js";
export { rushDetails } from "../scenario-ui.js";

export async function mountRush(host, ctx) {
  host.innerHTML = `<p role="status">${L("Loading products…", "Загрузка изделий…")}</p>`;
  const r = await api("/api/rush/catalog");
  if (ctx.isActive && !ctx.isActive()) return;
  if (!r.ok) { host.innerHTML = empty(r.message || L("Could not load products. Reopen this screen to retry.", "Не удалось загрузить изделия. Откройте экран снова."), "alert"); return; }
  if (!r.products.length) {
    host.innerHTML = empty(L("Add a product to the catalog before calculating an urgent order.", "Добавьте изделие в каталог перед расчётом срочного заказа."));
    return;
  }
  host.innerHTML = `<header class="head"><div><h1>${L("Urgent order: can we promise it?", "Срочный заказ: можем обещать?")}</h1><p class="sub">${L("Check the queue, displaced promises and contribution margin.", "Проверьте очередь, сдвинутые сроки и маржу.")}</p></div></header>
    <section class="panel stack"><form class="stack" data-rush-form>
      <div class="scenario-form-grid">
        <label class="scenario-field">${L("Product", "Изделие")}<select class="input" name="product" required>${r.products.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
        <label class="scenario-field">${L("Quantity", "Количество")}<input class="input" name="qty" type="number" min="1" max="100" value="1" required></label>
        <label class="scenario-field">${L("Due date", "Срок")}<input class="input" name="due" type="date" required></label>
      </div>
      <details class="disc"><summary>${L("Customer, material specification and margin", "Клиент, параметры материалов и маржа")}</summary><div class="disc-body scenario-form-grid">
        <label class="scenario-field">${L("Customer", "Клиент")}<select class="input" name="customer"><option value="">${L("To confirm", "Уточнить")}</option>${r.customers.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
        <label class="scenario-field">${L("Fabric and variant", "Ткань и вариант")}<input class="input" name="variant"></label>
        <label class="scenario-field">${L("Target contribution margin %", "Целевая маржа %")}<input class="input" name="margin" type="number" min="0" max="99" value="35" required></label>
      </div></details>
      ${btn({label:L("Calculate rush quote", "Рассчитать срочный заказ"), icon:"clock", kind:"primary"})}
    </form></section><div class="feed scenario-results" data-rush-result aria-live="polite"></div>`;
  const form = host.querySelector("form"), button = form.querySelector("button");
  button.type = "submit";
  let requestKey = idem();
  form.oninput = () => { requestKey = idem(); };
  form.onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(form);
    button.disabled = true;
    const result = await api("/api/rush/quote", {request_key:requestKey,
      product_id:Number(f.get("product")), qty:Number(f.get("qty")), due_date:f.get("due") || null,
      customer_id:f.get("customer") ? Number(f.get("customer")) : null,
      variant:f.get("variant"), target_margin:Number(f.get("margin")) / 100});
    button.disabled = false;
    if (ctx.isActive && !ctx.isActive()) return;
    if (!result.silent) flash(result.ok, result.message);
    const out = host.querySelector("[data-rush-result]");
    out.innerHTML = result.ok ? whatIfCard(result.card) : empty(result.message || "", "alert");
    if (result.ok) bindWhatIfCards(out, ctx);
  };
}

registerScreen({
  id:"rush", roles:["office"], nav:false, icon:"clock",
  title:lang => lang === "ru" ? "Срочный расчёт" : "Rush quote",
  render:mountRush,
});
