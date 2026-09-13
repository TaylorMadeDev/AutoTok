from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from . import __version__
from .config import APP_DIR


GITHUB_REPOSITORY = "TaylorMadeDev/AutoTok"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_DIR = APP_DIR / ".autotok" / "updates"


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    name: str
    download_url: str
    release_url: str
    notes: str = ""


def _version_parts(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", value)
    return tuple(int(part) for part in match.group(0).split(".")) if match else (0,)


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    left = _version_parts(candidate)
    right = _version_parts(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def check_for_update(timeout: float = 12.0) -> UpdateInfo | None:
    response = requests.get(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"AutoTok/{__version__}"},
        timeout=timeout,
    )
    response.raise_for_status()
    release = response.json()
    tag = str(release.get("tag_name", ""))
    if not is_newer_version(tag):
        return None
    assets = release.get("assets") or []
    asset = next(
        (
            item for item in assets
            if re.fullmatch(r"AutoTok-Windows-x64-v?.*\.zip", str(item.get("name", "")), re.IGNORECASE)
        ),
        None,
    )
    if not asset or not str(asset.get("browser_download_url", "")).startswith("https://"):
        raise RuntimeError("The latest GitHub release has no Windows AutoTok ZIP.")
    return UpdateInfo(
        version=tag.removeprefix("v"),
        name=str(asset.get("name", "AutoTok update")),
        download_url=str(asset["browser_download_url"]),
        release_url=str(release.get("html_url") or RELEASES_URL),
        notes=str(release.get("body") or ""),
    )


def download_update(info: UpdateInfo, progress=None) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    archive = UPDATE_DIR / Path(info.name).name
    partial = archive.with_suffix(".download")
    with requests.get(info.download_url, stream=True, timeout=(20, 300)) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        received = 0
        with partial.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
    partial.replace(archive)
    return archive


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("The update archive contains an unsafe path.")
        bundle.extractall(destination)
    direct = destination / "AutoTok.exe"
    if direct.exists():
        return destination
    matches = list(destination.glob("*/AutoTok.exe"))
    if len(matches) == 1:
        return matches[0].parent
    raise RuntimeError("The downloaded update does not contain AutoTok.exe.")


def stage_and_launch_update(archive: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Automatic installation is available in the packaged Windows app.")
    staged = _safe_extract(archive, archive.with_suffix(""))
    target = Path(sys.executable).resolve().parent
    script = UPDATE_DIR / "install-update.ps1"
    script.write_text(
        "param([int]$ProcessId,[string]$Source,[string]$Target)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue\n"
        "Start-Sleep -Milliseconds 500\n"
        "Copy-Item -Path (Join-Path $Source '*') -Destination $Target -Recurse -Force\n"
        "Start-Process -FilePath (Join-Path $Target 'AutoTok.exe') -WorkingDirectory $Target\n"
        "Remove-Item -LiteralPath $Source -Recurse -Force -ErrorAction SilentlyContinue\n",
        encoding="utf-8",
    )
    powershell = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    subprocess.Popen(
        [
            str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-ProcessId", str(os.getpid()), "-Source", str(staged), "-Target", str(target),
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
