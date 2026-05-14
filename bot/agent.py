"""
Bot agent with three modes to test detection:

  naive           -- constant 0.2s delay, no proxy rotation, python-requests UA
  gaussian        -- Gaussian timing, realistic UA, Accept-Language headers
  gaussian+proxy  -- Gaussian timing + IP rotation via X-Forwarded-For

Usage:
  python sim/bot/agent.py --mode naive --posts 20
  python sim/bot/agent.py --mode gaussian --posts 20
  python sim/bot/agent.py --mode gaussian+proxy --posts 20
  python sim/bot/agent.py --mode all --posts 10   # run all three and compare
"""
import argparse
import random
import time
from dataclasses import dataclass, field

import requests

BASE_URL = "http://localhost:5000"

# ── identity pools ─────────────────────────────────────────────────────────────

# RFC 5737 documentation IPs — safe to use as fake headers in local testing
_PROXY_POOL = [
    "203.0.113.1",  "203.0.113.2",  "203.0.113.3",  "203.0.113.4",
    "203.0.113.5",  "203.0.113.6",  "203.0.113.7",  "203.0.113.8",
    "198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4",
    "192.0.2.10",   "192.0.2.11",   "192.0.2.12",   "192.0.2.13",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,pt;q=0.8",
    "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "es-ES,es;q=0.9,en;q=0.8",
]

# Sample posts to submit
_POST_BANK = [
    ("Jon Jones vs Stipe — who wins?", "I think Jones takes it by unanimous decision."),
    ("Best submission finish of the decade?", "Maia vs Shields has to be up there."),
    ("UFC 305 predictions thread", "Dricus looking sharp in camp footage."),
    ("Is wrestling still the most important base?", "With the evolution of BJJ I'm not so sure."),
    ("Khamzat Chimaev next opponent leaked", "Sources say it's going to be Whittaker."),
    ("GOAT debate: Silva vs Jones vs GSP", "Silva's prime was something else entirely."),
    ("Poirier announces retirement", "What a career. Diamond was special."),
    ("Conor's boxing record is being ignored", "Everyone focuses on MMA but his hands are elite."),
    ("Ngannou vs Fury 2 in the works?", "Would love to see this happen with proper training camp."),
    ("Why does the UFC keep making bad matchups?", "Feels like they don't watch the fights."),
    ("McGregor vs Chandler prediction", "Chandler by TKO round 2 if Conor isn't sharp."),
    ("Izzy's striking is massively underrated", "The angles he creates are unlike anything in MMA."),
    ("Adesanya vs Pereira trilogy confirmed?", "This matchup never gets old."),
    ("Training report: drilling guard passing", "Spent 2 hours on torreando today, feeling good."),
    ("Paddy Pimblett hype — justified?", "His grappling is genuinely elite but chin is a concern."),
    ("What's the best MMA gym in Brazil?", "Chute Boxe legacy is unmatched."),
    ("Volkanovski's chin is criminally underrated", "Took those Islam shots and kept fighting."),
    ("Curtis Blaydes power is insane", "People forget how hard that man hits."),
    ("Bisping commentating is the best", "Honest, knowledgeable and funny."),
    ("Alexa Grasso vs Valentina 3?", "Can't get enough of this rivalry."),
]


# ── Gaussian utilities ─────────────────────────────────────────────────────────

def gaussian_delay(mu: float = 2.5, sigma: float = 0.9, lo: float = 0.8) -> float:
    return max(lo, random.gauss(mu, sigma))


def gaussian_fill_time(mu: float = 4.5, sigma: float = 1.8, lo: float = 1.5) -> float:
    """Simulate how long a human takes to fill in a form."""
    return max(lo, random.gauss(mu, sigma))


# ── bot agent ──────────────────────────────────────────────────────────────────

@dataclass
class BotStats:
    sent:    int = 0
    passed:  int = 0
    flagged: int = 0
    reasons: list = field(default_factory=list)

    @property
    def detection_rate(self) -> float:
        return 100 * self.flagged / max(self.sent, 1)


