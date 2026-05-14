"""
Real Reddit browser poster via old.reddit.com.

Features:
  - Triangle-waypoint + cubic Bezier + Gaussian noise mouse movement
  - Anti-detection JS (hides navigator.webdriver, canvas noise, plugin list)
  - Cookie session persistence (login once, reuse cookies on next run)
  - Per-post proxy rotation with fresh browser context
  - Exponential-backoff retry on network failures
  - Structured logging

Usage:
  python bot/reddit_browser_agent.py --sub test --posts 1 --visible --dry-run
  python bot/reddit_browser_agent.py --sub test --posts 3 --proxy-file bot/proxies.txt
  python bot/reddit_browser_agent.py --sub MMA  --posts 1 --visible
  python bot/reddit_browser_agent.py --clear-session  # force re-login next run

Credentials: bot/.env
  REDDIT_USERNAME=your_username
  REDDIT_PASSWORD=your_password
"""
import argparse
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from bot.common import (
    UA_POOL, TIMEZONES, LOCALES, STEALTH_SCRIPT,
    g, get_logger, pick_post, retry,
    gaussian_click, gaussian_type, gaussian_scroll,
    save_session, load_session, clear_session,
)

log = get_logger(__name__)

BASE = "https://old.reddit.com"


# ── Credentials ───────────────────────────────────────────────────────────────

def _load_env() -> None:
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


def _credentials() -> tuple[str, str]:
    user = os.environ.get("REDDIT_USERNAME", "").strip()
    pwd  = os.environ.get("REDDIT_PASSWORD", "").strip()
    if not user or not pwd:
        raise EnvironmentError(
            "Set REDDIT_USERNAME and REDDIT_PASSWORD in bot/.env\n"
            "See bot/.env.example for the format."
        )
    return user, pwd


# ── Proxy helpers ─────────────────────────────────────────────────────────────

def load_proxies(proxy_file: str | None, single: str | None) -> list[str]:
    proxies: list[str] = []
    if single:
        proxies.append(single)
    if proxy_file:
        p = Path(proxy_file)
        if not p.exists():
            raise FileNotFoundError(f"Proxy file not found: {proxy_file}")
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    return proxies


def _proxy_cfg(proxy_str: str | None) -> dict | None:
    if not proxy_str:
        return None
    cfg: dict = {"server": proxy_str}
    try:
        parsed = urlparse(proxy_str)
        if parsed.username:
            cfg["username"] = parsed.username
            cfg["password"] = parsed.password or ""
            cfg["server"]   = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    except Exception:
        pass
    return cfg


# ── Browser context factory ───────────────────────────────────────────────────

def _new_context(browser, proxy_str: str | None, *, load_saved: bool = False):
    kwargs: dict = dict(
        viewport={"width":  random.randint(1280, 1920),
                  "height": random.randint(768,  1080)},
        user_agent=random.choice(UA_POOL),
        locale=random.choice(LOCALES),
        timezone_id=random.choice(TIMEZONES),
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    cfg = _proxy_cfg(proxy_str)
    if cfg:
        kwargs["proxy"] = cfg
    ctx = browser.new_context(**kwargs)
    ctx.add_cookies([
        {"name": "over18",          "value": "1",    "domain": ".reddit.com", "path": "/"},
        {"name": "redesign_optout", "value": "true", "domain": ".reddit.com", "path": "/"},
    ])
    if load_saved:
        load_session(ctx)
    return ctx


# ── Login ─────────────────────────────────────────────────────────────────────

def _detect_selectors(page) -> tuple[str, str]:
    if "old.reddit.com" in page.url:
        return "input[name='user'], input#user_login", "input[name='passwd'], input#passwd_login"
    return "input#loginUsername, input[name='username']", "input#loginPassword, input[name='password']"


def _is_logged_in(page) -> str | None:
    try:
        text = page.locator("#header-bottom-right .user a").first.inner_text(timeout=4000).strip()
        return text or None
    except Exception:
        return None


def login(page, username: str, password: str) -> bool:
    log.info("Navigating to login page")
    page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=20000)
    time.sleep(g(2.0, 0.6, lo=1.2))

    user_sel, pass_sel = _detect_selectors(page)
    page.mouse.wheel(0, int(g(80, 30, lo=20)))
    time.sleep(g(0.8, 0.3, lo=0.3))

    log.info("Typing username")
    gaussian_type(page, user_sel, username)
    time.sleep(g(0.5, 0.2, lo=0.2))

    log.info("Typing password")
    gaussian_type(page, pass_sel, password)
    time.sleep(g(1.2, 0.4, lo=0.6))

    gaussian_click(page, "button[type='submit']", sigma=4.0)
    time.sleep(g(3.5, 0.8, lo=2.5))

    page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
    time.sleep(g(1.5, 0.4, lo=0.8))

    who = _is_logged_in(page)
    if who:
        log.info("Logged in as u/%s", who)
        return True

    log.warning("Login failed — check credentials or CAPTCHA")
    return False


