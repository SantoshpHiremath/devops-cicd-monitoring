"""Gunicorn config. The only non-default thing here is child_exit,
which is required by prometheus_client's multiprocess mode: when a
worker process exits, its per-worker metric files under
PROMETHEUS_MULTIPROC_DIR need to be cleaned up, or a dead worker's last
values would linger forever in every future /metrics scrape. This is
the exact pattern documented by prometheus_client itself for gunicorn
integration, not a custom workaround.
"""
from prometheus_client import multiprocess


def child_exit(server, worker):
    multiprocess.mark_process_dead(worker.pid)
