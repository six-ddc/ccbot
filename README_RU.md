# CCBot

[English README](README.md)
[中文文档](README_CN.md)

Удалённое управление сессиями Claude Code через Telegram — мониторинг, интерактивное управление и работа с AI-сессиями в tmux.

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## Зачем CCBot?

Claude Code работает в терминале. Когда вы отходите от компьютера — в дороге, дома или просто не за рабочим местом — сессия продолжает выполняться, но вы теряете видимость и контроль.

CCBot позволяет **бесшовно продолжать ту же самую сессию через Telegram**. Ключевая идея: он работает поверх **tmux**, а не через Claude Code SDK. Процесс Claude Code остаётся в tmux-окне на вашей машине, а CCBot только читает вывод и отправляет нажатия клавиш. Это означает:

- **Переключение с десктопа на телефон в середине работы** — Claude делает рефакторинг? Можно отойти и продолжать наблюдать/отвечать из Telegram.
- **Мгновенное возвращение к десктопу** — tmux-сессия не прерывается; `tmux attach` возвращает вас в тот же терминал с полной историей и контекстом.
- **Параллельная работа с несколькими сессиями** — каждый Telegram topic соответствует отдельному tmux-окну.

Большинство других Telegram-ботов для Claude Code используют отдельные API-сессии через SDK. Такие сессии изолированы и не продолжаются в вашем терминале. CCBot работает иначе: это тонкий слой управления над tmux, поэтому терминал остаётся источником истины, и вы всегда можете вернуться к нему.

## Возможности

- **Сессии по темам** — каждый Telegram topic 1:1 связан с tmux-окном и Claude-сессией
- **Уведомления в реальном времени** — ответы ассистента, thinking-контент, tool use/result, вывод локальных команд
- **Интерактивный UI** — управление AskUserQuestion, ExitPlanMode и Permission Prompt через inline-клавиатуру
- **Голосовые сообщения** — голосовые сообщения транскрибируются через OpenAI и пересылаются как текст
- **Отправка сообщений** — проброс текста в Claude Code через tmux
- **Проброс slash-команд** — любая `/command` уходит напрямую в Claude Code (например, `/clear`, `/compact`, `/cost`)
- **Создание новых сессий** — запуск Claude Code из Telegram через браузер директорий
- **Возобновление сессий** — выберите существующую Claude-сессию в директории, чтобы продолжить с того места, где остановились
- **Завершение сессий** — закрытие topic автоматически завершает связанное tmux-окно
- **История сообщений** — пагинация истории диалога (сначала новые)
- **Трекинг сессий через hook** — авто-связывание tmux-окон с Claude-сессиями через `SessionStart`
- **Персистентное состояние** — привязки topic/window и read-offset сохраняются после перезапуска

## Требования

- **tmux** — должен быть установлен и доступен в PATH
- **Claude Code** — CLI-инструмент `claude` должен быть установлен

## Установка

### Вариант 1: установка из GitHub (рекомендуется)

```bash
# Через uv (рекомендуется)
uv tool install git+https://github.com/six-ddc/ccmux.git

# Или через pipx
pipx install git+https://github.com/six-ddc/ccmux.git
```

### Вариант 2: установка из исходников

```bash
git clone https://github.com/six-ddc/ccmux.git
cd ccmux
uv sync
```

## Конфигурация

**1. Создайте Telegram-бота и включите Threaded Mode:**

