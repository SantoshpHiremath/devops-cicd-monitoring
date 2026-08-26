"""Tests for the monitoring watchdog. Mix of fast unit tests against
the evaluate() logic directly, and slower integration tests that run
the watchdog against a real running instance of the Flask app (via the
Werkzeug dev server in a background thread) to prove the whole poll ->
evaluate -> alert pipeline actually works end to end, not just that
the pieces are individually plausible."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "monitoring"))
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import watchdog as wd


# ---------------------------------------------------------------------
# Fast unit tests: evaluate() logic in isolation, no network involved.
# ---------------------------------------------------------------------

def _result(healthz_ok=True, readyz_ok=True, requests_total=None, errors_total=None, error=None):
    return wd.PollResult(
        timestamp=1000.0,
        healthz_ok=healthz_ok,
        readyz_ok=readyz_ok,
        requests_total=requests_total,
        errors_total=errors_total,
        error=error,
    )


def test_healthy_result_produces_no_alerts():
    state = wd.WatchdogState()
    alerts = wd.evaluate(_result(), state)
    assert alerts == []


def test_healthz_down_produces_critical_alert():
    state = wd.WatchdogState()
    alerts = wd.evaluate(_result(healthz_ok=False, error="connection refused"), state)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["check"] == "healthz"


def test_alive_but_not_ready_produces_warning():
    state = wd.WatchdogState()
    alerts = wd.evaluate(_result(healthz_ok=True, readyz_ok=False), state)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "warning"
    assert alerts[0]["check"] == "readyz"


def test_error_rate_spike_triggers_critical_alert():
    state = wd.WatchdogState()
    # First poll establishes baseline, no alert possible yet (no delta).
    wd.evaluate(_result(requests_total=100, errors_total=10), state)
    # Second poll: 20 new requests, 15 of them errors -> 75% error rate.
    alerts = wd.evaluate(_result(requests_total=120, errors_total=25), state, error_rate_threshold=0.5)
    assert any(a["check"] == "error_rate" and a["severity"] == "critical" for a in alerts)


def test_error_rate_below_threshold_does_not_alert():
    state = wd.WatchdogState()
    wd.evaluate(_result(requests_total=100, errors_total=10), state)
    # 20 new requests, 2 errors -> 10% error rate, below default 50% threshold.
    alerts = wd.evaluate(_result(requests_total=120, errors_total=12), state, error_rate_threshold=0.5)
    assert not any(a["check"] == "error_rate" for a in alerts)


def test_error_rate_check_skipped_below_min_requests():
    """Guards against noisy alerts on tiny sample sizes (e.g. 1 error
    out of 2 requests = 50% but is not a meaningful signal)."""
    state = wd.WatchdogState()
    wd.evaluate(_result(requests_total=100, errors_total=10), state)
    alerts = wd.evaluate(_result(requests_total=102, errors_total=12), state, error_rate_threshold=0.1)
    assert not any(a["check"] == "error_rate" for a in alerts)


def test_counter_reset_does_not_produce_bogus_negative_rate():
    """If the monitored process restarts, its counters reset to a
    lower value than the watchdog last saw. A naive delta would go
    negative and either crash or silently misreport — this guards
    against that."""
    state = wd.WatchdogState()
    wd.evaluate(_result(requests_total=500, errors_total=50), state)
    # Simulate a restart: counters dropped back down.
    alerts = wd.evaluate(_result(requests_total=5, errors_total=1), state, error_rate_threshold=0.5)
    assert not any(a["check"] == "error_rate" for a in alerts)


def test_run_watchdog_writes_alert_log_on_failure(tmp_path):
    log_path = tmp_path / "alerts.log"
    calls = {"poll": 0}

    def fake_poll_sequence():
        calls["poll"] += 1
        if calls["poll"] == 1:
            return _result(healthz_ok=False, error="simulated outage")
        return _result()

    original_poll = wd.poll
    wd.poll = lambda base_url, timeout=3.0: fake_poll_sequence()
    try:
        wd.run_watchdog("http://fake", interval_seconds=0, iterations=2,
                         alert_log_path=str(log_path), sleep_fn=lambda s: None)
    finally:
        wd.poll = original_poll

    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["check"] == "healthz"
    assert record["severity"] == "critical"


# ---------------------------------------------------------------------
# Integration test: run the REAL Flask app in a background thread and
# point the watchdog at it over real HTTP, to prove poll() correctly
# parses the app's real /healthz, /readyz, and /metrics output (not a
# mocked stand-in for them).
# ---------------------------------------------------------------------

@pytest.fixture
def live_app_server():
    import main as app_module

    # Reset state for test isolation.
    app_module.ARTICLES = {
        1: {"id": 1, "title": "Test article", "views": 0},
    }

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app_module.app)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_poll_against_real_running_app(live_app_server):
    result = wd.poll(live_app_server)
    assert result.healthz_ok is True
    assert result.readyz_ok is True
    assert result.requests_total is not None
    assert result.errors_total is not None
    assert result.error is None


def test_poll_detects_real_readyz_failure(live_app_server):
    import main as app_module
    app_module.ARTICLES.clear()  # makes /readyz return 503 for real

    result = wd.poll(live_app_server)
    assert result.healthz_ok is True
    assert result.readyz_ok is False

    state = wd.WatchdogState()
    alerts = wd.evaluate(result, state)
    assert any(a["check"] == "readyz" for a in alerts)


def test_poll_detects_real_unreachable_server():
    # Nothing listening on this port.
    result = wd.poll("http://127.0.0.1:1")
    assert result.healthz_ok is False
    assert result.error is not None
