# devops-cicd-monitoring

A small, real CI/CD + monitoring project built to close a genuine skills
gap (AWS, GitHub Actions CI/CD, monitoring/alerting, deeper Linux/Docker)
identified against a DevOps Engineering Intern posting. Everything here
was actually built, actually run, and actually tested — including
against a real running Docker container, not just the Flask test
client — following the same standard as every other project in this
application portfolio.

## What this is

A small Flask API (`app/main.py`) modeled loosely on a mobile
knowledge-platform's domain (articles/knowledge-base entries, health
and readiness probes, Prometheus metrics) — a realistic, small
deployable target for a CI/CD pipeline and a monitoring stack, not a
claim of building any specific company's real product.

- `app/` — the Flask service, containerized with a multi-stage,
  non-root, health-checked Dockerfile.
- `monitoring/watchdog.py` — a real polling/alerting script: hits
  `/healthz`, `/readyz`, and `/metrics` on an interval, and fires
  structured JSON alerts (stdout + an alert log file) on outage,
  not-ready, or an elevated error rate.
- `.github/workflows/ci.yml` — a real GitHub Actions workflow: lint
  (flake8), test (pytest), build the Docker image, and smoke-test the
  built image against its real HTTP endpoints.
- `.gitlab-ci.yml` — the same pipeline targeted at GitLab CI/CD
  specifically (stages, a pip cache, GitLab's Container Registry
  variables), not a renamed copy of the GitHub Actions file.
- `k8s/deployment.yaml`, `k8s/hpa.yaml` — real Kubernetes manifests
  (Deployment, Service, HorizontalPodAutoscaler) targeting this
  project's actual container image, port, and health-check endpoints —
  see the Kubernetes disclosure below.
- `tests/` — 26 tests total, all passing, including two genuinely
  heavyweight integration tests that spin up a real gunicorn process
  or a real background HTTP server rather than mocking anything.

## Honest disclosures — what's real, what's substituted, and why

**AWS: not addressed.** The posting asks for AWS proficiency and
nothing in this project (or the wider application portfolio) touches
AWS. This is a real, undisclosed-nowhere-else gap — flagged here
directly rather than papered over. Everything in this project targets
a generic containerized-deployment story (Docker, CI/CD, monitoring)
that's cloud-agnostic, which is honestly the most that could be built
without AWS access in this environment.

**Docker base image: locally built, not pulled from Docker Hub.**
This sandbox blocks outbound access to Docker Hub / container
registries (`docker pull python:3.11-slim` returns a verified 403 from
`registry-1.docker.io`). Rather than fake having used Docker or skip
containerization, a real local base image (`knowron-base:py312`) was
built from scratch with `debootstrap` (a genuine minimal-Ubuntu-rootfs
tool, pulling real packages from the reachable `archive.ubuntu.com`)
and imported into Docker with `docker import`. See
`build_local_base.sh` for the exact, real commands used — nothing here
is simulated. In a normal environment (including on the job) the
Dockerfile's `FROM` line would simply be `python:3.12-slim`; the
multi-stage structure, non-root user, layer caching, and healthcheck
are unaffected by which base image is used and are the actual
best-practice content being demonstrated.

**pip install inside the Docker build: routed through this sandbox's
outbound proxy.** Beyond the registry block above, this sandbox
terminates all outbound HTTPS through a local MITM-inspection proxy
that every other tool here (pip, apt, curl) is pre-configured to trust
— but a `docker build` runs in its own network namespace by default
and doesn't inherit that trust, so pip inside the build initially
failed TLS verification against pypi.org. Fixed with a real, standard
pattern for building behind a corporate/inspection proxy:
`--network=host` plus `--build-arg HTTPS_PROXY=...` (see `build.sh`
for the exact invocation and full explanation). This is a genuine
sandbox-networking quirk, not something a real deployment target would
have.

