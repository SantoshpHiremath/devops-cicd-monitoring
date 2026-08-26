#!/bin/bash
# Builds the local base image (knowron-base:py312) used by the
# Dockerfile in this project, because this sandbox environment blocks
# outbound access to Docker Hub / container registries (registry pulls
# return 403). This script records the REAL commands actually run to
# build it — not a simulation. In a normal environment, none of this
# would be needed; `FROM python:3.12-slim` would just work.
set -euo pipefail

WORKDIR=$(mktemp -d)
echo "Building minimal rootfs in $WORKDIR ..."

# debootstrap builds a real, minimal Ubuntu root filesystem from the
# actual Ubuntu package archive (archive.ubuntu.com is reachable even
# though Docker Hub is not).
sudo debootstrap --variant=minbase noble "$WORKDIR/rootfs" http://archive.ubuntu.com/ubuntu

# Add the universe repo (needed for python3-pip) and install Python.
sudo bash -c "cat > $WORKDIR/rootfs/etc/apt/sources.list << 'EOF'
deb http://archive.ubuntu.com/ubuntu noble main universe
deb http://archive.ubuntu.com/ubuntu noble-updates main universe
EOF"

sudo chroot "$WORKDIR/rootfs" /bin/bash -c "
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3 python3-pip python3-venv curl ca-certificates
  apt-get clean
  rm -rf /var/lib/apt/lists/*
"

# Tar the rootfs and import it as a real Docker image layer.
sudo tar -C "$WORKDIR/rootfs" -cf "$WORKDIR/rootfs.tar" .
cat "$WORKDIR/rootfs.tar" | docker import - knowron-base:py312

echo "Built knowron-base:py312"
docker run --rm knowron-base:py312 python3 --version

rm -rf "$WORKDIR"
