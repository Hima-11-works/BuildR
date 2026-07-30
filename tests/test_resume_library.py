"""Tests for services/resume_library.py — storage, listing, and path safety.

All public functions take a user_id as their first argument. Tests use a
constant "test-user" id throughout, plus a second "other-user" id in the
isolation test to confirm cross-user scoping.
"""
import time

import pytest


TEST_USER = "test-user"
OTHER_USER = "other-user"


class TestValidateResumeId:
    def test_rejects_empty_and_blank(self, isolated_resume_library):
        with pytest.raises(ValueError):
            isolated_resume_library._validate_resume_id(TEST_USER, "")
        with pytest.raises(ValueError):
            isolated_resume_library._validate_resume_id(TEST_USER, "   ")

    @pytest.mark.parametrize("bad_id", [".", "..", "../../etc", "../secrets"])
    def test_rejects_path_traversal(self, isolated_resume_library, bad_id):
        # Regression test: "." used to resolve to RESUMES_DIR itself, so
        # delete_resume(".") would rmtree() the entire library (AUDIT.md 4.2).
        with pytest.raises(ValueError):
            isolated_resume_library._validate_resume_id(TEST_USER, bad_id)

    def test_dot_does_not_resolve_to_base_dir(self, isolated_resume_library):
        rl = isolated_resume_library
        rl.user_resumes_dir(TEST_USER).mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError):
            rl._validate_resume_id(TEST_USER, ".")

    def test_nonexistent_legit_id_raises_not_found(self, isolated_resume_library):
        isolated_resume_library.user_resumes_dir(TEST_USER).mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError):
            isolated_resume_library._validate_resume_id(TEST_USER, "20260101-000000_never-existed")


@pytest.fixture
def compiled_pdf_path(tmp_path):
    """A fake .pdf file standing in for a real Tectonic compile output."""
    pdf_path = tmp_path / "output" / "master_resume.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")
    return pdf_path


