"""
In-browser diagnostic: test the tailored download endpoint via real HTTP.
Uses requests to simulate the browser's fetch() call.
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import requests
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Find the latest session
SESSIONS_DIR = PROJECT_ROOT / "storage" / "sessions"
sessions = sorted([d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()])
print(f"Found {len(sessions)} sessions")

if sessions:
    latest = sessions[-1]
    print(f"Latest session: {latest}")
    
    # Check if the draft is valid
    draft_profile = SESSIONS_DIR / latest / "draft" / "profile.json"
    if draft_profile.exists():
        import json
        with open(draft_profile, 'r', encoding='utf-8') as f:
            prof = json.load(f)
        print(f"Draft profile name: {prof['personal_info']['name']}")
        print(f"Draft profile size: {draft_profile.stat().st_size} bytes")
        
        # Check if there are any problematic characters in the profile
        print("\n--- Checking for problematic content ---")
        prof_str = json.dumps(prof)
        
        # Check for unicode chars that might break LaTeX
        problematic = []
        for i, ch in enumerate(prof_str):
            if ord(ch) > 127 and ch not in ['₹']:  # Known safe unicode
                problematic.append((i, ch, hex(ord(ch))))
        
        if problematic:
            print(f"Found {len(problematic)} non-ASCII characters:")
            for pos, ch, code in problematic[:20]:
                context = prof_str[max(0,pos-20):pos+20]
                print(f"  char={ch!r} code={code} context=...{context!r}...")
        else:
            print("No problematic non-ASCII characters found")
        
        # Try to render LaTeX from this draft
        print("\n--- Attempting LaTeX render from draft profile ---")
        from dotenv import load_dotenv
        load_dotenv()
        
        from models.profile import Profile
        from services.latex_service import render_latex
        from services.pdf_service import compile_pdf, PdfCompilationError
        import tempfile
        
        profile = Profile.model_validate(prof)
        tex = render_latex(profile)
        print(f"LaTeX output: {len(tex)} chars")
        
        # Save tex for inspection
        debug_tex = PROJECT_ROOT / "debug_output.tex"
        debug_tex.write_text(tex, encoding='utf-8')
        print(f"Saved LaTeX to: {debug_tex}")
        
        # Try to compile
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                pdf_path = compile_pdf(tex, Path(tmpdir))
                size = pdf_path.stat().st_size
                with open(pdf_path, 'rb') as f:
                    header = f.read(5)
                print(f"PDF compiled: {size} bytes, header={header!r}, valid={header == b'%PDF-'}")
            except PdfCompilationError as e:
                print(f"COMPILATION FAILED: {e}")
                print(f"Log:\n{e.log}")
                
                # Save the problematic tex for inspection
                print(f"\nThe problematic .tex file has been saved to: {debug_tex}")
                print("You can inspect it manually to find the issue.")
    else:
        print("No draft profile found!")
