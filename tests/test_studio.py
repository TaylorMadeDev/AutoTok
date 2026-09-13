from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from autotok.preferences import AppPreferences
from autotok.reddit import StoryPost
from autotok.studio import (
    apply_preset,
    file_fingerprint,
    generate_hook_variants,
    story_fingerprint,
)
from autotok.config import OpenRouterConfig
from autotok.engine import synthesize_speech
from autotok.voices import DEFAULT_FLUX_VOICE, FLUX_VOICES, valid_flux_voice
from autotok.updater import _safe_extract, is_newer_version


class StudioTests(unittest.TestCase):
    def test_complete_flux_voice_catalog_includes_wes_default(self) -> None:
        self.assertEqual(len(FLUX_VOICES), 36)
        self.assertIn("flux-wes-en", FLUX_VOICES)
        self.assertEqual(DEFAULT_FLUX_VOICE, "flux-wes-en")
        self.assertEqual(valid_flux_voice("not-a-voice"), "flux-wes-en")

    def test_story_fingerprint_ignores_case_and_punctuation(self) -> None:
        first = StoryPost("r/TIFU", "A TITLE!", "Something happened.")
        second = StoryPost("r/TIFU", "a title", "something happened")
        self.assertEqual(story_fingerprint(first), story_fingerprint(second))

    def test_hooks_are_ranked_and_short(self) -> None:
        post = StoryPost("r/TIFU", "I trusted the wrong coworker", "A sufficiently long story body.")
        hooks = generate_hook_variants(post)
        self.assertGreaterEqual(len(hooks), 4)
        self.assertEqual(hooks, sorted(hooks, key=lambda item: item["score"], reverse=True))
        self.assertTrue(all(len(item["text"]) <= 140 for item in hooks))

    def test_builtin_preset_changes_only_known_preferences(self) -> None:
        result = apply_preset(AppPreferences(), "TIFU 60s")
        self.assertEqual(result.part_length, "60 seconds")
        self.assertEqual(result.caption_style, "Karaoke")

    def test_custom_chunk_seconds_and_full_story(self) -> None:
        custom = AppPreferences(part_length="75 seconds")
        full = AppPreferences(part_length="Full story")
        self.assertEqual(custom.part_seconds, 75)
        self.assertTrue(custom.split_enabled)
        self.assertEqual(full.part_seconds, 0)
        self.assertFalse(full.split_enabled)

    def test_chunk_seconds_are_clamped_to_safe_range(self) -> None:
        self.assertEqual(AppPreferences(part_length="2 seconds").part_seconds, 15)
        self.assertEqual(AppPreferences(part_length="9999 seconds").part_seconds, 3600)

    def test_github_release_version_comparison(self) -> None:
        self.assertTrue(is_newer_version("v1.1.0", "1.0.2"))
        self.assertFalse(is_newer_version("v1.0.2", "1.0.2"))
        self.assertFalse(is_newer_version("v1.0.1", "1.0.2"))

    def test_updater_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "update.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside.txt", "unsafe")
                bundle.writestr("AutoTok.exe", "placeholder")
            with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                _safe_extract(archive, root / "staged")
            self.assertFalse((root / "outside.txt").exists())

    def test_file_fingerprint_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.bin"
            path.write_bytes(b"first")
            first = file_fingerprint(path)
            path.write_bytes(b"second")
            self.assertNotEqual(first, file_fingerprint(path))

    def test_azure_speech_uses_regional_endpoint_and_ssml(self) -> None:
        class Response:
            ok = True
            status_code = 200
            content = b"ID3" + b"a" * 200

        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache"
            output = Path(folder) / "voice"
            config = OpenRouterConfig(
                provider="azure", azure_key="test-key", azure_region="uksouth",
                azure_voice="en-GB-SoniaNeural",
            )
            with patch("autotok.engine.TTS_CACHE_DIR", cache), patch("autotok.engine.requests.post", return_value=Response()) as post:
                path, label = synthesize_speech("This & that", config, output, allow_local_fallback=False)
            self.assertEqual(label, "Azure Speech")
            self.assertTrue(path.exists())
            self.assertIn("uksouth.tts.speech.microsoft.com", post.call_args.args[0])
            self.assertIn(b"This &amp; that", post.call_args.kwargs["data"])


if __name__ == "__main__":
    unittest.main()
