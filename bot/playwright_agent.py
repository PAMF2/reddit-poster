"""
Gaussian click bot using Playwright.

Mouse movement algorithm:
  1. Random triangle waypoints — the path deviates through 0-2 triangle
     vertices scattered perpendicularly off the direct line A→B.
     Each vertex is placed at a Gaussian perpendicular offset from the
     straight-line midpoint, creating organic curved detours.
  2. Per-segment Bezier curves — each A→waypoint→B segment is a cubic
     Bezier with Gaussian-jittered control points.
  3. Per-step Gaussian noise — small wobble (±1.5 px) added at every step.
  4. Ease-in-out speed — sine curve on step timing, slower at start/end.

Usage:
  pip install playwright
  playwright install chromium

  python sim/bot/playwright_agent.py --mode visible  --posts 5   # watch it move
  python sim/bot/playwright_agent.py --mode headless --posts 20
"""
import argparse
import math
import random
import time
from dataclasses import dataclass, field

from playwright.sync_api import Page, sync_playwright

BASE_URL = "http://localhost:5000"


# ── Gaussian primitives ────────────────────────────────────────────────────────

def g(mu: float, sigma: float, lo: float | None = None, hi: float | None = None) -> float:
    v = random.gauss(mu, sigma)
    if lo is not None: v = max(lo, v)
    if hi is not None: v = min(hi, v)
    return v


def gaussian_delay(mu=2.5, sigma=0.9, lo=0.5) -> float:
    return g(mu, sigma, lo=lo)


# ── Triangle waypoints ─────────────────────────────────────────────────────────

def _triangle_waypoints(
    x0: float, y0: float,
    x1: float, y1: float,
    n: int | None = None,
) -> list[tuple[float, float]]:
    """
    Generate intermediate waypoints that form triangular detours.

    For each triangle, a vertex is placed at a random position along the
    direct line A→B, then displaced perpendicularly by a Gaussian amount.
    The displacement magnitude scales with distance so short moves get
    small detours and long moves can swing wider.

              vertex (triangle peak)
               *
              / \\
             /   \\
    A ──────*─────*────── B
          t=0.3  t=0.7

    Returns ordered list: [A, v1, v2, ..., B]
    """
    if n is None:
        dist = math.hypot(x1 - x0, y1 - y0)
        # Short moves: 0-1 detours. Long moves: 1-2.
        n = random.choices([0, 1, 2], weights=[0.2, 0.5, 0.3])[0] if dist > 80 else random.choices([0, 1], weights=[0.5, 0.5])[0]

    waypoints: list[tuple[float, float]] = [(x0, y0)]
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy) or 1.0

    # Unit perpendicular vector (rotated 90°)
    px = -dy / dist
    py =  dx / dist

    # Spread triangles along the path avoiding start/end
    slots = sorted(random.uniform(0.2, 0.8) for _ in range(n))

    for t in slots:
        # Point on direct line at parameter t
        mx = x0 + dx * t
        my = y0 + dy * t

        # Gaussian perpendicular offset — sigma ~15% of distance, random sign
        sigma   = dist * g(0.15, 0.05, lo=0.05, hi=0.35)
        offset  = random.gauss(0, sigma)
        vx = mx + px * offset
        vy = my + py * offset
        waypoints.append((vx, vy))

    waypoints.append((x1, y1))
    return waypoints


# ── Bezier segment ─────────────────────────────────────────────────────────────

def _bezier(t: float, pts: list[tuple[float, float]]) -> tuple[float, float]:
    p = list(pts)
    while len(p) > 1:
        p = [(p[i][0]*(1-t) + p[i+1][0]*t,
              p[i][1]*(1-t) + p[i+1][1]*t) for i in range(len(p)-1)]
    return p[0]


def _move_segment(
    page: Page,
    x0: float, y0: float,
    x1: float, y1: float,
) -> None:
    """Move along one segment using a cubic Bezier + per-step Gaussian noise."""
    dist  = math.hypot(x1 - x0, y1 - y0)
    steps = max(12, int(dist / 6))

    # Cubic Bezier: two Gaussian-jittered control points
    jitter = max(10.0, dist * 0.2)
    t1 = g(0.30, 0.08, lo=0.15, hi=0.45)
    t2 = g(0.70, 0.08, lo=0.55, hi=0.85)
    ctrl = [
        (x0, y0),
        (x0 + (x1-x0)*t1 + g(0, jitter), y0 + (y1-y0)*t1 + g(0, jitter)),
        (x0 + (x1-x0)*t2 + g(0, jitter), y0 + (y1-y0)*t2 + g(0, jitter)),
        (x1, y1),
    ]

    for i in range(steps + 1):
        t       = i / steps
        bx, by  = _bezier(t, ctrl)
        # Small per-step Gaussian wobble
        bx += g(0, 1.2)
        by += g(0, 1.2)
        page.mouse.move(bx, by)

        # Ease-in-out: slow at start and end of each segment
        speed      = 0.5 + 0.5 * math.sin(math.pi * t)
        step_delay = g(0.004, 0.0015, lo=0.001) / (speed + 0.1)
        time.sleep(step_delay)


