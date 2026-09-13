from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from phantomwright.sync_api import sync_playwright


def main() -> int:
    if len(sys.argv) != 3:
        print("Expected account name and output directory", flush=True)
        return 2
    account = sys.argv[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", account):
        print("Account name may contain letters, numbers, dots, dashes, and underscores", flush=True)
        return 2
    output = Path(sys.argv[2]).resolve() / f"TK_cookies_{account}.json"
    print("Opening TikTok sign-in. Sign in normally in the visible browser.", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded", timeout=60_000)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            cookies = context.cookies()
            cookie_names = {cookie.get("name") for cookie in cookies}
            if cookie_names.intersection({"sessionid", "sessionid_ss", "sid_tt"}):
                output.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                print(f"Sign-in saved for {account}.", flush=True)
                browser.close()
                return 0
            page.wait_for_timeout(1000)
        browser.close()
    print("Sign-in timed out after 10 minutes", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
