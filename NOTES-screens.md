# Screens / Экраны

## Built / Что сделано

Resumed the existing partial work; no restart, package installation or commit. Added registered EN/RU screens in the Linen & Steel design language:

- `#/channels` — owner/manager: channel listings, tagged cover/capacity estimates, make-to-stock proposals, stored Yes/No decisions, raw CSV preview and confirm/reject. Cover target refreshes the plan; unknown sales remain unknown.
- `#/shelf` — owner/manager, phone layout: separate camera and upload controls; local orientation/resize to PNG; pixel-coordinate detection overlays; counter attribution; before/after stock offer; idempotent retry and stored Yes/No. Changing the item/photo/prompt invalidates the visible offer. Image URLs are released on exit. Meta-off uses the existing offline counter.
- `#/live` and `#/live?tv=1` — owner: live stage rail, queues/capacity, crew, recent mark ticker, highlighted late orders, shop date/time, simulator start/pause, dropped-stream reconnect and resync. TV hides navigation and simulator controls. Motion opts in through `prefers-reduced-motion: no-preference`. No money fields are sent in the dedicated snapshot or SSE projections.
- `#/outbox` — owner/manager: visible dry-run drafts from confirmed owner decisions (separate from delivery-email outbox in Paperwork).
- `#/tablet` — owner/manager device setup, then explicit worker chooser and shift confirmation; signs the office out, pings while touched, and returns to the chooser after idle/sign-out. Me is reachable on a tablet for language/theme and material requests; money screens remain excluded.
- Work/Me gaps: upload server voice recording (typed Speak remains the offline path), withdraw pending other-work claims, and send missing-material signals.

Added `/api/live/snapshot` in the simulator feature, plus only the display fields needed by the SSE ticker. Snapshot and stream reuse one mark projection. The API client now retains offered choices on structured refusals (required by existing order edit/rework UI), chooses bilingual messages and handles expiry consistently for JSON, CSV and photo/audio uploads. Render disposal closes streams, timers and image URLs. Office navigation scrolls at phone width and short desktop heights.

РУ: Продолжена частичная работа. Добавлены «Каналы», «Полка», «Цех вживую» с режимом ТВ, «Исходящие» и настройка планшета. Решения применяются только по сохранённым предложениям; повтор запроса не меняет смысл действия. Фото считается офлайн при выключенной Meta. Денег в снимке цеха и потоке событий нет. Документы, отметки, решения владельца и полная карточка заказа уже доступны из меню.

## Route audit / Аудит маршрутов

Every explicit HTTP route in `floorhand/main.py` and `floorhand/features/*.py` is listed below. **New** marks a caller added by this task; other callers existed before the interrupted run. Names are screen names, with helper modules where relevant. Paperwork hub is the linked `/paperwork` page implemented by `static/paperwork.js`, not a missing SPA screen. Routes listed without a direct caller are diagnostics, QR entry points or alternate read surfaces; their operational workflow is reachable through the named alternative. No duplicate action screen is needed for those helpers. `/static` is a mount; OpenAPI/docs routes are framework-generated and are not explicit feature routes.

Modules without HTTP route declarations: `__init__.py`, `ask.py`, `paperwork_html.py`, `planner.py`. The shelf pixel counter is `floorhand/shelf_vision.py` (not an HTTP module); Shelf reaches it through `POST /api/shelf/count`.

