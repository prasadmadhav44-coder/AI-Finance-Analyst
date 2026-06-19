"""
Pipeline execution entrypoint used by the Flask layer.

Wraps the ADK runner with:
  - retry + exponential backoff for transient errors (503 overloaded, 5xx)
  - model fallback across the configured chain when a model is persistently
    unavailable or quota-exhausted (429)
  - structured logging instead of bare prints, so this is usable in a real
    production log aggregator (Render logs, etc.)

Public contract: run_once(question) -> str (raw text from the evaluator
agent, expected to contain a JSON object). The Flask route is responsible
for parsing that JSON; this module's job is just "get me a response from
the model, working around transient provider issues."
"""

from __future__ import annotations

import logging

from google.adk.runners import InMemoryRunner
from google.genai import types

from agents.model_manager import (
    call_with_retry,
    get_fallback_chain,
    is_quota_error,
    is_retryable_error,
)
from agents.pipeline import root_agent

logger = logging.getLogger("ai_finance_analyst.runner")

APP_NAME = "financial-agent"
USER_ID = "local-user"


class PipelineError(RuntimeError):
    """Raised when the agent pipeline could not produce a response after
    exhausting retries and the model fallback chain."""


async def _run_pipeline_once(question: str, *, model_id: str | None = None) -> str:
    """Runs the full agent pipeline once and returns the final agent output.

    If model_id is provided, it temporarily overrides every sub-agent's
    model for this run — used by the fallback loop in run_once().
    """
    original_models: list[str] | None = None
    if model_id:
        original_models = [agent.model for agent in root_agent.sub_agents]
        for agent in root_agent.sub_agents:
            agent.model = model_id

    try:
        runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)

        session = await runner.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
        )

        content = types.Content(
            role="user",
            parts=[types.Part(text=question)],
        )

        final_text = ""
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session.id,
            new_message=content,
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    final_text = event.content.parts[0].text or ""

        if not final_text:
            raise PipelineError("Pipeline completed but produced no final response.")

        return final_text
    finally:
        if original_models is not None:
            for agent, original in zip(root_agent.sub_agents, original_models):
                agent.model = original


async def run_once(question: str) -> str:
    """Runs the pipeline with retry-with-backoff, then falls back across the
    configured model chain if a given model keeps failing.

    Raises PipelineError if every model in the chain fails. Callers (the
    Flask route) should catch this and return a clean error response rather
    than letting a raw exception/traceback reach the client.
    """
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")

    chain = get_fallback_chain()
    last_exc: BaseException | None = None

    for index, model_id in enumerate(chain):
        is_last_model = index == len(chain) - 1
        try:
            logger.info("Running pipeline with model=%s", model_id)
            return await call_with_retry(
                lambda mid=model_id: _run_pipeline_once(question, model_id=mid),
                max_attempts=3,
                base_delay_seconds=1.0,
                max_delay_seconds=8.0,
                on_retry=lambda attempt, exc: logger.warning(
                    "Retry %s for model=%s after error: %s", attempt, model_id, exc
                ),
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            last_exc = exc
            if is_quota_error(exc):
                logger.error("Quota exhausted for model=%s, trying next in chain.", model_id)
            elif is_retryable_error(exc):
                logger.error(
                    "Model=%s still failing after retries, trying next in chain.", model_id
                )
            else:
                # Non-transient error (bad request, auth failure, etc.) —
                # switching models won't help, so fail fast instead of
                # burning through the whole chain.
                logger.exception("Non-retryable error from model=%s", model_id)
                raise PipelineError(str(exc)) from exc

            if is_last_model:
                break

    raise PipelineError(
        f"All models in fallback chain exhausted. Last error: {last_exc}"
    ) from last_exc
