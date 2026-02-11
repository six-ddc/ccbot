# Conventions

- Window IDs (`@0`, `@12`) are the canonical internal key, never window names
- All Telegram output goes through `safe_reply`/`safe_edit`/`safe_send` (MarkdownV2 + fallback)
- Config via env vars, loaded in `config.py` with `python-dotenv`
- State persisted in `~/.ccbot/` (or `$CCBOT_DIR`): `state.json`, `session_map.json`, `monitor_state.json`
- Handlers live in `src/ccbot/handlers/`, core modules in `src/ccbot/`
- Callback data constants in `handlers/callback_data.py`, prefixed `CB_`
- Tests mirror source structure under `tests/ccbot/`
- Python 3.14+, managed with `uv`
