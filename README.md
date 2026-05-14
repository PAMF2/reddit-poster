# reddit-poster

Browser-based Reddit bot with human-like behavior simulation.

## Architecture

```
bot/
  agent.py               HTTP bot — naive / gaussian / gaussian+proxy modes
  playwright_agent.py    Playwright browser bot with Gaussian mouse movement
  reddit_browser_agent.py  Real Reddit poster via old.reddit.com + proxy rotation
  reddit_agent.py        PRAW OAuth poster (API-based, no browser)
  cloak_agent.py         CloakBrowser stealth variant (optional)
  proxies.example.txt    Proxy list template
  .env.example           Credentials template

server/
  app.py                 Flask sim server with bot detection engine
  templates/             submit.html + admin.html dashboard

tests/
  test_smoke.py          34 tests: algorithms, proxy parsing, bot modes, browser E2E
```

## Mouse movement algorithm

Every click goes through three layers:

1. **Triangle waypoints** — path deviates through 0-2 vertices placed perpendicularly off the A→B line with a Gaussian offset
2. **Cubic Bezier per segment** — two Gaussian-jittered control points per segment
3. **Per-step Gaussian noise** — ±1.2px wobble at every step + ease-in-out speed curve

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

## Run the simulation server

```bash
python server/app.py
# Admin dashboard: http://localhost:5000/admin
```

## Bot modes

```bash
# HTTP bots — test detection logic
python bot/agent.py --mode naive --posts 20
python bot/agent.py --mode gaussian --posts 20
python bot/agent.py --mode gaussian+proxy --posts 20
python bot/agent.py --mode all --posts 10

# Playwright browser bot vs local sim
python bot/playwright_agent.py --posts 5 --mode visible

# Real Reddit — dry-run first
python bot/reddit_browser_agent.py --sub test --posts 1 --visible --dry-run

# Real Reddit with proxy rotation
python bot/reddit_browser_agent.py --sub test --posts 3 --proxy-file bot/proxies.txt
```

## Tests

```bash
python -m pytest tests/ -v
```

34 tests — Bezier curves, triangle waypoints, Gaussian bounds, proxy parsing, HTTP bot agent modes, full Playwright browser round-trip.

## Proxy format (`bot/proxies.txt`)

```
http://ip:port
http://user:pass@ip:port
socks5://ip:port
```
