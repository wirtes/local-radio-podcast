from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from flask import Flask, Response, abort, send_file, url_for
from mutagen import File as MutagenFile


ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("content", CONTENT_NS)


@dataclass(frozen=True)
class FeedConfig:
    title: str
    description: str
    author: str
    language: str
    explicit: str
    image_url: str | None
    category: str
    directories: tuple[Path, ...]
    base_url: str | None
    host: str
    port: int


@dataclass(frozen=True)
class Episode:
    id: str
    path: Path
    title: str
    description: str
    author: str
    album: str | None
    duration_seconds: int | None
    pubdate: datetime
    size: int

    @property
    def duration_text(self) -> str | None:
        if self.duration_seconds is None:
            return None
        hours, remainder = divmod(self.duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_config(path: Path) -> FeedConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.toml to config.toml first."
        )

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    feed = data.get("feed", {})
    server = data.get("server", {})
    raw_directories = feed.get("directories", [])

    if not raw_directories:
        raise ValueError("Add at least one MP3 directory to feed.directories in config.toml.")

    directories = tuple(Path(item).expanduser().resolve() for item in raw_directories)
    missing = [str(directory) for directory in directories if not directory.is_dir()]
    if missing:
        raise ValueError(f"Configured directories do not exist: {', '.join(missing)}")

    return FeedConfig(
        title=str(feed.get("title", "Local MP3 Podcast")),
        description=str(feed.get("description", "A private podcast feed from local MP3 files.")),
        author=str(feed.get("author", "Local Podcast")),
        language=str(feed.get("language", "en-us")),
        explicit=str(feed.get("explicit", "false")).lower(),
        image_url=feed.get("image_url") or None,
        category=str(feed.get("category", "Music")),
        directories=directories,
        base_url=server.get("base_url") or None,
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 8000)),
    )


def create_app(config_path: str | Path | None = None) -> Flask:
    path = Path(config_path or os.environ.get("PODCAST_CONFIG", "config.toml")).resolve()
    config = load_config(path)

    app = Flask(__name__)
    app.config["PODCAST_CONFIG"] = config

    @app.get("/")
    def index() -> Response:
        feed_url = absolute_url("feed", config)
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(config.title)}</title>
</head>
<body>
  <h1>{escape_html(config.title)}</h1>
  <p>{escape_html(config.description)}</p>
  <p><a href="{feed_url}">Podcast RSS feed</a></p>
