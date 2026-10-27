# Living shop / Живой цех

Built `floorhand/features/simulator.py` and `tests/test_simulator.py`. The feature loader discovers the schema, routes and router lifespan automatically; no hook or changes to `main.py`, `db.py`, domain functions, or the frontend are needed.

Добавлены отдельный модуль симулятора и тесты. Модуль подключается автоматически; изменения общих файлов и интерфейса не нужны.

## Run / Запуск

Use a fictional seeded demo database. In PowerShell from the repository root:

```powershell
$python = 'E:/Work/Floorhand/floorhand/.venv/Scripts/python'
& $python -m floorhand.seed --fresh
$env:FLOORHAND_SIMULATE = '1'
$env:FLOORHAND_SIM_SPEED = '60'
& $python -m uvicorn floorhand.main:app --port 8000
```

`--fresh` replaces the selected demo database; omit it when keeping existing demo work. Automatic simulation is off unless `FLOORHAND_SIMULATE=1`. An authenticated owner may also explicitly start it through the API while automatic startup is off. Use a single Uvicorn worker for the demo.

`--fresh` пересоздаёт выбранную демо-базу. Для сохранения текущих данных пропустите эту команду. По умолчанию симулятор выключен; владелец также может запустить его через API. Для демонстрации используйте один процесс Uvicorn.

FACT / ФАКТ — configuration defaults:

| Variable | Default | Meaning / Значение |
| --- | --- | --- |
| `FLOORHAND_SIMULATE` | unset | Automatic startup only with `1` / Автозапуск только при `1` |
| `FLOORHAND_SIM_SPEED` | `1` | Working-time multiplier / Ускорение рабочего времени |
| `FLOORHAND_SIM_ORDERS_PER_HOUR` | `0.2` | Expected email orders per accelerated working hour; `0` disables / Ожидаемое число заказов в ускоренный рабочий час; `0` выключает |
| `FLOORHAND_SIM_UNDO_CHANCE` | `0.015` | Probability of immediately undoing a simulated mark / Вероятность немедленной отмены демо-отметки |
| `FLOORHAND_SIM_BREAK_CHANCE` | `0.08` | Probability of a short stage break when ready to mark / Вероятность короткого перерыва участка перед отметкой |

FACT / ФАКТ — the shop works weekdays, local shop time, from 08:00–12:00 and 13:00–17:00. The time zone comes from `domain.now` and the shop's `settings.tz`. Random breaks last 3–10 accelerated working minutes. One worker at each stage is deliberately slower. Existing assignments are honoured.

CALC / РАСЧЁТ — speed `60` accrues one shop hour of work budget per elapsed working minute. It changes pacing, not the calendar: events keep their actual shop work date. Daily stage capacity includes all human and simulated `done` records, even subsequently undone ones. The simulator stops adding work at capacity, does not spend future days' capacity and does not catch up a suspended process. For a longer demo, begin with enough remaining daily capacity; setting a higher speed does not remove the daily limit.

FACT / ФАКТ — phone sessions are protected for five minutes after sign-in or their most recent authenticated API request. Feature-owned `simulator_activity` records last activity by session token and joins the existing `sessions` table. A router-lifespan ASGI wrapper observes existing human routes; long-lived live streams refresh activity at each heartbeat. Shared tablet sessions do not reserve a person. Active workers' assigned units are left alone. Activity, assignment, capacity, stage and opening hours are rechecked before applying a planned mark.

Все рабочие отметки и отмены проходят через `domain.mark_done` и `domain.undo`. Новые заказы создаются через `domain.create_order` с источником `email`, вымышленной демонстрационной тканью и существующими демо-товарами и демо-заказчиками. Симулятор не отправляет письма и не изменяет денежные таблицы напрямую.

## API / Интерфейс API

- `GET /api/sim/status`: office only; `running`, `speed`, `events_made`, `error`, `fact`, English/Russian messages.
- `POST /api/sim/start`, `POST /api/sim/stop`: owner only; repeated calls are safe. Start resets `events_made` for that run. It counts new simulated marks, visible undo records and orders; human actions are excluded.
- `GET /api/live`: any authenticated session, including the shared tablet. Money is never included.

FACT / ФАКТ — the live feed polls new database rows every second, even with simulation stopped, and sends a heartbeat comment every fifteen seconds. The simulator also polls immediately after applying its own actions. Events are deduplicated by database row cursors. Logout closes the stream. Subscriber queues are bounded and disconnected clients are removed.

Live event names and payloads / Имена событий и поля:

- `marked`, `undo`: `id` (work event), `kind`, `unit_id`, `stage_id`, `number` (order number), `stage`, `stage_ru`, `worker` (first name), `fact`, `message`, `message_ru`.
- `order_created`: `id`, `number`, `source`, `fact`, `message`, `message_ru`.
- `shipment`: `id`, `numbers` (distinct order numbers), `fact`, `message`, `message_ru`.
- `resync`: refresh the relevant screen after a slow consumer's queue overflows; includes bilingual messages and `fact`.

Frontend integration example (same-origin session cookie is sent automatically):

```javascript
const live = new EventSource('/api/live');
for (const name of ['marked', 'undo', 'order_created', 'shipment', 'resync']) {
  live.addEventListener(name, event => {
    const change = JSON.parse(event.data);
    // Animate the matching unit or refresh the relevant screen here.
    console.log(name, change);
  });
}
// Call live.close() when leaving the application or logging out.
```

## Verification / Проверка

Normal test command:

```powershell
& 'E:/Work/Floorhand/floorhand/.venv/Scripts/python' -m pytest -q
```

The provided virtual-environment launcher currently points to a missing Python installation. A compatible local Python bundled with Blender can reuse the existing dependencies without installing packages. This is the command used for validation; all generated test data stays within this worktree:

Указанный launcher сейчас ссылается на отсутствующий Python. Проверка выполнена совместимым локальным интерпретатором с уже имеющимися зависимостями, без установки пакетов:

```powershell
$env:PYTHONPATH = 'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -m pytest -q --basetemp=.tmp-simulator-tests
```

Tests cover deterministic pure planning; stage order, real and accelerated pacing, daily capacity including human work and undone marks, lunch/weekends/day rollover, slower workers and random breaks, phone activity and assignment protection, idempotent marks, visible undo, email orders, owner/office permissions, automatic startup and shutdown, actual background-thread writes/publication, cancellation, reported thread failures, feature discovery in the real app, SSE framing and human events, shipment privacy, bounded pub/sub, heartbeats, logout and disconnect cleanup.

FACT / ФАКТ — final full-suite result: **56 passed**, with one pre-existing Starlette/httpx deprecation warning.

## Remaining limits / Ограничения

- Frontend animation is left to the parallel frontend work; the endpoint and example above are ready. / Анимация интерфейса остаётся параллельной frontend-задаче; API готов.
- In-process pub/sub has no historical replay or `Last-Event-ID` recovery. Refresh current state on reconnect or `resync`. / Исторического воспроизведения нет; после переподключения обновляйте состояние экрана.
- Simulation never ships automatically; human shipments appear in the live feed. / Отгрузку выполняет человек; она появляется в ленте.
- There are no new package dependencies or Meta calls. Existing Starlette/httpx deprecation warnings remain; no packages were installed. / Новых зависимостей и вызовов Meta нет; существующее предупреждение Starlette/httpx не изменялось.
- Use the shop's real local working hours for the demo. Acceleration does not bypass lunch, weekends, the daily limit or phone protection. / Демонстрируйте в рабочее время цеха; ускорение не отменяет перерывы, выходные, дневную мощность и защиту телефона.
