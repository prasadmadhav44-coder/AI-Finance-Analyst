# AI Finance Analyst

A multi-agent investment research assistant. A sequential pipeline of three
Gemini-backed agents — **Research → Planner → Evaluator** — turns a plain
-English question into a structured research summary, investment plan, risk
evaluation, and a BUY / WATCH / AVOID verdict.

```
Frontend:  HTML + Tailwind CSS + vanilla JS
Backend:   Flask (gunicorn in production)
AI:        Google ADK + Gemini (with model fallback chain)
Data:      yfinance + pandas
```

## Architecture

```
User question
     │
     ▼
Research Agent   — gathers price history / sector context via yfinance tools
     │
     ▼
Planner Agent    — drafts thesis, time horizon, entry/exit strategy
     │
     ▼
Evaluator Agent  — STRICT JSON output: { research, plan, risk_evaluation, verdict }
     │
     ▼
Flask /analyze   — parses JSON, returns it to the frontend
```

This is unchanged from the original design intentionally — see
`agents/pipeline.py`.

## What changed in this revision

| Area | Before | Now |
|---|---|---|
| Model selection | `get_model()` called Gemini at import time → hung Flask startup | `model_manager.get_primary_model()` is pure/offline; no network call until a request actually runs |
| 503 / overload errors | None handled | Exponential backoff retry (3 attempts) + fallback across a configurable model chain |
| 429 / quota errors | None handled | Detected separately; skips straight to the next model in the chain instead of retrying a dead key |
| `requirements.txt` | Unpinned `google-genai` (would install an incompatible 2.x against `google-adk==1.21.0`, which requires `<2.0.0`) | Every package pinned to a verified-compatible version |
| Duplicate tool code | `fetch_price_history` defined differently in both `pipeline.py` and `tools.py`; only one was wired in | Single source of truth in `tools.py`, with input validation + short-lived caching |
| Security headers | None | CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, HSTS (prod) on every response |
| CORS | Implicit (none configured = browser default, but no explicit policy) | Explicit allowlist, empty by default (same-origin only) |
| Rate limiting | None — a single user could exhaust your Gemini quota | `Flask-Limiter`: 10/min on `/analyze`, 200/hour globally (env-configurable) |
| Error responses | Could leak Python tracebacks via Flask debug mode | All error paths return clean JSON; tracebacks only ever go to server logs |
| Input validation | None — any JSON shape accepted | Type-checked, length-capped (1000 chars default), empty-string rejected |
| Logging | `print()` | Structured `logging` with request-correlation IDs |
| Frontend | Fixed `py-10` paddings, no skeletons, no abort handling | Mobile-first responsive spacing, loading skeletons, request timeout + cancellation, accessible labels/roles, reduced-motion support |
| Local dev | Flask reloader watching OneDrive-synced files → restart loops | `use_reloader=False` by default; reloader only relevant if you explicitly run in dev mode |
| Production server | `app.run(debug=True)` | `gunicorn` with a `Procfile` and `render.yaml`, debug forced off outside `FLASK_ENV=development` |

## Local development

```bash
git clone <your-repo-url>
cd AI-Finance-Analyst
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
cp .env.example .env
# edit .env and set GOOGLE_API_KEY

# Windows-specific note: if this project lives inside a OneDrive-synced
# folder, OneDrive's file-watching can interact badly with Flask's dev
# reloader. The app disables the reloader by default (use_reloader=False),
# so this should no longer be an issue — but if you ever re-enable it,
# move the project outside OneDrive first.

python app.py
```

Visit `http://127.0.0.1:5000`.

## Configuration reference

All settings are environment variables — see `.env.example` for the full,
documented list. The important ones:

- `GOOGLE_API_KEY` — required. Get one at https://aistudio.google.com/apikey
- `FLASK_ENV` — `production` (default) or `development`
- `SECRET_KEY` — required in production; generate with
  `python -c "import secrets; print(secrets.token_hex(32))"`
- `MODEL_FALLBACK_CHAIN` — comma-separated Gemini model IDs, tried in order
- `ANALYZE_RATE_LIMIT` / `GLOBAL_RATE_LIMIT` — rate limit strings, e.g. `"10 per minute"`

`config.py` validates these at startup and logs warnings for anything
missing or risky (e.g. debug mode left on in production).

## Deployment (Render free tier)

This repo includes a `render.yaml` Blueprint, so deployment is push-button:

1. Push this repo to GitHub.
2. In the Render Dashboard: **New → Blueprint**, select the repo.
3. Render reads `render.yaml` and provisions a free web service. You'll be
   prompted for `GOOGLE_API_KEY` during the Blueprint creation flow (it's
   marked `sync: false` so Render never stores it in the YAML itself).
   `SECRET_KEY` is auto-generated for you.
