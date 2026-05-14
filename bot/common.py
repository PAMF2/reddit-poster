"""
Shared primitives: Gaussian mouse movement, structured logging, retry, identity pools.

All bot files import from here — no duplication.
"""
import functools
import logging
import math
import random
import time
from typing import Callable

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ── Gaussian helper ───────────────────────────────────────────────────────────

def g(mu: float, sigma: float, lo: float | None = None, hi: float | None = None) -> float:
    v = random.gauss(mu, sigma)
    if lo is not None: v = max(lo, v)
    if hi is not None: v = min(hi, v)
    return v


# ── Retry with exponential backoff ────────────────────────────────────────────

def retry(attempts: int = 3, base_delay: float = 1.0, exceptions: tuple = (Exception,)):
    """Decorator: retry up to `attempts` times with exponential backoff + jitter."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = get_logger(fn.__module__ or __name__)
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        raise
                    delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    log.warning("%s failed (attempt %d/%d): %s — retry in %.1fs",
                                fn.__name__, attempt, attempts, exc, delay)
                    time.sleep(delay)
        return wrapper
    return decorator


# ── Mouse movement algorithm ──────────────────────────────────────────────────

def _bezier(t: float, pts: list[tuple]) -> tuple[float, float]:
    p = list(pts)
    while len(p) > 1:
        p = [(p[i][0]*(1-t)+p[i+1][0]*t, p[i][1]*(1-t)+p[i+1][1]*t)
             for i in range(len(p)-1)]
    return p[0]


def _triangle_waypoints(
    x0: float, y0: float,
    x1: float, y1: float,
    n: int | None = None,
) -> list[tuple[float, float]]:
    """
    Generate 0-2 perpendicular triangle vertices between (x0,y0) and (x1,y1).

    Each vertex is displaced Gaussian-randomly off the direct line, creating
    organic detours that mimic how a human hand wanders before settling.
    """
    dist = math.hypot(x1-x0, y1-y0) or 1.0
    if n is None:
        n = (random.choices([0, 1, 2], weights=[0.2, 0.5, 0.3])[0]
             if dist > 80 else random.choices([0, 1], weights=[0.5, 0.5])[0])
    waypoints: list[tuple[float, float]] = [(x0, y0)]
    dx, dy = x1-x0, y1-y0
    px, py = -dy/dist, dx/dist
    for t in sorted(random.uniform(0.2, 0.8) for _ in range(n)):
        sigma  = dist * g(0.15, 0.05, lo=0.05, hi=0.35)
        offset = random.gauss(0, sigma)
        waypoints.append((x0+dx*t + px*offset, y0+dy*t + py*offset))
    waypoints.append((x1, y1))
    return waypoints


def _move_segment(page, x0: float, y0: float, x1: float, y1: float) -> None:
    dist  = math.hypot(x1-x0, y1-y0)
    steps = max(12, int(dist / 6))
    jit   = max(10.0, dist * 0.2)
    t1 = g(0.30, 0.08, lo=0.15, hi=0.45)
    t2 = g(0.70, 0.08, lo=0.55, hi=0.85)
    ctrl = [
        (x0, y0),
        (x0+(x1-x0)*t1 + g(0, jit), y0+(y1-y0)*t1 + g(0, jit)),
        (x0+(x1-x0)*t2 + g(0, jit), y0+(y1-y0)*t2 + g(0, jit)),
        (x1, y1),
    ]
    for i in range(steps + 1):
        t = i / steps
        bx, by = _bezier(t, ctrl)
        page.mouse.move(bx + g(0, 1.2), by + g(0, 1.2))
        speed = 0.5 + 0.5 * math.sin(math.pi * t)
        time.sleep(g(0.004, 0.0015, lo=0.001) / (speed + 0.1))


def move_mouse(page, x: float, y: float) -> None:
    cx = page.evaluate("() => window._mx || 400")
    cy = page.evaluate("() => window._my || 300")
    waypoints = _triangle_waypoints(cx, cy, x, y)
    for i in range(len(waypoints) - 1):
        _move_segment(page, *waypoints[i], *waypoints[i + 1])
        if i < len(waypoints) - 2:
            time.sleep(g(0.04, 0.02, lo=0.01))
    page.evaluate(f"() => {{ window._mx = {x}; window._my = {y}; }}")


def gaussian_click(page, selector: str, sigma: float = 3.0, timeout: int = 10000) -> None:
    el  = page.locator(selector).first
    el.wait_for(state="visible", timeout=timeout)
    box = el.bounding_box()
    if not box:
        el.click()
        return
    cx = box["x"] + box["width"]  / 2 + g(0, sigma)
    cy = box["y"] + box["height"] / 2 + g(0, sigma)
    move_mouse(page, cx, cy)
    hold = int(g(90, 35, lo=40, hi=320))
    page.mouse.down()
    time.sleep(hold / 1000)
    page.mouse.up()


# Adjacent keyboard keys for realistic typo simulation
_ADJACENT: dict[str, str] = {
    'a': 'sqwz',  'b': 'vghn',  'c': 'xdfv',  'd': 'serfcx',
    'e': 'wsdr',  'f': 'drtgvc','g': 'ftyhbv', 'h': 'gyujnb',
    'i': 'ujko',  'j': 'huikmn','k': 'jiolm',  'l': 'kop',
    'm': 'njk',   'n': 'bhjm',  'o': 'iklp',   'p': 'ol',
    'q': 'wa',    'r': 'edft',  's': 'awedxz', 't': 'rfgy',
    'u': 'yhji',  'v': 'cfgb',  'w': 'qase',   'x': 'zsdc',
    'y': 'tghu',  'z': 'asx',
}


def gaussian_type(page, selector: str, text: str, timeout: int = 10000) -> None:
    """
    Type text with Gaussian inter-keystroke delays and occasional typos.

    ~3 % of alphabetic characters trigger an adjacent-key typo followed by
    a Backspace correction, mimicking how a real human hand slips.
    """
    gaussian_click(page, selector, timeout=timeout)
    time.sleep(g(0.35, 0.12, lo=0.15))
    for char in text:
        # Typo: press an adjacent key, pause, backspace, then type the right char
        if char.isalpha() and random.random() < 0.03:
            neighbours = _ADJACENT.get(char.lower(), 'x')
            page.keyboard.type(random.choice(neighbours))
            time.sleep(g(0.17, 0.06, lo=0.08))
            page.keyboard.press('Backspace')
            time.sleep(g(0.13, 0.05, lo=0.06))

        page.keyboard.type(char)
        delay = g(0.09, 0.04, lo=0.03, hi=0.45)
        if random.random() < 0.08:
            delay += g(0.6, 0.25, lo=0.25)
        time.sleep(delay)


def gaussian_scroll(page, pixels: int | None = None) -> None:
    if pixels is None:
        pixels = int(g(300, 120, lo=50))
    page.mouse.wheel(0, pixels)
    time.sleep(g(0.4, 0.15, lo=0.1))


# ── Anti-detection browser init script ───────────────────────────────────────

STEALTH_SCRIPT = """
(() => {
    // Remove automation flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Plausible plugin list
    Object.defineProperty(navigator, 'plugins', {
        get: () => [{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer'},
                    {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                    {name:'Native Client',    filename:'internal-nacl-plugin'}]
    });

    // Consistent language
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

    // Canvas noise — subtle, defeats exact-hash fingerprinting
    const _getCtx = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...a) {
        const ctx = _getCtx.call(this, type, ...a);
        if (type === '2d') {
            const _fill = ctx.fillText.bind(ctx);
            ctx.fillText = function(t, x, y, ...r) {
                ctx.save();
                ctx.translate(Math.random() * 0.08 - 0.04, Math.random() * 0.08 - 0.04);
                _fill(t, x, y, ...r);
                ctx.restore();
            };
        }
        return ctx;
    };

    // Track real mouse position for move_mouse continuity
    window._mx = 400; window._my = 300;
    document.addEventListener('mousemove', e => { window._mx = e.clientX; window._my = e.clientY; });
})();
"""


# ── Session (cookie) persistence ──────────────────────────────────────────────

import json
from pathlib import Path

_SESSION_PATH = Path(__file__).parent / ".session.json"


def save_session(context, path: Path = _SESSION_PATH) -> None:
    """Persist browser cookies so next run skips the login step."""
    cookies = context.cookies()
    path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    get_logger(__name__).info("Session saved (%d cookies)", len(cookies))


def load_session(context, path: Path = _SESSION_PATH) -> bool:
    """Load persisted cookies. Returns True if session was found."""
    if not path.exists():
        return False
    try:
        cookies = json.loads(path.read_text(encoding="utf-8"))
        context.add_cookies(cookies)
        get_logger(__name__).info("Session loaded (%d cookies)", len(cookies))
        return True
    except Exception as exc:
        get_logger(__name__).warning("Could not load session: %s", exc)
        return False


def clear_session(path: Path = _SESSION_PATH) -> None:
    if path.exists():
        path.unlink()


# ── Identity pools ─────────────────────────────────────────────────────────────

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

TIMEZONES = [
    "America/New_York", "America/Chicago",
    "America/Los_Angeles", "America/Denver", "America/Phoenix",
]

LOCALES = ["en-US", "en-GB", "en-CA", "en-AU"]


# ── Post bank ─────────────────────────────────────────────────────────────────

POST_BANK: dict[str, list[tuple[str, str]]] = {
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
        ("Khamzat Chimaev next opponent — who makes sense?",
         "Whittaker would be the most interesting stylistic matchup at this point."),
        ("GOAT debate: Silva vs Jones vs GSP",
         "Silva's prime was something else entirely. The Anderson we saw at his peak was untouchable."),
        ("Ngannou vs Fury 2 in the works?",
         "Would love to see this with a proper training camp behind it. The first fight showed Francis belongs."),
        ("Why does the UFC keep making bad matchups?",
         "Feels like they don't actually watch the fights anymore. Some of these cards are embarrassing."),
        ("McGregor vs Chandler — who lands first?",
         "Chandler's timing is elite. If Conor isn't sharp in round 1 this gets ugly fast."),
    ],
}


def pick_post(sub: str) -> tuple[str, str]:
    bank = POST_BANK.get(sub, POST_BANK["test"])
    return random.choice(bank)
