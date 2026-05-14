# reddit-poster

Browser-based Reddit bot with layered human-behavior simulation.
Academic study in bot detection evasion — all real-browser, no API shortcuts.

## Architecture

```
bot/
  common.py              Shared primitives: mouse, keyboard, session, identity pools
  reddit_browser_agent.py  Real Reddit poster via old.reddit.com + proxy rotation
  agent.py               HTTP bot — naive / gaussian / gaussian+proxy modes
  playwright_agent.py    Playwright browser bot vs local sim server
  reddit_agent.py        PRAW OAuth poster (API-based, no browser)
  cloak_agent.py         CloakBrowser stealth variant (optional)
  proxies.example.txt    Proxy list template
  .env.example           Credentials template

server/
  app.py                 Flask sim server — 10-signal bot detection engine
  templates/             submit.html (with JS telemetry) + admin.html dashboard

tests/
  test_smoke.py          53 tests: algorithms, proxy, bot modes, browser E2E
```

---

## Evasion layers

### 1. Mouse movement — triangle waypoints + cubic Bezier + noise

Every click goes through three stages:

1. **Triangle waypoints** — path deviates through 0–2 vertices displaced Gaussian-randomly
   off the A→B line, mimicking how a hand wanders before settling
2. **Cubic Bezier per segment** — two Gaussian-jittered control points + ease-in-out speed
   (`speed = 0.5 + 0.5 * sin(π·t)`)
3. **Per-step Gaussian noise** — ±1.2 px wobble at every interpolation step

Click landing positions use `sigma=15 px` from element center (human-realistic off-center).

### 2. Keystroke timing — Gaussian delays + typo simulation

- Inter-keystroke delay: `gauss(90ms, 40ms)`, clamped 30–450 ms
- 8% chance of a long pause mid-word: additional `gauss(600ms, 250ms)`
- **Typo simulation**: 3% of alphabetic characters trigger an adjacent-key mis-press
  followed by `Backspace` — uses a full 26-key `_ADJACENT` map

### 3. Organic pre-post browsing

Before submitting, `browse_feed()` spends ~35 s (Gaussian) on the target subreddit:
scrolls the feed in chunks, opens 35% of visible posts, reads them, goes back.
Includes random long pauses (12% chance, 6 s) simulating a distracted user.

### 4. Session persistence

After login, the full cookie jar is saved to `bot/.session.json`.
Subsequent runs load those cookies and skip login — avoiding repeated auth events,
which are themselves a detection signal.

### 5. Anti-detection browser init script

Injected into every page via `page.add_init_script()`:
- Hides `navigator.webdriver`
- Injects a plausible `navigator.plugins` list
- Forces `navigator.languages = ['en-US', 'en']`
- Adds subtle canvas noise (defeats exact-hash fingerprinting)
- Tracks cursor position for mouse continuity across navigations

### 6. Per-post proxy rotation

Each post uses a fresh browser context with a different proxy.
Supports HTTP, authenticated HTTP, and SOCKS5.

### 7. Rate-limit detection + backoff

After each submit, scans page body for Reddit's rate-limit phrases.
On detection, backs off `gauss(70 s, 20 s)` before continuing.

---

## Server detection signals (10)

| # | Signal | Threshold |
|---|--------|-----------|
| 1 | Request rate | > 10 req/min per IP |
| 2 | Timing regularity | inter-request interval variance < 0.05 |
| 3 | Bot user-agent | python-requests, curl, wget, scrapy… |
| 4 | Missing headers | no Accept-Language or Accept |
| 5 | Fast form fill | < 1.5 s |
| 6 | Honeypot | hidden `_email` field filled |
| 7 | Keystroke CoV | coefficient of variation of `_kt` intervals < 0.15 |
| 8 | Headless browser tells | Chrome UA + ≥2 missing `Sec-Fetch-*` headers |
| 9 | Low title entropy | Shannon entropy < 2.2 bits/char |
| 10 | Centered clicks | mean normalized distance from element center < 0.04 |

Signals 7–10 require JS telemetry fields sent by `submit.html`:
`_kt` (keydown timestamps), `_cp` (mousedown positions relative to each element).

---

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Create `bot/.env`:
```
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password
```

---

## Usage

### Simulation server

```bash
python server/app.py
# Feed:           http://localhost:5000
# Submit form:    http://localhost:5000/submit
# Admin dashboard: http://localhost:5000/admin
```

### HTTP bots (test detection logic without a browser)

```bash
python bot/agent.py --mode naive          --posts 20   # flagged by 5+ signals
python bot/agent.py --mode gaussian       --posts 20   # evades timing signals
python bot/agent.py --mode gaussian+proxy --posts 20   # evades rate + timing
python bot/agent.py --mode all            --posts 10   # compare all three
```

### Playwright browser bot vs local sim

```bash
python bot/playwright_agent.py --posts 5             # headless
python bot/playwright_agent.py --posts 5 --mode visible
```

### Real Reddit — old.reddit.com

```bash
# Always dry-run first
python bot/reddit_browser_agent.py --sub test --posts 1 --visible --dry-run

# Live post
python bot/reddit_browser_agent.py --sub test   --posts 1 --visible
python bot/reddit_browser_agent.py --sub MMA    --posts 3 --proxy-file bot/proxies.txt

# Clear saved session (force re-login next run)
python bot/reddit_browser_agent.py --clear-session
```

### PRAW OAuth poster (API-based)

```bash
# Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in bot/.env
python bot/reddit_agent.py --subreddit test --posts 1 --dry-run
```

---

## Post bank

Pre-written posts for 5 subreddits — randomly sampled each run:

| Subreddit | Posts |
|-----------|-------|
| test | 3 |
| MMA | 10 |
| gaming | 5 |
| learnprogramming | 4 |
| science | 4 |

---

## Proxy format (`bot/proxies.txt`)

```
http://ip:port
http://user:pass@ip:port
socks5://ip:port
# lines starting with # are ignored
```

---

## Tests

```bash
python -m pytest tests/ -v                        # 52 unit tests
python -m pytest tests/ -v -m "not integration"  # same — skip browser E2E
python -m pytest tests/ -v -m integration        # full Playwright round-trip
```

**53 tests total** (52 unit, 1 integration):

| Suite | Tests | Covers |
|-------|-------|--------|
| TestBezier | 4 | De Casteljau algorithm correctness |
| TestTriangleWaypoints | 5 | Waypoint count, finiteness, distance scaling |
| TestGaussianHelper | 4 | Bounds clamping, return type |
| TestProxyConfig | 4 | HTTP/SOCKS5 parsing, credential extraction |
| TestLoadProxies | 5 | File loading, inline, combined, missing file |
| TestPostBank | 5 | All subreddits non-empty, randomness, fallback |
| TestSessionPersistence | 2 | Save/load round-trip, missing file |
| TestRetry | 4 | First-try success, retry-then-succeed, max failure, exception filter |
| TestTypoSimulation | 3 | Adjacent map completeness, Backspace on typo |
| TestServerDetection | 6 | Shannon entropy, keystroke CoV edge cases |
| TestClickDeviation | 4 | Centered vs off-center clicks, edge cases |
| TestRateLimitDetection | 3 | Phrase match, clean page, exception safety |
| TestBotAgentModes | 3 | Naive flagged, gaussian+proxy passes, stats |
| TestPlaywrightVsSimServer | 1 | Full browser submit round-trip (integration) |
