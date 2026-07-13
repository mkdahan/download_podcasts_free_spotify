# AGENTS.md

## Cursor Cloud specific instructions

This is a small single-product Python app: **Offline Podcast Downloader** (FastAPI/uvicorn browser GUI in `gui_server.py` + `gui/`, sharing a core module `podcast_cli.py` which is also a standalone CLI). State is flat JSON (`library.json`) + downloaded media on disk; there is **no database or other backing service**.

- **Python deps live in a `.venv/` virtualenv** (system Python is PEP 668 externally-managed, so global `pip install` is blocked). The update script creates/refreshes `.venv`; always run tools through it, e.g. `.venv/bin/python gui_server.py`.
- **Run the GUI (only long-running service):** `.venv/bin/python gui_server.py`. It binds `127.0.0.1:8765` (auto-increments to 8766+ if busy). On this headless VM it tries to auto-open Chrome, which prints harmless `dbus`/`gpu` errors — ignore them; the server itself works. The API works headlessly (`curl http://127.0.0.1:8765/api/defaults`).
- **CLI alternative:** `.venv/bin/python podcast_cli.py <search|list|download|add|library> ...`.
- **Search & downloads require internet egress** to public podcast APIs (iTunes, fyyd, gpodder, optionally Podcast Index) and to RSS/CDN hosts. There are no required credentials; Podcast Index keys (`podcastindex.keys`) are optional and only improve search.
- **No lint, test, or build tooling exists** in this repo (no test suite, linter config, Makefile, or CI). "Build" = installing `requirements.txt`.
- `/api/pick-folder` uses a Tkinter dialog and will not work headlessly; it is not needed — pass an explicit `out_dir` to `/api/download` instead.