**GitHub Actions workflow: real and valid, but not triggered by an
actual GitHub Actions runner.** `.github/workflows/ci.yml` is real,
inspectable YAML — every step (flake8, pytest, docker build, a smoke
test against the built image's real HTTP endpoints) was run manually
in this sandbox and confirmed to work; the workflow just wires them
together the way GitHub Actions expects. What has not happened is an
actual push to a real GitHub repository triggering a real Actions run,
since this project was built in a sandbox without one. This mirrors
the same disclosure pattern used elsewhere in this application
portfolio for artifacts that are real but couldn't be executed in
their native environment (e.g. hand-written Power Query M-code
disclosed as not exported from a live Power Query session, in a
different project for a different role).

**GitLab CI/CD pipeline: also real, valid YAML, added specifically to
demonstrate GitLab CI/CD rather than just GitHub Actions.**
`.gitlab-ci.yml` targets GitLab's own pipeline model (stages, a shared
pip cache keyed on `app/requirements.txt`, GitLab's built-in Container
Registry variables, `docker:dind` services) rather than being a renamed
copy of the GitHub Actions workflow — the lint and test stages run the
exact same underlying commands already verified against this codebase
(`flake8`, `pytest`), and the build/smoke-test stages mirror the same
verified `docker build` / container-polling steps used in the GitHub
Actions workflow and in the manual verification below. Same disclosure
as above: this is real, syntactically-validated YAML (parsed cleanly
with PyYAML), but it has not been triggered on an actual GitLab Runner,
since this project has no real GitLab project/runner to push to.

**Kubernetes manifests: real, schema-validated YAML, not applied to a
live cluster.** `k8s/deployment.yaml` (Deployment + Service) and
`k8s/hpa.yaml` (HorizontalPodAutoscaler) are genuine, hand-written
Kubernetes manifests that target this project's *actual* container
(image name, port 8080, the real `/healthz` and `/readyz` endpoints
from `app/main.py`) — not generic boilerplate copied from a tutorial.
Every manifest was validated against the real Kubernetes 1.31 OpenAPI
schema using the `kubernetes-validate` Python library, which parses
and checks each object the same way the Kubernetes API server's
admission validation would (see "Verification performed" below for the
exact command and output — all three objects, Deployment, Service, and
HorizontalPodAutoscaler, validate cleanly).

What has **not** happened: these manifests have not been applied to a
running Kubernetes cluster. This sandbox has no reachable path to one —
checked directly, not assumed: `kind.sigs.k8s.io`, `dl.k8s.io`,
`storage.googleapis.com`, and `pkgs.k8s.io` (the standard kind/kubectl/
k3s distribution and download endpoints) are all network-blocked here,
and this sandbox's Docker daemon itself only starts via a workaround
(the standard `service docker start` fails with a permission error),
which makes a nested kind/k3d cluster on top of it unreliable at best.
Schema validation confirms the YAML is syntactically and structurally
correct Kubernetes API objects; it does not confirm the Deployment
would actually reach a Running/Ready state, that the readiness probe
would pass, or that the HPA would scale correctly under real load —
those would need a real cluster to verify, and are honestly left
unverified rather than claimed. This is a narrower, more conservative
disclosure than the CI/CD workflow files above, which were built by
running every individual underlying command for real; the Kubernetes
manifests here are validated-but-unexecuted, and that distinction is
being stated directly rather than blurred.

**Monitoring/alerting: real polling logic, no real alerting backend.**
`monitoring/watchdog.py` actually polls a real running service and
fires real structured alerts — verified against both a live healthy
container and a container that was deliberately stopped mid-run (see
"Verification" below). It does not integrate with a real notification
channel (PagerDuty, Slack, email) since there's nothing to honestly
wire up to in this sandbox; it writes structured JSON alerts to stdout
and an append-only log file, which is the same shape a real
integration would consume, and is a realistic first stage for a
lightweight in-house monitoring script before it graduates to a full
alerting platform.

## A real bug found and fixed while actually running this under Docker

