#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# render-build.sh — Render.com Build Script for BuildR
# ──────────────────────────────────────────────────────────────
#
# PURPOSE
# -------
# Render executes this script as the Build Command. It runs
# inside the project directory during the build phase. We:
#
#   1. Install Python dependencies from requirements.txt
#   2. Download a pinned Tectonic LaTeX compiler and install
#      it to /usr/local/bin so it is on PATH and persists
#      into the runtime image.
#
# WHY /usr/local/bin/tectonic?
# ----------------------------
# Earlier revisions installed Tectonic to "$HOME/tectonic" and
# relied on services/pdf_service.py to discover it there. That
# broke at runtime: Render's build container writes to one
# filesystem, the runtime container runs from another, and
# arbitrary files in $HOME during build are not guaranteed
# to exist in the runtime image.
#
# /usr/local/bin is part of the runtime image's PATH on every
# standard Render runtime, so `tectonic` is discoverable by
# the same lookup (shutil.which) that already exists in
# services/pdf_service.py.
#
# TECTONIC VERSION
# ----------------
# Pinned to v0.16.9 (released 2026-04-17), the current stable
# release at time of writing. To upgrade, update TECTONIC_VERSION
# below and redeploy.
#
# Source: https://github.com/tectonic-typesetting/tectonic/releases
# ──────────────────────────────────────────────────────────────

set -o errexit   # Exit immediately if any command fails
set -o nounset   # Treat unset variables as errors
set -o pipefail  # Fail on first error in a pipeline

# ── Configuration ────────────────────────────────────────────
TECTONIC_VERSION="0.16.9"
TECTONIC_ARCHIVE="tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-gnu.tar.gz"
TECTONIC_URL="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/${TECTONIC_ARCHIVE}"
TECTONIC_TARGET="/usr/local/bin/tectonic"

# ── Step 1: Install Python dependencies ──────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Step 1/2: Installing Python dependencies"
echo "──────────────────────────────────────────────────────────"
pip install --upgrade pip
pip install -r requirements.txt

# ── Step 2: Install Tectonic ─────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Step 2/2: Installing Tectonic v${TECTONIC_VERSION}"
echo "──────────────────────────────────────────────────────────"

# Skip the download if a usable Tectonic is already on PATH.
if command -v tectonic >/dev/null 2>&1 && tectonic --version >/dev/null 2>&1; then
    echo "Tectonic already present on PATH: $(command -v tectonic)"
    echo "  Version: $(tectonic --version 2>&1 | head -n 1)"
else
    echo "Downloading ${TECTONIC_URL} ..."
    curl -fsSL -o "/tmp/${TECTONIC_ARCHIVE}" "${TECTONIC_URL}"

    echo "Extracting tectonic binary to /tmp ..."
    tar -xzf "/tmp/${TECTONIC_ARCHIVE}" -C /tmp tectonic
    chmod +x /tmp/tectonic

    # Move into /usr/local/bin so it is on PATH in the runtime
    # image. Render grants its build user write access here.
    echo "Installing tectonic to ${TECTONIC_TARGET} ..."
    mv /tmp/tectonic "${TECTONIC_TARGET}"

    # Clean up the downloaded archive (the binary is now in place).
    rm -f "/tmp/${TECTONIC_ARCHIVE}"
fi

# ── Verification ─────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Verifying Tectonic installation:"
echo "  Location: $(command -v tectonic || echo '<NOT FOUND>')"
echo "  Version:  $(${TECTONIC_TARGET} --version 2>&1 | head -n 1)"
echo "──────────────────────────────────────────────────────────"
echo "Build complete."
