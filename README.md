# BuildR 🚀 — Master Resume Builder & AI Tailoring Engine

BuildR is a modern, developer-friendly web application designed to help job seekers maintain a single, comprehensive **Master Resume** and automatically generate **tailored, job-specific resumes** using MiniMax AI (`minimax-m3`). It imports and parses existing files, provides an elegant form-based editor, escapes LaTeX formatting defensively, and compiles clean, ATS-compliant PDFs using the Tectonic compiler.

---

## 📋 Project Overview

When applying for different jobs, submitting a generic resume limits your response rate. However, manually rewriting experiences, achievements, and skill list permutations for dozens of roles is incredibly repetitive.

**BuildR** implements a separation between your **career database (master profile)** and **document rendering**:
1. **One Master Profile**: You store every experience, project, certification, and skill in a structured local JSON file.
2. **AI-Driven Tailoring**: When applying for a role, BuildR's AI engine analyzes the job description, filters out irrelevant history, and rephrases your work bullet points to highlight matches to the target job constraints.
3. **Pristine PDF Rendering**: The application escapes special characters and formats the layout into clean, industry-standard LaTeX, compilation-ready for a polished PDF.

---

## ✨ Features

- **✍️ Interactive Profile Editor**: A side-by-side editing layout divided into tabs: Personal Info, Work Experience, Education, Projects, Skills, Certifications, and Achievements with integrated TipTap rich-text editing.
- **📄 Resume PDF/DOCX Parser**: Import existing resumes directly! Upload a PDF or DOCX file, and MiniMax parses the text into the structured Profile schema.
- **🤖 Automated AI Tailoring & Interactive Chat**: Paste a job description or provide a Job URL. BuildR scrapes and cleans the content, tailoring your profile items to match. Chat directly with the AI in real time to refine bullet points or adjust draft focus.
- **🔍 Job Scraping & Keyword Analysis**: Automatically scrapes target job URLs and extracts key skills, technologies, and missing requirements (`keywords_not_included_list`).
- **🛡️ Anti-Fabrication Safeguards**: Uses strict JSON schema enforcement and an automated post-generation fuzzy-matching sanitizer (`_sanitize_tailored_output`) to guarantee that the LLM only rephrases existing history and never invents/fabricates credentials.
- **🎨 Custom LaTeX Engine & Defensive Escaping**: Uses Jinja2 with custom delimiters (e.g., `\VAR{}` and `\BLOCK{}`) to avoid syntactical clashes with native LaTeX markup. Automatically normalizes Unicode characters (en/em dashes, bullets, smart quotes) and recursively escapes all user-facing data (including dictionary keys like skill categories) to prevent LaTeX compilation errors.
- **⚡ Standalone PDF Compilation & Live Preview**: Uses `tectonic` to fetch missing LaTeX packages on-the-fly, compiling PDFs in seconds with real-time in-memory preview streaming.
- **📸 Draft Snapshots**: Create, restore, and compare named draft snapshots during AI tailoring sessions.
- **📁 Resume Library**: Automatically catalogs all generated resumes (master and tailored), allowing you to download the compiled PDFs, grab the editable LaTeX source code (`.tex`), rename entries, duplicate versions, or clean up historical files.

---

## 🛠️ Tech Stack

- **Backend**: [Flask](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/app.py) (routes & HTTP APIs), [Pydantic v2](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/requirements.txt) (strict model validation), `python-dotenv` (configuration loader)
- **AI Integrations**: [openai SDK](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/ai_service.py) targeting MiniMax API with the `minimax-m3` model. Features deferred SDK loading for zero idle memory overhead (~30 MB saved).
- **Parsers**: [pypdf](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/parser_service.py) & [python-docx](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/parser_service.py) (text extraction from PDFs and Word docs)
- **Web Scraping**: [BeautifulSoup4](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/scraper_service.py) & `requests` (job description page cleaning)
- **LaTeX Renderer**: [Jinja2](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/latex_service.py) (standalone template engine env) & Tectonic CLI compiler
- **Frontend**: Single-Page App (SPA) built using Semantic HTML5, Vanilla JavaScript ([static/app.js](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/static/app.js)), TipTap Rich Text Editor, and Vanilla CSS ([static/style.css](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/static/style.css)) featuring glassmorphism elements, dark/light themes, live PDF viewer, and responsive layouts
- **Database**: Flat-file JSON database storing your profile inside [storage/profile.json](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/storage/profile.json) and workspace sessions in `storage/sessions/`

---

## 📁 Project Structure

Below is the directory map of the BuildR repository:

