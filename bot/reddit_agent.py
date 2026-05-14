"""
Real Reddit poster using PRAW (official Reddit API + OAuth).

Setup (one-time):
  1. Go to https://www.reddit.com/prefs/apps
  2. Click "create another app..." at the bottom
  3. Name: anything (e.g. "research-bot")
     Type: "script"
     redirect uri: http://localhost:8080
  4. Copy the client_id (under app name) and client_secret
  5. Create bot/.env with:
       REDDIT_CLIENT_ID=...
       REDDIT_CLIENT_SECRET=...
       REDDIT_USERNAME=your_reddit_username
       REDDIT_PASSWORD=your_reddit_password

Usage:
  python bot/reddit_agent.py --subreddit test --posts 3 --dry-run
  python bot/reddit_agent.py --subreddit MMA  --posts 1 --dry-run
  python bot/reddit_agent.py --subreddit test --posts 1
"""
import argparse
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import praw
from prawcore.exceptions import PrawcoreException

from bot.common import g, get_logger, pick_post

log = get_logger(__name__)


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
        self.stats   = Stats()
        self.reddit  = self._auth()

    def _auth(self) -> praw.Reddit:
        client_id     = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        username      = os.environ.get("REDDIT_USERNAME")
        password      = os.environ.get("REDDIT_PASSWORD")

        missing = [k for k, v in {
            "REDDIT_CLIENT_ID":     client_id,
            "REDDIT_CLIENT_SECRET": client_secret,
            "REDDIT_USERNAME":      username,
            "REDDIT_PASSWORD":      password,
        }.items() if not v]

        if missing:
            raise EnvironmentError(
                f"Missing env vars: {missing}\n"
                "Create bot/.env — see module docstring for setup steps."
            )

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username,
            password=password,
            user_agent=f"reddit-research-bot/1.0 (by u/{username})",
        )
        me = reddit.user.me()
        log.info("Logged in as u/%s  karma=%d+%d", me.name, me.link_karma, me.comment_karma)
        return reddit

    def post(self, subreddit: str, title: str, body: str) -> bool:
        if self.dry_run:
            log.info("[DRY-RUN] r/%s | %s", subreddit, title)
            self.stats.sent    += 1
            self.stats.success += 1
            return True

        try:
            sub        = self.reddit.subreddit(subreddit)
            submission = sub.submit(title=title, selftext=body)
            self.stats.sent    += 1
            self.stats.success += 1
            log.info("[OK] https://reddit.com%s", submission.permalink)
            return True
        except PrawcoreException as exc:
            self.stats.sent   += 1
            self.stats.failed += 1
            self.stats.errors.append(str(exc))
            log.error("[ERR] %s", exc)
            return False

    def run(self, subreddit: str, n: int = 3) -> Stats:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        log.info("RedditAgent | r/%s | posts=%d | %s | Gaussian timing, PRAW OAuth",
                 subreddit, n, mode)

        for i in range(n):
            title, body = pick_post(subreddit)
            log.info("[%d/%d] %s", i + 1, n, title[:60])
            self.post(subreddit, title, body)

            if i < n - 1:
                wait = g(15.0, 5.0, lo=8.0)
                log.info("Waiting %.1fs before next post", wait)
                time.sleep(wait)

        log.info("Done — %d success / %d failed / %d sent",
                 self.stats.success, self.stats.failed, self.stats.sent)
        if self.stats.errors:
            log.warning("Errors: %s", self.stats.errors)
        return self.stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real Reddit poster (PRAW OAuth)")
    parser.add_argument("--subreddit", default="test",
                        help="Target subreddit (default: test — safe sandbox)")
    parser.add_argument("--posts",     type=int, default=1,
                        help="Number of posts to submit")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Print what would be posted without actually posting")
    args = parser.parse_args()

    agent = RedditAgent(dry_run=args.dry_run)
    agent.run(subreddit=args.subreddit, n=args.posts)
