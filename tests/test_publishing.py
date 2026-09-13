from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autotok import publishing


class PublishingTests(unittest.TestCase):
    def test_publish_queue_tracks_status_without_uploading(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "video.mp4"
            video.write_bytes(b"fake video bytes")
            queue_path = root / "publish_queue.json"
            with patch.object(publishing, "PUBLISH_QUEUE_PATH", queue_path):
                item = publishing.enqueue_publish({"video": str(video)}, "approved")
                publishing.update_publish_item(item["id"], "failed", "test error")
                saved = publishing.load_publish_queue()
            self.assertEqual(saved[0]["status"], "failed")
            self.assertEqual(saved[0]["error"], "test error")

    def test_private_test_writes_review_package(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            private_dir = root / "private"
            with patch.object(publishing, "PRIVATE_TEST_DIR", private_dir):
                package = publishing.create_private_test({"video": str(video), "description": "Review me"})
            self.assertTrue(package.exists())
            self.assertIn("Review me", package.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
