#!/usr/bin/env python3
"""Local HTML GUI for searching podcast RSS feeds and downloading episodes."""

from __future__ import annotations

import os
import subprocess
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import podcast_cli as core

core.configure_stdout()

ROOT = Path(__file__).resolve().parent
GUI_DIR = ROOT / "gui"
# Prefer a folder outside OneDrive — sync locks cause WinError 32 on rename.
_default_local = Path.home() / "PodcastsOffline"
DEFAULT_OUT = str(_default_local.resolve())

app = FastAPI(title="Podcast Offline GUI")
app.mount("/static", StaticFiles(directory=GUI_DIR), name="static")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=12, ge=1, le=50)
    country: str = Field(default="IL", min_length=2, max_length=2)


class FeedRequest(BaseModel):
    rss: str = Field(min_length=8)


class DownloadRequest(BaseModel):
    rss: str = Field(min_length=8)
    out_dir: str = Field(min_length=1)
    indices: list[int] | None = None
    skip_existing: bool = True


class AddLibraryRequest(BaseModel):
    name: str = Field(min_length=1)
    rss: str = Field(min_length=8)
    artist: str = ""
    aliases: list[str] = Field(default_factory=list)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(GUI_DIR / "index.html")


@app.get("/api/defaults")
def defaults() -> dict:
    return {
        "out_dir": DEFAULT_OUT,
        "library_count": len(core.load_library()),
        "podcastindex_configured": core.podcastindex_configured(),
    }


@app.post("/api/search")
def search(body: SearchRequest) -> dict:
    detailed = core.search_podcasts_detailed(
        body.query.strip(), limit=body.limit, country=body.country.upper()
    )
    return {
        "query": body.query,
        "results": detailed["results"],
        "notes": detailed.get("notes") or [],
        "podcastindex_configured": detailed.get("podcastindex_configured", False),
    }


class PodcastIndexKeysRequest(BaseModel):
    api_key: str = Field(min_length=4)
    api_secret: str = Field(min_length=4)


@app.post("/api/settings/podcastindex")
def save_podcastindex(body: PodcastIndexKeysRequest) -> dict:
    core.save_podcastindex_keys(body.api_key, body.api_secret)
    return {"status": "saved", "podcastindex_configured": True}


@app.post("/api/feed")
def feed(body: FeedRequest) -> dict:
    try:
        return core.get_feed_episodes(body.rss.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/download")
def download(body: DownloadRequest) -> dict:
    out = Path(body.out_dir.strip())
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Cannot use folder: {e}") from e

    try:
        return core.download_episodes(
            body.rss.strip(),
            out,
            indices=body.indices,
            skip_existing=body.skip_existing,
            quiet=True,
        )
    except Exception as e:
        # Never leak raw codec crashes to the UI; give a usable message.
        msg = str(e)
        if "charmap" in msg or "codec can't encode" in msg:
            msg = (
                "Download failed due to a Windows console encoding issue. "
                "Restart the app with run_gui.bat and try again."
            )
        raise HTTPException(status_code=400, detail=msg) from e


@app.post("/api/pick-folder")
def pick_folder() -> dict:
    """Open a native folder dialog on the server machine."""
    result: dict = {"path": None, "cancelled": True}

    def _pick() -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(title="Choose download folder")
            root.destroy()
            if path:
                result["path"] = path
                result["cancelled"] = False
        except Exception as e:
            result["error"] = str(e)

    thread = threading.Thread(target=_pick)
    thread.start()
    thread.join(timeout=300)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.post("/api/library/add")
def library_add(body: AddLibraryRequest) -> dict:
    shows = core.load_library()
    feed = body.rss.strip().rstrip("/")
    for show in shows:
        if show["feed"].rstrip("/").casefold() == feed.casefold():
            show["name"] = body.name.strip()
            if body.artist:
                show["artist"] = body.artist.strip()
            if body.aliases:
                show["aliases"] = body.aliases
            core.save_library(shows)
            return {"status": "updated", "show": show}
    entry = {
        "name": body.name.strip(),
        "artist": body.artist.strip(),
        "feed": body.rss.strip(),
        "aliases": body.aliases,
    }
    shows.append(entry)
    core.save_library(shows)
    return {"status": "added", "show": entry}


@app.get("/api/library")
def library_list() -> dict:
    return {"shows": core.load_library()}


def _pids_listening_on_port(port: int) -> set[int]:
    pids: set[int] = set()
    if sys.platform != "win32":
        try:
            out = subprocess.check_output(["lsof", "-ti", f"tcp:{port}"], text=True)
            for line in out.splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))
        except Exception:
            pass
        return pids

    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            errors="replace",
        )
    except Exception:
        return pids

    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        if needle not in line:
            continue
        # Prefer exact :8765 bound address match
        parts = line.split()
        if len(parts) < 2:
            continue
        local = parts[1] if parts[0].upper().startswith("TCP") else parts[0]
        if not (local.endswith(needle) or local.endswith(f"]{needle}")):
            continue
        pid_s = parts[-1]
        if pid_s.isdigit():
            pids.add(int(pid_s))
    return pids


def free_port(port: int) -> None:
    """Best-effort stop of whatever is still listening on our GUI port."""
    my_pid = os.getpid()
    for pid in _pids_listening_on_port(port):
        if pid == my_pid:
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                os.kill(pid, 9)
            print(f"Stopped old process on port {port} (PID {pid})")
        except Exception as e:
            print(f"Could not stop PID {pid} on port {port}: {e}")


def pick_port(preferred: int = 8765, attempts: int = 20) -> int:
    import socket

    free_port(preferred)
    time.sleep(0.4)

    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free port found from {preferred} upward")


def main() -> None:
    import uvicorn

    core.configure_stdout()
    port = pick_port(8765)
    url = f"http://127.0.0.1:{port}"
    print(f"Opening GUI at {url}")
    print("Keep this window open while using the app.")
    if port != 8765:
        print(f"(Port 8765 was busy, using {port} instead.)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
