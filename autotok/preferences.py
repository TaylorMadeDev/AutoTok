from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import APP_DIR


PREFERENCES_PATH = APP_DIR / "preferences.json"


@dataclass(slots=True)
class AppPreferences:
    caption_style: str = "Karaoke"
    caption_position: float = 0.66
    part_length: str = "90 seconds"
    align_captions: bool = True
    whisper_model: str = "tiny.en"
    profanity_mode: str = "Uncensored"
    auto_music: bool = False
    music_volume: float = 0.10
    voice_speed: float = 1.0
    video_category: str = "All"
    scene_aware: bool = True
    smart_match_background: bool = True
    audio_polish: bool = True
    bake_tiktok_cover: bool = True
    duplicate_check: bool = True
    desktop_notifications: bool = True
    notification_failures_only: bool = True
    default_publish_provider: str = "Export only"
    tiktok_account: str = ""
    default_privacy: str = "Private test"
    schedule_spacing_hours: int = 24
    active_preset: str = "TIFU 60s"

    @property
    def part_seconds(self) -> int:
        return {
            "60 seconds": 60,
            "90 seconds": 90,
            "3 minutes": 180,
            "Full story": 0,
        }.get(self.part_length, 90)


def load_preferences() -> AppPreferences:
    if not PREFERENCES_PATH.exists():
        return AppPreferences()
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
        allowed = AppPreferences.__dataclass_fields__
        return AppPreferences(**{key: value for key, value in data.items() if key in allowed})
    except (OSError, ValueError, TypeError):
        return AppPreferences()


def save_preferences(preferences: AppPreferences) -> Path:
    PREFERENCES_PATH.write_text(json.dumps(asdict(preferences), indent=2), encoding="utf-8")
    return PREFERENCES_PATH
