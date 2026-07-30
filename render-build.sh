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
#   2. Download a pinned, MUSL-LINKED Tectonic LaTeX compiler
#      and install it into the project tree at
#      .tectonic/tectonic so the runtime image can locate it
#      via services/pdf_service.py.
#   3. Pre-warm Tectonic's package cache (also inside the
#      project tree at .tectonic/cache/) by compiling a small
#      dummy document during build. Without this the first
#      runtime compile would download ~50 MB of LaTeX packages
#      over Render's slower egress and blow past the compile
#      timeout.
#
# WHY MUSL-LINKED (not the default gnu-linked artifact)?
# -----------------------------------------------------
# The default Tectonic binaries on GitHub Releases are built
# against Ubuntu 24.04's glibc (2.39). Render's Python runtime
# image is based on an older Ubuntu (typically 22.04, glibc
# 2.35). glibc is forward-compatible WITHIN major versions
# but NOT backward: a binary built for 2.39 cannot run on
# 2.35, and it fails at startup with errors like:
#
#   /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
#   /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.39' not found
#
# The musl-linked Tectonic artifact is statically linked
# against musl libc, which means it has NO external glibc
# dependency at all — it bundles its own libc and runs on any
# Linux system regardless of glibc version. This is the same
# approach used by Alpine Linux and by every "static binary"
# distribution. Downgrading Tectonic would only postpone the
# problem (every new Ubuntu release bumps glibc); the musl
# artifact sidesteps it entirely.
#
# Compared to the gnu-linked binary, the musl artifact is:
#   • Truly portable: runs on any Linux regardless of libc
#   • Identical in functionality (same upstream Tectonic build,
#     just linked against musl instead of glibc)
#   • ~10 MB smaller (no glibc version metadata)
#   • Maintained upstream by the Tectonic team as a
#     first-class release artifact — not a third-party build
#
# WHY .tectonic/ IN THE PROJECT DIRECTORY?
# ----------------------------------------
# On Render's Python runtime the base image directories are
# read-only (mv into /usr/local/bin fails with "inter-device
# move ... Read-only file system"), and arbitrary files written
# to $HOME during build are not guaranteed to survive into the
# runtime image. The project directory itself
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

# MUSL-LINKED STATIC BINARY: works on any Linux, regardless of
# the host's glibc version. See the comment block above for
# the full rationale.
TECTONIC_TARGET="x86_64-unknown-linux-musl"
TECTONIC_ARCHIVE="tectonic-${TECTONIC_VERSION}-${TECTONIC_TARGET}.tar.gz"
TECTONIC_URL="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/${TECTONIC_ARCHIVE}"

# Resolve project directory. Render's build runs with pwd at the
# project root, so $(pwd) is reliable.
PROJECT_ROOT="$(pwd)"
TECTONIC_DIR="${PROJECT_ROOT}/.tectonic"
TECTONIC_BIN="${TECTONIC_DIR}/tectonic"
TECTONIC_CACHE="${TECTONIC_DIR}/cache"

# ── Step 1: Install Python dependencies ──────────────────────
echo "──────────────────────────────────────────────────────────"
echo "Step 1/3: Installing Python dependencies"
echo "──────────────────────────────────────────────────────────"
pip install --upgrade pip
pip install -r requirements.txt

# ── Step 2: Install Tectonic (musl-static) ───────────────────
echo "──────────────────────────────────────────────────────────"
echo "Step 2/3: Installing Tectonic v${TECTONIC_VERSION} (musl-static)"
echo "──────────────────────────────────────────────────────────"

# Skip the download if a usable Tectonic is already on PATH.
# This makes the script idempotent and lets a future Render
# runtime image that ships tectonic system-wide skip the
# download entirely.
if command -v tectonic >/dev/null 2>&1 && tectonic --version >/dev/null 2>&1; then
    echo "Tectonic already present on PATH: $(command -v tectonic)"
    echo "  Version: $(tectonic --version 2>&1 | head -n 1)"
else
    echo "Downloading ${TECTONIC_URL} ..."
    if ! curl -fsSL -o "/tmp/${TECTONIC_ARCHIVE}" "${TECTONIC_URL}"; then
        echo "ERROR: Failed to download ${TECTONIC_URL}." >&2
        echo "       Check that Tectonic v${TECTONIC_VERSION} publishes a" >&2
        echo "       ${TECTONIC_TARGET} artifact at the URL above." >&2
        echo "       See: https://github.com/tectonic-typesetting/tectonic/releases" >&2
        exit 1
    fi

    # Sanity-check the archive size. Anything under 1 MB almost
    # certainly means we got an HTML error page from GitHub
    # instead of a real tarball — e.g. wrong version, missing
    # musl target, GitHub rate limit. Fail loudly here so the
    # cause is obvious in the build log.
    ARCHIVE_BYTES=$(stat -c %s "/tmp/${TECTONIC_ARCHIVE}" 2>/dev/null \
        || stat -f %z "/tmp/${TECTONIC_ARCHIVE}" 2>/dev/null \
        || echo 0)
    if [ "${ARCHIVE_BYTES}" -lt 1048576 ]; then
        echo "ERROR: Downloaded archive is only ${ARCHIVE_BYTES} bytes." >&2
        echo "       Expected ≥ 1 MB. The URL probably points to an" >&2
        echo "       HTML error page, or Tectonic v${TECTONIC_VERSION}" >&2
        echo "       does not publish a ${TECTONIC_TARGET} artifact." >&2
        echo "       Verify the release at:" >&2
        echo "         https://github.com/tectonic-typesetting/tectonic/releases" >&2
        exit 1
    fi

    echo "Extracting tectonic binary to /tmp ..."
    tar -xzf "/tmp/${TECTONIC_ARCHIVE}" -C /tmp tectonic

    if [ ! -f /tmp/tectonic ]; then
        echo "ERROR: tar extraction did not produce /tmp/tectonic." >&2
        echo "       Archive layout may have changed; check the release page." >&2
        exit 1
    fi

    chmod +x /tmp/tectonic

    # ── Pre-install functional check ──────────────────────────
    # Run the binary once to confirm it actually executes in
    # THIS environment. This catches GLIBC / libc mismatches,
    # missing linker deps, and corrupted downloads at BUILD
    # time — where the failure is visible in build logs —
    # rather than at first runtime request — where the user
    # sees a confusing 500 error.
    echo "Verifying tectonic --version runs in this environment ..."
    if ! /tmp/tectonic --version >/dev/null 2>&1; then
        echo "ERROR: /tmp/tectonic --version failed." >&2
        echo "       The binary is likely incompatible with this" >&2
        echo "       image (e.g. wrong libc target). Output:" >&2
        /tmp/tectonic --version >&2 || true
        exit 1
    fi

    # Install into .tectonic/ at the project root.
    echo "Installing tectonic to ${TECTONIC_BIN} ..."
    mkdir -p "${TECTONIC_DIR}"
    mv /tmp/tectonic "${TECTONIC_BIN}"

    # Clean up the downloaded archive.
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
# At runtime, services/pdf_service.py reads this same directory
# and sets TECTONIC_CACHE_DIR to it before invoking tectonic,
# so all runtime compiles hit the pre-warmed cache.
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
# any packages it doesn't yet have. This is the long step
# (potentially a couple of minutes on slow Render egress).
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
