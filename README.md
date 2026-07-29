# call-you-bot — «Тебя зовут!»

[![CI](https://github.com/emmzde/call-you-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/emmzde/call-you-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## English

I built the first version of this Telegram bot as a trial project for a fandub
team. Their work happens across busy production and release chats, so mentions
are easy to miss. The bot gives every participant a convenient personal inbox
for those mentions without changing the team's usual workflow.

When someone mentions a registered user, the bot sends that person two private
messages:

1. `Тебя зовут! Давай быстрее;)`
2. `Текст сообщения: <message text>` with a button that opens the source
   message.

The bot works in private and public supergroups, forum topics, media captions,
and edited messages. It recognizes both `@username` mentions and Telegram text
mentions.

### Telegram requirements

- Every user must open the bot and press **Start** once. Telegram does not allow
  bots to start private conversations.
- Group Privacy must be disabled in `@BotFather`: `/setprivacy` → select the bot
  → `Disable`.
- The bot does not need administrator rights.
- Exact message links work in supergroups. Basic groups use a best-effort
  `tg://openmessage` link because Telegram assigns different message IDs to
  different accounts. Convert a basic group to a supergroup when exact links are
  required.

### Run with Docker

1. Create a bot with `@BotFather`.
2. Copy `.env.example` to `.env` and add the token:

   ```env
   BOT_TOKEN=your_token_from_BotFather
   ```

3. Build and start the bot:

   ```bash
   docker compose up -d --build
   ```

4. Check the logs:

   ```bash
   docker compose logs -f bot
   ```

The SQLite database is stored in the `bot-data` Docker volume and survives
container rebuilds.

### Local development

Python 3.11 or newer is required.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check src tests
pytest
```

On Windows, activate the environment with
`.\.venv\Scripts\Activate.ps1`. On Linux and macOS, use
`source .venv/bin/activate`.

### Environment variables

| Variable | Required | Default |
| --- | --- | --- |
| `BOT_TOKEN` | yes | — |
| `DATABASE_PATH` | no | `data/bot.sqlite3` |
| `LOG_LEVEL` | no | `INFO` |
| `DROP_PENDING_UPDATES` | no | `false` |

`DROP_PENDING_UPDATES=true` discards Telegram updates accumulated while the bot
was offline.

The test suite includes mention parsing, message links, storage, notification
retries, duplicate protection, and a 300-recipient load scenario.

---

## Русский

Первую версию этого Telegram-бота я сделал для фандаб-команды как пробный
проект. Работа идёт сразу в нескольких загруженных производственных чатах и
чатах релизов, поэтому упоминания легко пропустить. Бот собирает их в личных
сообщениях и остаётся удобным для участников, потому что не меняет привычный
процесс общения.

Когда в сообщении отмечают зарегистрированного пользователя, бот присылает ему
в личные сообщения:

1. `Тебя зовут! Давай быстрее;)`
2. `Текст сообщения: <текст>` и кнопку перехода к исходному сообщению.

Бот работает в публичных и приватных супергруппах, темах, подписях к медиа и
отредактированных сообщениях. Распознаются упоминания через `@username` и
текстовые упоминания Telegram.

### Требования Telegram

- Каждый пользователь должен один раз открыть бота и нажать **Start**. Telegram
  не разрешает ботам первыми начинать личный диалог.
- В `@BotFather` нужно отключить Group Privacy: `/setprivacy` → выбрать бота →
  `Disable`.
- Права администратора боту не нужны.
- Точные ссылки на сообщения работают в супергруппах. В basic group
  используется best-effort ссылка `tg://openmessage`, потому что Telegram
  назначает одному сообщению разные ID для разных аккаунтов. Если важен точный
  переход, группу нужно преобразовать в супергруппу.

### Запуск через Docker

1. Создайте бота через `@BotFather`.
2. Скопируйте `.env.example` в `.env` и укажите токен:

   ```env
   BOT_TOKEN=токен_от_BotFather
   ```

3. Соберите и запустите контейнер:

   ```bash
   docker compose up -d --build
   ```

4. Посмотрите логи:

   ```bash
   docker compose logs -f bot
   ```

SQLite-база хранится в Docker volume `bot-data` и не теряется при пересборке
контейнера.

### Локальная разработка

Нужен Python 3.11 или новее.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check src tests
pytest
```

В Windows окружение активируется командой
`.\.venv\Scripts\Activate.ps1`, в Linux и macOS —
`source .venv/bin/activate`.

### Переменные окружения

| Переменная | Обязательна | По умолчанию |
| --- | --- | --- |
| `BOT_TOKEN` | да | — |
| `DATABASE_PATH` | нет | `data/bot.sqlite3` |
| `LOG_LEVEL` | нет | `INFO` |
| `DROP_PENDING_UPDATES` | нет | `false` |

`DROP_PENDING_UPDATES=true` удаляет обновления Telegram, накопившиеся за время
простоя бота.

Тесты проверяют разбор упоминаний, ссылки на сообщения, работу базы, повторную
отправку при временных ошибках, защиту от дублей и нагрузочный сценарий на 300
получателей.

## License

MIT