1. Напишите [@BotFather](https://t.me/BotFather), создайте бота и получите токен
2. Откройте профиль @BotFather и нажмите **Open App**
3. Выберите вашего бота, затем **Settings** > **Bot Settings**
4. Включите **Threaded Mode**

**2. Настройте переменные окружения:**

Создайте `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

**Обязательные:**

| Переменная | Описание |
| ---------- | -------- |
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `ALLOWED_USERS` | Список Telegram user ID через запятую |

**Опциональные:**

| Переменная | По умолчанию | Описание |
| ---------- | ------------ | -------- |
| `CCBOT_DIR` | `~/.ccbot` | Каталог конфигурации/состояния (`.env` грузится отсюда) |
| `TMUX_SESSION_NAME` | `ccbot` | Имя tmux-сессии |
| `CLAUDE_COMMAND` | `claude` | Команда запуска в новых окнах |
| `MONITOR_POLL_INTERVAL` | `2.0` | Интервал опроса в секундах |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | Показывать скрытые (dot) директории в браузере каталогов |
| `OPENAI_API_KEY` | _(нет)_ | API-ключ OpenAI для транскрипции голосовых сообщений |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Базовый URL OpenAI API (для прокси или совместимых API) |

Форматирование сообщений всегда HTML через `chatgpt-md-converter` (`chatgpt_md_converter`).
Переключателя формата на MarkdownV2 во время выполнения нет.

> Если бот запущен на VPS без интерактивного терминала для подтверждений, можно использовать:
>
> ```
> CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
> ```

## Настройка Hook (рекомендуется)

Авто-установка через CLI:

```bash
ccbot hook --install
```

Или вручную добавьте в `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ]
  }
}
```

Это записывает отображение window-session в `$CCBOT_DIR/session_map.json` (по умолчанию `~/.ccbot/`), чтобы бот автоматически отслеживал, какая Claude-сессия работает в каждом tmux-окне — даже после `/clear` или рестарта сессии.

## Использование

```bash
# Если установлено через uv tool / pipx
ccbot

# Если запуск из исходников
uv run ccbot
```

### Команды

**Команды бота:**

| Команда | Описание |
| ------- | -------- |
| `/start` | Показать приветственное сообщение |
| `/history` | История сообщений для текущего topic |
| `/screenshot` | Снимок терминала |
| `/esc` | Отправить Escape для прерывания Claude |
| `/kill` | Завершить сессию и отвязать topic |

**Команды Claude Code (пробрасываются через tmux):**

| Команда | Описание |
| ------- | -------- |
| `/clear` | Очистить историю диалога |
| `/compact` | Уплотнить контекст диалога |
| `/cost` | Показать статистику токенов/стоимости |
| `/help` | Справка Claude Code |
| `/memory` | Редактировать CLAUDE.md |

Любая неизвестная `/command` также пробрасывается в Claude Code как есть (например, `/review`, `/doctor`, `/init`).

### Workflow по topic

**1 topic = 1 window = 1 session.** Бот работает в режиме Telegram Forum Topics.

**Создание новой сессии:**

1. Создайте новый topic в Telegram-группе
2. Отправьте любое сообщение в topic
3. Появится браузер директорий — выберите каталог проекта
4. Если в каталоге есть существующие Claude-сессии, появится выбор сессий — возобновите существующую или начните новую
5. Будет создано tmux-окно, запустится `claude` (с `--resume` при возобновлении), и ваше отложенное сообщение отправится в сессию

**Отправка сообщений:**

После привязки topic к сессии отправляйте текст или голосовые сообщения в topic — текст уходит в Claude Code через tmux, голосовые сообщения автоматически транскрибируются и пересылаются как текст.

**Завершение сессии:**

Используйте `/kill` в topic, чтобы завершить связанное tmux-окно и отвязать topic.  
Также можно закрыть (или удалить) topic в Telegram — это тоже автоматически завершит связанное tmux-окно и удалит привязку.

### История сообщений

Навигация через inline-кнопки:

```
📋 [project-name] Messages (42 total)

───── 14:32 ─────

👤 fix the login bug

───── 14:33 ─────

I'll look into the login bug...

