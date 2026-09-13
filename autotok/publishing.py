from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR
from .studio import file_fingerprint


STATE_DIR = APP_DIR / ".autotok"
UPLOADER_ENV = STATE_DIR / "uploader_env"
PUBLISH_QUEUE_PATH = STATE_DIR / "publish_queue.json"
PRIVATE_TEST_DIR = STATE_DIR / "private_tests"
WORKER_PATH = APP_DIR / "autotok" / "uploader_worker.py"
SIGNIN_WORKER_PATH = APP_DIR / "autotok" / "signin_worker.py"
BROWSER_MARKER = UPLOADER_ENV / ".chromium-installed"
Progress = Callable[[str], None]


def uploader_python() -> Path:
    return UPLOADER_ENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def uploader_status() -> dict[str, Any]:
    python = uploader_python()
    node = shutil.which("node")
    npm = shutil.which("npm")
    installed = False
    version = ""
    if python.exists():
        if sys.platform == "win32":
            site_packages = UPLOADER_ENV / "Lib/site-packages"
        else:
            candidates = list((UPLOADER_ENV / "lib").glob("python*/site-packages"))
            site_packages = candidates[0] if candidates else UPLOADER_ENV / "lib"
        distributions = list(site_packages.glob("tiktokautouploader-*.dist-info"))
        package = site_packages / "tiktokautouploader"
        installed = package.exists() and bool(distributions)
        if distributions:
            version = distributions[0].name.removeprefix("tiktokautouploader-").removesuffix(".dist-info")
    return {
        "ready": bool(installed and node and npm and BROWSER_MARKER.exists()), "installed": installed,
        "version": version, "node": node or "", "npm": npm or "",
        "python": str(python), "browser_ready": BROWSER_MARKER.exists(),
    }


def setup_uploader(progress: Progress | None = None) -> dict[str, Any]:
    if not shutil.which("node") or not shutil.which("npm"):
        raise RuntimeError("Node.js and npm are required. Install the Node.js LTS release, then retry.")
    UPLOADER_ENV.parent.mkdir(parents=True, exist_ok=True)
    python = uploader_python()
    if not python.exists():
        if progress:
            progress("Creating isolated uploader environment…")
        result = subprocess.run([sys.executable, "-m", "venv", str(UPLOADER_ENV)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Could not create uploader environment")
    if progress:
        progress("Installing TikTokAutoUploader…")
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "tiktokautouploader"],
        capture_output=True, text=True, timeout=1800,
    )
    if install.returncode:
        raise RuntimeError(install.stderr[-1500:] or "Uploader installation failed")
    if progress:
        progress("Installing the isolated Chromium browser…")
    browser = subprocess.run(
        [str(UPLOADER_ENV / ("Scripts/phantomwright_driver.exe" if sys.platform == "win32" else "bin/phantomwright_driver")), "install", "chromium"],
        capture_output=True, text=True, timeout=1800,
    )
    if browser.returncode:
        raise RuntimeError(browser.stderr[-1500:] or browser.stdout[-1500:] or "Chromium installation failed")
    BROWSER_MARKER.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return uploader_status()


def _read_queue() -> list[dict[str, Any]]:
    try:
        data = json.loads(PUBLISH_QUEUE_PATH.read_text(encoding="utf-8")) if PUBLISH_QUEUE_PATH.exists() else []
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _write_queue(items: list[dict[str, Any]]) -> None:
    PUBLISH_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLISH_QUEUE_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def load_publish_queue() -> list[dict[str, Any]]:
    return _read_queue()


def enqueue_publish(payload: dict[str, Any], status: str = "approved") -> dict[str, Any]:
    items = _read_queue()
    video = Path(payload["video"]).resolve()
    item = {
        "id": str(uuid.uuid4()), "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"), "status": status,
        "video": str(video), "video_fingerprint": file_fingerprint(video), "payload": payload,
        "error": "",
    }
    items.insert(0, item)
    _write_queue(items[:500])
    return item


def update_publish_item(item_id: str, status: str, error: str = "") -> None:
    items = _read_queue()
    for item in items:
        if item.get("id") == item_id:
            item["status"] = status
            item["error"] = error
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            break
    _write_queue(items)


def duplicate_published_video(video: Path) -> dict[str, Any] | None:
    fingerprint = file_fingerprint(video)
    for item in _read_queue():
        if item.get("video_fingerprint") == fingerprint and item.get("status") in {"publishing", "published", "private-test"}:
            return item
    return None


def create_private_test(payload: dict[str, Any]) -> Path:
    video = Path(payload["video"])
    PRIVATE_TEST_DIR.mkdir(parents=True, exist_ok=True)
    package = PRIVATE_TEST_DIR / f"{video.stem}_{datetime.now():%Y%m%d_%H%M%S}.json"
    package.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return package


def run_community_upload(payload: dict[str, Any], progress: Progress | None = None) -> None:
    status = uploader_status()
    if not status["ready"]:
        raise RuntimeError("The community uploader is not installed. Use Studio → Publishing → Set up uploader.")
    payload_path = STATE_DIR / f"upload_{uuid.uuid4().hex}.json"
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        if progress:
            progress("Opening visible TikTok browser session…")
        process = subprocess.Popen(
            [str(uploader_python()), str(WORKER_PATH), str(payload_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            cwd=str(APP_DIR), bufsize=1,
        )
        if process.stdout:
            for line in process.stdout:
                if progress and line.strip():
                    progress(line.strip()[-220:])
        code = process.wait()
        if code:
            raise RuntimeError(f"TikTok uploader stopped with exit code {code}. Check the activity log above.")
    finally:
        try:
            payload_path.unlink()
        except OSError:
            pass


def sign_in_account(account: str, progress: Progress | None = None) -> Path:
    if not uploader_status()["ready"]:
        raise RuntimeError("Install the community uploader before signing in.")
    account = account.strip()
    if not account:
        raise ValueError("Enter a short account/profile name first.")
    if progress:
        progress("Opening visible TikTok sign-in…")
    process = subprocess.Popen(
        [str(uploader_python()), str(SIGNIN_WORKER_PATH), account, str(APP_DIR)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(APP_DIR), bufsize=1,
    )
    if process.stdout:
        for line in process.stdout:
            if progress and line.strip():
                progress(line.strip())
    code = process.wait()
    cookie_path = APP_DIR / f"TK_cookies_{account}.json"
    if code or not cookie_path.exists():
        raise RuntimeError("TikTok sign-in did not complete. You can retry or choose Do it later.")
    return cookie_path
