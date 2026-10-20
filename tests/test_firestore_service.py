# ──────────────────────────────────────────────────────────────
# tests/test_firestore_service.py — Unit tests for Firestore service
# ──────────────────────────────────────────────────────────────

from unittest.mock import MagicMock, patch
import pytest

from services.firestore_service import (
    _py_to_firestore_value,
    _firestore_value_to_py,
    _py_to_firestore_document,
    _firestore_document_to_py,
    save_user_profile,
    load_user_profile,
    save_resume_variation,
    list_resume_variations,
    delete_resume_variation,
    update_resume_variation_label,
    save_session_draft,
    load_session_draft,
)


def test_firestore_value_conversions_roundtrip():
    """Verify that Python data structures convert to Firestore format and back."""
    data = {
        "name": "Jane Doe",
        "age": 28,
        "gpa": 3.85,
        "active": True,
        "notes": None,
        "skills": ["Python", "Flask", "LaTeX"],
        "nested": {
            "title": "Software Engineer",
            "experience_years": 4,
        },
    }

    doc = _py_to_firestore_document(data)
    assert "fields" in doc
    assert doc["fields"]["name"]["stringValue"] == "Jane Doe"
    assert doc["fields"]["age"]["integerValue"] == "28"
    assert doc["fields"]["gpa"]["doubleValue"] == 3.85
    assert doc["fields"]["active"]["booleanValue"] is True
    assert doc["fields"]["notes"]["nullValue"] is None
    assert len(doc["fields"]["skills"]["arrayValue"]["values"]) == 3

    recovered = _firestore_document_to_py(doc)
    assert recovered == data


def test_save_user_profile_admin_sdk():
    """Test saving user profile using mock Admin SDK client."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc

    with patch("services.firestore_service._get_admin_db", return_value=mock_db):
        profile_data = {"personal_info": {"name": "Alice"}}
        result = save_user_profile("user-123", profile_data)
        assert result is True
        mock_db.collection.assert_called_with("users")
        mock_db.collection().document.assert_called_with("user-123")
        mock_doc.set.assert_called_once()
        args, kwargs = mock_doc.set.call_args
        assert args[0]["user_id"] == "user-123"
        assert args[0]["profile"] == profile_data
        assert kwargs.get("merge") is True


def test_load_user_profile_admin_sdk():
    """Test loading user profile using mock Admin SDK client."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_snapshot = MagicMock()
    mock_snapshot.exists = True
    mock_snapshot.to_dict.return_value = {
        "user_id": "user-123",
        "profile": {"personal_info": {"name": "Bob"}},
    }
    mock_doc.get.return_value = mock_snapshot
    mock_db.collection.return_value.document.return_value = mock_doc

    with patch("services.firestore_service._get_admin_db", return_value=mock_db):
        profile = load_user_profile("user-123")
        assert profile == {"personal_info": {"name": "Bob"}}


def test_save_user_profile_rest_api():
    """Test saving user profile via REST API when Admin SDK is unavailable."""
    with patch("services.firestore_service._get_admin_db", return_value=None):
        with patch("services.firestore_service.requests.patch") as mock_patch:
            mock_patch.return_value.status_code = 200
            result = save_user_profile("user-456", {"personal_info": {"name": "Charlie"}}, id_token="test-token")
            assert result is True
            assert mock_patch.called
            url = mock_patch.call_args[0][0]
            assert "users/user-456" in url


def test_save_resume_variation_no_pdf():
    """Verify resume variations store metadata and LaTeX text but NO pdf bytes."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = mock_doc

    with patch("services.firestore_service._get_admin_db", return_value=mock_db):
        metadata = {
            "type": "tailored",
            "label": "Google SWE",
            "job_description": "Exciting role",
        }
        tex_string = "\\documentclass{article}\\begin{document}Resume\\end{document}"
        result = save_resume_variation("user-123", "resume-abc", metadata, tex_string)
        assert result is True

        args, kwargs = mock_doc.set.call_args
        saved_dict = args[0]
        assert saved_dict["id"] == "resume-abc"
        assert saved_dict["type"] == "tailored"
        assert saved_dict["label"] == "Google SWE"
        assert saved_dict["job_description"] == "Exciting role"
        assert saved_dict["tex"] == tex_string
        # Strictly verify no PDF or binary content exists in Firestore document
        assert "pdf" not in saved_dict
        assert "pdf_bytes" not in saved_dict


def test_delete_resume_variation():
    """Verify deleting a resume variation calls document.delete()."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = mock_doc

    with patch("services.firestore_service._get_admin_db", return_value=mock_db):
        result = delete_resume_variation("user-123", "resume-abc")
        assert result is True
        mock_doc.delete.assert_called_once()


def test_save_session_draft():
    """Verify saving session draft saves draft profile, metadata, and context."""
    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value = mock_doc

    with patch("services.firestore_service._get_admin_db", return_value=mock_db):
        draft_data = {"personal_info": {"name": "Dave"}}
        meta = {"suggestions": ["Improve verbs"]}
        job_ctx = {"job_description": "Backend Engineer"}
        result = save_session_draft("user-123", "session-xyz", draft_data, meta, job_ctx)
        assert result is True
        args, kwargs = mock_doc.set.call_args
        saved = args[0]
        assert saved["session_id"] == "session-xyz"
        assert saved["draft_profile"] == draft_data
        assert saved["metadata"] == meta
        assert saved["job_context"] == job_ctx
