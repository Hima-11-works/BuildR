"""
End-to-end diagnostic script for the PDF download pipeline.

Tests:
1. Can we render LaTeX from the profile?
2. Can Tectonic compile the .tex to a valid PDF?
3. Does the master download API return a valid PDF?
4. Does the tailored download API return a valid PDF?
"""
import os, sys, json, tempfile
from pathlib import Path

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from models.profile import Profile
from services.storage_service import load_profile
from services.latex_service import render_latex
from services.pdf_service import compile_pdf, PdfCompilationError

DIVIDER = "=" * 60

def test_latex_rendering():
    """Test step 1-2: Profile → LaTeX → PDF"""
    print(f"\n{DIVIDER}")
    print("STEP 1: Loading profile")
    print(DIVIDER)
    
    profile = load_profile("default")
    print(f"  [OK] Profile loaded: {profile.personal_info.name}")
    print(f"  [OK] Email: {profile.personal_info.email}")
    print(f"  [OK] Education entries: {len(profile.education)}")
    print(f"  [OK] Experience entries: {len(profile.experience)}")
    print(f"  [OK] Project entries: {len(profile.projects)}")
    
    print(f"\n{DIVIDER}")
    print("STEP 2: Rendering LaTeX")
    print(DIVIDER)
    
    tex_string = render_latex(profile)
    print(f"  [OK] LaTeX rendered: {len(tex_string)} chars")
    print(f"  [OK] Starts with: {tex_string[:50]!r}")
    print(f"  [OK] Contains \\begin{{document}}: {'\\\\begin{document}' in tex_string or 'begin{document}' in tex_string}")
    print(f"  [OK] Contains \\end{{document}}: {'end{document}' in tex_string}")
    
    print(f"\n{DIVIDER}")
    print("STEP 3: Compiling PDF via Tectonic")
    print(DIVIDER)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        try:
            pdf_path = compile_pdf(tex_string, tmppath)
            print(f"  [OK] PDF compiled successfully!")
            print(f"  [OK] PDF path: {pdf_path}")
            print(f"  [OK] PDF exists: {pdf_path.exists()}")
            print(f"  [OK] PDF size: {pdf_path.stat().st_size} bytes")
            
            # Read first 5 bytes to verify it's a real PDF
            with open(pdf_path, "rb") as f:
                header = f.read(5)
            print(f"  [OK] PDF header: {header!r}")
            is_valid = header == b"%PDF-"
            print(f"  [OK] Valid PDF: {is_valid}")
            return is_valid, tex_string
        except PdfCompilationError as e:
            print(f"  ✗ COMPILATION FAILED: {e}")
            print(f"  ✗ Log:\n{e.log}")
            return False, tex_string
        except Exception as e:
            print(f"  ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False, tex_string


def test_flask_endpoints():
    """Test step 4: Flask endpoint responses"""
    print(f"\n{DIVIDER}")
    print("STEP 4: Testing Flask download endpoints")
    print(DIVIDER)
    
    from app import app
    client = app.test_client()
    
    # Test master resume generation + download
    print("\n  --- Testing POST /api/resume/master ---")
    resp = client.post("/api/resume/master")
    print(f"  Status: {resp.status_code}")
    print(f"  Content-Type: {resp.content_type}")
    print(f"  Content-Length: {resp.content_length}")
    
    if resp.status_code == 200:
        data = resp.get_json()
        if data and "id" in data:
            resume_id = data["id"]
            print(f"  [OK] Resume created with ID: {resume_id}")
            
            # Now test the download endpoint
            print(f"\n  --- Testing GET /api/resumes/{resume_id}/pdf ---")
            dl_resp = client.get(f"/api/resumes/{resume_id}/pdf")
            print(f"  Status: {dl_resp.status_code}")
            print(f"  Content-Type: {dl_resp.content_type}")
            print(f"  Content-Length: {dl_resp.content_length}")
            print(f"  Content-Disposition: {dl_resp.headers.get('Content-Disposition', 'NOT SET')}")
            
            body = dl_resp.data
            print(f"  Response body length: {len(body)} bytes")
            print(f"  Response first 5 bytes: {body[:5]!r}")
            is_pdf = body[:5] == b"%PDF-"
            print(f"  [OK] Valid PDF response: {is_pdf}")
            
            if not is_pdf:
                # Show what was returned instead
                try:
                    text = body.decode("utf-8", errors="replace")[:500]
                    print(f"  ✗ Response body (text): {text!r}")
                except:
                    pass
        else:
            print(f"  ✗ Unexpected JSON: {data}")
    else:
        data = resp.get_json() if resp.content_type and "json" in resp.content_type else resp.data
        print(f"  ✗ Error response: {data}")
    
    # Test tailored download if a session exists
    from services.auth_service import USERS_ROOT
    SESSIONS_DIR = USERS_ROOT / "default" / "sessions"
    sessions = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()] if SESSIONS_DIR.exists() else []
    
    if sessions:
        # Use the most recent session
        latest_session = sorted(sessions)[-1]
        print(f"\n  --- Testing POST /api/tailor/download/{latest_session} ---")
        
        dl_resp = client.post(f"/api/tailor/download/{latest_session}")
        print(f"  Status: {dl_resp.status_code}")
        print(f"  Content-Type: {dl_resp.content_type}")
        print(f"  Content-Length: {dl_resp.content_length}")
        print(f"  Content-Disposition: {dl_resp.headers.get('Content-Disposition', 'NOT SET')}")
        
        body = dl_resp.data
        print(f"  Response body length: {len(body)} bytes")
        print(f"  Response first 5 bytes: {body[:5]!r}")
        is_pdf = body[:5] == b"%PDF-"
        print(f"  [OK] Valid PDF response: {is_pdf}")
        
        if not is_pdf:
            try:
                text = body.decode("utf-8", errors="replace")[:500]
                print(f"  ✗ Response body (text): {text!r}")
            except:
                pass
    else:
        print("\n  (No sessions found to test tailored download)")


if __name__ == "__main__":
    print("BuildR PDF Pipeline Diagnostic Tool")
    print("=" * 60)
    
    ok, tex_string = test_latex_rendering()
    
    if ok:
        test_flask_endpoints()
    else:
        print("\n⚠ PDF generation failed — fixing this is the priority.")
        print("  The .tex content has been printed above for debugging.")
    
    print(f"\n{DIVIDER}")
    print("DIAGNOSTIC COMPLETE")
    print(DIVIDER)
