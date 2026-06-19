"""Integration tests — validate credentials and live LLM API connectivity.

These tests make real network calls to the LLM endpoint configured in .env.
They are intentionally kept separate from unit tests.

Run with:
    pytest -m integration -v -s
"""

from __future__ import annotations

import logging
import os

import pytest
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm() -> ChatOpenAI:
    import httpx
    from pathlib import Path

    api_key = os.environ["OPENAI_API_KEY"]
    _project_root = Path(__file__).resolve().parents[1]
    if os.getenv("VERIFY_SSL", "").lower() == "false":
        ssl_verify: bool | str = False
    elif ca_bundle := os.getenv("SSL_CA_BUNDLE"):
        ssl_verify = str(_project_root / ca_bundle)
    else:
        ssl_verify = True

    return ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-5.2"),
        temperature=0,
        api_key=api_key,
        base_url=os.environ["OPENAI_API_BASE"],
        default_headers={"Authorization": f"Bearer {api_key}"},
        http_client=httpx.Client(verify=ssl_verify),
        http_async_client=httpx.AsyncClient(verify=ssl_verify),
    )


# ---------------------------------------------------------------------------
# Credential / connectivity tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_env_vars_are_present():
    """Fail fast if any required env var is missing before attempting network calls."""
    missing = [
        v
        for v in ("OPENAI_API_KEY", "OPENAI_API_BASE", "LLM_MODEL")
        if not os.getenv(v)
    ]
    assert not missing, f"Missing env vars: {missing}"
    logger.info("All required env vars present.")


@pytest.mark.integration
def test_llm_direct_invoke():
    """Send a single message directly via ChatOpenAI and verify a non-empty reply.

    Fails with an AuthenticationError / HTTP 401 if credentials are wrong.
    Fails with a connection error if the endpoint is unreachable.
    """
    llm = _make_llm()
    logger.info(
        "Sending test prompt to %s  model=%s",
        os.environ["OPENAI_API_BASE"],
        llm.model_name,
    )

    response = llm.invoke(
        [HumanMessage(content="Reply with exactly the text: credentials ok")]
    )

    assert response.content, "LLM returned an empty response"
    logger.info("LLM response: %s", response.content)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_base_agent_chat():
    """End-to-end: instantiate BaseAgent and call chat() against the real endpoint."""
    from src.agents.base_agent import BaseAgent

    agent = BaseAgent(system_prompt="You are a helpful assistant. Be concise.")
    logger.info("BaseAgent instantiated, sending prompt...")

    response = await agent.chat("Reply with exactly the text: agent ok")

    assert (
        isinstance(response, str) and len(response) > 0
    ), "Agent returned empty response"
    logger.info("Agent response: %s", response)
