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
#      it into the project tree at .tectonic/tectonic so the
#      runtime image can locate it via services/pdf_service.py.
#
# WHY .tectonic/tectonic IN THE PROJECT DIRECTORY?
# -----------------------------------------------
# On Render's Python runtime the base image directories are
# read-only (mv into /usr/local/bin fails with
# "inter-device move ... Read-only file system"), and arbitrary
# files written to $HOME during build are not guaranteed to
# survive into the runtime image. The project directory itself
# (/opt/render/project/src) IS writable during build AND is
# carried into the runtime image, so it is the safe place to
# drop the binary.
#
# The .tectonic/ folder is added to .gitignore so the binary
# itself is never committed.
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

# Resolve project directory. Render's build runs with pwd at the
# project root, so $(pwd) is reliable. We pin the install to a
# .tectonic/ folder at that root.
PROJECT_ROOT="$(pwd)"
TECTONIC_DIR="${PROJECT_ROOT}/.tectonic"
TECTONIC_BIN="${TECTONIC_DIR}/tectonic"

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

    # Install into .tectonic/ at the project root. This dir is
    # writable during build and its contents are copied into the
    # runtime image, so gunicorn will find it on startup.
    echo "Installing tectonic to ${TECTONIC_BIN} ..."
    mkdir -p "${TECTONIC_DIR}"
    mv /tmp/tectonic "${TECTONIC_BIN}"

    # Clean up the downloaded archive (the binary is now in place).
    rm -f "/tmp/${TECTONIC_ARCHIVE}"
fi

# ── Verification ─────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Verifying Tectonic installation:"
echo "  Location: ${TECTONIC_BIN}"
echo "  Exists:   $([[ -x "${TECTONIC_BIN}" ]] && echo yes || echo no)"
echo "  Version:  $(${TECTONIC_BIN} --version 2>&1 | head -n 1)"
echo "──────────────────────────────────────────────────────────"
echo "Build complete."
