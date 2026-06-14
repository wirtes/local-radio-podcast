from __future__ import annotations

import argparse
import hashlib
import os
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from flask import Flask, Response, abort, request, send_file, url_for
from mutagen import File as MutagenFile
from mutagen.id3 import COMM, ID3, TALB, TDAT, TDRC, TIT2, TPE1, TRCK, TYER, ID3NoHeaderError


ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
FILENAME_DATE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?:\s+(?P<title>.+))?$")
ENTRIES_PER_PAGE_OPTIONS = (10, 25, 50, 100)
DEFAULT_ENTRIES_PER_PAGE = 10
IGNORED_PODCAST_DIRECTORY_NAMES = {"__pycache__", ".venv", "venv", "env", "tests"}

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
    show_info: str | None = None


@dataclass(frozen=True)
class Episode:
    id: str
    path: Path
    title: str
    description: str
    episode_info: str | None
    author: str
    album: str | None
    duration_seconds: int | None
    pubdate: datetime
    size: int
    modified_ns: int

    @property
    def duration_text(self) -> str | None:
        if self.duration_seconds is None:
            return None
        hours, remainder = divmod(self.duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class TagTarget:
    frame_id: str
    label: str
    value: str


@dataclass(frozen=True)
class TagDiff:
    frame_id: str
    label: str
    current: str | None
    target: str

    @property
    def status(self) -> str:
        if self.current is None:
            return "ADD"
        if self.current != self.target:
            return "CHANGE"
        return "OK"


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

    @app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        if request.endpoint == "cover" and response.status_code == 200:
            return cache_artwork_response(response)
        return no_cache_response(response)

    @app.get("/")
    def index() -> Response:
        podcast_candidates = scan_podcast_candidates(config)
        per_page = parse_per_page(request.args.get("per_page"))
        page = parse_page(request.args.get("page"))
        pagination = paginate(len(podcast_candidates), page, per_page)
        visible_candidates = podcast_candidates[pagination.start_index:pagination.end_index]
        podcasts = [
            build_podcast(config, path)
            for path in visible_candidates
            if is_podcast_directory(path)
        ]
        podcast_cards = "\n".join(render_podcast_card(config, podcast) for podcast in podcasts)
        pagination_html = render_index_pagination_controls(config, pagination)
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_html(config.title)}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {{
      background: #f6f7f9;
    }}
    .podcast-card {{
      height: 100%;
    }}
    .podcast-card .card-body {{
      padding: 0.75rem;
    }}
    .podcast-card .card-title {{
      font-size: 1rem;
      line-height: 1.25;
    }}
    .podcast-cover {{
      aspect-ratio: 1 / 1;
      object-fit: cover;
      background: #e9ecef;
    }}
    .podcast-cover-placeholder {{
      aspect-ratio: 1 / 1;
      background: linear-gradient(135deg, #e9ecef, #cfd8dc);
      color: #495057;
    }}
    .feed-url {{
      font-size: 0.75rem;
    }}
    .copy-button {{
      width: 2.25rem;
      flex: 0 0 2.25rem;
      padding-left: 0.5rem;
      padding-right: 0.5rem;
    }}
    .copy-status {{
      min-height: 1rem;
    }}
    .entry-pager-form {{
      max-width: 13rem;
    }}
  </style>
</head>
<body>
  <main class="container py-4 py-md-5">
    <div class="mb-4">
      <h1 class="display-6 mb-2">{escape_html(config.title)}</h1>
      <p class="lead text-secondary mb-0">{escape_html(config.description)}</p>
    </div>
    <div class="d-flex flex-column flex-md-row align-items-md-end justify-content-between gap-3 mb-3">
      <div class="text-secondary small">{render_pagination_summary(pagination, "podcast")}</div>
      {pagination_html}
    </div>
    <div class="row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-lg-4 row-cols-xl-5 g-3">
{podcast_cards}
    </div>
    {pagination_html}
  </main>
  <script>
    function copyFeedUrl(button) {{
      const input = document.getElementById(button.dataset.target);
      if (!input) return;

      const setCopied = function() {{
        const status = document.getElementById(button.dataset.statusTarget);
        button.classList.remove("btn-outline-secondary");
        button.classList.add("btn-success");
        button.setAttribute("aria-label", "Copied");
        if (status) {{
          status.textContent = "Copied";
        }}
        setTimeout(function() {{
          button.classList.remove("btn-success");
          button.classList.add("btn-outline-secondary");
          button.setAttribute("aria-label", "Copy feed URL");
          if (status) {{
            status.textContent = "";
          }}
        }}, 1200);
      }};

      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(input.value).then(setCopied);
        return;
      }}

      input.focus();
      input.select();
      document.execCommand("copy");
      input.blur();
      setCopied();
    }}
  </script>
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

    @app.get("/podcasts/<podcast_id>/")
    def podcast_page(podcast_id: str) -> Response:
        podcast = find_podcast(config, podcast_id)
        if podcast is None:
            abort(404)
        episodes = scan_episodes(config, podcast)
        per_page = parse_per_page(request.args.get("per_page"))
        page = parse_page(request.args.get("page"))
        body = build_podcast_html(config, podcast, episodes, page=page, per_page=per_page)
        return Response(body, mimetype="text/html")

    @app.get("/podcasts/<podcast_id>/audio/<episode_id>.mp3")
    def audio(podcast_id: str, episode_id: str):
        return send_episode_audio(config, podcast_id, episode_id)

    @app.get("/podcasts/<podcast_id>/audio/<episode_id>/<download_name>")
    def audio_named(podcast_id: str, episode_id: str, download_name: str):
        return send_episode_audio(config, podcast_id, episode_id)

    @app.get("/podcasts/<podcast_id>/cover.jpg")
    def cover(podcast_id: str):
        podcast = find_podcast(config, podcast_id)
        if podcast is None or podcast.image_path is None:
            abort(404)
        return send_file(
            podcast.image_path,
            mimetype="image/jpeg",
            as_attachment=False,
            conditional=False,
            etag=False,
            max_age=0,
            download_name=podcast.image_path.name,
        )

    return app


