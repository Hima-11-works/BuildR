# BuildR 🚀 — Master Resume Builder & AI Tailoring Engine

BuildR is a modern, developer-friendly web application designed to help job seekers maintain a single, comprehensive **Master Resume** and automatically generate **tailored, job-specific resumes** using Google Gemini. It imports and parses existing files, provides an elegant form-based editor, escapes LaTeX formatting defensively, and compiles clean, ATS-compliant PDFs using the Tectonic compiler.

---

## 📋 Project Overview

When applying for different jobs, submitting a generic resume limits your response rate. However, manually rewriting experiences, achievements, and skill list permutations for dozens of roles is incredibly repetitive.

**BuildR** implements a separation between your **career database (master profile)** and **document rendering**:
1. **One Master Profile**: You store every experience, project, certification, and skill in a structured local JSON file.
2. **AI-Driven Tailoring**: When applying for a role, BuildR's AI engine analyzes the job description, filters out irrelevant history, and rephrases your work bullet points to highlight matches to the target job constraints.
3. **Pristine PDF Rendering**: The application escapes special characters and formats the layout into clean, industry-standard LaTeX, compilation-ready for a polished PDF.

---

## ✨ Features

- **✍️ Interactive Profile Editor**: A side-by-side editing layout divided into tabs: Personal Info, Work Experience, Education, Projects, Skills, Certifications, and Achievements.
- **📄 Resume PDF/DOCX Parser**: Import existing resumes directly! Upload a PDF or DOCX file, and Gemini parses the text into the structured Profile schema.
- **🤖 Automated AI Tailoring**: Paste a job description or provide a Job URL. BuildR scrapes and cleans the content, tailoring your profile items to match.
- **🛡️ Anti-Fabrication Safeguards**: Uses constrained decoding via Pydantic response schemas to guarantee that the LLM only rephrases existing history and never invents/fabricates credentials.
- **🎨 Custom LaTeX Engine**: Uses Jinja2 with custom delimiters (e.g., `\VAR{}` and `\BLOCK{}`) to avoid syntactical clashes with native LaTeX markup.
- **⚡ Standalone PDF Compilation**: Uses `tectonic` to fetch missing LaTeX packages on-the-fly, compiling PDFs in seconds without requiring full, multi-gigabyte TeX distributions.
- **📁 Resume Library**: Automatically catalogs all generated resumes (master and tailored), allowing you to download the compiled PDFs, grab the editable LaTeX source code (`.tex`), or clean up historical files.

---

## 🛠️ Tech Stack

- **Backend**: [Flask](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/app.py) (routes & HTTP APIs), [Pydantic](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/requirements.txt) (strict model validation), `python-dotenv` (configuration loader)
- **AI Integrations**: [google-genai SDK](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/ai_service.py) (Gemini Developer API client using the `gemini-3.5-flash` model)
- **Parsers**: [pypdf](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/parser_service.py) & [python-docx](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/parser_service.py) (text extraction from PDFs and Word docs)
- **Web Scraping**: [BeautifulSoup4](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/scraper_service.py) & `requests` (job description page cleaning)
- **LaTeX Renderer**: [Jinja2](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/latex_service.py) (standalone template engine env) & Tectonic CLI compiler
- **Frontend**: Single-Page App (SPA) built using Semantic HTML5, Vanilla JavaScript ([static/app.js](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/static/app.js)), and Vanilla CSS ([static/style.css](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/static/style.css)) featuring glassmorphism elements, transitions, and fully responsive grid views
- **Database**: Flat-file JSON database storing your profile inside [storage/profile.json](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/storage/profile.json)

---

## 📁 Project Structure

Below is the directory map of the BuildR repository:

```text
BuildR/
├── app.py                     # Main Flask entry-point and API routing
├── requirements.txt           # Python packages listing
├── LICENSE                    # MIT License details
├── models/                    # Pydantic schemas and serialization models
│   ├── profile.py             # Core Profile model definitions
│   └── tailored_profile.py    # AI-safe schema mappings for Gemini API
├── services/                  # Business logic layers
│   ├── ai_service.py          # Gemini configuration, prompts, and calls
│   ├── latex_service.py       # Jinja2 environment and LaTeX escaping utilities
│   ├── pdf_service.py         # Subprocess wrappers executing Tectonic
│   ├── parser_service.py      # PDF & DOCX text extractors
│   ├── scraper_service.py     # BeautifulSoup scrapers for job posting web pages
│   ├── storage_service.py     # Local file JSON database managers
│   └── resume_library.py      # Metadata and history catalog management
├── static/                    # Frontend assets
│   ├── app.js                 # Frontend SPA router, state manager, and forms controller
│   └── style.css              # Custom responsive styles and color palette
├── templates/                 # Jinja2 HTML layouts
│   └── index.html             # Single-page app HTML view
├── templates_latex/           # Raw LaTeX templates
│   └── resume.tex             # LaTeX skeleton compiled to PDF
└── storage/                   # Saved master profile JSON and generated resumes
    ├── profile.json           # User profile save-state
    └── resumes/               # Historical archives (PDF/TeX/Metadata)
```

