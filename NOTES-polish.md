# Polish / Полировка

## Built / Готово

- Resumed the committed partial polish and resolved the three merge conflicts in
  `features/__init__.py`, `features/ripple.py` and `tests/test_ripple.py`.
  Both branches wanted forecasts built after all feature seeds: the finalizer
  runs after every seed, so the stored scenario describes the complete queue.
- What if uses ui.js chips, inputs, decision cards and a responsive stage timeline.
  The timeline keeps the API's shop wall clock (for example `Thu 14:46`), independent
  of the phone's time zone. Order rows show old/new ETAs, one action is primary,
  and customer notices start collapsed. Today uses the same ripple/rush cards.
- Lateness compares order identities with a no-disruption baseline. Each option
  states newly late, already late, total late and saved orders. Saw parsing uses
  the `cut` stage code rather than a stage position. A downstream backlog can
  absorb a cutting delay without changing final ETAs; stage delays remain visible.
  Regression examples include CALC: baseline late 1, newly late 1, total late 2.
- Rush has an independently registered screen with product, quantity and due-date
  inputs, optional customer/specification/margin fields, actionable catalog errors,
  fits verdict, displaced orders, overtime, surcharge and margin before/after.
  Retries retain their request key. Unknown costs remain unknown.
- Gate uses decision cards, singular English “1 line”, dictionary tables with
  audited soft deletion and explicit empty states for learned corrections/part work.
- Glue mode registers for workers and tablets; an owner sees worker sign-in
  guidance. The push-to-talk target is 240px tall and Yes/No targets 140px tall.
  Typed fallback opens when recognition is absent or the microphone fails; work
  still requires confirmation of the stored proposal. Recognition ending after a
  microphone error keeps the fallback label visible.
- EN/RU text, token colors, phone CSS for 360–420px and existing reduced-motion
  behavior are preserved. No packages, paid models, real data or external messages.

RU: экраны сценариев, срочного расчёта и входного контроля приведены к общим
компонентам. Новые опоздания отделены от прежних; сдвиги по этапам видны даже
при прежнем итоговом сроке. Работник может ввести фразу без микрофона и отдельно
подтвердить сохранённое предложение. Владельцу показано пояснение режима.

## Run / Запуск

Use the supplied Python when available. Its launcher fails here because its base
Python is missing, so validation uses the documented NOTES-merge-cx.md fallback:

```powershell
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:FLOORHAND_DB = 'E:/Work/Floorhand/wt/cx-polish/.polish/demo.db'
$env:FLOORHAND_META_LIVE = '0'
$env:FLOORHAND_SIMULATE = '0'
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m floorhand.seed --fresh
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m uvicorn floorhand.main:app --port 8874
```

Sign in as invented demo owner Alex (person 30). Open `#/ripple`, `#/rush`,
`#/gate` and `#/today`; sign in as a worker for `#/handsfree`. For rush material
specification, the demo accepts `velvet beige · walnut`. No models or microphone
are required for the typed flow. `.polish/` is ignored scratch space.

## Validation / Проверка

- FACT: 79 targeted ripple, gate/glue and static tests passed, plus the new
  default-seed owner/worker integration test passed separately.
- Node interaction checks cover real ES modules in EN/RU: rush form and retry,
  errors/empty catalog, Today integration, dictionary deletion, escaped text,
  shop-clock timeline, one primary decision, collapsed notices, worker registration,
  typed confirmation, microphone failure and owner guidance.
- FACT: the full suite ran once at the end: **651 passed, 1 skipped**, with the
  existing Starlette/httpx deprecation warning; elapsed time 475.92 seconds.
  Command (with the fallback environment above):

  ```powershell
  & 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m pytest -q -p no:cacheprovider --basetemp=.polish/tests-full-final
  ```

RU: ФАКТ — полный набор запущен один раз в конце: 651 тест пройден, один
пропущен. Сохранилось прежнее предупреждение Starlette/httpx.

## Limits / Ограничения

Browser access to the local demo was declined by the browser permission gate.
No alternative browser access was attempted. Actual screenshots and physical
360/420px layout or microphone testing remain for the reviewer; responsive rules,
role routing and event behavior were reviewed/tested without a browser.

Git's shared metadata is outside the permitted writable worktree. Conflict markers
are resolved in the working files, but the index still reports those three paths
as unmerged. The reviewer must stage the resolutions. No commit was made.
No new API routes were added; the merged route audit notes remain intact.

RU: браузер не получил разрешение открыть локальный адрес; визуальная проверка
на телефоне остаётся ревьюеру. Конфликты исправлены в файлах, но общий индекс Git
за пределами доступной для записи папки: ревьюеру нужно добавить решения в индекс.
Коммитов не было.
