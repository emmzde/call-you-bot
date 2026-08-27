# call-you-bot — «Тебя зовут!»

[![CI](https://github.com/emmzde/call-you-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/emmzde/call-you-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Русский · [English](#english)

## Русский

Это финальная версия бота, которую я подготовил для фандаб-команды. Я делаю и
поддерживаю проект один, поэтому постарался оставить внутри только понятные
решения, предсказуемое поведение и инструменты, с которыми не придётся каждый
день разбираться на сервере.

Бот следит за упоминаниями в подключённых группах. Когда пользователя зовут, он
получает одно компактное сообщение в личку: короткую цитату и кнопку с названием
чата. Кнопка открывает именно то сообщение, из которого его позвали.

### Что умеет бот

- Подставляет в кнопку название исходного чата вместо текста «Перейти к
  сообщению».
- Аккуратно сокращает длинные названия до 48 видимых символов и добавляет
  многоточие.
- Правильно строит ссылки для публичных и приватных суперчатов, тем форума и
  отдельных сообщений в медиаальбомах.
- Не отправляет повторный вызов для одной и той же пары «чат + сообщение +
  пользователь».
- Сначала сохраняет уведомления в SQLite, а уже потом отправляет их. Очередь не
  теряется после перезапуска.
- Повторяет временно неудачные отправки с задержкой, учитывает ответы Telegram
  `429` и ограничивает общий темп рассылки.
- Сам переподключается к Telegram после сетевых сбоев.
- Работает в закрытом Docker-контейнере без root и без открытых портов.

### Что нужно настроить в Telegram

- Каждый пользователь должен один раз открыть бота и нажать **Start**. Telegram
  не разрешает ботам первыми начинать личный диалог.
- В `@BotFather` отключите Group Privacy:
  `/setprivacy` → выберите бота → `Disable`.
- Добавьте бота в нужные группы. Права администратора ему не требуются.
- Для старых basic groups точная ссылка зависит от Telegram-клиента. Если
  критически важен переход к конкретному сообщению, преобразуйте группу в
  суперчат.

### Установка с GitHub

На чистом Ubuntu/Debian/Fedora/RHEL-сервере вставьте одну команду:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/emmzde/call-you-bot/main/deploy/bootstrap.sh | sudo bash
```

Она сама установит Git и Docker, скачает проект в `/opt/call-you-bot`, попросит
токен скрытым вводом, запустит бота, настроит self-heal, backups и обновления.
Повторный запуск этой же команды безопасно обновляет существующую установку.

Чистый сервер физически не установится за одну секунду: скачивание Docker и
образа зависит от сети. Но действий владельца ровно одно. Для каждого проверенного
revision CI публикует готовый image; установщик сверяет его label с Git commit и
обычно пропускает локальную сборку. Если registry недоступен, автоматически
используется более медленная локальная сборка.

Чтобы новый сервер мог скачать GHCR-образ без GitHub-логина, владелец
репозитория после первой успешной публикации один раз выставляет package
`call-you-bot` в режим **Public**. Это влияет только на скорость: пока package
закрыт, тот же установщик сам выполнит локальную сборку.

Классическая установка остаётся доступна:

```bash
git clone https://github.com/emmzde/call-you-bot.git
cd call-you-bot
sudo bash deploy/install.sh
```

Если на совсем чистом Ubuntu/Debian ещё нет Git и вы выбрали ручной вариант:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/emmzde/call-you-bot.git
cd call-you-bot
sudo bash deploy/install.sh
```

Для Fedora/RHEL-подобных систем вместо первых двух команд:

```bash
sudo dnf install -y git
```

Поддерживаются Ubuntu, Debian, Fedora, RHEL, Rocky Linux, AlmaLinux и CentOS с
`systemd`. Короткая инструкция без лишних технических деталей находится в
[INSTALL_RU.md](INSTALL_RU.md), а результаты production-аудита — в
[AUDIT_RU.md](AUDIT_RU.md).

История изменений публикуется в [CHANGELOG.md](CHANGELOG.md).

Установщик:

- поставит Docker Engine, Buildx, Docker Compose и недостающие системные
  инструменты;
- включит автоматический запуск Docker после перезагрузки;
- скрыто запросит токен от `@BotFather`;
- скачает проверенный готовый Docker-образ для точного Git revision либо
  безопасно соберёт его локально;
- запустит контейнер и дождётся состояния `healthy`;
- включит минутное самовосстановление отсутствующего, остановленного или
  `unhealthy` контейнера;
- создаст первый проверенный backup вне Docker volume и включит ежедневные
  backups;
- включит ежедневную проверку обновлений.

Во время обновления текущий бот продолжает работать, пока собирается новый
образ. Перед переключением создаётся резервная копия SQLite. Если новая версия
не проходит локальный healthcheck или не подтверждает связь с Telegram, скрипт
возвращает предыдущий рабочий образ. При уже недоступном Telegram автоматическое
обновление откладывается: такой релиз невозможно честно проверить.

### Три команды для владельца сервера

```bash
# Проверить состояние и последние ошибки
sudo bash deploy/status.sh

# Забрать новую версию из GitHub вручную
sudo bash deploy/update.sh

# Заменить токен
sudo bash deploy/install.sh --reconfigure-token
```

Автообновление выполняется раз в сутки через systemd timer. Если кто-то вручную
изменил файлы проекта на сервере, обновление не перезаписывает их и
останавливается с понятным сообщением.

### Как бот переживает сбои

- Docker поднимает контейнер после перезагрузки сервера или аварийного выхода
  процесса. Отдельный systemd reconciler раз в минуту воссоздаёт удалённый
  контейнер и контролируемо перезапускает `unhealthy`.
- Локальный healthcheck проверяет event loop и SQLite. Если процесс зависнет,
  watchdog завершит его для чистого перезапуска.
- Доступность Telegram API контролируется отдельно. При внешнем сбое бот не
  попадает в бессмысленный restart-loop, а остаётся запущенным, продолжает
  переподключение и отправляет сигнал во внешний мониторинг.
- Временные ошибки Telegram и сети повторяются с exponential backoff и jitter.
- Незавершённая очередь продолжает отправку после рестарта.
- Завершённые записи удаляются через 7 дней, WAL регулярно checkpoint-ится.
- JSON-логи ротируются, поэтому не могут бесконтрольно заполнить диск.
- Контейнер ограничен `0.5 CPU`, `256 MB RAM` и 64 процессами. В обычной работе
  он использует меньше.
- Контейнер запускается без root, без Linux capabilities, с read-only root
  filesystem и `no-new-privileges`.

Если выключилась вся машина или пропал её диск, бот физически не сможет сам
сообщить об этом. Укажите независимые heartbeat URL в root-only файле
`/etc/call-you-bot/ops.env`. Отсутствие минутного heartbeat должно поднимать
тревогу во внешнем сервисе; отдельный heartbeat контролирует ежедневный backup.

### Диагностика, backup и восстановление

```bash
# Состояние, healthcheck и последние логи
sudo bash deploy/status.sh

# Создать проверенный gzip backup вне Docker volume
sudo bash deploy/backup.sh

# Восстановить backup; перед заменой автоматически сохраняется текущая база
sudo bash deploy/restore.sh /var/backups/call-you-bot/BACKUP.sqlite3.gz

# Непрерывный поток JSON-логов
docker compose logs -f --tail=100 bot

# Проверка целостности базы без остановки бота
docker compose exec -T bot python -m tebya_zovut_bot.db_admin check

```

SQLite хранится в Docker volume `bot-data` и переживает пересборку контейнера и
перезагрузку сервера. Ежедневный timer экспортирует согласованный и проверенный
backup в `/var/backups/call-you-bot`. Это защищает от удаления volume, но не от
потери всего VPS. Для off-site хранения настройте в `/etc/call-you-bot/ops.env`
смонтированный `BACKUP_MIRROR_DIRECTORY` или зашифрованный `RESTIC_REPOSITORY`.
Restic поддерживает S3, Backblaze B2, SFTP и другие удалённые хранилища.

Production-токен разрешено запускать только на одном сервере. Для разработки и
ноутбуков используйте отдельного тестового бота: параллельные polling-процессы с
одним токеном конкурируют за Telegram updates и не разделяют локальную SQLite.

Если указать Telegram ID владельца в `ADMIN_USER_IDS`, команда `/status` в
личном диалоге покажет состояние очереди и статистику доставок.

### Ручной Docker-запуск

Для ручной установки нужен Linux-сервер с Docker Engine и Docker Compose:

```bash
cp .env.example .env
chmod 600 .env
# Укажите BOT_TOKEN в .env
docker compose up -d --build
docker compose ps
```

Для хранения токена через Docker secret:

```bash
mkdir -p secrets
chmod 700 secrets
printf '%s' 'TOKEN_FROM_BOTFATHER' > secrets/bot_token
chmod 600 secrets/bot_token
docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d --build
```

Каталог `secrets/` исключён из Git и Docker build context. Токен монтируется в
контейнер только для чтения и не попадает в его переменные окружения.

### Основные настройки

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `BOT_TOKEN` | — | Токен `@BotFather` |
| `BOT_TOKEN_FILE` | — | Путь к файлу с токеном |
| `DATABASE_PATH` | `data/bot.sqlite3` | Путь к SQLite |
| `LOG_LEVEL` | `INFO` | Уровень логов |
| `LOG_FORMAT` | `text` | Формат `text` или `json` |
| `DROP_PENDING_UPDATES` | `false` | Удалять накопленные updates; не включайте в production |
| `SEND_RATE_PER_SECOND` | `25` | Общий лимит отправки, максимум 29 |
| `NOTIFICATION_MAX_ATTEMPTS` | `20` | Максимум попыток доставки |
| `NOTIFICATION_RETENTION_DAYS` | `7` | Срок хранения завершённых записей |
| `TELEGRAM_HEALTHCHECK_INTERVAL_SECONDS` | `60` | Период проверки Telegram API |
| `TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS` | `300` | Когда считать связь с Telegram потерянной |
| `ADMIN_USER_IDS` | пусто | Telegram ID владельцев для `/status` |

Полный список с безопасными значениями находится в `.env.example`.

### Локальная разработка

Нужен Python 3.11 или новее:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check src tests
bandit -q -r src
pytest
pip-audit
```

Полный автономный стресс-профиль не обращается к Telegram и не трогает
production-базу:

```bash
python -m tebya_zovut_bot.stress
```

По умолчанию он проверяет 20 000 пользователей и 100 000 durable-уведомлений:
дедупликацию, crash/reopen, извлечение очереди, согласованный backup и restore.

На Windows окружение активируется командой
`.\.venv\Scripts\Activate.ps1`.

---

## English

This is the final version of the bot I built for the fandub team. I develop and
maintain the project on my own, so the repository is deliberately straightforward:
predictable behavior, a small runtime footprint, and server tools that do not
need constant attention.

The bot watches connected groups for mentions. When someone is called, they
receive one compact private message with a short quote and a button named after
the source chat. The button opens the exact message where the mention happened.

### Features

- Uses the source chat title as the button label instead of a generic “Go to
  message” caption.
- Truncates long chat titles cleanly to 48 visible characters with an ellipsis.
- Builds correct links for public and private supergroups, forum topics, and
  individual messages inside media groups.
- Prevents duplicate notifications for the same chat, message, and user.
- Stores work in a durable SQLite outbox before sending. Pending notifications
  survive restarts.
- Retries temporary failures with backoff, respects Telegram `429` responses,
  and applies a global send rate limit.
- Reconnects polling automatically after network or Telegram outages.
- Runs in a hardened, non-root Docker container with no exposed ports.

### Telegram setup

- Every user must open the bot and press **Start** once. Telegram does not allow
  bots to initiate private conversations.
- In `@BotFather`, disable Group Privacy:
  `/setprivacy` → select the bot → `Disable`.
- Add the bot to the required groups. Administrator rights are not required.
- Exact cross-client links are guaranteed for supergroups. Legacy basic groups
  use a best-effort `tg://openmessage` link; convert them to supergroups when an
  exact jump is essential.

### Install from GitHub

On a clean Ubuntu, Debian, Fedora, or RHEL-family server, paste one command:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/emmzde/call-you-bot/main/deploy/bootstrap.sh | sudo bash
```

It installs Git and Docker, checks out the project into `/opt/call-you-bot`,
prompts for the token without echoing it, and configures the bot, self-healing,
backups, and updates. Re-running the same command safely updates an existing
installation. Network downloads cannot literally complete in one second, but
the owner performs one action. A verified prebuilt image is used when available;
its revision label must match the checked-out Git commit, otherwise the installer
falls back to a local build.

After the first successful publish, the repository owner should make the
`call-you-bot` GHCR package **Public** so a new server can pull it anonymously.
This only affects installation speed: while the package is private, the same
installer safely builds the image locally.

The classic manual installation remains available:

```bash
git clone https://github.com/emmzde/call-you-bot.git
cd call-you-bot
sudo bash deploy/install.sh
```

If Git is missing on a clean Ubuntu/Debian server:

```bash
sudo apt-get update
sudo apt-get install -y git
```

On Fedora/RHEL-family systems:

```bash
sudo dnf install -y git
```

The automatic installer supports Ubuntu, Debian, Fedora, RHEL, Rocky Linux,
AlmaLinux, and CentOS systems that use `systemd`. It installs Docker Engine,
Buildx, Compose, and missing system tools; asks for the BotFather token without
echoing it; builds the image; waits for a healthy container; and enables a daily
update timer.

The current bot stays online while an update is downloaded and built. A
database backup is made before the switch. If the new image does not become
healthy or cannot confirm Telegram connectivity, the updater rolls back to the
previous working image. Automatic updates are deferred while Telegram is
already unavailable because a release cannot be validated safely then.

### Server commands

```bash
# Health, container state, and recent errors
sudo bash deploy/status.sh

# Pull and deploy the latest GitHub version now
sudo bash deploy/update.sh

# Replace the bot token
sudo bash deploy/install.sh --reconfigure-token
```

The daily updater refuses to overwrite local source changes. It stops and
explains the conflict instead.

### Failure handling and resource use

- Docker restarts the container after a server reboot or process crash. A
  systemd reconciler runs every minute to recreate a missing container and
  rate-limit restarts of an unhealthy one.
- The local healthcheck covers the event loop and SQLite. A watchdog terminates
  a stuck process so Docker can restart it cleanly.
- Telegram API availability is tracked separately. An upstream outage does not
  create a restart loop: the bot stays alive, keeps reconnecting, and reports
  the dependency failure to external monitoring.
- Telegram and network failures use bounded retries with exponential backoff
  and jitter.
- Pending outbox work resumes after a restart.
- Completed records expire after seven days and SQLite WAL files are
  checkpointed regularly.
- JSON logs are rotated to prevent unbounded disk use.
- Default limits are `0.5 CPU`, `256 MB RAM`, and 64 processes. Normal usage is
  lower.
- The container runs without root or Linux capabilities, with a read-only root
  filesystem and `no-new-privileges`.

A bot running on a powered-off server cannot report that its own host is down.
Configure independent heartbeat URLs in `/etc/call-you-bot/ops.env`; one tracks
the minute reconciler and another tracks the daily backup.

### Diagnostics, backups, and restore

```bash
# State, healthcheck, and recent logs
sudo bash deploy/status.sh

# Create a verified gzip backup outside the Docker volume
sudo bash deploy/backup.sh

# Restore a backup with automatic pre-restore backup and rollback
sudo bash deploy/restore.sh /var/backups/call-you-bot/BACKUP.sqlite3.gz

# Follow structured logs
docker compose logs -f --tail=100 bot

# Check the live database
docker compose exec -T bot python -m tebya_zovut_bot.db_admin check

```

SQLite lives in the `bot-data` Docker volume, so it survives container rebuilds
and server reboots. A daily timer exports verified backups to
`/var/backups/call-you-bot`. Configure `BACKUP_MIRROR_DIRECTORY` or an encrypted
`RESTIC_REPOSITORY` for off-host disaster recovery.

Run the production token on exactly one server. Laptops and development must use
a separate test bot token; polling processes do not share their SQLite registry.

Set owner Telegram IDs in `ADMIN_USER_IDS` to enable the private `/status`
command with queue and delivery statistics.

### Manual Docker setup

```bash
cp .env.example .env
chmod 600 .env
# Set BOT_TOKEN in .env
docker compose up -d --build
docker compose ps
```

To keep the token in a Docker secret instead:

```bash
mkdir -p secrets
chmod 700 secrets
printf '%s' 'TOKEN_FROM_BOTFATHER' > secrets/bot_token
chmod 600 secrets/bot_token
docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d --build
```

The `secrets/` directory is excluded from Git and the Docker build context. The
token is mounted read-only and is not stored in the container environment.

### Main settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_TOKEN` | — | Token issued by `@BotFather` |
| `BOT_TOKEN_FILE` | — | Path to a token file |
| `DATABASE_PATH` | `data/bot.sqlite3` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `text` | `text` or `json` output |
| `DROP_PENDING_UPDATES` | `false` | Discard queued updates; keep disabled in production |
| `SEND_RATE_PER_SECOND` | `25` | Global send limit, maximum 29 |
| `NOTIFICATION_MAX_ATTEMPTS` | `20` | Maximum delivery attempts |
| `NOTIFICATION_RETENTION_DAYS` | `7` | Completed record retention |
| `TELEGRAM_HEALTHCHECK_INTERVAL_SECONDS` | `60` | Telegram API probe interval |
| `TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS` | `300` | Telegram connectivity loss threshold |
| `ADMIN_USER_IDS` | empty | Telegram owner IDs allowed to use `/status` |

See `.env.example` for the full configuration and safe defaults.

### Local development

Python 3.11 or newer is required:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check src tests
bandit -q -r src
pytest
pip-audit
```

Run the isolated durable-outbox stress profile with:

```bash
python -m tebya_zovut_bot.stress
```

On Windows, activate the environment with
`.\.venv\Scripts\Activate.ps1`.

## License

MIT
