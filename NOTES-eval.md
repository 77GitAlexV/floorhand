# Order intake quality harness / Оценка качества приёма заказов

Built / Сделано:

- `tests/eval/intake_cases.jsonl`: FACT / ФАКТ — 72 fictional cases, 84 gold orders; 36 EN and 36 RU, with 12 cases per language/kind combination. Only the seed catalog and fictional unknown customers are used. / 72 вымышленных примера, 84 эталонных заказа; 36 EN и 36 RU, по 12 примеров каждого языка и типа. Только демокаталог и вымышленные неизвестные клиенты.
- Messages cover informal chat, typos, emojis, forwarded history, multiple orders, quantity corrections, missing/ambiguous fields, word quantities, relative dates, and shuffled CSV/TSV/semicolon headers. / Покрыты переписка, опечатки, эмодзи, пересылки, несколько заказов, поправки количества, пропуски, неоднозначность, числа словами, относительные даты и таблицы с перемешанными колонками.
- `floorhand/eval_intake.py`: calls the public `propose()` against an isolated clone of a freshly seeded temporary database for each case. Fixes `domain.today()` to each case's shop date; creates proposals, never applies them. Temporary files stay inside this worktree and are removed. / Каждый пример вызывает публичный `propose()` на отдельной копии демобазы с датой из примера. Заказы не применяются; временные файлы находятся внутри рабочей копии и удаляются.
- Reports correct, asked (`check`), silent error (`ok` but wrong), and missed fields overall, by language and kind. Matches orders one-to-one regardless of output order. Extra orders count too; errors list case IDs and expected/actual values. / Отчёт показывает верные поля, вопросы, тихие ошибки и пропуски, суммарно и по языкам/типам. Порядок предложений не влияет на сопоставление; лишние заказы тоже учитываются. Для тихих ошибок указаны ID примера и значения.
- Offline parsing now handles demo Russian words/inflections, English/Russian word quantities, `шт`, relative dates, local customer/date context, latest single-order quantity corrections, and forwarded history. Partial/conflicting variants and dates ask for review. Table fabric and frame cells retain a real source quote. / Добавлены русские слова демокаталога, числа словами, `шт`, относительные даты, локальные клиент/срок, поправки количества и обработка пересылок. Неоднозначные варианты и даты требуют проверки. Цитаты из таблиц сохраняются.
- Grounding reads evidence from source quotes instead of trusting an unsupported model value; missing quotes cannot produce accepted facts. Existing public functions, confirmation, and offered-pick restrictions remain in use. / Сервер проверяет цитаты, а не доверяет неподтверждённому значению модели; без цитаты поле не принимается. Сохранены публичные функции и подтверждение сохранённых предложений.

## Before / After — До / После

Baseline measured before editing `intake.py`, starting from revision `a9c677a`, on the same fixed corpus. / Базовый замер сделан до изменения `intake.py`, от ревизии `a9c677a`, на том же наборе.

CALC / РАСЧЁТ:

| Metric / Метрика | Before / До | After / После |
|---|---:|---:|
| Correct fields / Верные поля | 237 | 481 |
| Asked fields / Вопросы | 24 | 23 |
| SILENT ERROR fields / Тихие ошибки | 12 | 0 |
| Missed fields / Пропуски | 237 | 0 |
| Compared fields / Сравниваемые поля | 510 | 504 |
| Correct / Верно | 46.47% | 95.44% |
| Silent error rate / Доля тихих ошибок | 2.35% | 0.00% |

The denominator is all compared fields, including extra orders. Before had one extra stale forwarded order; afterward all gold orders are accounted for. The silent-error target of ≤1% is met on this corpus. / Знаменатель — все сравниваемые поля, включая лишние заказы. До исправления был лишний заказ из старой пересылки; после учтены все эталонные заказы. Цель ≤1% достигнута на этом наборе.

The remaining questions are deliberate: ambiguous/typo products, omitted quantities/customers, unknown customers, invalid dates, partial fabrics. A shared `variant` review status counts as a question for both fabric and frame. / Оставшиеся вопросы относятся к неоднозначным моделям и опечаткам, пропущенным количествам/клиентам, неизвестным клиентам, неверным датам и неполным тканям. Общий статус проверки варианта считается вопросом и для ткани, и для каркаса.

Saved reports / Сохранённые отчёты: [before / до](reports/intake-eval-before.md), [after / после](reports/intake-eval.md).

## Run / Запуск

From the repo root, with a working project Python / Из корня репозитория с рабочим Python проекта:

```powershell
& 'E:/Work/Floorhand/floorhand/.venv/Scripts/python' -m floorhand.eval_intake --model offline
& 'E:/Work/Floorhand/floorhand/.venv/Scripts/python' -m pytest -q
```

