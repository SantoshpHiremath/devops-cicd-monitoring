# devops-cicd-monitoring

A small CI/CD + monitoring project covering GitHub Actions, GitLab CI/CD, Docker, Kubernetes manifests, and monitoring/alerting for a deployable Flask service. Everything here was built, run, and tested, including against a real running Docker container and not just the Flask test client.

## What it does

A small Flask API (`app/main.py`) for a mobile knowledge platform (articles/knowledge-base entries, health and readiness probes, Prometheus metrics): a realistic, small deployable target for a CI/CD pipeline and a monitoring stack.

- `app/`: the Flask service, containerized with a multi-stage, non-root, health-checked Dockerfile.
- `monitoring/watchdog.py`: a polling/alerting script that hits `/healthz`, `/readyz`, and `/metrics` on an interval, and fires structured JSON alerts (stdout + an alert log file) on outage, not-ready, or an elevated error rate.
- `.github/workflows/ci.yml`: a GitHub Actions workflow: lint (flake8), test (pytest), build the Docker image, and smoke-test the built image against its real HTTP endpoints.
- `.gitlab-ci.yml`: the same pipeline targeted at GitLab CI/CD (stages, a pip cache keyed on `app/requirements.txt`, GitLab's Container Registry variables, `docker:dind` services), not a renamed copy of the GitHub Actions file. The lint and test stages run the same commands verified against this codebase (`flake8`, `pytest`), and the build/smoke-test stages mirror the verified `docker build` / container-polling steps.
- `k8s/deployment.yaml`, `k8s/hpa.yaml`: Kubernetes manifests (Deployment, Service, HorizontalPodAutoscaler) targeting this project's container image, port 8080, and the `/healthz` and `/readyz` endpoints from `app/main.py`.
- `tests/`: 26 tests, all passing, including two heavyweight integration tests that spin up a real gunicorn process or a real background HTTP server rather than mocking anything.

## Scope

The project is cloud-agnostic: Docker, CI/CD, Kubernetes, and monitoring, with no dependency on a specific cloud provider.

- The Dockerfile builds `FROM python:3.12-slim`. For environments without Docker Hub access (registry pulls return 403), `build_local_base.sh` builds an equivalent local base image (`local-base:py312`) from scratch with `debootstrap` (pulling packages from `archive.ubuntu.com`) and imports it with `docker import`. The multi-stage structure, non-root user, layer caching, and healthcheck are the same whichever base image is used.
- `build.sh` builds the image behind a TLS-inspecting outbound proxy, using `--network=host` plus `--build-arg HTTPS_PROXY=...` so pip inside the build trusts the proxy, which is a standard pattern for corporate networks. Without such a proxy, a plain `docker build` works.
- The CI workflow files are validated YAML (the GitHub Actions workflow with `actionlint`, the GitLab file parsed with PyYAML) and every underlying command was run locally; they have not been triggered on a hosted GitHub or GitLab runner.
- The Kubernetes manifests are validated against the Kubernetes 1.31 OpenAPI schema with the `kubernetes-validate` Python library, which checks each object the way API-server validation would. All three objects (Deployment, Service, HorizontalPodAutoscaler) validate cleanly. They have not been applied to a running cluster, so Running/Ready state, probe behavior, and HPA scaling under load are the next things to verify on a real cluster.
- `monitoring/watchdog.py` polls a real running service and fires real structured alerts, verified against both a live healthy container and a container deliberately stopped mid-run (see Tests). It writes JSON alerts to stdout and an append-only log file, the same shape a PagerDuty, Slack, or email integration would consume.

## Tests

- `python3 -m pytest tests/ -v`: 26/26 tests pass, including the multiprocess-gunicorn regression test and the live-HTTP-server watchdog integration tests.
- `flake8 app monitoring --max-line-length=120`: clean, zero violations.
- `k8s/deployment.yaml` and `k8s/hpa.yaml` validated against the Kubernetes 1.31 OpenAPI schema with `kubernetes-validate`: all three objects (Deployment, Service, HorizontalPodAutoscaler) validate cleanly.
- `bash build.sh`: `docker build` succeeds end to end.
- `docker run` of the built image, then checks against the actual running container (not the Flask test client):
  - `/healthz`, `/readyz`, `/articles`, `/articles/<id>` (GET and POST), and `/metrics` all return correct responses.
  - `docker inspect --format='{{.State.Health.Status}}'` confirmed `healthy` after the container's `start-period`.
  - `/tmp/prometheus_multiproc/` inside the container contains the expected per-worker `counter_<pid>.db` / `gauge_all_<pid>.db` files, confirming the multiprocess metrics architecture works as designed.
  - 20 real 404s fired at the running container, then `/metrics` polled 8 times in a row: `requests_total` increased monotonically each time and `errors_total` correctly held at 20, regardless of which worker answered.
- `monitoring/watchdog.py` run against the real container twice: once healthy (produced `{"status": "ok"}` on each poll), and once after `docker stop`-ing the container mid-run (produced `critical` / `healthz` alerts to stdout and to an alert log file, with the real connection-refused error message attached).

## Running it

```bash
# Build the local base image (only needed without Docker Hub access)
bash build_local_base.sh

# Build the application image
bash build.sh

# Run it
docker run -d --name devops-demo -p 8080:8080 devops-cicd-demo:latest
curl http://localhost:8080/healthz

# Watch it
python3 monitoring/watchdog.py --url http://localhost:8080 --interval 10

# Test it
python3 -m pytest tests/ -v
```

## Notes

**A bug found and fixed by running under Docker.** The original version of `app/main.py` kept request/error counters in a plain Python dict (`REQUEST_COUNT = {"total": 0, "errors": 0}`). That passed every unit test against the Flask test client, which is single-process. But gunicorn (used in the real container, `--workers 2`) pre-forks separate OS processes, each with its own private memory, so each worker had its own independent copy of that dict.

I caught this by running the built image and polling `/metrics` repeatedly against the live container: consecutive polls returned visibly inconsistent values (`requests_total` jumping 5, 17, 18, 6), because each poll landed on whichever of the 2 workers gunicorn routed it to.

The fix uses the `prometheus_client` library's documented multiprocess mode: each worker writes its counters to per-worker mmap'd files under `PROMETHEUS_MULTIPROC_DIR`, and `/metrics` aggregates across all of them at scrape time via `MultiProcessCollector`, plus a `child_exit` gunicorn hook (`gunicorn.conf.py`) to clean up a worker's files when it exits. This is the standard pattern for this class of problem in any pre-fork WSGI server.

It is guarded by a heavyweight regression test, `tests/test_metrics_multiprocess.py`, which starts a real gunicorn process with 2 workers as a subprocess, fires 20 real HTTP requests at it, polls `/metrics` 8 times over real HTTP, and asserts the counters are monotonically non-decreasing. I verified the test fails against the original implementation (by temporarily reverting `main.py`; it failed with `requests_total went backwards across polls: [20.0, 21.0, 3.0, 22.0, ...]`, the exact symptom seen manually) and passes against the fix.

## Possible extensions

- Apply the Kubernetes manifests to a real cluster (kind, k3s, or a managed service) and verify readiness and HPA scaling under load.
- Run the GitHub Actions and GitLab pipelines on hosted runners.
- Deploy to a cloud provider (for example AWS) and wire the watchdog's alerts into Slack or PagerDuty.
