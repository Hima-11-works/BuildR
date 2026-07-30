"""Tests for services/session_service.py — path safety, roundtrips, cleanup.

All public functions now take a user_id as the first argument so that
tailoring scratch space is scoped per-user. Tests pass a constant
"test-user" id throughout.
"""
import os
import time

import pytest

from models.profile import Profile, PersonalInfo


TEST_USER = "test-user"


class TestGetSessionDir:
    def test_does_not_create_directory(self, isolated_session_service):
        path = isolated_session_service.get_session_dir(TEST_USER, "session_20260101_120000_abc123")
        assert not path.exists()

    @pytest.mark.parametrize("bad_id", [".", "", "   ", "..", "///"])
    def test_rejects_ids_that_sanitize_to_nothing(self, isolated_session_service, bad_id):
        # ".", "", "   ", "..", "///" all sanitize (alnum + "-"/"_" only,
        # see get_session_dir) down to an empty string, which used to
        # collapse to the user's sessions dir itself — see AUDIT.md follow-up
        # fix alongside 4.2.
        with pytest.raises(ValueError):
            isolated_session_service.get_session_dir(TEST_USER, bad_id)

    def test_traversal_characters_are_stripped_not_rejected(self, isolated_session_service):
        # Unlike resume_library's resolve-and-verify approach, session IDs
        # are sanitized by character allow-list: only alnum/-/_ survive, so
        # "../../etc" simply becomes the harmless literal id "etc" — it
        # can never escape the user's sessions dir, so this correctly does
        # NOT raise.
        ss = isolated_session_service
        path = ss.get_session_dir(TEST_USER, "../../etc")
        assert path == ss.user_sessions_dir(TEST_USER) / "etc"

    def test_legit_id_resolves_under_user_sessions_dir(self, isolated_session_service):
        ss = isolated_session_service
        path = ss.get_session_dir(TEST_USER, "session_20260101_120000_abc123")
        assert path.parent == ss.user_sessions_dir(TEST_USER)
        assert path.name == "session_20260101_120000_abc123"


class TestSessionRoundtrip:
    def test_create_update_load_draft(self, isolated_session_service):
        ss = isolated_session_service
        profile = Profile(personal_info=PersonalInfo(name="Jane", email="jane@example.com"))
        session_id = ss.create_session(TEST_USER, profile, {"job_description": "A job", "job_url": "", "preferences": {}, "contact_info": {}})

        assert ss.get_session_dir(TEST_USER, session_id).exists()

        updated_profile = Profile(personal_info=PersonalInfo(name="Jane Updated", email="jane@example.com"))
        ss.update_draft(TEST_USER, session_id, updated_profile, {"suggestions": ["did a thing"]})

        loaded_profile, metadata = ss.load_draft(TEST_USER, session_id)
        assert loaded_profile.personal_info.name == "Jane Updated"
        assert metadata == {"suggestions": ["did a thing"]}

    def test_load_missing_session_raises(self, isolated_session_service):
        with pytest.raises(FileNotFoundError):
            isolated_session_service.load_draft(TEST_USER, "session_does_not_exist_000000_abcdef")

    def test_snapshot_save_and_restore(self, isolated_session_service):
        ss = isolated_session_service
        profile = Profile(personal_info=PersonalInfo(name="Jane", email="jane@example.com"))
        session_id = ss.create_session(TEST_USER, profile, {"job_description": "A job"})
        ss.update_draft(TEST_USER, session_id, profile, {"v": 1})

        snapshot_id = ss.save_snapshot(TEST_USER, session_id, "First Draft")
        snapshots = ss.list_snapshots(TEST_USER, session_id)
        assert len(snapshots) == 1
        assert snapshots[0]["id"] == snapshot_id
        assert snapshots[0]["name"] == "First Draft"

        changed_profile = Profile(personal_info=PersonalInfo(name="Changed", email="jane@example.com"))
        ss.update_draft(TEST_USER, session_id, changed_profile, {"v": 2})

        ss.restore_snapshot(TEST_USER, session_id, snapshot_id)
        restored_profile, restored_meta = ss.load_draft(TEST_USER, session_id)
        assert restored_profile.personal_info.name == "Jane"
        assert restored_meta == {"v": 1}


class TestCleanupExpiredSessions:
    def test_fresh_session_survives(self, isolated_session_service):
        ss = isolated_session_service
        profile = Profile(personal_info=PersonalInfo(name="Jane", email="jane@example.com"))
        ss.create_session(TEST_USER, profile, {"job_description": "A job"})

        removed = ss.cleanup_expired_sessions(TEST_USER, max_age_seconds=999999)
        assert removed == 0

    def test_aged_out_session_is_removed(self, isolated_session_service):
        ss = isolated_session_service
        profile = Profile(personal_info=PersonalInfo(name="Jane", email="jane@example.com"))
        session_id = ss.create_session(TEST_USER, profile, {"job_description": "A job"})

        old_time = time.time() - 999999
        for f in ss.get_session_dir(TEST_USER, session_id).rglob("*"):
            if f.is_file():
                os.utime(f, (old_time, old_time))

        removed = ss.cleanup_expired_sessions(TEST_USER, max_age_seconds=1000)
        assert removed == 1
        assert not ss.get_session_dir(TEST_USER, session_id).exists()

    def test_no_sessions_dir_is_a_noop(self, isolated_session_service):
        assert isolated_session_service.cleanup_expired_sessions(TEST_USER) == 0
