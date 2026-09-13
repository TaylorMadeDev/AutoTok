from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import imageio_ffmpeg
from moviepy.editor import VideoFileClip
from yt_dlp import YoutubeDL

from .config import APP_DIR
from .library import VIDEO_DIR, VIDEO_EXTENSIONS


ProgressCallback = Callable[[float, str], None]
HARVEST_WORK_DIR = APP_DIR / ".autotok" / "harvester"
DEFAULT_HARVEST_PLAYLIST = "https://www.youtube.com/watch?v=xKRNDalWE-E&list=PLJVvekmbcMxBCh1Cb997PA2hsrxmxdB6G"
HARVEST_SOURCES = {
    "Minecraft": DEFAULT_HARVEST_PLAYLIST,
    "Subway Surfers": "",
    "ASMR": "",
}


@dataclass(frozen=True, slots=True)
class HarvestResult:
    title: str
    source_url: str
    segments: tuple[Path, ...]


def _safe_name(value: str, fallback: str = "harvested_video") -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")[:70]
    return clean or fallback


def _validate_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid video URL: {value}")
    return value.strip()


def _playlist_entry_url(entry: dict) -> str | None:
    for key in ("webpage_url", "original_url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
    value = entry.get("url")
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    identifier = entry.get("id")
    if isinstance(identifier, str) and identifier:
        return f"https://www.youtube.com/watch?v={identifier}"
    return None


def playlist_video_urls(playlist_url: str) -> list[str]:
    source_url = _validate_url(playlist_url)
    options = {
        "extract_flat": "in_playlist",
        "lazy_playlist": False,
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(source_url, download=False)
    if not isinstance(info, dict):
        raise RuntimeError("The playlist information could not be read.")
    entries = info.get("entries")
    if not entries:
        direct_url = _playlist_entry_url(info)
        return [direct_url] if direct_url else [source_url]
    urls = [_playlist_entry_url(entry) for entry in entries if isinstance(entry, dict)]
    clean_urls = [url for url in urls if url]
    if not clean_urls:
        raise RuntimeError("The playlist does not contain any available videos.")
    return clean_urls


def split_video(
    source: Path,
    output_dir: Path,
    segment_seconds: int = 60,
    progress: ProgressCallback | None = None,
    include_audio: bool = True,
) -> list[Path]:
    if not source.exists():
        raise FileNotFoundError(source)
    if not 1 <= segment_seconds <= 3600:
        raise ValueError("Segment length must be between 1 and 3600 seconds.")
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = 0.0
    clip = VideoFileClip(str(source), audio=False)
    try:
        duration = float(clip.duration or 0)
    finally:
        clip.close()
    if duration <= 0:
        raise RuntimeError("The downloaded video has no readable duration.")

    if progress:
        progress(0.55, f"Cutting {duration:.0f}s into {segment_seconds}-second clips")
    output_template = output_dir / "clip_%03d.mp4"
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0:v:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
    ]
    if include_audio:
        command.extend(["-map", "0:a?", "-c:a", "aac", "-b:a", "160k"])
    else:
        command.append("-an")
    command.extend([
        "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
        "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
        str(output_template),
    ])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=max(300, int(duration * 4)))
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "FFmpeg could not split the video.").strip())
    segments = sorted(output_dir.glob("clip_*.mp4"))
    if not segments:
        raise RuntimeError("FFmpeg completed without producing any video clips.")
    return segments


