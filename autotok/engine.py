from __future__ import annotations

import math
import os
import random
import re
import hashlib
import json
import shutil
import subprocess
import wave
from urllib.parse import quote
from xml.sax.saxutils import escape
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable

import requests
from PIL import Image, ImageDraw, ImageFont

# MoviePy 1.0.3 references the pre-Pillow-10 constant.
if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from moviepy.editor import (  # noqa: E402
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_audioclips,
    concatenate_videoclips,
)
from moviepy.audio.fx import all as audio_fx  # noqa: E402
from moviepy.video.fx import all as video_fx  # noqa: E402

from .alignment import AlignedWord, align_audio_parts
from .config import APP_DIR, OpenRouterConfig
from .library import (
    choose_music,
    choose_scene_start,
    choose_smart_video,
    choose_story_category,
    recommended_cut_range,
    scan_videos,
    video_categories,
)
from .reddit import StoryPost
from .text_processing import (
    clean_story_for_narration,
    make_spoken_text,
    split_by_limits,
    split_story_parts,
    write_srt,
)


OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
TTS_CACHE_DIR = APP_DIR / ".autotok" / "tts"
ProgressCallback = Callable[[float, str], None]
_KEY_ROTATION_CURSOR: dict[tuple[str, tuple[str, ...]], int] = {}
_KEY_LIMIT_STATUS_CODES = {401, 402, 403, 429}


def spoken_subreddit_name(subreddit: str) -> str:
    """Return a narration-friendly subreddit name without the visual r/ prefix."""
    return make_spoken_text(subreddit.strip().removeprefix("r/"))


def split_text_for_tts(
    text: str,
    target_words: int = 145,
    max_chars: int = 1100,
) -> list[str]:
    """Split narration into sentence-aware requests of roughly one minute."""
    return split_by_limits(text, max_words=target_words, max_chars=max_chars)


@dataclass(slots=True)
class RenderOptions:
    output_path: Path
    background_files: list[Path] = field(default_factory=list)
    width: int = 1080
    height: int = 1920
    fps: int = 30
    caption_words: int = 6
    caption_position: float = 0.69
    caption_style: str = "Karaoke"
    align_captions: bool = True
    whisper_model: str = "tiny.en"
    min_clip_seconds: float = 12.0
    max_clip_seconds: float = 24.0
    part_seconds: int = 90
    profanity_mode: str = "Uncensored"
    voice_speed: float = 1.0
    video_speed: float = 1.0
    video_bitrate: str | None = None
    audio_bitrate: str = "192k"
    music_file: Path | None = None
    auto_music: bool = False
    music_volume: float = 0.10
    auto_pick_background: bool = False
    video_category: str = "All"
    scene_aware: bool = True
    resume_existing: bool = True
    preset: str = "medium"
    keep_work_files: bool = False
    smart_match_background: bool = True
    audio_polish: bool = True
    bake_tiktok_cover: bool = True
    series_id: str = ""
    series_index: int = 1
    series_total: int = 1


