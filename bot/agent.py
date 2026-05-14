"""
HTTP bot agent — three modes to benchmark detection signals.

  naive          — constant 0.2s delay, python-requests UA, no headers
  gaussian       — Gaussian timing, realistic UA + Accept-Language
  gaussian+proxy — everything above + X-Forwarded-For IP rotation

Usage:
  python bot/agent.py --mode naive          --posts 20
  python bot/agent.py --mode gaussian       --posts 20
  python bot/agent.py --mode gaussian+proxy --posts 20
  python bot/agent.py --mode all            --posts 10
"""
import argparse
import random
import time
from collections import Counter
from dataclasses import dataclass, field

import requests

from bot.common import UA_POOL, POST_BANK, g, get_logger

log = get_logger(__name__)

BASE_URL = "http://localhost:5000"

# RFC 5737 documentation IPs — safe fake headers for local testing only
_PROXY_POOL = [
    "203.0.113.1",  "203.0.113.2",  "203.0.113.3",  "203.0.113.4",
    "203.0.113.5",  "203.0.113.6",  "203.0.113.7",  "203.0.113.8",
    "198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4",
    "192.0.2.10",   "192.0.2.11",   "192.0.2.12",   "192.0.2.13",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,pt;q=0.8",
    "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "es-ES,es;q=0.9,en;q=0.8",
]

_POST_BANK = [t for posts in POST_BANK.values() for t in posts]


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class BotStats:
    sent:    int = 0
    passed:  int = 0
    flagged: int = 0
    reasons: list = field(default_factory=list)

    @property
    def detection_rate(self) -> float:
        return 100 * self.flagged / max(self.sent, 1)


# ── Agent ─────────────────────────────────────────────────────────────────────

class BotAgent:
    """
    Submits posts via plain HTTP (no browser).
    Three modes demonstrate how each evasion layer affects detection rate.
    """

    def __init__(self, mode: str = "gaussian+proxy"):
        self.mode    = mode
        self.stats   = BotStats()
        self.session = requests.Session()
        self._ip: str = ""
        self._ua: str = ""

    def _rotate_identity(self) -> None:
        if "proxy" in self.mode:
            self._ip = random.choice(_PROXY_POOL)
        if self.mode != "naive":
            self._ua = random.choice(UA_POOL)

    def _headers(self) -> dict:
        if self.mode == "naive":
            return {}
        h = {
            "User-Agent":      self._ua,
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
        }
        if self._ip:
            h["X-Forwarded-For"] = self._ip
        return h

    def _delay(self) -> float:
        if self.mode == "naive":
            return 0.2
        return g(2.5, 0.9, lo=0.8)

    def _fill_time(self) -> float:
        if self.mode == "naive":
            return 0.05
        return g(4.5, 1.8, lo=1.5)

    def post(self, title: str, body: str) -> dict | None:
        self._rotate_identity()
        fill_t     = self._fill_time()
        ip_display = self._ip or "default"

        try:
            r = self.session.post(
                f"{BASE_URL}/submit",
                data={"title": title, "body": body, "_t": round(fill_t, 2), "_email": ""},
                headers=self._headers(),
                timeout=10,
            )
            data = r.json()
            self.stats.sent += 1

            if r.status_code == 200:
                self.stats.passed += 1
                log.info("[PASS] %-45s  ip=%-15s  fill=%.1fs", title[:45], ip_display, fill_t)
            else:
                self.stats.flagged += 1
                self.stats.reasons.extend(data.get("reasons", []))
                log.warning("[FLAG] %-45s  ip=%-15s  reasons=%s",
                            title[:45], ip_display, data.get("reasons"))
            return data

        except requests.exceptions.ConnectionError:
            log.error("Cannot connect to server — is it running? (python server/app.py)")
            return None
        except Exception as exc:
            log.error("Request failed: %s", exc)
            return None

    def run(self, n: int = 20) -> BotStats:
        pool   = _POST_BANK * ((n // len(_POST_BANK)) + 1)
        sample = random.sample(pool, n)

        log.info("Mode: %s | Posts: %d", self.mode, n)

        for i, (title, body) in enumerate(sample):
            log.info("[%d/%d] Submitting", i + 1, n)
            result = self.post(title, body)
            if result is None:
                break
            if i < n - 1:
                delay = self._delay()
                log.info("    wait %.2fs", delay)
                time.sleep(delay)

        log.info("Results: %d passed / %d flagged / %d sent | detection=%.1f%%",
                 self.stats.passed, self.stats.flagged, self.stats.sent, self.stats.detection_rate)
        if self.stats.reasons:
            log.info("Top reasons: %s", Counter(self.stats.reasons).most_common(5))
        return self.stats


# ── CLI ───────────────────────────────────────────────────────────────────────

def _compare(n: int) -> None:
    results = {}
    for mode in ("naive", "gaussian", "gaussian+proxy"):
        results[mode] = BotAgent(mode=mode).run(n=n)

    log.info("\n%s", "=" * 60)
    log.info("  COMPARISON")
    log.info("  %-20s %6s %8s %8s %10s", "Mode", "Sent", "Passed", "Flagged", "Det.Rate")
    for mode, s in results.items():
        log.info("  %-20s %6d %8d %8d %9.1f%%",
                 mode, s.sent, s.passed, s.flagged, s.detection_rate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP bot agent")
    parser.add_argument("--mode",  default="gaussian+proxy",
                        choices=["naive", "gaussian", "gaussian+proxy", "all"])
    parser.add_argument("--posts", type=int, default=20)
    args = parser.parse_args()

    if args.mode == "all":
        _compare(n=args.posts)
    else:
        BotAgent(mode=args.mode).run(n=args.posts)
