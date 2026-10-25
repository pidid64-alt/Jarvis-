# «Джарвис» — личный ИИ-ассистент

Агентный ассистент, работающий от вашего имени: чтение веб-сайтов, MCP-инструменты,
Telegram от вашего юзер-аккаунта (режим «только читать»), долгосрочная память,
строгая политика подтверждений исходящих действий.

## Что уже умеет MVP

- **Агентное ядро**: цикл «план → действие → наблюдение», нативный function calling,
  лимит шагов, ретраи. Провайдеры модели: OpenAI-совместимый API (OpenRouter, Groq,
  Ollama — по одной строке в `.env`) и Anthropic Claude.
- **MCP-клиент**: серверы stdio и HTTP/SSE из `config/mcp_servers.yaml`, allowlist
  инструментов, классы действий, подключение без перезапуска кода.
- **Веб-чтение**: `read_url` — httpx + trafilatura → markdown; robots.txt, rate limit
  на домен, кеш страниц в SQLite. Динамические страницы — `render_js=true` открывает
  их в headless-Chromium (Playwright) с прокруткой и ожиданием загрузки.
  Плагины сайтов (fetch → parse → normalize).
- **Веб-поиск**: `web_search` — DuckDuckGo без API-ключей; нашёл ссылку — читай
  `read_url`.
- **Telegram (read-only)**: сводка непрочитанного, последние сообщения, поиск по
  истории — через Telethon (MTProto, ваш юзер-аккаунт).
- **Память**: SQLite + FTS5 + локальные эмбеддинги (fastembed, ONNX на CPU);
  гибридный поиск; просмотр/правка/удаление любой записи через `jarvis memory`.
- **Документы** (этап 2.5): чтение PDF, Word (.docx) и Excel (.xlsx) с таблицами —
  из папки `data/docs` или по полному пути (`read_document`, `list_documents`).
- **Приложения Windows**: открыть/закрыть программу или файл/ссылку
  (`open_app`, `close_app` — закрытие только после подтверждения, `list_apps`).
- **Gmail (IMAP, только чтение)**: непрочитанные, поиск, открыть письмо —
  по паролю приложения Google (`.env`: `JARVIS_GMAIL_USER`, `JARVIS_GMAIL_APP_PASSWORD`).
- **Безопасность**: исходящие действия — только после подтверждения; внешний контент
  всегда в рамке `<untrusted>` (защита от prompt injection); аудит всех действий
  (`jarvis audit`); секреты только в `.env`.

## Установка (Windows)

