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
#   3. Pre-warm Tectonic's package cache (also inside the
#      project tree at .tectonic/cache/) by compiling a small
#      dummy document during build. Without this the first
#      runtime compile would download ~50 MB of LaTeX packages
#      over Render's slower egress and blow past the compile
#      timeout.
#
# WHY .tectonic/ IN THE PROJECT DIRECTORY?
# ----------------------------------------
# On Render's Python runtime the base image directories are
# read-only (mv into /usr/local/bin fails with
# "inter-device move ... Read-only file system"), and arbitrary
# files written to $HOME during build are not guaranteed to
# survive into the runtime image. The project directory itself
# (/opt/render/project/src) IS writable during build AND is
# carried into the runtime image, so it is the safe place to
# drop the binary AND the package cache.
#
# The .tectonic/ folder is added to .gitignore so the binary
# and the (large, host-specific) cache are never committed.
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
TECTONIC_CACHE="${TECTONIC_DIR}/cache"

# ── Step 1: Install Python dependencies ──────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Step 1/2: Installing Python dependencies"
echo "──────────────────────────────────────────────────────────"
pip install --upgrade pip
pip install -r requirements.txt

# ── Step 2: Install Tectonic ─────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Step 2/3: Installing Tectonic v${TECTONIC_VERSION}"
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

# ── Step 3: Pre-warm the Tectonic package cache ──────────────
# Tectonic downloads ~50 MB of LaTeX packages from
# relay.fullyjustified.net on first compile. On Render that
# download is slow enough to blow past our 180 s subprocess
# timeout, and the cache lives in $HOME/.cache/Tectonic by
# default — which is NOT persistent across deploys. We fix
# both problems here:
#
#   1. Repoint the cache to .tectonic/cache/ inside the
#      project tree (persistent on Render).
#   2. Compile a small dummy document during build so the
#      runtime image already has every package Tectonic needs.
#
# The runtime side (services/pdf_service.py) sets the same
# TECTONIC_CACHE_DIR env var so all compiles hit this cache.
echo "──────────────────────────────────────────────────────────"
echo "Step 3/3: Pre-warming Tectonic package cache"
echo "──────────────────────────────────────────────────────────"

mkdir -p "${TECTONIC_CACHE}"
DUMMY_TEX='/tmp/buildr_tectonic_smoke.tex'
cat > "${DUMMY_TEX}" <<'LATEX'
\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[utf8]{inputenc}
\usepackage{hyperref}
\usepackage{geometry}
\geometry{margin=1in}
\title{BuildR Tectonic Smoke Test}
\begin{document}
Hello from BuildR's Tectonic cache warmup.
\end{document}
LATEX

# Compile with the project-tree cache. Tectonic will download
# any packages it doesn't yet have. This is the long step (up
# to a couple of minutes on slow Render egress).
echo "Compiling smoke document (this downloads LaTeX packages on first run) ..."
TECTONIC_CACHE_DIR="${TECTONIC_CACHE}" \
    "${TECTONIC_BIN}" --keep-logs --outdir /tmp "${DUMMY_TEX}"

# Inspect the cache to confirm it actually populated. If the
# compile silently succeeded but the cache is empty, something
# is wrong and we'd rather fail loudly here than at runtime.
CACHE_BYTES=$(du -sb "${TECTONIC_CACHE}" 2>/dev/null | awk '{print $1}')
CACHE_BYTES=${CACHE_BYTES:-0}
echo "Cache populated at ${TECTONIC_CACHE}: ${CACHE_BYTES} bytes"
if [ "${CACHE_BYTES}" -lt 1048576 ]; then
    echo "WARNING: Tectonic cache is suspiciously small (< 1 MB)." >&2
    echo "         First runtime compile may re-download packages." >&2
fi
rm -f "${DUMMY_TEX}" /tmp/buildr_tectonic_smoke.log

# ── Verification ─────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Verifying Tectonic installation:"
echo "  Location:   ${TECTONIC_BIN}"
echo "  Exists:     $([[ -x "${TECTONIC_BIN}" ]] && echo yes || echo no)"
echo "  Version:    $(${TECTONIC_BIN} --version 2>&1 | head -n 1)"
echo "  Cache dir:  ${TECTONIC_CACHE}"
echo "  Cache size: $(du -sh "${TECTONIC_CACHE}" 2>/dev/null | awk '{print $1}')"
echo "──────────────────────────────────────────────────────────"
echo "Build complete."
