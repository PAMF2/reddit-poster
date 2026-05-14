"""
Real Reddit browser poster via old.reddit.com with proxy rotation.

Each post gets a fresh browser context routed through a different proxy,
so every submission appears to come from a different IP.

Proxy formats supported (one per line in proxies.txt):
  http://ip:port
  http://user:pass@ip:port
  socks5://ip:port
  socks5://user:pass@ip:port

Usage:
  # No proxy (single IP):
  python sim/bot/reddit_browser_agent.py --sub test --posts 1 --visible

  # Single proxy:
  python sim/bot/reddit_browser_agent.py --sub test --posts 1 --proxy http://1.2.3.4:8080

  # Rotate from file (one proxy per line):
  python sim/bot/reddit_browser_agent.py --sub test --posts 5 --proxy-file sim/bot/proxies.txt

  # Dry-run (navigate + type but don't click submit):
  python sim/bot/reddit_browser_agent.py --sub MMA --posts 1 --visible --dry-run --proxy-file sim/bot/proxies.txt

  # Stealth Chromium (if cloakbrowser installed):
  python sim/bot/reddit_browser_agent.py --sub test --posts 1 --stealth --proxy-file sim/bot/proxies.txt

Credentials: sim/bot/.env
  REDDIT_USERNAME=your_username
  REDDIT_PASSWORD=your_password
"""
import argparse
import math
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path


# ── load .env ─────────────────────────────────────────────────────────────────

def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

BASE = "https://old.reddit.com"


# ── Proxy pool ────────────────────────────────────────────────────────────────

def _load_proxies(proxy_file: str | None, single: str | None) -> list[str]:
    """Return list of proxy strings. Empty list = no proxy."""
    proxies = []
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
    """Convert proxy URL string to Playwright proxy dict."""
    if not proxy_str:
        return None
    # Playwright wants: {"server": "...", "username": "...", "password": "..."}
    # Parse user:pass out of the URL if present
    # e.g. http://user:pass@1.2.3.4:8080
    cfg = {"server": proxy_str}
    # Extract credentials if embedded in URL
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_str)
        if parsed.username:
            cfg["username"] = parsed.username
            cfg["password"] = parsed.password or ""
            # Rebuild server without credentials
            cfg["server"] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    except Exception:
        pass
    return cfg


# ── Gaussian primitives ────────────────────────────────────────────────────────

def g(mu, sigma, lo=None, hi=None):
    v = random.gauss(mu, sigma)
    if lo is not None: v = max(lo, v)
    if hi is not None: v = min(hi, v)
    return v


# ── Triangle waypoints + Bezier ───────────────────────────────────────────────

def _bezier(t, pts):
    p = list(pts)
    while len(p) > 1:
        p = [(p[i][0]*(1-t)+p[i+1][0]*t, p[i][1]*(1-t)+p[i+1][1]*t)
             for i in range(len(p)-1)]
    return p[0]


def _triangle_waypoints(x0, y0, x1, y1, n=None):
    dist = math.hypot(x1-x0, y1-y0) or 1.0
    if n is None:
        n = random.choices([0,1,2], weights=[0.2,0.5,0.3])[0] if dist > 80 \
            else random.choices([0,1], weights=[0.5,0.5])[0]
    waypoints = [(x0, y0)]
    dx, dy = x1-x0, y1-y0
    px, py = -dy/dist, dx/dist
    for t in sorted(random.uniform(0.2, 0.8) for _ in range(n)):
        sigma  = dist * g(0.15, 0.05, lo=0.05, hi=0.35)
        offset = random.gauss(0, sigma)
        waypoints.append((x0+dx*t + px*offset, y0+dy*t + py*offset))
    waypoints.append((x1, y1))
    return waypoints


def _move_segment(page, x0, y0, x1, y1):
    dist  = math.hypot(x1-x0, y1-y0)
    steps = max(12, int(dist/6))
    jit   = max(10.0, dist*0.2)
    t1 = g(0.30, 0.08, lo=0.15, hi=0.45)
    t2 = g(0.70, 0.08, lo=0.55, hi=0.85)
    ctrl = [
        (x0, y0),
        (x0+(x1-x0)*t1+g(0,jit), y0+(y1-y0)*t1+g(0,jit)),
        (x0+(x1-x0)*t2+g(0,jit), y0+(y1-y0)*t2+g(0,jit)),
        (x1, y1),
    ]
    for i in range(steps+1):
        t = i/steps
        bx, by = _bezier(t, ctrl)
        page.mouse.move(bx+g(0,1.2), by+g(0,1.2))
        speed = 0.5 + 0.5*math.sin(math.pi*t)
        time.sleep(g(0.004, 0.0015, lo=0.001) / (speed+0.1))


def move_mouse(page, x, y):
    cx = page.evaluate("() => window._mx || 400")
    cy = page.evaluate("() => window._my || 300")
    waypoints = _triangle_waypoints(cx, cy, x, y)
    for i in range(len(waypoints)-1):
        ax, ay = waypoints[i]
        bx, by = waypoints[i+1]
        _move_segment(page, ax, ay, bx, by)
        if i < len(waypoints)-2:
            time.sleep(g(0.04, 0.02, lo=0.01))
    page.evaluate(f"() => {{ window._mx={x}; window._my={y}; }}")