```text
BuildR/
├── app.py                     # Main Flask entry-point and API routing (17 REST endpoints)
├── requirements.txt           # Production Python packages
├── requirements-dev.txt       # Development & testing packages (pytest)
├── Procfile                   # Render process definition (Gunicorn)
├── render-build.sh            # Render build script (installs pinned Tectonic v0.16.9)
├── README.md                  # System overview and quickstart guide
├── HANDOFF.md                 # Complete technical handoff specification
├── LICENSE                    # MIT License details
├── models/                    # Pydantic schemas and serialization models
│   ├── profile.py             # Core Profile database model
│   ├── tailored_profile.py    # AI-safe schema mappings
│   └── tailoring_result.py    # Multi-agent output schema (profile, suggestions, stats)
├── services/                  # Business logic layers
│   ├── ai_service.py          # MiniMax client, prompts, reasoning block sanitizer, & job analysis
│   ├── latex_service.py       # Jinja2 env, Unicode normalization, & regex LaTeX escaper
│   ├── pdf_service.py         # Subprocess execution wrapper for Tectonic compiler
│   ├── parser_service.py      # PDF & DOCX text extractors
│   ├── scraper_service.py     # BeautifulSoup job posting scraper with noise stripping
│   ├── storage_service.py     # Disk persistence for storage/profile.json
│   ├── resume_library.py      # Metadata, history catalog, & path-traversal safety
│   └── session_service.py     # Scratch workspace session manager (storage/sessions/)
├── static/                    # Frontend assets
│   ├── app.js                 # SPA router, state manager, TipTap integration, & live PDF viewer
│   └── style.css              # Custom responsive styles and glassmorphism design system
├── templates/                 # HTML templates
│   └── index.html             # Single-page application UI layout
├── templates_latex/           # Raw LaTeX templates
│   └── resume.tex             # LaTeX skeleton compiled to PDF
├── tests/                     # 102 automated unit/integration tests
└── storage/                   # Saved master profile JSON and generated resumes
    ├── profile.json           # User profile save-state
    ├── resumes/               # Cataloged output folders (PDF/TeX/Metadata)
    └── sessions/              # Workspace session state (auto-cleaned after 7 days)
```

---

## ⚙️ Prerequisites

To run BuildR locally, you will need:
1. **Python 3.10+**
2. **Tectonic CLI**: The compiler for LaTeX templates. Install it using the method matching your OS:
    *   **Windows**: `winget install --id=AnotherRedFox.Tectonic -e`
    *   **macOS**: `brew install tectonic`
    *   **Linux**: `cargo install tectonic` or use your package manager (e.g. `sudo apt install tectonic`)
    *   **Anaconda (Cross-Platform)**: `conda install -c conda-forge tectonic`