def _notify(callback: ProgressCallback | None, value: float, message: str) -> None:
    if callback:
        callback(max(0.0, min(1.0, value)), message)


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    names = ["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]
    candidates = [windows / name for name in names]
    candidates.extend(
        [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def _format_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def build_reddit_card(post: StoryPost, path: Path, width: int, height: int) -> Path:
    """Create a transparent, modern Reddit-style overlay card."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = width / 1080.0
    margin = int(52 * scale)
    card_width = width - margin * 2
    title_size = max(24, int(45 * scale))
    sub_size = max(18, int(31 * scale))
    meta_size = max(15, int(24 * scale))
    title_font = _font(title_size)
    sub_font = _font(sub_size)
    meta_font = _font(meta_size, bold=False)
    pad = int(36 * scale)
    max_title_width = card_width - pad * 2
    title_lines: list[str] = []
    current_line: list[str] = []
    for word in post.title.split():
        candidate = " ".join([*current_line, word])
        box = draw.textbbox((0, 0), candidate, font=title_font)
        if current_line and box[2] - box[0] > max_title_width:
            title_lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    if current_line:
        title_lines.append(" ".join(current_line))
    title_lines = title_lines[:6] or ["Untitled story"]
    card_height = int((235 + len(title_lines) * 62) * scale)
    card_height = max(int(420 * scale), min(card_height, int(height * 0.58)))
    left = margin
    top = (height - card_height) // 2
    right = left + card_width
    bottom = top + card_height

    # Soft shadow and card.
    for spread, alpha in ((18, 18), (10, 30), (4, 48)):
        draw.rounded_rectangle(
            (left - spread, top - spread, right + spread, bottom + spread),
            radius=int(34 * scale), fill=(0, 0, 0, alpha)
        )
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=int(28 * scale),
        fill=(22, 24, 31, 246), outline=(72, 76, 94, 255), width=max(1, int(2 * scale))
    )

    icon_r = int(24 * scale)
    icon_x = left + pad + icon_r
    icon_y = top + pad + icon_r
    draw.ellipse((icon_x - icon_r, icon_y - icon_r, icon_x + icon_r, icon_y + icon_r), fill=(255, 69, 0, 255))
    draw.ellipse((icon_x - icon_r // 2, icon_y - icon_r // 3, icon_x + icon_r // 2, icon_y + icon_r // 3), fill=(255, 255, 255, 255))

    text_x = icon_x + icon_r + int(18 * scale)
    draw.text((text_x, top + pad - int(5 * scale)), post.subreddit, font=sub_font, fill=(248, 249, 252, 255))
    draw.text(
        (text_x, top + pad + sub_size + int(4 * scale)),
        f"u/{post.author}  •  story time",
        font=meta_font, fill=(157, 161, 178, 255)
    )

    y = icon_y + icon_r + int(36 * scale)
    for line in title_lines:
        draw.text((left + pad, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += title_size + int(13 * scale)

    footer_y = bottom - pad - meta_size
    accent = (255, 99, 71, 255)
    muted = (194, 197, 209, 255)
    draw.text((left + pad, footer_y), f"▲  {_format_count(post.score)}", font=meta_font, fill=accent)
    draw.text((left + pad + int(210 * scale), footer_y), f"●  {_format_count(post.comments)} comments", font=meta_font, fill=muted)
    image.save(path)
    return path


def _measure_audio(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as source:
            return source.getnframes() / float(source.getframerate())
    clip = AudioFileClip(str(path))
    try:
        return float(clip.duration)
    finally:
        clip.close()


def estimate_word_boundaries(text: str, duration: float) -> list[dict[str, float | str]]:
    words = text.split()
    if not words:
        return []
    weights: list[int] = []
    for word in words:
        weight = max(1, len(re.sub(r"\W", "", word)))
        if re.search(r"[,;:]$", word):
            weight += 1
        if re.search(r"[.!?]$", word):
            weight += 3
        weights.append(weight)
    total = float(sum(weights))
    cursor = 0.0
    boundaries: list[dict[str, float | str]] = []
    for word, weight in zip(words, weights):
        word_duration = duration * weight / total
        boundaries.append({"text": word, "start": cursor, "duration": word_duration})
        cursor += word_duration
    return boundaries


def _windows_sapi(text: str, output_path: Path) -> Path:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        fallback = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        powershell = str(fallback) if fallback.exists() else None
    if not powershell:
        raise RuntimeError("Windows PowerShell is unavailable for local voice fallback.")

    environment = os.environ.copy()
    environment["AUTOTOK_TTS_TEXT"] = text
    environment["AUTOTOK_TTS_OUTPUT"] = str(output_path)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "try { $v=$s.GetInstalledVoices() | Where-Object {$_.VoiceInfo.Culture.Name -like 'en-*'} | Select-Object -First 1; "
        "if($v){$s.SelectVoice($v.VoiceInfo.Name)}; $s.Rate=1; "
        "$s.SetOutputToWaveFile($env:AUTOTOK_TTS_OUTPUT); $s.Speak($env:AUTOTOK_TTS_TEXT) } finally {$s.Dispose()}"
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=environment, capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 100:
        details = (result.stderr or result.stdout or "no audio produced").strip()
        raise RuntimeError(f"Windows voice fallback failed: {details}")
    return output_path


def _provider_label(provider: str, cached: bool = False) -> str:
    labels = {"flux": "OpenRouter Flux", "azure": "Azure Speech", "elevenlabs": "ElevenLabs"}
    label = labels.get(provider, "OpenRouter Flux")
    return f"{label} (cached)" if cached else label


def _key_candidates(config: OpenRouterConfig) -> list[str]:
    keys = list(config.keys_for_provider())
    mode = config.key_mode_for_provider()
    if len(keys) < 2 or mode == "single":
        return keys[:1]
    if mode == "random":
        random.shuffle(keys)
        return keys
    cursor_key = (config.provider, tuple(keys))
    start = _KEY_ROTATION_CURSOR.get(cursor_key, 0) % len(keys)
    return keys[start:] + keys[:start]


def _advance_rotation(config: OpenRouterConfig, exhausted_key: str) -> None:
    if config.key_mode_for_provider() != "rotate":
        return
    keys = list(config.keys_for_provider())
    if exhausted_key in keys:
        _KEY_ROTATION_CURSOR[(config.provider, tuple(keys))] = (keys.index(exhausted_key) + 1) % len(keys)


def _request_tts(text: str, config: OpenRouterConfig, speed: float, api_key: str):
    if config.provider == "azure":
        rate = int(round((max(0.5, min(1.5, speed)) - 1.0) * 100))
        ssml = (
            "<speak version='1.0' xml:lang='en-US'>"
            f"<voice name='{escape(config.azure_voice)}'><prosody rate='{rate:+d}%'>"
            f"{escape(text)}</prosody></voice></speak>"
        )
        return requests.post(
            f"https://{config.azure_region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
                "User-Agent": "AutoTok Story Studio",
            },
            data=ssml.encode("utf-8"), timeout=(30, 600),
        )
    if config.provider == "elevenlabs":
        return requests.post(
            f"{ELEVENLABS_TTS_URL}/{quote(config.elevenlabs_voice, safe='')}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": config.elevenlabs_model,
                "voice_settings": {"speed": max(0.7, min(1.2, speed))},
            },
            timeout=(30, 600),
        )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "AutoTok Story Studio",
    }
    if config.http_referer:
        headers["HTTP-Referer"] = config.http_referer
    return requests.post(
        OPENROUTER_TTS_URL,
        headers=headers,
        json={
            "model": config.model,
            "input": text,
            "voice": config.voice,
            "response_format": "mp3",
            "speed": max(0.5, min(1.5, speed)),
        },
        timeout=(30, 600),
    )


def _response_error(response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text[:400]


def synthesize_speech(
    text: str,
    config: OpenRouterConfig,
    output_stem: Path,
    allow_local_fallback: bool = True,
    fallback_notice: Callable[[str], None] | None = None,
    speed: float = 1.0,
) -> tuple[Path, str]:
    """Synthesize narration. Returns path and engine label."""
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_seed = json.dumps(
        {
            "text": text,
            "provider": config.provider,
            "model": config.elevenlabs_model if config.provider == "elevenlabs" else config.model,
            "voice": (
                config.azure_voice if config.provider == "azure"
                else config.elevenlabs_voice if config.provider == "elevenlabs"
                else config.voice
            ),
            "region": config.azure_region if config.provider == "azure" else "",
            "speed": round(speed, 2),
        },
        sort_keys=True,
    ).encode("utf-8")
    cache_id = hashlib.sha256(cache_seed).hexdigest()
    flux_cache = TTS_CACHE_DIR / f"{cache_id}.mp3"
    if config.configured:
        if flux_cache.exists() and flux_cache.stat().st_size > 100:
            return flux_cache, _provider_label(config.provider, cached=True)
        try:
            provider_label = _provider_label(config.provider)
            candidates = _key_candidates(config)
            for index, api_key in enumerate(candidates):
                response = _request_tts(text, config, speed, api_key)
                if response.ok:
                    if len(response.content) < 100:
                        raise RuntimeError(f"{provider_label} returned an empty audio file.")
                    flux_cache.write_bytes(response.content)
                    return flux_cache, provider_label
                error = RuntimeError(f"{provider_label} HTTP {response.status_code}: {_response_error(response)}")
                can_retry = response.status_code in _KEY_LIMIT_STATUS_CODES and index + 1 < len(candidates)
                if not can_retry:
                    raise error
                _advance_rotation(config, api_key)
            raise RuntimeError(f"{provider_label} could not generate speech with the configured keys.")
        except Exception as openrouter_error:
            if not allow_local_fallback:
                raise
            if fallback_notice:
                fallback_notice(str(openrouter_error))

    if not allow_local_fallback:
        raise RuntimeError(f"Add a {_provider_label(config.provider)} API key before rendering.")
    windows_id = hashlib.sha256(("windows:" + text).encode("utf-8")).hexdigest()
    windows_cache = TTS_CACHE_DIR / f"{windows_id}.wav"
    if not windows_cache.exists() or windows_cache.stat().st_size < 100:
        _windows_sapi(text, windows_cache)
    return windows_cache, "Windows voice fallback"


def synthesize_speech_parts(
    text: str,
    config: OpenRouterConfig,
    work_dir: Path,
    progress: ProgressCallback | None = None,
    speed: float = 1.0,
) -> tuple[list[Path], str, list[str]]:
    """Synthesize one-minute parts so long stories stay below API limits."""
    parts = split_text_for_tts(text)
    if not parts:
        raise ValueError("The story contains no narratable text.")

    outputs: list[Path] = []
    engines: list[str] = []
    active_config = config
    for index, part in enumerate(parts, start=1):
        progress_value = 0.23 + 0.17 * ((index - 1) / len(parts))
        _notify(progress, progress_value, f"Dubbing story part {index}/{len(parts)} (~1 minute each)")
        output, engine = synthesize_speech(
            make_spoken_text(part),
            active_config,
            work_dir / f"story_{index:03d}",
            fallback_notice=lambda reason, value=progress_value: _notify(
                progress, value, f"Cloud voice failed: {reason} — using Windows voice"
            ),
            speed=speed,
        )
        outputs.append(output)
        engines.append(engine)
        if engine == "Windows voice fallback":
            active_config = OpenRouterConfig()

    remote = [engine for engine in engines if engine != "Windows voice fallback"]
    label = remote[0] if len(remote) == len(engines) and len(set(remote)) == 1 else "Windows voice fallback"
    return outputs, label, parts


def build_caption_chunks(text: str, duration: float, words_per_chunk: int) -> list[dict[str, float | str]]:
    boundaries = estimate_word_boundaries(text, duration)
    chunks: list[dict[str, float | str]] = []
    for start_index in range(0, len(boundaries), max(2, words_per_chunk)):
        group = boundaries[start_index : start_index + max(2, words_per_chunk)]
        first = group[0]
        last = group[-1]
        chunks.append(
            {
                "text": " ".join(str(item["text"]) for item in group),
                "start": float(first["start"]),
                "end": float(last["start"]) + float(last["duration"]),
            }
        )
    return chunks


def build_caption_timeline(
    words: list[dict[str, float | str]],
    words_per_chunk: int,
    style: str,
) -> tuple[list[dict[str, float | str | int | None]], list[dict[str, float | str]]]:
    events: list[dict[str, float | str | int | None]] = []
    chunks: list[dict[str, float | str]] = []
    chunk_size = max(2, words_per_chunk)
    for start_index in range(0, len(words), chunk_size):
        group = words[start_index : start_index + chunk_size]
        text = " ".join(str(word["text"]) for word in group)
        chunk_start = float(group[0]["start"])
        chunk_end = float(group[-1]["end"])
        chunks.append({"text": text, "start": chunk_start, "end": chunk_end})
        if style == "Classic":
            events.append({"text": text, "highlight": None, "start": chunk_start, "end": chunk_end})
            continue
        for local_index, word in enumerate(group):
            next_start = float(group[local_index + 1]["start"]) if local_index + 1 < len(group) else chunk_end
            events.append(
                {
                    "text": text,
                    "highlight": local_index,
                    "start": float(word["start"]),
                    "end": max(float(word["end"]), next_start),
                }
            )
    return events, chunks


def render_caption(
    text: str,
    path: Path,
    output_width: int,
    style: str = "Karaoke",
    highlight_index: int | None = None,
) -> tuple[int, int]:
    scale = output_width / 1080.0
    font_size = max(27, int(62 * scale))
    font = _font(font_size)
    canvas_width = output_width
    side_margin = max(26, int(output_width * 0.075))
    stroke = max(2, int(7 * scale))
    available_width = canvas_width - side_margin * 2 - stroke * 2
    dummy = Image.new("RGBA", (canvas_width, 20), (0, 0, 0, 0))
    measure = ImageDraw.Draw(dummy)

    # Character-count wrapping clips wide uppercase glyphs (especially M/W).
    # Build each line against its real rendered pixel width instead.
    words = text.upper().split()
    lines: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for index, word in enumerate(words):
        candidate = " ".join(item[1] for item in [*current, (index, word)])
        box = measure.textbbox((0, 0), candidate, font=font, stroke_width=stroke)
        if current and box[2] - box[0] > available_width:
            lines.append(current)
            current = [(index, word)]
        else:
            current.append((index, word))
    if current:
        lines.append(current)
    if not lines:
        lines = [[(0, text.upper())]]

    line_strings = [" ".join(word for _, word in line) for line in lines]
    boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=max(2, int(5 * scale))) for line in line_strings]
    line_height = max(box[3] - box[1] for box in boxes)
    gap = max(6, int(11 * scale))
    vertical_pad = max(16, int(24 * scale))
    canvas_height = vertical_pad * 2 + len(lines) * line_height + (len(lines) - 1) * gap
    image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    y = vertical_pad
    space_width = float(measure.textlength(" ", font=font))
    highlight_color = (255, 220, 64, 255) if style == "Karaoke" else (148, 112, 255, 255)
    for line in lines:
        widths = [float(measure.textlength(word, font=font)) for _, word in line]
        total_width = sum(widths) + space_width * max(0, len(line) - 1)
        x = (canvas_width - total_width) / 2
        for (word_index, word), word_width in zip(line, widths):
            fill = highlight_color if word_index == highlight_index else (255, 255, 255, 255)
            draw.text(
                (x, y), word, font=font, fill=fill, stroke_width=stroke,
                stroke_fill=(8, 9, 13, 255), anchor="la"
            )
            x += word_width + space_width
        y += line_height + gap
    image.save(path)
    return image.size


def crop_to_vertical(clip: VideoFileClip, width: int, height: int):
    source_aspect = clip.w / float(clip.h)
    target_aspect = width / float(height)
    if source_aspect > target_aspect:
        crop_width = clip.h * target_aspect
        clip = clip.crop(width=crop_width, height=clip.h, x_center=clip.w / 2, y_center=clip.h / 2)
    else:
        crop_height = clip.w / target_aspect
        clip = clip.crop(width=clip.w, height=crop_height, x_center=clip.w / 2, y_center=clip.h / 2)
    return clip.resize(newsize=(width, height))


def _gradient_background(path: Path, width: int, height: int) -> Path:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        for x in range(width):
            glow = max(0.0, 1.0 - math.dist((x / width, y / height), (0.78, 0.22)) * 2.0)
            pixels[x, y] = (
                int(11 + 24 * glow),
                int(13 + 15 * glow + 8 * ratio),
                int(24 + 54 * glow + 16 * ratio),
            )
    image.save(path, quality=92)
    return path


def build_background(
    files: list[Path], duration: float, width: int, height: int, min_seconds: float,
    max_seconds: float, work_dir: Path, log: ProgressCallback | None = None,
    scene_aware: bool = True,
):
    valid = [path for path in files if path.exists() and path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}]
    if not valid:
        gradient = _gradient_background(work_dir / "background.jpg", width, height)
        return ImageClip(str(gradient)).set_duration(duration)

    segments = []
    sources = []
    covered = 0.0
    pool: list[Path] = []
    failures = 0
    while covered < duration:
        if not pool:
            pool = valid.copy()
            random.shuffle(pool)
        path = pool.pop()
        try:
            source = VideoFileClip(str(path))
            sources.append(source)
            remaining = duration - covered
            length = min(random.uniform(min_seconds, max_seconds), source.duration, remaining)
            if length <= 0.04:
                continue
            max_start = max(0.0, source.duration - length)
            start = choose_scene_start(path, max_start, enabled=scene_aware) if max_start else 0.0
            segment = crop_to_vertical(source.subclip(start, start + length), width, height).without_audio()
            segments.append(segment)
            covered += length
            failures = 0
            _notify(log, 0.50 + min(0.10, (covered / duration) * 0.10), f"Cutting background: {covered:.0f}/{duration:.0f}s")
        except Exception as exc:
            failures += 1
            if failures >= len(valid) * 2:
                for source in sources:
                    source.close()
                raise RuntimeError(f"The selected background videos could not be opened: {exc}") from exc

    background = concatenate_videoclips(segments, method="compose").subclip(0, duration)
    # Keep source handles alive through rendering and close them with the composed clip.
    background._autotok_sources = sources  # type: ignore[attr-defined]
    return background


def _create_thumbnail(card_path: Path, output_path: Path, width: int, height: int) -> Path:
    background_path = output_path.parent / f".{output_path.stem}_thumb_bg.jpg"
    _gradient_background(background_path, width, height)
    background = Image.open(background_path).convert("RGBA")
    card = Image.open(card_path).convert("RGBA")
    background.alpha_composite(card)
    background.convert("RGB").save(output_path, quality=94)
    try:
        background_path.unlink()
    except OSError:
        pass
    return output_path


def _posting_package(
    post: StoryPost,
    output: Path,
    card_path: Path,
    caption_chunks: list[dict[str, float | str]],
    intro_duration: float,
    width: int,
    height: int,
    series_id: str = "",
    series_index: int = 1,
    series_total: int = 1,
    cover_baked: bool = False,
    production: dict | None = None,
) -> None:
    write_srt(caption_chunks, output.with_suffix(".srt"), offset=intro_duration)
    thumbnail = output.with_name(f"{output.stem}_thumbnail.jpg")
    _create_thumbnail(card_path, thumbnail, width, height)
    subreddit_tag = re.sub(r"\W+", "", post.subreddit.removeprefix("r/"))
    from .studio import generate_hook_variants

    hooks = generate_hook_variants(post)
    package = {
        "title": post.title[:100],
        "hook": f"This {post.subreddit} story gets wild fast…",
        "description": f"{post.title}\n\nSource: {post.permalink or post.subreddit}",
        "hashtags": [f"#{subreddit_tag}", "#redditstories", "#storytime", "#shorts", "#fyp"],
        "source": post.permalink,
        "video": str(output),
        "captions": str(output.with_suffix(".srt")),
        "thumbnail": str(thumbnail),
        "cover_baked": cover_baked,
        "hook_variants": hooks,
        "series": {
            "id": series_id,
            "part": series_index,
            "total": series_total,
            "previous_teaser": "Previously in this story…" if series_index > 1 else "",
            "next_teaser": "Follow for the next part." if series_index < series_total else "",
        },
        "rights": {
            "reddit_source": post.permalink or post.subreddit,
            "footage_confirmed": False,
            "music_confirmed": False,
            "voice_confirmed": False,
        },
        "production": production or {},
    }
    output.with_name(f"{output.stem}_post.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    output.with_name(f"{output.stem}_post.txt").write_text(
        f"TITLE\n{package['title']}\n\nHOOK\n{package['hook']}\n\nDESCRIPTION\n{package['description']}\n\nHASHTAGS\n{' '.join(package['hashtags'])}\n",
        encoding="utf-8",
    )


def generate_preview_image(
    post: StoryPost,
    output_path: Path,
    background_path: Path | None = None,
    caption_position: float = 0.66,
    caption_style: str = "Karaoke",
    show_safe_zones: bool = True,
    width: int = 405,
    height: int = 720,
) -> Path:
    """Create a fast still preview without calling TTS or rendering video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if background_path and background_path.exists():
        source = VideoFileClip(str(background_path), audio=False)
        try:
            frame = Image.fromarray(source.get_frame(min(2.0, max(0.0, source.duration / 3)))).convert("RGB")
        finally:
            source.close()
        source_aspect = frame.width / frame.height
        target_aspect = width / height
        if source_aspect > target_aspect:
            new_width = int(frame.height * target_aspect)
            left = (frame.width - new_width) // 2
            frame = frame.crop((left, 0, left + new_width, frame.height))
        else:
            new_height = int(frame.width / target_aspect)
            top = (frame.height - new_height) // 2
            frame = frame.crop((0, top, frame.width, top + new_height))
        canvas = frame.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
    else:
        background_file = output_path.with_name("preview_background.jpg")
        _gradient_background(background_file, width, height)
        canvas = Image.open(background_file).convert("RGBA")

    preview_words = " ".join(clean_story_for_narration(post.body).split()[:6]) or "YOUR CAPTIONS APPEAR HERE"
    caption_file = output_path.with_name("preview_caption.png")
    render_caption(preview_words, caption_file, width, caption_style, 1 if caption_style != "Classic" else None)
    caption = Image.open(caption_file).convert("RGBA")
    y = int(height * max(0.30, min(0.78, caption_position)))
    canvas.alpha_composite(caption, ((width - caption.width) // 2, min(height - caption.height, y)))

    if show_safe_zones:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        safe_left, safe_right = int(width * 0.06), int(width * 0.84)
        safe_top, safe_bottom = int(height * 0.12), int(height * 0.80)
        draw.rounded_rectangle(
            (safe_left, safe_top, safe_right, safe_bottom), radius=8,
            outline=(255, 193, 75, 210), width=2,
        )
        draw.text((safe_left + 8, safe_top + 6), "SAFE AREA", font=_font(max(10, width // 32)), fill=(255, 210, 100, 230))
        canvas.alpha_composite(overlay)
    canvas.convert("RGB").save(output_path, quality=92)
    for temp_path in (output_path.with_name("preview_background.jpg"), caption_file):
        if temp_path != output_path:
            try:
                temp_path.unlink()
            except OSError:
                pass
    return output_path


def render_video(
    post: StoryPost,
    tts: OpenRouterConfig,
    options: RenderOptions,
    progress: ProgressCallback | None = None,
) -> Path:
    if not post.title.strip() or not post.body.strip():
        raise ValueError("A title and story are required before rendering.")
    if len(post.body.split()) < 8:
        raise ValueError("The story is too short. Add at least a few sentences.")

    story_text = clean_story_for_narration(post.body, options.profanity_mode)

    output = options.output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output.parent / f".{output.stem}_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    created_clips = []

    try:
        _notify(progress, 0.03, "Designing the Reddit story card")
        card_path = build_reddit_card(post, work_dir / "reddit_card.png", options.width, options.height)

        _notify(progress, 0.10, "Dubbing the title with Flux TTS")
        intro_text = f"{spoken_subreddit_name(post.subreddit)}. {make_spoken_text(post.title)}."
        intro_path, intro_engine = synthesize_speech(
            intro_text,
            tts,
            work_dir / "intro",
            fallback_notice=lambda reason: _notify(
                progress, 0.16, f"Flux failed: {reason} — using Windows voice"
            ),
            speed=options.voice_speed,
        )

        _notify(progress, 0.23, f"Dubbing the story with {intro_engine}")
        # If the short intro request was rejected, do not send the much longer
        # story through the same failing configuration a second time.
        story_tts = tts if intro_engine != "Windows voice fallback" else OpenRouterConfig()
        story_paths, story_engine, story_text_parts = synthesize_speech_parts(
            story_text,
            story_tts,
            work_dir,
            progress,
            speed=options.voice_speed,
        )
        intro_audio = AudioFileClip(str(intro_path))
        story_part_clips = [AudioFileClip(str(path)) for path in story_paths]
        story_audio = concatenate_audioclips(story_part_clips)
        created_clips.extend([intro_audio, *story_part_clips, story_audio])
        intro_duration = intro_audio.duration + 0.35
        total_duration = intro_duration + story_audio.duration

        _notify(progress, 0.42, "Timing and styling captions")
        timed_words: list[dict[str, float | str]] = []
        if options.align_captions:
            try:
                aligned = align_audio_parts(
                    story_paths,
                    [make_spoken_text(part) for part in story_text_parts],
                    [clip.duration for clip in story_part_clips],
                    options.whisper_model,
                    status=lambda message: _notify(progress, 0.44, message),
                )
                timed_words = [
                    {"text": word.text, "start": word.start, "end": word.end}
                    for word in aligned
                ]
            except Exception as alignment_error:
                _notify(progress, 0.44, f"Whisper alignment unavailable: {alignment_error}; using estimated timing")
        if not timed_words:
            estimated = estimate_word_boundaries(story_text, story_audio.duration)
            timed_words = [
                {
                    "text": str(word["text"]),
                    "start": float(word["start"]),
                    "end": float(word["start"]) + float(word["duration"]),
                }
                for word in estimated
            ]

        caption_events, chunks = build_caption_timeline(timed_words, options.caption_words, options.caption_style)
        caption_clips = []
        for index, event in enumerate(caption_events):
            caption_path = work_dir / f"caption_{index:04d}.png"
            render_caption(
                str(event["text"]), caption_path, options.width,
                style=options.caption_style,
                highlight_index=event["highlight"] if isinstance(event["highlight"], int) else None,
            )
            caption = (
                ImageClip(str(caption_path))
                .set_start(intro_duration + float(event["start"]))
                .set_duration(max(0.05, float(event["end"]) - float(event["start"])))
                .set_position(("center", int(options.height * options.caption_position)))
            )
            caption_clips.append(caption)
            created_clips.append(caption)

        _notify(progress, 0.50, "Building the vertical background")
        background = build_background(
            options.background_files, total_duration, options.width, options.height,
            options.min_clip_seconds, options.max_clip_seconds, work_dir, progress,
            scene_aware=options.scene_aware,
        )
        created_clips.append(background)

        card = (
            ImageClip(str(card_path))
            .set_start(0)
            .set_duration(intro_duration)
            .crossfadein(0.18)
            .crossfadeout(0.22)
        )
        created_clips.append(card)
        narration_audio = CompositeAudioClip(
            [intro_audio.set_start(0), story_audio.set_start(intro_duration)]
        ).set_duration(total_duration).set_fps(44_100)
        if options.audio_polish:
            narration_audio = audio_fx.audio_normalize(narration_audio)
            narration_audio = audio_fx.audio_fadein(narration_audio, 0.05)
            narration_audio = audio_fx.audio_fadeout(narration_audio, 0.18)
        created_clips.append(narration_audio)

        music_path = options.music_file or (choose_music() if options.auto_music else None)
        if music_path and music_path.exists():
            _notify(progress, 0.57, f"Mixing music: {music_path.name}")
            music_source = AudioFileClip(str(music_path))
            music_loop = audio_fx.audio_loop(music_source, duration=total_duration)
            music_loop = audio_fx.audio_fadein(music_loop, 0.8)
            music_loop = audio_fx.audio_fadeout(music_loop, 1.2).volumex(max(0.0, min(0.5, options.music_volume)))
            audio = CompositeAudioClip([music_loop, narration_audio]).set_duration(total_duration)
            created_clips.extend([music_source, music_loop, audio])
        else:
            audio = narration_audio
        layers = [background, card, *caption_clips]
        publish_duration = total_duration
        if options.bake_tiktok_cover:
            thumbnail_path = output.with_name(f"{output.stem}_thumbnail.jpg")
            _create_thumbnail(card_path, thumbnail_path, options.width, options.height)
            cover_clip = ImageClip(str(thumbnail_path)).set_start(total_duration).set_duration(0.65)
            layers.append(cover_clip)
            created_clips.append(cover_clip)
            publish_duration += 0.65
        base_final = CompositeVideoClip(layers, size=(options.width, options.height)).set_duration(publish_duration).set_audio(audio.set_duration(publish_duration))
        created_clips.append(base_final)
        playback_speed = max(0.5, min(2.0, options.video_speed))
        final = base_final.fx(video_fx.speedx, factor=playback_speed)
        created_clips.append(final)

        final_duration = publish_duration / playback_speed
        _notify(progress, 0.62, f"Rendering {final_duration:.1f}s video at {playback_speed:.2f}× with {story_engine}")
        final.write_videofile(
            str(output), fps=options.fps, codec="libx264", audio_codec="aac",
            threads=max(2, min(6, os.cpu_count() or 4)), preset=options.preset,
            bitrate=options.video_bitrate, audio_bitrate=options.audio_bitrate,
            temp_audiofile=str(work_dir / "render_audio.m4a"), remove_temp=True,
            logger=None,
        )
        adjusted_chunks = [
            {
                **chunk,
                "start": float(chunk["start"]) / playback_speed,
                "end": float(chunk["end"]) / playback_speed,
            }
            for chunk in chunks
        ]
        _posting_package(
            post, output, card_path, adjusted_chunks, intro_duration / playback_speed, options.width, options.height,
            options.series_id, options.series_index, options.series_total,
            options.bake_tiktok_cover,
            {
                "caption_style": options.caption_style,
                "caption_words": options.caption_words,
                "part_length_seconds": options.part_seconds,
                "voice": tts.azure_voice if tts.provider == "azure" else tts.voice,
                "voice_provider": tts.provider,
                "voice_speed": options.voice_speed,
                "video_speed": playback_speed,
                "video_bitrate": options.video_bitrate,
                "audio_polish": options.audio_polish,
                "backgrounds": [str(path) for path in options.background_files],
                "video_category": options.video_category,
            },
        )
        _notify(progress, 1.0, f"Finished: {output.name}")
        return output
    finally:
        for clip in reversed(created_clips):
            try:
                clip.close()
            except Exception:
                pass
            for source in getattr(clip, "_autotok_sources", []):
                try:
                    source.close()
                except Exception:
                    pass
        if work_dir.exists() and not options.keep_work_files:
            shutil.rmtree(work_dir, ignore_errors=True)


def render_story_series(
    post: StoryPost,
    tts: OpenRouterConfig,
    options: RenderOptions,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Render one video or an automatically numbered short-form series."""
    cleaned = clean_story_for_narration(post.body, options.profanity_mode)
    # Splitting happens before rendering. Account for the final playback-speed
    # transform so a requested 60-second part still lands near 60 seconds.
    split_target = round(options.part_seconds * max(0.5, min(2.0, options.video_speed)))
    story_parts = split_story_parts(cleaned, split_target)
    outputs: list[Path] = []
    used_backgrounds: set[Path] = set()
    candidates = options.background_files
    if options.auto_pick_background:
        category = options.video_category
        if options.smart_match_background and category == "All":
            category = choose_story_category(f"{post.title} {post.body}", video_categories())
            if category != "All":
                _notify(progress, 0.01, f"Matched story to {category} footage")
        candidates = scan_videos(category)

    total_parts = len(story_parts)
    series_id = hashlib.sha256(f"{post.permalink}|{post.title}".encode("utf-8")).hexdigest()[:12]
    cut_min, cut_max = recommended_cut_range(f"{post.title} {post.body}")
    for index, body in enumerate(story_parts, start=1):
        if total_parts > 1:
            suffix = f"_part_{index:02d}_of_{total_parts:02d}"
            output = options.output_path.with_name(f"{options.output_path.stem}{suffix}{options.output_path.suffix}")
            title = f"{post.title} — Part {index} of {total_parts}"
        else:
            output = options.output_path
            title = post.title

        backgrounds = candidates
        if options.auto_pick_background:
            chosen = choose_smart_video(candidates, used_backgrounds)
            backgrounds = [chosen] if chosen else []
            if chosen:
                used_backgrounds.add(chosen)

        part_post = StoryPost(
            subreddit=post.subreddit,
            title=title,
            body=body,
            author=post.author,
            score=post.score,
            comments=post.comments,
            permalink=post.permalink,
        )
        part_options = replace(
            options,
            output_path=output,
            background_files=backgrounds,
            part_seconds=0,
            auto_pick_background=False,
            min_clip_seconds=cut_min,
            max_clip_seconds=cut_max,
            series_id=series_id,
            series_index=index,
            series_total=total_parts,
        )

        fingerprint_data = {
            "post": asdict(part_post),
            "settings": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(part_options).items()
                if key not in {"output_path", "background_files"}
            },
            "backgrounds": [str(path.resolve()) for path in backgrounds],
            "model": tts.model,
            "voice": tts.azure_voice if tts.provider == "azure" else tts.voice,
            "voice_provider": tts.provider,
        }
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode("utf-8")).hexdigest()
        manifest_path = output.with_name(f"{output.stem}_render.json")
        if part_options.resume_existing and output.exists() and manifest_path.exists():
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                previous = {}
            if previous.get("fingerprint") == fingerprint:
                _notify(progress, index / total_parts, f"Reusing completed part {index}/{total_parts}")
                outputs.append(output)
                continue

        def part_progress(value: float, message: str, part=index) -> None:
            overall = ((part - 1) + value) / total_parts
            _notify(progress, overall, f"Part {part}/{total_parts}: {message}" if total_parts > 1 else message)

        rendered = render_video(part_post, tts, part_options, part_progress)
        manifest_path.write_text(json.dumps({"fingerprint": fingerprint}, indent=2), encoding="utf-8")
        outputs.append(rendered)
    return outputs
