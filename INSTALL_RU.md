# Установка бота на сервер

Проект лежит на GitHub:
[emmzde/call-you-bot](https://github.com/emmzde/call-you-bot).
ZIP-файл передавать не нужно.

## Первый запуск

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
обновления.

## Проверка

```bash
sudo bash deploy/status.sh
```

Если в конце написано `Бот работает нормально`, ничего делать не нужно.

## Ручное обновление

```bash
sudo bash deploy/update.sh
```

Обновление сначала создаёт резервную копию. Если новая версия не запустится,
скрипт автоматически вернёт предыдущую рабочую версию.

## Замена токена

```bash
sudo bash deploy/install.sh --reconfigure-token
```

При любой ошибке скопируйте весь текст из терминала и отправьте разработчику.
