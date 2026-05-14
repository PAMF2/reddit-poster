"""
Simulated Reddit server with multi-signal bot detection.

Detection signals:
  1. Request rate          — > 10 req/min per IP
  2. Timing regularity     — inter-request interval variance too low
  3. Bot user-agent        — python-requests, curl, etc.
  4. Missing browser headers — Accept-Language, Accept
  5. Fast form fill        — < 1.5 s
  6. Honeypot filled       — hidden _email field
  7. Keystroke regularity  — CoV of _kt timing array < 0.15
  8. Headless browser tells — missing Sec-Fetch-* / Sec-CH-UA with Chrome UA
  9. Low title entropy     — Shannon entropy < 2.2 bits/char (repetitive text)

Run:  python server/app.py
Admin dashboard: http://localhost:5000/admin
"""
import math
import statistics
import threading
import time
import uuid
from collections import defaultdict

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.secret_key = "sim-demo-key"

# ── In-memory state (protected by a lock for thread safety) ───────────────────

_lock = threading.Lock()

posts: list[dict]         = []
detection_log: list[dict] = []
ip_stats: dict            = defaultdict(lambda: {
    "requests": [],
    "posts":    0,
    "flagged":  0,
    "reasons_seen": [],
})


# ── Detection helpers ─────────────────────────────────────────────────────────

_BOT_UA_PATTERNS = [
    "python-requests", "python-urllib", "curl", "wget",
    "scrapy", "httpx", "aiohttp", "java/", "go-http",
]

# Chrome sends these on every real navigation; headless Playwright often omits them
_EXPECTED_CHROME_HEADERS = ["Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest"]


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    from collections import Counter
    freq = Counter(text.lower())
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in freq.values())


def _keystroke_cov(kt_field: str) -> float | None:
    """Coefficient of variation of keystroke intervals. None if too few samples."""
    try:
        intervals = [float(x) for x in kt_field.split("|") if x.strip()]
        if len(intervals) < 8:
            return None
        mean = statistics.mean(intervals)
        if mean == 0:
            return None
        return statistics.stdev(intervals) / mean
    except Exception:
        return None


def _detect(ip: str, ua: str, form_time: float, honeypot: str, kt: str, title: str) -> list[str]:
    reasons: list[str] = []
    now = time.time()

    with _lock:
        stat = ip_stats[ip]
        stat["requests"].append(now)
        stat["requests"] = [t for t in stat["requests"] if now - t < 60]
        req_count = len(stat["requests"])
        intervals = [
            stat["requests"][i+1] - stat["requests"][i]
            for i in range(req_count - 1)
        ] if req_count >= 2 else []

    # 1. Request rate
    if req_count > 10:
        reasons.append(f"rate:{req_count}/min")

    # 2. Timing regularity
    if len(intervals) >= 3:
        variance = statistics.variance(intervals) if len(intervals) >= 2 else 0
        if variance < 0.05:
            reasons.append(f"regular_timing:var={variance:.3f}")

    # 3. Bot user-agent
    ua_lower = ua.lower()
    for pat in _BOT_UA_PATTERNS:
        if pat in ua_lower:
            reasons.append(f"bot_ua:{pat}")
            break

    # 4. Missing browser headers
    if not request.headers.get("Accept-Language"):
        reasons.append("no_accept_language")
    if not request.headers.get("Accept"):
        reasons.append("no_accept")

    # 5. Fast fill
    if form_time < 1.5:
        reasons.append(f"fast_fill:{form_time:.2f}s")

    # 6. Honeypot
    if honeypot:
        reasons.append("honeypot")

    # 7. Keystroke timing regularity
    cov = _keystroke_cov(kt)
    if cov is not None and cov < 0.15:
        reasons.append(f"regular_keystrokes:cov={cov:.2f}")

    # 8. Headless browser tells — Chrome UA but missing Sec-Fetch-* headers
    if "chrome" in ua_lower and not ua_lower.startswith("python"):
        missing = [h for h in _EXPECTED_CHROME_HEADERS if not request.headers.get(h)]
        if len(missing) >= 2:
            reasons.append(f"headless_tells:{','.join(missing)}")

    # 9. Low title entropy (repetitive / auto-generated text)
    if len(title) >= 10:
        entropy = _shannon_entropy(title)
        if entropy < 2.2:
            reasons.append(f"low_entropy:{entropy:.2f}")

    return reasons


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    with _lock:
        recent = list(reversed(posts[-30:]))
    return render_template("index.html", posts=recent)


@app.route("/submit", methods=["GET", "POST"])
def submit():
    if request.method == "POST":
        ip        = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
        ua        = request.headers.get("User-Agent", "")
        form_time = float(request.form.get("_t", 0) or 0)
        honeypot  = request.form.get("_email", "")
        kt        = request.form.get("_kt", "")
        title     = request.form.get("title", "")[:200]

        reasons = _detect(ip, ua, form_time, honeypot, kt, title)
        flagged = bool(reasons)

        entry = {
            "id":      str(uuid.uuid4())[:8],
            "title":   title,
            "body":    request.form.get("body", "")[:500],
            "ip":      ip,
            "ua":      ua[:120],
            "fill_s":  round(form_time, 2),
            "ts":      time.strftime("%H:%M:%S"),
            "flagged": flagged,
            "reasons": reasons,
        }

        with _lock:
            posts.append(entry)
            detection_log.append({**entry, "epoch": time.time()})
            stat = ip_stats[ip]
            stat["posts"] += 1
            if flagged:
                stat["flagged"] += 1
                for r in reasons:
                    if r not in stat["reasons_seen"]:
                        stat["reasons_seen"].append(r)

        if flagged:
            return jsonify({"status": "flagged", "reasons": reasons}), 403
        return jsonify({"status": "ok", "post_id": entry["id"]})

    return render_template("submit.html")


@app.route("/admin")
def admin():
    with _lock:
        total   = len(posts)
        flagged = sum(1 for p in posts if p["flagged"])
        per_ip  = [
            {
                "ip":      ip,
                "reqs":    len(stat["requests"]),
                "posts":   stat["posts"],
                "flagged": stat["flagged"],
                "reasons": stat["reasons_seen"],
            }
            for ip, stat in ip_stats.items()
        ]
        recent = list(reversed(posts[-50:]))

    return render_template(
        "admin.html",
        posts=recent,
        per_ip=per_ip,
        total=total,
        flagged=flagged,
        passed=total - flagged,
        rate=round(100 * flagged / max(total, 1), 1),
    )


@app.route("/api/stats")
def api_stats():
    with _lock:
        total   = len(posts)
        flagged = sum(1 for p in posts if p["flagged"])
    return jsonify({
        "total":          total,
        "flagged":        flagged,
        "passed":         total - flagged,
        "detection_rate": round(100 * flagged / max(total, 1), 1),
        "unique_ips":     len(ip_stats),
    })


if __name__ == "__main__":
    print("Simulated Reddit running at http://localhost:5000")
    print("Admin dashboard:          http://localhost:5000/admin")
    app.run(debug=False, port=5000, threaded=True)