def gaussian_click(page, selector, sigma=3.0, timeout=10000):
    el = page.locator(selector).first
    el.wait_for(state="visible", timeout=timeout)
    box = el.bounding_box()
    if not box:
        el.click()
        return
    cx = box["x"] + box["width"]/2  + g(0, sigma)
    cy = box["y"] + box["height"]/2 + g(0, sigma)
    move_mouse(page, cx, cy)
    hold = int(g(90, 35, lo=40, hi=320))
    page.mouse.down()
    time.sleep(hold/1000)
    page.mouse.up()


def gaussian_type(page, selector, text, timeout=10000):
    gaussian_click(page, selector, timeout=timeout)
    time.sleep(g(0.35, 0.12, lo=0.15))
    for char in text:
        page.keyboard.type(char)
        delay = g(0.09, 0.04, lo=0.03, hi=0.45)
        if random.random() < 0.05:
            delay += g(0.55, 0.25, lo=0.25)
        time.sleep(delay)


# ── Post bank ─────────────────────────────────────────────────────────────────

_POSTS = {
    "test": [
        ("Testing automated post — please ignore",
         "Research test post. Part of an academic study on bot detection."),
        ("Browser automation research test",
         "Testing a browser-based submission flow. No action needed."),
        ("Automated submission test — disregard",
         "Academic research on human-like browser behavior. Ignore this post."),
    ],
    "MMA": [
        ("Jon Jones vs Stipe — who wins the rematch?",
         "Jones by decision, Stipe's chin can't survive 5 rounds of Jon's pace. What do you think?"),
        ("Best KO finish of the decade?",
         "Velasquez vs Brock still hits different. Pure fury and speed on display."),
        ("Is wrestling still the most important base in MMA?",
         "With how BJJ has evolved I'd argue striking matters more now. The guard work we're seeing in 2024 would have been elite 10 years ago."),
        ("Izzy's striking variety is genuinely insane",
         "Seven different striking styles in one fighter. Genuinely unprecedented in MMA history."),
        ("Poirier's legacy after retirement",
         "Top 5 LW all time imo. That body shot against McGregor was pure art."),
    ],
}

def _pick_post(sub):
    bank = _POSTS.get(sub, _POSTS["test"])
    return random.choice(bank)


# ── Reddit navigation ──────────────────────────────────────────────────────────

def _login(page, username, password):
    print("[LOGIN] Navigating to login page...")
    # Set cookie to opt out of new Reddit before navigating
    page.context.add_cookies([
        {"name": "over18",         "value": "1",    "domain": ".reddit.com", "path": "/"},
        {"name": "redesign_optout","value": "true", "domain": ".reddit.com", "path": "/"},
    ])
    page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=20000)
    time.sleep(g(2.0, 0.6, lo=1.2))

    # Detect which login page we landed on
    current = page.url
    if "www.reddit.com" in current or "reddit.com/login" in current and "old." not in current:
        # New Reddit login — different selectors
        user_sel   = "input#loginUsername, input[name='username']"
        passwd_sel = "input#loginPassword, input[name='password']"
    else:
        # Old Reddit login
        user_sel   = "input[name='user'], input#user_login"
        passwd_sel = "input[name='passwd'], input#passwd_login"

    page.mouse.wheel(0, int(g(80, 30, lo=20)))
    time.sleep(g(0.8, 0.3, lo=0.3))

    print("[LOGIN] Typing username...")
    gaussian_type(page, user_sel, username)
    time.sleep(g(0.5, 0.2, lo=0.2))

    print("[LOGIN] Typing password...")
    gaussian_type(page, passwd_sel, password)
    time.sleep(g(1.2, 0.4, lo=0.6))

    print("[LOGIN] Submitting...")
    gaussian_click(page, "button[type='submit']", sigma=4.0)
    time.sleep(g(3.5, 0.8, lo=2.5))

    # Force old Reddit after login
    page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
    time.sleep(g(1.5, 0.4, lo=0.8))

    try:
        logged_user = page.locator("#header-bottom-right .user a").first.inner_text(timeout=6000).strip()
        if logged_user:
            print(f"[LOGIN] OK — u/{logged_user}")
            return True
    except Exception:
        pass

    print("[LOGIN] Login may have failed — check credentials or CAPTCHA")
    return False


