from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .config import APP_DIR
from .reddit import StoryPost
from .studio import story_fingerprint


STATE_DIR = APP_DIR / ".autotok"
QUEUE_PATH = STATE_DIR / "queue.json"
HISTORY_PATH = STATE_DIR / "history.json"


def _read(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError, TypeError):
        return []


def _write(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def load_queue() -> list[StoryPost]:
    posts: list[StoryPost] = []
    for item in _read(QUEUE_PATH):
        try:
            posts.append(StoryPost(**item))
        except TypeError:
            pass
    return posts


def save_queue(posts: list[StoryPost]) -> None:
    _write(QUEUE_PATH, [asdict(post) for post in posts])


def append_history(post: StoryPost, outputs: list[Path], status: str = "completed", error: str = "") -> None:
    items = _read(HISTORY_PATH)
    items.insert(
        0,
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "title": post.title,
            "subreddit": post.subreddit,
            "permalink": post.permalink,
            "status": status,
            "outputs": [str(path) for path in outputs],
            "error": error,
            "story_fingerprint": story_fingerprint(post),
        },
    )
    _write(HISTORY_PATH, items[:250])


def load_history() -> list[dict]:
    return _read(HISTORY_PATH)


def seen_permalinks() -> set[str]:
    return {item.get("permalink", "") for item in _read(HISTORY_PATH) if item.get("permalink")}


def find_duplicate_story(post: StoryPost) -> dict | None:
    fingerprint = story_fingerprint(post)
    for item in _read(HISTORY_PATH):
        if item.get("story_fingerprint") == fingerprint:
            return item
        if post.permalink and item.get("permalink") == post.permalink:
            return item
    return None
