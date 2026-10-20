# ──────────────────────────────────────────────────────────────
# Dockerfile — BuildR production image for Railway
# ──────────────────────────────────────────────────────────────
#
# Build stages:
#   1. tectonic-installer  — Downloads + verifies the Tectonic binary
#   2. app                 — Final slim image with Python + Tectonic
#
# WHY MULTI-STAGE?
# Keeps the final image lean: the curl / tar toolchain used to fetch
# Tectonic is not copied into the runtime layer.
# ──────────────────────────────────────────────────────────────

# ── Stage 1: Download Tectonic ────────────────────────────────
FROM debian:bookworm-slim AS tectonic-installer

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fetch the latest musl-static binary (works on any glibc version).
# The musl build is preferred over the glibc one because it has zero
# shared-library dependencies and is guaranteed to run on Debian/Ubuntu.
RUN curl -fsSL \
    "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.15.0/tectonic-0.15.0-x86_64-unknown-linux-musl.tar.gz" \
    | tar xz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/tectonic \
    && /usr/local/bin/tectonic --version

# ── Stage 2: Application image ────────────────────────────────
FROM python:3.11-slim

# Runtime deps for Tectonic (font config + SSL for package downloads)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libssl3 \
        libfontconfig1 \
        ca-certificates \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Copy Tectonic binary from stage 1
COPY --from=tectonic-installer /usr/local/bin/tectonic /usr/local/bin/tectonic

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Install Python dependencies first (layer caching — only re-runs
# when requirements.txt changes, not on every code change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create the storage directories that the app writes to at runtime.
# These are intentionally excluded from .gitignore so we create them here.
RUN mkdir -p storage/resumes storage/sessions storage/users storage/workspaces \
    && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Pre-warm the Tectonic package cache by compiling a minimal document.
# This runs at IMAGE BUILD time so the first real user request doesn't
# have to wait for Tectonic to download LaTeX packages over the network.
RUN echo '\documentclass{article}\begin{document}Hello\end{document}' \
    > /tmp/warmup.tex \
    && tectonic /tmp/warmup.tex --outdir /tmp \
    && rm -f /tmp/warmup.tex /tmp/warmup.pdf

# Railway injects $PORT at runtime; default to 8000 for local docker runs
ENV PORT=8000
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Gunicorn: 2 workers × 2 threads = 4 concurrent requests.
# Adjust --workers based on your Railway plan's vCPU count.
CMD gunicorn \
        --bind "0.0.0.0:${PORT}" \
        --workers 2 \
        --threads 2 \
        --timeout 180 \
        --log-level info \
        app:app
