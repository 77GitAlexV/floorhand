# Floorhand live demo / Публичная демоверсия

ESTIMATE: about 10 minutes after the VPS and DNS are ready, depending on image downloads.
ОЦЕНКА: около 10 минут после подготовки VPS и DNS, в зависимости от загрузки образов.

This is a public, disposable demo: all workshop people, customers, work and prices are invented.
Visitors can choose a demo role and change the shop. Use only invented inputs, including model inputs.
Это общедоступная демоверсия: сотрудники, клиенты, работа и цены вымышлены.
Посетители могут выбирать демороли и менять данные. Вводите только вымышленные данные, в том числе для модели.

## Prepare Ubuntu / Подготовка Ubuntu

Use a fresh Ubuntu 24.04 LTS VPS **outside Russia**, a public IP and a hostname you control.
ESTIMATE: 1 vCPU, 1 GB RAM and 10 GB disk are enough for a small judging demo.
Используйте новый VPS с Ubuntu 24.04 LTS **за пределами России**, публичным IP и вашим доменом.
ОЦЕНКА: для небольшой демоверсии достаточно 1 vCPU, 1 ГБ ОЗУ и 10 ГБ диска.

Point the domain's DNS A record at the VPS. Add AAAA only if IPv6 reaches this server.
Allow inbound TCP 80/443 (and optionally UDP 443) in the provider firewall; retain SSH access.
No other service may occupy these web ports. Caddy obtains and renews HTTPS certificates automatically.
Направьте DNS-запись A домена на VPS. Добавляйте AAAA только при рабочем IPv6.
Откройте входящие TCP 80/443 (необязательно UDP 443), сохранив доступ по SSH.
Эти веб-порты должны быть свободны. Caddy автоматически получает и продлевает сертификаты HTTPS.

SSH into the VPS, become root, and install Docker from Ubuntu's packages:
Подключитесь по SSH, станьте root и установите Docker из пакетов Ubuntu:

```bash
sudo -i
apt-get update
apt-get install -y docker.io docker-compose-v2 git curl util-linux
systemctl enable --now docker
docker compose version
```

Clone your repository, replacing `YOUR_REPOSITORY_URL`, or upload its contents to `/opt/floorhand`.
Склонируйте ваш репозиторий, заменив `YOUR_REPOSITORY_URL`, или загрузите файлы в `/opt/floorhand`.

```bash
git clone YOUR_REPOSITORY_URL /opt/floorhand
cd /opt/floorhand
```

## Configure and run / Настройка и запуск

Replace the reserved example domain with your DNS hostname (no scheme, path or port).
Замените пример домена на ваш DNS-домен (без протокола, пути и порта).

```bash
umask 077
cat > .env <<'ENV'
DOMAIN=demo.example.com
FLOORHAND_DEMO=1
FLOORHAND_META_LIVE=0
MODEL_API_KEY=
FLOORHAND_BUDGET_USD=120
ENV
chmod 600 .env
docker compose config --quiet
docker compose up -d --build --wait
curl --fail "https://$(sed -n 's/^DOMAIN=//p' .env)/healthz"
```

Open your HTTPS domain and choose a demo person. No local Python, Node or manual seed step is needed.
The first launch builds the fictional shop; later launches keep it. `/api/me` includes `demo: true`
after sign-in when `FLOORHAND_DEMO=1`, for the frontend's English/Russian demo banner.
Откройте HTTPS-домен и выберите демопользователя. Локальные Python, Node и ручное заполнение не нужны.
Первый запуск создаёт вымышленный цех, следующие сохраняют его. При `FLOORHAND_DEMO=1`
ответ `/api/me` после входа содержит `demo: true` для демобаннера интерфейса.

`/healthz` is public and checks the persisted model ledger without making a model request.
It returns HTTP 200 with `ok`, `db`, `meta_live` and `budget_left_usd`, or HTTP 503 on failure.
FACT: `budget_left_usd` is the configured cap minus the recorded ledger, clamped at zero;
the ledger may include estimated charges. `meta_live` is true only when both the live flag and key are set;
it does not test network access or guarantee remaining budget.
`/healthz` общедоступен и проверяет журнал модели без обращения к модели.
При успехе возвращает HTTP 200 с `ok`, `db`, `meta_live`, `budget_left_usd`, при ошибке — HTTP 503.
ФАКТ: остаток бюджета равен лимиту минус журнал расходов, не ниже нуля;
журнал может содержать оценочные списания. `meta_live` истинен только при флаге и ключе;
он не проверяет сеть и не гарантирует остаток бюджета.

Model use is **off by default**, and offline paths work without a key. To enable paid Meta calls,
edit `.env`: set `MODEL_API_KEY` and `FLOORHAND_META_LIVE=1`, choose your budget cap, then run
`docker compose up -d --wait`. Keep `.env` private. All model calls still go through `floorhand/models.py`.
Модель **выключена по умолчанию**, автономный режим работает без ключа. Для платных запросов
задайте в `.env` ключ `MODEL_API_KEY`, `FLOORHAND_META_LIVE=1` и лимит бюджета,
затем выполните `docker compose up -d --wait`. Храните `.env` в секрете.
Все обращения к модели по-прежнему проходят через `floorhand/models.py`.

