#!/usr/bin/env python3
"""Search podcast RSS feeds and download episodes to a local folder."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

ITUNES_SEARCH = "https://itunes.apple.com/search"
FYYD_SEARCH = "https://api.fyyd.de/0.2/search/podcast"
GPODDER_SEARCH = "https://gpodder.net/search.json"
PODCASTINDEX_SEARCH = "https://api.podcastindex.org/api/1.0/search/byterm"
PODCASTINDEX_BY_TITLE = "https://api.podcastindex.org/api/1.0/search/bytitle"
PODCASTINDEX_BY_SPOTIFY = "https://api.podcastindex.org/api/1.0/podcasts/byspotifyid"
USER_AGENT = "podcast-cli/1.0 (personal offline library)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
DEFAULT_OUT = Path("downloads")
LIBRARY_PATH = Path(__file__).resolve().parent / "library.json"
KEYS_PATH = Path(__file__).resolve().parent / "podcastindex.keys"
ITUNES_COUNTRIES = ("IL", "US", "GB", "AU", "CA", "DE", "FR")
MUSIC_HINT_RE = re.compile(
    r"\b(lullaby|lullabies|mozart|playlist|album|sleep music|white noise|brain development)\b",
    re.I,
)
SPOTIFY_SHOW_RE = re.compile(r"open\.spotify\.com/show/([A-Za-z0-9]+)", re.I)
ANCHOR_URL_RE = re.compile(
    r"https?://(?:www\.)?anchor\.fm/[A-Za-z0-9_./\-]+",
    re.I,
)
ANCHOR_RSS_RE = re.compile(
    r"https?://(?:www\.)?anchor\.fm/s/[A-Za-z0-9]+/podcast/rss",
    re.I,
)


def safe_print(*args, **kwargs) -> None:
    """Print without crashing on Windows cp1252 consoles."""
    try:
        print(*args, **kwargs)
        return
    except Exception:
        pass
    try:
        stream = kwargs.get("file") or sys.stdout
        encoding = getattr(stream, "encoding", None) or "utf-8"
        text = " ".join(str(a) for a in args) + kwargs.get("end", "\n")
        data = text.encode(encoding, errors="replace")
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(data)
            buffer.flush()
        else:
            stream.write(data.decode(encoding, errors="replace"))
            stream.flush()
    except Exception:
        pass


def configure_stdout() -> None:
    """Prefer UTF-8 so Hebrew titles do not break logging."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_stdout()


def safe_name(text: str, max_len: int = 120) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text or "untitled")[:max_len]


def load_library() -> list[dict]:
    if not LIBRARY_PATH.exists():
        return []
    data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    return data.get("shows", [])