# ── Organic pre-post browsing ─────────────────────────────────────────────────

def browse_feed(page, sub: str, duration: float | None = None) -> None:
    """
    Simulate organic browsing on r/sub before posting.
    Scrolls the feed, optionally opens a post, reads it, then returns.
    This establishes realistic cookie/session history before any submission.
    """
    if duration is None:
        duration = g(35.0, 12.0, lo=18.0)

    log.info("Browsing r/%s for %.0fs before posting", sub, duration)
    page.goto(f"{BASE}/r/{sub}/", wait_until="domcontentloaded", timeout=20000)
    time.sleep(g(2.0, 0.6, lo=1.2))

    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        # Scroll down the feed in chunks
        pixels = int(g(280, 100, lo=80))
        page.mouse.wheel(0, pixels)
        time.sleep(g(1.8, 0.7, lo=0.8))

        # Occasionally open a post and read it
        if random.random() < 0.35 and time.monotonic() < deadline - 12:
            try:
                links = page.locator("a.title").all()
                if links:
                    link = random.choice(links[:6])
                    link.click()
                    time.sleep(g(2.5, 0.8, lo=1.5))
                    # Scroll through the post
                    for _ in range(random.randint(2, 4)):
                        page.mouse.wheel(0, int(g(250, 100, lo=80)))
                        time.sleep(g(1.5, 0.6, lo=0.7))
                    page.go_back(wait_until="domcontentloaded", timeout=10000)
                    time.sleep(g(1.5, 0.5, lo=0.8))
            except Exception:
                pass  # post link may have been stale — just continue scrolling

        # Occasional long pause (user distracted)
        if random.random() < 0.12:
            pause = g(6.0, 2.0, lo=3.0)
            log.info("Organic pause %.1fs", pause)
            time.sleep(pause)


# ── Rate-limit detection ──────────────────────────────────────────────────────

_RATE_LIMIT_PHRASES = [
    "you are doing that too much",
    "try again in",
    "you're doing that too much",
    "whoa, slow down",
    "something went wrong",
]


def _check_rate_limited(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=2000).lower()
        return any(phrase in body for phrase in _RATE_LIMIT_PHRASES)
    except Exception:
        return False


# ── Post submission ───────────────────────────────────────────────────────────

@retry(attempts=3, base_delay=2.0)
def submit_post(page, sub: str, title: str, body: str, *, dry_run: bool = False) -> str | None:
    log.info("Navigating to r/%s/submit", sub)
    page.goto(f"{BASE}/r/{sub}/submit?type=self", wait_until="domcontentloaded", timeout=20000)
    time.sleep(g(1.5, 0.5, lo=0.8))

    gaussian_scroll(page, int(g(100, 40, lo=30)))
    time.sleep(g(1.0, 0.4, lo=0.4))

    log.info("Typing title (%d chars)", len(title))
    gaussian_type(page, "input[name='title']", title)
    time.sleep(g(0.7, 0.25, lo=0.25))

    log.info("Typing body (%d chars)", len(body))
    gaussian_type(page, "textarea[name='text']", body)
    time.sleep(g(1.0, 0.4, lo=0.5))

    reread = g(2.5, 0.9, lo=1.2)
    log.info("Re-reading for %.1fs", reread)
    time.sleep(reread)

    if dry_run:
        log.info("[DRY-RUN] Skipping submit click")
        return None

    gaussian_click(page, "button[type='submit'], input[type='submit']", sigma=4.0)
    time.sleep(g(3.5, 0.8, lo=2.5))

    url = page.url
    log.info("Post URL: %s", url)
    return url


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    sent:    int = 0
    success: int = 0
    failed:  int = 0
    urls:    list = field(default_factory=list)


