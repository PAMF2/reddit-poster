"""
Simulated Reddit server with bot detection.
Tracks timing, IP patterns, user agents, and honeypot fields.

Run:  python sim/server/app.py
Admin dashboard: http://localhost:5000/admin
"""
import time
import uuid
from collections import defaultdict

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.secret_key = "sim-demo-key"

# ── in-memory state ────────────────────────────────────────────────────────────
posts: list[dict]       = []
detection_log: list[dict] = []
ip_stats: dict          = defaultdict(lambda: {
    "requests": [],
    "posts":    0,
    "flagged":  0,
    "reasons_seen": [],
})


# ── detection engine ───────────────────────────────────────────────────────────

_BOT_UA_PATTERNS = [
    "python-requests", "python-urllib", "curl", "wget",
    "scrapy", "httpx", "aiohttp", "java/", "go-http",
]

def _detect(ip: str, ua: str, form_time: float, honeypot: str) -> list[str]:
    reasons: list[str] = []
    now  = time.time()
    stat = ip_stats[ip]

    stat["requests"].append(now)
    # keep only last 60 s
    stat["requests"] = [t for t in stat["requests"] if now - t < 60]

    # 1. request rate
    if len(stat["requests"]) > 10:
        reasons.append(f"rate:{len(stat['requests'])}/min")

    # 2. timing regularity (std dev of inter-request intervals)
    if len(stat["requests"]) >= 4:
        intervals = [
            stat["requests"][i+1] - stat["requests"][i]
            for i in range(len(stat["requests"]) - 1)
        ]
        variance = sum((x - sum(intervals)/len(intervals))**2 for x in intervals) / len(intervals)
        if variance < 0.05:
            reasons.append(f"regular_timing:var={variance:.3f}")

    # 3. bot user-agent
    ua_lower = ua.lower()
    for pat in _BOT_UA_PATTERNS:
        if pat in ua_lower:
            reasons.append(f"bot_ua:{pat}")
            break

    # 4. missing browser headers
    if not request.headers.get("Accept-Language"):
        reasons.append("no_accept_language")
    if not request.headers.get("Accept"):
        reasons.append("no_accept")

    # 5. form fill time
    if form_time < 1.5:
        reasons.append(f"fast_fill:{form_time:.2f}s")

    # 6. honeypot
    if honeypot:
        reasons.append("honeypot")

    return reasons


# ── routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", posts=list(reversed(posts[-30:])))


@app.route("/submit", methods=["GET", "POST"])
def submit():
    if request.method == "POST":
        ip        = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
        ua        = request.headers.get("User-Agent", "")
        form_time = float(request.form.get("_t", 0))
        honeypot  = request.form.get("_email", "")

        reasons = _detect(ip, ua, form_time, honeypot)
        flagged = bool(reasons)

        stat = ip_stats[ip]
        stat["posts"] += 1
        if flagged:
            stat["flagged"] += 1
            for r in reasons:
                if r not in stat["reasons_seen"]:
                    stat["reasons_seen"].append(r)

        entry = {
            "id":      str(uuid.uuid4())[:8],
            "title":   request.form.get("title", "")[:200],
            "body":    request.form.get("body",  "")[:500],
            "ip":      ip,
            "ua":      ua[:120],
            "fill_s":  round(form_time, 2),
            "ts":      time.strftime("%H:%M:%S"),
            "flagged": flagged,
            "reasons": reasons,
        }
        posts.append(entry)
        detection_log.append({**entry, "epoch": time.time()})

        if flagged:
            return jsonify({"status": "flagged", "reasons": reasons}), 403
        return jsonify({"status": "ok", "post_id": entry["id"]})

    return render_template("submit.html")


@app.route("/admin")
def admin():
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
    return render_template(
        "admin.html",
        posts=list(reversed(posts[-50:])),
        per_ip=per_ip,
        total=total,
        flagged=flagged,
        passed=total - flagged,
        rate=round(100 * flagged / max(total, 1), 1),
    )


@app.route("/api/stats")
def api_stats():
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
    app.run(debug=True, port=5000)
