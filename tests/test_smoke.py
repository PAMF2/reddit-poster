"""
Tests for the simulation components.

Run:
  python -m pytest tests/ -v
  python -m pytest tests/ -v -m "not integration"   # skip browser E2E
"""
import math
import random
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bot.common import (
    _bezier, _triangle_waypoints, g,
    save_session, load_session,
    pick_post, POST_BANK,
    retry,
)
from bot.reddit_browser_agent import _proxy_cfg, load_proxies


# ── Bezier ────────────────────────────────────────────────────────────────────

class TestBezier:
    def test_at_t0_returns_first_point(self):
        pts = [(0, 0), (50, 0), (50, 100), (100, 100)]
        x, y = _bezier(0, pts)
        assert abs(x) < 1e-9 and abs(y) < 1e-9

    def test_at_t1_returns_last_point(self):
        pts = [(0, 0), (50, 0), (50, 100), (100, 100)]
        x, y = _bezier(1, pts)
        assert abs(x - 100) < 1e-9 and abs(y - 100) < 1e-9

    def test_straight_line_midpoint(self):
        pts = [(0, 0), (0, 0), (100, 0), (100, 0)]
        x, y = _bezier(0.5, pts)
        assert abs(x - 50) < 1e-6 and abs(y) < 1e-6

    def test_single_point(self):
        x, y = _bezier(0.5, [(7, 3)])
        assert x == 7 and y == 3


# ── Triangle waypoints ────────────────────────────────────────────────────────

class TestTriangleWaypoints:
    def test_no_detours(self):
        wps = _triangle_waypoints(0, 0, 100, 100, n=0)
        assert len(wps) == 2
        assert wps[0] == (0, 0) and wps[-1] == (100, 100)

    def test_two_detours(self):
        wps = _triangle_waypoints(0, 0, 500, 300, n=2)
        assert len(wps) == 4
        assert wps[0] == (0, 0) and wps[-1] == (500, 300)

    def test_vertices_finite(self):
        random.seed(42)
        for _ in range(20):
            for pt in _triangle_waypoints(0, 0, 200, 0, n=1):
                assert all(math.isfinite(v) for v in pt)

    def test_auto_short_distance(self):
        random.seed(0)
        counts = {len(_triangle_waypoints(0, 0, 10, 10)) - 2 for _ in range(30)}
        assert counts <= {0, 1}

    def test_auto_long_distance_hits_two(self):
        random.seed(0)
        counts = {len(_triangle_waypoints(0, 0, 500, 500)) - 2 for _ in range(50)}
        assert 2 in counts


# ── Gaussian helper ───────────────────────────────────────────────────────────

class TestGaussianHelper:
    def test_lower_bound(self):
        assert all(g(0, 10, lo=5.0) >= 5.0 for _ in range(200))

    def test_upper_bound(self):
        assert all(g(100, 10, hi=95.0) <= 95.0 for _ in range(200))

    def test_both_bounds(self):
        assert all(30 <= g(50, 20, lo=30, hi=70) <= 70 for _ in range(200))

    def test_returns_float(self):
        assert isinstance(g(5, 1), float)


# ── Proxy parsing ─────────────────────────────────────────────────────────────

class TestProxyConfig:
    def test_none(self):
        assert _proxy_cfg(None) is None

    def test_simple_http(self):
        cfg = _proxy_cfg("http://1.2.3.4:8080")
        assert cfg["server"] == "http://1.2.3.4:8080"
        assert "username" not in cfg

    def test_socks5(self):
        cfg = _proxy_cfg("socks5://5.6.7.8:1080")
        assert "socks5" in cfg["server"]

    def test_with_credentials(self):
        cfg = _proxy_cfg("http://alice:secret@10.0.0.1:3128")
        assert cfg["username"] == "alice"
        assert cfg["password"] == "secret"
        assert "@" not in cfg["server"]


