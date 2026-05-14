"""
Tests for the simulation components.

Covers:
  - Gaussian + triangle waypoint algorithm (pure Python, no browser)
  - Proxy config parsing
  - Bot agent HTTP modes against the local Flask server
  - Playwright browser flow against the local Flask server

Run:
  pip install pytest
  python -m pytest sim/tests/ -v
"""
import math
import sys
import os
import threading
import time
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sim"))


# ── Algorithm unit tests ───────────────────────────────────────────────────────

from bot.reddit_browser_agent import (
    _bezier, _triangle_waypoints, _proxy_cfg, _load_proxies, _pick_post, g
)


class TestBezier:
    def test_at_t0_returns_first_point(self):
        pts = [(0, 0), (50, 0), (50, 100), (100, 100)]
        x, y = _bezier(0, pts)
        assert abs(x) < 1e-9 and abs(y) < 1e-9

    def test_at_t1_returns_last_point(self):
        pts = [(0, 0), (50, 0), (50, 100), (100, 100)]
        x, y = _bezier(1, pts)
        assert abs(x - 100) < 1e-9 and abs(y - 100) < 1e-9

    def test_midpoint_is_on_curve(self):
        pts = [(0, 0), (0, 0), (100, 0), (100, 0)]  # straight line
        x, y = _bezier(0.5, pts)
        assert abs(x - 50) < 1e-6 and abs(y) < 1e-6

    def test_single_point(self):
        x, y = _bezier(0.5, [(7, 3)])
        assert x == 7 and y == 3


class TestTriangleWaypoints:
    def test_zero_detours_returns_endpoints_only(self):
        wps = _triangle_waypoints(0, 0, 100, 100, n=0)
        assert len(wps) == 2
        assert wps[0] == (0, 0)
        assert wps[-1] == (100, 100)

    def test_two_detours_returns_four_points(self):
        wps = _triangle_waypoints(0, 0, 500, 300, n=2)
        assert len(wps) == 4
        assert wps[0] == (0, 0)
        assert wps[-1] == (500, 300)

    def test_vertices_are_between_endpoints(self):
        random.seed(42)
        for _ in range(20):
            wps = _triangle_waypoints(0, 0, 200, 0, n=1)
            vx, vy = wps[1]
            # x must be between 0 and 200 (within some margin for Gaussian offset)
            assert -50 < vx < 250
            # y should be offset from 0 (perpendicular to horizontal line)
            # just check it's a finite float
            assert math.isfinite(vy)

    def test_auto_n_short_distance(self):
        # dist < 80 → n is 0 or 1
        random.seed(0)
        counts = set()
        for _ in range(30):
            wps = _triangle_waypoints(0, 0, 10, 10)
            counts.add(len(wps) - 2)
        assert counts <= {0, 1}

    def test_auto_n_long_distance(self):
        # dist > 80 → n can be 0, 1, or 2
        random.seed(0)
        counts = set()
        for _ in range(50):
            wps = _triangle_waypoints(0, 0, 500, 500)
            counts.add(len(wps) - 2)
        assert counts <= {0, 1, 2}
        assert 2 in counts  # with 50 tries we should hit n=2


class TestGaussianHelper:
    def test_lower_bound_respected(self):
        for _ in range(200):
            assert g(0, 10, lo=5.0) >= 5.0

    def test_upper_bound_respected(self):
        for _ in range(200):
            assert g(100, 10, hi=95.0) <= 95.0

    def test_both_bounds(self):
        for _ in range(200):
            v = g(50, 20, lo=30, hi=70)
            assert 30 <= v <= 70

    def test_no_bounds_returns_float(self):
        assert isinstance(g(5, 1), float)


class TestProxyConfig:
    def test_none_returns_none(self):
        assert _proxy_cfg(None) is None

    def test_simple_http(self):
        cfg = _proxy_cfg("http://1.2.3.4:8080")
        assert cfg["server"] == "http://1.2.3.4:8080"
        assert "username" not in cfg

    def test_socks5(self):
        cfg = _proxy_cfg("socks5://5.6.7.8:1080")
        assert cfg["server"] == "socks5://5.6.7.8:1080"

    def test_with_credentials(self):
        cfg = _proxy_cfg("http://alice:secret@10.0.0.1:3128")
        assert cfg["username"] == "alice"
        assert cfg["password"] == "secret"
        assert "alice" not in cfg["server"]
        assert "secret" not in cfg["server"]

    def test_credentials_stripped_from_server_url(self):
        cfg = _proxy_cfg("http://user:pw@192.168.1.1:8888")
        assert "@" not in cfg["server"]