def save_library(shows: list[dict]) -> None:
    LIBRARY_PATH.write_text(
        json.dumps({"shows": shows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def search_library(query: str) -> list[dict]:
    q = query.casefold()
    hits = []
    for show in load_library():
        hay = " ".join(
            [
                show.get("name", ""),
                show.get("artist", ""),
                show.get("feed", ""),
                " ".join(show.get("aliases", [])),
            ]
        ).casefold()
        if q in hay:
            hits.append(
                {
                    "name": show["name"],
                    "artist": show.get("artist") or "library",
                    "feed": show["feed"],
                    "episodes": show.get("episodes"),
                    "source": "library",
                }
            )
    return hits


def search_itunes(query: str, limit: int, country: str) -> list[dict]:
    r = requests.get(
        ITUNES_SEARCH,
        params={
            "term": query,
            "media": "podcast",
            "entity": "podcast",
            "limit": limit,
            "country": country,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    results = []
    for item in r.json().get("results", []):
        feed = item.get("feedUrl")
        if not feed:
            continue
        results.append(
            {
                "name": item.get("collectionName") or item.get("trackName") or "?",
                "artist": item.get("artistName") or "?",
                "feed": feed,
                "episodes": item.get("trackCount"),
                "source": "itunes",
            }
        )
    return results


def search_fyyd(query: str, limit: int) -> list[dict]:
    r = requests.get(
        FYYD_SEARCH,
        params={"term": query, "count": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data") or []
    results = []
    for item in data:
        feed = item.get("xmlURL")
        if not feed:
            continue
        results.append(
            {
                "name": item.get("title") or "?",
                "artist": item.get("author") or "?",
                "feed": feed,
                "episodes": item.get("episode_count"),
                "source": "fyyd",
            }
        )
    return results


def search_gpodder(query: str, limit: int) -> list[dict]:
    r = requests.get(
        GPODDER_SEARCH,
        params={"q": query},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json() if isinstance(r.json(), list) else []
    results = []
    for item in data[:limit]:
        feed = item.get("url")
        if not feed:
            continue
        results.append(
            {
                "name": item.get("title") or "?",
                "artist": item.get("author") or "gpodder",
                "feed": feed,
                "episodes": item.get("subscribers"),
                "source": "gpodder",
            }
        )
    return results


def load_podcastindex_keys() -> tuple[str, str]:
    key = os.environ.get("PODCASTINDEX_API_KEY", "").strip()
    secret = os.environ.get("PODCASTINDEX_API_SECRET", "").strip()
    if key and secret:
        return key, secret
    if KEYS_PATH.exists():
        for line in KEYS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "PODCASTINDEX_API_KEY":
                key = v
            elif k == "PODCASTINDEX_API_SECRET":
                secret = v
    return key, secret


def save_podcastindex_keys(key: str, secret: str) -> None:
    KEYS_PATH.write_text(
        "# Free keys from https://api.podcastindex.org/signup\n"
        f"PODCASTINDEX_API_KEY={key.strip()}\n"
        f"PODCASTINDEX_API_SECRET={secret.strip()}\n",
        encoding="utf-8",
    )


def podcastindex_configured() -> bool:
    key, secret = load_podcastindex_keys()
    return bool(key and secret)


def _podcastindex_headers() -> dict[str, str] | None:
    key, secret = load_podcastindex_keys()
    if not key or not secret:
        return None
    import hashlib

    epoch = str(int(time.time()))
    token = hashlib.sha1(f"{key}{secret}{epoch}".encode("utf-8")).hexdigest()
    return {
        "User-Agent": USER_AGENT,
        "X-Auth-Key": key,
        "X-Auth-Date": epoch,
        "Authorization": token,
    }


def search_podcastindex(query: str, limit: int) -> list[dict]:
    headers = _podcastindex_headers()
    if not headers:
        return []
    results: list[dict] = []
    seen: set[str] = set()
    for endpoint, params in (
        (PODCASTINDEX_SEARCH, {"q": query, "max": limit}),
        (PODCASTINDEX_BY_TITLE, {"q": query, "max": limit}),
    ):
        try:
            r = requests.get(endpoint, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            feeds = r.json().get("feeds") or []
        except requests.RequestException as e:
            safe_print(f"Warning: Podcast Index failed ({endpoint}): {e}", file=sys.stderr)
            continue
        for item in feeds:
            feed = item.get("url")
            if not feed:
                continue
            key = feed.rstrip("/").casefold()
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "name": item.get("title") or "?",
                    "artist": item.get("author") or item.get("ownerName") or "?",
                    "feed": feed,
                    "episodes": item.get("episodeCount"),
                    "source": "podcastindex",
                }
            )
            if len(results) >= limit:
                return results
    return results


def search_itunes_multi(query: str, limit: int, primary_country: str) -> list[dict]:
    countries = [primary_country.upper()] + [
        c for c in ITUNES_COUNTRIES if c != primary_country.upper()
    ]
    merged: list[dict] = []
    seen: set[str] = set()
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(country: str) -> list[dict]:
        try:
            return search_itunes(query, limit=limit, country=country)
        except requests.RequestException:
            return []

    with ThreadPoolExecutor(max_workers=min(6, len(countries))) as pool:
        futs = [pool.submit(_one, c) for c in countries[:5]]
        for fut in as_completed(futs):
            for item in fut.result():
                key = item["feed"].rstrip("/").casefold()
                if key in seen:
                    continue
                seen.add(key)
                item = dict(item)
                item["source"] = "itunes"
                merged.append(item)
                if len(merged) >= limit:
                    return merged
    return merged


def resolve_anchor_to_rss(anchor_url: str) -> str | None:
    """Follow an Anchor / Spotify for Podcasters page to its RSS feed."""
    url = anchor_url.strip()
    if ANCHOR_RSS_RE.fullmatch(url.rstrip("/")) or url.rstrip("/").endswith("/podcast/rss"):
        return url if url.endswith("/podcast/rss") else url.rstrip("/") + "/podcast/rss"

    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30, allow_redirects=True)
    r.raise_for_status()
    match = ANCHOR_RSS_RE.search(r.text)
    if match:
        return match.group(0)
    # stationId embedded in page
    station = re.search(r'"stationId"\s*:\s*"([A-Za-z0-9]+)"', r.text)
    if station:
        return f"https://anchor.fm/s/{station.group(1)}/podcast/rss"
    return None


def _spotify_embed_meta(show_id: str) -> dict:
    """Best-effort title from Spotify embed (works when /show is a SPA shell)."""
    r = requests.get(
        f"https://open.spotify.com/embed/show/{show_id}",
        headers={"User-Agent": BROWSER_UA},
        timeout=30,
    )
    r.raise_for_status()
    html = r.text.replace("\\/", "/")
    title = None
    description = ""

    # Prefer show entity from Next.js payload when present.
    next_data = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        flags=re.S,
    )
    if next_data:
        try:
            payload = json.loads(next_data.group(1))
            entity = (
                payload.get("props", {})
                .get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity")
            ) or {}
            if isinstance(entity, dict):
                title = entity.get("name") or entity.get("title") or title
                description = entity.get("description") or description
        except Exception:
            pass

    if not title:
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m:
            title = m.group(1)
    if not description:
        m = re.search(r'<meta property="og:description" content="([^"]*)"', html)
        if m:
            description = m.group(1)
    return {"title": title, "description": description}


def _strong_title_match(query: str, candidate: str) -> bool:
    q = re.sub(r"\s+", " ", (query or "").casefold()).strip()
    c = re.sub(r"\s+", " ", (candidate or "").casefold()).strip()
    if not q or not c:
        return False
    if q == c or q in c or c in q:
        return True
    # Require at least 2 meaningful overlapping tokens (Hebrew/Latin).
    q_tokens = {t for t in re.split(r"[^\w\u0590-\u05ff]+", q, flags=re.U) if len(t) >= 3}
    c_tokens = {t for t in re.split(r"[^\w\u0590-\u05ff]+", c, flags=re.U) if len(t) >= 3}
    return len(q_tokens & c_tokens) >= 2


def resolve_spotify_show(show_id_or_url: str) -> dict | None:
    """
    Resolve a Spotify show URL/ID to a public RSS feed when possible.

    Strategy:
    1) Podcast Index (optional API keys)
    2) Scrape /show page for Anchor links (often blocked → SPA shell)
    3) Read embed title, then search library / iTunes / fyyd with strong name match only
    """
    match = SPOTIFY_SHOW_RE.search(show_id_or_url)
    show_id = match.group(1) if match else show_id_or_url.strip()
    if not show_id:
        return None

    headers = _podcastindex_headers()
    if headers:
        try:
            r = requests.get(
                PODCASTINDEX_BY_SPOTIFY,
                params={"id": show_id},
                headers=headers,
                timeout=30,
            )
            if r.ok:
                feed_obj = r.json().get("feed") or {}
                feed = feed_obj.get("url")
                if feed:
                    return {
                        "name": feed_obj.get("title") or f"Spotify show {show_id}",
                        "artist": feed_obj.get("author") or "spotify",
                        "feed": feed,
                        "episodes": feed_obj.get("episodeCount"),
                        "source": "spotify+podcastindex",
                    }
        except requests.RequestException:
            pass

    try:
        page = requests.get(
            f"https://open.spotify.com/show/{show_id}",
            headers={"User-Agent": BROWSER_UA},
            timeout=30,
        )
        if page.ok:
            html = page.text.replace("\\/", "/")
            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            name = title_match.group(1) if title_match else None
            anchors = list(dict.fromkeys(ANCHOR_URL_RE.findall(html)))
            for bare in re.findall(r"anchor\.fm/([A-Za-z0-9_\-]+)", html, flags=re.I):
                anchors.append(f"https://anchor.fm/{bare}")
            for anchor in dict.fromkeys(anchors):
                if any(x in anchor.lower() for x in ("/api/", ".js", ".css", "/static/")):
                    continue
                try:
                    feed = resolve_anchor_to_rss(anchor)
                except requests.RequestException:
                    continue
                if feed:
                    return {
                        "name": name or f"Spotify show {show_id}",
                        "artist": "spotify/anchor",
                        "feed": feed,
                        "episodes": None,
                        "source": "spotify",
                    }
    except requests.RequestException:
        pass

    try:
        meta = _spotify_embed_meta(show_id)
    except requests.RequestException:
        meta = {"title": None, "description": ""}

    title = (meta.get("title") or "").strip()
    description = (meta.get("description") or "").strip()
    queries = [q for q in (title, description[:80]) if q and len(q) >= 4]

    for q in queries:
        groups: list[list[dict]] = [search_library(q)]
        for finder in (
            lambda: search_itunes(q, limit=8, country="IL"),
            lambda: search_itunes(q, limit=8, country="US"),
            lambda: search_fyyd(q, limit=8),
        ):
            try:
                groups.append(finder())
            except requests.RequestException:
                continue
        for group in groups:
            for item in group:
                if _strong_title_match(q, item.get("name") or ""):
                    out = dict(item)
                    out["source"] = f"spotify→{item.get('source', 'dir')}"
                    return out
    return None


def search_spotify_web(query: str, limit: int = 5) -> list[dict]:
    """
    Search Spotify's public web search page, then resolve shows that expose RSS.
    Slower, but finds Anchor/Spotify-for-Podcasters shows missing from Apple/fyyd.
    """
    from urllib.parse import quote

    # Never treat a Spotify show URL as a search keyword.
    if SPOTIFY_SHOW_RE.search(query):
        resolved = resolve_spotify_show(query)
        return [resolved] if resolved else []

    url = f"https://open.spotify.com/search/{quote(query)}/shows"
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
    r.raise_for_status()
    html = r.text.replace("\\/", "/")
    ids = list(dict.fromkeys(re.findall(r"/show/([A-Za-z0-9]{22})", html)))
    results = []
    for show_id in ids[: max(limit * 2, 1)]:
        try:
            resolved = resolve_spotify_show(show_id)
        except requests.RequestException:
            continue
        if resolved:
            results.append(resolved)
        if len(results) >= limit:
            break
    return results


def merge_results(*groups: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for group in groups:
        for item in group:
            key = item["feed"].rstrip("/").casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)/", url or "", re.I))


def _yt_dlp_bin() -> str:
    found = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if found:
        return found
    # Common on Windows when Scripts/ is not on PATH
    scripts = Path(sys.executable).resolve().parent / "Scripts"
    for name in ("yt-dlp.exe", "yt-dlp"):
        candidate = scripts / name
        if candidate.exists():
            return str(candidate)
    return "yt-dlp"


def search_youtube(query: str, limit: int = 6) -> list[dict]:
    """Search YouTube via yt-dlp (channels + top videos)."""
    bin_path = _yt_dlp_bin()
    try:
        proc = subprocess.run(
            [
                bin_path,
                f"ytsearch{max(limit * 2, 8)}:{query}",
                "--flat-playlist",
                "--print",
                "%(id)s\t%(title)s\t%(channel)s\t%(channel_url)s\t%(webpage_url)s\t%(duration)s",
                "--no-warnings",
                "--ignore-errors",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
    except FileNotFoundError:
        safe_print(
            "Warning: yt-dlp not found. Install with: py -3 -m pip install yt-dlp",
            file=sys.stderr,
        )
        return []
    except subprocess.TimeoutExpired:
        safe_print("Warning: YouTube search timed out", file=sys.stderr)
        return []

    results: list[dict] = []
    seen_channels: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        _vid, title, channel, channel_url, webpage = parts[:5]
        channel_url = (channel_url or "").strip()
        webpage = (webpage or "").strip()
        channel = (channel or "YouTube").strip()
        title = (title or "YouTube video").strip()

        if channel_url and channel_url not in seen_channels:
            seen_channels.add(channel_url)
            results.append(
                {
                    "name": f"{channel} (YouTube channel)",
                    "artist": channel,
                    "feed": channel_url,
                    "episodes": None,
                    "source": "youtube",
                }
            )
        if webpage:
            results.append(
                {
                    "name": title,
                    "artist": channel,
                    "feed": webpage,
                    "episodes": 1,
                    "source": "youtube-video",
                }
            )
        if len(results) >= limit:
            break
    return results[:limit]


def get_youtube_entries(url: str, limit: int | None = 50) -> dict:
    """List videos from a YouTube URL / channel / playlist as episode-like rows."""
    bin_path = _yt_dlp_bin()
    max_items = limit or 50
    cmd = [
        bin_path,
        "--flat-playlist",
        "--playlist-end",
        str(max_items),
        "-J",
        url,
        "--no-warnings",
        "--ignore-errors",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "yt-dlp is required for YouTube. Install: py -3 -m pip install yt-dlp"
        ) from e

    if not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or "yt-dlp returned no data")

    data = json.loads(proc.stdout)
    entries = data.get("entries") or [data]
    title = data.get("title") or data.get("channel") or "YouTube"
    author = data.get("uploader") or data.get("channel") or "YouTube"
    episodes = []
    for i, entry in enumerate(entries, 1):
        if not entry:
            continue
        vid = entry.get("id") or ""
        webpage = entry.get("url") or entry.get("webpage_url") or ""
        if vid and not webpage.startswith("http"):
            webpage = f"https://www.youtube.com/watch?v={vid}"
        elif vid and "watch?v=" not in webpage and not webpage.startswith("http"):
            webpage = f"https://www.youtube.com/watch?v={vid}"
        if vid and not webpage:
            webpage = f"https://www.youtube.com/watch?v={vid}"
        episodes.append(
            {
                "index": i,
                "title": entry.get("title") or f"video-{i}",
                "published": "",
                "has_audio": True,
                "audio_url": webpage,
                "video_id": vid,
            }
        )
    return {
        "title": title,
        "author": author,
        "feed": url,
        "episode_count": len(episodes),
        "episodes": episodes,
        "kind": "youtube",
    }


def download_youtube_entries(
    url: str,
    out_dir: Path,
    limit: int | None = None,
    indices: list[int] | None = None,
    skip_existing: bool = True,
    quiet: bool = False,
) -> dict:
    info = get_youtube_entries(url, limit=None if indices else (limit or 50))
    show = safe_name(info["title"] or "youtube")
    dest = out_dir / show
    dest.mkdir(parents=True, exist_ok=True)

    selected = info["episodes"]
    if indices:
        wanted = set(indices)
        selected = [ep for ep in selected if ep["index"] in wanted]
    elif limit:
        selected = selected[:limit]

    bin_path = _yt_dlp_bin()
    results = []
    downloaded = 0
    for ep in selected:
        title = ep["title"]
        index = ep["index"]
        webpage = ep.get("audio_url") or ""
        base = f"{index:03d} - {safe_name(title)}"
        existing = list(dest.glob(f"{base}.*"))
        existing = [p for p in existing if p.suffix.lower() not in {".part", ".ytdl"}]
        if skip_existing and existing:
            results.append(
                {
                    "index": index,
                    "title": title,
                    "status": "exists",
                    "path": str(existing[0].resolve()),
                }
            )
            continue

        outtmpl = str(dest / f"{base}.%(ext)s")
        # Prefer audio-only; keep original format if ffmpeg is missing.
        cmd = [
            bin_path,
            "-f",
            "bestaudio/best",
            "-o",
            outtmpl,
            "--no-playlist",
            "--no-warnings",
            webpage,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check=False,
            )
            files = list(dest.glob(f"{base}.*"))
            files = [p for p in files if p.suffix.lower() not in {".part", ".ytdl"}]
            if proc.returncode != 0 or not files:
                results.append(
                    {
                        "index": index,
                        "title": title,
                        "status": "error",
                        "reason": (proc.stderr or proc.stdout or "yt-dlp failed")[-300:],
                    }
                )
                continue
            downloaded += 1
            results.append(
                {
                    "index": index,
                    "title": title,
                    "status": "downloaded",
                    "path": str(files[0].resolve()),
                }
            )
            if not quiet:
                safe_print(f"Downloaded: {files[0].name}")
        except Exception as e:
            results.append(
                {
                    "index": index,
                    "title": title,
                    "status": "error",
                    "reason": str(e),
                }
            )

    if not quiet:
        safe_print(f"\nDone. Saved {downloaded} file(s) to: {dest.resolve()}")
    return {
        "show": show,
        "dest": str(dest.resolve()),
        "downloaded": downloaded,
        "results": results,
    }


def search_podcasts(query: str, limit: int = 10, country: str = "IL") -> list[dict]:
    """
    Search many public podcast directories in parallel, then YouTube as fallback/music.

    Returns list of {name, artist, feed, episodes, source}.
    Extra key `_meta` is NOT used; use search_podcasts_detailed for notes.
    """
    return search_podcasts_detailed(query, limit=limit, country=country)["results"]


def search_podcasts_detailed(
    query: str, limit: int = 12, country: str = "IL"
) -> dict:
    query = query.strip()
    notes: list[str] = []

    if is_youtube_url(query):
        return {
            "results": [
                {
                    "name": query,
                    "artist": "YouTube",
                    "feed": query,
                    "episodes": None,
                    "source": "youtube",
                }
            ],
            "notes": [],
            "podcastindex_configured": podcastindex_configured(),
        }

    if SPOTIFY_SHOW_RE.search(query) or re.fullmatch(r"[A-Za-z0-9]{22}", query or ""):
        try:
            resolved = resolve_spotify_show(query)
            return {
                "results": [resolved] if resolved else [],
                "notes": (
                    []
                    if resolved
                    else [
                        "Spotify show has no public RSS we could resolve "
                        "(music albums / Spotify-only shows often have no podcast feed)."
                    ]
                ),
                "podcastindex_configured": podcastindex_configured(),
            }
        except requests.RequestException as e:
            return {
                "results": [],
                "notes": [f"Spotify resolve failed: {e}"],
                "podcastindex_configured": podcastindex_configured(),
            }

    if not podcastindex_configured():
        notes.append(
            "Tip: add free Podcast Index keys (millions of feeds) — "
            "sign up at https://api.podcastindex.org/signup then save in the GUI settings "
            "or podcastindex.keys file."
        )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = {
        "library": lambda: search_library(query),
        "itunes": lambda: search_itunes_multi(query, limit=limit, primary_country=country),
        "fyyd": lambda: search_fyyd(query, limit=limit),
        "gpodder": lambda: search_gpodder(query, limit=limit),
        "podcastindex": lambda: search_podcastindex(query, limit=limit),
    }

    buckets: dict[str, list[dict]] = {k: [] for k in jobs}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                buckets[name] = fut.result() or []
            except Exception as e:
                safe_print(f"Warning: {name} search failed: {e}", file=sys.stderr)
                notes.append(f"{name} search failed: {e}")

    merged = merge_results(
        buckets["library"],
        buckets["podcastindex"],
        buckets["itunes"],
        buckets["fyyd"],
        buckets["gpodder"],
        limit=max(limit * 2, 30),
    )

    name_hit = any(_strong_title_match(query, m.get("name") or "") for m in merged)
    if len(merged) < 5 or not name_hit:
        try:
            spotify = search_spotify_web(query, limit=min(5, limit))
            merged = merge_results(merged, spotify, limit=max(limit * 2, 30))
        except Exception as e:
            safe_print(f"Warning: Spotify web search failed: {e}", file=sys.stderr)

    youtube: list[dict] = []
    yt_limit = 10 if (MUSIC_HINT_RE.search(query) or not name_hit) else 5
    try:
        youtube = search_youtube(query, limit=yt_limit)
    except Exception as e:
        safe_print(f"Warning: YouTube search failed: {e}", file=sys.stderr)
        notes.append(f"YouTube search failed: {e}")

    # For music-like queries, put YouTube first so lullabies aren't buried under weak podcast matches.
    if MUSIC_HINT_RE.search(query) or not name_hit:
        merged = merge_results(youtube, merged, limit=max(limit * 2, 30))
    else:
        merged = merge_results(merged, youtube, limit=max(limit * 2, 30))

    def _rank(item: dict) -> tuple:
        name = item.get("name") or ""
        src = item.get("source") or ""
        if _strong_title_match(query, name):
            return (0, src)
        if src.startswith("youtube"):
            return (1 if MUSIC_HINT_RE.search(query) else 2, src)
        if src == "library":
            return (0, src)
        return (3, src)

    merged = sorted(merged, key=_rank)

    strong_podcast = any(
        _strong_title_match(query, r.get("name") or "")
        and not str(r.get("source", "")).startswith("youtube")
        for r in merged
    )
    if MUSIC_HINT_RE.search(query) and not strong_podcast:
        notes.append(
            "No matching podcast RSS for this title. Names like “Baby Mozart … lullabies” are often "
            "Spotify/Apple Music albums (not podcasts). Prefer YouTube results, or paste a YouTube "
            "playlist/channel URL."
        )
    elif not merged:
        notes.append(
            "No public podcast RSS found. Not everything on Spotify is a podcast — "
            "only shows with a public feed can be downloaded this way."
        )

    return {
        "results": merged[:limit],
        "notes": notes,
        "podcastindex_configured": podcastindex_configured(),
    }


def print_search_results(results: list[dict]) -> None:
    if not results:
        print("No podcasts found.")
        print("Tip: if you already have an RSS URL, use:")
        print('  py -3 podcast_cli.py download "https://..." -o downloads')
        print("Or save it to your library:")
        print('  py -3 podcast_cli.py add "Show name" "https://..."')
        return
    for i, p in enumerate(results, 1):
        eps = f", ~{p['episodes']} eps" if p.get("episodes") else ""
        src = p.get("source", "?")
        print(f"{i}. {p['name']}")
        print(f"   by {p['artist']}{eps}  [{src}]")
        print(f"   RSS: {p['feed']}")
        print()


def parse_feed(rss_url: str) -> feedparser.FeedParserDict:
    r = requests.get(rss_url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Could not parse feed: {feed.bozo_exception}")
    return feed


def episode_audio_url(entry) -> str | None:
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        typ = (enc.get("type") or "").lower()
        if href and (typ.startswith("audio") or _looks_like_audio(href)):
            return href
    for link in getattr(entry, "links", []) or []:
        href = link.get("href")
        typ = (link.get("type") or "").lower()
        if href and (typ.startswith("audio") or link.get("rel") == "enclosure"):
            return href
    return None


def _looks_like_audio(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"))


def audio_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"):
        if path.endswith(ext):
            return ext
    return ".mp3"


def get_feed_episodes(rss_url: str, limit: int | None = None) -> dict:
    """Return structured show + episode metadata for CLI/GUI reuse."""
    if is_youtube_url(rss_url):
        return get_youtube_entries(rss_url, limit=limit)

    feed = parse_feed(rss_url)
    title = feed.feed.get("title") or rss_url
    author = feed.feed.get("author") or feed.feed.get("publisher") or ""
    entries = feed.entries[:limit] if limit else feed.entries
    episodes = []
    for i, entry in enumerate(entries, 1):
        audio = episode_audio_url(entry)
        episodes.append(
            {
                "index": i,
                "title": entry.get("title") or f"episode-{i}",
                "published": entry.get("published") or entry.get("updated") or "",
                "has_audio": bool(audio),
                "audio_url": audio,
            }
        )
    return {
        "title": title,
        "author": author,
        "feed": rss_url,
        "episode_count": len(feed.entries),
        "episodes": episodes,
        "kind": "rss",
    }


def list_episodes(rss_url: str, limit: int | None = None) -> None:
    info = get_feed_episodes(rss_url, limit=limit)
    print(f"Show: {info['title']}")
    print(f"Episodes: {info['episode_count']}\n")
    for ep in info["episodes"]:
        mark = "OK" if ep["has_audio"] else "NO AUDIO"
        print(f"{ep['index']}. [{mark}] {ep['title']}")
        if ep["published"]:
            print(f"   {ep['published']}")
        if ep["audio_url"]:
            print(f"   {ep['audio_url']}")
        print()


def replace_with_retry(src: Path, dest: Path, attempts: int = 12) -> None:
    """
    Move/replace a file on Windows even when OneDrive/Antivirus briefly locks it.
    WinError 32 = file in use.
    """
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError as e:
            last_err = e
        except OSError as e:
            # WinError 32 / sharing violation
            winerr = getattr(e, "winerror", None)
            if winerr not in (32, 33) and e.errno not in (13, 11):
                raise
            last_err = e
        time.sleep(0.25 * (i + 1))
    # Final fallback: copy then delete temp
    try:
        shutil.copy2(src, dest)
        try:
            src.unlink(missing_ok=True)
        except OSError:
            pass
        return
    except Exception as e:
        raise OSError(f"Could not finalize download to {dest}: {last_err or e}") from e


def download_one_episode(
    entry,
    index: int,
    dest: Path,
    skip_existing: bool = True,
) -> dict:
    """Download a single feed entry. Returns a status dict."""
    ep_title = entry.get("title") or f"episode-{index}"
    audio = episode_audio_url(entry)
    if not audio:
        return {"index": index, "title": ep_title, "status": "skipped", "reason": "no audio"}

    ext = audio_extension(audio)
    filename = f"{index:03d} - {safe_name(ep_title)}{ext}"
    path = dest / filename

    if skip_existing and path.exists() and path.stat().st_size > 0:
        return {
            "index": index,
            "title": ep_title,
            "status": "exists",
            "path": str(path.resolve()),
        }

    # Write outside OneDrive first (avoids sync locks on .part -> final rename).
    tmp_dir = Path(tempfile.gettempdir()) / "podcast_cli_downloads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{os.getpid()}_{index}_{safe_name(ep_title)[:40]}.part"

    try:
        with requests.get(
            audio,
            headers={"User-Agent": USER_AGENT},
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
                f.flush()
                os.fsync(f.fileno())

        replace_with_retry(tmp, path)
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "index": index,
            "title": ep_title,
            "status": "error",
            "reason": str(e),
        }

    return {
        "index": index,
        "title": ep_title,
        "status": "downloaded",
        "path": str(path.resolve()),
    }


def download_episodes(
    rss_url: str,
    out_dir: Path,
    limit: int | None = None,
    skip_existing: bool = True,
    indices: list[int] | None = None,
    quiet: bool = False,
) -> dict:
    """
    Download episodes to out_dir/<show>/.

    indices: optional 1-based episode numbers (feed order, newest first).
    limit: used only when indices is None (newest N).
    quiet: suppress console progress (used by GUI).
    """
    if is_youtube_url(rss_url):
        return download_youtube_entries(
            rss_url,
            out_dir,
            limit=limit,
            indices=indices,
            skip_existing=skip_existing,
            quiet=quiet,
        )

    feed = parse_feed(rss_url)
    show = safe_name(feed.feed.get("title") or "podcast")
    dest = out_dir / show
    dest.mkdir(parents=True, exist_ok=True)

    selected: list[tuple[int, object]] = []
    if indices:
        wanted = set(indices)
        for i, entry in enumerate(feed.entries, 1):
            if i in wanted:
                selected.append((i, entry))
    else:
        entries = feed.entries[:limit] if limit else feed.entries
        selected = list(enumerate(entries, 1))

    results = []
    downloaded = 0
    for i, entry in selected:
        try:
            result = download_one_episode(entry, i, dest, skip_existing=skip_existing)
        except Exception as e:
            result = {
                "index": i,
                "title": entry.get("title") or f"episode-{i}",
                "status": "error",
                "reason": str(e),
            }
        results.append(result)
        if result["status"] == "downloaded":
            downloaded += 1
            if not quiet:
                safe_print(f"Downloading: {Path(result['path']).name}")
        elif result["status"] == "exists":
            if not quiet:
                safe_print(f"Exists: {Path(result['path']).name}")
        elif result["status"] == "error":
            if not quiet:
                safe_print(f"Error: {result['title']} ({result.get('reason')})")
        else:
            if not quiet:
                safe_print(f"Skip ({result.get('reason', '?')}): {result['title']}")

    if not quiet:
        safe_print(f"\nDone. Saved {downloaded} file(s) to: {dest.resolve()}")
    return {
        "show": show,
        "dest": str(dest.resolve()),
        "downloaded": downloaded,
        "results": results,
    }


def cmd_search(args: argparse.Namespace) -> int:
    results = search_podcasts(args.query, limit=args.limit, country=args.country)
    print_search_results(results)
    if args.download and results:
        choice = args.index
        if choice is None:
            raw = input("Enter number to download (or Enter to cancel): ").strip()
            if not raw:
                return 0
            choice = int(raw)
        if choice < 1 or choice > len(results):
            print("Invalid selection.", file=sys.stderr)
            return 1
        feed = results[choice - 1]["feed"]
        print(f"Using RSS: {feed}\n")
        download_episodes(feed, Path(args.out), limit=args.episodes)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    list_episodes(args.rss, limit=args.limit)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    download_episodes(args.rss, Path(args.out), limit=args.limit)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    shows = load_library()
    feed = args.rss.rstrip("/")
    for show in shows:
        if show["feed"].rstrip("/").casefold() == feed.casefold():
            show["name"] = args.name
            if args.artist:
                show["artist"] = args.artist
            save_library(shows)
            print(f"Updated library entry: {args.name}")
            return 0
    shows.append(
        {
            "name": args.name,
            "artist": args.artist or "",
            "feed": args.rss,
            "aliases": args.aliases or [],
        }
    )
    save_library(shows)
    print(f"Added to library: {args.name}")
    print(f"RSS: {args.rss}")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    shows = load_library()
    if not shows:
        print("Library is empty. Add a show with:")
        print('  py -3 podcast_cli.py add "Show name" "https://feeds..."')
        return 0
    for i, show in enumerate(shows, 1):
        print(f"{i}. {show['name']}")
        if show.get("artist"):
            print(f"   by {show['artist']}")
        print(f"   RSS: {show['feed']}")
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="podcast_cli",
        description="Search podcast RSS feeds and download episodes as audio files.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="Search podcasts and show RSS URLs")
    s.add_argument("query", help="Search text, e.g. מתוק מדבש")
    s.add_argument("-n", "--limit", type=int, default=10, help="Max search results")
    s.add_argument("--country", default="IL", help="iTunes country code (default: IL)")
    s.add_argument(
        "-d",
        "--download",
        action="store_true",
        help="After search, download a selected show",
    )
    s.add_argument(
        "-i",
        "--index",
        type=int,
        help="Result number to download (with --download)",
    )
    s.add_argument(
        "-e",
        "--episodes",
        type=int,
        default=None,
        help="Max episodes to download (newest first)",
    )
    s.add_argument(
        "-o",
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output folder (default: {DEFAULT_OUT})",
    )
    s.set_defaults(func=cmd_search)

    l = sub.add_parser("list", help="List episodes from an RSS URL")
    l.add_argument("rss", help="Podcast RSS feed URL")
    l.add_argument("-n", "--limit", type=int, default=None, help="Max episodes to show")
    l.set_defaults(func=cmd_list)

    d = sub.add_parser("download", help="Download episodes from an RSS URL")
    d.add_argument("rss", help="Podcast RSS feed URL")
    d.add_argument(
        "-o",
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output folder (default: {DEFAULT_OUT})",
    )
    d.add_argument(
        "-n",
        "--limit",
        type=int,
        default=None,
        help="Max episodes to download (newest first)",
    )
    d.set_defaults(func=cmd_download)

    a = sub.add_parser("add", help="Save a known RSS feed into local library.json")
    a.add_argument("name", help="Display name")
    a.add_argument("rss", help="RSS feed URL")
    a.add_argument("--artist", default="", help="Author / host")
    a.add_argument(
        "--alias",
        dest="aliases",
        action="append",
        default=[],
        help="Extra search alias (repeatable)",
    )
    a.set_defaults(func=cmd_add)

    lib = sub.add_parser("library", help="List shows saved in library.json")
    lib.set_defaults(func=cmd_library)

    return p


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
