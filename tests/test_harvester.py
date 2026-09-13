from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from moviepy.editor import ColorClip, VideoFileClip

from autotok.harvester import (
    DEFAULT_HARVEST_PLAYLIST,
    HARVEST_SOURCES,
    HarvestResult,
    _playlist_entry_url,
    _safe_name,
    _validate_url,
    harvest_playlist,
    harvest_sources,
    split_video,
)


class HarvesterTests(unittest.TestCase):
    def test_url_validation_and_safe_library_names(self) -> None:
        self.assertEqual(_validate_url("https://example.com/video"), "https://example.com/video")
        with self.assertRaises(ValueError):
            _validate_url("not a url")
        self.assertEqual(_safe_name("My Clips / Racing!"), "My_Clips_Racing")
        self.assertIn("PLJVvekmbcMxBCh1Cb997PA2hsrxmxdB6G", DEFAULT_HARVEST_PLAYLIST)
        self.assertEqual(list(HARVEST_SOURCES), ["Minecraft", "Subway Surfers", "ASMR"])
        self.assertTrue(HARVEST_SOURCES["Minecraft"])
        self.assertFalse(HARVEST_SOURCES["Subway Surfers"])
        self.assertFalse(HARVEST_SOURCES["ASMR"])

    def test_playlist_entries_are_converted_to_video_urls(self) -> None:
        self.assertEqual(
            _playlist_entry_url({"id": "xKRNDalWE-E"}),
            "https://www.youtube.com/watch?v=xKRNDalWE-E",
        )
        self.assertEqual(
            _playlist_entry_url({"webpage_url": "https://example.com/watch/1", "id": "ignored"}),
            "https://example.com/watch/1",
        )
        self.assertIsNone(_playlist_entry_url({}))

    @patch("autotok.harvester.harvest_video")
    @patch("autotok.harvester.playlist_video_urls")
    def test_playlist_is_harvested_one_video_at_a_time(self, playlist_urls, harvest_video_mock) -> None:
        playlist_urls.return_value = ["https://example.com/1", "https://example.com/2"]
        harvest_video_mock.side_effect = [
            HarvestResult("One", "https://example.com/1", (Path("one.mp4"),)),
            HarvestResult("Two", "https://example.com/2", (Path("two.mp4"),)),
        ]

        results = harvest_playlist(DEFAULT_HARVEST_PLAYLIST, "Harvested", 60)

        self.assertEqual([result.title for result in results], ["One", "Two"])
        self.assertEqual(
            [item.args[:3] for item in harvest_video_mock.call_args_list],
            [
                call("https://example.com/1", "Harvested", 60).args,
                call("https://example.com/2", "Harvested", 60).args,
            ],
        )

    @patch("autotok.harvester.harvest_urls")
    @patch("autotok.harvester.playlist_video_urls")
    def test_custom_sources_expand_playlists_and_respect_download_limit(self, playlist_urls, harvest_urls_mock) -> None:
        playlist_urls.side_effect = [
            ["https://example.com/1", "https://example.com/2"],
            ["https://example.com/3"],
        ]
        harvest_urls_mock.return_value = []

        harvest_sources(
            ["https://example.com/playlist", "https://example.com/video"],
            "Custom", 45, max_videos=2, include_audio=False,
        )

        playlist_urls.assert_called_once_with("https://example.com/playlist")
        harvest_urls_mock.assert_called_once_with(
            ["https://example.com/1", "https://example.com/2"],
            "Custom", 45, None, False,
        )

    def test_split_video_creates_sequential_segments(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            clip = ColorClip((64, 64), color=(40, 80, 120), duration=2.2)
            try:
                clip.write_videofile(str(source), fps=10, codec="libx264", audio=False, logger=None)
            finally:
                clip.close()
            outputs = split_video(source, root / "segments", segment_seconds=1)
            self.assertEqual([path.name for path in outputs], ["clip_000.mp4", "clip_001.mp4", "clip_002.mp4"])
            durations = []
            for output in outputs:
                rendered = VideoFileClip(str(output), audio=False)
                try:
                    durations.append(rendered.duration)
                finally:
                    rendered.close()
            self.assertTrue(all(0 < duration <= 1.2 for duration in durations))

    def test_split_video_can_remove_audio(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            clip = ColorClip((64, 64), color=(20, 40, 60), duration=1.2)
            try:
                clip.write_videofile(str(source), fps=10, codec="libx264", audio=False, logger=None)
            finally:
                clip.close()
            output = split_video(source, root / "silent", segment_seconds=1, include_audio=False)[0]
            rendered = VideoFileClip(str(output))
            try:
                self.assertIsNone(rendered.audio)
            finally:
                rendered.close()


if __name__ == "__main__":
    unittest.main()