3. **MiniMax API Key**: Obtain an API key from the [MiniMax Platform](https://platform.minimaxi.com/).

---

## 🚀 Installation & Setup

Follow these steps to configure your environment and start the application:

1. **Clone the Repository**
   ```bash
   git clone https://github.com/<your-username>/BuildR.git
   cd BuildR
   ```

2. **Create and Activate a Virtual Environment**
   *   **On Windows (PowerShell)**:
       ```powershell
       python -m venv env
       .\env\Scripts\Activate.ps1
       ```
   *   **On macOS / Linux**:
       ```bash
       python -m venv env
       source env/bin/activate
       ```

3. **Install Python Dependencies**
   ```bash
   pip install -r requirements.txt -r requirements-dev.txt
   ```

4. **Verify Tectonic is Available**
   ```bash
   tectonic --version
   ```

---

## 🧪 Running Tests

BuildR features an extensive automated `pytest` suite covering deterministic core logic (Unicode normalization, regex LaTeX escaping, dictionary key escaping, anti-hallucination sanitization, resume-library cataloging, and workspace session management) plus Flask web API endpoints:

```bash
python -m pytest
```

Tests tagged as requiring Tectonic (real PDF compilation) are skipped automatically if it isn't installed. No `MINIMAX_API_KEY` is required — AI tests exercise the sanitizer and schema validation functions directly without making live network requests.

---

## 🔒 Environment Variables

BuildR uses a `.env` file to securely retrieve settings at runtime. Copy [.env.example](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/.env.example) to `.env` in the project's root folder and fill in your key:

```env
# MiniMax API Key (required)
MINIMAX_API_KEY=your_actual_minimax_api_key_here

# MiniMax Base URL (optional, default: https://api.minimax.io/v1)
# MINIMAX_BASE_URL=https://api.minimax.io/v1

# MiniMax Model Choice (optional, default: minimax-m3)
# MINIMAX_MODEL=minimax-m3

# Timeout (ms) for MiniMax API calls (optional, default: 90000)
MINIMAX_TIMEOUT_MS=90000

# Enable Flask auto-reload + interactive debugger (optional, default: 0)
FLASK_DEBUG=1

# Port the dev server listens on (optional, default: 5000)
PORT=5000

# Secret key for signing session cookies
SECRET_KEY=your_secret_key_here
```

> [!WARNING]
> Do not commit your `.env` file to version control. The repository's [.gitignore](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/.gitignore) file is configured to prevent committing this file.

---

## 💡 Usage Walkthrough

### Step 1: Launch the Local Server
From your terminal (with the virtual environment activated), start the Flask development server:
```bash
python app.py
```
The server will run on [http://127.0.0.1:5000/](http://127.0.0.1:5000/).

### Step 2: Establish Your Master Profile
Open your browser and navigate to the local address. You can build your master profile in two ways:
*   **Resume Ingestion (Import)**: Click **Import PDF/DOCX** at the top right, upload an existing resume, and let MiniMax extract the information to pre-populate the forms.
*   **Manual Entry**: Click through the forms to fill in contact details, work history, projects, certifications, and skills with TipTap rich text formatting.
*   **Save Progress**: Click the **Save Profile** button. The backend validates your information against [Profile](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/models/profile.py) rules and saves the state to [storage/profile.json](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/storage/profile.json).

### Step 3: Print Your Master Resume
Click **Generate Master PDF** under the Master Resume editor. The application runs the LaTeX generator, compiles it, and downloads a clean, professional PDF file.

### Step 4: Tailor for a Target Role
1. Switch to the **AI Tailoring** tab.
2. Paste the text description of the target job role, or provide a public career posting URL.
3. Click **Start Tailoring Workspace**.
4. Behind the scenes, `ai_service.py` sends the job details and your master profile to MiniMax (`minimax-m3`). The model selects matching accomplishments, optimizes bullet descriptions to align with key criteria, extracts missing job keywords, and returns a structured tailored profile dataset.
5. Review the live PDF preview, refine your draft manually or via the interactive **AI Chat**, create draft snapshots, and export your finalized resume to the catalog.

### Step 5: Manage Your Resumes
Navigate to the **Resume Library** tab to review cataloged resumes. Here you can:
*   Download compiled PDFs.
*   Download the raw LaTeX source (`.tex`) file to make manual adjustments.
*   Rename resume entries or duplicate previous versions.
*   Permanently delete historical tailoring tests.

---

## ☁️ Deployment (Render)

BuildR is configured for deployment on [Render](https://render.com/) as a **Web Service** using the Python native runtime.

### Service Configuration

| Setting | Value |
|---|---|
| **Build Command** | `./render-build.sh` |
| **Start Command** | `gunicorn --bind 0.0.0.0:$PORT app:app` (from [Procfile](Procfile)) |

### Required Environment Variables

Set the following in the Render Dashboard under **Environment**:

| Variable | Description |
|---|---|
| `MINIMAX_API_KEY` | Your MiniMax API key ([get one here](https://platform.minimaxi.com/)) |

### How Tectonic is Installed

The [render-build.sh](render-build.sh) build script automatically:

1. Installs Python dependencies from `requirements.txt`.
2. Downloads a **pinned version** of the [Tectonic](https://tectonic-typesetting.github.io/) LaTeX compiler (v0.16.9) from GitHub Releases.
3. Places the binary at `$HOME/tectonic`.

**No Procfile modification is needed.** The existing [`_find_tectonic()`](services/pdf_service.py) function in `pdf_service.py` already searches `Path.home() / "tectonic"` as a fallback location. By placing the binary there during the build phase, the application discovers it automatically at runtime — no `PATH` changes, no code changes.

### Updating Tectonic

To upgrade the pinned Tectonic version, edit the `TECTONIC_VERSION` variable at the top of `render-build.sh` and redeploy. Available versions are listed on the [Tectonic Releases](https://github.com/tectonic-typesetting/tectonic/releases) page.

> [!NOTE]
> The **first PDF compilation** after a fresh deploy may take 30–60 seconds longer than usual because Tectonic downloads required LaTeX packages on-the-fly. Subsequent compilations use a cache and are significantly faster. The application's 120-second subprocess timeout accommodates this.

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/LICENSE) file for the full text.

Copyright (c) 2026 Himanshi Saxena.

---

## 🗺️ Roadmap & Future Improvements

- [ ] **Dynamic Layout Templates**: Let users choose between various styles (e.g., standard academic, modern minimalist, two-column layouts).
- [ ] **Direct DOCX Export**: Support downloading tailored resumes as Microsoft Word files.
- [x] **Keywords Matching & Missing Skill Analysis**: Automated extraction of target job skills and missing requirements tracking (`keywords_not_included_list`).
- [ ] **Multi-User Capabilities**: Add user authentication with database persistence (e.g., PostgreSQL/SQLite) instead of single-user local file storage.
- [ ] **Automated Syncing**: Integrate directly with platforms like LinkedIn to sync profile updates.