def _submit_post(page, sub, title, body, dry_run=False):
    url = f"{BASE}/r/{sub}/submit?type=self"
    print(f"[POST] r/{sub}/submit")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    time.sleep(g(1.5, 0.5, lo=0.8))

    page.mouse.wheel(0, int(g(100, 40, lo=30)))
    time.sleep(g(1.0, 0.4, lo=0.4))

    print(f"[POST] Title ({len(title)} chars)...")
    gaussian_type(page, "input[name='title']", title)
    time.sleep(g(0.7, 0.25, lo=0.25))

    print(f"[POST] Body ({len(body)} chars)...")
    gaussian_type(page, "textarea[name='text']", body)
    time.sleep(g(1.0, 0.4, lo=0.5))

    reread = g(2.5, 0.9, lo=1.2)
    print(f"[POST] Re-reading {reread:.1f}s...")
    time.sleep(reread)

    if dry_run:
        print("[DRY-RUN] Skipping submit click.")
        return None

    print("[POST] Submitting...")
    # old Reddit submit button has class "btn" or "btn-primary", not "submit"
    gaussian_click(page, "button[type='submit'], input[type='submit'][value*='submit' i]", sigma=4.0)
    time.sleep(g(3.5, 0.8, lo=2.5))

    current = page.url
    if "/comments/" in current or sub.lower() in current.lower():
        print(f"[POST] Done: {current}")
        return current
    print(f"[POST] URL after submit: {current}")
    return current


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    sent:    int = 0
    success: int = 0
    failed:  int = 0
    urls:    list = field(default_factory=list)


# ── Per-post browser context ───────────────────────────────────────────────────

_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

_TIMEZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles", "America/Denver"]
_LOCALES   = ["en-US", "en-GB", "en-CA"]


def _make_context(browser, proxy_str: str | None):
    """Fresh context per post — new fingerprint + new proxy."""
    kwargs = dict(
        viewport={"width": random.randint(1280,1920), "height": random.randint(768,1080)},
        user_agent=random.choice(_UAS),
        locale=random.choice(_LOCALES),
        timezone_id=random.choice(_TIMEZONES),
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    cfg = _proxy_cfg(proxy_str)
    if cfg:
        kwargs["proxy"] = cfg
    return browser.new_context(**kwargs)


# ── Runner ────────────────────────────────────────────────────────────────────

def run(sub="test", n=1, headless=True, stealth=False, dry_run=False,
        proxies: list | None = None):

    username = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")
    if not username or not password:
        raise EnvironmentError("Set REDDIT_USERNAME and REDDIT_PASSWORD in sim/bot/.env")

    proxies = proxies or []
    stats   = Stats()

    proxy_mode = f"{len(proxies)} proxies rotating" if proxies else "no proxy (single IP)"
    engine     = "CloakBrowser" if stealth else "Playwright"
    mode_str   = f"{engine} | {proxy_mode}" + (" | DRY-RUN" if dry_run else "")

    print(f"\n{'='*60}")
    print(f"  Reddit Browser Agent | r/{sub} | posts={n}")
    print(f"  {mode_str}")
    print(f"{'='*60}\n")

    def _run_with_browser(browser):
        for i in range(n):
            # Pick proxy for this post (cycle through list)
            proxy_str = proxies[i % len(proxies)] if proxies else None
            proxy_display = proxy_str.split("@")[-1] if proxy_str else "direct"

            print(f"\n[{i+1}/{n}] proxy={proxy_display}")

            ctx  = _make_context(browser, proxy_str)
            page = ctx.new_page()
            page.add_init_script("window._mx=400; window._my=300;")

            try:
                ok = _login(page, username, password)
                if not ok:
                    stats.sent   += 1
                    stats.failed += 1
                    continue

                title, body = _pick_post(sub)
                print(f"    {title[:55]}")
                url = _submit_post(page, sub, title, body, dry_run=dry_run)
                stats.sent += 1
                if url:
                    stats.success += 1
                    stats.urls.append(url)
                else:
                    stats.failed += 1
            except Exception as exc:
                print(f"  [ERR] {exc}")
                stats.sent   += 1
                stats.failed += 1
            finally:
                ctx.close()

            if i < n-1:
                wait = g(22.0, 7.0, lo=12.0)
                print(f"    [wait] {wait:.1f}s before next post...")
                time.sleep(wait)

    if stealth:
        from cloakbrowser import launch
        browser = launch(headless=headless, args=["--no-sandbox", "--disable-notifications"])
        try:
            _run_with_browser(browser)
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
                _run_with_browser(browser)
            finally:
                browser.close()

    print(f"\n{'-'*60}")
    print(f"  Results: {stats.success} success / {stats.failed} failed / {stats.sent} sent")
    for url in stats.urls:
        print(f"  {url}")
    print(f"{'-'*60}\n")
    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real Reddit browser poster with proxy rotation")
    parser.add_argument("--sub",        default="test",   help="Target subreddit (default: test)")
    parser.add_argument("--posts",      type=int, default=1)
    parser.add_argument("--visible",    action="store_true", help="Show browser window")
    parser.add_argument("--stealth",    action="store_true", help="Use CloakBrowser")
    parser.add_argument("--dry-run",    action="store_true", help="Type but don't submit")
    parser.add_argument("--proxy",      default=None,     help="Single proxy URL")
    parser.add_argument("--proxy-file", default=None,     help="File with one proxy per line")
    args = parser.parse_args()

    proxy_list = _load_proxies(args.proxy_file, args.proxy)

    run(
        sub=args.sub,
        n=args.posts,
        headless=not args.visible,
        stealth=args.stealth,
        dry_run=args.dry_run,
        proxies=proxy_list,
    )
