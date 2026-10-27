# Glue / Руки в клею

Implemented two features with invented demo data only. No packages were installed,
no live Meta requests were made, and no git commit was created.

## Built

- `floorhand/features/slang.py`: the shop database owns its EN/RU product, option,
  stage and component vocabulary. Exact learned phrases resolve before the model.
  New wording uses `models.respond(..., 'slang-resolve', ...)` with Muse Spark's
  existing budget guard, catalog identifiers and dictionary context. Model-off,
  budget exhaustion and malformed output leave an editable proposal.
- A corrected proposal learns its final meaning only after successful confirmation.
  The alias records the worker, shop-local timestamp and proposal id; revisions are
  retained. Gatekeeper lets the office review learned aliases and remove them from
  use, retaining who removed them and when.
- Exact units are stored in the existing marking proposal. Confirmation applies by
  id through the existing marking/domain rules. A different trade, an incomplete
  batch, competing orders, an expired proposal or changed product/leg option cannot
  silently become a mark. Identifying assignments can disambiguate one whole batch;
  otherwise the worker must correct the phrase or scan the sheet.
- Seat-cushion phrases record append-only component counts, visible to the office.
  A component count does not finish a whole chair or create piece pay. This keeps
  the phrase “three seat cushions for Harbor” useful without inventing completed
  chairs. Component records use the work-day checks and proposal-id idempotency.
- `static/js/screens/handsfree.js`: Glue mode has a palm-sized push-to-talk control,
  large read-back sentence, large Yes/No buttons, speechSynthesis, and spoken
  `yes/да` or `no/нет`. Listening for confirmation starts after read-back ends.
  Correction and typed-entry fallbacks are available. The existing Speak flow
  forwards slang proposals into this screen with the same stored proposal id.
  Existing tap marking stays available.
- `floorhand/features/gate.py`: catalog-linked required options get family defaults:
  upholstered chairs, sofas, daybeds and benches require fabric and leg finish;
  poufs require fabric; other product families require frame colour. These are
  required-field defaults, never guessed specification values. Newly added catalog
  products receive their declarations through the feature-owned schema.
- Missing/unknown/ambiguous specifications stay outside cutting queues, taking work,
  the office planner and direct/QR work marks. Undoing cutting cannot bypass the gate.
  Work already beyond cutting remains visible.
- `static/js/screens/gate.js`: the office sees the reason, original intake quote,
  a bilingual customer question saved in the existing draft outbox, and a box for
  the customer's answer. Answer extraction uses intake's catalog normalization;
  only a unique catalog value is proposed. Confirmation accepts a stored id and
  checks the order snapshot under a SQLite write lock. Changed, cancelled or
  started orders require a new proposal or the existing order-change review.
- The owner Floor view shows `FACT: held at the gate: N lines` (also in Russian).
  The summary is money-free. Office detail and dictionary review are role protected.

Minimal integration hooks are in domain queue/marking, planner, intake source
capture, marking proposal/confirmation, the existing Speak helper, screen imports
and the Floor banner. Feature discovery needs no main.py or db.py changes.

## Run and demo

The supplied venv launcher failed because its configured base interpreter is absent.
Use the existing fallback, from this worktree:

```powershell
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE = '1'
$py = 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe'
$env:FLOORHAND_DB = "$PWD/data/glue-demo.db"
& $py -B -m floorhand.seed --fresh
& $py -B -m uvicorn floorhand.main:app --port 8872
```

Sign in as the first assembly worker and open **Glue mode** (`#/handsfree`).
The seed supplies an assigned Harbor batch with curved legs. Say or type
`собрал два харбора на кривых ногах` or
`assembled two harbors on curved legs`, listen and confirm. Confirming again records
once. For learning, use a new phrase such as `два харбора дожал на дугах`, correct
the product/quantity/stage/curved-legs option and confirm. On another eligible batch,
the identical words resolve offline. The dictionary is scoped to this shop database.

On a sewing worker, `три сидушки на харбор` demonstrates part-work counting.
The office can see the count under **Recorded part work** in Gatekeeper.

Sign in as office and open **Gatekeeper** (`#/gate`). The seeded Harbor line has
fabric but lacks leg finish. Save its question, inspect it in the existing outbox,
paste `Ножки ореховые` or `Leg finish: walnut`, inspect the quoted proposal and
confirm. The line becomes eligible for cutting; the Floor gate count decreases.
Demo timestamps come from seeded shop history so paperwork stays reproducible.

## Offline evaluation

FACT: `tests/eval/slang.jsonl` contains **48 phrases**, EN and RU, with **24** marked
as a different style from the starter vocabulary. It includes ordinary composites,
novel slang, negation, future/uncertain work, competing products and competing stages.

