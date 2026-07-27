#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# render-build.sh — Render.com Build Script for BuildR
# ──────────────────────────────────────────────────────────────
#
# PURPOSE
# -------
# Render executes this script as the Build Command.  It runs
# inside /opt/render/project/src (the repo root) during the
# build phase.  Files created here persist into the runtime
# image, so the Tectonic binary will be available when the
# application starts.
#
# WHAT IT DOES
# ------------
#   1. Installs Python dependencies from requirements.txt
#   2. Downloads a pinned version of the Tectonic LaTeX compiler
#      and places it in $HOME/tectonic
#
# WHY $HOME/tectonic?
# -------------------
# The existing _find_tectonic() function in services/pdf_service.py
# checks Path.home() / "tectonic" as a fallback search location
# (line 125).  By placing the binary there, the application
# discovers it automatically — no PATH modification, no Procfile
# change, and no Python code change required.
#
# TECTONIC VERSION
# ----------------
# Pinned to v0.16.9 (released 2026-04-17), the current stable
# release at time of writing.  To upgrade, update TECTONIC_VERSION
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

# Download the pinned release tarball from GitHub Releases.
# Using GitHub Releases directly (instead of the drop-sh installer
# script) gives us version pinning and avoids depending on a
# third-party domain (drop-sh.fullyjustified.net).
echo "Downloading ${TECTONIC_URL} ..."
curl -fsSL -o "/tmp/${TECTONIC_ARCHIVE}" "${TECTONIC_URL}"

# Extract the tectonic binary from the tarball.
# The archive contains a single file: the tectonic executable.
echo "Extracting tectonic binary to \$HOME/tectonic ..."
tar -xzf "/tmp/${TECTONIC_ARCHIVE}" -C /tmp tectonic

# Move the binary to $HOME/tectonic — the exact path that
# _find_tectonic() checks at services/pdf_service.py line 125:
#   home_path_unix = Path.home() / "tectonic"
mv /tmp/tectonic "$HOME/tectonic"
chmod +x "$HOME/tectonic"

# Clean up the downloaded archive
rm -f "/tmp/${TECTONIC_ARCHIVE}"

# ── Verification ─────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Verifying Tectonic installation:"
echo "  Location: $HOME/tectonic"
echo "  Version:  $("$HOME/tectonic" --version)"
echo "──────────────────────────────────────────────────────────"
echo "Build complete."
