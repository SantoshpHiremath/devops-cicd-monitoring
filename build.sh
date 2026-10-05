#!/bin/bash
# Builds the application image (devops-cicd-demo:latest).
#
# NOTE: this script is written for a build environment where all
# outbound HTTPS goes through a local TLS-inspecting proxy (bound to
# 127.0.0.1 on the host, trusted by pip, apt, curl, etc. via env vars
# like HTTPS_PROXY / SSL_CERT_FILE). A `docker build` runs in its own
# network namespace by default, so it does not inherit those env vars or
# that proxy, and pypi.org requests inside the build would fail TLS
# verification ("self-signed certificate in certificate chain").
#
# The fix below -- `--network=host` so the build shares the host's
# network namespace and can reach 127.0.0.1:<proxy-port>, plus
# `--build-arg HTTPS_PROXY=...` so pip inside the build uses it -- is a
# standard pattern for building Docker images behind a corporate proxy /
# TLS-inspecting gateway. Without such a proxy in front of outbound
# HTTPS, a plain `docker build -t devops-cicd-demo .` works.
set -euo pipefail

PROXY_PORT="${CCR_PROXY_PORT:-41871}"

docker build \
  --network=host \
  --build-arg HTTPS_PROXY="http://127.0.0.1:${PROXY_PORT}" \
  --build-arg HTTP_PROXY="http://127.0.0.1:${PROXY_PORT}" \
  -t devops-cicd-demo:latest \
  .

echo "Built devops-cicd-demo:latest"