| Source | Route | Reachable screen / caller |
| --- | --- | --- |
| `main.py` | `GET /` | App shell (`index.html`) |
| `main.py` | `GET /api/demo/people` | Sign in |
| `main.py` | `POST /api/login` | Sign in |
| `main.py` | `POST /api/logout` | App shell / Me / tablet sign-out |
| `main.py` | `GET /api/me` | App shell; Live reconnect auth check |
| `main.py` | `GET /api/work` | Work |
| `main.py` | `POST /api/work/done` | Work (legacy fallback) |
| `main.py` | `POST /api/work/undo` | Work (legacy fallback) |
| `main.py` | `POST /api/work/take` | Work |
| `main.py` | `GET /api/money` | Money (worker only) |
| `main.py` | `GET /api/floor` | Floor |
| `main.py` | `GET /api/orders` | Orders; Paperwork hub |
| `main.py` | `GET /api/orders/{order_id}` | Orders detail |
| `main.py` | `POST /api/intake` | Orders intake |
| `main.py` | `POST /api/intake/{action_id}/apply` | Orders intake |
| `main.py` | `GET /api/shipping` | Shipping |
| `main.py` | `GET /api/shipping/{customer_id}` | Shipping |
| `main.py` | `POST /api/ship` | Shipping |
| `main.py` | `GET /invoice/{shipment_id}` | No direct caller: legacy print; Shipping uses `/paperwork/note/{shipment_id}` |
| `channels.py` | `GET /api/channels/overview` | **New** Channels > cover and listings |
| `channels.py` | `POST /api/channels/import` | **New** Channels > preview CSV diff |
| `channels.py` | `POST /api/channels/import/{import_id}/apply` | **New** Channels > confirm stored import |
| `channels.py` | `POST /api/channels/suggest` | **New** Channels > suggest stock launch |
| `channels.py` | `POST /api/channels/proposals/{action_id}/apply` | **New** Channels > Yes to stored launch |
| `channels.py` | `POST /api/channels/proposals/{action_id}/reject` | **New** Channels > No to launch/import |
| `health.py` | `GET /healthz` | No screen: operational readiness/liveness probe |
| `marking.py` | `POST /api/marking/mark` | Work / Scan / add missed marks (`marking.js`) |
| `marking.py` | `POST /api/marking/undo` | Work (`marking.js`) |
| `marking.py` | `POST /api/marking/sync` | Work offline queue (`marking-queue.js`) |
| `marking.py` | `POST /api/marking/voice/text` | Work > Speak (typed offline fallback) |
| `marking.py` | `POST /api/marking/voice/audio` | **New** Work > upload voice recording |
| `marking.py` | `POST /api/marking/photo` | Work > photo of sheet |
| `marking.py` | `POST /api/marking/proposals/{action_id}/decide` | Work > review voice/photo offer |
| `marking.py` | `GET /api/marking/claims` | Work > other work |
| `marking.py` | `POST /api/marking/claims` | Work > other work |
| `marking.py` | `POST /api/marking/claims/{claim_id}/withdraw` | **New** Work > other work > withdraw pending claim |
| `marking.py` | `POST /api/marking/claims/{claim_id}/decide` | From the floor |
| `marking.py` | `GET /api/marking/day-check` | Work evening check |
| `marking.py` | `POST /api/marking/day-check/confirm` | Work evening check |
| `marking.py` | `GET /api/marking/review` | From the floor |
| `marking.py` | `POST /api/marking/signals/{signal_id}/close` | From the floor |
| `marking.py` | `POST /api/marking/material` | **New** Me > ask for material (worker/tablet) |
| `marking.py` | `POST /api/marking/tablet/setup` | **New** Shop tablet (office) |
| `marking.py` | `GET /api/marking/tablet` | **New** tablet worker chooser (Sign in) |
| `marking.py` | `POST /api/marking/tablet/act` | **New** tablet worker chooser |
| `marking.py` | `POST /api/marking/tablet/leave` | **New** tablet sign-out / chooser |
| `marking.py` | `POST /api/marking/tablet/ping` | **New** shell tablet activity |
| `materials.py` | `GET /api/materials` | Stock > materials and shortages |
| `materials.py` | `GET /api/materials/{material_id}/journal` | Stock > journal |
| `materials.py` | `POST /api/materials/receive` | Stock > receipt |
| `orders.py` | `POST /api/orders/{order_id}/edit` | Orders detail > edit (`orders-more.js`) |
| `orders.py` | `POST /api/orders/{order_id}/rework` | Orders detail > rework (`orders-more.js`) |
| `orders.py` | `POST /api/orders/{order_id}/cancel` | Orders detail > cancel (`orders-more.js`) |
| `orders.py` | `POST /api/orders/{order_id}/take-from-stock` | Orders detail > take-from-stock (`orders-more.js`) |
| `orders.py` | `POST /api/orders/{order_id}/comment` | Orders detail > comment (`orders-more.js`) |
| `orders.py` | `GET /api/orders/{order_id}/story` | Orders detail > story (`orders-more.js`) |
| `orders.py` | `GET /api/orders/stock/offers` | No direct caller: per-order stock offers and Use it are already in Orders > story |
| `orders.py` | `GET /api/orders/catalog/lists` | Orders edit/intake datalists; **New** Shop tablet stage picker |
| `orders.py` | `POST /api/orders/intake/{action_id}/fix` | Orders intake > photo/preview or fix/add/remove proposed line (`orders-more.js`) |
| `orders.py` | `POST /api/orders/intake/{action_id}/line` | Orders intake > photo/preview or fix/add/remove proposed line (`orders-more.js`) |
| `orders.py` | `POST /api/orders/intake/{action_id}/line/{idx}/remove` | Orders intake > photo/preview or fix/add/remove proposed line (`orders-more.js`) |
| `orders.py` | `POST /api/orders/intake/photo` | Orders intake > photo/preview or fix/add/remove proposed line (`orders-more.js`) |
| `orders.py` | `GET /api/orders/intake/photo/{photo_id}` | Orders intake > photo/preview or fix/add/remove proposed line (`orders-more.js`) |
| `orders.py` | `POST /api/orders/sheet` | Orders > More ways in > upload/reconcile sheet; download demo sheet |
| `orders.py` | `POST /api/orders/sheet/{action_id}/apply` | Orders > More ways in > upload/reconcile sheet; download demo sheet |
| `orders.py` | `GET /api/orders/sheet/demo.xlsx` | Orders > More ways in > upload/reconcile sheet; download demo sheet |
| `orders.py` | `POST /api/orders/catalog` | Orders > More ways in > catalog in one sentence > review/apply |
| `orders.py` | `POST /api/orders/catalog/{action_id}/apply` | Orders > More ways in > catalog in one sentence > review/apply |
| `owner.py` | `GET /api/owner/today` | Today > decision feed |
| `owner.py` | `POST /api/owner/cards/{action_id}/apply` | Today > why, offered choices, apply/reject, or enable proven advice |
| `owner.py` | `POST /api/owner/cards/{action_id}/reject` | Today > why, offered choices, apply/reject, or enable proven advice |
| `owner.py` | `GET /api/owner/cards/{action_id}/why` | Today > why, offered choices, apply/reject, or enable proven advice |
| `owner.py` | `POST /api/owner/advice/{kind}/enable` | Today > why, offered choices, apply/reject, or enable proven advice |
| `owner.py` | `POST /api/owner/ask` | Today > Ask |
| `owner.py` | `GET /api/owner/morning` | Today > morning summary |
| `owner.py` | `GET /api/owner/outbox` | **New** Outbox (owner decision drafts) |
| `paperwork.py` | `POST /api/paperwork/sheets/print` | Orders detail; Paperwork hub print/reprint |
| `paperwork.py` | `GET /api/paperwork/sheets/attention` | Paperwork screen and hub |
| `paperwork.py` | `GET /api/paperwork/sheets/state/{unit_id}` | No direct caller: diagnostic helper; attention/resolve expose the same sheet state |
| `paperwork.py` | `POST /api/paperwork/sheets/void` | Paperwork hub > cancelled sheet |
| `paperwork.py` | `GET /api/paperwork/resolve` | No direct caller: Scan/mark calls the same resolver on the server and shows its warnings |
| `paperwork.py` | `GET /api/paperwork/notes` | Paperwork hub > delivery notes |
| `paperwork.py` | `PUT /api/paperwork/customers/{customer_id}/email` | Paperwork hub > customer contact |
| `paperwork.py` | `POST /api/paperwork/notes/{shipment_id}/email` | Paperwork hub > draft delivery email |
| `paperwork.py` | `GET /api/paperwork/outbox` | Paperwork hub > email outbox |
| `paperwork.py` | `POST /api/paperwork/outbox/{outbox_id}/send` | Paperwork hub > send (dry-run until configured) |
| `paperwork.py` | `GET /paperwork` | Paperwork screen; Shipping > Paperwork |
| `paperwork.py` | `GET /paperwork/sheets/batch/{batch_id}` | Orders detail / Paperwork hub > successful print URL |
| `paperwork.py` | `GET /paperwork/sheets/order/{order_id}` | No direct caller: legacy per-order render; print workflow opens the recorded batch URL |
| `paperwork.py` | `GET /paperwork/note/{shipment_id}` | Shipping > print; Paperwork hub > delivery note |
| `paperwork.py` | `GET /paperwork/outbox/{outbox_id}` | Paperwork hub > attachment preview |
| `paperwork.py` | `GET /u/{token}` | Printed QR > Sign in > Work scan |
| `paperwork.py` | `GET /u/{token}/{copy}` | Printed copy QR > Sign in > Work scan |
| `shelf_count.py` | `GET /api/shelf/items` | **New** Shelf > stock item picker |
| `shelf_count.py` | `POST /api/shelf/count` | **New** Shelf > photo count |
| `shelf_count.py` | `GET /api/shelf/{action_id}` | **New** Shelf > read stored offer and boxes |
| `shelf_count.py` | `POST /api/shelf/{action_id}/apply` | **New** Shelf > Yes/No to stored count |
| `simulator.py` | `GET /api/sim/status` | **New** Live shop > simulator state |
| `simulator.py` | `GET /api/live/snapshot` | **New** Live shop > initial/reconnect snapshot and recent ticker (new endpoint) |
| `simulator.py` | `POST /api/sim/start` | **New** Live shop > start demo |
| `simulator.py` | `POST /api/sim/stop` | **New** Live shop > pause demo |
| `simulator.py` | `GET /api/live` | **New** Live shop > SSE stream |