Нужен только [uv](https://docs.astral.sh/uv/) — Python 3.12 он поставит сам.

```bash
# 1) окружение
uv sync

# 2) секреты
cp .env.example .env   # затем заполните ключи (см. ниже)

# 3) проверка
uv run jarvis --help
uv run pytest -q
```

## Настройка .env

1. **Модель (бесплатно)** — зарегистрируйтесь на [openrouter.ai](https://openrouter.ai),
   создайте ключ на `/keys`, выберите модель с суффиксом `:free`:
   ```
   JARVIS_LLM_PROVIDER=openai_compat
   JARVIS_LLM_BASE_URL=https://openrouter.ai/api/v1
   JARVIS_LLM_API_KEY=sk-or-...
   JARVIS_LLM_MODEL=qwen/qwen3-14b:free
   ```
   Альтернативы прописаны в `.env.example` (Groq, локальный Ollama, Claude).
2. **Telegram** — получите `api_id`/`api_hash` на [my.telegram.org](https://my.telegram.org)
   → API development tools:
   ```
   JARVIS_TELEGRAM_API_ID=123456
   JARVIS_TELEGRAM_API_HASH=0123456789abcdef...
   JARVIS_TELEGRAM_PHONE=+7...
   ```

## Запуск и проверка

```bash
uv run jarvis chat              # интерактивный чат с агентом (все инструменты)
uv run jarvis once "что нового?"# одиночный вопрос
uv run jarvis telegram bot      # Telegram-бот управления (чат с ассистентом в TG)
uv run jarvis tools             # реестр инструментов (встроенные + MCP)
uv run jarvis fetch URL         # прочитать страницу без модели
uv run jarvis fetch URL --js    # то же, но через headless-браузер (динамические страницы)
uv run jarvis mcp list          # статусы MCP-серверов
uv run jarvis telegram login    # одноразовый вход в Telegram (код из приложения)
uv run jarvis telegram unread   # сводка непрочитанного
uv run jarvis memory list       # факты долгосрочной памяти
uv run jarvis audit             # журнал действий агента
```

Первый запуск динамических страниц требует браузера:

```bash
uv run playwright install chromium
```

### Telegram-бот управления

Бот = полноценный интерфейс Джарвиса в Telegram: пишешь сообщение — ассистент
отвечает (те же инструменты, что и в CLI), а опасные действия подтверждает
кнопками ✅/❌ прямо в чате.

1. Создай бота: в Telegram @BotFather → `/newbot` → имя и @username → получишь токен.
2. Впиши его в `.env`: `JARVIS_TELEGRAM_BOT_TOKEN=123456:ABC…`
3. Запусти: `uv run jarvis telegram bot`
4. Открой своего бота в Telegram → `/start` → пиши.

Доступ только у владельца: числовой `JARVIS_TELEGRAM_OWNER_ID` в `.env`
(свой id узнай у @userinfobot), либо владельцем становится первый, кто нажал
`/start` после запуска (id запоминается в `data/bot_owner.json`).

Проверка агента с инструментами (после заполнения `.env`):

```bash
uv run jarvis once "Прочитай https://example.com и перескажи в двух предложениях"
uv run jarvis once "Запомни: любимая музыка — рок"   # → memory_save
uv run jarvis once "Что ты помнишь о моей музыке?"   # → memory_search / промпт
```

## MCP-серверы

Реестр — `config/mcp_servers.yaml`. Уже подключён демо-сервер `fetch` (официальный
reference-сервер, ставится через `uvx`). Примеры HTTP-сервера с авторизацией
(`headers: {Authorization: "env:ИМЯ"}`) и npm-сервера filesystem — в комментариях
файла. Политика для каждого сервера: `allowed_tools` (в реестр попадут только они),
`read_only`, `tool_side_effects` (точечные классы read/write/outbound).

## Архитектура

```
src/jarvis/
├── core/        ядро: agent.py (цикл), prompts.py (изоляция <untrusted>),
│                llm/ (openai_compat, anthropic), tools/ (registry, policy, builtin/)
├── mcp/         менеджер серверов stdio/HTTP/SSE + мост в реестр
├── web/         fetcher (robots/rate limit/кеш), extract (trafilatura), sites/ (плагины)
├── connectors/  base (интерфейс) + telegram_user (Telethon, read-only)
├── memory/      db (SQLite-схема), store (факты+диалоги), vector (fastembed)
├── interfaces/  cli.py, runtime.py (сборка всего), позже tg_bot и voice
└── scheduler/   этап 2: APScheduler — мониторинг, брифинги, напоминания
```

Данные — в `data/` (SQLite-БД, сессия Telegram, журнал). Бэкап — копия каталога.

## Безопасность

- Классы действий инструментов: `read` / `write` — выполняются свободно;
  `outbound` (отправка, пост, удаление) — подтверждение в CLI; `high_stakes`
  (финансы, документы, обещания от вашего имени) — подтверждение всегда,
  авто-режим для них невозможен технически.
- Авто-разрешения задаются только вручную в `assistant.yaml → approvals.auto`
  (инструмент + chat_id). Текст из переписки или сайтов включить их не может.
- Весь внешний контент (веб, Telegram, MCP) поступает модели только внутри
  `<untrusted source="...">…</untrusted>`; системный промпт запрещает выполнять
  инструкции из таких блоков. Даже при неудачной инъекции исходящее действие
  упирается в гейт подтверждения.
- Сессия Telegram (`data/sessions/`) содержит ключ авторизации — не копируйте и
  не пересылайте её; `.env` и `data/` исключены из git.

## Docker (для сервера)

```bash
cp .env.example .env   # заполнить
docker compose build
docker compose run --rm jarvis chat
```

## Дорожная карта

- **Этап 1** (готово): Telegram-бот управления с кнопками подтверждения,
  Playwright для динамических страниц, веб-поиск DuckDuckGo.
- **Этап 2** (готово): планировщик — напоминания (`напомни через 20 минут…`),
  утренний брифинг (непрочитанное TG, изменения страниц, задачи), мониторинг
  страниц из `config/monitoring.yaml` с уведомлениями в бота; черновики
  ответов в вашем стиле (`jarvis style add …` + инструмент draft_reply);
  отправка сообщений от юзер-аккаунта — защищена подтверждением и режимом
  коннектора (по умолчанию read_only, отправка запрещена).
- **Этап 3**: Discord-коннектор; далее VK.
- **Этап 4**: голос — «Джарвис» wake-word, faster-whisper (STT), Silero (TTS).

Команды этапа 2:

```bash
uv run jarvis remind "позвонить маме" --in 60   # напоминание через час
uv run jarvis remind "зарядка" --daily "09:00"  # каждое утро
uv run jarvis jobs                               # задания планировщика
uv run jarvis briefing                           # брифинг прямо сейчас
uv run jarvis scheduler                          # планировщик без бота
uv run jarvis style add "Привет! Как дела?)"    # пример твоего стиля
```
