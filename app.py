from __future__ import annotations

import argparse
import hashlib
import os
import re
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
class AppConfig:
    title: str
    description: str
    author: str
    language: str
    explicit: str
    image_url: str | None
    category: str
    root_directory: Path
    base_url: str | None
    host: str
    port: int


@dataclass(frozen=True)
class Podcast:
    id: str
    path: Path
    title: str
    description: str
    image_path: Path | None


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


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.toml to config.toml first."
        )

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    feed = data.get("feed", {})
    server = data.get("server", {})
    raw_root = feed.get("root_directory")

    if not raw_root:
        raise ValueError("Set feed.root_directory in config.toml.")

    root_directory = Path(str(raw_root)).expanduser().resolve()
    if not root_directory.is_dir():
        raise ValueError(f"Configured root directory does not exist: {root_directory}")

    return AppConfig(
        title=str(feed.get("title", "Local MP3 Podcasts")),
        description=str(feed.get("description", "Private podcast feeds from local MP3 folders.")),
        author=str(feed.get("author", "Local Podcast")),
        language=str(feed.get("language", "en-us")),
        explicit=str(feed.get("explicit", "false")).lower(),
        image_url=feed.get("image_url") or None,
        category=str(feed.get("category", "Music")),
        root_directory=root_directory,
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
        podcasts = scan_podcasts(config)
        podcast_links = "\n".join(
            f'  <li><a href="{absolute_url("feed", config, podcast_id=podcast.id)}">'
            f"{escape_html(podcast.title)}</a></li>"
            for podcast in podcasts
        )
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
  <ul>
{podcast_links}
  </ul>
</body>
</html>
"""
        return Response(body, mimetype="text/html")

    @app.get("/podcasts/<podcast_id>/feed.xml")
    def feed(podcast_id: str) -> Response:
        podcast = find_podcast(config, podcast_id)
        if podcast is None:
            abort(404)
        episodes = scan_episodes(config, podcast)
        xml = build_feed_xml(config, podcast, episodes)
        return Response(xml, mimetype="application/rss+xml; charset=utf-8")

    @app.get("/podcasts/<podcast_id>/audio/<episode_id>.mp3")
    def audio(podcast_id: str, episode_id: str):
        podcast = find_podcast(config, podcast_id)
        if podcast is None:
            abort(404)
        episode = find_episode(config, podcast, episode_id)
        if episode is None:
            abort(404)
        return send_file(
            episode.path,
            mimetype="audio/mpeg",
            as_attachment=False,
            conditional=True,
            download_name=episode.path.name,
        )

    @app.get("/podcasts/<podcast_id>/cover.jpg")
    def cover(podcast_id: str):
        podcast = find_podcast(config, podcast_id)
        if podcast is None or podcast.image_path is None:
            abort(404)
        return send_file(
            podcast.image_path,
            mimetype="image/jpeg",
            as_attachment=False,
            conditional=True,
            download_name=podcast.image_path.name,
        )

    return app


def scan_podcasts(config: AppConfig) -> list[Podcast]:
    podcasts = [
        Podcast(
            id=podcast_id(path),
            path=path,
            title=path.name,
            description=f"{config.description} ({path.name})",
            image_path=find_podcast_image(path),
        )
        for path in sorted(config.root_directory.iterdir(), key=lambda item: item.name.lower())
        if path.is_dir()
    ]
    return podcasts


def find_podcast(config: AppConfig, podcast_id: str) -> Podcast | None:
    for podcast in scan_podcasts(config):
        if podcast.id == podcast_id:
            return podcast
    return None


def find_podcast_image(podcast_path: Path) -> Path | None:
    jpgs = [
        path
        for path in sorted(podcast_path.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() == ".jpg"
    ]
    return jpgs[0] if jpgs else None


def scan_episodes(config: AppConfig, podcast: Podcast) -> list[Episode]:
    episodes: list[Episode] = []
    for path in sorted(podcast.path.rglob("*.mp3")):
        if path.is_file():
            episodes.append(read_episode(path, config))
    return sorted(episodes, key=lambda episode: episode.pubdate, reverse=True)


def find_episode(config: AppConfig, podcast: Podcast, episode_id: str) -> Episode | None:
    for episode in scan_episodes(config, podcast):
        if episode.id == episode_id:
            return episode
    return None


def read_episode(path: Path, config: AppConfig) -> Episode:
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


def build_feed_xml(config: AppConfig, podcast: Podcast, episodes: list[Episode]) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    add_text(channel, "title", podcast.title)
    add_text(channel, "link", absolute_url("index", config))
    add_text(channel, "description", podcast.description)
    add_text(channel, "language", config.language)
    add_text(channel, f"{{{ITUNES_NS}}}author", config.author)
    add_text(channel, f"{{{ITUNES_NS}}}explicit", config.explicit)
    add_text(channel, f"{{{ITUNES_NS}}}category", "", {"text": config.category})
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": absolute_url("feed", config, podcast_id=podcast.id),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    image_url = podcast_image_url(config, podcast)
    if image_url:
        image = ET.SubElement(channel, "image")
        add_text(image, "url", image_url)
        add_text(image, "title", podcast.title)
        add_text(image, "link", absolute_url("index", config))
        ET.SubElement(channel, f"{{{ITUNES_NS}}}image", {"href": image_url})

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

        audio_url = absolute_url("audio", config, podcast_id=podcast.id, episode_id=episode.id)
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


def absolute_url(endpoint: str, config: AppConfig, **values: str) -> str:
    if config.base_url:
        path = url_for(endpoint, **values)
        return f"{config.base_url.rstrip('/')}{path}"
    return url_for(endpoint, _external=True, **values)


def podcast_image_url(config: AppConfig, podcast: Podcast) -> str | None:
    if podcast.image_path:
        return absolute_url("cover", config, podcast_id=podcast.id)
    return config.image_url


def episode_id(path: Path) -> str:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:20]


def podcast_id(path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-") or "podcast"
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


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
    config: AppConfig = app.config["PODCAST_CONFIG"]
    app.run(host=config.host, port=config.port)


if __name__ == "__main__":
    main()