4. Deploy. Render runs `pip install -r requirements.txt`, then
   `gunicorn app:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`.
5. Render polls `/healthz` to confirm the service is up.

No database is required. If you outgrow the free tier's cold-start spin-down
behavior, the same Blueprint works unmodified on a paid Render plan — just
change `plan: free` to `plan: starter` (or higher) in `render.yaml`.

### Why Render over Railway / Vercel / Firebase / Netlify

- **Vercel / Netlify**: built around serverless functions with short
  execution limits; a multi-agent Gemini pipeline with retries can run past
  those limits, and neither platform runs a persistent Flask process.
- **Firebase Hosting**: static-only; cannot run Flask/Python at all.
- **Railway**: works similarly to Render and is a reasonable alternative,
  but its free tier has historically been more limited/usage-based than
  Render's. The `Procfile` in this repo (`web: gunicorn ...`) is
  Railway-compatible too if you'd rather use that.

## Security checklist

What's implemented, and what you (the operator) still need to do:

**Implemented in code**
- [x] Security headers on every response (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, HSTS in production)
- [x] CORS locked to an explicit allowlist (empty by default = same-origin only)
- [x] Rate limiting on the expensive `/analyze` endpoint and globally
- [x] Request body size cap (16 KB default)
- [x] Input validation: type-checked, non-empty, length-capped query
- [x] No stack traces or internal exception text ever returned to the client
- [x] `.env` excluded from git via `.gitignore`; `.env.example` has no real secrets
- [x] Debug mode and the Werkzeug reloader forced off outside explicit `FLASK_ENV=development`
- [x] Dependency versions pinned (no surprise major-version upgrades on redeploy)
- [x] Ticker symbols validated against a strict pattern before reaching yfinance, mitigating injection via the query→tool path

**You still need to do, before going live**
- [ ] Set a strong, unique `SECRET_KEY` in your production environment (Render's Blueprint auto-generates this for you; for other platforms, generate it yourself — see Configuration reference above)
- [ ] Restrict your `GOOGLE_API_KEY` in Google AI Studio / Cloud Console to only the Gemini API, and set a budget alert so a bug or abuse can't run up an unexpected bill
- [ ] If you ever add a custom domain, confirm HTTPS is enforced (Render does this automatically for both its own subdomain and custom domains)
- [ ] Review `ALLOWED_ORIGINS` before pointing any other frontend at this API — the default (same-origin only) is intentionally restrictive
- [ ] Rotate `GOOGLE_API_KEY` immediately if it's ever been committed to git history, posted in a screenshot, or pasted into a chat/ticket
- [ ] If you add authentication later (e.g. per-user accounts), don't roll your own session/password handling — use a maintained library, and keep `SECRET_KEY` out of source control

## Scalability notes

- The app is stateless per request (no server-side session state beyond the
  in-memory ADK runner created fresh per call), so it scales horizontally —
  increasing `--workers` in the `Procfile`/`render.yaml` or moving to a
  paid Render plan with more instances requires no code changes.
- `tools.py` includes a small in-process TTL cache (60s) for yfinance
  lookups to reduce redundant external calls under load. This is
  per-process; if you scale to multiple instances and want a *shared*
  cache, replace it with Redis (Render offers a free-tier Key Value
  instance — see `render.yaml`'s Blueprint reference for `type: keyvalue`).
- The model fallback chain doubles as a basic availability strategy: if
  your primary Gemini model is degraded, traffic automatically shifts to
  the next model rather than failing outright.
- Flask-Limiter's default in-memory storage (`storage_uri="memory://"`) is
  per-process. With multiple gunicorn workers or multiple instances, each
  has its own counter, so the effective rate limit is `limit × worker_count`.
  For a strict global limit across all workers/instances, switch
  `storage_uri` to a shared Redis instance.

## Project structure

```
.
├── app.py                  # Flask app: routes, security headers, error handling
├── config.py                # Centralized env-driven configuration + validation
├── agents/
│   ├── __init__.py
│   ├── pipeline.py          # Research -> Planner -> Evaluator agent definitions
│   ├── runner.py             # Executes the pipeline with retry + model fallback
│   ├── model_manager.py      # Fallback chain, retry/backoff, error classification
│   └── tools.py               # yfinance-backed tools used by the Research agent
├── templates/
│   └── index.html            # Responsive, accessible dashboard UI
├── static/
│   ├── css/app.css           # Skeletons, toast, skip-link, reduced-motion
│   └── js/app.js              # Fetch logic, validation, abort/timeout handling
├── requirements.txt          # Pinned, verified-compatible dependencies
├── Procfile                  # gunicorn start command (Render/Railway/Heroku-style)
├── render.yaml                # Render Blueprint for one-click deployment
├── runtime.txt                 # Python version pin
├── .env.example                 # Documented environment variables
└── .gitignore
```