### Module Breakdown:
*   [app.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/app.py) handles HTTP requests and returns JSON/downloads.
*   [models/profile.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/models/profile.py) specifies the strict [Profile](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/models/profile.py) data layout.
*   [models/tailored_profile.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/models/tailored_profile.py) details the [TailoredProfile](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/models/tailored_profile.py) schema mapping structure passed to the Gemini API.
*   [services/ai_service.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/ai_service.py) structures LLM prompt guidelines and instructs the client session.
*   [services/latex_service.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/latex_service.py) handles character escapes (e.g. converting `&` to `\&`) to ensure error-free compilation.
*   [services/pdf_service.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/pdf_service.py) spawns subprocess runs targeting the `tectonic` binary.
*   [services/parser_service.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/parser_service.py) reads text streams from uploaded PDF/DOCX files.
*   [services/scraper_service.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/scraper_service.py) requests and strips HTML clutter from external postings.
*   [services/storage_service.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/storage_service.py) reads and validates [storage/profile.json](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/storage/profile.json).
*   [services/resume_library.py](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/services/resume_library.py) catalogs files compiled inside the [storage/resumes/](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/storage/resumes) directory.
*   [templates/index.html](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/templates/index.html) hosts the single-page application structure.
*   [templates_latex/resume.tex](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/templates_latex/resume.tex) specifies the layout rules for generating PDFs.

---

## ⚙️ Prerequisites

To run BuildR locally, you will need:
1. **Python 3.10+**
2. **Tectonic CLI**: The compiler for LaTeX templates. Install it using the method matching your OS:
    *   **Windows**: `winget install --id=AnotherRedFox.Tectonic -e`
    *   **macOS**: `brew install tectonic`
    *   **Linux**: `cargo install tectonic` or use your package manager (e.g. `sudo apt install tectonic`)
    *   **Anaconda (Cross-Platform)**: `conda install -c conda-forge tectonic`
3. **Google Gemini API Key**: Visit the [Google AI Studio](https://aistudio.google.com/app/apikey) to generate an API key.

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
   pip install -r requirements.txt
   ```

4. **Verify Tectonic is Available**
   ```bash
   tectonic --version
   ```

---

## 🧪 Running Tests

BuildR has a `pytest` suite covering the deterministic core logic (LaTeX escaping/rendering, the AI anti-hallucination sanitizer, resume-library and session path safety, list-detection heuristics) plus Flask route tests against fully isolated storage — nothing under `tests/` touches your real `storage/` directory.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

Tests tagged as requiring Tectonic (real PDF compilation) are skipped automatically if it isn't installed. No `GEMINI_API_KEY` is required — the AI-related tests exercise the anti-hallucination sanitizer function directly rather than calling the Gemini API.

---

## 🔒 Environment Variables

BuildR uses a `.env` file to securely retrieve settings at runtime. Copy [.env.example](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/.env.example) to `.env` in the project's root folder and fill in your key:

```env
# Google Gemini Developer API key (required)
GEMINI_API_KEY=your_actual_gemini_api_key_here

# Enable Flask's auto-reload + interactive debugger (optional, default: off).
# Only turn this on for local development — the debugger lets anyone who can
# reach the port run arbitrary Python. Never enable it if the server is
# reachable from outside your own machine.
FLASK_DEBUG=1

# Port the dev server listens on (optional, default: 5000)
PORT=5000

# Timeout (ms) for Gemini API calls (optional, default: 90000)
GEMINI_TIMEOUT_MS=90000
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
*   **Resume Ingestion (Import)**: Click **Import PDF/DOCX** at the top right, upload an existing resume, and let Gemini extract the information to pre-populate the forms.
*   **Manual Entry**: Click through the forms to fill in contact details, work history, projects, certifications, and skills.
*   **Save Progress**: Click the **Save Profile** button. The backend validates your information against [Profile](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/models/profile.py) rules and saves the state to [storage/profile.json](file:///c:/Users/KIIT/OneDrive/Documents/GitHub/BuildR/storage/profile.json).

### Step 3: Print Your Master Resume
Click **Generate Master PDF** under the Master Resume editor. The application runs the LaTeX generator, compiles it, and downloads a clean, professional PDF file.

### Step 4: Tailor for a Target Role
1. Switch to the **AI Tailoring** tab.
2. Paste the text description of the target job role, or provide a public career posting URL.
3. Click **Generate Tailored PDF**.
4. Behind the scenes, the `ai_service.py` sends the job details and your master profile to Gemini. The model selects matching accomplishments, optimizes bullet descriptions to align with key criteria, and returns a tailored dataset.
5. The application builds a customized PDF and saves it to the library.

### Step 5: Manage Your Resumes
Navigate to the **Resume Library** tab to review previous downloads. Here you can:
*   Download compiled PDFs.
*   Download the raw LaTeX source (`.tex`) file to make manual adjustments.
*   Permanently delete old tailoring tests.

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
| `GEMINI_API_KEY` | Your Google Gemini API key ([get one here](https://aistudio.google.com/app/apikey)) |

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
- [ ] **Keywords Matching Analysis**: Calculate and display an ATS fit score detailing how well the tailored resume aligns with the target job posting.
- [ ] **Multi-User Capabilities**: Add user registration, authorization, and cloud databases (e.g., PostgreSQL/SQLite) instead of single-user local file storage.
- [ ] **Automated Syncing**: Integrate directly with platforms like LinkedIn to sync profile updates.
