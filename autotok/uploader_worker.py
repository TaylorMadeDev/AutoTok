from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Expected one upload payload path", flush=True)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    video = Path(payload["video"]).resolve()
    if not video.exists() or video.suffix.lower() != ".mp4":
        print("The selected MP4 does not exist", flush=True)
        return 2
    account = str(payload.get("account", "")).strip()
    if not account:
        print("A TikTok account/profile name is required", flush=True)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", account):
        print("Account name may contain only letters, numbers, dots, dashes, and underscores", flush=True)
        return 2
    cookie_file = Path.cwd() / f"TK_cookies_{account}.json"
    if not cookie_file.exists():
        print("No user-created TikTok session found. Run setup.bat and complete the visible TikTok sign-in first.", flush=True)
        return 2

    # TikTokAutoUploader 6.x always builds a fingerprint-masked context and
    # invokes its image CAPTCHA solver internally, even when stealth=False.
    # AutoTok deliberately replaces both pieces: ordinary visible Chromium is
    # used and any challenge is left to the signed-in user.
    import tiktokautouploader.function as uploader

    def plain_browser_context(playwright, headless, proxy):
        browser = playwright.chromium.launch(headless=False, proxy=proxy)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        return browser, context

    def wait_for_manual_challenge(page, suppressprint):
        print("TikTok challenge detected. Complete it manually in the visible browser.", flush=True)
        deadline = time.monotonic() + 600
        selector = uploader.CAPTCHA_QUESTION_SELECTOR
        while time.monotonic() < deadline:
            try:
                if not page.locator(selector).is_visible():
                    print("Challenge completed; continuing upload.", flush=True)
                    return
            except Exception:
                return
            page.wait_for_timeout(1000)
        raise RuntimeError("Timed out waiting for the TikTok challenge to be completed manually.")

    uploader._make_stealth_context = plain_browser_context
    uploader._solve_captcha_if_needed = wait_for_manual_challenge

    kwargs = {
        "video": str(video),
        "description": str(payload.get("description", ""))[:2200],
        "accountname": account,
        "hashtags": [str(tag) for tag in payload.get("hashtags", [])],
        "copyrightcheck": bool(payload.get("copyright_check", True)),
        "headless": False,
        "stealth": False,
        "suppressprint": False,
    }
    cover = str(payload.get("cover", "")).strip()
    if payload.get("cover_baked") and cover and Path(cover).exists():
        kwargs["cover_image"] = cover
    sound = str(payload.get("sound", "")).strip()
    if sound:
        kwargs["sound_name"] = sound
        kwargs["sound_aud_vol"] = "mix"
        kwargs["search_mode"] = str(payload.get("sound_search_mode", "search"))
    schedule = str(payload.get("schedule", "")).strip()
    if schedule:
        parsed = datetime.fromisoformat(schedule)
        kwargs["schedule"] = parsed.strftime("%H:%M")
        kwargs["day"] = parsed.day

    print("Starting user-visible upload. Complete login or any challenge in the browser.", flush=True)
    uploader.upload_tiktok(**kwargs)
    print("Uploader finished successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
