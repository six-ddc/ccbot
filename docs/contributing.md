# Contributing

## Development Setup

```bash
git clone https://github.com/six-ddc/ccmux.git
cd ccmux
uv sync
```

## Common Commands

```bash
make check       # Run all: fmt, lint, typecheck, test
make fmt         # Format code (ruff)
make lint        # Lint (ruff) — must pass before committing
make typecheck   # Type check (pyright) — must be 0 errors
make test        # Run tests (pytest)
```

## Code Conventions

- Python 3.12+, managed with `uv`
- Every `.py` file starts with a module-level docstring
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes
- All Telegram output goes through `safe_reply`/`safe_edit`/`safe_send` (MarkdownV2 with auto fallback to plain text)
- Tests mirror source structure under `tests/ccbot/`