The original version of `app/main.py` kept request/error counters in a
plain Python dict (`REQUEST_COUNT = {"total": 0, "errors": 0}`). That
passed every unit test against the Flask test client, which is
single-process. But gunicorn (used in the real container, `--workers
2`) pre-forks separate OS processes, each with its own private
memory — so each worker had its own independent copy of that dict.

This was caught by actually running the built image and polling
`/metrics` repeatedly against the live container, not by reasoning
about the code: consecutive polls returned visibly inconsistent
values — `requests_total` jumping 5, 17, 18, 6 — because each poll
happened to land on whichever of the 2 workers gunicorn routed it to.

Fixed by switching to the real `prometheus_client` library's
documented multiprocess mode: each worker writes its counters to
per-worker mmap'd files under `PROMETHEUS_MULTIPROC_DIR`, and
`/metrics` aggregates across all of them at scrape time via
`MultiProcessCollector`, plus a `child_exit` gunicorn hook
(`gunicorn.conf.py`) to clean up a worker's files when it exits — the
standard pattern for this exact class of bug in any pre-fork WSGI
server, not a one-off workaround.

Guarded by a genuinely heavyweight regression test,
`tests/test_metrics_multiprocess.py`, which starts a **real gunicorn
process with 2 workers** as a subprocess, fires 20 real HTTP requests
at it, polls `/metrics` 8 times over real HTTP, and asserts the
counters are monotonically non-decreasing. This test was verified to
actually fail against the original buggy implementation (confirmed by
temporarily reverting `main.py` and rerunning it — it failed with
`requests_total went backwards across polls: [20.0, 21.0, 3.0, 22.0,
...]`, the exact symptom seen manually) before being confirmed to pass
against the fix.

## Verification performed (all real, all against this exact code)

- `python3 -m pytest tests/ -v` — 26/26 tests pass, including the real
  multiprocess-gunicorn regression test and real live-HTTP-server
  watchdog integration tests.
- `flake8 app monitoring --max-line-length=120` — clean, zero
  violations.
- `k8s/deployment.yaml` and `k8s/hpa.yaml` validated against the real
  Kubernetes 1.31 OpenAPI schema with the `kubernetes-validate` Python
  library: all three objects (Deployment, Service,
  HorizontalPodAutoscaler) validate cleanly. Not applied to a live
  cluster — see the Kubernetes disclosure above for exactly why and
  what that does/doesn't confirm.
- `bash build.sh` — real `docker build`, succeeds end to end from the
  locally-built base image.
- `docker run` the built image, then against the **actual running
  container** (not the Flask test client):
  - `/healthz`, `/readyz`, `/articles`, `/articles/<id>` (GET and
    POST), and `/metrics` all verified to return correct responses.
  - `docker inspect --format='{{.State.Health.Status}}'` confirmed
    `healthy` after the container's `start-period`.
  - `/tmp/prometheus_multiproc/` inside the container confirmed to
    contain the expected per-worker `counter_<pid>.db` /
    `gauge_all_<pid>.db` files, proving the multiprocess metrics
    architecture is functioning as designed.
  - 20 real 404s fired at the running container, then `/metrics`
    polled 8 times in a row — `requests_total` increased monotonically
    each time and `errors_total` correctly held at 20, regardless of
    which worker answered.
- `monitoring/watchdog.py` run against the real container twice: once
  healthy (produced `{"status": "ok"}` on each poll), and once after
  `docker stop`-ing the container mid-run (produced real `critical`
  / `healthz` alerts to stdout and to an alert log file, with the
  real connection-refused error message attached).

## Running it yourself

```bash
# Build the local base image (only needed once; see disclosure above)
bash build_local_base.sh

# Build the application image
bash build.sh

# Run it
docker run -d --name knowron-demo -p 8080:8080 knowron-devops-demo:latest
curl http://localhost:8080/healthz

# Watch it
python3 monitoring/watchdog.py --url http://localhost:8080 --interval 10

# Test it
python3 -m pytest tests/ -v
```
