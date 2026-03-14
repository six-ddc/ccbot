# Ralph Loop Prompt: CCBot Continuous Improvement

> Этот файл — документация промпта для Ralph Loop.

## Как запустить

```bash
cd /Users/malovnik/Documents/Dev/ccbot
```

Затем в Claude Code:

```
/ralph-loop Read the file docs/ralph-loop-instructions.md and follow ALL instructions exactly. You are in a 20-cycle continuous improvement loop for CCBot. Each cycle: research a meaningful feature, validate it through Sequential Thinking debate, design with Superpowers brainstorming and planning skills, implement with Serena and Superpowers executing-plans skill, run semgrep security scan and fix ALL findings, update CHANGELOG.md and docs/improvement-log.md, then commit and push with /push skill. Start by reading docs/improvement-log.md to see past work. Be autonomous. Act decisively. No trivial changes. --max-iterations 20
```

## Что происходит

1. Ralph Loop запускается с 20 итерациями
2. Каждая итерация Claude читает instructions.md
3. Claude проходит 8 фаз: ориентация, ресерч, валидация, дизайн, реализация, аудит, коммит, отчет
4. Stop hook ловит завершение и возвращает промпт
5. Следующая итерация начинается с чтения improvement-log.md

## Файлы

- `docs/ralph-loop-instructions.md` — полные инструкции (роли, процесс, критерии)
- `docs/improvement-log.md` — лог всех улучшений
- `.claude/ralph-loop.local.md` — state file Ralph Loop (автоматический)

## Остановка

- Автоматическая: после 20 итераций
- Ручная: `/cancel-ralph`