# ── Public move_mouse ──────────────────────────────────────────────────────────

def move_mouse(page: Page, x: float, y: float) -> None:
    """
    Move from current tracked position to (x, y).

    Path = random triangle waypoints  ×  per-segment Bezier  ×  Gaussian noise.
    """
    cur_x = page.evaluate("() => window._mx || 400")
    cur_y = page.evaluate("() => window._my || 300")

    waypoints = _triangle_waypoints(cur_x, cur_y, x, y)

    for i in range(len(waypoints) - 1):
        ax, ay = waypoints[i]
        bx, by = waypoints[i + 1]
        _move_segment(page, ax, ay, bx, by)

        # Tiny pause at each triangle vertex (human micro-correction)
        if i < len(waypoints) - 2:
            time.sleep(g(0.04, 0.02, lo=0.01))

    page.evaluate(f"() => {{ window._mx = {x}; window._my = {y}; }}")


def gaussian_click(page: Page, selector: str, sigma: float = 3.0) -> None:
    """
    Click an element with a Gaussian offset from its center.
    Simulates human imprecision (clicks don't land exactly on center).
    """
    el  = page.locator(selector).first
    box = el.bounding_box()
    if not box:
        el.click()
        return

    cx = box["x"] + box["width"]  / 2 + g(0, sigma)
    cy = box["y"] + box["height"] / 2 + g(0, sigma)

    move_mouse(page, cx, cy)

    # Gaussian hold duration between mousedown and mouseup
    hold_ms = int(g(80, 30, lo=30, hi=300))
    page.mouse.down()
    time.sleep(hold_ms / 1000)
    page.mouse.up()


def gaussian_type(page: Page, selector: str, text: str) -> None:
    """
    Type text with Gaussian inter-keystroke delays.
    Simulates human typing rhythm with occasional micro-pauses.
    """
    gaussian_click(page, selector)
    time.sleep(g(0.3, 0.1, lo=0.1))

    for i, char in enumerate(text):
        page.keyboard.type(char)
        delay = g(0.08, 0.04, lo=0.02, hi=0.4)

        # Occasional longer pause (thinking, hesitation)
        if random.random() < 0.05:
            delay += g(0.5, 0.2, lo=0.2)

        time.sleep(delay)


def gaussian_scroll(page: Page, pixels: int | None = None) -> None:
    """Scroll the page a Gaussian amount."""
    if pixels is None:
        pixels = int(g(300, 120, lo=50))
    page.mouse.wheel(0, pixels)
    time.sleep(g(0.4, 0.15, lo=0.1))


# ── post bank ──────────────────────────────────────────────────────────────────

_POSTS = [
    ("Jon Jones vs Stipe — who wins the rematch?", "Jones by decision, Stipe's chin can't survive 5 rounds."),
    ("Best KO finish of the decade?", "Velasquez vs Brock still hits different. Fury and speed."),
    ("UFC 305 fight week predictions", "Dricus has been looking sharp. This could be a statement win."),
    ("Is wrestling still the most important base in MMA?", "With how BJJ has evolved, I'd argue striking is more important now."),
    ("Poirier's legacy after retirement", "Top 5 LW all time. The body shot against McGregor was art."),
    ("Izzy's striking variety is insane", "Seven different striking styles in one fighter. Unprecedented."),
    ("Adesanya vs Pereira 3 — would you watch?", "Yes but only if Izzy has fixed the clinch game."),
    ("Paddy Pimblett has top 5 grappling in the division", "His guard work is genuinely elite, people sleep on it."),
    ("Ngannou should go back to MMA", "Boxing career was respectable but MMA was his true calling."),
    ("McGregor vs Chandler — who lands first?", "Chandler's timing is elite. Conor has to box at range."),
]


# ── Playwright bot ─────────────────────────────────────────────────────────────

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


