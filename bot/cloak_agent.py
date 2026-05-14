"""
Gaussian click bot using CloakBrowser (stealth Chromium).

Drop-in replacement for playwright_agent.py — same triangle+Gaussian
mouse movement, but running on a Chromium binary with 49 C++ fingerprint
patches compiled in. navigator.webdriver = false, canvas randomized, etc.

Usage:
  python sim/bot/cloak_agent.py --posts 5 --visible
  python sim/bot/cloak_agent.py --posts 20
"""
import argparse
import math
import random
import time
from dataclasses import dataclass, field

from cloakbrowser import launch

BASE_URL = "http://localhost:5000"


# ── Gaussian primitives ────────────────────────────────────────────────────────

def g(mu, sigma, lo=None, hi=None):
    v = random.gauss(mu, sigma)
    if lo is not None: v = max(lo, v)
    if hi is not None: v = min(hi, v)
    return v


# ── Triangle waypoints + Bezier (same as playwright_agent) ────────────────────

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
    t1, t2 = g(0.30,0.08,lo=0.15,hi=0.45), g(0.70,0.08,lo=0.55,hi=0.85)
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
        time.sleep(g(0.004,0.0015,lo=0.001) / (speed+0.1))


def move_mouse(page, x, y):
    cx = page.evaluate("() => window._mx || 400")
    cy = page.evaluate("() => window._my || 300")
    for i, ((ax,ay),(bx,by)) in enumerate(
            zip(_triangle_waypoints(cx,cy,x,y),
                _triangle_waypoints(cx,cy,x,y)[1:])):
        _move_segment(page, ax, ay, bx, by)
        if i < len(_triangle_waypoints(cx,cy,x,y))-2:
            time.sleep(g(0.04,0.02,lo=0.01))
    page.evaluate(f"() => {{ window._mx={x}; window._my={y}; }}")


def gaussian_click(page, selector, sigma=3.0):
    el  = page.locator(selector).first
    box = el.bounding_box()
    if not box:
        el.click(); return
    cx = box["x"] + box["width"]/2  + g(0, sigma)
    cy = box["y"] + box["height"]/2 + g(0, sigma)
    move_mouse(page, cx, cy)
    hold = int(g(80, 30, lo=30, hi=300))
    page.mouse.down()
    time.sleep(hold/1000)
    page.mouse.up()


def gaussian_type(page, selector, text):
    gaussian_click(page, selector)
    time.sleep(g(0.3, 0.1, lo=0.1))
    for char in text:
        page.keyboard.type(char)
        delay = g(0.08, 0.04, lo=0.02, hi=0.4)
        if random.random() < 0.05:
            delay += g(0.5, 0.2, lo=0.2)
        time.sleep(delay)


# ── Post bank ─────────────────────────────────────────────────────────────────

_POSTS = [
    ("Jon Jones vs Stipe — who wins the rematch?", "Jones by decision, Stipe's chin can't survive 5 rounds."),
    ("Best KO finish of the decade?", "Velasquez vs Brock still hits different. Fury and speed."),
    ("UFC 305 fight week predictions", "Dricus has been looking sharp. This could be a statement win."),
    ("Is wrestling still the most important base in MMA?", "With how BJJ has evolved, I'd argue striking matters more now."),
    ("Poirier's legacy after retirement", "Top 5 LW all time. The body shot against McGregor was art."),
    ("Izzy's striking variety is insane", "Seven different striking styles in one fighter. Unprecedented."),
    ("Adesanya vs Pereira 3 — would you watch?", "Yes but only if Izzy fixes his clinch game first."),
    ("Paddy Pimblett has top 5 grappling in the division", "His guard work is genuinely elite, people sleep on it."),
    ("Ngannou should go back to MMA", "Boxing career was respectable but MMA was his true calling."),
    ("McGregor vs Chandler — who lands first?", "Chandler timing is elite. Conor has to box at range."),
]


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    sent: int = 0
    passed: int = 0
    flagged: int = 0
    errors: int = 0

    @property
    def detection_rate(self):
        return 100 * self.flagged / max(self.sent, 1)


# ── Bot ───────────────────────────────────────────────────────────────────────

def run(n=10, visible=False):
    posts  = random.sample(_POSTS * ((n//len(_POSTS))+1), n)
    stats  = Stats()

    print(f"\n{'='*60}")
    print(f"  CloakBrowser Gaussian bot  |  posts={n}  visible={visible}")
    print(f"  Chromium 146 + 49 C++ stealth patches")
    print(f"{'='*60}\n")

    browser = launch(
        headless=not visible,
        args=["--no-sandbox", "--disable-notifications"],
    )

    ctx = browser.new_context(
        viewport={"width": random.randint(1280,1920), "height": random.randint(768,1080)},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    page = ctx.new_page()
    page.add_init_script("window._mx=400; window._my=300;")

    try:
        # Land on index first, scroll a bit
        page.goto(BASE_URL, wait_until="domcontentloaded")
        time.sleep(g(1.5, 0.5, lo=0.8))
        page.mouse.wheel(0, int(g(200,80,lo=50)))
        time.sleep(g(1.0, 0.4, lo=0.4))

        for i, (title, body) in enumerate(posts):
            print(f"[{i+1:>2}/{n}] Navigating to submit...")
            page.goto(f"{BASE_URL}/submit", wait_until="domcontentloaded")
            time.sleep(g(0.8, 0.3, lo=0.3))

            page.mouse.wheel(0, int(g(60,25,lo=10)))
            time.sleep(g(1.0, 0.4, lo=0.4))

            print(f"         Typing title ({len(title)} chars)...")
            gaussian_type(page, "input[name='title']", title)
            time.sleep(g(0.6, 0.2, lo=0.2))

            if body:
                print(f"         Typing body ({len(body)} chars)...")
                gaussian_type(page, "textarea[name='body']", body)
                time.sleep(g(0.8, 0.3, lo=0.3))

            pause = g(2.0, 0.8, lo=0.8)
            print(f"         Re-reading for {pause:.1f}s...")
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
                print(f"  [FLAG] {title[:50]}")
            elif "ID:" in txt or "Posted" in txt:
                stats.passed += 1
                print(f"  [PASS] {title[:50]}")
            else:
                stats.errors += 1
                print(f"  [??  ] {title[:50]}  response={txt[:40]}")

            if i < n-1:
                wait = g(3.5, 1.2, lo=1.5)
                print(f"         Waiting {wait:.1f}s...")
                if random.random() < 0.3:
                    page.goto(BASE_URL, wait_until="domcontentloaded")
                    page.mouse.wheel(0, int(g(250,100,lo=60)))
                    time.sleep(g(1.2, 0.5, lo=0.5))
                time.sleep(wait)

    except Exception as exc:
        print(f"\n[ERR] {exc}")
        stats.errors += 1
    finally:
        browser.close()

    print(f"\n{'-'*60}")
    print(f"  Results: {stats.passed} passed / {stats.flagged} flagged / {stats.errors} errors")
    print(f"  Detection rate: {stats.detection_rate:.1f}%")
    print(f"{'-'*60}\n")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CloakBrowser Gaussian bot")
    parser.add_argument("--posts",   type=int, default=10)
    parser.add_argument("--visible", action="store_true", help="Show browser window")
    args = parser.parse_args()
    run(n=args.posts, visible=args.visible)
