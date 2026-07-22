from __future__ import annotations

import json
import time
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from models.profile import Profile

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = _PROJECT_ROOT / "storage" / "sessions"

# Tailoring sessions are short-lived scratch space for the AI workspace —
# nothing here is a permanent record (finished resumes get copied into the
# Resume Library by save_resume()). Anything untouched this long is an
# abandoned session and safe to delete. See cleanup_expired_sessions().
_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 days


def get_session_dir(session_id: str) -> Path:
    """
    Resolve a session ID to its directory, preventing path traversal.

    Does NOT create the directory. Read paths (load_draft, load_job_context,
    etc.) rely on this: looking up a bogus or expired session ID should
    raise FileNotFoundError, not silently create an empty orphan directory
    under storage/sessions/. Write paths that need the directory to exist
    (create_session, update_draft, save_snapshot, restore_snapshot) create
    it explicitly at their own call sites.

    Raises
    ------
    ValueError
        If the ID sanitizes to nothing (e.g. "", ".", "///") — without
        this guard it would collapse to SESSIONS_DIR itself, and any
        future write/delete keyed on that "session id" would silently
        operate on the shared sessions directory instead of a single
        session's folder.
    """
    # Path safety sanitization: only alnum, "-", "_" survive, so this can
    # never traverse outside SESSIONS_DIR regardless of what's stripped.
    clean_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
    if not clean_id:
        raise ValueError("Invalid session ID.")
    return SESSIONS_DIR / clean_id


def cleanup_expired_sessions(max_age_seconds: int = _SESSION_MAX_AGE_SECONDS) -> int:
    """
    Delete session directories that haven't been touched in over
    max_age_seconds. Intended to be run once at app startup so
    storage/sessions/ doesn't grow without bound across restarts.

    "Touched" is the most recent mtime among any file anywhere in the
    session tree (not just the top-level folder), since ongoing edits
    only update files inside draft/ or snapshots/, not the parent dir.

    Returns the number of sessions removed.
    """
    if not SESSIONS_DIR.exists():
        return 0

    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in SESSIONS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            files = [f for f in entry.rglob("*") if f.is_file()]
            last_touched = max((f.stat().st_mtime for f in files), default=entry.stat().st_mtime)
        except OSError:
            continue
        if last_touched < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


def create_session(master_profile: Profile, job_context: dict[str, Any]) -> str:
    """
    Start a tailoring session. Generates a session ID and copies profile / context reference files.
    """
    session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    # Save master profile reference
    with open(session_dir / "master_profile.json", "w", encoding="utf-8") as f:
        f.write(master_profile.model_dump_json(indent=2))
        
    # Save job description and preferences context
    with open(session_dir / "job_context.json", "w", encoding="utf-8") as f:
        json.dump(job_context, f, indent=2, ensure_ascii=False)
        
    # Initialize empty chat history
    with open(session_dir / "chat_history.json", "w", encoding="utf-8") as f:
        json.dump([], f, indent=2)
        
    # Create draft directory
    (session_dir / "draft").mkdir(exist_ok=True)
    
    return session_id

def load_master_profile(session_id: str) -> Profile:
    """Load the original master profile for reference."""
    session_dir = get_session_dir(session_id)
    with open(session_dir / "master_profile.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return Profile.model_validate(data)

def load_job_context(session_id: str) -> dict[str, Any]:
    """Load session context (job description, target level, etc.)."""
    session_dir = get_session_dir(session_id)
    with open(session_dir / "job_context.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_chat_history(session_id: str) -> list[dict[str, str]]:
    """Load chat logs."""
    session_dir = get_session_dir(session_id)
    chat_file = session_dir / "chat_history.json"
    if not chat_file.exists():
        return []
    with open(chat_file, "r", encoding="utf-8") as f:
        return json.load(f)

def save_chat_history(session_id: str, chat_history: list[dict[str, str]]) -> None:
    """Save chat logs."""
    session_dir = get_session_dir(session_id)
    with open(session_dir / "chat_history.json", "w", encoding="utf-8") as f:
        json.dump(chat_history, f, indent=2, ensure_ascii=False)

def update_draft(session_id: str, profile: Profile, metadata: dict[str, Any]) -> None:
    """
    Overwrites the active draft profile and metadata.
    """
    session_dir = get_session_dir(session_id)
    draft_dir = session_dir / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)

    with open(draft_dir / "profile.json", "w", encoding="utf-8") as f:
        f.write(profile.model_dump_json(indent=2))
        
    with open(draft_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

def load_draft(session_id: str) -> tuple[Profile, dict[str, Any]]:
    """Load the current active draft profile and metadata."""
    session_dir = get_session_dir(session_id)
    draft_dir = session_dir / "draft"
    
    with open(draft_dir / "profile.json", "r", encoding="utf-8") as f:
        profile_data = json.load(f)
    with open(draft_dir / "metadata.json", "r", encoding="utf-8") as f:
        metadata = json.load(f)
        
    return Profile.model_validate(profile_data), metadata

def save_snapshot(session_id: str, snapshot_name: str) -> str:
    """
    Saves a copy of the current draft profile and metadata as a named snapshot.
    Returns the snapshot ID.
    """
    session_dir = get_session_dir(session_id)
    snapshot_id = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    
    snapshots_dir = session_dir / "snapshots" / snapshot_id
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy files from draft to snapshot
    draft_dir = session_dir / "draft"
    shutil.copy2(draft_dir / "profile.json", snapshots_dir / "profile.json")
    shutil.copy2(draft_dir / "metadata.json", snapshots_dir / "metadata.json")
    
    # Store snapshot label metadata
    snap_meta = {
        "id": snapshot_id,
        "name": snapshot_name,
        "timestamp": datetime.now().isoformat()
    }
    with open(snapshots_dir / "snapshot_meta.json", "w", encoding="utf-8") as f:
        json.dump(snap_meta, f, indent=2, ensure_ascii=False)
        
    return snapshot_id

def restore_snapshot(session_id: str, snapshot_id: str) -> None:
    """
    Overwrites the current draft with the snapshot files.
    """
    session_dir = get_session_dir(session_id)
    snapshot_dir = session_dir / "snapshots" / snapshot_id
    
    draft_dir = session_dir / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(snapshot_dir / "profile.json", draft_dir / "profile.json")
    shutil.copy2(snapshot_dir / "metadata.json", draft_dir / "metadata.json")

def list_snapshots(session_id: str) -> list[dict[str, Any]]:
    """List all saved snapshots with metadata."""
    session_dir = get_session_dir(session_id)
    snaps_base = session_dir / "snapshots"
    if not snaps_base.exists():
        return []
        
    snapshots = []
    for child in snaps_base.iterdir():
        if child.is_dir():
            meta_file = child / "snapshot_meta.json"
            if meta_file.exists():
                with open(meta_file, "r", encoding="utf-8") as f:
                    snapshots.append(json.load(f))
                    
    # Sort newest first
    snapshots.sort(key=lambda x: x["timestamp"], reverse=True)
    return snapshots
