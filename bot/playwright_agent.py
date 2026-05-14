"""
Gaussian click bot using Playwright against the local simulation server.

All mouse movement primitives live in bot/common.py — this file contains
only the Playwright-specific runner and the sim-server post loop.

Usage:
  python bot/playwright_agent.py --mode visible  --posts 5
  python bot/playwright_agent.py --mode headless --posts 20
"""
import argparse
import random
import time
from dataclasses import dataclass, field

from playwright.sync_api import Page, sync_playwright

from bot.common import (
    UA_POOL, STEALTH_SCRIPT,
    g, get_logger, pick_post,
    gaussian_click, gaussian_type, gaussian_scroll,
)

log = get_logger(__name__)

BASE_URL = "http://localhost:5000"


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class PlaywrightStats:
    sent:    int = 0
    passed:  int = 0
    flagged: int = 0
    errors:  int = 0
    reasons: list = field(default_factory=list)

    @property
    def detection_rate(self) -> float:
        return 100 * self.flagged / max(self.sent, 1)


# ── Sim-server post loop ───────────────────────────────────────────────────────

def _run_bot(page: Page, posts: list[tuple[str, str]], stats: PlaywrightStats) -> None:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    time.sleep(g(1.5, 0.5, lo=0.8))
    gaussian_scroll(page, int(g(200, 80, lo=50)))
    time.sleep(g(1.0, 0.4, lo=0.4))

    for i, (title, body) in enumerate(posts):
        log.info("[%d/%d] Navigating to submit form", i + 1, len(posts))
        page.goto(f"{BASE_URL}/submit", wait_until="domcontentloaded")
        time.sleep(g(0.8, 0.3, lo=0.3))

        gaussian_scroll(page, int(g(80, 30, lo=10)))
        time.sleep(g(1.2, 0.5, lo=0.5))

        log.info("Typing title (%d chars)", len(title))
        gaussian_type(page, "input[name='title']", title)
        time.sleep(g(0.6, 0.2, lo=0.2))

        gaussian_scroll(page, int(g(60, 20, lo=10)))
        time.sleep(g(0.5, 0.2, lo=0.2))

        if body:
            log.info("Typing body (%d chars)", len(body))
            gaussian_type(page, "textarea[name='body']", body)
            time.sleep(g(0.8, 0.3, lo=0.3))

        read_pause = g(2.0, 0.8, lo=0.8)
        log.info("Re-reading for %.1fs", read_pause)
        time.sleep(read_pause)

        gaussian_click(page, "button[type='submit']", sigma=4.0)
        time.sleep(g(1.5, 0.5, lo=0.8))

        try:
            result_text = page.locator("#result").first.inner_text(timeout=3000)
        except Exception:
            result_text = ""

        stats.sent += 1
        if "FLAGGED" in result_text.upper():
            stats.flagged += 1
            log.warning("[FLAG] %s", title[:50])
        elif "Posted" in result_text or "ID:" in result_text:
            stats.passed += 1
            log.info("[PASS] %s", title[:50])
        else:
            stats.errors += 1
            log.error("[??] %s — %s", title[:50], result_text[:60])

        if i < len(posts) - 1:
            pause = g(3.5, 1.2, lo=1.5)
            log.info("Waiting %.1fs before next post", pause)
            if random.random() < 0.3:
                page.goto(BASE_URL, wait_until="domcontentloaded")
                gaussian_scroll(page, int(g(300, 100, lo=80)))
                time.sleep(g(1.5, 0.5, lo=0.5))
            time.sleep(pause)


# ── Runner ────────────────────────────────────────────────────────────────────

def run(n: int = 10, headless: bool = True) -> PlaywrightStats:
    sample = random.sample(
        list(pick_post("MMA") for _ in range(n * 3)),  # draw n unique-ish posts
        n,
    )
    # Deduplicate by title
    seen, unique = set(), []
    for t, b in sample:
        if t not in seen:
            seen.add(t); unique.append((t, b))
        if len(unique) == n:
            break
    while len(unique) < n:
        unique.append(pick_post("MMA"))

    stats = PlaywrightStats()

    log.info("Playwright Gaussian-click bot | posts=%d | headless=%s", n, headless)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            viewport={"width": random.randint(1280, 1920),
                      "height": random.randint(768, 1080)},
            user_agent=random.choice(UA_POOL),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = ctx.new_page()
        page.add_init_script(STEALTH_SCRIPT)

        try:
            _run_bot(page, unique, stats)
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
    parser = argparse.ArgumentParser(description="Gaussian click bot (Playwright vs sim server)")
    parser.add_argument("--posts", type=int, default=10)
    parser.add_argument("--mode",  choices=["headless", "visible"], default="visible")
    args = parser.parse_args()
    run(n=args.posts, headless=(args.mode == "headless"))