The default model is offline. The report is `reports/intake-eval.md`; `--cases PATH` and `--report PATH` allow other corpora/destinations. Exit status fails on a case exception or a silent rate above the target. / По умолчанию используется офлайн-режим. Отчёт: `reports/intake-eval.md`; доступны `--cases PATH` и `--report PATH`. При сбое примера или превышении целевой доли тихих ошибок команда завершается с ошибкой.

`--model spark` uses only `floorhand.models` and existing live/key/budget configuration. It never enables live mode itself. All cases carry forward the model-call ledger, so the per-run budget cannot reset between cases. Requested and actual models are reported separately, including offline fallback. / `--model spark` использует только `floorhand.models` и существующие настройки ключа/бюджета. Live-режим не включается автоматически. Журнал затрат переносится между примерами; бюджет не сбрасывается на каждом примере. В отчёте раздельно указаны запрошенная и фактическая модели, включая офлайн-путь.

## Verification and limits / Проверка и ограничения

The supplied virtualenv executable fails because its configured base Python is missing. No packages were installed and no external files were changed. Verification used the existing Codex Python runtime, its compatible native dependencies, and the project's installed pure-Python packages appended to the import path. / Указанное виртуальное окружение не запускается: отсутствует базовый Python. Пакеты не устанавливались, внешние файлы не менялись. Проверка выполнена установленным Python среды Codex с совместимыми бинарными зависимостями и пакетами проекта в конце пути импорта.

Actual verification command / Фактическая команда проверки:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
& 'C:/Users/muziny/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -c "import sys; sys.path.append('E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); import pytest; raise SystemExit(pytest.main(['-q', '--basetemp=.pytest-eval-tmp']))"
& 'C:/Users/muziny/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -c "import sys; sys.path.append('E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); from floorhand.eval_intake import main; raise SystemExit(main())"
```

Tests cover scoring, order alignment, extra/missing orders, database/environment isolation, cumulative budget history, model-off enforcement, exception reporting, corpus quality thresholds, and new parsing/grounding rules. / Тесты проверяют метрики, сопоставление заказов, лишние/пропущенные заказы, изоляцию базы и окружения, перенос журнала затрат, принудительный офлайн-режим, отчёт о сбоях, пороги качества и новые правила разбора/проверки цитат.

FACT / ФАКТ: final full suite — **109 passed**, with one existing Starlette/httpx deprecation warning. `git diff --check` passed. The final offline evaluation completed without case failures. / Итог: **109 тестов пройдено**, одно предупреждение об устаревшем пути Starlette/httpx. `git diff --check` пройден. Финальная офлайн-оценка завершилась без сбоев примеров.

Missing / Не сделано:

- Spark live evaluation was not run: this task forbids network access. No external calls or model spend occurred. / Live-замер Spark не выполнен: задача запрещает сеть. Внешних вызовов и затрат на модели не было.
- This is a development corpus used to improve rules, not independent held-out evidence. General Russian morphology, arbitrary catalogs, and unrestricted conversation history need further cases. / Набор использован для разработки правил; это не независимая отложенная выборка. Произвольная русская морфология, другие каталоги и сложная история переписки требуют дополнительных примеров.
- Weekdays resolve to the upcoming occurrence; `next Friday` on Friday advances a week. `к 15-му` uses the next occurrence of that day of the month. Ambiguous multi-order quantity corrections ask which order changed. Conflicting dates ask rather than infer a correction. / День недели означает ближайшее наступление; «следующая пятница» в пятницу — через неделю. «К 15-му» означает ближайшее 15-е число. При неоднозначной поправке нескольких заказов система задаёт вопрос; конфликтующие даты тоже требуют уточнения.
- Case alignment is bounded to at most 16 orders per message. Split larger evaluation inputs. Unknown variants can require revising the source, rather than choosing a guessed material. / Сопоставление ограничено 16 заказами на сообщение; большие примеры нужно разбивать. Для неизвестного варианта может потребоваться уточнить исходный текст.

## Held-out set (written by a different model, gpt-6-astra, without seeing the parser) — 27.10.2026
tests/eval/intake_heldout.jsonl: 40 cases (20 EN, 20 RU), 60 orders, 384 fields. Offline rules:
correct 255 (66.4%) · asked 56 (14.6%) · SILENT ERRORS 9 (2.3%) · missed 64 (16.7%).
Silent errors: customer 3, due date 4, frame 2. The 95% / 0% on the author's own set is optimistic;
this is the honest offline baseline. Muse Spark is to be measured on the same set once credits arrive.