</body>
</html>
"""
        return Response(body, mimetype="text/html")

    @app.get("/feed.xml")
    def feed() -> Response:
        episodes = scan_episodes(config)
        xml = build_feed_xml(config, episodes)
        return Response(xml, mimetype="application/rss+xml; charset=utf-8")

    @app.get("/audio/<episode_id>.mp3")
    def audio(episode_id: str):
        episode = find_episode(config, episode_id)
        if episode is None:
            abort(404)
        return send_file(
            episode.path,
            mimetype="audio/mpeg",
            as_attachment=False,
            conditional=True,
            download_name=episode.path.name,
        )

    return app


def scan_episodes(config: FeedConfig) -> list[Episode]:
    episodes: list[Episode] = []
    for directory in config.directories:
        for path in sorted(directory.rglob("*.mp3")):
            if path.is_file():
                episodes.append(read_episode(path, config))
    return sorted(episodes, key=lambda episode: episode.pubdate, reverse=True)


def find_episode(config: FeedConfig, episode_id: str) -> Episode | None:
    for episode in scan_episodes(config):
        if episode.id == episode_id:
            return episode
    return None


def read_episode(path: Path, config: FeedConfig) -> Episode:
    stat = path.stat()
    metadata = read_audio_metadata(path)
    title = metadata.get("title") or path.stem
    author = metadata.get("artist") or metadata.get("albumartist") or config.author
    description = metadata.get("comment") or metadata.get("description") or title
    pubdate = parse_pubdate(metadata.get("date"), stat.st_mtime)
    duration_seconds = metadata.get("duration_seconds")

    return Episode(
        id=episode_id(path),
        path=path,
        title=title,
        description=description,
        author=author,
        album=metadata.get("album"),
        duration_seconds=duration_seconds,
        pubdate=pubdate,
        size=stat.st_size,
    )


def read_audio_metadata(path: Path) -> dict[str, Any]:
    audio = MutagenFile(path, easy=True)
    metadata: dict[str, Any] = {}

    if audio is None:
        return metadata

    for key in ("title", "artist", "albumartist", "album", "date", "comment", "description"):
        value = audio.tags.get(key) if audio.tags else None
        if value:
            metadata[key] = str(value[0])

    if audio.info and getattr(audio.info, "length", None):
        metadata["duration_seconds"] = round(float(audio.info.length))

    return metadata


def parse_pubdate(raw_date: str | None, fallback_mtime: float) -> datetime:
    if raw_date:
        for fmt, length in (("%Y-%m-%d", 10), ("%Y/%m/%d", 10), ("%Y", 4)):
            try:
                parsed = datetime.strptime(raw_date[:length], fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.fromtimestamp(fallback_mtime, timezone.utc)


def build_feed_xml(config: FeedConfig, episodes: list[Episode]) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    add_text(channel, "title", config.title)
    add_text(channel, "link", absolute_url("index", config))
    add_text(channel, "description", config.description)
    add_text(channel, "language", config.language)
    add_text(channel, f"{{{ITUNES_NS}}}author", config.author)
    add_text(channel, f"{{{ITUNES_NS}}}explicit", config.explicit)
    add_text(channel, f"{{{ITUNES_NS}}}category", "", {"text": config.category})
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": absolute_url("feed", config),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    if config.image_url:
        image = ET.SubElement(channel, "image")
        add_text(image, "url", config.image_url)
        add_text(image, "title", config.title)
        add_text(image, "link", absolute_url("index", config))
        ET.SubElement(channel, f"{{{ITUNES_NS}}}image", {"href": config.image_url})

    for episode in episodes:
        item = ET.SubElement(channel, "item")
        add_text(item, "title", episode.title)
        add_text(item, "description", episode.description)
        add_text(item, f"{{{CONTENT_NS}}}encoded", episode.description)
        add_text(item, f"{{{ITUNES_NS}}}author", episode.author)
        add_text(item, f"{{{ITUNES_NS}}}summary", episode.description)
        if episode.duration_text:
            add_text(item, f"{{{ITUNES_NS}}}duration", episode.duration_text)
        if episode.album:
            add_text(item, f"{{{ITUNES_NS}}}subtitle", episode.album)

        audio_url = absolute_url("audio", config, episode_id=episode.id)
        add_text(item, "guid", audio_url, {"isPermaLink": "true"})
        add_text(item, "pubDate", format_datetime(episode.pubdate))
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": audio_url,
                "length": str(episode.size),
                "type": "audio/mpeg",
            },
        )

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def add_text(parent: ET.Element, tag: str, text: str, attrs: dict[str, str] | None = None) -> None:
    child = ET.SubElement(parent, tag, attrs or {})
    child.text = text


def absolute_url(endpoint: str, config: FeedConfig, **values: str) -> str:
    if config.base_url:
        path = url_for(endpoint, **values)
        return f"{config.base_url.rstrip('/')}{path}"
    return url_for(endpoint, _external=True, **values)


def episode_id(path: Path) -> str:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:20]


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve local MP3 files as a private podcast feed.")
    parser.add_argument(
        "--config",
        default=os.environ.get("PODCAST_CONFIG", "config.toml"),
        help="Path to TOML config file. Defaults to config.toml or PODCAST_CONFIG.",
    )
    args = parser.parse_args()

    app = create_app(args.config)
    config: FeedConfig = app.config["PODCAST_CONFIG"]
    app.run(host=config.host, port=config.port)


if __name__ == "__main__":
    main()
