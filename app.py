"""
AI Finance Analyst — Flask application entrypoint.

Production-readiness features in this file:
  - Structured logging (no bare print() calls reaching stdout unstructured)
  - Security headers on every response (CSP, X-Frame-Options, etc.)
  - CORS locked down to an explicit allowlist (empty by default = same-origin only)
  - Rate limiting on the expensive /analyze endpoint
  - Request size limit + input validation (query length, type)
  - Errors never leak stack traces or internal exception text to the client
  - Health check endpoint for the hosting platform's uptime monitor
  - Debug mode and the Werkzeug reloader are OFF by default; both are only
    enabled when FLASK_ENV=development is explicitly set, which also avoids
    the "Flask auto-restarting because OneDrive is being watched" issue from
    development on Windows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

from flask import Flask, g, jsonify, render_template, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from agents.runner import PipelineError, run_once
from config import Config

# ---------------------------------------------------------------------------
# Logging — structured, single configuration point.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("ai_finance_analyst.app")

config_problems = Config.validate()
for problem in config_problems:
    logger.warning("Configuration issue: %s", problem)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = Config.SECRET_KEY or "dev-only-insecure-key"
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH_BYTES
    app.config["JSON_SORT_KEYS"] = False

    # CORS: explicit allowlist only. If ALLOWED_ORIGINS is empty (the
    # default), this effectively disables cross-origin requests entirely,
    # which is correct for a server-rendered app where the frontend is
    # served by this same Flask process.
    if Config.ALLOWED_ORIGINS:
        CORS(
            app,
            resources={r"/analyze": {"origins": Config.ALLOWED_ORIGINS}},
            supports_credentials=False,
        )

    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[Config.GLOBAL_RATE_LIMIT],
        storage_uri="memory://",
    )

    # -------------------------------------------------------------------
    # Security headers on every response
    # -------------------------------------------------------------------
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Tailwind is loaded from the CDN in templates/index.html, so the
        # CSP must allow that specific host rather than 'unsafe-inline'/
        # wildcards across the board.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if Config.IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        return response

    # -------------------------------------------------------------------
    # Request correlation id — makes it possible to find a specific
    # request's log lines in production logs when a user reports an issue.
    # -------------------------------------------------------------------
    @app.before_request
    def assign_request_id():
        g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))

    @app.after_request
    def attach_request_id(response):
        response.headers["X-Request-Id"] = getattr(g, "request_id", "")
        return response

    # -------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/healthz")
    def healthz():
        """Liveness/readiness probe for the hosting platform."""
        return jsonify({"status": "ok"}), 200

    @app.route("/analyze", methods=["POST"])
    @limiter.limit(Config.ANALYZE_RATE_LIMIT)
    def analyze():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        question = body.get("query")
        if not isinstance(question, str) or not question.strip():
            return jsonify({"error": "'query' is required and must be a non-empty string."}), 400

        question = question.strip()
        if len(question) > Config.MAX_QUERY_LENGTH:
            return jsonify(
                {
                    "error": (
                        f"'query' exceeds the maximum length of "
                        f"{Config.MAX_QUERY_LENGTH} characters."
                    )
                }
            ), 400

        request_id = getattr(g, "request_id", "unknown")
        logger.info("[%s] /analyze query_len=%d", request_id, len(question))

        try:
            raw_output = asyncio.run(run_once(question))
        except PipelineError as exc:
            logger.error("[%s] Pipeline failed: %s", request_id, exc)
            return jsonify(
                {
                    "research": "",
                    "plan": "",
                    "risk_evaluation": "",
                    "verdict": "ERROR",
                    "error": "The analysis service is temporarily unavailable. Please try again shortly.",
                }
            ), 503
        except Exception:  # noqa: BLE001
            logger.exception("[%s] Unexpected error in /analyze", request_id)
            return jsonify(
                {
                    "research": "",
                    "plan": "",
                    "risk_evaluation": "",
                    "verdict": "ERROR",
                    "error": "An unexpected error occurred while processing your request.",
                }
            ), 500

        parsed = _extract_json(raw_output)
        if parsed is None:
            logger.error("[%s] Could not parse model output as JSON", request_id)
            return jsonify(
                {
                    "research": "",
                    "plan": "",
                    "risk_evaluation": "",
                    "verdict": "ERROR",
                    "error": "The analysis service returned an unexpected response format.",
                }
            ), 502

        return jsonify(parsed), 200

    # -------------------------------------------------------------------
    # Error handlers — never let Flask's default HTML error pages or
    # tracebacks reach the client; always return clean JSON.
    # -------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(_exc):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(413)
    def payload_too_large(_exc):
        return jsonify({"error": "Request body too large."}), 413

    @app.errorhandler(429)
    def rate_limited(_exc):
        return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429

    @app.errorhandler(500)
    def internal_error(exc):
        logger.exception("Unhandled server error: %s", exc)
        return jsonify({"error": "Internal server error."}), 500

    return app


def _extract_json(raw_output: str) -> dict | None:
    """Safely extracts the first top-level JSON object from model output.

    Models are instructed to return ONLY JSON, but defensively handles
    stray markdown fences or surrounding text just in case.
    """
    if not raw_output:
        return None

    text = raw_output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    # Ensure expected keys exist so the frontend never hits `undefined`.
    return {
        "research": parsed.get("research", ""),
        "plan": parsed.get("plan", ""),
        "risk_evaluation": parsed.get("risk_evaluation", ""),
        "verdict": parsed.get("verdict", "WATCH"),
    }


app = create_app()


if __name__ == "__main__":
    # Local development only. In production this app is served by gunicorn
    # (see Procfile / start command), which ignores this __main__ block.
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=Config.DEBUG,
        use_reloader=False,  # avoid the OneDrive/watchdog restart loop
    )
