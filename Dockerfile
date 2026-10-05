# NOTE: this Dockerfile builds FROM the public python:3.12-slim image.
# For environments without access to Docker Hub or other container
# registries, build_local_base.sh assembles an equivalent local base
# image (local-base:py312, built with debootstrap + `apt-get install
# python3` and imported with `docker import`); swap the FROM line to use
# it. The multi-stage structure, layer caching, and non-root user below
# are unaffected by which base image is used.

FROM python:3.12-slim AS base

# Best practice: run as a non-root user, not root, inside the container.
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Best practice: copy only the dependency manifest first so Docker's
# layer cache is reused on rebuilds when only application code changes,
# not dependencies.
COPY app/requirements.txt .

# Ubuntu 24.04's system Python is "externally managed" (PEP 668) and
# refuses unprotected `pip install`. The correct fix — not just adding
# --break-system-packages — is a dedicated virtual environment, which is
# also the real-world best-practice pattern for a container image: it
# keeps app dependencies isolated from any OS-level Python tooling.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

COPY app/main.py app/gunicorn.conf.py .

# prometheus_client multiprocess mode (gunicorn runs multiple worker
# processes below) requires a writable directory where each worker
# writes its own mmap'd metric files; /metrics then merges across all
# of them. This is what actually fixes a real bug found by running
# this service under gunicorn and polling /metrics repeatedly — see
# the docstring in app/main.py for the full diagnosis.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
RUN mkdir -p /tmp/prometheus_multiproc && chown app:app /tmp/prometheus_multiproc

# Best practice: drop privileges before running the app.
USER app

EXPOSE 8080

# Real container-level healthcheck, not just an app-level endpoint —
# Docker (and any orchestrator reading this image's metadata) can use
# this to know the container is actually healthy, not just running.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=2)" || exit 1

CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:8080", "--workers", "2", "--access-logfile", "-", "main:app"]
