from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1

from app import (
    Podcast,
    TagTarget,
    build_tag_targets,
    create_app,
    diagnose_podcast,
    diff_id3_tags,
    load_config,
    repair_mp3_tags,
    write_id3_tags,
)


class FakeInfo:
    length = 3723.4


class FakeAudio:
    info = FakeInfo()
    tags = {
        "title": ["The First Track"],
        "artist": ["Station Host"],
        "album": ["Morning Show"],
        "date": ["2026-06-01"],
        "comment": ["A locally hosted episode."],
    }


class FakeAudioWithoutTitle:
    info = FakeInfo()
    tags = {
        "artist": ["Station Host"],
        "date": ["2001-01-01"],
    }


class FakeAudioWithFilenameTitle:
    info = FakeInfo()
    tags = {
        "title": ["2026-03-11 Modern Jetset"],
        "artist": ["Station Host"],
        "date": ["2001-01-01"],
        "comment": ["2026-03-11 Modern Jetset"],
    }


class FakeAudioWithStaleTitle:
    info = FakeInfo()
    tags = {
        "title": ["2026-06-06 Singing to the Same Sky"],
        "artist": ["Station Host"],
        "date": ["2026-06-06"],
        "comment": ["2026-06-06 Singing to the Same Sky"],
    }


class PodcastServerTest(unittest.TestCase):
    def test_feed_and_audio_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            audio_dir = library_dir / "Kitchen Radio"
            other_dir = library_dir / "Evening News"
            audio_dir.mkdir(parents=True)
            other_dir.mkdir()
            (library_dir / ".git").mkdir()
            (library_dir / ".venv").mkdir()
            (library_dir / "__pycache__").mkdir()
            (library_dir / "tests").mkdir()
            mp3 = audio_dir / "episode.mp3"
            mp3.write_bytes(b"not a real mp3, but enough for send_file")
            cover = audio_dir / "01-cover.jpg"
            cover.write_bytes(b"first cover")
            (audio_dir / "z-cover.jpg").write_bytes(b"second cover")
            nested_dir = audio_dir / "nested"
            nested_dir.mkdir()
            (nested_dir / "00-nested.jpg").write_bytes(b"not the show cover")
            (other_dir / "briefing.mp3").write_bytes(b"another fake mp3")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            with patch("app.MutagenFile", return_value=FakeAudio()):
                flask_app = create_app(config)
                client = flask_app.test_client()

                index_response = client.get("/")
                self.assertEqual(index_response.status_code, 200)
                self._assert_uncached(index_response)
                self.assertIn(b"Kitchen Radio", index_response.data)
                self.assertIn(b"Evening News", index_response.data)
                self.assertIn(b"podcast-card", index_response.data)
                self.assertIn(b"card-img-top podcast-cover", index_response.data)
                self.assertIn(b"feed-url-", index_response.data)
                self.assertIn(b"copy-status-", index_response.data)
                self.assertIn(b"data-status-target", index_response.data)
                self.assertIn(b"copyFeedUrl", index_response.data)
                self.assertIn(b"Copy feed URL", index_response.data)
                self.assertNotIn(b".git", index_response.data)
                self.assertNotIn(b".venv", index_response.data)
                self.assertNotIn(b"__pycache__", index_response.data)
                self.assertNotIn(b"tests", index_response.data)

                index_html = index_response.data.decode()
                detail_path = self._first_link_for(index_html, "Kitchen Radio")
                detail_response = client.get(detail_path)
                self.assertEqual(detail_response.status_code, 200)
                self._assert_uncached(detail_response)
                self.assertIn(b"All podcasts", detail_response.data)
                self.assertIn(b"Episodes", detail_response.data)
                self.assertIn(b"<audio controls", detail_response.data)
                self.assertIn(b"GUID", detail_response.data)
                self.assertIn(b"Enclosure", detail_response.data)

                feed_path = self._feed_input_path_for(index_html, "Kitchen Radio")
                feed_response = client.get(feed_path)
                self.assertEqual(feed_response.status_code, 200)
                self._assert_uncached(feed_response)

                rss = ET.fromstring(feed_response.data)
                channel = rss.find("./channel")
                self.assertIsNotNone(channel)
                image = channel.find("image")
                self.assertIsNotNone(image)
                self.assertIn("/cover.jpg?v=", image.findtext("url"))
                itunes_image = channel.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}image")
                self.assertIsNotNone(itunes_image)
                self.assertIn("/cover.jpg?v=", itunes_image.attrib["href"])

                item = rss.find("./channel/item")
                self.assertIsNotNone(item)
                self.assertEqual(item.findtext("title"), "The First Track")
                self.assertEqual(item.findtext("description"), "A locally hosted episode.")

                enclosure = item.find("enclosure")
                self.assertIsNotNone(enclosure)
                self.assertEqual(enclosure.attrib["type"], "audio/mpeg")
                self.assertIn("?v=", enclosure.attrib["url"])
                self.assertIn("/The-First-Track.mp3?v=", enclosure.attrib["url"])
                self.assertEqual(item.findtext("link"), enclosure.attrib["url"])
                guid = item.find("guid")
                self.assertIsNotNone(guid)
                self.assertEqual(guid.attrib["isPermaLink"], "false")
                self.assertTrue(guid.text.startswith("local-radio-podcast:"))

                audio_response = client.get(enclosure.attrib["url"].replace("http://127.0.0.1:8000", ""))
                self.assertEqual(audio_response.status_code, 200)
                self._assert_uncached(audio_response)
                self.assertEqual(audio_response.data, mp3.read_bytes())
                audio_response.close()

                cover_path = image.findtext("url").replace("http://127.0.0.1:8000", "")
                cover_response = client.get(cover_path)
                self.assertEqual(cover_response.status_code, 200)
                self._assert_cached_artwork(cover_response)
                self.assertEqual(cover_response.data, cover.read_bytes())
                cover_response.close()

    def test_filename_date_sets_pubdate_and_sort_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            audio_dir = library_dir / "Radio Rips"
            year_dir = audio_dir / "Radio Rips 2026"
            year_dir.mkdir(parents=True)
            older = year_dir / "2026-03-04 Modern Jetset.MP3"
            newer = year_dir / "2026-03-11 Modern Jetset.mp3"
            older.write_bytes(b"older")
            newer.write_bytes(b"newer")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            with patch("app.MutagenFile", return_value=FakeAudioWithoutTitle()):
                flask_app = create_app(config)
                client = flask_app.test_client()
                index_response = client.get("/")
                feed_path = self._feed_input_path_for(index_response.data.decode(), "Radio Rips")

                feed_response = client.get(feed_path)
                self.assertEqual(feed_response.status_code, 200)

                rss = ET.fromstring(feed_response.data)
                items = rss.findall("./channel/item")
                self.assertEqual(len(items), 2)
                self.assertEqual(items[0].findtext("title"), "2026-03-11 Modern Jetset")
                self.assertEqual(
                    items[0].findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}title"),
                    "2026-03-11 Modern Jetset",
                )
                self.assertIn("11 Mar 2026", items[0].findtext("pubDate"))
                self.assertIn("04 Mar 2026", items[1].findtext("pubDate"))

    def test_filename_title_wins_when_mp3_title_is_filename_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            audio_dir = library_dir / "Radio Rips"
            audio_dir.mkdir(parents=True)
            mp3 = audio_dir / "2026-03-11 Modern Jetset.mp3"
            mp3.write_bytes(b"audio")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            with patch("app.MutagenFile", return_value=FakeAudioWithFilenameTitle()):
                flask_app = create_app(config)
                client = flask_app.test_client()
                index_response = client.get("/")
                feed_path = self._feed_input_path_for(index_response.data.decode(), "Radio Rips")

                feed_response = client.get(feed_path)
                rss = ET.fromstring(feed_response.data)
                item = rss.find("./channel/item")
                self.assertIsNotNone(item)
                self.assertEqual(item.findtext("title"), "2026-03-11 Modern Jetset")
                self.assertEqual(item.findtext("description"), "2026-03-11 Modern Jetset")

    def test_filename_title_wins_over_stale_mp3_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            audio_dir = library_dir / "Radio Rips"
            audio_dir.mkdir(parents=True)
            mp3 = audio_dir / "2026-03-11 Modern Jetset.mp3"
            mp3.write_bytes(b"audio")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            with patch("app.MutagenFile", return_value=FakeAudioWithStaleTitle()):
                flask_app = create_app(config)
                client = flask_app.test_client()
                index_response = client.get("/")
                feed_path = self._feed_input_path_for(index_response.data.decode(), "Radio Rips")

                feed_response = client.get(feed_path)
                rss = ET.fromstring(feed_response.data)
                item = rss.find("./channel/item")
                self.assertIsNotNone(item)
                self.assertEqual(item.findtext("title"), "2026-03-11 Modern Jetset")
                self.assertEqual(item.findtext("description"), "2026-03-11 Modern Jetset")

    def test_write_id3_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mp3 = Path(temp_dir) / "2026-03-11 Modern Jetset.mp3"
            mp3.write_bytes(b"audio")
            targets = [
                TagTarget("TIT2", "Title", "2026-03-11 Modern Jetset"),
                TagTarget("TPE1", "Artist", "Modern Jetset"),
                TagTarget("TALB", "Album", "Modern Jetset 2026"),
                TagTarget("TDRC", "Date", "2026-03-11"),
                TagTarget("TYER", "Year", "2026"),
                TagTarget("TDAT", "DayMonth", "1103"),
                TagTarget("TRCK", "Track", "20260311"),
                TagTarget("COMM", "Comment", "2026-03-11 Modern Jetset"),
            ]

            write_id3_tags(mp3, targets)

            tags = ID3(mp3)
            self.assertEqual(tags["TIT2"].text[0], "2026-03-11 Modern Jetset")
            self.assertEqual(tags["TPE1"].text[0], "Modern Jetset")
            self.assertEqual(tags["TALB"].text[0], "Modern Jetset 2026")
            self.assertEqual(str(tags["TDRC"].text[0]), "2026-03-11")
            self.assertEqual(str(tags["TRCK"].text[0]), "20260311")

    def test_diff_id3_tags_reports_added_and_changed_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            podcast_dir = root / "Modern Jetset"
            year_dir = podcast_dir / "Modern Jetset 2026"
            year_dir.mkdir(parents=True)
            mp3 = year_dir / "2026-03-11 Modern Jetset.mp3"
            mp3.write_bytes(b"audio")
            podcast = Podcast(
                id="modern-jetset",
                path=podcast_dir,
                title="Modern Jetset",
                description="desc",
                image_path=None,
            )
            targets = build_tag_targets(
                podcast,
                mp3,
                {"title": "2026-03-11 Modern Jetset", "date": "2026-03-11"},
            )

            add_diffs = diff_id3_tags(mp3, targets)
            self.assertEqual([diff.status for diff in add_diffs], ["ADD"] * 8)

            tags = ID3()
            tags.add(TIT2(encoding=3, text="2026-06-06 Singing to the Same Sky"))
            tags.add(TPE1(encoding=3, text="Modern Jetset"))
            tags.add(TALB(encoding=3, text="Modern Jetset 2025"))
            tags.add(TDRC(encoding=3, text="2026-06-06"))
            tags.save(mp3)

            change_diffs = diff_id3_tags(mp3, targets)
            by_frame = {diff.frame_id: diff for diff in change_diffs}
            self.assertEqual(by_frame["TIT2"].status, "CHANGE")
            self.assertEqual(by_frame["TIT2"].current, "2026-06-06 Singing to the Same Sky")
            self.assertEqual(by_frame["TIT2"].target, "2026-03-11 Modern Jetset")
            self.assertEqual(by_frame["TPE1"].status, "OK")
            self.assertEqual(by_frame["TALB"].status, "CHANGE")
            self.assertEqual(by_frame["TDRC"].status, "CHANGE")
            self.assertEqual(by_frame["TYER"].status, "ADD")
            self.assertEqual(by_frame["TDAT"].status, "ADD")
            self.assertEqual(by_frame["TRCK"].status, "ADD")
            self.assertEqual(by_frame["COMM"].status, "ADD")

    def test_repair_mp3_tags_output_shows_add_change_and_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            podcast_dir = library_dir / "Modern Jetset"
            year_dir = podcast_dir / "Modern Jetset 2026"
            year_dir.mkdir(parents=True)
            mp3 = year_dir / "2026-03-11 Modern Jetset.mp3"
            mp3.write_bytes(b"audio")
            tags = ID3()
            tags.add(TIT2(encoding=3, text="2026-06-06 Singing to the Same Sky"))
            tags.add(TPE1(encoding=3, text="Modern Jetset"))
            tags.save(mp3)

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            output = "\n".join(repair_mp3_tags(load_config(config), write=False))
            self.assertIn("DRY", output)
            self.assertIn("CHANGE Title (TIT2): 2026-06-06 Singing to the Same Sky -> 2026-03-11 Modern Jetset", output)
            self.assertIn("OK     Artist (TPE1): Modern Jetset", output)
            self.assertIn("ADD    Album (TALB): Modern Jetset 2026", output)
            self.assertIn("ADD    Date (TDRC): 2026-03-11", output)
            self.assertIn("ADD    Year (TYER): 2026", output)
            self.assertIn("ADD    DayMonth (TDAT): 1103", output)
            self.assertIn("ADD    Track (TRCK): 20260311", output)
            self.assertIn("ADD    Comment (COMM): 2026-03-11 Modern Jetset", output)

    def test_repair_mp3_tags_can_target_single_podcast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            modern_dir = library_dir / "Modern Jetset"
            sky_dir = library_dir / "Singing to the Same Sky"
            modern_dir.mkdir(parents=True)
            sky_dir.mkdir()
            (modern_dir / "2026-03-11 Modern Jetset.mp3").write_bytes(b"audio")
            (sky_dir / "2026-06-06 Singing to the Same Sky.mp3").write_bytes(b"audio")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            output = "\n".join(
                repair_mp3_tags(load_config(config), write=False, podcast_filter="modern-jetset")
            )
            self.assertIn("Modern Jetset.mp3", output)
            self.assertNotIn("Singing to the Same Sky.mp3", output)

    def test_repair_mp3_tags_skips_non_matching_filename_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            podcast_dir = library_dir / "Modern Jetset"
            podcast_dir.mkdir(parents=True)
            mp3 = podcast_dir / "Modern Jetset without a date.mp3"
            mp3.write_bytes(b"audio")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            output = "\n".join(repair_mp3_tags(load_config(config), write=True))
            self.assertIn("SKIP no filename date", output)
            with self.assertRaises(Exception):
                ID3(mp3)

    def test_repair_mp3_tags_reports_no_matching_podcast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            podcast_dir = library_dir / "Modern Jetset"
            podcast_dir.mkdir(parents=True)
            (podcast_dir / "2026-03-11 Modern Jetset.mp3").write_bytes(b"audio")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            self.assertEqual(
                repair_mp3_tags(load_config(config), write=False, podcast_filter="missing"),
                ["No podcast matched: missing"],
            )

    def test_diagnose_podcast_reports_feed_identity_and_tag_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            podcast_dir = library_dir / "Gracyn Your Eardrums"
            podcast_dir.mkdir(parents=True)
            mp3 = podcast_dir / "2026-06-01 Gracyn Your Eardrums.mp3"
            mp3.write_bytes(b"audio")

            config = root / "config.toml"
            config.write_text(
                f"""
[server]
base_url = "http://127.0.0.1:8000"
host = "127.0.0.1"
port = 8000

[feed]
title = "Kitchen Radio"
description = "Local shows"
author = "KVCU"
root_directory = "{library_dir}"
""",
                encoding="utf-8",
            )

            with patch("app.MutagenFile", return_value=FakeAudioWithoutTitle()):
                flask_app = create_app(config)
                with flask_app.test_request_context():
                    output = "\n".join(
                        diagnose_podcast(
                            flask_app.config["PODCAST_CONFIG"],
                            podcast_filter="gracyn-your-eardrums",
                            limit=1,
                        )
                    )
            self.assertIn("Podcast: Gracyn Your Eardrums", output)
            self.assertIn("feed titles: no duplicates", output)
            self.assertIn("feed GUIDs: no duplicates", output)
            self.assertIn("enclosure URLs: no duplicates", output)
            self.assertIn("Episode: 2026-06-01 Gracyn Your Eardrums", output)
            self.assertIn("TAG ADD    Title (TIT2): 2026-06-01 Gracyn Your Eardrums", output)

    def _first_link_for(self, html: str, title: str) -> str:
        match = re.search(
            rf'<a[^>]+href="http://127\.0\.0\.1:8000(?P<path>[^"]+)"[^>]*>{re.escape(title)}</a>',
            html,
        )
        self.assertIsNotNone(match)
        return match.group("path")

    def _feed_input_path_for(self, html: str, title: str) -> str:
        title_at = html.index(f">{title}</a>")
        match = re.search(r'<input[^>]+value="http://127\.0\.0\.1:8000(?P<path>[^"]+)"', html[title_at:])
        self.assertIsNotNone(match)
        return match.group("path")

    def _assert_uncached(self, response) -> None:
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")
        self.assertEqual(response.headers["Surrogate-Control"], "no-store")
        self.assertEqual(response.headers["CDN-Cache-Control"], "no-store")
        self.assertEqual(response.headers["Cloudflare-CDN-Cache-Control"], "no-store")
        self.assertNotIn("ETag", response.headers)
        self.assertNotIn("Last-Modified", response.headers)

    def _assert_cached_artwork(self, response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=604800, immutable")
        self.assertEqual(response.headers["Surrogate-Control"], "max-age=604800")
        self.assertEqual(response.headers["CDN-Cache-Control"], "public, max-age=604800")
        self.assertEqual(response.headers["Cloudflare-CDN-Cache-Control"], "public, max-age=604800")
        self.assertNotIn("Pragma", response.headers)


if __name__ == "__main__":
    unittest.main()
