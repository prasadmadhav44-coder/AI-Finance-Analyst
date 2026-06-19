"""
Centralized configuration, loaded once from environment variables.

Fails fast: if a required setting is missing in production, the app should
refuse to start rather than fail mysteriously on the first real request.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


class Config:
    # Environment
    ENV: str = os.getenv("FLASK_ENV", "production")
    IS_PRODUCTION: bool = ENV != "development"
    DEBUG: bool = _env_bool("FLASK_DEBUG", default=not IS_PRODUCTION)

    # Secrets / API keys
    GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")

    # Flask secret key — required for session/cookie signing if ever used.
    # In production, generate with: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")

    # CORS — comma-separated list of allowed origins. Defaults to "same
    # origin only" (empty) rather than "*", since this app is server-rendered
    # (templates + same-origin fetch) and has no legitimate cross-origin
    # caller by default.
    ALLOWED_ORIGINS: list[str] = _env_list("ALLOWED_ORIGINS", default=[])

    # Rate limiting (requests per window, per client IP) for the /analyze
    # endpoint specifically, since each call is expensive (multiple LLM
    # calls + yfinance fetches).
    ANALYZE_RATE_LIMIT: str = os.getenv("ANALYZE_RATE_LIMIT", "10 per minute")
    GLOBAL_RATE_LIMIT: str = os.getenv("GLOBAL_RATE_LIMIT", "200 per hour")

    # Request constraints
    MAX_QUERY_LENGTH: int = int(os.getenv("MAX_QUERY_LENGTH", "1000"))
    MAX_CONTENT_LENGTH_BYTES: int = int(
        os.getenv("MAX_CONTENT_LENGTH_BYTES", str(16 * 1024))
    )  # 16 KB is generous for a single text query

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls) -> list[str]:
        """Returns a list of human-readable problems with the current
        configuration. Does not raise — caller decides how strict to be
        (e.g. app.py warns in dev, can be made to hard-fail in prod)."""
        problems: list[str] = []

        if not cls.GOOGLE_API_KEY:
            problems.append(
                "GOOGLE_API_KEY is not set. Gemini calls will fail until "
                "this is configured in the environment (.env locally, or "
                "the platform's secret/env var manager in production)."
            )

        if cls.IS_PRODUCTION and not cls.SECRET_KEY:
            problems.append(
                "SECRET_KEY is not set in production. Set a strong random "
                "value via the SECRET_KEY environment variable."
            )

        if cls.IS_PRODUCTION and cls.DEBUG:
            problems.append(
                "FLASK_DEBUG is enabled while FLASK_ENV=production. This "
                "exposes the interactive debugger and stack traces to "
                "anyone who can trigger a 500 — never run this combination "
                "in production."
            )

        return problems
