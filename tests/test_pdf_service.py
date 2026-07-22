"""
Integration smoke test for services/pdf_service.py against a real Tectonic
binary. Skipped automatically if Tectonic isn't installed (e.g. on CI
without the binary) — see AUDIT.md's "No CI" note for why that's worth
having wired up.
"""
import shutil
from pathlib import Path

import pytest

from services.pdf_service import compile_pdf, _find_tectonic, PdfCompilationError
from models.profile import Profile, PersonalInfo, Experience
from services.latex_service import render_latex

_tectonic_available = shutil.which("tectonic") is not None or Path(_find_tectonic()).exists()

pytestmark = pytest.mark.skipif(not _tectonic_available, reason="Tectonic is not installed")


def test_compile_produces_valid_pdf(tmp_path):
    profile = Profile(
        personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
        experience=[
            Experience(
                company="Acme & Co.", role="Engineer #1",
                start_date="Jan 2022", end_date="Present",
                bullets=["Shipped a 50% faster pipeline"],
            )
        ],
    )
    tex = render_latex(profile)
    pdf_path = compile_pdf(tex, tmp_path)

    assert pdf_path.exists()
    assert pdf_path.read_bytes()[:5] == b"%PDF-"


def test_compile_isolated_per_request_directories_do_not_clash(tmp_path):
    """Regression test for AUDIT.md 1.4: concurrent-looking compiles into
    separate directories must not interfere with each other."""
    profile = Profile(personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"))
    tex = render_latex(profile)

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    pdf_a = compile_pdf(tex, dir_a)
    pdf_b = compile_pdf(tex, dir_b)

    assert pdf_a != pdf_b
    assert pdf_a.exists() and pdf_b.exists()


def test_invalid_tex_raises_compilation_error(tmp_path):
    with pytest.raises(PdfCompilationError):
        compile_pdf(r"\documentclass{article}\begin{document}\undefinedcommand{broken", tmp_path)
