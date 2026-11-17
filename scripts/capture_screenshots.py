"""Drive the running app with the system Chrome and capture README screenshots (+ a silent walkthrough video).

    streamlit run streamlit_app.py      # in another terminal
    python scripts/capture_screenshots.py [--url http://localhost:8501]
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
VIDEO_DIR = ROOT / "docs" / "demo"


def settle(page: Page, seconds: float = 2.5) -> None:
    """Streamlit keeps a websocket open, so 'networkidle' may never fire: wait for the app shell instead."""
    page.wait_for_load_state("domcontentloaded")
    page.locator('[data-testid="stApp"]').wait_for(timeout=60_000)
    deadline = time.time() + 60
    while page.locator('[data-testid="stStatusWidget"]').count() and time.time() < deadline:
        time.sleep(0.5)  # the "Running…" indicator is visible while the script executes
    time.sleep(seconds)


def n_turns(page: Page) -> int:
    """Finished agent turns on screen: each one renders an 'Agent trace' expander after the page re-runs."""
    return page.locator('[data-testid="stExpander"]').filter(has_text="Agent trace").count()


LIVE_STATUS = re.compile(r"…$|^Completed in ")  # progress labels shown while a turn is running


def wait_for_reply(page: Page, before: int, timeout: float = 300) -> None:
    """Wait until one more agent turn has finished and the page has re-run (no live status label left)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if n_turns(page) >= before + 1 and page.get_by_text(LIVE_STATUS).count() == 0:
            time.sleep(2)
            if page.get_by_text(LIVE_STATUS).count() == 0:
                settle(page, 3)
                return
        time.sleep(1.5)
    raise TimeoutError("no reply from the concierge")


def send(page: Page, text: str) -> None:
    before = n_turns(page)
    box = page.get_by_placeholder("Message the concierge", exact=False)
    box.fill(text)
    box.press("Enter")
    wait_for_reply(page, before)


def click_and_wait(page: Page, name: str, *, first: bool = False) -> None:
    before = n_turns(page)
    buttons = page.get_by_role("button", name=name)
    (buttons.first if first else buttons.last).click()
    wait_for_reply(page, before)


def shot(page: Page, name: str, *, full: bool = False) -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(IMAGES / f"{name}.png"), full_page=full)
    print("saved", name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--no-chat", action="store_true", help="skip the LLM-driven conversation")
    parser.add_argument("--video", action="store_true", help="also record a walkthrough (needs `playwright install ffmpeg`)")
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        video = {"record_video_dir": str(VIDEO_DIR / "raw"), "record_video_size": {"width": 1440, "height": 900}}
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2,
                                      **(video if args.video else {}))
        page = context.new_page()
        page.goto(args.url)
        settle(page, 6)
        shot(page, "concierge-welcome")

        if not args.no_chat:
            page.get_by_role("button", name="Show suggested requests").click(force=True)
            time.sleep(1.5)
            shot(page, "concierge-mascot")
            click_and_wait(page, "Italian dinner for two, Saturday 8 PM")
            shot(page, "concierge-recommendations")
            click_and_wait(page, "Reserve this table", first=True)
            send(page, "Priya Sharma, 98450 11122")
            shot(page, "concierge-hold")
            click_and_wait(page, "Confirm reservation")
            shot(page, "concierge-confirmed")
            page.get_by_text("Agent trace").last.click()
            time.sleep(1.5)
            shot(page, "agent-trace")
            page.get_by_role("button", name="Start new conversation").click()
            settle(page)
            send(page, "kal raat 8 baje 4 logon ke liye biryani, Frazer Town mein")
            shot(page, "concierge-hinglish")

        for path, name in [("dashboard", "dashboard"), ("roi", "roi-calculator"), ("internals", "under-the-hood")]:
            page.goto(f"{args.url}/{path}")
            settle(page, 6)
            shot(page, name)
        page.get_by_role("tab", name="MCP server").click()
        settle(page)
        shot(page, "mcp-server")
        page.get_by_role("tab", name="Evals").click()
        settle(page)
        shot(page, "evals")

        video = page.video
        context.close()
        browser.close()
        if video:
            target = VIDEO_DIR / "walkthrough.webm"
            shutil.move(video.path(), target)
            shutil.rmtree(VIDEO_DIR / "raw", ignore_errors=True)
            print("saved", target)


if __name__ == "__main__":
    main()