# ── Runner ────────────────────────────────────────────────────────────────────

def run(
    sub:           str        = "test",
    n:             int        = 1,
    headless:      bool       = True,
    stealth:       bool       = False,
    dry_run:       bool       = False,
    proxies:       list[str] | None = None,
    reuse_session: bool       = True,
) -> Stats:
    username, password = _credentials()
    proxies = proxies or []
    stats   = Stats()

    log.info("Starting | r/%s | posts=%d | %s | %s%s",
             sub, n,
             "CloakBrowser" if stealth else "Playwright",
             f"{len(proxies)} proxies" if proxies else "direct",
             " | DRY-RUN" if dry_run else "")

    def _run_session(browser):
        for i in range(n):
            proxy_str = proxies[i % len(proxies)] if proxies else None
            log.info("[%d/%d] proxy=%s", i + 1, n,
                     proxy_str.split("@")[-1] if proxy_str else "direct")

            ctx  = _new_context(browser, proxy_str, load_saved=(reuse_session and i == 0))
            page = ctx.new_page()
            page.add_init_script(STEALTH_SCRIPT)

            try:
                page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
                time.sleep(g(1.2, 0.4, lo=0.6))

                if not _is_logged_in(page):
                    ok = login(page, username, password)
                    if not ok:
                        stats.sent += 1; stats.failed += 1
                        continue
                    if reuse_session:
                        save_session(ctx)

                # Organic browsing before first post in each context
                if i == 0 or random.random() < 0.25:
                    browse_feed(page, sub)

                title, body = pick_post(sub)
                log.info("Post: %s", title[:60])

                url = submit_post(page, sub, title, body, dry_run=dry_run)

                # Detect Reddit rate-limiting after submit
                if url and _check_rate_limited(page):
                    backoff = g(70.0, 20.0, lo=45.0)
                    log.warning("Rate-limited — backing off %.0fs", backoff)
                    time.sleep(backoff)
                stats.sent += 1
                if dry_run:
                    stats.success += 1
                elif url:
                    stats.success += 1
                    stats.urls.append(url)
                else:
                    stats.failed += 1

            except Exception as exc:
                log.error("Post %d/%d failed: %s", i + 1, n, exc)
                stats.sent += 1; stats.failed += 1
            finally:
                ctx.close()

            if i < n - 1:
                wait = g(22.0, 7.0, lo=12.0)
                log.info("Waiting %.1fs before next post", wait)
                time.sleep(wait)

    if stealth:
        from cloakbrowser import launch
        browser = launch(headless=headless, args=["--no-sandbox"])
        try:
            _run_session(browser)
        finally:
            browser.close()
    else:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                _run_session(browser)
            finally:
                browser.close()

    log.info("Done — %d success / %d failed / %d sent", stats.success, stats.failed, stats.sent)
    for url in stats.urls:
        log.info("  %s", url)
    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reddit browser poster with proxy rotation")
    parser.add_argument("--sub",           default="test")
    parser.add_argument("--posts",         type=int, default=1)
    parser.add_argument("--visible",       action="store_true")
    parser.add_argument("--stealth",       action="store_true")
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--proxy",         default=None)
    parser.add_argument("--proxy-file",    default=None)
    parser.add_argument("--no-session",    action="store_true")
    parser.add_argument("--clear-session", action="store_true")
    args = parser.parse_args()

    if args.clear_session:
        clear_session()
        log.info("Session cleared — will re-login on next run")
    else:
        run(
            sub=args.sub,
            n=args.posts,
            headless=not args.visible,
            stealth=args.stealth,
            dry_run=args.dry_run,
            proxies=load_proxies(args.proxy_file, args.proxy),
            reuse_session=not args.no_session,
        )
