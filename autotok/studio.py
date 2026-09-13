from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from moviepy.editor import VideoFileClip

from .config import APP_DIR
from .preferences import AppPreferences
from .reddit import StoryPost


STATE_DIR = APP_DIR / ".autotok"
PRESETS_PATH = STATE_DIR / "presets.json"
ANALYTICS_PATH = STATE_DIR / "analytics.json"
RIGHTS_PATH = STATE_DIR / "rights.json"
DRAFTS_PATH = STATE_DIR / "drafts.json"

BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "TIFU 60s": {
        "caption_style": "Karaoke", "part_length": "60 seconds",
        "profanity_mode": "Platform-safe", "voice_speed": 1.05,
        "scene_aware": True, "smart_match_background": True,
    },
    "AITA 90s": {
        "caption_style": "Purple Pop", "part_length": "90 seconds",
        "profanity_mode": "Softened", "voice_speed": 1.0,
        "scene_aware": True, "smart_match_background": True,
    },
    "Confessions fast": {
        "caption_style": "Karaoke", "part_length": "60 seconds",
        "profanity_mode": "Uncensored", "voice_speed": 1.1,
        "scene_aware": True, "smart_match_background": True,
    },
}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def load_presets() -> dict[str, dict[str, Any]]:
    custom = _read_json(PRESETS_PATH, {})
    return {**BUILTIN_PRESETS, **(custom if isinstance(custom, dict) else {})}


def save_preset(name: str, preferences: AppPreferences) -> None:
    custom = _read_json(PRESETS_PATH, {})
    if not isinstance(custom, dict):
        custom = {}
    custom[name.strip()] = asdict(preferences)
    _write_json(PRESETS_PATH, custom)


def apply_preset(preferences: AppPreferences, name: str) -> AppPreferences:
    values = load_presets().get(name, {})
    allowed = AppPreferences.__dataclass_fields__
    current = asdict(preferences)
    current.update({key: value for key, value in values.items() if key in allowed})
    return AppPreferences(**current)


def load_drafts() -> list[dict[str, Any]]:
    data = _read_json(DRAFTS_PATH, [])
    return data if isinstance(data, list) else []


def save_draft(post: StoryPost, preferences: AppPreferences) -> dict[str, Any]:
    drafts = load_drafts()
    fingerprint = story_fingerprint(post)
    draft = {
        "id": fingerprint[:16],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "post": asdict(post),
        "preferences": asdict(preferences),
    }
    drafts = [item for item in drafts if item.get("id") != draft["id"]]
    drafts.insert(0, draft)
    _write_json(DRAFTS_PATH, drafts[:100])
    return draft