FACT: audited explicit routes: 102.

## Run / Запуск

The supplied venv launcher fails because its configured Python base executable is missing. Used the exact interpreter fallback documented in `NOTES-merge-cx.md`; no packages installed and no network dependencies added.

```powershell
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE = '1'
$python = 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe'
& $python -B -m pytest -q tests -p no:cacheprovider --basetemp=data/screens-tests

$env:FLOORHAND_DB = 'E:/Work/Floorhand/wt/cx-screens/data/screens-demo.db'
$env:FLOORHAND_META_LIVE = '0'
& $python -B -m floorhand.seed --fresh
$env:FLOORHAND_SIMULATE = '1'
$env:FLOORHAND_SIM_SPEED = '60'
& $python -B -m uvicorn floorhand.main:app --host 127.0.0.1 --port 8870
```

Open `http://127.0.0.1:8870/`, choose the invented owner for Live shop/TV and all office screens, or the invented manager for Channels/Shelf. Sign in as a worker for marking, claims and material requests. Run the repeatable HTTP smoke in another shell with `& $python -B tests/screens_smoke.py`. It mutates the disposable demo database only. Stop the task-owned server with Ctrl+C.

The requested `E:/Work/Floorhand/wt/cx-screens.db` is outside this worktree. To obey the explicit no-outside-writes rule, seeded the database under the ignored `data/` directory inside this worktree instead. No attempt was made to write to the outside path.

