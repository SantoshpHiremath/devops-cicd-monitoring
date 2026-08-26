"""Tests for the knowledge-platform API service. Runs against the real
Flask app via its test client — no mocking of the app itself."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import main as app_module


def _counter_value(counter):
    """Read a prometheus_client Counter's current value. Counters are
    intentionally monotonic (no public reset API — that's correct
    Prometheus semantics), so tests assert on the delta across a
    request rather than resetting to zero between tests."""
    return counter._value.get()


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    # reset mutable module state between tests so tests don't leak into
    # each other (a real bug class in Flask app testing if skipped)
    app_module.ARTICLES = {
        1: {"id": 1, "title": "How to reset the CNC machine safety interlock", "views": 0},
        2: {"id": 2, "title": "Weekly forklift inspection checklist", "views": 0},
        3: {"id": 3, "title": "Reporting a near-miss safety incident", "views": 0},
    }
    app_module._next_id = 4
    with app_module.app.test_client() as c:
        yield c


def test_healthz_returns_200(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_healthz_reports_uptime(client):
    resp = client.get("/healthz")
    assert resp.get_json()["uptime_seconds"] >= 0


def test_readyz_returns_200_when_articles_present(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


def test_readyz_returns_503_when_no_articles(client):
    app_module.ARTICLES.clear()
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "not_ready"


def test_list_articles_returns_all(client):
    resp = client.get("/articles")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 3


def test_get_article_by_id(client):
    resp = client.get("/articles/1")
    assert resp.status_code == 200
    assert resp.get_json()["title"] == "How to reset the CNC machine safety interlock"


def test_get_article_increments_view_count(client):
    client.get("/articles/1")
    client.get("/articles/1")
    resp = client.get("/articles/1")
    assert resp.get_json()["views"] == 3


def test_get_nonexistent_article_returns_404(client):
    resp = client.get("/articles/999")
    assert resp.status_code == 404


def test_create_article_success(client):
    resp = client.post("/articles", json={"title": "New safety procedure"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["title"] == "New safety procedure"
    assert data["id"] == 4


def test_create_article_missing_title_returns_400(client):
    resp = client.post("/articles", json={})
    assert resp.status_code == 400


def test_created_article_is_retrievable(client):
    create_resp = client.post("/articles", json={"title": "Test article"})
    new_id = create_resp.get_json()["id"]
    get_resp = client.get(f"/articles/{new_id}")
    assert get_resp.status_code == 200
    assert get_resp.get_json()["title"] == "Test article"


def test_metrics_endpoint_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "app_uptime_seconds" in body
    assert "app_articles_total 3" in body
    assert resp.content_type.startswith("text/plain")


def test_metrics_request_count_increments(client):
    before = _counter_value(app_module.REQUESTS_TOTAL)
    client.get("/healthz")
    client.get("/healthz")
    resp = client.get("/metrics")
    body = resp.get_data(as_text=True)
    # 2 healthz calls + this /metrics call itself counted by before_request
    assert f"app_requests_total {before + 3.0}" in body


def test_metrics_error_count_increments_on_404(client):
    before = _counter_value(app_module.ERRORS_TOTAL)
    client.get("/articles/999")  # 404
    resp = client.get("/metrics")
    body = resp.get_data(as_text=True)
    assert f"app_requests_errors_total {before + 1.0}" in body