## Back up and refresh / Копии и обновление демобазы

Run scripts with `sh`; executable file bits are not required. Backups use SQLite's online backup API,
including committed WAL records, and keep the latest seven snapshots in `/data/backups`.
Set `BACKUP_KEEP` to a positive count to change retention for `backup.sh`.
Запускайте скрипты через `sh`, исполняемый бит не нужен. Копии создаются онлайн через SQLite,
включают сохранённые записи WAL; последние семь хранятся в `/data/backups`.
Для другого количества задайте положительное значение `BACKUP_KEEP` при запуске `backup.sh`.

```bash
sh deploy/backup.sh
BACKUP_KEEP=14 sh deploy/backup.sh
sh deploy/reset-demo.sh
docker compose exec -T app ls -lh /data/backups
```

Reset requires `FLOORHAND_DEMO=1`. It briefly stops only the app, saves a backup, builds a fresh shop
using today's shop date (Asia/Bangkok), preserves every `model_calls` row, and restarts the app.
Visitor changes and sessions are cleared; visitors sign in again. A failed seed keeps the old database.
Never run the reset Python command directly against a running app; the shell wrapper stops it safely.
Сброс требует `FLOORHAND_DEMO=1`. Скрипт ненадолго останавливает приложение, создаёт копию,
строит новый цех на текущую дату цеха (Asia/Bangkok), сохраняет весь `model_calls` и запускает приложение.
Изменения посетителей и сеансы сбрасываются; нужен повторный вход. При ошибке старая база сохраняется.
Не запускайте Python-команду сброса при работающем приложении: используйте shell-скрипт.

Add to root's `crontab -e` for a nightly refresh and daily backup. These times use **the VPS timezone**
(`timedatectl`), independently of the shop timezone. The shared lock prevents overlapping maintenance.
Добавьте в `crontab -e` пользователя root для ночного сброса и ежедневной копии. Время указано
**в часовом поясе VPS** (`timedatectl`), независимо от пояса цеха. Общая блокировка исключает наложение.

```cron
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 2 * * * cd /opt/floorhand && sh deploy/backup.sh >> /var/log/floorhand-maintenance.log 2>&1
15 2 * * * cd /opt/floorhand && sh deploy/reset-demo.sh >> /var/log/floorhand-maintenance.log 2>&1
```

The named `app_data` volume holds `/data/floorhand.db` and backups. Caddy's volumes hold certificates.
`docker compose down` keeps them; **do not use `down -v`**, which also deletes the model-spend ledger.
Backups on the same VPS do not survive disk loss. Copy snapshots off-host, for example:
Именованный том `app_data` содержит `/data/floorhand.db` и копии. Тома Caddy содержат сертификаты.
`docker compose down` сохраняет тома; **не используйте `down -v`**: он удаляет и журнал расходов модели.
Копии на том же VPS не спасут при потере диска. Выгружайте снимки на другой компьютер, например:

```bash
# Replace the timestamp with a filename from ls above. / Подставьте имя из ls выше.
docker compose cp app:/data/backups/floorhand-TIMESTAMP.db ./snapshot.db
```

An older restored snapshot also has an older spend ledger. Keep live model use off until charges since
that snapshot have been reconciled; routine demo refresh already preserves the current ledger.
Старая копия содержит старый журнал расходов. После восстановления не включайте модель до учёта
расходов после снимка; обычный ночной сброс уже сохраняет текущий журнал.

## Operate and troubleshoot / Обслуживание

```bash
docker compose ps
docker compose logs --tail=100 app caddy
# After updating repository files / После обновления файлов:
docker compose up -d --build --wait
```

If HTTPS fails, verify A/AAAA, firewall and Caddy logs. If the app is unhealthy, check its logs,
free disk space and `/data` permissions. The app runs as UID/GID 10001 with no published app port;
use one app instance with its local SQLite volume. Named volumes receive the correct ownership on
creation; a replacement bind mount must be writable by that UID. Keep the deployment directory name
and Compose project name stable so subsequent commands reuse the same volumes.
При ошибке HTTPS проверьте A/AAAA, firewall и журнал Caddy. При ошибке приложения проверьте
его журнал, свободное место и права `/data`. Приложение работает с UID/GID 10001 без публичного
порта; используйте один экземпляр с локальным томом SQLite. Именованные тома получают нужные права
при создании; при замене на bind mount дайте этому UID право записи. Не меняйте каталог развёртывания
и имя проекта Compose, чтобы команды использовали прежние тома.

GitHub Actions runs the test suite on pushes and pull requests using Python 3.12.
Locally, with dependencies already available: `python -m pytest -q`.
GitHub Actions запускает тесты при push и pull request на Python 3.12.
Локально, при установленных зависимостях: `python -m pytest -q`.
