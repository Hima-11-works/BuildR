# ──────────────────────────────────────────────────────────────
# services/firestore_service.py — Firestore persistence layer
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Manages storing user profile details (master resume data) and
# AI resume variations (tailored resumes, LaTeX text, metadata)
# in Google Cloud Firestore (Firebase).
#
# IMPORTANT:
#   • As required, PDFs are NEVER stored in Firestore.
#     PDF files stay on local disk storage.
#   • Data values (JSON schemas, LaTeX strings, job contexts,
#     and variation metadata) are persisted to Firestore.
#
# AUTHENTICATION & CONNECTIVITY
# -----------------------------
# Supports two execution paths automatically:
#   1. Firebase Admin SDK:
#      Used when a Service Account Key is configured
#      (via GOOGLE_APPLICATION_CREDENTIALS, FIREBASE_SERVICE_ACCOUNT_KEY,
#      or a serviceAccountKey.json file in the project root).
#   2. Firestore REST API (Bearer ID token):
#      Used when running in client-credential mode with the user's
#      Google Auth Firebase ID token.
#   3. Resilient fallback:
#      If Firestore is temporarily unreachable or misconfigured,
#      operations fail gracefully and log a warning, allowing the
#      local filesystem cache to serve the user without interruption.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Project root directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Cache for Admin SDK firestore client
_firestore_admin_client = None
_admin_client_checked = False


def _get_admin_db():
    """
    Attempt to obtain a firebase_admin.firestore client if credentials exist.
    """
    global _firestore_admin_client, _admin_client_checked
    if _admin_client_checked:
        return _firestore_admin_client

    _admin_client_checked = True
    try:
        import firebase_admin
        from firebase_admin import firestore

        app = None
        try:
            app = firebase_admin.get_app()
        except ValueError:
            pass

        if app is not None:
            # Check if app has valid credentials for GCP
            _firestore_admin_client = firestore.client(app=app)
            logger.info("Firestore: Connected via Firebase Admin SDK.")
            return _firestore_admin_client
    except Exception as exc:
        logger.debug("Firestore: Admin SDK client unavailable (%s). Will use REST API if token available.", exc)
        _firestore_admin_client = None

    return _firestore_admin_client


# ── Helpers for Firestore REST API ────────────────────────────

def _py_to_firestore_value(val: Any) -> dict[str, Any]:
    """Convert a Python native value to a Firestore REST API typed value."""
    if val is None:
        return {"nullValue": None}
    if isinstance(val, bool):
        return {"booleanValue": val}
    if isinstance(val, int):
        return {"integerValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": val}
    if isinstance(val, str):
        return {"stringValue": val}
    if isinstance(val, dict):
        fields = {k: _py_to_firestore_value(v) for k, v in val.items()}
        return {"mapValue": {"fields": fields}}
    if isinstance(val, (list, tuple, set)):
        values = [_py_to_firestore_value(item) for item in val]
        return {"arrayValue": {"values": values}}
    return {"stringValue": str(val)}


