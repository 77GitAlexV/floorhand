# fix1 integration / Интеграция fix1

## Changes / Изменения

- Completed `NOTES-screens.md` with the real callers of ripple, rush, slang and
  gate routes. FACT: the route audit now covers all 120 explicitly declared routes.
- Today now shows the gate summary and links to Gatekeeper alongside its ripple
  decision cards and What if/rush forms. Floor uses the same translated summary
  link. A failed gate request leaves the main screen and ripple controls usable.
- The slang catalog accepts authenticated read requests from the office as well
  as workers. It selects identifiers and words only, without prices. Creating,
  correcting and confirming work proposals still requires a worker session.
- Added a final seed phase so the stored ripple scenario describes the complete
  queue after slang and all other features add their demo work. The existing
  fingerprint regression confirms that the scenario is immediately usable.
  Ripple's small test queues now provide the invented fabric and finish required
  by gate; their original scheduling and financial assertions are unchanged.
- Extended the real ES-module interaction check to evaluate
  `js/screens/index.js`, verify every imported screen registers, and render Floor
  and Today with both integrations in English and Russian, including gate errors
  and escaped summary text. The merged `marking.js` retains both `apiFetch` and
  `go`; the complete module graph resolves both exports.
- Added `tests/fix1_smoke.py` and a seeded regression in `tests/test_fix1.py`.
  The smoke discovers the target GET declarations, so a newly added route cannot
  silently escape the audit. It creates an offline shelf count proposal, reads
  each route as the seeded owner and worker, checks guests, verifies financial
  fields are absent from worker responses, and confirms stock is unchanged.

РУ: Дополнен аудит маршрутов. На экранах «Сегодня» и «Цех» одновременно работают
входной контроль и сценарии сбоев/срочных заказов. Статус входного контроля ведёт
к уточнению параметров заказа; ошибка этого запроса не ломает основной экран.
Офис может читать словарь без цен, но отмечать работу по-прежнему может только
работник. Добавлены проверки общего реестра экранов, двух языков и прав доступа.

## Run / Запуск

The supplied venv launcher was attempted and failed because its configured base
Python executable is missing. Used the fallback from `NOTES-merge-cx.md`.
No packages were installed or environment files changed.

```powershell
$python = 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe'
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages;E:/Work/Floorhand/wt/cx-fix1'
$env:PYTHONDONTWRITEBYTECODE = '1'
New-Item -ItemType Directory -Force data/fix1 | Out-Null

& $python -B -m pytest -q tests -p no:cacheprovider --basetemp=data/fix1/resume-final-tests
node --experimental-vm-modules tests/frontend_screens.mjs
node --experimental-vm-modules tests/ripple-ui.mjs
node --experimental-vm-modules tests/glue_ui.mjs

$env:FLOORHAND_DB = 'E:/Work/Floorhand/wt/cx-fix1/data/fix1/fix1.db'
$env:FLOORHAND_META_LIVE = '0'
$env:FLOORHAND_SIMULATE = '0'
& $python -B -m floorhand.seed --fresh
& $python -B tests/fix1_smoke.py --output data/fix1/get-smoke.json

# Optional interactive demo, using the same database:
& $python -B -m uvicorn floorhand.main:app --host 127.0.0.1 --port 8871
```

The requested `E:/Work/Floorhand/wt/cx-fix1.db` is outside this worktree. To obey
the explicit instruction to write only inside the worktree, the fresh seed is at
`data/fix1/fix1.db` instead. No outside database was created or modified.

## Verification / Проверка

- FACT: final full suite: **643 passed, 1 skipped, 1 warning** in CALC: 473.93
  seconds. The skip is the existing optional Pillow test; the warning is the
  existing Starlette/httpx deprecation. Ran the full suite once after resuming,
  following targeted checks. Log: `data/fix1/resume-final-tests.log`.
- FACT: resumed targeted ripple/integration/route-audit tests: 32 passed.
- All three standalone Node checks passed. These execute the real modules with
  DOM/transport doubles; no npm packages or browser driver are required.
- FACT: resumed fresh seed CLI summary: 200 base orders, 306 base units,
  1141 events, 64 shipments. Feature seeds add their own invented demonstration
  stories; totals vary with the shop date.
- FACT: all 10 target GET routes returned HTTP 200 with data for owner person 30.
  Worker person 1 received HTTP 200 from `/api/slang/catalog` and
  `/api/gate/summary`; the other 8 office/financial surfaces returned HTTP 403.
  All 10 routes returned HTTP 401 without a session. Worker JSON was checked
  recursively for financial fields. Owner work-proposal POST remained HTTP 403.
- The shelf GET used an actual offline count proposal for a synthetic PNG;
  its status stayed `proposed`, and stock quantities were unchanged.
- Smoke calls use FastAPI TestClient through the real ASGI routes, login cookies,
  dependencies and role checks. Evidence is in ignored
  `data/fix1/get-smoke.json`, with seed/smoke/test logs beside it.
- No commits, real model calls, external messages, package installs or writes
  outside this worktree were made. All seed data and the test photo are invented.

## Remaining / Ограничения

- Browser/device visual QA and real microphone/camera behavior were outside this
  integration check. Node checks cover imports, registration and interactions.
- Live Meta transports were not exercised; the smoke runs with Meta disabled.
- The existing optional Pillow test and Starlette/httpx deprecation warning are
  environment limitations; no dependency installation was attempted.

РУ: ФАКТ: полный набор — 643 теста пройдены, один существующий необязательный
тест Pillow пропущен. Все три проверки Node пройдены. Свежая демо-база находится
внутри рабочего дерева. ФАКТ: владелец получил
данные по всем 10 GET-маршрутам; работник получил только разрешённые ответы без
денег, остальные запросы отклонены. Без входа все эти маршруты возвращают 401.
Остатки не изменены: подсчёт фото остался предложением. Проверки браузера,
реального микрофона, камеры и платных моделей не выполнялись. Коммит не создан.