class TestSaveListGetDelete:
    def test_save_and_list_roundtrip(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        resume_id = rl.save_resume(
            TEST_USER, tex_string="\\documentclass{article}", pdf_path=compiled_pdf_path,
            resume_type="master", label="Master",
        )
        resumes = rl.list_resumes(TEST_USER)
        assert len(resumes) == 1
        assert resumes[0]["id"] == resume_id
        assert resumes[0]["type"] == "master"
        assert resumes[0]["has_pdf"] is True
        assert resumes[0]["has_tex"] is True

    def test_get_resume_path_pdf_and_tex(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        resume_id = rl.save_resume(
            TEST_USER, tex_string="content", pdf_path=compiled_pdf_path,
            resume_type="tailored", label="Job A", job_description="desc",
        )
        pdf_path = rl.get_resume_path(TEST_USER, resume_id, "pdf")
        tex_path = rl.get_resume_path(TEST_USER, resume_id, "tex")
        assert pdf_path.exists()
        assert tex_path.read_text(encoding="utf-8") == "content"

    def test_get_resume_path_invalid_file_type(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        resume_id = rl.save_resume(
            TEST_USER, tex_string="content", pdf_path=compiled_pdf_path,
            resume_type="master", label="Master",
        )
        with pytest.raises(ValueError):
            rl.get_resume_path(TEST_USER, resume_id, "docx")

    def test_delete_resume_removes_folder(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        resume_id = rl.save_resume(
            TEST_USER, tex_string="content", pdf_path=compiled_pdf_path,
            resume_type="master", label="Master",
        )
        rl.delete_resume(TEST_USER, resume_id)
        assert rl.list_resumes(TEST_USER) == []

    def test_list_resumes_skips_corrupt_metadata(self, isolated_resume_library):
        rl = isolated_resume_library
        rl.user_resumes_dir(TEST_USER).mkdir(parents=True, exist_ok=True)
        broken = rl.user_resumes_dir(TEST_USER) / "broken-entry"
        broken.mkdir()
        (broken / "metadata.json").write_text("{not valid json", encoding="utf-8")
        assert rl.list_resumes(TEST_USER) == []


class TestDeleteResumesByType:
    def test_only_removes_matching_type(self, isolated_resume_library, compiled_pdf_path):
        # Resume IDs are only unique to the second (see _make_resume_id's
        # docstring) — sleep between saves so the two "master" entries land
        # in distinct folders instead of the second silently overwriting
        # the first's same-second ID.
        rl = isolated_resume_library
        rl.save_resume(TEST_USER, tex_string="t1", pdf_path=compiled_pdf_path, resume_type="tailored", label="Job A")
        rl.save_resume(TEST_USER, tex_string="m1", pdf_path=compiled_pdf_path, resume_type="master", label="Master")
        time.sleep(1.1)
        rl.save_resume(TEST_USER, tex_string="m2", pdf_path=compiled_pdf_path, resume_type="master", label="Master")

        removed = rl.delete_resumes_by_type(TEST_USER, "master")

        assert removed == 2
        remaining = rl.list_resumes(TEST_USER)
        assert len(remaining) == 1
        assert remaining[0]["type"] == "tailored"

    def test_repeated_master_regeneration_keeps_exactly_one_entry(self, isolated_resume_library, compiled_pdf_path):
        """Regression test for AUDIT.md 2.3: regenerating the master resume
        used to pile up a new library entry every time."""
        rl = isolated_resume_library
        for _ in range(5):
            rl.delete_resumes_by_type(TEST_USER, "master")
            rl.save_resume(TEST_USER, tex_string="m", pdf_path=compiled_pdf_path, resume_type="master", label="Master")

        resumes = rl.list_resumes(TEST_USER)
        assert len(resumes) == 1
        assert resumes[0]["type"] == "master"

    def test_no_matching_type_is_a_noop(self, isolated_resume_library):
        assert isolated_resume_library.delete_resumes_by_type(TEST_USER, "master") == 0

    def test_other_users_masters_are_not_touched(self, isolated_resume_library, compiled_pdf_path):
        """Cross-user safety: delete_resumes_by_type for user A must not
        delete user B's masters, even if B has them."""
        rl = isolated_resume_library
        rl.save_resume(TEST_USER, tex_string="alice-m", pdf_path=compiled_pdf_path, resume_type="master", label="Alice Master")
        time.sleep(1.1)
        rl.save_resume(OTHER_USER, tex_string="bob-m", pdf_path=compiled_pdf_path, resume_type="master", label="Bob Master")
        time.sleep(1.1)
        rl.save_resume(OTHER_USER, tex_string="bob-m2", pdf_path=compiled_pdf_path, resume_type="master", label="Bob Master 2")

        removed = rl.delete_resumes_by_type(TEST_USER, "master")

        assert removed == 1
        assert len(rl.list_resumes(TEST_USER)) == 0
        # Bob's two masters are untouched.
        bob_resumes = rl.list_resumes(OTHER_USER)
        assert len(bob_resumes) == 2
        assert all(r["type"] == "master" for r in bob_resumes)


class TestRenameResume:
    def test_rename_updates_label_keeps_id(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        resume_id = rl.save_resume(
            TEST_USER, tex_string="content", pdf_path=compiled_pdf_path,
            resume_type="tailored", label="Original Label",
        )
        meta = rl.rename_resume(TEST_USER, resume_id, "New Label")
        assert meta["label"] == "New Label"
        assert meta["id"] == resume_id

        resumes = rl.list_resumes(TEST_USER)
        assert len(resumes) == 1
        assert resumes[0]["label"] == "New Label"
        assert resumes[0]["id"] == resume_id  # folder name / download links unchanged

    def test_rename_rejects_empty_label(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        resume_id = rl.save_resume(
            TEST_USER, tex_string="content", pdf_path=compiled_pdf_path,
            resume_type="tailored", label="Original",
        )
        with pytest.raises(ValueError):
            rl.rename_resume(TEST_USER, resume_id, "   ")

    def test_rename_nonexistent_resume_raises(self, isolated_resume_library):
        isolated_resume_library.user_resumes_dir(TEST_USER).mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError):
            isolated_resume_library.rename_resume(TEST_USER, "20260101-000000_nope", "New Label")


class TestDuplicateResume:
    def test_duplicate_creates_independent_copy(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        original_id = rl.save_resume(
            TEST_USER, tex_string="original content", pdf_path=compiled_pdf_path,
            resume_type="tailored", label="Job A",
        )
        new_id = rl.duplicate_resume(TEST_USER, original_id)

        assert new_id != original_id
        resumes = rl.list_resumes(TEST_USER)
        assert len(resumes) == 2

        # Original untouched
        original_tex = rl.get_resume_path(TEST_USER, original_id, "tex").read_text(encoding="utf-8")
        assert original_tex == "original content"

        # Copy has independent files with a distinguishing label
        copy_meta = next(r for r in resumes if r["id"] == new_id)
        assert copy_meta["label"] == "Job A (Copy)"
        copy_tex = rl.get_resume_path(TEST_USER, new_id, "tex").read_text(encoding="utf-8")
        assert copy_tex == "original content"

        # Mutating the copy must not affect the original
        rl.get_resume_path(TEST_USER, new_id, "tex").write_text("edited copy", encoding="utf-8")
        assert rl.get_resume_path(TEST_USER, original_id, "tex").read_text(encoding="utf-8") == "original content"

    def test_duplicate_with_custom_label(self, isolated_resume_library, compiled_pdf_path):
        rl = isolated_resume_library
        original_id = rl.save_resume(
            TEST_USER, tex_string="content", pdf_path=compiled_pdf_path,
            resume_type="master", label="Master",
        )
        new_id = rl.duplicate_resume(TEST_USER, original_id, new_label="Master v2")
        meta = next(r for r in rl.list_resumes(TEST_USER) if r["id"] == new_id)
        assert meta["label"] == "Master v2"

    def test_duplicate_nonexistent_resume_raises(self, isolated_resume_library):
        isolated_resume_library.user_resumes_dir(TEST_USER).mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError):
            isolated_resume_library.duplicate_resume(TEST_USER, "20260101-000000_nope")
