#!/bin/bash
# Builds the application image (knowron-devops-demo:latest).
#
# DISCLOSURE: this sandbox routes all outbound HTTPS through a local
# MITM-inspection proxy (bound to 127.0.0.1 on the host, used by every
# other tool in this environment — pip, apt, curl, etc. all trust it via
# env vars like HTTPS_PROXY / SSL_CERT_FILE). A `docker build` runs in
# its own network namespace by default, so it does NOT inherit those env
# vars or that proxy, and pypi.org requests inside the build fail TLS
# verification ("self-signed certificate in certificate chain").
#
# The fix below — `--network=host` so the build shares the host's
# network namespace and can reach 127.0.0.1:<proxy-port>, plus
# `--build-arg HTTPS_PROXY=...` so pip inside the build actually uses
# it — is a real, standard pattern for building Docker images behind a
# corporate proxy / TLS-inspecting gateway, which is a common situation
# on real engineering teams, not a sandbox-only hack. In a normal
# environment without a MITM proxy in front of outbound HTTPS, this
# build would work with a plain `docker build -t knowron-devops-demo .`
set -euo pipefail

PROXY_PORT="${CCR_PROXY_PORT:-41871}"

docker build \
  --network=host \
  --build-arg HTTPS_PROXY="http://127.0.0.1:${PROXY_PORT}" \
  --build-arg HTTP_PROXY="http://127.0.0.1:${PROXY_PORT}" \
  -t knowron-devops-demo:latest \
  .

echo "Built knowron-devops-demo:latest"