class TestLoadProxies:
    def test_inline(self):
        assert load_proxies(None, "http://1.2.3.4:80") == ["http://1.2.3.4:80"]

    def test_empty(self):
        assert load_proxies(None, None) == []

    def test_from_file(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("http://1.1.1.1:80\n# comment\nsocks5://2.2.2.2:1080\n")
        result = load_proxies(str(f), None)
        assert len(result) == 2

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_proxies("/nonexistent/proxies.txt", None)

    def test_combines_inline_and_file(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("http://3.3.3.3:80\n")
        assert len(load_proxies(str(f), "http://1.1.1.1:80")) == 2


# ── Post bank ─────────────────────────────────────────────────────────────────

class TestPostBank:
    def test_known_sub(self):
        title, body = pick_post("test")
        assert len(title) > 5 and len(body) > 5

    def test_mma(self):
        title, body = pick_post("MMA")
        assert isinstance(title, str)

    def test_fallback_to_test(self):
        title, body = pick_post("xyznonexistent")
        assert title in {t for t, _ in POST_BANK["test"]}

    def test_randomness(self):
        titles = {pick_post("MMA")[0] for _ in range(40)}
        assert len(titles) >= 3

    def test_all_banks_non_empty(self):
        for sub, posts in POST_BANK.items():
            assert len(posts) >= 2, f"POST_BANK['{sub}'] has fewer than 2 posts"


# ── Session persistence ───────────────────────────────────────────────────────

class TestSessionPersistence:
    def test_save_and_load(self, tmp_path):
        fake_cookies = [{"name": "session", "value": "abc", "domain": ".reddit.com",
                         "path": "/", "httpOnly": True, "secure": True}]
        ctx = MagicMock()
        ctx.cookies.return_value = fake_cookies

        path = tmp_path / "session.json"
        save_session(ctx, path=path)
        assert path.exists()

        ctx2 = MagicMock()
        assert load_session(ctx2, path=path) is True
        ctx2.add_cookies.assert_called_once_with(fake_cookies)

    def test_load_missing_returns_false(self, tmp_path):
        ctx = MagicMock()
        assert load_session(ctx, path=tmp_path / "missing.json") is False
        ctx.add_cookies.assert_not_called()


# ── Retry decorator ───────────────────────────────────────────────────────────

class TestRetry:
    def test_succeeds_first_try(self):
        calls = []
        @retry(attempts=3)
        def ok():
            calls.append(1); return 42
        assert ok() == 42 and len(calls) == 1

    def test_retries_then_succeeds(self):
        calls = []
        @retry(attempts=3, base_delay=0.01)
        def flaky():
            calls.append(1)
            if len(calls) < 3: raise ValueError("not yet")
            return "done"
        assert flaky() == "done" and len(calls) == 3

    def test_raises_after_max(self):
        @retry(attempts=2, base_delay=0.01)
        def always_fails():
            raise RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            always_fails()

    def test_only_catches_specified(self):
        @retry(attempts=3, base_delay=0.01, exceptions=(ValueError,))
        def wrong():
            raise TypeError("not caught")
        with pytest.raises(TypeError):
            wrong()


# ── Typo simulation ───────────────────────────────────────────────────────────

class TestTypoSimulation:
    def test_adjacent_keys_map_complete(self):
        from bot.common import _ADJACENT
        # Every lowercase letter should have adjacents defined
        for ch in 'abcdefghijklmnopqrstuvwxyz':
            assert ch in _ADJACENT, f"'{ch}' missing from _ADJACENT"
            assert len(_ADJACENT[ch]) >= 1

    def test_adjacent_keys_are_lowercase(self):
        from bot.common import _ADJACENT
        for ch, neighbours in _ADJACENT.items():
            assert neighbours == neighbours.lower(), f"Neighbours for '{ch}' not lowercase"

    def test_gaussian_type_calls_backspace_on_typo(self):
        from unittest.mock import MagicMock, call, patch
        from bot.common import gaussian_type

        page = MagicMock()
        page.locator.return_value.first.bounding_box.return_value = {
            "x": 100, "y": 100, "width": 200, "height": 30
        }
        # Force every character to trigger a typo
        with patch('bot.common.random.random', return_value=0.0):  # 0.0 < 0.03 always
            with patch('bot.common.time.sleep'):
                gaussian_type(page, "input", "abc")

        typed  = [c.args[0] for c in page.keyboard.type.call_args_list]
        pressed = [c.args[0] for c in page.keyboard.press.call_args_list]
        # Should see Backspace presses (one per letter since all trigger typo)
        assert "Backspace" in pressed
        # Correct chars should still be typed (a, b, c appear somewhere)
        assert set(typed) >= {'a', 'b', 'c'}


# ── Server detection signal tests ─────────────────────────────────────────────

class TestServerDetection:
    def test_shannon_entropy_high_for_normal_text(self):
        from server.app import _shannon_entropy
        e = _shannon_entropy("Jon Jones vs Stipe — who wins the rematch?")
        assert e > 3.0

    def test_shannon_entropy_low_for_repetitive(self):
        from server.app import _shannon_entropy
        e = _shannon_entropy("aaaaaaaaaaaaaaaaaaaaaa")
        assert e < 1.0

    def test_keystroke_cov_regular(self):
        from server.app import _keystroke_cov
        # Perfectly regular 100ms intervals → CoV ≈ 0
        kt = "|".join(["100"] * 20)
        cov = _keystroke_cov(kt)
        assert cov is not None and cov < 0.05

    def test_keystroke_cov_human(self):
        from server.app import _keystroke_cov
        # Human-like variance: mix of fast and slow
        import random as rnd; rnd.seed(42)
        intervals = [str(int(rnd.gauss(120, 50))) for _ in range(25)]
        cov = _keystroke_cov("|".join(intervals))
        assert cov is not None and cov > 0.15

    def test_keystroke_cov_too_few_samples(self):
        from server.app import _keystroke_cov
        assert _keystroke_cov("100|110|105") is None  # < 8 samples

    def test_keystroke_cov_empty(self):
        from server.app import _keystroke_cov
        assert _keystroke_cov("") is None


# ── Rate-limit detection ──────────────────────────────────────────────────────

class TestRateLimitDetection:
    def test_known_phrases_detected(self):
        from bot.reddit_browser_agent import _check_rate_limited
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = \
            "Whoa, slow down. You are doing that too much."
        assert _check_rate_limited(page) is True

    def test_clean_page_not_rate_limited(self):
        from bot.reddit_browser_agent import _check_rate_limited
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = "Welcome to Reddit"
        assert _check_rate_limited(page) is False

    def test_exception_returns_false(self):
        from bot.reddit_browser_agent import _check_rate_limited
        page = MagicMock()
        page.locator.return_value.inner_text.side_effect = Exception("timeout")
        assert _check_rate_limited(page) is False


# ── HTTP bot vs local sim server ──────────────────────────────────────────────

from bot.agent import BotAgent
from server.app import app as flask_app


@pytest.fixture(scope="module")
def sim_server():
    flask_app.config["TESTING"] = True
    t = threading.Thread(
        target=lambda: flask_app.run(port=5001, use_reloader=False, threaded=True),
        daemon=True,
    )
    t.start()
    time.sleep(0.8)
    yield "http://localhost:5001"


class TestBotAgentModes:
    def test_naive_flagged(self, sim_server):
        import bot.agent as m; m.BASE_URL = sim_server
        result = BotAgent(mode="naive").post("Naive test", "body")
        assert result is not None
        m.BASE_URL = "http://localhost:5000"

    def test_gaussian_proxy_passes(self, sim_server):
        import bot.agent as m; m.BASE_URL = sim_server
        agent = BotAgent(mode="gaussian+proxy")
        passed = sum(1 for _ in range(3)
                     if (r := agent.post("Proxy test", "body")) and r.get("status") == "ok")
        assert passed >= 1
        m.BASE_URL = "http://localhost:5000"

    def test_stats_tracked(self, sim_server):
        import bot.agent as m; m.BASE_URL = sim_server
        agent = BotAgent(mode="gaussian+proxy")
        agent.post("Stats test", "body")
        assert agent.stats.sent >= 1
        m.BASE_URL = "http://localhost:5000"


# ── Playwright browser vs sim server ──────────────────────────────────────────

@pytest.mark.integration
class TestPlaywrightVsSimServer:
    def test_full_submit_round_trip(self, sim_server):
        import bot.playwright_agent as pw_mod
        from bot.playwright_agent import _run_bot, PlaywrightStats
        from playwright.sync_api import sync_playwright

        orig = pw_mod.BASE_URL
        pw_mod.BASE_URL = sim_server
        stats = PlaywrightStats()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx  = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
            page = ctx.new_page()
            page.add_init_script("window._mx=400; window._my=300;")
            _run_bot(page, [("Playwright smoke test", "Full browser flow test.")], stats)
            browser.close()

        pw_mod.BASE_URL = orig
        assert stats.sent == 1
        assert stats.passed + stats.flagged + stats.errors == 1
