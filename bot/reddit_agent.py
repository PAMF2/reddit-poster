"""
Real Reddit poster using PRAW (official Reddit API + OAuth).

Setup (one-time):
  1. Go to https://www.reddit.com/prefs/apps
  2. Click "create another app..." at the bottom
  3. Name: anything (e.g. "research-bot")
     Type: "script"
     redirect uri: http://localhost:8080
  4. Copy the client_id (under app name) and client_secret
  5. Create sim/bot/.env with:
       REDDIT_CLIENT_ID=...
       REDDIT_CLIENT_SECRET=...
       REDDIT_USERNAME=your_reddit_username
       REDDIT_PASSWORD=your_reddit_password

Usage:
  python sim/bot/reddit_agent.py --subreddit test --posts 3 --dry-run
  python sim/bot/reddit_agent.py --subreddit test --posts 1
  python sim/bot/reddit_agent.py --subreddit MMA --posts 1 --dry-run
"""
import argparse
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import praw
from prawcore.exceptions import PrawcoreException

# ── load .env if present ──────────────────────────────────────────────────────

def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── Gaussian utilities ────────────────────────────────────────────────────────

def _gauss(mu, sigma, lo=None, hi=None):
    v = random.gauss(mu, sigma)
    if lo is not None: v = max(lo, v)
    if hi is not None: v = min(hi, v)
    return v


def _human_pause(mu=3.0, sigma=1.2, lo=1.0):
    delay = _gauss(mu, sigma, lo=lo)
    print(f"    [wait] {delay:.1f}s")
    time.sleep(delay)


# ── Post bank (safe for r/test and MMA discussion subs) ──────────────────────

_POSTS = {
    "test": [
        ("Testing automated post — ignore", "This is a test post from a research script."),
        ("API test post", "Testing PRAW OAuth flow. Please disregard."),
        ("Research test submission", "Academic bot research — test submission."),
    ],
    "MMA": [
        ("Jon Jones vs Stipe — who wins the rematch?",
         "Jones by decision, Stipe's chin can't survive 5 rounds of Jon's pace."),
        ("Best KO finish of the decade?",
         "Velasquez vs Brock still hits different. Pure fury and speed."),
        ("Is wrestling still the most important base in MMA?",
         "With how BJJ has evolved I'd argue striking matters more now. What do you think?"),
        ("Izzy's striking variety is insane",
         "Seven different striking styles in one fighter. Genuinely unprecedented in MMA history."),
        ("Poirier's legacy after retirement",
         "Top 5 LW all time imo. That body shot against McGregor was pure art."),
    ],
}

def _pick_post(subreddit: str) -> tuple[str, str]:
    bank = _POSTS.get(subreddit, _POSTS["test"])
    return random.choice(bank)


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    sent:    int = 0
    success: int = 0
    failed:  int = 0
    errors:  list = field(default_factory=list)


# ── Agent ─────────────────────────────────────────────────────────────────────

class RedditAgent:
    """
    Posts to real Reddit via PRAW (official OAuth API).

    Timing is Gaussian to avoid looking like a tight loop even though
    the API has its own rate limit (30 req/min). Being generous here
    so posts look organic in the activity log.
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.stats = Stats()
        self.reddit = self._auth()

    def _auth(self) -> praw.Reddit:
        client_id     = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        username      = os.environ.get("REDDIT_USERNAME")
        password      = os.environ.get("REDDIT_PASSWORD")

        missing = [k for k, v in {
            "REDDIT_CLIENT_ID": client_id,
            "REDDIT_CLIENT_SECRET": client_secret,
            "REDDIT_USERNAME": username,
            "REDDIT_PASSWORD": password,
        }.items() if not v]

        if missing:
            raise EnvironmentError(
                f"Missing env vars: {missing}\n"
                "Create sim/bot/.env — see module docstring for setup steps."
            )

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username,
            password=password,
            user_agent=f"reddit-research-bot/1.0 (by u/{username})",
        )

        me = reddit.user.me()
        print(f"[AUTH] Logged in as u/{me.name}  karma={me.link_karma}+{me.comment_karma}")
        return reddit

    def post(self, subreddit: str, title: str, body: str) -> bool:
        if self.dry_run:
            print(f"  [DRY-RUN] Would post to r/{subreddit}:")
            print(f"    title: {title}")
            print(f"    body:  {body[:60]}...")
            self.stats.sent += 1
            self.stats.success += 1
            return True

        try:
            sub = self.reddit.subreddit(subreddit)
            submission = sub.submit(title=title, selftext=body)
            self.stats.sent += 1
            self.stats.success += 1
            print(f"  [OK] https://reddit.com{submission.permalink}")
            return True
        except PrawcoreException as exc:
            self.stats.sent += 1
            self.stats.failed += 1
            self.stats.errors.append(str(exc))
            print(f"  [ERR] {exc}")
            return False

    def run(self, subreddit: str, n: int = 3):
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        print(f"\n{'='*60}")
        print(f"  RedditAgent | r/{subreddit} | posts={n} | {mode}")
        print(f"  Gaussian timing, official PRAW OAuth (30 req/min)")
        print(f"{'='*60}\n")

        for i in range(n):
            title, body = _pick_post(subreddit)
            print(f"[{i+1:>2}/{n}] Posting: {title[:50]}")
            self.post(subreddit, title, body)

            if i < n - 1:
                _human_pause(mu=_gauss(15.0, 5.0, lo=8.0), sigma=3.0, lo=5.0)

        print(f"\n{'-'*60}")
        print(f"  Results: {self.stats.success} success / {self.stats.failed} failed / {self.stats.sent} sent")
        if self.stats.errors:
            print(f"  Errors: {self.stats.errors}")
        print(f"{'-'*60}\n")
        return self.stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real Reddit poster (PRAW OAuth)")
    parser.add_argument("--subreddit", default="test",
                        help="Target subreddit (default: test — safe sandbox)")
    parser.add_argument("--posts", type=int, default=1,
                        help="Number of posts to submit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be posted without actually posting")
    args = parser.parse_args()

    agent = RedditAgent(dry_run=args.dry_run)
    agent.run(subreddit=args.subreddit, n=args.posts)
