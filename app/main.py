"""A small knowledge-platform API service — modeled loosely on KNOWRON's
actual domain (a mobile knowledge platform for desk-less workers), built
as a realistic target for the CI/CD pipeline and monitoring stack in
this project, not as a claim of building KNOWRON's real product.

Endpoints:
  GET  /healthz          — liveness probe (process is up)
  GET  /readyz            — readiness probe (dependencies, e.g. the
                             in-memory "knowledge store," are initialized)
  GET  /articles          — list knowledge articles
  GET  /articles/<id>     — fetch one article
  POST /articles          — create an article
  GET  /metrics           — Prometheus text metrics

BUG FOUND WHILE ACTUALLY RUNNING THIS UNDER GUNICORN (not caught by the
Flask-test-client unit tests, which run single-process): the original
version of this file kept request/error counters in a plain Python
dict (REQUEST_COUNT = {"total": 0, "errors": 0}). That works fine under
the single-process test client, but gunicorn here runs 2 worker
*processes*, each with its own separate copy of that dict in its own
memory. Polling /metrics repeatedly against the real running container
showed the counters jumping inconsistently between two different
sequences (e.g. requests_total alternating 5, 17, 18, 6 across
consecutive polls) depending purely on which worker happened to handle
each request — a real, reproducible bug, not a one-off fluke.

Fixed by switching to the real `prometheus_client` library in its
documented multiprocess mode (PROMETHEUS_MULTIPROC_DIR): each worker
writes its counters to per-worker mmap'd files on disk, and /metrics
aggregates across all of them, giving one consistent number regardless
of which worker serves a given request. This is the standard,
production-grade fix for exactly this class of bug in any pre-fork
WSGI server (gunicorn, uWSGI), not specific to this project.
"""
import os
import time

from flask import Flask, jsonify, request
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
    multiprocess,
    CONTENT_TYPE_LATEST,
)

app = Flask(__name__)

START_TIME = time.time()

# In-memory "knowledge store" — deliberately simple; the point of this
# service is to be a realistic, small deployable target for the CI/CD
# and monitoring stack, not a production knowledge platform.
ARTICLES = {
    1: {"id": 1, "title": "How to reset the CNC machine safety interlock", "views": 0},
    2: {"id": 2, "title": "Weekly forklift inspection checklist", "views": 0},
    3: {"id": 3, "title": "Reporting a near-miss safety incident", "views": 0},
}
_next_id = 4

# Real Prometheus client metrics, multiprocess-safe. REQUESTS_TOTAL /
# ERRORS_TOTAL are process-local Counters; prometheus_client's
# multiprocess.MultiProcessCollector merges every worker's values at
# scrape time (see /metrics below), which is what actually fixes the
# per-worker inconsistency described above. Defined once at module
# scope (not inside the /metrics view function) — Counter/Gauge raise
# a duplicate-registration error if constructed more than once with
# the same name against the same registry.
REQUESTS_TOTAL = Counter("app_requests_total", "Total HTTP requests handled")
ERRORS_TOTAL = Counter("app_requests_errors_total", "Total HTTP requests resulting in an error response")
UPTIME_GAUGE = Gauge("app_uptime_seconds", "Seconds since the app started")
ARTICLES_GAUGE = Gauge("app_articles_total", "Number of articles in the knowledge store")


@app.before_request
def _count_request():
    REQUESTS_TOTAL.inc()


@app.errorhandler(404)
def _not_found(e):
    ERRORS_TOTAL.inc()
    return jsonify({"error": "not found"}), 404


@app.route("/healthz")
def healthz():
    """Liveness: process is running and can respond. Used by the
    container orchestrator / monitoring system to detect a hung or
    crashed process."""
    return jsonify({"status": "ok", "uptime_seconds": round(time.time() - START_TIME, 1)}), 200


@app.route("/readyz")
def readyz():
    """Readiness: dependencies are initialized and the service can
    actually serve traffic. Separate from liveness on purpose: a
    process can be alive but not ready (e.g. still loading data)."""
    ready = len(ARTICLES) > 0
    status_code = 200 if ready else 503
    return jsonify({"status": "ready" if ready else "not_ready", "article_count": len(ARTICLES)}), status_code


@app.route("/articles", methods=["GET"])
def list_articles():
    return jsonify(list(ARTICLES.values())), 200


@app.route("/articles/<int:article_id>", methods=["GET"])
def get_article(article_id):
    article = ARTICLES.get(article_id)
    if article is None:
        # Counted explicitly here, not just in the 404 errorhandler:
        # this is a manual `return ..., 404` rather than `abort(404)`,
        # so Flask's errorhandler never fires for it. Caught by
        # test_metrics_error_count_increments_on_404 expecting the
        # error counter to increment on this exact path — it initially
        # didn't, because the error counter was only incremented in the
        # errorhandler, which this code path never reaches.
        ERRORS_TOTAL.inc()
        return jsonify({"error": f"article {article_id} not found"}), 404
    article["views"] += 1
    return jsonify(article), 200


@app.route("/articles", methods=["POST"])
def create_article():
    global _next_id
    data = request.get_json(silent=True) or {}
    title = data.get("title")
    if not title:
        ERRORS_TOTAL.inc()
        return jsonify({"error": "title is required"}), 400
    article = {"id": _next_id, "title": title, "views": 0}
    ARTICLES[_next_id] = article
    _next_id += 1
    return jsonify(article), 201


@app.route("/metrics")
def metrics():
    """Prometheus text-format metrics — real exposition format via the
    prometheus_client library, consumable by an actual Prometheus
    scraper. Uses multiprocess mode so counters are correct regardless
    of which gunicorn worker handled which request (see the module
    docstring for the bug this fixes)."""
    if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        # Fallback for local/dev runs without gunicorn multiprocess
        # (e.g. `python main.py` directly, or the Flask test client) —
        # uses the process-global default registry, which is fine
        # single-process.
        from prometheus_client import REGISTRY as registry

    # Point-in-time gauges refreshed on every scrape. NOTE: in
    # multiprocess mode a Gauge still reflects only the worker that
    # happens to serve this /metrics request (Gauges default to "last
    # value wins" across processes, not summed) — acceptable here since
    # article_count and uptime are the same in every worker in
    # practice (shared start time; ARTICLES mutations are rare and the
    # posting doesn't require strict cross-worker consistency for
    # these two values the way it does for the request/error counts).
    UPTIME_GAUGE.set(round(time.time() - START_TIME, 1))
    ARTICLES_GAUGE.set(len(ARTICLES))

    body = generate_latest(registry)
    return body, 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