def send_episode_audio(config: AppConfig, podcast_id: str, episode_id: str):
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
        conditional=False,
        etag=False,
        max_age=0,
        download_name=episode_download_name(episode),
    )


def no_cache_response(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Surrogate-Control"] = "no-store"
    response.headers["CDN-Cache-Control"] = "no-store"
    response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
    response.headers.pop("ETag", None)
    response.headers.pop("Last-Modified", None)
    return response


def cache_artwork_response(response: Response) -> Response:
    response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    response.headers["Surrogate-Control"] = "max-age=604800"
    response.headers["CDN-Cache-Control"] = "public, max-age=604800"
    response.headers["Cloudflare-CDN-Cache-Control"] = "public, max-age=604800"
    response.headers.pop("Pragma", None)
    response.headers.pop("Expires", None)
    return response


def render_podcast_card(config: AppConfig, podcast: Podcast) -> str:
    feed_url = absolute_url("feed", config, podcast_id=podcast.id)
    page_url = absolute_url("podcast_page", config, podcast_id=podcast.id)
    image_url = podcast_image_url(config, podcast)
    input_id = f"feed-url-{podcast.id}"
    status_id = f"copy-status-{podcast.id}"
    title = escape_html(podcast.title)
    escaped_feed_url = escape_html(feed_url)
    escaped_page_url = escape_html(page_url)

    if image_url:
        cover_html = (
            f'<a class="d-block" href="{escaped_page_url}" aria-label="Open {title}">'
            f'<img src="{escape_html(image_url)}" class="card-img-top podcast-cover" '
            f'alt="{title} cover"></a>'
        )
    else:
        cover_html = f"""<a class="d-block text-decoration-none" href="{escaped_page_url}" aria-label="Open {title}">
        <div class="card-img-top podcast-cover-placeholder d-flex align-items-center justify-content-center">
          <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M9 18V5l12-2v13"></path>
            <circle cx="6" cy="18" r="3"></circle>
            <circle cx="18" cy="16" r="3"></circle>
          </svg>
        </div>
        </a>"""

    return f"""      <div class="col">
        <div class="card podcast-card shadow-sm">
          {cover_html}
          <div class="card-body">
            <h2 class="h6 card-title mb-2"><a class="link-dark text-decoration-none" href="{escaped_page_url}">{title}</a></h2>
            <label class="visually-hidden" for="{input_id}">Feed URL</label>
            <div class="input-group">
              <input id="{input_id}" class="form-control feed-url" type="text" readonly value="{escaped_feed_url}">
              <button class="btn btn-outline-secondary copy-button" type="button" data-target="{input_id}" data-status-target="{status_id}" onclick="copyFeedUrl(this)" aria-label="Copy feed URL" title="Copy feed URL">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect>
                  <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>
                </svg>
              </button>
            </div>
            <div id="{status_id}" class="copy-status small text-success mt-1" aria-live="polite"></div>
          </div>
        </div>
      </div>"""


@dataclass(frozen=True)
class Pagination:
    page: int
    per_page: int
    total_items: int
    total_pages: int
    start_index: int
    end_index: int


def build_podcast_html(
    config: AppConfig,
    podcast: Podcast,
    episodes: list[Episode],
    *,
    page: int = 1,
    per_page: int = DEFAULT_ENTRIES_PER_PAGE,
) -> str:
    title = escape_html(podcast.title)
    description = escape_html(podcast.description)
    feed_url = absolute_url("feed", config, podcast_id=podcast.id)
    image_url = podcast_image_url(config, podcast)
    pagination = paginate(len(episodes), page, per_page)
    visible_episodes = episodes[pagination.start_index:pagination.end_index]
    episode_cards = "\n".join(render_episode_card(config, podcast, episode) for episode in visible_episodes)
    pagination_html = render_pagination_controls(config, podcast, pagination)
    show_info_html = render_show_info(podcast)

    if image_url:
        cover_html = (
            f'<img src="{escape_html(image_url)}" class="podcast-page-cover rounded shadow-sm" '
            f'alt="{title} cover">'
        )
    else:
        cover_html = """<div class="podcast-page-cover rounded shadow-sm d-flex align-items-center justify-content-center bg-secondary-subtle text-secondary">
          <svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M9 18V5l12-2v13"></path>
            <circle cx="6" cy="18" r="3"></circle>
            <circle cx="18" cy="16" r="3"></circle>
          </svg>
        </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {{
      background: #f6f7f9;
    }}
    .podcast-page-cover {{
      width: min(100%, 260px);
      aspect-ratio: 1 / 1;
      object-fit: cover;
    }}
    .episode-card {{
      border-radius: 0.5rem;
    }}
    audio {{
      width: 100%;
    }}
    .xml-data {{
      font-size: 0.875rem;
    }}
    .show-info-body {{
      white-space: pre-wrap;
    }}
    .episode-info-body {{
      white-space: pre-wrap;
    }}
    .entry-pager-form {{
      max-width: 13rem;
    }}
  </style>
</head>
<body>
  <main class="container py-4 py-md-5">
    <nav class="mb-4">
      <a class="link-secondary text-decoration-none" href="{escape_html(absolute_url("index", config))}">&larr; All podcasts</a>
    </nav>
    <section class="row g-4 align-items-start mb-5">
      <div class="col-12 col-md-auto">
        {cover_html}
      </div>
      <div class="col">
        <h1 class="display-6 mb-2">{title}</h1>
        <p class="lead text-secondary">{description}</p>
        {show_info_html}
        <dl class="row xml-data">
          <dt class="col-sm-3">Feed</dt>
          <dd class="col-sm-9"><a href="{escape_html(feed_url)}">{escape_html(feed_url)}</a></dd>
          <dt class="col-sm-3">Language</dt>
          <dd class="col-sm-9">{escape_html(config.language)}</dd>
          <dt class="col-sm-3">Author</dt>
          <dd class="col-sm-9">{escape_html(config.author)}</dd>
          <dt class="col-sm-3">Episodes</dt>
          <dd class="col-sm-9">{len(episodes)}</dd>
        </dl>
      </div>
    </section>
    <section>
      <div class="d-flex flex-column flex-md-row align-items-md-end justify-content-between gap-3 mb-3">
        <div>
          <h2 class="h4 mb-1">Episodes</h2>
          <div class="text-secondary small">{render_pagination_summary(pagination, "episode")}</div>
        </div>
        {pagination_html}
      </div>
      <div class="vstack gap-3">
{episode_cards}
      </div>
      {pagination_html}
    </section>
  </main>
</body>
</html>
"""


def parse_per_page(raw_value: str | None) -> int:
    try:
        value = int(raw_value or "")
    except ValueError:
        return DEFAULT_ENTRIES_PER_PAGE
    if value in ENTRIES_PER_PAGE_OPTIONS:
        return value
    return DEFAULT_ENTRIES_PER_PAGE


def parse_page(raw_value: str | None) -> int:
    try:
        value = int(raw_value or "")
    except ValueError:
        return 1
    return max(value, 1)


def paginate(total_items: int, page: int, per_page: int) -> Pagination:
    total_pages = max((total_items + per_page - 1) // per_page, 1)
    current_page = min(max(page, 1), total_pages)
    start_index = (current_page - 1) * per_page
    end_index = min(start_index + per_page, total_items)
    return Pagination(
        page=current_page,
        per_page=per_page,
        total_items=total_items,
        total_pages=total_pages,
        start_index=start_index,
        end_index=end_index,
    )


def render_pagination_summary(pagination: Pagination, item_label: str) -> str:
    if pagination.total_items == 0:
        return f"No {item_label}s"
    start = pagination.start_index + 1
    plural_label = item_label if pagination.total_items == 1 else f"{item_label}s"
    return (
        f"Showing {start}-{pagination.end_index} of {pagination.total_items} "
        f"{plural_label}"
    )


def render_pagination_controls(config: AppConfig, podcast: Podcast, pagination: Pagination) -> str:
    selected_options = "\n".join(
        f'<option value="{option}"{" selected" if option == pagination.per_page else ""}>{option}</option>'
        for option in ENTRIES_PER_PAGE_OPTIONS
    )
    previous_disabled = " disabled" if pagination.page <= 1 else ""
    next_disabled = " disabled" if pagination.page >= pagination.total_pages else ""
    previous_url = escape_html(podcast_page_url(config, podcast, pagination.page - 1, pagination.per_page))
    next_url = escape_html(podcast_page_url(config, podcast, pagination.page + 1, pagination.per_page))

    return f"""<div class="d-flex flex-column flex-sm-row align-items-sm-center gap-2">
          <form class="entry-pager-form" method="get">
            <input type="hidden" name="page" value="1">
            <label class="form-label small text-secondary mb-1">Entries per page</label>
            <select class="form-select form-select-sm" name="per_page" aria-label="Entries per page" onchange="this.form.submit()">
              {selected_options}
            </select>
            <noscript><button class="btn btn-sm btn-outline-secondary mt-2" type="submit">Apply</button></noscript>
          </form>
          <nav aria-label="Episode pages">
            <ul class="pagination pagination-sm mb-0">
              <li class="page-item{previous_disabled}"><a class="page-link" href="{previous_url}" aria-label="Previous page">Previous</a></li>
              <li class="page-item disabled"><span class="page-link">Page {pagination.page} of {pagination.total_pages}</span></li>
              <li class="page-item{next_disabled}"><a class="page-link" href="{next_url}" aria-label="Next page">Next</a></li>
            </ul>
          </nav>
        </div>"""


def render_index_pagination_controls(config: AppConfig, pagination: Pagination) -> str:
    selected_options = "\n".join(
        f'<option value="{option}"{" selected" if option == pagination.per_page else ""}>{option}</option>'
        for option in ENTRIES_PER_PAGE_OPTIONS
    )
    previous_disabled = " disabled" if pagination.page <= 1 else ""
    next_disabled = " disabled" if pagination.page >= pagination.total_pages else ""
    previous_url = escape_html(index_page_url(config, pagination.page - 1, pagination.per_page))
    next_url = escape_html(index_page_url(config, pagination.page + 1, pagination.per_page))

    return f"""<div class="d-flex flex-column flex-sm-row align-items-sm-center gap-2">
          <form class="entry-pager-form" method="get">
            <input type="hidden" name="page" value="1">
            <label class="form-label small text-secondary mb-1">Entries per page</label>
            <select class="form-select form-select-sm" name="per_page" aria-label="Entries per page" onchange="this.form.submit()">
              {selected_options}
            </select>
            <noscript><button class="btn btn-sm btn-outline-secondary mt-2" type="submit">Apply</button></noscript>
          </form>
          <nav aria-label="Podcast pages">
            <ul class="pagination pagination-sm mb-0">
              <li class="page-item{previous_disabled}"><a class="page-link" href="{previous_url}" aria-label="Previous page">Previous</a></li>
              <li class="page-item disabled"><span class="page-link">Page {pagination.page} of {pagination.total_pages}</span></li>
              <li class="page-item{next_disabled}"><a class="page-link" href="{next_url}" aria-label="Next page">Next</a></li>
            </ul>
          </nav>
        </div>"""


def index_page_url(config: AppConfig, page: int, per_page: int) -> str:
    return absolute_url(
        "index",
        config,
        page=str(max(page, 1)),
        per_page=str(per_page),
    )


def podcast_page_url(config: AppConfig, podcast: Podcast, page: int, per_page: int) -> str:
    return absolute_url(
        "podcast_page",
        config,
        podcast_id=podcast.id,
        page=str(max(page, 1)),
        per_page=str(per_page),
    )


def render_show_info(podcast: Podcast) -> str:
    if not podcast.show_info:
        return ""

    return f"""<details class="show-info mb-4">
          <summary class="link-secondary">Show information</summary>
          <div class="show-info-body text-secondary mt-2">{escape_html(podcast.show_info)}</div>
        </details>"""


def render_episode_card(config: AppConfig, podcast: Podcast, episode: Episode) -> str:
    audio_url = episode_audio_url(config, podcast, episode)
    pubdate = format_datetime(episode.pubdate)
    duration = episode.duration_text or "Unknown"
    album = episode.album or podcast.title
    description_html = "" if episode.episode_info else render_episode_description(episode)
    episode_info_html = render_episode_info(episode)
    return f"""        <article class="card episode-card shadow-sm">
          <div class="card-body">
            <div class="d-flex flex-column flex-lg-row justify-content-between gap-2 mb-2">
              <h3 class="h5 mb-0">{escape_html(episode.title)}</h3>
              <time class="text-secondary small" datetime="{episode.pubdate.isoformat()}">{escape_html(pubdate)}</time>
            </div>
            {description_html}
            <audio controls preload="none" src="{escape_html(audio_url)}"></audio>
            <dl class="row xml-data mt-3 mb-0">
              <dt class="col-sm-2">GUID</dt>
              <dd class="col-sm-10 text-break">{escape_html(episode_guid(podcast, episode))}</dd>
              <dt class="col-sm-2">Enclosure</dt>
              <dd class="col-sm-10 text-break"><a href="{escape_html(audio_url)}">{escape_html(audio_url)}</a></dd>
              <dt class="col-sm-2">Duration</dt>
              <dd class="col-sm-10">{escape_html(duration)}</dd>
              <dt class="col-sm-2">Album</dt>
              <dd class="col-sm-10">{escape_html(album)}</dd>
            </dl>
            {episode_info_html}
          </div>
        </article>"""


def render_episode_description(episode: Episode) -> str:
    return f'<p class="text-secondary mb-3">{escape_html(episode.description)}</p>'


def render_episode_info(episode: Episode) -> str:
    if not episode.episode_info:
        return ""

    line_count = len(episode.episode_info.splitlines())
    if line_count > 5:
        return f"""<details class="episode-info mt-3">
              <summary class="link-secondary">Playlist</summary>
              <div class="episode-info-body text-secondary mt-2">{escape_html(episode.episode_info)}</div>
            </details>"""

    return f'<div class="episode-info-body text-secondary mt-3">{escape_html(episode.episode_info)}</div>'


def scan_podcasts(config: AppConfig) -> list[Podcast]:
    podcasts = [
        build_podcast(config, path)
        for path in scan_podcast_candidates(config)
        if is_podcast_directory(path)
    ]
    return podcasts


def scan_podcast_candidates(config: AppConfig) -> list[Path]:
    try:
        children = sorted(config.root_directory.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []
    return [path for path in children if is_visible_podcast_candidate(path)]


def build_podcast(config: AppConfig, path: Path) -> Podcast:
    return Podcast(
        id=podcast_id(path),
        path=path,
        title=path.name,
        description=f"{config.description} ({path.name})",
        image_path=find_podcast_image(path),
        show_info=find_podcast_show_info(config, path),
    )


def find_podcast_show_info(config: AppConfig, podcast_path: Path) -> str | None:
    candidates = [
        podcast_path / f"{podcast_path.name}.txt",
        config.root_directory / f"{podcast_path.name}.txt",
    ]
    for path in candidates:
        text = read_text_file_if_larger_than_10_bytes(path)
        if text:
            return text
    return None


def find_episode_info(path: Path) -> str | None:
    return read_text_file_if_larger_than_10_bytes(path.with_suffix(".txt"))


def read_text_file_if_larger_than_10_bytes(path: Path) -> str | None:
    try:
        if path.is_file() and path.stat().st_size > 10:
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return None


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


def is_podcast_directory(path: Path) -> bool:
    if not is_visible_podcast_candidate(path):
        return False
    return any(find_mp3_files(path))


def is_visible_podcast_candidate(path: Path) -> bool:
    if not path.is_dir():
        return False
    if path.name.startswith(".") or path.name in IGNORED_PODCAST_DIRECTORY_NAMES:
        return False
    return True


def scan_episodes(config: AppConfig, podcast: Podcast) -> list[Episode]:
    episodes: list[Episode] = []
    for path in find_mp3_files(podcast.path):
        episodes.append(read_episode(path, config))
    return sorted(episodes, key=lambda episode: episode.pubdate, reverse=True)


def find_mp3_files(root: Path) -> list[Path]:
    results: list[Path] = []
    seen_dirs: set[Path] = set()

    def walk(directory: Path) -> None:
        try:
            resolved = directory.resolve()
        except OSError:
            return

        if resolved in seen_dirs:
            return
        seen_dirs.add(resolved)

        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            return

        for child in children:
            if child.is_dir():
                walk(child)
            elif child.is_file() and child.suffix.lower() == ".mp3":
                results.append(child)

    walk(root)
    return sorted(results, key=lambda path: str(path).lower())


def find_episode(config: AppConfig, podcast: Podcast, episode_id: str) -> Episode | None:
    for episode in scan_episodes(config, podcast):
        if episode.id == episode_id:
            return episode
    return None


def read_episode(path: Path, config: AppConfig) -> Episode:
    stat = path.stat()
    metadata = read_audio_metadata(path)
    filename_metadata = read_filename_metadata(path)
    metadata_title = metadata.get("title")
    if metadata_title == path.stem and filename_metadata.get("title"):
        metadata_title = None

    title = filename_metadata.get("title") or metadata_title or path.stem
    author = metadata.get("artist") or metadata.get("albumartist") or config.author
    episode_info = find_episode_info(path)
    description = episode_info or filename_metadata.get("title") or metadata.get("comment") or metadata.get("description") or title
    pubdate = parse_pubdate(filename_metadata.get("date") or metadata.get("date"), stat.st_mtime)
    duration_seconds = metadata.get("duration_seconds")

    return Episode(
        id=episode_id(path),
        path=path,
        title=title,
        description=description,
        episode_info=episode_info,
        author=author,
        album=metadata.get("album"),
        duration_seconds=duration_seconds,
        pubdate=pubdate,
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
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


def read_filename_metadata(path: Path) -> dict[str, str]:
    match = FILENAME_DATE_RE.match(path.stem)
    if not match:
        return {}

    metadata = {"date": match.group("date")}
    metadata["title"] = clean_filename_title(path.stem)
    return metadata


def clean_filename_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.replace("_", " ")).strip()


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
    show_description = podcast.show_info or podcast.description

    add_text(channel, "title", podcast.title)
    add_text(channel, "link", absolute_url("index", config))
    add_text(channel, "description", show_description)
    add_text(channel, "language", config.language)
    add_text(channel, f"{{{ITUNES_NS}}}author", config.author)
    add_text(channel, f"{{{ITUNES_NS}}}summary", show_description)
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
        add_text(item, f"{{{ITUNES_NS}}}title", episode.title)
        add_text(item, f"{{{ITUNES_NS}}}summary", episode.description)
        add_text(item, f"{{{ITUNES_NS}}}episodeType", "full")
        if episode.duration_text:
            add_text(item, f"{{{ITUNES_NS}}}duration", episode.duration_text)
        if episode.album:
            add_text(item, f"{{{ITUNES_NS}}}subtitle", episode.album)

        audio_url = episode_audio_url(config, podcast, episode)
        add_text(item, "link", audio_url)
        add_text(item, "guid", episode_guid(podcast, episode), {"isPermaLink": "false"})
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


def episode_audio_url(config: AppConfig, podcast: Podcast, episode: Episode) -> str:
    return absolute_url(
        "audio_named",
        config,
        podcast_id=podcast.id,
        episode_id=episode.id,
        download_name=episode_download_name(episode),
        v=str(episode.modified_ns),
    )


def diagnose_podcast(config: AppConfig, podcast_filter: str | None, limit: int = 10) -> list[str]:
    if not podcast_filter:
        lines = ["Specify --podcast to diagnose one show. Available podcasts:"]
        lines.extend(f"  {podcast.title} ({podcast.id})" for podcast in scan_podcasts(config))
        return lines

    podcasts = filter_podcasts(scan_podcasts(config), podcast_filter)
    if not podcasts:
        return [f"No podcast matched: {podcast_filter}"]

    lines: list[str] = []
    for podcast in podcasts:
        episodes = scan_episodes(config, podcast)
        lines.extend(
            [
                f"Podcast: {podcast.title}",
                f"ID: {podcast.id}",
                f"Path: {podcast.path}",
                f"Episodes: {len(episodes)}",
            ]
        )
        lines.extend(report_duplicates("feed titles", [episode.title for episode in episodes]))
        lines.extend(report_duplicates("feed GUIDs", [episode_guid(podcast, episode) for episode in episodes]))
        lines.extend(
            report_duplicates(
                "enclosure URLs",
                [episode_audio_url(config, podcast, episode) for episode in episodes],
            )
        )

        for episode in episodes[:limit]:
            lines.extend(
                [
                    "",
                    f"Episode: {episode.title}",
                    f"  File: {episode.path}",
                    f"  PubDate: {format_datetime(episode.pubdate)}",
                    f"  GUID: {episode_guid(podcast, episode)}",
                    f"  URL: {episode_audio_url(config, podcast, episode)}",
                ]
            )
            filename_metadata = read_filename_metadata(episode.path)
            if not filename_metadata.get("date"):
                lines.append("  Filename date: MISSING - repair-tags would skip this file")
                continue

            lines.append(f"  Filename date: {filename_metadata['date']}")
            targets = build_tag_targets(podcast, episode.path, filename_metadata)
            for diff in diff_id3_tags(episode.path, targets):
                if diff.status == "CHANGE":
                    lines.append(
                        f"  TAG CHANGE {diff.label} ({diff.frame_id}): {diff.current} -> {diff.target}"
                    )
                elif diff.status == "ADD":
                    lines.append(f"  TAG ADD    {diff.label} ({diff.frame_id}): {diff.target}")
                else:
                    lines.append(f"  TAG OK     {diff.label} ({diff.frame_id}): {diff.target}")
    return lines


def report_duplicates(label: str, values: list[str]) -> list[str]:
    duplicates = [(value, count) for value, count in Counter(values).items() if count > 1]
    if not duplicates:
        return [f"{label}: no duplicates"]
    lines = [f"{label}: {len(duplicates)} duplicate value(s)"]
    lines.extend(f"  {count}x {value}" for value, count in duplicates)
    return lines


def repair_mp3_tags(config: AppConfig, write: bool, podcast_filter: str | None = None) -> list[str]:
    results: list[str] = []
    podcasts = filter_podcasts(scan_podcasts(config), podcast_filter)
    if podcast_filter and not podcasts:
        return [f"No podcast matched: {podcast_filter}"]

    for podcast in podcasts:
        for path in find_mp3_files(podcast.path):
            filename_metadata = read_filename_metadata(path)
            if not filename_metadata.get("date"):
                results.append(f"SKIP no filename date: {path}")
                continue

            targets = build_tag_targets(podcast, path, filename_metadata)
            diffs = diff_id3_tags(path, targets)
            results.append(f"{'WRITE' if write else 'DRY'} {path}")
            for diff in diffs:
                if diff.status == "ADD":
                    results.append(f"  ADD    {diff.label} ({diff.frame_id}): {diff.target}")
                elif diff.status == "CHANGE":
                    results.append(
                        f"  CHANGE {diff.label} ({diff.frame_id}): {diff.current} -> {diff.target}"
                    )
                else:
                    results.append(f"  OK     {diff.label} ({diff.frame_id}): {diff.target}")

            if write:
                write_id3_tags(path, targets)
    return results


def filter_podcasts(podcasts: list[Podcast], podcast_filter: str | None) -> list[Podcast]:
    if not podcast_filter:
        return podcasts

    normalized_filter = normalize_podcast_filter(podcast_filter)
    return [
        podcast
        for podcast in podcasts
        if podcast.id == podcast_filter
        or podcast.title == podcast_filter
        or normalize_podcast_filter(podcast.title) == normalized_filter
    ]


def normalize_podcast_filter(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_tag_targets(podcast: Podcast, path: Path, filename_metadata: dict[str, str]) -> list[TagTarget]:
    title = filename_metadata["title"]
    date = filename_metadata["date"]
    album = path.parent.name if path.parent != podcast.path else podcast.title
    artist = podcast.title
    return [
        TagTarget("TIT2", "Title", title),
        TagTarget("TPE1", "Artist", artist),
        TagTarget("TALB", "Album", album),
        TagTarget("TDRC", "Date", date),
        TagTarget("TYER", "Year", date[:4]),
        TagTarget("TDAT", "DayMonth", f"{date[8:10]}{date[5:7]}"),
        TagTarget("TRCK", "Track", date.replace("-", "")),
        TagTarget("COMM", "Comment", title),
    ]


def diff_id3_tags(path: Path, targets: list[TagTarget]) -> list[TagDiff]:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    return [
        TagDiff(
            frame_id=target.frame_id,
            label=target.label,
            current=read_id3_value(tags, target.frame_id),
            target=target.value,
        )
        for target in targets
    ]


def read_id3_value(tags: ID3, frame_id: str) -> str | None:
    if frame_id == "COMM":
        comments = tags.getall("COMM")
        return str(comments[0].text[0]) if comments and comments[0].text else None

    frame = tags.get(frame_id)
    if frame is None or not getattr(frame, "text", None):
        return None
    return str(frame.text[0])


def write_id3_tags(path: Path, targets: list[TagTarget]) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TIT2")
    tags.delall("TPE1")
    tags.delall("TALB")
    tags.delall("TDRC")
    tags.delall("TYER")
    tags.delall("TDAT")
    tags.delall("TRCK")
    tags.delall("COMM")

    target_map = {target.frame_id: target.value for target in targets}
    tags.add(TIT2(encoding=3, text=target_map["TIT2"]))
    tags.add(TPE1(encoding=3, text=target_map["TPE1"]))
    tags.add(TALB(encoding=3, text=target_map["TALB"]))
    tags.add(TDRC(encoding=3, text=target_map["TDRC"]))
    tags.add(TYER(encoding=3, text=target_map["TYER"]))
    tags.add(TDAT(encoding=3, text=target_map["TDAT"]))
    tags.add(TRCK(encoding=3, text=target_map["TRCK"]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=target_map["COMM"]))
    tags.save(path, v2_version=3)


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
        return absolute_url(
            "cover",
            config,
            podcast_id=podcast.id,
            v=str(podcast.image_path.stat().st_mtime_ns),
        )
    return config.image_url


def episode_id(path: Path) -> str:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:20]


def episode_guid(podcast: Podcast, episode: Episode) -> str:
    return f"local-radio-podcast:{podcast.id}:{episode.id}"


def episode_download_name(episode: Episode) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", episode.title).strip("-") or episode.id
    if not stem.lower().endswith(".mp3"):
        stem = f"{stem}.mp3"
    return stem


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
        "command",
        nargs="?",
        choices=("serve", "repair-tags", "diagnose"),
        default="serve",
        help="Use repair-tags to write ID3 tags, or diagnose to inspect one podcast.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("PODCAST_CONFIG", "config.toml"),
        help="Path to TOML config file. Defaults to config.toml or PODCAST_CONFIG.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually modify MP3 files when using repair-tags. Without this, repair-tags is a dry run.",
    )
    parser.add_argument(
        "--podcast",
        help="Only repair one podcast, matched by exact title, slug title, or podcast ID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of episodes to show with diagnose. Defaults to 10.",
    )
    args = parser.parse_args()

    if args.command == "repair-tags":
        config = load_config(Path(args.config).resolve())
        for line in repair_mp3_tags(config, write=args.write, podcast_filter=args.podcast):
            print(line)
        if not args.write:
            print("Dry run only. Re-run with --write to update MP3 ID3 tags.")
        return

    if args.command == "diagnose":
        app = create_app(args.config)
        with app.test_request_context():
            config: AppConfig = app.config["PODCAST_CONFIG"]
            for line in diagnose_podcast(config, podcast_filter=args.podcast, limit=args.limit):
                print(line)
        return

    app = create_app(args.config)
    config: AppConfig = app.config["PODCAST_CONFIG"]
    app.run(host=config.host, port=config.port)


if __name__ == "__main__":
    main()