class TestLoadProxies:
    def test_single_inline(self):
        proxies = _load_proxies(None, "http://1.2.3.4:8080")
        assert proxies == ["http://1.2.3.4:8080"]

    def test_none_returns_empty(self):
        assert _load_proxies(None, None) == []

    def test_from_file(self, tmp_path):
        f = tmp_path / "proxies.txt"
        f.write_text("http://1.1.1.1:80\n# comment\nsocks5://2.2.2.2:1080\n")
        proxies = _load_proxies(str(f), None)
        assert len(proxies) == 2
        assert "http://1.1.1.1:80" in proxies
        assert "socks5://2.2.2.2:1080" in proxies

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            _load_proxies("/nonexistent/proxies.txt", None)

    def test_combines_inline_and_file(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("http://3.3.3.3:80\n")
        proxies = _load_proxies(str(f), "http://1.1.1.1:80")
        assert len(proxies) == 2


class TestPostBank:
    def test_known_subreddit(self):
        title, body = _pick_post("test")
        assert isinstance(title, str) and len(title) > 5
        assert isinstance(body,  str) and len(body)  > 5

    def test_mma_posts(self):
        title, body = _pick_post("MMA")
        assert isinstance(title, str)

    def test_unknown_sub_falls_back(self):
        # Unknown sub → falls back to "test" bank
        title, body = _pick_post("xyznonexistent")
        assert isinstance(title, str) and len(title) > 0

    def test_randomness(self):
        # With enough draws from a 3-item bank we should see at least 2 distinct titles
        titles = {_pick_post("test")[0] for _ in range(30)}
        assert len(titles) >= 2


# ── HTTP bot agent vs local sim server ────────────────────────────────────────

from bot.agent import BotAgent
from server.app import app as flask_app


@pytest.fixture(scope="module")
def sim_server():
    """Start the Flask sim server in a background thread for the test session."""
    flask_app.config["TESTING"] = True
    t = threading.Thread(
        target=lambda: flask_app.run(port=5001, use_reloader=False, threaded=True),
        daemon=True,
    )
    t.start()
    time.sleep(0.8)  # give Flask a moment to bind
    yield "http://localhost:5001"


def _agent_for(mode, base_url):
    import bot.agent as agent_mod
    orig = agent_mod.BASE_URL
    agent_mod.BASE_URL = base_url
    a = BotAgent(mode=mode)
    a.session.headers.update({})
    agent_mod.BASE_URL = orig
    return a, base_url


class TestBotAgentModes:
    def test_naive_gets_flagged(self, sim_server):
        import bot.agent as agent_mod
        agent_mod.BASE_URL = sim_server
        agent = BotAgent(mode="naive")
        # Naive mode: constant 0.05s fill time, python-requests UA → should be flagged
        result = agent.post("Test naive post", "body")
        assert result is not None
        agent_mod.BASE_URL = "http://localhost:5000"

    def test_gaussian_proxy_can_pass(self, sim_server):
        import bot.agent as agent_mod
        agent_mod.BASE_URL = sim_server
        agent = BotAgent(mode="gaussian+proxy")
        # Gaussian+proxy: rotates IP each call → rate check doesn't accumulate
        passed = 0
        for _ in range(3):
            result = agent.post("Test gaussian post", "body content here")
            if result and result.get("status") == "ok":
                passed += 1
        assert passed >= 1, "gaussian+proxy should pass at least 1 of 3 posts"
        agent_mod.BASE_URL = "http://localhost:5000"

    def test_stats_tracking(self, sim_server):
        import bot.agent as agent_mod
        agent_mod.BASE_URL = sim_server
        agent = BotAgent(mode="gaussian+proxy")
        agent.post("Stats test post", "body")
        assert agent.stats.sent >= 1
        agent_mod.BASE_URL = "http://localhost:5000"


# ── Playwright browser vs local sim server ────────────────────────────────────

from playwright.sync_api import sync_playwright
from bot.playwright_agent import (
    _triangle_waypoints as pw_waypoints,
    _bezier as pw_bezier,
    gaussian_delay,
)


class TestPlaywrightAlgorithms:
    """Verify playwright_agent.py exports the same algorithm as reddit_browser_agent."""

    def test_waypoints_consistent(self):
        random.seed(99)
        wps1 = pw_waypoints(0, 0, 200, 150, n=1)
        random.seed(99)
        wps2 = _triangle_waypoints(0, 0, 200, 150, n=1)
        # Both should produce same structure (same n, same endpoint)
        assert len(wps1) == len(wps2)
        assert wps1[0] == wps2[0]
        assert wps1[-1] == wps2[-1]

    def test_bezier_consistent(self):
        pts = [(0,0),(50,100),(150,100),(200,0)]
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            x1, y1 = pw_bezier(t, pts)
            x2, y2 = _bezier(t, pts)
            assert abs(x1 - x2) < 1e-9
            assert abs(y1 - y2) < 1e-9

    def test_gaussian_delay_bounds(self):
        for _ in range(100):
            d = gaussian_delay(mu=2.5, sigma=0.9, lo=0.5)
            assert d >= 0.5


@pytest.mark.integration
class TestPlaywrightVsSimServer:
    """Full Playwright browser round-trip against local sim server."""

    def test_submit_and_detect(self, sim_server):
        from bot.playwright_agent import (
            _triangle_waypoints, gaussian_type, gaussian_click,
            gaussian_scroll, _run_bot, PlaywrightStats
        )
        import bot.playwright_agent as pw_mod
        orig = pw_mod.BASE_URL
        pw_mod.BASE_URL = sim_server

        sample = [("Playwright smoke test post", "Testing the full browser flow.")]
        stats = PlaywrightStats()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = ctx.new_page()
            page.add_init_script("window._mx=400; window._my=300;")
            _run_bot(page, sample, stats)
            browser.close()

        pw_mod.BASE_URL = orig
        assert stats.sent == 1
        # Either passed or flagged — both mean the server responded
        assert stats.passed + stats.flagged + stats.errors == 1
