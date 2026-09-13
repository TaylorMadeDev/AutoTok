from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .config import APP_DIR


ALIGNMENT_CACHE = APP_DIR / ".autotok" / "alignments"
_MODELS: dict[str, object] = {}


@dataclass(slots=True)
class AlignedWord:
    text: str
    start: float
    end: float


def _cache_path(audio_path: Path, model_name: str) -> Path:
    digest = hashlib.sha256()
    digest.update(model_name.encode("utf-8"))
    with audio_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    ALIGNMENT_CACHE.mkdir(parents=True, exist_ok=True)
    return ALIGNMENT_CACHE / f"{digest.hexdigest()}.json"


def align_audio(
    audio_path: Path,
    transcript: str,
    model_name: str = "tiny.en",
    status: Callable[[str], None] | None = None,
) -> list[AlignedWord]:
    cache = _cache_path(audio_path, model_name)
    if cache.exists():
        try:
            cached = [AlignedWord(**item) for item in json.loads(cache.read_text(encoding="utf-8"))]
            return _reconcile_transcript(cached, transcript)
        except (OSError, ValueError, TypeError):
            pass

    if status:
        status(f"Aligning captions with Whisper ({model_name})")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed; run setup.bat again.") from exc

    model = _MODELS.get(model_name)
    if model is None:
        if status:
            status(f"Loading Whisper {model_name} (first use downloads the model)")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _MODELS[model_name] = model

    segments, _info = model.transcribe(
        str(audio_path), language="en", beam_size=1, word_timestamps=True,
        vad_filter=False, condition_on_previous_text=False,
        initial_prompt=transcript[:800],
    )
    words: list[AlignedWord] = []
    for segment in segments:
        for word in segment.words or []:
            cleaned = word.word.strip()
            if cleaned and word.start is not None and word.end is not None:
                words.append(AlignedWord(cleaned, float(word.start), float(word.end)))
    if not words:
        raise RuntimeError("Whisper did not return word timestamps.")
    cache.write_text(json.dumps([asdict(word) for word in words]), encoding="utf-8")
    return _reconcile_transcript(words, transcript)


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9']", "", value.lower())


def _reconcile_transcript(observed: list[AlignedWord], transcript: str) -> list[AlignedWord]:
    """Keep Whisper timing while restoring the exact known narration words."""
    expected = transcript.split()
    if not observed or not expected:
        return observed
    matcher = SequenceMatcher(
        None,
        [_normalized(word.text) for word in observed],
        [_normalized(word) for word in expected],
        autojunk=False,
    )
    result: list[AlignedWord] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result.extend(
                AlignedWord(expected[j], observed[i].start, observed[i].end)
                for i, j in zip(range(i1, i2), range(j1, j2))
            )
            continue
        if tag == "delete":
            continue
        replacement = expected[j1:j2]
        if not replacement:
            continue
        if i2 > i1:
            start, end = observed[i1].start, observed[i2 - 1].end
        else:
            start = result[-1].end if result else (observed[i1].start if i1 < len(observed) else 0.0)
            end = observed[i1].start if i1 < len(observed) else start + 0.25 * len(replacement)
        duration = max(0.04 * len(replacement), end - start)
        weights = [max(1, len(_normalized(word))) for word in replacement]
        total = float(sum(weights))
        cursor = start
        for word, weight in zip(replacement, weights):
            word_end = cursor + duration * weight / total
            result.append(AlignedWord(word, cursor, word_end))
            cursor = word_end
    return sorted(result, key=lambda word: (word.start, word.end))


def align_audio_parts(
    audio_paths: list[Path],
    transcripts: list[str],
    durations: list[float],
    model_name: str,
    status: Callable[[str], None] | None = None,
) -> list[AlignedWord]:
    combined: list[AlignedWord] = []
    offset = 0.0
    for index, (path, transcript, duration) in enumerate(zip(audio_paths, transcripts, durations), start=1):
        if status:
            status(f"Aligning narration part {index}/{len(audio_paths)}")
        words = align_audio(path, transcript, model_name, status)
        combined.extend(AlignedWord(word.text, word.start + offset, word.end + offset) for word in words)
        offset += duration
    return combined
