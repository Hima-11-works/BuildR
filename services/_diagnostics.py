# ──────────────────────────────────────────────────────────────
# services/_diagnostics.py — Memory + latency instrumentation
# ──────────────────────────────────────────────────────────────
#
# WHY THIS EXISTS
# ---------------
# Render free plan has a 512 MB per-container RSS cap, and the worker
# is being OOM-killed inside POST /api/profile/parse before any AI
# tailoring begins. We need detailed per-step logs that show:
#
#   • current RSS memory at the start AND end of each step
#   • elapsed time for each step
#   • step-specific metrics (file size, extracted text length,
#     number of PDF pages, Gemini request payload size, Gemini
#     response size, validation time)
#
# The leading "[parse-trace]" tag makes these lines easy to grep out
# of Render's log stream.
#
# NOT OPTIMIZING ANYTHING
# -----------------------
# This module ONLY logs. It does not change any behavior. Every step
# runs the same code as before; we just observe memory and timing
# around it.
#
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import contextlib
import logging
import os
import time
from typing import Any, Iterator, Optional


# ── Cross-platform RSS reading ──────────────────────────────────
# Try psutil first (works on Linux, macOS, Windows — Render uses
# Linux). Fall back to reading /proc/self/status on Linux, then to
# `None` (we'll log "n/a" for RSS).
try:
    import psutil
    _PROCESS = psutil.Process(os.getpid())
except Exception:  # noqa: BLE001 — psutil missing or Process() failed
    _PROCESS = None


def _rss_mb() -> Optional[float]:
    """Return current resident set size in MB, or None if unavailable."""
    if _PROCESS is None:
        return None
    try:
        return _PROCESS.memory_info().rss / 1024 / 1024
    except Exception:  # noqa: BLE001 — never let tracing crash the request
        return None


# ── Public API ──────────────────────────────────────────────────
_LOGGER = logging.getLogger("buildr.parse_trace")


@contextlib.contextmanager
def instrument_step(
    name: str,
    **metrics: Any,
) -> Iterator[None]:
    """
    Context manager that logs memory + elapsed time around a pipeline step.

    Usage
    -----
        with instrument_step("PDF text extraction", file_size_bytes=len(data)):
            text = extract_text_from_pdf(stream)

    On entry:  logs `[parse-trace] START <name> | rss=<MB>MB | <key=value>...`
    On exit:   logs `[parse-trace] END   <name> | elapsed=<ms>ms | rss=<MB>MB delta=<±MB>MB`

    The metrics you pass in are emitted at START. Use them to label
    what the step is operating on (file_size_bytes, text_length, etc.).
    """
    start_time = time.monotonic()
    rss_before = _rss_mb()
    metrics_str = " ".join(f"{k}={v}" for k, v in metrics.items()) if metrics else ""
    rss_before_str = f"{rss_before:.2f}MB" if rss_before is not None else "n/a"
    _LOGGER.info(
        "[parse-trace] START %-40s | rss=%s | %s",
        name, rss_before_str, metrics_str,
    )
    try:
        yield
    finally:
        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        rss_after = _rss_mb()
        if rss_before is None or rss_after is None:
            rss_after_str = "n/a"
            delta_str = "n/a"
        else:
            rss_after_str = f"{rss_after:.2f}MB"
            delta_str = f"{rss_after - rss_before:+.2f}MB"
        _LOGGER.info(
            "[parse-trace] END   %-40s | elapsed=%.1fms | rss=%s delta=%s",
            name, elapsed_ms, rss_after_str, delta_str,
        )


def log_event(name: str, **metrics: Any) -> None:
    """
    One-shot log line that records a single event (not a step with
    duration). Use for things like "PDF has N pages" or "Gemini
    response was 47KB".

    Format: `[parse-trace] EVENT <name> | <key=value>...`
    """
    metrics_str = " ".join(f"{k}={v}" for k, v in metrics.items()) if metrics else ""
    rss_str = _rss_mb()
    rss_str = f"{rss_str:.2f}MB" if rss_str is not None else "n/a"
    _LOGGER.info(
        "[parse-trace] EVENT %-40s | rss=%s | %s",
        name, rss_str, metrics_str,
    )