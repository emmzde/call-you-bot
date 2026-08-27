# Установка бота на сервер

Проект лежит на GitHub:
[emmzde/call-you-bot](https://github.com/emmzde/call-you-bot).
ZIP-файл передавать не нужно.

## Первый запуск одной командой

Подключитесь к серверу по SSH и вставьте:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/emmzde/call-you-bot/main/deploy/bootstrap.sh | sudo bash
```

Скрипт сам скачает проект в `/opt/call-you-bot`, установит нужные пакеты и
попросит токен. Ввод токена будет скрыт. Эту же команду можно безопасно запускать
повторно для обновления установки.

Установка не может физически занять одну секунду из-за скачивания Docker и
образа, но дополнительных команд не требуется. Если готовый image для точного
Git revision опубликован, долгая локальная сборка автоматически пропускается.

## Ручной первый запуск

### 1. Скачайте проект

На Ubuntu или Debian:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/emmzde/call-you-bot.git
cd call-you-bot
```

На Fedora, RHEL, Rocky Linux, AlmaLinux или CentOS:

```bash
sudo dnf install -y git
git clone https://github.com/emmzde/call-you-bot.git
cd call-you-bot
```

Git нужен только для загрузки проекта и будущих обновлений. Docker,
Docker Compose, Python-зависимости и остальные инструменты установщик добавит
сам, если их ещё нет.

### 2. Запустите установку

```bash
sudo bash deploy/install.sh
```

Когда скрипт попросит токен, вставьте токен от `@BotFather` и нажмите Enter.
Символы токена на экране не показываются — это нормально.

Дождитесь сообщения `Установка полностью завершена`. После этого бот работает
сам, запускается после перезагрузки сервера и раз в сутки безопасно проверяет
обновления. Раз в минуту systemd проверяет контейнер и восстанавливает его при
остановке, а раз в сутки создаётся проверенный backup вне Docker volume.

## Проверка

```bash
sudo bash deploy/status.sh
```

Если в конце написано `Бот работает нормально`, ничего делать не нужно.

## Обязательная настройка внешней надёжности

Откройте root-only файл:

```bash
sudoedit /etc/call-you-bot/ops.env
```

Укажите `MONITOR_HEARTBEAT_URL` для минутной проверки и
`BACKUP_HEARTBEAT_URL` для ежедневного backup. Эти URL должен выдать внешний
мониторинг: если весь VPS выключится, именно отсутствие heartbeat создаст
уведомление.

Для защиты от потери диска настройте один из вариантов:

- `BACKUP_MIRROR_DIRECTORY` — отдельный смонтированный диск/NFS;
- `RESTIC_REPOSITORY` и `RESTIC_PASSWORD_FILE` — зашифрованный off-site backup
  в S3, Backblaze B2, SFTP или совместимое хранилище.

Без внешнего heartbeat и off-site копии сервис самовосстанавливается на одном
VPS, но не защищён от исчезновения всего сервера.

## Ручное обновление

```bash
sudo bash deploy/update.sh
```

Обновление сначала создаёт резервную копию. Если новая версия не запустится,
скрипт автоматически вернёт предыдущую рабочую версию.

Если Telegram API уже недоступен, автоматическое обновление откладывается: в
таком состоянии новую версию нельзя надёжно проверить.

Версия, не прошедшая healthcheck, помечается нерабочей и больше не ставится
автоматически до появления нового revision.

## Backup и восстановление

```bash
sudo bash deploy/backup.sh
sudo bash deploy/restore.sh /var/backups/call-you-bot/BACKUP.sqlite3.gz
```

Restore сначала делает backup текущей базы, проверяет обе SQLite и при неудачном
healthcheck автоматически возвращает прежнее состояние.

## Замена токена

```bash
sudo bash deploy/install.sh --reconfigure-token
```

При любой ошибке скопируйте весь текст из терминала и отправьте разработчику.

Production-токен должен работать ровно на одном сервере. На ноутбуках и для
тестов используйте отдельный токен другого Telegram-бота.
