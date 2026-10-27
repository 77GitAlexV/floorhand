# Ripple and rush quotes

Built `floorhand/features/ripple.py` and `floorhand/features/rush.py`, with new UI
modules in `static/js/screens/`. Shared edits are limited to planner integration,
the owner feed/private-tablet guard, and small screen imports/mount points.
No packages were installed, no live model calls were made, and no commit was made.
All added fixtures, material costs and scenario data are invented.

## What is available

- Worker/tablet/manager **Report disruption** screen: large saw/material/absence
  presets and free text. Workers and shared tablets receive only a receipt.
- Owner **Today** cards and **Floor → What if…**: a reported shock, new starvation
  times, old/new order ETAs, customers requiring notice, and up to three options:
  wait, a same-trade recovery half-shift, or prioritize an affected customer order.
  Each option has its own order effects and estimated labour cost. Penalty exposure
  stays unknown because the existing schema does not hold penalty contracts.
- Choosing a disruption option applies only its stored `agent_actions` payload.
  The planner reads the accepted capacity reduction, absent crew, priority and
  recovery overtime. Delayed materials limit hand-out to the available stock.
  Work events, rates, order status and shipments are never rewritten.
- Customer notices go to the existing dry-run outbox, with readable escaped HTML
  previews. Nothing is sent. Decline/dismiss is recorded without applying changes.
- Rush quote form: product/quantity/deadline/customer/variant/target contribution
  margin, plus optional free text. It compares ordinary queue ETA, priority ETA,
  and priority plus overtime; lists displaced orders; checks material coverage;
  and shows stage rates, overtime premiums, margin before/after, surcharge, current
  WIP cost and the urgent order's additional pre-shipment cost.
- A quote saves a **conditional customer draft**, without creating an order or
  reserving capacity. Unknown customers must be confirmed. Missing rates, BOM,
  fabric specification or material cost remain unknown; they are not zero.
- Standalone routes: `#/ripple`, `#/rush`, `#/disruption`. The report screen is in
  worker/manager navigation; the owner's main entry points remain Today and Floor.
- Seed adds fictional material cost estimates and one pending broken-saw scenario.

## Model and decision boundaries

Muse Spark parses free text through the budgeted `models.respond` client only.
Offline parsing handles exact worker/material names, saw/foam presets, known
product names and aliases, ISO dates and English/Russian Friday requests. Ambiguous
names require a catalog selection; the system does not invent a product or person.
The seed explicitly uses local rules, even when live models are configured.

All queue and financial calculations are Python/SQL. Numeric explanations carry
FACT / CALC / ESTIMATE labels. Money-bearing routes require a private owner or
manager session; the existing owner outbox route also refuses shared tablets.

Reports/quotes bind a retry key to person, kind and original input. Application
locks the database, re-reads the stored option, verifies the shop date and input
fingerprint, and writes the adjustment, drafts and decision atomically. A retry
returns “Already recorded”; another option on an applied card is refused. Changed
queue, marks, rates, stock, crew or prior adjustments require a fresh calculation.
Input changes during calculation or before proposal storage are refused too.

## Arithmetic and assumptions

The forecast begins on the next configured working day, following the existing
planner. A unit advances at most one stage per working day. Starvation clocks
assume even production during an 08:00–16:00 shift in the shop time zone; they are
estimates, not machine telemetry. The finite horizon is 180 calendar days; work
that cannot finish within it has an unknown ETA. Crew stay in their own trade.

Reported delayed material uses current derived stock first, then assumes the
unmet quantity arrives on its recovery date. Other materials are assumed available
in the disruption simulation. Rush quotes separately flag shortages against the
whole open queue, and require supply/specification confirmation before a promise.
This does not create purchase receipts or supplier commitments.

Recovery half-shift cost uses the full 1.5× piece rate for pieces beyond normal
capacity. For rush margin, ordinary labour is already in base cost, so incremental
cost is the 0.5× overtime premium across **all** overtime pieces, including displaced
work, at each product/stage rate. It is not an hourly wage guarantee.

`required revenue = ceil((material + ordinary labour + overtime premium) /
(1 - target margin))`, with surcharge at least zero. These are contribution
margins: overhead, taxes, financing and contractual penalties are not available.
Material unit costs live in `ripple_material_costs` as explicit office estimates;
there is currently no cost-maintenance UI. WIP is consumed estimated material plus
earned labour on unshipped units, with linked payout amounts frozen. It is a cost
proxy, not a bank balance or supplier-payment ledger.

## Run

The supplied venv launcher failed because its configured base Python is missing.
Use the documented fallback without installing anything. From this worktree:

```powershell
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:FLOORHAND_META_LIVE = '0'
$env:FLOORHAND_DB = 'E:/Work/Floorhand/wt/cx-ripple/data/ripple-demo.db'
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m floorhand.seed --fresh
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m uvicorn floorhand.main:app --port 8873
```

Sign in as the seeded owner, open Today to see the saw scenario, then Floor for
What if. For a fully costed rush example, choose an existing product and enter its
fabric exactly as listed in an existing order (for example fictional `velvet beige`).
Try a tight date to inspect displacements and overtime. Save the quote draft and
review it in Paperwork → Outbox. Shared tablets cannot access these financial views.

## Verification

Tests are in `tests/test_ripple.py` and `tests/ripple-ui.mjs`. Fixtures check by hand
the weekday/weekend ripple, inventory exhaustion, multiple BOM lines sharing stock,
trade-specific absences, overtime and margin math, missing costs, frozen payouts,
material shortages and impossible deadlines. API tests cover authorization,
redaction, idempotency, forbidden extra apply fields, stale inputs, atomic rollback,
draft previews and dismissal. Node checks English/Russian rendering, HTML escaping
and id-only decision handlers without a browser, packages or network.

Run the full suite:

```powershell
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m pytest -q
```

FACT: the full-suite checkpoint passed **603 tests**, with **one optional test
skipped**. After the final shared-material reservation regression, the affected
feature, owner and static suites passed **51 tests** (including **30 ripple/rush
tests**). The existing Starlette/httpx deprecation warning remains. `git diff
--check` is clean. Scratch databases and pytest directories were preserved under
the existing ignored `data/ripple-validation/` directory, within this worktree.

JavaScript syntax was checked with the existing Node installation. An isolated
fresh seed and Uvicorn startup succeeded. Browser approval review rejected opening
the local demo origin because access was declined; no workaround was attempted.
Visual browser QA therefore remains unverified. The optional existing Pillow test
requires a package that is absent; no package was installed.

## Ограничения и запуск

Сбой показывается в «Сегодня» и «Цех → А если…». Работники отправляют сообщение
без финансовых данных. Вариант применяется только из сохранённой карточки;
черновики клиентам видны в исходящих, отправки нет. Срочный расчёт не бронирует
мощности и не создаёт заказ. Неизвестные ставки и стоимость не заменяются нулём.
Прогноз начинается со следующего рабочего дня; время истощения очереди — оценка.
Маржа без накладных расходов и налогов; НЗП — оценка вложенных затрат.
Для запуска используйте команды выше. Визуальная проверка браузером недоступна:
доступ к локальному адресу был отклонён системой подтверждения.
