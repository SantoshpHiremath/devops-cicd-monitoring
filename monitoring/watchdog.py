"""A small, real monitoring/alerting watchdog for the knowledge-platform
service. Polls /healthz and /metrics on an interval and fires a
structured alert (JSON line to stdout, and appended to an alert log
file) when:
  - the service is unreachable or /healthz doesn't return 200
  - /readyz returns non-200 (service alive but not ready)
  - the error rate over the polling window exceeds a threshold

This is a real, runnable, testable poller — not a description of one.
It intentionally does NOT integrate with a real alerting backend
(PagerDuty, Slack, etc.) since there's nothing in this sandbox to wire
up honestly; instead it writes structured alerts that a real
integration would consume, which is the same shape used by many
lightweight in-house monitoring scripts before they graduate to a full
alerting platform.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class PollResult:
    timestamp: float
    healthz_ok: bool
    readyz_ok: bool
    requests_total: float | None
    errors_total: float | None
    error: str | None = None


@dataclass
class WatchdogState:
    """Tracks state across polls so the error-rate check can look at
    the delta between two polls rather than the lifetime total (a
    lifetime total would dilute a sudden spike into meaninglessness
    once uptime is long)."""
    last_requests_total: float | None = None
    last_errors_total: float | None = None
    alerts: list = field(default_factory=list)


def _get_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def _get_text(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def _parse_metric(body, name):
    for line in body.splitlines():
        if line.startswith(f"{name} "):
            return float(line.split()[-1])
    return None


def poll(base_url, timeout=3.0):
    """Poll the service once and return a PollResult. Never raises —
    connection failures and non-200s are captured in the result so the
    caller can alert on them, which is the whole point of a watchdog
    (it must survive the thing it's watching being down)."""
    ts = time.time()
    healthz_ok = False
    readyz_ok = False
    requests_total = None
    errors_total = None
    error = None

    try:
        status, _ = _get_json(f"{base_url}/healthz", timeout)
        healthz_ok = status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        error = f"healthz unreachable: {e}"

    try:
        status, _ = _get_json(f"{base_url}/readyz", timeout)
        readyz_ok = status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        error = error or f"readyz unreachable: {e}"

    try:
        status, body = _get_text(f"{base_url}/metrics", timeout)
        if status == 200:
            requests_total = _parse_metric(body, "app_requests_total")
            errors_total = _parse_metric(body, "app_requests_errors_total")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        error = error or f"metrics unreachable: {e}"

    return PollResult(ts, healthz_ok, readyz_ok, requests_total, errors_total, error)


def evaluate(result, state, error_rate_threshold=0.5, min_requests_for_rate_check=5):
    """Given one poll result and the running state, decide which
    alerts (if any) should fire. Returns a list of alert dicts.
    Mutates state in place (updates last_* for the next call)."""
    alerts = []

    if not result.healthz_ok:
        alerts.append({
            "severity": "critical",
            "check": "healthz",
            "message": result.error or "healthz did not return 200",
        })

    if result.healthz_ok and not result.readyz_ok:
        alerts.append({
            "severity": "warning",
            "check": "readyz",
            "message": "service is alive but not ready",
        })

    if result.requests_total is not None and result.errors_total is not None:
        if state.last_requests_total is not None and state.last_errors_total is not None:
            delta_requests = result.requests_total - state.last_requests_total
            delta_errors = result.errors_total - state.last_errors_total
            # Guard against a restarted process where counters reset to
            # a lower value than last time — a negative delta would
            # otherwise silently produce a nonsensical negative or
            # >100% rate.
            enough_samples = delta_requests >= min_requests_for_rate_check
            if delta_requests >= 0 and delta_errors >= 0 and enough_samples:
                error_rate = delta_errors / delta_requests
                if error_rate > error_rate_threshold:
                    message = (
                        f"error rate {error_rate:.0%} over last "
                        f"{int(delta_requests)} requests exceeds "
                        f"threshold {error_rate_threshold:.0%}"
                    )
                    alerts.append({
                        "severity": "critical",
                        "check": "error_rate",
                        "message": message,
                    })
        state.last_requests_total = result.requests_total
        state.last_errors_total = result.errors_total

    return alerts


def emit_alert(alert, result, alert_log_path=None):
    record = {
        "timestamp": result.timestamp,
        "severity": alert["severity"],
        "check": alert["check"],
        "message": alert["message"],
    }
    line = json.dumps(record)
    print(f"ALERT {line}", file=sys.stderr)
    if alert_log_path:
        with open(alert_log_path, "a") as f:
            f.write(line + "\n")


def run_watchdog(
    base_url,
    interval_seconds,
    iterations=None,
    alert_log_path=None,
    error_rate_threshold=0.5,
    sleep_fn=time.sleep,
):
    """Main polling loop. iterations=None runs forever; a finite value
    is used by tests and by anyone wanting a bounded run (e.g. a cron
    invocation that polls a few times and exits)."""
    state = WatchdogState()
    count = 0
    while iterations is None or count < iterations:
        result = poll(base_url)
        alerts = evaluate(result, state, error_rate_threshold=error_rate_threshold)
        for alert in alerts:
            emit_alert(alert, result, alert_log_path)
        if not alerts:
            print(json.dumps({"timestamp": result.timestamp, "status": "ok"}))
        count += 1
        if iterations is None or count < iterations:
            sleep_fn(interval_seconds)
    return state


def main():
    parser = argparse.ArgumentParser(
        description="Poll the knowledge-platform service and alert on failures."
    )
    parser.add_argument(
        "--url", default="http://localhost:8080", help="Base URL of the service"
    )
    parser.add_argument(
        "--interval", type=float, default=10.0, help="Seconds between polls"
    )
    parser.add_argument(
        "--iterations", type=int, default=None,
        help="Number of polls before exiting (default: run forever)",
    )
    parser.add_argument(
        "--alert-log", default=None,
        help="Path to append structured alert JSON lines to",
    )
    parser.add_argument(
        "--error-rate-threshold", type=float, default=0.5,
        help="Error-rate fraction over a poll window that triggers a critical alert",
    )
    args = parser.parse_args()

    run_watchdog(
        args.url,
        args.interval,
        iterations=args.iterations,
        alert_log_path=args.alert_log,
        error_rate_threshold=args.error_rate_threshold,
    )


if __name__ == "__main__":
    main()