def _firestore_value_to_py(val: dict[str, Any]) -> Any:
    """Convert a Firestore REST API typed value back to a Python native value."""
    if not isinstance(val, dict):
        return val
    if "nullValue" in val:
        return None
    if "booleanValue" in val:
        return val["booleanValue"]
    if "integerValue" in val:
        return int(val["integerValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "stringValue" in val:
        return val["stringValue"]
    if "timestampValue" in val:
        return val["timestampValue"]
    if "mapValue" in val:
        fields = val["mapValue"].get("fields", {})
        return {k: _firestore_value_to_py(v) for k, v in fields.items()}
    if "arrayValue" in val:
        values = val["arrayValue"].get("values", [])
        return [_firestore_value_to_py(item) for item in values]
    return None


def _py_to_firestore_document(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a flat/nested Python dict to Firestore REST document fields."""
    return {"fields": {k: _py_to_firestore_value(v) for k, v in data.items()}}


def _firestore_document_to_py(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a Firestore REST document to a Python dictionary."""
    fields = doc.get("fields", {})
    return {k: _firestore_value_to_py(v) for k, v in fields.items()}


def _get_current_id_token() -> Optional[str]:
    """Retrieve the current Firebase ID token from the Flask session if inside a request."""
    try:
        from flask import session, has_request_context
        if has_request_context():
            return session.get("id_token")
    except Exception:
        pass
    return None


def _get_rest_headers(id_token: Optional[str] = None) -> dict[str, str]:
    token = id_token or _get_current_id_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_project_id() -> str:
    return os.getenv("FIREBASE_PROJECT_ID", "buildr-b5cd8")


def _get_rest_base_url() -> str:
    project_id = _get_project_id()
    return f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents"


# ══════════════════════════════════════════════════════════════
# MASTER PROFILE / USER DETAILS
# ══════════════════════════════════════════════════════════════

def save_user_profile(
    user_id: str,
    profile_data: dict[str, Any],
    id_token: Optional[str] = None,
) -> bool:
    """
    Save the user's master profile details to Firestore under `users/{user_id}`.

    Parameters
    ----------
    user_id : str
        The authenticated user's ID.
    profile_data : dict
        The serialized Profile dictionary.
    id_token : str, optional
        User's Firebase ID token for REST authorization if Admin SDK is unavailable.

    Returns
    -------
    bool
        True if successfully saved to Firestore, False otherwise.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc_data = {
        "user_id": user_id,
        "profile": profile_data,
        "updated_at": now_iso,
    }

    # 1. Try Firebase Admin SDK first
    db = _get_admin_db()
    if db:
        try:
            doc_ref = db.collection("users").document(user_id)
            doc_ref.set(doc_data, merge=True)
            logger.info("Firestore: Successfully saved master profile for user %s via Admin SDK.", user_id)
            return True
        except Exception as exc:
            logger.warning("Firestore Admin SDK save failed for user %s: %s", user_id, exc)

    # 2. Try Firestore REST API
    try:
        url = f"{_get_rest_base_url()}/users/{user_id}"
        payload = _py_to_firestore_document(doc_data)
        # In Firestore REST API, PATCH creates or updates the document
        res = requests.patch(
            url,
            json=payload,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        if res.status_code in (200, 201):
            logger.info("Firestore: Successfully saved master profile for user %s via REST API.", user_id)
            return True
        elif res.status_code == 403:
            logger.warning(
                "Firestore returned HTTP 403 (PERMISSION_DENIED) for user %s. "
                "Because your Firestore database is in Production Mode, please either update your "
                "Firestore Rules in the Firebase Console (allow read, write: if request.auth != null;) "
                "or place a serviceAccountKey.json file in your project root. Falling back to local storage.",
                user_id,
            )
        else:
            logger.warning(
                "Firestore REST API save profile failed: HTTP %s - %s",
                res.status_code,
                res.text[:200],
            )
    except Exception as exc:
        logger.warning("Firestore REST API connection failed: %s", exc)

    return False


def load_user_profile(
    user_id: str,
    id_token: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Load the user's master profile details from Firestore under `users/{user_id}`.

    Returns
    -------
    dict or None
        The profile data dictionary, or None if not found / Firestore unavailable.
    """
    # 1. Try Firebase Admin SDK first
    db = _get_admin_db()
    if db:
        try:
            doc_ref = db.collection("users").document(user_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                return data.get("profile")
        except Exception as exc:
            logger.warning("Firestore Admin SDK load profile failed for user %s: %s", user_id, exc)

    # 2. Try Firestore REST API
    try:
        url = f"{_get_rest_base_url()}/users/{user_id}"
        res = requests.get(
            url,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        if res.status_code == 200:
            doc_data = _firestore_document_to_py(res.json())
            return doc_data.get("profile")
        elif res.status_code == 404:
            return None
        elif res.status_code == 403:
            logger.warning(
                "Firestore returned HTTP 403 (PERMISSION_DENIED) while loading profile for user %s. "
                "Update Firestore Rules in Firebase Console or add serviceAccountKey.json. Falling back to local storage.",
                user_id,
            )
            return None
        logger.warning(
            "Firestore REST API load profile returned HTTP %s: %s",
            res.status_code,
            res.text[:200],
        )
    except Exception as exc:
        logger.warning("Firestore REST API load failed: %s", exc)

    return None


# ══════════════════════════════════════════════════════════════
# RESUME VARIATIONS (MASTER & AI VARIATIONS)
# ══════════════════════════════════════════════════════════════

def save_resume_variation(
    user_id: str,
    resume_id: str,
    metadata: dict[str, Any],
    tex_string: str,
    id_token: Optional[str] = None,
) -> bool:
    """
    Save a resume variation's data values to Firestore under:
    `users/{user_id}/resumes/{resume_id}`.

    Stores:
      • id, type ("master" or "tailored")
      • label, date, job_description
      • tex (LaTeX text content)
      • NO PDF files or binary data are saved here.

    Parameters
    ----------
    user_id : str
        Authenticated user ID.
    resume_id : str
        Unique resume identifier (folder/document ID).
    metadata : dict
        Resume metadata dictionary.
    tex_string : str
        LaTeX source text of this variation.
    id_token : str, optional
        Auth token for REST API.

    Returns
    -------
    bool
        True if successfully saved to Firestore.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc_data = {
        "id": resume_id,
        "type": metadata.get("type", "tailored"),
        "label": metadata.get("label", "Resume"),
        "date": metadata.get("date", now_iso),
        "job_description": metadata.get("job_description"),
        "tex": tex_string,
        "user_id": user_id,
        "updated_at": now_iso,
    }

    # 1. Admin SDK
    db = _get_admin_db()
    if db:
        try:
            ref = db.collection("users").document(user_id).collection("resumes").document(resume_id)
            ref.set(doc_data, merge=True)
            logger.info("Firestore: Saved resume variation '%s' via Admin SDK.", resume_id)
            return True
        except Exception as exc:
            logger.warning("Firestore Admin SDK save variation failed: %s", exc)

    # 2. REST API
    try:
        url = f"{_get_rest_base_url()}/users/{user_id}/resumes/{resume_id}"
        payload = _py_to_firestore_document(doc_data)
        res = requests.patch(
            url,
            json=payload,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        if res.status_code in (200, 201):
            logger.info("Firestore: Saved resume variation '%s' via REST API.", resume_id)
            return True
        logger.warning(
            "Firestore REST API save variation returned HTTP %s: %s",
            res.status_code,
            res.text[:200],
        )
    except Exception as exc:
        logger.warning("Firestore REST API save variation failed: %s", exc)

    return False


def list_resume_variations(
    user_id: str,
    id_token: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    List resume variations stored in Firestore under `users/{user_id}/resumes`.

    Returns
    -------
    list of dict
        Metadata for all variations found in Firestore.
    """
    variations = []

    # 1. Admin SDK
    db = _get_admin_db()
    if db:
        try:
            docs = db.collection("users").document(user_id).collection("resumes").stream()
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                variations.append(data)
            variations.sort(key=lambda r: r.get("date", ""), reverse=True)
            return variations
        except Exception as exc:
            logger.warning("Firestore Admin SDK list variations failed: %s", exc)

    # 2. REST API
    try:
        url = f"{_get_rest_base_url()}/users/{user_id}/resumes"
        res = requests.get(
            url,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        if res.status_code == 200:
            data = res.json()
            for doc in data.get("documents", []):
                doc_py = _firestore_document_to_py(doc)
                name = doc.get("name", "")
                doc_id = name.split("/")[-1] if name else doc_py.get("id", "")
                doc_py["id"] = doc_id
                variations.append(doc_py)
            variations.sort(key=lambda r: r.get("date", ""), reverse=True)
            return variations
    except Exception as exc:
        logger.warning("Firestore REST API list variations failed: %s", exc)

    return variations


def delete_resume_variation(
    user_id: str,
    resume_id: str,
    id_token: Optional[str] = None,
) -> bool:
    """
    Delete a resume variation document from Firestore.
    """
    # 1. Admin SDK
    db = _get_admin_db()
    if db:
        try:
            db.collection("users").document(user_id).collection("resumes").document(resume_id).delete()
            return True
        except Exception as exc:
            logger.warning("Firestore Admin SDK delete variation failed: %s", exc)

    # 2. REST API
    try:
        url = f"{_get_rest_base_url()}/users/{user_id}/resumes/{resume_id}"
        res = requests.delete(
            url,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        if res.status_code in (200, 204, 404):
            return True
    except Exception as exc:
        logger.warning("Firestore REST API delete variation failed: %s", exc)

    return False


def update_resume_variation_label(
    user_id: str,
    resume_id: str,
    new_label: str,
    id_token: Optional[str] = None,
) -> bool:
    """
    Update the label of a resume variation in Firestore.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # 1. Admin SDK
    db = _get_admin_db()
    if db:
        try:
            ref = db.collection("users").document(user_id).collection("resumes").document(resume_id)
            ref.update({"label": new_label, "updated_at": now_iso})
            return True
        except Exception as exc:
            logger.warning("Firestore Admin SDK update label failed: %s", exc)

    # 2. REST API
    try:
        url = f"{_get_rest_base_url()}/users/{user_id}/resumes/{resume_id}"
        payload = _py_to_firestore_document({"label": new_label, "updated_at": now_iso})
        res = requests.patch(
            url,
            json=payload,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", ""), "updateMask.fieldPaths": ["label", "updated_at"]},
            timeout=8,
        )
        if res.status_code == 200:
            return True
    except Exception as exc:
        logger.warning("Firestore REST API update label failed: %s", exc)

    return False


# ══════════════════════════════════════════════════════════════
# TAILORING SESSIONS / DRAFTS
# ══════════════════════════════════════════════════════════════

def save_session_draft(
    user_id: str,
    session_id: str,
    draft_data: dict[str, Any],
    metadata: dict[str, Any],
    job_context: Optional[dict[str, Any]] = None,
    id_token: Optional[str] = None,
) -> bool:
    """
    Save active AI tailoring session draft and metadata to Firestore under:
    `users/{user_id}/sessions/{session_id}`.
    """
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc_data: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "draft_profile": draft_data,
        "metadata": metadata,
        "updated_at": now_iso,
    }
    if job_context:
        doc_data["job_context"] = job_context

    db = _get_admin_db()
    if db:
        try:
            ref = db.collection("users").document(user_id).collection("sessions").document(session_id)
            ref.set(doc_data, merge=True)
            return True
        except Exception as exc:
            logger.warning("Firestore Admin SDK save session failed: %s", exc)

    try:
        url = f"{_get_rest_base_url()}/users/{user_id}/sessions/{session_id}"
        payload = _py_to_firestore_document(doc_data)
        res = requests.patch(
            url,
            json=payload,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        return res.status_code in (200, 201)
    except Exception as exc:
        logger.warning("Firestore REST API save session failed: %s", exc)

    return False


def load_session_draft(
    user_id: str,
    session_id: str,
    id_token: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Load active AI tailoring session draft from Firestore.
    """
    db = _get_admin_db()
    if db:
        try:
            doc = db.collection("users").document(user_id).collection("sessions").document(session_id).get()
            if doc.exists:
                return doc.to_dict()
        except Exception as exc:
            logger.warning("Firestore Admin SDK load session failed: %s", exc)

    try:
        url = f"{_get_rest_base_url()}/users/{user_id}/sessions/{session_id}"
        res = requests.get(
            url,
            headers=_get_rest_headers(id_token),
            params={"key": os.getenv("FIREBASE_API_KEY", "")},
            timeout=8,
        )
        if res.status_code == 200:
            return _firestore_document_to_py(res.json())
    except Exception as exc:
        logger.warning("Firestore REST API load session failed: %s", exc)

    return None