class BotAgent:
    """
    mode options:
      naive           — bare requests, constant timing, default UA
      gaussian        — realistic UA + headers + Gaussian inter-request delay
      gaussian+proxy  — everything above + IP rotation
    """

    def __init__(self, mode: str = "gaussian+proxy"):
        self.mode    = mode
        self.stats   = BotStats()
        self.session = requests.Session()
        self._ip: str = ""
        self._ua: str = ""

    # ── identity ───────────────────────────────────────────────────────────────

    def _rotate_identity(self) -> None:
        if "proxy" in self.mode:
            self._ip = random.choice(_PROXY_POOL)
        if self.mode != "naive":
            self._ua = random.choice(_USER_AGENTS)

    def _headers(self) -> dict:
        if self.mode == "naive":
            return {}   # python-requests default UA, no extra headers

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

    # ── timing ─────────────────────────────────────────────────────────────────

    def _wait_between_posts(self) -> None:
        if self.mode == "naive":
            delay = 0.2
        else:
            delay = gaussian_delay(mu=2.5, sigma=0.9, lo=0.8)
        print(f"    [wait] {delay:.2f}s")
        time.sleep(delay)

    def _fill_time(self) -> float:
        if self.mode == "naive":
            return 0.05   # instant fill — very suspicious
        return gaussian_fill_time(mu=4.5, sigma=1.8, lo=1.5)

    # ── post ───────────────────────────────────────────────────────────────────

    def post(self, title: str, body: str) -> dict | None:
        self._rotate_identity()
        fill_t = self._fill_time()

        payload = {
            "title":  title,
            "body":   body,
            "_t":     round(fill_t, 2),
            "_email": "",     # honeypot — always empty for bot
        }

        ip_display = self._ip or "default"
        try:
            r = self.session.post(
                f"{BASE_URL}/submit",
                data=payload,
                headers=self._headers(),
                timeout=10,
            )
            data = r.json()
            self.stats.sent += 1

            if r.status_code == 200:
                self.stats.passed += 1
                print(f"  [PASS] {title[:45]:<45} ip={ip_display:<15} fill={fill_t:.1f}s")
            else:
                self.stats.flagged += 1
                self.stats.reasons.extend(data.get("reasons", []))
                print(f"  [FLAG] {title[:45]:<45} ip={ip_display:<15} => {data.get('reasons')}")

            return data

        except requests.exceptions.ConnectionError:
            print("  [ERR] Cannot connect to server. Is it running? (python sim/server/app.py)")
            return None
        except Exception as exc:
            print(f"  [ERR] {exc}")
            return None

    # ── run ────────────────────────────────────────────────────────────────────

    def run(self, n: int = 20) -> BotStats:
        pool   = _POST_BANK * ((n // len(_POST_BANK)) + 1)
        sample = random.sample(pool, n)

        print(f"\n{'='*60}")
        print(f"  Mode: {self.mode}  |  Posts: {n}")
        print(f"{'='*60}")

        for i, (title, body) in enumerate(sample):
            print(f"[{i+1:>2}/{n}] Submitting...")
            result = self.post(title, body)
            if result is None:
                break
            if i < n - 1:
                self._wait_between_posts()

        print(f"\n{'-'*60}")
        print(f"  Results: {self.stats.passed} passed / {self.stats.flagged} flagged / {self.stats.sent} sent")
        print(f"  Detection rate: {self.stats.detection_rate:.1f}%")
        if self.stats.reasons:
            from collections import Counter
            top = Counter(self.stats.reasons).most_common(5)
            print(f"  Top reasons flagged: {top}")
        print(f"{'-'*60}\n")

        return self.stats


# ── CLI ────────────────────────────────────────────────────────────────────────

def _compare(n: int) -> None:
    """Run all three modes and print a comparison table."""
    results = {}
    for mode in ("naive", "gaussian", "gaussian+proxy"):
        agent = BotAgent(mode=mode)
        stats = agent.run(n=n)
        results[mode] = stats

    print("\n" + "="*60)
    print("  COMPARISON")
    print("="*60)
    print(f"  {'Mode':<20} {'Sent':>6} {'Passed':>8} {'Flagged':>8} {'Det.Rate':>10}")
    print(f"  {'-'*55}")
    for mode, s in results.items():
        print(f"  {mode:<20} {s.sent:>6} {s.passed:>8} {s.flagged:>8} {s.detection_rate:>9.1f}%")
    print("="*60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reddit sim bot agent")
    parser.add_argument("--mode",  default="gaussian+proxy",
                        choices=["naive", "gaussian", "gaussian+proxy", "all"])
    parser.add_argument("--posts", type=int, default=20, help="Number of posts to submit")
    args = parser.parse_args()

    if args.mode == "all":
        _compare(n=args.posts)
    else:
        agent = BotAgent(mode=args.mode)
        agent.run(n=args.posts)
