"""
Gaussian click bot using CloakBrowser (stealth Chromium).

Drop-in replacement for playwright_agent.py — same algorithm from common.py,
but launched through a Chromium binary with 49 C++ fingerprint patches:
navigator.webdriver = false, canvas randomized, GPU fingerprint masked, etc.

Install:
  pip install cloakbrowser
  py -m cloakbrowser install   # downloads ~535MB patched Chromium binary

Usage:
  python bot/cloak_agent.py --posts 5 --visible
  python bot/cloak_agent.py --posts 20
"""
import argparse
import random
import time
from dataclasses import dataclass, field

from cloakbrowser import launch

from bot.common import (
    UA_POOL, STEALTH_SCRIPT,
    g, get_logger, pick_post,
    gaussian_click, gaussian_type,
)

log = get_logger(__name__)

BASE_URL = "http://localhost:5000"


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    sent:    int = 0
    passed:  int = 0
    flagged: int = 0
    errors:  int = 0

    @property
    def detection_rate(self) -> float:
        return 100 * self.flagged / max(self.sent, 1)


# ── Runner ────────────────────────────────────────────────────────────────────

def run(n: int = 10, visible: bool = False) -> Stats:
    posts = [pick_post("MMA") for _ in range(n)]
    stats = Stats()

    log.info("CloakBrowser Gaussian bot | posts=%d | visible=%s", n, visible)
    log.info("Chromium 146 + 49 C++ stealth patches")

    browser = launch(
        headless=not visible,
        args=["--no-sandbox", "--disable-notifications"],
    )
    ctx = browser.new_context(
        viewport={"width": random.randint(1280, 1920),
                  "height": random.randint(768, 1080)},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = ctx.new_page()
    page.add_init_script(STEALTH_SCRIPT)

    try:
        page.goto(BASE_URL, wait_until="domcontentloaded")
        time.sleep(g(1.5, 0.5, lo=0.8))
        page.mouse.wheel(0, int(g(200, 80, lo=50)))
        time.sleep(g(1.0, 0.4, lo=0.4))

        for i, (title, body) in enumerate(posts):
            log.info("[%d/%d] Navigating to submit", i + 1, n)
            page.goto(f"{BASE_URL}/submit", wait_until="domcontentloaded")
            time.sleep(g(0.8, 0.3, lo=0.3))

            page.mouse.wheel(0, int(g(60, 25, lo=10)))
            time.sleep(g(1.0, 0.4, lo=0.4))

            log.info("Typing title (%d chars)", len(title))
            gaussian_type(page, "input[name='title']", title)
            time.sleep(g(0.6, 0.2, lo=0.2))

            if body:
                log.info("Typing body (%d chars)", len(body))
                gaussian_type(page, "textarea[name='body']", body)
                time.sleep(g(0.8, 0.3, lo=0.3))

            pause = g(2.0, 0.8, lo=0.8)
            log.info("Re-reading for %.1fs", pause)
            time.sleep(pause)

            gaussian_click(page, "button[type='submit']", sigma=4.0)
            time.sleep(g(1.5, 0.5, lo=0.8))

            try:
                txt = page.locator("#result").first.inner_text(timeout=3000)
            except Exception:
                txt = ""

            stats.sent += 1
            if "FLAGGED" in txt.upper():
                stats.flagged += 1
                log.warning("[FLAG] %s", title[:50])
            elif "ID:" in txt or "Posted" in txt:
                stats.passed += 1
                log.info("[PASS] %s", title[:50])
            else:
                stats.errors += 1
                log.error("[??] %s  response=%s", title[:50], txt[:40])

            if i < n - 1:
                wait = g(3.5, 1.2, lo=1.5)
                log.info("Waiting %.1fs", wait)
                if random.random() < 0.3:
                    page.goto(BASE_URL, wait_until="domcontentloaded")
                    page.mouse.wheel(0, int(g(250, 100, lo=60)))
                    time.sleep(g(1.2, 0.5, lo=0.5))
                time.sleep(wait)

    except Exception as exc:
        log.error("Bot error: %s", exc)
        stats.errors += 1
    finally:
        browser.close()

    log.info("Results: %d passed / %d flagged / %d errors | detection=%.1f%%",
             stats.passed, stats.flagged, stats.errors, stats.detection_rate)
    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CloakBrowser Gaussian bot")
    parser.add_argument("--posts",   type=int, default=10)
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    run(n=args.posts, visible=args.visible)