def harvest_video(
    url: str,
    category: str = "Harvested",
    segment_seconds: int = 60,
    progress: ProgressCallback | None = None,
    include_audio: bool = True,
) -> HarvestResult:
    source_url = _validate_url(url)
    category_name = _safe_name(category, "Harvested")
    job_dir = HARVEST_WORK_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=False)
    try:
        if progress:
            progress(0.02, "Reading video information")

        def download_progress(data: dict) -> None:
            if not progress or data.get("status") != "downloading":
                return
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            received = data.get("downloaded_bytes") or 0
            fraction = (received / total) if total else 0.0
            progress(0.08 + min(1.0, fraction) * 0.42, f"Downloading video • {fraction * 100:.0f}%" if total else "Downloading video")

        options = {
            "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "format_sort": ["res:1080", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            "outtmpl": str(job_dir / "source.%(ext)s"),
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [download_progress],
        }
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(source_url, download=True)
        candidates = [path for path in job_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
        if not candidates:
            raise RuntimeError("The download completed but no supported video file was found.")
        downloaded = max(candidates, key=lambda path: path.stat().st_size)
        title = str(info.get("title") or info.get("id") or "Harvested video") if isinstance(info, dict) else "Harvested video"
        identifier = str(info.get("id") or uuid.uuid4().hex[:8]) if isinstance(info, dict) else uuid.uuid4().hex[:8]
        base_destination = VIDEO_DIR / category_name / f"{_safe_name(title)}_{_safe_name(identifier)}"
        destination = base_destination if not base_destination.exists() else base_destination.with_name(f"{base_destination.name}_{uuid.uuid4().hex[:6]}")
        segments = split_video(downloaded, destination, segment_seconds, progress, include_audio)
        if progress:
            progress(1.0, f"Saved {len(segments)} clips to {category_name}")
        return HarvestResult(title=title, source_url=source_url, segments=tuple(segments))
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def harvest_urls(
    urls: list[str],
    category: str = "Harvested",
    segment_seconds: int = 60,
    progress: ProgressCallback | None = None,
    include_audio: bool = True,
) -> list[HarvestResult]:
    clean_urls = [_validate_url(url) for url in urls if url.strip()]
    if not clean_urls:
        raise ValueError("Paste at least one video URL.")
    results: list[HarvestResult] = []
    for index, url in enumerate(clean_urls, start=1):
        def item_progress(value: float, message: str, current=index) -> None:
            if progress:
                progress(((current - 1) + value) / len(clean_urls), f"Video {current}/{len(clean_urls)} • {message}")

        results.append(harvest_video(url, category, segment_seconds, item_progress, include_audio))
    return results


def harvest_playlist(
    playlist_url: str = DEFAULT_HARVEST_PLAYLIST,
    category: str = "Harvested",
    segment_seconds: int = 60,
    progress: ProgressCallback | None = None,
    max_videos: int | None = None,
    include_audio: bool = True,
) -> list[HarvestResult]:
    if progress:
        progress(0.0, "Reading preset playlist")
    urls = playlist_video_urls(playlist_url)
    if max_videos is not None:
        if max_videos < 1:
            raise ValueError("Playlist download count must be at least 1.")
        urls = urls[:max_videos]
    if progress:
        progress(0.0, f"Found {len(urls)} playlist video(s)")
    return harvest_urls(urls, category, segment_seconds, progress, include_audio)


def harvest_sources(
    source_urls: list[str],
    category: str = "Harvested",
    segment_seconds: int = 60,
    progress: ProgressCallback | None = None,
    max_videos: int | None = None,
    include_audio: bool = True,
) -> list[HarvestResult]:
    """Expand one or more video/playlist links, then harvest a bounded selection."""
    clean_sources = [_validate_url(url) for url in source_urls if url.strip()]
    if not clean_sources:
        raise ValueError("Paste at least one video or playlist URL.")
    if max_videos is not None and max_videos < 1:
        raise ValueError("Download count must be at least 1.")

    urls: list[str] = []
    for index, source_url in enumerate(clean_sources, start=1):
        if progress:
            progress(0.0, f"Reading link {index}/{len(clean_sources)}")
        urls.extend(playlist_video_urls(source_url))
        if max_videos is not None and len(urls) >= max_videos:
            break
    if max_videos is not None:
        urls = urls[:max_videos]
    if progress:
        progress(0.0, f"Selected {len(urls)} video(s)")
    return harvest_urls(urls, category, segment_seconds, progress, include_audio)