def story_fingerprint(post: StoryPost) -> str:
    normalized = re.sub(r"\W+", " ", f"{post.title} {post.body}".lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as source:
        digest.update(source.read(1024 * 1024))
        if size > 1024 * 1024:
            source.seek(max(0, size - 1024 * 1024))
            digest.update(source.read(1024 * 1024))
    return digest.hexdigest()


def generate_hook_variants(post: StoryPost) -> list[dict[str, Any]]:
    community = post.subreddit.removeprefix("r/") or "Reddit"
    title = re.sub(r"\s+", " ", post.title).strip().rstrip(".!?")
    title_short = title[:82].rsplit(" ", 1)[0] if len(title) > 82 else title
    candidates = [
        f"This {community} story gets wild fast…",
        f"I did not expect this ending: {title_short}",
        "The first red flag was only the beginning…",
        "Would you have handled this differently?",
        f"Reddit had a lot to say about this: {title_short}",
    ]
    seen: set[str] = set()
    hooks: list[dict[str, Any]] = []
    for text in candidates:
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        words = len(text.split())
        score = 50
        score += 18 if 6 <= words <= 14 else 5
        score += 12 if any(token in text.lower() for token in ("ending", "red flag", "would you", "wild")) else 0
        score += 10 if len(text) <= 90 else 0
        hooks.append({"text": text, "score": min(100, score)})
    return sorted(hooks, key=lambda item: item["score"], reverse=True)


def package_path_for(video: Path) -> Path:
    return video.with_name(f"{video.stem}_post.json")


def load_post_package(video: Path) -> dict[str, Any]:
    path = package_path_for(video)
    data = _read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("video", str(video))
    data.setdefault("title", video.stem.replace("_", " ").title())
    data.setdefault("description", data["title"])
    data.setdefault("hashtags", ["#storytime", "#redditstories", "#fyp"])
    data.setdefault("hook_variants", [])
    return data


def save_post_package(video: Path, data: dict[str, Any]) -> Path:
    path = package_path_for(video)
    _write_json(path, data)
    return path


def save_rights_record(video: Path, record: dict[str, Any]) -> Path:
    items = _read_json(RIGHTS_PATH, {})
    if not isinstance(items, dict):
        items = {}
    items[str(video.resolve())] = {
        **record,
        "video_fingerprint": file_fingerprint(video),
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(RIGHTS_PATH, items)
    return RIGHTS_PATH


def preflight_video(video: Path, caption_position: float = 0.66) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(level: str, label: str, detail: str) -> None:
        checks.append({"level": level, "label": label, "detail": detail})

    if not video.exists():
        add("error", "Video", "File does not exist")
        return {"ok": False, "checks": checks}
    size = video.stat().st_size
    add("pass" if size <= 4_000_000_000 else "error", "File size", f"{size / 1024 / 1024:.1f} MB")
    if video.suffix.lower() != ".mp4":
        add("warning", "Container", f"{video.suffix or 'unknown'}; MP4 is recommended")
    else:
        add("pass", "Container", "MP4")

    clip = None
    try:
        clip = VideoFileClip(str(video))
        width, height = int(clip.w), int(clip.h)
        fps = float(clip.fps or 0)
        duration = float(clip.duration or 0)
        add("pass" if height > width and width >= 360 else "error", "Canvas", f"{width}×{height}")
        add("pass" if 23 <= fps <= 60 else "warning", "Frame rate", f"{fps:.2f} FPS")
        add("pass" if 0 < duration <= 600 else "warning", "Duration", f"{duration:.1f} seconds")
        add("pass" if clip.audio is not None else "error", "Audio", "AAC/narration present" if clip.audio else "No audio track")
    except Exception as exc:
        add("error", "Media inspection", str(exc))
    finally:
        if clip:
            clip.close()
    if 0.38 <= caption_position <= 0.72:
        add("pass", "Caption safe zone", f"{caption_position * 100:.0f}% from top")
    else:
        add("warning", "Caption safe zone", "Caption position may overlap TikTok controls")
    captions = video.with_suffix(".srt")
    if captions.exists():
        content = captions.read_text(encoding="utf-8", errors="replace")
        text_lines = [line.strip() for line in content.splitlines() if line.strip() and "-->" not in line and not line.strip().isdigit()]
        longest = max((len(line) for line in text_lines), default=0)
        add("pass" if longest <= 48 else "warning", "Caption readability", f"SRT present; longest caption is {longest} characters")
    else:
        add("warning", "Captions", "No sidecar SRT found")
    return {
        "ok": not any(item["level"] == "error" for item in checks),
        "warnings": sum(item["level"] == "warning" for item in checks),
        "checks": checks,
        "fingerprint": file_fingerprint(video),
    }


def import_analytics_csv(path: Path) -> int:
    existing = _read_json(ANALYTICS_PATH, [])
    if not isinstance(existing, list):
        existing = []
    added = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            normalized = {str(key).strip().lower().replace(" ", "_"): value for key, value in row.items() if key}
            if normalized:
                normalized["imported_at"] = datetime.now().isoformat(timespec="seconds")
                existing.append(normalized)
                added += 1
    _write_json(ANALYTICS_PATH, existing[-5000:])
    return added


def analytics_summary() -> dict[str, Any]:
    rows = _read_json(ANALYTICS_PATH, [])
    if not isinstance(rows, list):
        rows = []

    def number(row: dict[str, Any], *keys: str) -> float:
        for key in keys:
            try:
                return float(str(row.get(key, "0")).replace(",", ""))
            except ValueError:
                continue
        return 0.0

    views = sum(number(row, "views", "video_views") for row in rows)
    likes = sum(number(row, "likes") for row in rows)
    comments = sum(number(row, "comments") for row in rows)
    shares = sum(number(row, "shares") for row in rows)
    engagement = ((likes + comments + shares) / views * 100) if views else 0.0
    ranked = sorted(rows, key=lambda row: number(row, "views", "video_views"), reverse=True)
    return {"rows": len(rows), "views": int(views), "engagement": engagement, "top": ranked[:5]}


def notify_desktop(title: str, message: str) -> None:
    if os.name != "nt":
        return
    safe_title = title.replace("'", "''")[:80]
    safe_message = message.replace("'", "''")[:240]
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
        "$xml=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{safe_title}')) > $null;"
        f"$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('{safe_message}')) > $null;"
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($xml);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AutoTok').Show($toast)"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