[◀ Older]    [2/9]    [Newer ▶]
```

### Уведомления

Монитор опрашивает session JSONL-файлы каждые 2 секунды и отправляет уведомления о:

- **Ответах ассистента** — текстовые ответы Claude
- **Thinking-контенте** — отображается как раскрываемые цитаты
- **Tool use/result** — краткие сводки (например, `Read 42 lines`, `Found 5 matches`)
- **Выводе локальных команд** — stdout команд вроде `git status`, префикс `❯ command_name`

Уведомления отправляются в topic, привязанный к окну сессии.

Примечание по форматированию:
- Telegram-сообщения рендерятся с parse mode `HTML` через `chatgpt-md-converter`
- Длинные сообщения делятся с учётом HTML-тегов, чтобы сохранять код-блоки и форматирование

## Запуск Claude Code в tmux

### Вариант 1: создать через Telegram (рекомендуется)

1. Создайте новый topic в Telegram-группе
2. Отправьте любое сообщение
3. Выберите каталог проекта в браузере

### Вариант 2: создать вручную

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
# Затем запустите Claude Code в новом окне
claude
```

Окно должно находиться в tmux-сессии `ccbot` (настраивается через `TMUX_SESSION_NAME`). Hook автоматически зарегистрирует его в `session_map.json` при запуске Claude.

## Обзор архитектуры

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Topic ID   │ ───▶ │ Window ID   │ ───▶ │ Session ID  │
│ (Telegram)  │      │ (tmux @id)  │      │  (Claude)   │
└─────────────┘      └─────────────┘      └─────────────┘
   thread_bindings       session_map.json
   (state.json)          (записывается hook)
```

## Хранение данных

| Путь | Описание |
| ---- | -------- |
| `$CCBOT_DIR/state.json` | Привязки topic, состояния окон, display names и read-offset на пользователя |
| `$CCBOT_DIR/session_map.json` | Hook-таблица `{tmux_session:window_id: {session_id, cwd, window_name}}` |
| `$CCBOT_DIR/monitor_state.json` | Byte-offset монитора по сессиям (предотвращает дубли) |
| `~/.claude/projects/` | Данные сессий Claude Code (только чтение) |

## Структура файлов

```
src/ccbot/
├── __init__.py            # Точка входа пакета
├── main.py                # CLI-диспетчер (hook подкоманда + запуск бота)
├── hook.py                # Hook-подкоманда для трекинга сессий (+ --install)
├── config.py              # Конфигурация из переменных окружения
├── bot.py                 # Настройка Telegram-бота, обработчики команд, topic routing
├── session.py             # Управление сессиями, persist состояния, история сообщений
├── session_monitor.py     # Мониторинг JSONL-файлов (polling + обнаружение изменений)
├── monitor_state.py       # Persist состояния монитора (byte-offset)
├── transcript_parser.py   # Парсинг JSONL-транскриптов Claude Code
├── terminal_parser.py     # Парсинг terminal pane (interactive UI + status line)
├── html_converter.py      # Markdown -> Telegram HTML + HTML-aware splitting
├── screenshot.py          # Terminal text -> PNG с поддержкой ANSI-цветов
├── transcribe.py          # Транскрипция голоса в текст через OpenAI API
├── utils.py               # Общие утилиты (atomic JSON writes, JSONL helpers)
├── tmux_manager.py        # Управление tmux-окнами (list, create, send keys, kill)
├── fonts/                 # Встроенные шрифты для рендера скриншотов
└── handlers/
    ├── __init__.py        # Экспорты handler-модулей
    ├── callback_data.py   # Константы callback data (префиксы CB_*)
    ├── directory_browser.py # Inline UI браузера директорий
    ├── history.py         # Пагинация истории сообщений
    ├── interactive_ui.py  # Обработка interactive UI (AskUser, ExitPlan, Permissions)
    ├── message_queue.py   # Очередь сообщений на пользователя + worker (merge, rate limit)
    ├── message_sender.py  # safe_reply / safe_edit / safe_send helpers
    ├── response_builder.py # Сборка ответных сообщений (tool_use, thinking и т.д.)
    └── status_polling.py  # Polling terminal status line
```

## Участники

Спасибо всем, кто вносит вклад! Мы поощряем использование Claude Code для совместной разработки.

<a href="https://github.com/six-ddc/ccmux/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=six-ddc/ccmux" />
</a>