def _run_bot(page: Page, posts: list[tuple[str, str]], stats: PlaywrightStats) -> None:
    # Initial page load with scroll (simulate browsing before posting)
    page.goto(BASE_URL, wait_until="domcontentloaded")
    time.sleep(g(1.5, 0.5, lo=0.8))
    gaussian_scroll(page, pixels=int(g(200, 80, lo=50)))
    time.sleep(g(1.0, 0.4, lo=0.4))

    for i, (title, body) in enumerate(posts):
        print(f"[{i+1:>2}/{len(posts)}] Navigating to submit form...")

        # Navigate to submit
        page.goto(f"{BASE_URL}/submit", wait_until="domcontentloaded")
        time.sleep(g(0.8, 0.3, lo=0.3))

        # Simulate reading the page before typing
        gaussian_scroll(page, pixels=int(g(80, 30, lo=10)))
        time.sleep(g(1.2, 0.5, lo=0.5))

        # Type title with Gaussian keystroke timing
        print(f"         Typing title ({len(title)} chars)...")
        gaussian_type(page, "input[name='title']", title)
        time.sleep(g(0.6, 0.2, lo=0.2))

        # Scroll down slightly before typing body
        gaussian_scroll(page, pixels=int(g(60, 20, lo=10)))
        time.sleep(g(0.5, 0.2, lo=0.2))

        # Type body
        if body:
            print(f"         Typing body ({len(body)} chars)...")
            gaussian_type(page, "textarea[name='body']", body)
            time.sleep(g(0.8, 0.3, lo=0.3))

        # Gaussian pause before submitting (re-reading)
        read_pause = g(2.0, 0.8, lo=0.8)
        print(f"         Re-reading for {read_pause:.1f}s...")
        time.sleep(read_pause)

        # Click submit
        print(f"         Clicking submit...")
        gaussian_click(page, "button[type='submit']", sigma=4.0)
        time.sleep(g(1.5, 0.5, lo=0.8))

        # Read response
        try:
            result_el = page.locator("#result").first
            result_text = result_el.inner_text(timeout=3000)
        except Exception:
            result_text = ""

        stats.sent += 1
        if "FLAGGED" in result_text.upper():
            stats.flagged += 1
            print(f"  [FLAG] {title[:45]}")
        elif "Posted" in result_text or "ID:" in result_text:
            stats.passed += 1
            print(f"  [PASS] {title[:45]}")
        else:
            stats.errors += 1
            print(f"  [??  ] {title[:45]} — {result_text[:60]}")

        # Gaussian pause between posts (browsing behavior)
        if i < len(posts) - 1:
            pause = gaussian_delay(mu=3.5, sigma=1.2, lo=1.5)
            print(f"         Waiting {pause:.1f}s before next post...")
            # Occasionally go back to index (simulate browsing)
            if random.random() < 0.3:
                page.goto(BASE_URL, wait_until="domcontentloaded")
                gaussian_scroll(page, pixels=int(g(300, 100, lo=80)))
                time.sleep(g(1.5, 0.5, lo=0.5))
            time.sleep(pause)


def run(n: int = 10, headless: bool = True) -> PlaywrightStats:
    sample = random.sample(_POSTS * ((n // len(_POSTS)) + 1), n)
    stats  = PlaywrightStats()

    print(f"\n{'='*60}")
    print(f"  Playwright Gaussian-click bot")
    print(f"  Posts: {n}  |  Headless: {headless}")
    print(f"  Bezier mouse curves + Gaussian keystroke timing")
    print(f"{'='*60}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
            user_agent=random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            ]),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = ctx.new_page()

        # Inject mouse position tracker
        page.add_init_script("window._mx = 400; window._my = 300;")

        try:
            _run_bot(page, sample, stats)
        except Exception as exc:
            print(f"\n[ERR] {exc}")
            stats.errors += 1
        finally:
            browser.close()

    print(f"\n{'─'*60}")
    print(f"  Results: {stats.passed} passed / {stats.flagged} flagged / {stats.errors} errors")
    print(f"  Detection rate: {stats.detection_rate:.1f}%")
    print(f"{'─'*60}\n")
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gaussian click bot (Playwright)")
    parser.add_argument("--posts",    type=int, default=10)
    parser.add_argument("--mode",     choices=["headless", "visible"], default="visible",
                        help="visible = watch the mouse move in real browser")
    args = parser.parse_args()

    run(n=args.posts, headless=(args.mode == "headless"))
