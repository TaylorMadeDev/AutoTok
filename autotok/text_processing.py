from __future__ import annotations

import json
import re
from pathlib import Path

from .config import APP_DIR


PRONUNCIATIONS_PATH = APP_DIR / "pronunciations.json"


def load_pronunciations() -> dict[str, str]:
    defaults = {"TIFU": "time I effed up"}
    if not PRONUNCIATIONS_PATH.exists():
        return defaults
    try:
        loaded = json.loads(PRONUNCIATIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            defaults.update({str(key): str(value) for key, value in loaded.items() if key and value})
    except (OSError, ValueError, TypeError):
        pass
    return defaults


def make_spoken_text(text: str, pronunciations: dict[str, str] | None = None) -> str:
    result = text
    dictionary = pronunciations or load_pronunciations()
    for source in sorted(dictionary, key=len, reverse=True):
        result = re.sub(
            rf"(?<!\w){re.escape(source)}(?!\w)",
            dictionary[source],
            result,
            flags=re.IGNORECASE,
        )
    return result


def clean_story_for_narration(text: str, profanity_mode: str = "Uncensored") -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?im)^\s*(?:edit|update)(?:\s+\d+)?\s*:\s*", "", text)
    text = re.sub(r"(?<!\w)/?[ur]/[A-Za-z0-9_-]+", "", text)
    text = re.sub(r"[*_~`#>]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    mode = profanity_mode.lower()
    if mode == "softened":
        replacements = {
            r"\bfuck(?:ing|ed)?\b": "freaking",
            r"\bshit(?:ty)?\b": "mess",
            r"\basshole\b": "jerk",
            r"\bbitch\b": "jerk",
        }
    elif mode == "platform-safe":
        replacements = {
            r"\bfuck(?:ing|ed)?\b": "effed",
            r"\bshit(?:ty)?\b": "stuff",
            r"\basshole\b": "a-hole",
            r"\bbitch\b": "person",
            r"\bdick\b": "idiot",
        }
    else:
        replacements = {}
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _sentence_units(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    return [unit.strip() for unit in re.split(r"(?<=[.!?])\s+", clean) if unit.strip()]


def split_by_limits(text: str, max_words: int, max_chars: int) -> list[str]:
    sentences = _sentence_units(text)
    chunks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            chunks.append(" ".join(current))
            current.clear()

    for sentence in sentences:
        remaining = sentence.split()
        while remaining:
            existing_chars = len(" ".join(current))
            room_words = max(1, max_words - len(current))
            room_chars = max(1, max_chars - existing_chars - (1 if current else 0))
            take = 0
            chars = 0
            for word in remaining[:room_words]:
                added = len(word) + (1 if take else 0)
                if take and chars + added > room_chars:
                    break
                if not take and len(word) > room_chars and current:
                    break
                chars += added
                take += 1
            if not take:
                flush()
                continue
            current.extend(remaining[:take])
            remaining = remaining[take:]
            if remaining or len(current) >= max_words or len(" ".join(current)) >= max_chars:
                flush()
    flush()
    return chunks


def split_story_parts(text: str, target_seconds: int) -> list[str]:
    if target_seconds <= 0:
        return [text.strip()]
    # Reserve roughly eight seconds for the narrated intro/card.
    story_seconds = max(25, target_seconds - 8)
    target_words = max(55, int(story_seconds * 2.35))
    return split_by_limits(text, max_words=target_words, max_chars=100_000)


def seconds_to_srt(value: float) -> str:
    milliseconds = max(0, int(round(value * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def write_srt(chunks: list[dict[str, float | str]], path: Path, offset: float = 0.0) -> Path:
    lines: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        lines.extend(
            [
                str(index),
                f"{seconds_to_srt(float(chunk['start']) + offset)} --> {seconds_to_srt(float(chunk['end']) + offset)}",
                str(chunk["text"]),
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
