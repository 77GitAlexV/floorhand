# Deploy handoff / Передача развёртывания

## Built / Готово

- Python 3.12 slim image, non-root UID/GID 10001, automatic first-start demo seed,
  HTTP healthcheck, read-only application filesystem with persistent `/data`.
  Образ Python 3.12 slim, UID/GID 10001 без root, автоматическая демобаза при первом запуске,
  HTTP-проверка готовности, файловая система приложения только для чтения и постоянный `/data`.
- Compose app + Caddy, domain from `.env`, automatic HTTPS, persistent database and certificate volumes,
  bounded logs, model calls off by default. Docker build context excludes credentials and local databases.
  Compose с приложением и Caddy, домен из `.env`, автоматический HTTPS, постоянные тома базы и сертификатов,
  ограниченные журналы; модель выключена по умолчанию. Секреты и локальные базы исключены из сборки.
- Public `/healthz`: actual SQLite ledger read, effective model configuration, nonnegative budget remaining;
  HTTP 503 for unreadable/missing/corrupt DB or invalid budget. It never creates a DB or calls a model.
  Общедоступный `/healthz`: чтение журнала SQLite, фактическая настройка модели, остаток бюджета не ниже нуля;
  HTTP 503 при недоступной/отсутствующей/повреждённой базе или неверном бюджете. Базу не создаёт, модель не вызывает.
- `/api/me` includes boolean `demo`, true only for `FLOORHAND_DEMO=1`, behind the existing session dependency.
  `/api/me` содержит булево `demo`, истинное только при `FLOORHAND_DEMO=1`; проверка сеанса сохранена.
- Online SQLite backups include committed WAL records; default retention is seven snapshots.
  Nightly reset wrapper stops the app, backs up, rebuilds, preserves **all model-call charges**, restarts;
  failed seeding leaves the previous database intact. Backup and reset share a host lock.
  Онлайн-копии SQLite включают сохранённые записи WAL; по умолчанию хранятся семь снимков.
  Ночной сброс останавливает приложение, сохраняет копию, обновляет демобазу, сохраняет **весь журнал расходов модели**
  и запускает приложение; при ошибке старая база остаётся. Копирование и сброс используют общую блокировку.
- English/Russian Ubuntu setup, operations and cron examples in `deploy/README.md`;
  GitHub Actions tests pushes and pull requests on Python 3.12.
  Инструкции Ubuntu, обслуживание и cron на английском и русском в `deploy/README.md`;
  GitHub Actions проверяет push и pull request на Python 3.12.

## Run / Запуск

Follow `deploy/README.md`: prepare an Ubuntu VPS outside Russia and DNS, install Docker,
create a private `.env` with `DOMAIN`, then `docker compose up -d --build --wait`.
Keep `FLOORHAND_DEMO=1`, `FLOORHAND_META_LIVE=0`; `MODEL_API_KEY` is optional.
Use `sh deploy/backup.sh` and `sh deploy/reset-demo.sh`. Add the documented cron entries for nightly refresh.
Следуйте `deploy/README.md`: подготовьте VPS Ubuntu вне России и DNS, установите Docker,
создайте закрытый `.env` с `DOMAIN`, выполните `docker compose up -d --build --wait`.
Оставьте `FLOORHAND_DEMO=1`, `FLOORHAND_META_LIVE=0`; ключ `MODEL_API_KEY` необязателен.
Используйте `sh deploy/backup.sh`, `sh deploy/reset-demo.sh` и примеры cron для ночного сброса.

## Validation and limits / Проверка и ограничения

FACT: full suite passes: **44 passed**, with one existing FastAPI/Starlette TestClient deprecation warning.
Coverage includes health failures, model configuration, budget arithmetic, session-protected demo flag,
seed idempotency, WAL backup, rotation, model-ledger preservation and failed-reset recovery.
ФАКТ: полный набор тестов пройден: **44 passed**, одно предупреждение FastAPI/Starlette о TestClient.
Проверены ошибки готовности, настройки модели, расчёт бюджета, демофлаг с проверкой сеанса,
повторный запуск, WAL-копии, ротация, сохранение расходов и восстановление после ошибки сброса.

The supplied virtualenv launcher is broken: its configured Python 3.13 installation is missing.
No packages were installed and nothing outside this worktree was edited. Tests used an existing
compatible Python 3.13 interpreter plus the supplied virtualenv's existing dependencies:
Указанный launcher virtualenv не работает: отсутствует настроенная установка Python 3.13.
Пакеты не устанавливались, файлы вне рабочего дерева не менялись. Тесты использовали существующий
совместимый Python 3.13 и готовые зависимости из указанного virtualenv:

```powershell
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -c "import sys; sys.path.insert(0, r'E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'); import pytest; raise SystemExit(pytest.main(['-q', '--basetemp=.test-tmp']))"
```

An existing Python 3.12 interpreter also parsed the changed Python files and deployment YAML successfully.
Shell syntax checks passed using Git Bash. Existing CRLF line endings in `main.py` were preserved to keep
its diff minimal; new Linux scripts use LF and `.gitattributes` preserves them on checkout.
Существующий Python 3.12 успешно разобрал изменённые Python-файлы и YAML развёртывания.
Shell-скрипты прошли проверку синтаксиса Git Bash. Исходный CRLF в `main.py` сохранён для минимального diff;
новые Linux-скрипты имеют LF, который закреплён в `.gitattributes`.

Docker is unavailable on this workstation, so image build, Compose startup, Linux non-root volume behavior,
and real DNS/HTTPS issuance remain to be smoke-tested on the target VPS. No VPS deployment was performed.
Docker отсутствует на этой машине: сборку образа, запуск Compose, права томов Linux и выпуск HTTPS
нужно проверить на целевом VPS. На VPS ничего не развёртывалось.

The frontend demo banner belongs to the parallel frontend task. This change supplies only its API flag.
This is a single-instance demo deployment; it adds no production authentication or off-host backup service.
Do not delete the data volume or restore an old spend ledger while enabling paid calls.
Демобаннер относится к параллельной задаче интерфейса; здесь добавлен только API-флаг.
Развёртывание рассчитано на один экземпляр демоверсии; промышленная аутентификация и внешнее хранилище копий не добавлены.
Не удаляйте том данных и не восстанавливайте старый журнал расходов при включённой платной модели.
