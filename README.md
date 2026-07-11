# Offline Podcast Downloader (GUI)

A small local app to search free podcast feeds and download episodes for offline listening. It includes a browser GUI (`gui_server.py`) and a CLI (`podcast_cli.py`).

## How to run

1. Install [Python 3](https://www.python.org/downloads/) and enable **Add python.exe to PATH**.
2. Double-click `run_gui.bat`.
3. Open the URL shown in the console (usually `http://127.0.0.1:8765`).

The batch file upgrades pip, installs `requirements.txt`, then starts the GUI server.

## Optional: Podcast Index API keys

For better search via Podcast Index, copy `podcastindex.keys.example` to `podcastindex.keys` and fill in your keys. That file stays local and is gitignored.

## Downloads

Downloaded media is stored under `downloads/`. That folder is local-only (gitignored) and is not pushed to GitHub.
