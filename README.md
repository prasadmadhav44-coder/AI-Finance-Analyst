# AI Finance Analyst

AI Finance Analyst is a full-stack, agentic financial research assistant that turns a plain-English question into a structured investment analysis. It uses Flask, Google ADK, Gemini, and yFinance to gather market context, generate an investment plan, assess risk, and return a BUY / WATCH / AVOID recommendation through a responsive web interface.

## Features

- Multi-agent analysis workflow: Research → Planner → Evaluator
- Real market data retrieval through yFinance
- Risk-aware investment planning and verdict generation
- Secure Flask backend with rate limiting and input validation
- Responsive frontend with loading states and cancellation support
- Production-ready deployment setup for Render and similar platforms

## Tech Stack

- Frontend: HTML, Tailwind CSS, Vanilla JavaScript
- Backend: Flask, Gunicorn
- AI orchestration: Google ADK + Gemini
- Data: yFinance, pandas

## How It Works

1. A user submits a question through the web UI.
2. The Research agent gathers price history and market context.
3. The Planner agent creates an investment thesis, timeline, and strategy.
4. The Evaluator agent returns structured JSON with research, plan, risk evaluation, and a verdict.

## Quick Start

### 1) Clone the repository

```bash
git clone <your-repo-url>
cd AI-Finance-Analyst
```

### 2) Create a virtual environment

```bash
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate # macOS/Linux
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
```

### 4) Configure environment variables

Create an environment file with at least:

```env
GOOGLE_API_KEY=your_google_gemini_api_key
SECRET_KEY=replace-with-a-secure-secret
FLASK_ENV=development
```

### 5) Run the app

```bash
python app.py
```

Open http://127.0.0.1:5000 to use the app.

## Project Structure

```text
.
├── app.py
├── config.py
├── agents/
│   ├── pipeline.py
│   ├── runner.py
│   ├── model_manager.py
│   └── tools.py
├── static/
│   ├── css/
│   └── js/
├── templates/
│   └── index.html
├── requirements.txt
├── Procfile
├── render.yaml
└── runtime.txt
```

## Deployment

The repository includes a Render Blueprint configuration for quick deployment. The app is also compatible with Gunicorn-based hosting environments.

## Notes

- The app is designed to be safe for production use with security headers, rate limiting, and input validation.
- Keep your API keys private and avoid committing them to source control.
