"""
Agent pipeline definition.

Architecture preserved exactly as designed:

    Research Agent -> Planner Agent -> Evaluator Agent

Do not flatten this into a single-agent call — the sequential structure is
intentional (each agent's output becomes context for the next), and the
evaluator's strict-JSON contract is what the Flask layer depends on.
"""

from __future__ import annotations

import logging

from google.adk.agents import LlmAgent, SequentialAgent

from agents.model_manager import get_primary_model
from agents.tools import fetch_growth_stocks, fetch_price_history

# Note: tools.py also exposes fetch_stocks_by_factor (growth/value/momentum
# ranked lists with returns). It's deliberately NOT wired into
# research_agent.tools below, to keep the agent's tool surface minimal and
# match the original architecture exactly. It's available as a ready-made
# extension point if you later want the Research Agent to answer broader
# "what should I look at" style questions — just add it to the `tools=[...]`
# list below and it will work as-is (it's already validated and tested).

logger = logging.getLogger("ai_finance_analyst.pipeline")

# Resolved once, lazily, the first time this module is imported — NOT at
# process start before Flask is up. Importing this module is still cheap
# (pure string lookup), only the LlmAgent objects below get built once.
MODEL_ID = get_primary_model()

# ======================
# RESEARCH AGENT
# ======================
research_agent = LlmAgent(
    name="research_agent",
    model=MODEL_ID,
    description="Collects market research and stock context.",
    instruction=(
        "You are a financial research analyst.\n"
        "Analyze the user's question.\n"
        "If the question is about a specific stock, use price history.\n"
        "If the question asks for growth stocks, list relevant tickers.\n\n"
        "Return a concise research summary in plain text."
    ),
    tools=[fetch_price_history, fetch_growth_stocks],
)


# ======================
# PLANNER AGENT
# ======================
planner_agent = LlmAgent(
    name="planner_agent",
    model=MODEL_ID,
    description="Creates an investment plan.",
    instruction=(
        "You receive a research summary.\n"
        "Create a clear investment plan including:\n"
        "- Thesis\n"
        "- Time horizon\n"
        "- Entry strategy\n"
        "- Exit strategy\n\n"
        "Return plain text only."
    ),
)


# ======================
# EVALUATOR AGENT (STRICT JSON)
# ======================
evaluator_agent = LlmAgent(
    name="evaluator_agent",
    model=MODEL_ID,
    description="Final evaluator that MUST return valid JSON only.",
    instruction=(
        "You are the FINAL agent.\n"
        "You MUST output ONLY valid JSON.\n"
        "NO markdown, NO explanations, NO extra text.\n\n"
        "Return exactly this JSON schema:\n"
        "{\n"
        '  "research": "summary of findings",\n'
        '  "plan": "investment plan",\n'
        '  "risk_evaluation": "key risks",\n'
        '  "verdict": "BUY | WATCH | AVOID"\n'
        "}\n\n"
        "Even if information is limited, still return valid JSON."
    ),
)


# ======================
# PIPELINE
# ======================
root_agent = SequentialAgent(
    name="financial_pipeline",
    description="Research -> Planning -> Evaluation",
    sub_agents=[
        research_agent,
        planner_agent,
        evaluator_agent,
    ],
)