CALC: offline exact semantic accuracy is **42/48 = 87.5%**. All product, quantity,
stage, option and component fields must match. There are **36** positive examples
and **12** expected refusals. The resolver resolves **30/36 = 83.3%** of positive
examples; **6** unfamiliar positives require correction, and all **12** refusal
examples are refused. Total abstentions: **18**.

CALC: silent-error rate is **0/48 = 0%** of all phrases, also **0/30 = 0%** of
resolved phrases. A silent error means returning a meaning that differs from the
annotated meaning, including accepting a refusal case. An abstention on a positive
phrase counts as an accuracy miss, not as a silent error. These are corpus results,
not a measured real-world audio error rate. The eval never calls a model.

```powershell
& $py -B -m pytest -q tests -p no:cacheprovider --basetemp=.glue-check
& $py -B -m pytest -q -s tests/test_glue.py::test_slang_eval_corpus -p no:cacheprovider --basetemp=.glue-eval
node tests/glue_ui.mjs
```

The new tests cover proposals without operational writes, learned corrections,
history/deletion, bilingual spoken confirmation, role/money boundaries, negative
phrases, stale/expired proposals, exact batch selection, component append-only
records, catalog defaults, the gate across floor/planner/direct marking, quoted
outbox drafts, customer answers, confirmation retries and stored-value enforcement.
Older channel, simulator and marking fixtures now declare the invented options
needed to exercise their original readiness rules under the gate.

FACT: final full-suite validation: **607 passed, 1 skipped**, using the documented
fallback interpreter. The existing optional Pillow check is skipped because Pillow
is unavailable. The existing Starlette/httpx deprecation warning remains. The Node
UI event checks and JavaScript syntax checks passed. Whitespace validation passed
with CRLF-aware checking for the existing Windows files. Temporary databases and
validation logs remain in `.glue-*` paths: policy review rejected both bounded
cleanup commands with “blocked by policy.” All paths were verified inside this
worktree; no alternate deletion mechanism was attempted. The test server was stopped.

## Remaining limits

- Browser visual QA was blocked: browser approval rejected opening the loopback
  demo because network-egress authority was denied. No alternate browser or raw
  browser-control workaround was used. JavaScript syntax checks and isolated Node
  UI event tests cover read-back ordering, spoken confirmation, corrections,
  same-id retries, cleanup and recognition fallback; actual microphone/speaker
  behavior and phone layout still need a browser/device check.
- Cleanup was also blocked by tool policy. The generated `.glue-debug`, `.glue-demo`,
  `.glue-final`, `.glue-new`, `.glue-regression`, `.glue-regression2`, `.glue-tests`,
  `.glue-tests2`, `.glue-tests3` directories and `.glue-*.log` validation files remain
  untracked in this worktree. They are task-created demo/test artifacts, not source
  changes to include in the reviewer's commit.
- Browser speech recognition depends on browser support and microphone permission;
  some implementations use a browser vendor's recognition service. Offline slang
  resolution works on transcripts; a disconnected phone is not an offline ASR
  engine. Typed entry and existing tap/offline-queue marking remain available.
- Muse Spark is wired and tested with fake responses, but live model quality was
  not measured. Existing model configuration and budget settings are required for
  live use. Unknown offline slang deliberately asks for correction.
- Component counts have no per-order allocation, automatic piece rates or undo UI.
  Whole-piece completion and its existing undo/rework flow remain separate.
- Requirements are catalog rows with family defaults; there is no custom
  requirement-editor screen. Fabric and finish values use the existing shop
  catalog, so unknown customer options must first be added there.
- Customer questions remain draft outbox entries without an automatic recipient
  address. No mail, marketplace message or other external delivery occurs here.

## По-русски

Готовы голосовые отметки со словарём цеха и входной контроль неполных заказов.
Исправленный смысл запоминается только после подтверждения человеком. Офис видит
автора и время обучения и может убрать слово из использования, сохранив историю.
Без обязательных параметров строка не попадает в раскрой; вопрос клиенту остаётся
в черновиках исходящих. Ответ сначала становится предложением с цитатой, затем
подтверждается по сохранённому номеру предложения.

РАСЧЁТ: офлайн-точность **42/48 (87,5%)**, молчаливых ошибок **0/48**. Незнакомые
фразы требуют исправления. Учёт сидушек не завершает целое кресло и не начисляет
оплату. Проверка интерфейса в браузере заблокирована разрешениями; автоматические
проверки событий интерфейса выполняются без браузера и сети. Установка пакетов,
живые запросы к моделям, внешняя отправка и git commit не выполнялись.

ФАКТ: итоговая проверка — **607 тестов пройдено, 1 пропущен** (необязательный
Pillow). Удаление временных баз и журналов заблокировано политикой инструментов;
артефакты `.glue-*` остались в рабочем дереве. Тестовый сервер остановлен.
