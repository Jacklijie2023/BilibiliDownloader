import json
import tempfile
import unittest
from pathlib import Path

from app.metadata.writer import (
    metadata_paths,
    save_subtitle_tracks,
    save_video_metadata,
)
from app.models import VideoInfo
from app.url_parser import canonicalize_bilibili_url, parse_video_url
from app.media.resolver import select_dash_streams, stream_urls
import main


class UrlParserTests(unittest.TestCase):
    def test_bvid_and_page(self):
        url = (
            "https://www.bilibili.com/video/BV1kkArz1EXq?"
            "spm_id_from=x&vd_source=y&p=7"
        )
        self.assertEqual(parse_video_url(url), ("BV1kkArz1EXq", None, 7))
        self.assertEqual(
            canonicalize_bilibili_url(url),
            "https://www.bilibili.com/video/BV1kkArz1EXq?p=7",
        )

    def test_av_without_page_defaults_to_one(self):
        self.assertEqual(
            parse_video_url("https://www.bilibili.com/video/av123"),
            (None, 123, 1),
        )


class StreamResolverTests(unittest.TestCase):
    def test_selects_best_allowed_video_and_audio(self):
        video, audio = select_dash_streams(
            {
                "video": [
                    {"height": 720, "codecid": 7, "bandwidth": 100},
                    {"height": 1080, "codecid": 7, "bandwidth": 200},
                ],
                "audio": [{"bandwidth": 50}, {"bandwidth": 100}],
            },
            720,
        )
        self.assertEqual(video["height"], 720)
        self.assertEqual(audio["bandwidth"], 100)

    def test_stream_urls_deduplicates_backup_addresses(self):
        self.assertEqual(
            stream_urls({"baseUrl": "a", "backupUrl": ["a", "b"]}),
            ["a", "b"],
        )


class MetadataTests(unittest.TestCase):
    def test_json_and_cover_are_written(self):
        class FakeResponse:
            headers = {"Content-Type": "image/jpeg"}
            content = b"fake-jpeg"

            def raise_for_status(self):
                return None

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        info = VideoInfo(
            platform="bilibili",
            bvid="BV1test123",
            aid=1,
            cid=2,
            title="Title",
            uploader="Uploader",
            page_number=1,
            page_count=1,
            page_title="Part",
            cover_url="https://example.com/cover.jpg",
            original_url="https://www.bilibili.com/video/BV1test123",
            canonical_url="https://www.bilibili.com/video/BV1test123",
        )
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "video.mp4"
            output.write_bytes(b"media")
            json_path, cover_path = save_video_metadata(
                output, info, "1080P", {"quality": 80}, FakeSession()
            )
            self.assertEqual(metadata_paths(output), (json_path, cover_path))
            self.assertEqual(json.loads(json_path.read_text())["title"], "Title")
            self.assertEqual(cover_path.read_bytes(), b"fake-jpeg")

    def test_subtitle_track_is_written_as_srt(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"body": [{"from": 0, "to": 1.25, "content": "hello"}]}

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "video.mp4"
            paths = save_subtitle_tracks(
                output,
                [{"subtitle_url": "https://example.com/subtitle.json", "lan": "en"}],
                FakeSession(),
            )
            self.assertEqual(len(paths), 1)
            self.assertIn("00:00:00,000 --> 00:00:01,250", paths[0].read_text())


class DownloadRoutingTests(unittest.TestCase):
    def test_bilibili_video_uses_api_before_ytdlp(self):
        downloader = main.BilibiliDownloader(
            tempfile.mkdtemp(), "1080P", "涓嶄娇鐢?"
        )
        seen = []
        downloader.download_via_api = lambda url: seen.append(url) or True
        downloader.download_via_ytdlp = lambda url: self.fail(
            "a standard BV URL must not use webpage extraction"
        )
        self.assertTrue(
            downloader.download_one(
                "https://www.bilibili.com/video/BV1kkArz1EXq?"
                "spm_id_from=x&vd_source=y&p=7"
            )
        )
        self.assertEqual(
            seen, ["https://www.bilibili.com/video/BV1kkArz1EXq?p=7"]
        )


if __name__ == "__main__":
    unittest.main()
