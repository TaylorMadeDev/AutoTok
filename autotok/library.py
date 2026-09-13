from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg

from .config import APP_DIR


VIDEO_DIR = APP_DIR / "videos"
MUSIC_DIR = APP_DIR / "music"
STATE_DIR = APP_DIR / ".autotok"
LIBRARY_STATE = STATE_DIR / "library.json"
SCENE_CACHE = STATE_DIR / "scenes.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


def ensure_library_folders() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def scan_videos(category: str = "All") -> list[Path]:
    ensure_library_folders()
    videos = [path for path in VIDEO_DIR.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
    if category and category != "All":
        videos = [path for path in videos if category.lower() in {part.lower() for part in path.relative_to(VIDEO_DIR).parts[:-1]}]
    return sorted(videos)


def video_categories() -> list[str]:
    categories = {path.relative_to(VIDEO_DIR).parts[0] for path in scan_videos() if len(path.relative_to(VIDEO_DIR).parts) > 1}
    return ["All", *sorted(categories, key=str.lower)]


def scan_music() -> list[Path]:
    ensure_library_folders()
    return sorted(path for path in MUSIC_DIR.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def choose_smart_video(candidates: list[Path], excluded: set[Path] | None = None) -> Path | None:
    if not candidates:
        return None
    state = _read_json(LIBRARY_STATE)
    excluded = excluded or set()
    available = [path for path in candidates if path not in excluded] or candidates
    ranked = sorted(available, key=lambda path: (state.get(str(path.resolve()), {}).get("uses", 0), random.random()))
    lowest_use = state.get(str(ranked[0].resolve()), {}).get("uses", 0)
    pool = [path for path in ranked if state.get(str(path.resolve()), {}).get("uses", 0) <= lowest_use + 1][:4]
    chosen = random.choice(pool)
    key = str(chosen.resolve())
    item = state.setdefault(key, {})
    item["uses"] = int(item.get("uses", 0)) + 1
    item["last_used"] = time.time()
    _write_json(LIBRARY_STATE, state)
    return chosen


def choose_music() -> Path | None:
    tracks = scan_music()
    return random.choice(tracks) if tracks else None


def choose_story_category(text: str, categories: list[str]) -> str:
    """Pick a matching footage folder when the user's folder names support it."""
    available = {category.lower(): category for category in categories if category != "All"}
    if not available:
        return "All"
    lowered = text.lower()
    themes = {
        "cooking": ("food", "cook", "restaurant", "dinner", "kitchen"),
        "racing": ("car", "drive", "road", "race", "speed"),
        "minecraft": ("game", "school", "family", "relationship", "work"),
        "nature": ("travel", "outside", "park", "walk", "animal"),
        "satisfying": ("clean", "organize", "repair", "build", "make"),
    }
    for category, keywords in themes.items():
        if category in available and any(word in lowered for word in keywords):
            return available[category]
    # Also allow arbitrary category names to match story words directly.
    for key, original in available.items():
        if key in lowered:
            return original
    return "All"


def recommended_cut_range(text: str) -> tuple[float, float]:
    lowered = text.lower()
    dramatic = sum(lowered.count(word) for word in ("suddenly", "but then", "shocked", "angry", "caught", "never"))
    questions = text.count("?")
    if dramatic + questions >= 4:
        return 7.0, 14.0
    if len(text.split()) > 500:
        return 10.0, 18.0
    return 12.0, 22.0


def scene_change_points(path: Path, timeout: int = 240) -> list[float]:
    """Detect and cache likely cut points using FFmpeg's scene score."""
    cache = _read_json(SCENE_CACHE)
    key = str(path.resolve())
    stamp = path.stat().st_mtime
    cached = cache.get(key, {})
    if cached.get("mtime") == stamp and isinstance(cached.get("points"), list):
        return [float(value) for value in cached["points"]]

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path),
        "-vf", r"scale=320:-1,select=gt(scene\,0.32),showinfo",
        "-an", "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        points = sorted({float(value) for value in re.findall(r"pts_time:([0-9.]+)", result.stderr)})
    except (OSError, subprocess.SubprocessError, ValueError):
        points = []
    cache[key] = {"mtime": stamp, "points": points}
    _write_json(SCENE_CACHE, cache)
    return points


def choose_scene_start(path: Path, max_start: float, enabled: bool = True) -> float:
    if max_start <= 0:
        return 0.0
    if enabled:
        valid = [point for point in scene_change_points(path) if 0 <= point <= max_start]
        if valid:
            return random.choice(valid)
    return random.uniform(0.0, max_start)
