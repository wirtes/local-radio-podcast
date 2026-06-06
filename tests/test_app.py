from __future__ import annotations

import tempfile
import unittest
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
            audio_dir = root / "audio"
            audio_dir.mkdir()
            mp3 = audio_dir / "episode.mp3"
            mp3.write_bytes(b"not a real mp3, but enough for send_file")

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
directories = ["{audio_dir}"]
""",
                encoding="utf-8",
            )

            with patch("app.MutagenFile", return_value=FakeAudio()):
                flask_app = create_app(config)
                client = flask_app.test_client()

                feed_response = client.get("/feed.xml")
                self.assertEqual(feed_response.status_code, 200)

                rss = ET.fromstring(feed_response.data)
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


if __name__ == "__main__":
    unittest.main()
