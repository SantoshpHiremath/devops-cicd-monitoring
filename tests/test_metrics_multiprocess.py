"""Regression test for a real bug found by running this app under
gunicorn with multiple workers and polling /metrics repeatedly against
the actual running container (docker run, not the Flask test client).

The original app kept REQUEST_COUNT as a plain Python dict. Gunicorn's
default "sync" worker class pre-forks N separate OS processes, each
with its own private memory — so each worker had its own independent
copy of that dict. Polling /metrics against the real container showed
requests_total jumping inconsistently between two different sequences
depending purely on which of the 2 workers happened to answer a given
request (observed directly: consecutive polls returned 5, 17, 18, 6).

This test reproduces that exact scenario for real: it starts the actual
app.py under a real gunicorn process with --workers 2, sends HTTP
requests that hit both workers (round-robin), and asserts the
requests_total counter is monotonically non-decreasing across polls —
which would fail against the original dict-based implementation and
passes against the prometheus_client multiprocess-mode fix.

This is slower and more heavyweight than the rest of the suite (real
subprocess, real sockets, real wall-clock waits) so it's kept in its
own file rather than mixed into test_app.py's fast in-process tests.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

APP_DIR = Path(__file__).parent.parent / "app"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"http://127.0.0.1:{port}/healthz", timeout=1)
            return True
        except requests.exceptions.ConnectionError:
            time.sleep(0.2)
    return False


@pytest.fixture
def running_app():
    port = _free_port()
    multiproc_dir = tempfile.mkdtemp(prefix="prometheus_multiproc_test_")
    env = dict(os.environ)
    env["PROMETHEUS_MULTIPROC_DIR"] = multiproc_dir

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "gunicorn",
            "--config", "gunicorn.conf.py",
            "--bind", f"127.0.0.1:{port}",
            "--workers", "2",
            "main:app",
        ],
        cwd=str(APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_for_server(port):
            proc.terminate()
            out, _ = proc.communicate(timeout=5)
            pytest.fail(f"gunicorn did not start in time. Output:\n{out.decode(errors='replace')}")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(multiproc_dir, ignore_errors=True)


def test_metrics_consistent_across_real_multiprocess_workers(running_app):
    """The actual regression test: hit the real running app (2 gunicorn
    worker processes) enough times to guarantee both workers have
    served requests, then poll /metrics repeatedly and assert the
    counters never go backwards. Against the original dict-based
    REQUEST_COUNT this would fail (counters visibly jump up AND down
    depending on which worker answers /metrics, exactly as observed
    manually: 5, 17, 18, 6)."""
    # Enough requests, spread over enough real HTTP round-trips, to
    # make it very likely gunicorn's OS-level connection scheduling
    # has routed some to each of the 2 workers.
    for _ in range(20):
        requests.get(f"{running_app}/articles/999", timeout=2)  # deliberate 404s

    samples = []
    for _ in range(8):
        resp = requests.get(f"{running_app}/metrics", timeout=2)
        assert resp.status_code == 200
        body = resp.text
        total_line = next(l for l in body.splitlines() if l.startswith("app_requests_total "))
        errors_line = next(l for l in body.splitlines() if l.startswith("app_requests_errors_total "))
        total = float(total_line.split()[-1])
        errors = float(errors_line.split()[-1])
        samples.append((total, errors))

    totals = [s[0] for s in samples]
    errors = [s[1] for s in samples]

    # The core regression assertion: monotonically non-decreasing.
    # A per-worker-only counter (the original bug) fails this because
    # different workers report different absolute values depending on
    # which one happens to serve a given /metrics request.
    assert totals == sorted(totals), f"requests_total went backwards across polls: {totals}"
    assert errors == sorted(errors), f"requests_errors_total went backwards across polls: {errors}"

    # And the error count must reflect ALL 20 of the 404s issued above,
    # summed across both workers — not just whichever worker's private
    # counter happened to answer the final /metrics call.
    assert errors[-1] >= 20, f"expected errors_total to include all 20 injected 404s across both workers, got {errors[-1]}"
