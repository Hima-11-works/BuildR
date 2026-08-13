# BuildR

Flask app for maintaining a single Master Resume and producing AI-tailored
PDFs for specific job applications. You keep one structured profile; for
each target role, the AI rephrases your bullets, drops irrelevant content,
and the renderer produces a clean LaTeX-to-PDF via Tectonic.

## Features

- Tabbed profile editor with TipTap rich-text fields for personal info,
  experience, education, projects, skills, certifications, achievements
- PDF / DOCX resume import — uploads are parsed into the structured
  profile by the AI
- AI tailoring workspace: paste a job description (or a URL), the
  model rephrases your bullets, surfaces missing keywords, and never
  invents new facts
- Iterative chat refinement of the tailored draft
- Named draft snapshots
- Resume library: every generated PDF (and its `.tex` source) catalogued,
  downloadable, renameable, duplicatable, deletable
- Job-description scraper for public posting URLs
- One-page optimization: when a borderline 2-page resume would spill
  over, an in-memory pass tightens spacing, caps redundant bullets,
  dedupes tech stacks, and trims verbose phrasing

## How it fits together

```
Browser (vanilla JS SPA, TipTap, in-browser PDF viewer)
        |
        v
app.py              Flask + REST routes
        |
        +-- services/ai_service.py     MiniMax client + anti-fabrication sanitizer
        +-- services/latex_service.py   Jinja2 env, Unicode normalize, LaTeX escape
        +-- services/pdf_service.py     Tectonic subprocess wrapper + page counter
        +-- services/parser_service.py  pypdf / python-docx extractors
        +-- services/scraper_service.py  BeautifulSoup job-posting cleaner
        +-- services/storage_service.py  profile.json persistence
        +-- services/resume_library.py   per-user resume catalog
        +-- services/session_service.py   tailoring scratch space
        +-- services/master_optimizer.py one-page intelligence
        |
        v
storage/users/<id>/
  profile.json      resumes/      sessions/      user.json
```

The AI never sees the on-disk `Profile` model. It receives a JSON-safe
schema and emits a separate `TailoredProfile` / `TailoringResult` shape;
the LaTeX renderer is the only thing that turns that into the final
document.

## Requirements

- Python 3.10+
- [Tectonic](https://tectonic-typesetting.github.io/) LaTeX compiler on
  PATH or discoverable in the project tree
- A MiniMax API key

## Install

```bash
git clone <repo> BuildR
cd BuildR
python -m venv env
source env/bin/activate   # Windows: .\env\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# set MINIMAX_API_KEY in .env
```

## Run

```bash
python app.py
# http://127.0.0.1:5000/
```

## Tests

```bash
pytest
```

Tectonic-dependent tests skip automatically if the binary isn't on PATH.
AI tests don't make live network calls — they exercise the sanitizer
and schema validation directly.

## Environment

| Variable | Required | Default | Notes |
|---|---|---|---|
| `MINIMAX_API_KEY` | yes | — | API key |
| `MINIMAX_BASE_URL` | no | `https://api.minimax.io/v1` | Override for self-hosted |
| `MINIMAX_MODEL` | no | `minimax-m3` | Model name |
| `MINIMAX_TIMEOUT_MS` | no | `90000` | Per-request HTTP timeout |
| `FLASK_DEBUG` | no | `0` | `1` for dev auto-reload |
| `PORT` | no | `5000` | Dev server port |

`.env` is gitignored.

## Project layout

```
app.py                    Flask entry + REST routes
models/                   Pydantic schemas
services/                 one module per concern
templates_latex/          resume.tex (Jinja2 template, custom delimiters)
templates/index.html      SPA shell
static/                   app.js, style.css, favicon
storage/                  runtime data (gitignored)
tests/                    pytest suite
Procfile                  gunicorn --workers=1 --threads=1
render-build.sh           build script: deps + Tectonic install + cache warm
.env.example              template for local env file
```

## Deployment

`render-build.sh` installs Python deps, drops a musl-static Tectonic
v0.16.9 into `.tectonic/tectonic`, and pre-warms its LaTeX package cache
so the first compile doesn't time out downloading packages.

Render settings:

- Build: `./render-build.sh`
- Start: from `Procfile`
- Env: `MINIMAX_API_KEY`

Gunicorn is pinned to one worker / one thread. Tectonic's first compile
on a fresh deploy may take 30-60 s longer than usual as it fetches
missing LaTeX packages; subsequent compiles use the cache. The
subprocess timeout is 180 s.

## Anti-fabrication

The AI is told to rephrase, never invent. After every response, a
sanitizer (`_sanitize_tailored_output` in `services/ai_service.py`) drops
any experience / project / certification / skill / technology / link
that isn't traceable back to the master profile. Skills the model wants
to add that aren't in the master go into `keywords_not_included_list`
instead.

## License

MIT. See [LICENSE](LICENSE).
