from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app import create_app


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


class PodcastServerTest(unittest.TestCase):
    def test_feed_and_audio_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_dir = root / "library"
            audio_dir = library_dir / "Kitchen Radio"
            other_dir = library_dir / "Evening News"
            audio_dir.mkdir(parents=True)
            other_dir.mkdir()
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
                self.assertIn(b"Kitchen Radio", index_response.data)
                self.assertIn(b"Evening News", index_response.data)

                feed_path = self._first_link_for(index_response.data.decode(), "Kitchen Radio")
                feed_response = client.get(feed_path)
                self.assertEqual(feed_response.status_code, 200)

                rss = ET.fromstring(feed_response.data)
                channel = rss.find("./channel")
                self.assertIsNotNone(channel)
                image = channel.find("image")
                self.assertIsNotNone(image)
                self.assertTrue(image.findtext("url").endswith("/cover.jpg"))
                itunes_image = channel.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}image")
                self.assertIsNotNone(itunes_image)
                self.assertTrue(itunes_image.attrib["href"].endswith("/cover.jpg"))

                item = rss.find("./channel/item")
                self.assertIsNotNone(item)
                self.assertEqual(item.findtext("title"), "The First Track")
                self.assertEqual(item.findtext("description"), "A locally hosted episode.")

                enclosure = item.find("enclosure")
                self.assertIsNotNone(enclosure)
                self.assertEqual(enclosure.attrib["type"], "audio/mpeg")

                audio_response = client.get(enclosure.attrib["url"].replace("http://127.0.0.1:8000", ""))
                self.assertEqual(audio_response.status_code, 200)
                self.assertEqual(audio_response.data, mp3.read_bytes())
                audio_response.close()

                cover_path = image.findtext("url").replace("http://127.0.0.1:8000", "")
                cover_response = client.get(cover_path)
                self.assertEqual(cover_response.status_code, 200)
                self.assertEqual(cover_response.data, cover.read_bytes())
                cover_response.close()

    def _first_link_for(self, html: str, title: str) -> str:
        match = re.search(
            rf'<a href="http://127\.0\.0\.1:8000(?P<path>[^"]+)">{re.escape(title)}</a>',
            html,
        )
        self.assertIsNotNone(match)
        return match.group("path")


if __name__ == "__main__":
    unittest.main()