Standalone JS checks: `node --experimental-vm-modules tests/frontend_screens.mjs`. They also run from `tests/test_static.py` when Node is available. No npm packages are needed. These are DOM/transport unit doubles, not browser rendering tests.

## Verification / Проверка

- FACT: initial baseline full suite: 574 passed, one optional Pillow test skipped.
- FACT: final full suite: **579 passed, 1 skipped** in CALC: 206.38 seconds. The skip is the existing optional Pillow test. The existing Starlette/httpx deprecation warning remains. Final front-end checks also pass after removing the stale stock quantity from the item-picker label; the proposal shows the authoritative before/after values.
- Added backend tests for money-free snapshot permissions/content, recent-history bounds, timestamps and ticker fields. Added module interaction checks for role navigation, query parsing/disposal, structured edit choices, CSV preview and stored decisions, shelf normalization/boxes/Yes/No/retry/stale response, SSE dedup/reconnect/resync, TV cleanup, draft escaping, tablet setup/worker chooser, material request, audio proposal rejection, claim withdrawal and shell listener cleanup across refreshes. Static checks enforce the complete audit and opt-in hero motion.
- FACT: fresh seed CLI summary: orders 200, units 306, events 1081, shipments 60 (base generator summary; feature seeds add fictional stories).
- FACT: server launched on loopback port 8870 with `FLOORHAND_SIMULATE=1`, speed 60, Meta off. HTTP smoke passed all listed office data paths, guest 401 and tablet/worker 403 checks, owner-only feed, CSV preview, shelf offer/read/confirm and unchanged stock before confirmation. Counter reported `offline-components`, counted three synthetic parts and displayed three boxes in the API data. Received a real SSE mark with product, stage translation, unit sequence and shop timestamp. Initial live snapshot contained five stages and eight recent marks. Evidence: ignored `data/screens-smoke.json`.
- The task-owned server was stopped after smoke checks; no other server was stopped. No live model calls, external messages, package installs or commits were made.

## Remaining / Ограничения

- Browser visual QA could not run. Automatic approval review rejected opening `http://127.0.0.1:8870` in Chrome because origin access was declined. No alternate browser route was attempted. Phone layout at 360px, actual camera/file-picker behavior, full-screen TV framing, dark theme and reduced-motion rendering still need an allowed browser/device review. CSS has responsive single-column layouts, scrolling navigation and opt-in animation; unit tests validate the interaction contracts only.
- Live SAM detections and server audio transcription were not exercised against paid Meta models. The offline photo path was exercised end to end; it counts separated foreground shapes and can miscount touching/hidden parts, as the screen explicitly explains. Browser canvas converts PNG/JPEG to PNG so the dependency-free counter works without Pillow. Older browsers without `createImageBitmap` receive the readable-photo error.
- Simulator intentionally obeys configured shop working hours, trade boundaries and phone activity; increasing speed does not bypass those rules. Outside working hours use the recorded snapshot/ticker or run the demo during shop hours. Decorative rail motion is not presented as fabricated shop activity.
- Owner outbox content is stored by the existing backend in its draft language; screen controls and explanations are EN/RU. Delivery-email preview/send remains in the existing Paperwork hub and follows its dry-run/configuration rules.

РУ: Полный аудит маршрутов и команды запуска приведены выше. Проверка HTTP прошла, офлайн-фото посчитано и подтверждено, событие SSE получено. Демо-база сохранена только внутри рабочего дерева. Сервер остановлен. Доступ браузера отклонён автоматической проверкой разрешений; визуальная проверка на телефоне и ТВ ещё нужна. Реальные модели, письма и данные мастерских не использовались.
