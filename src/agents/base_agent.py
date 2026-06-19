"""Base LangChain agent for the Coverage Gap Analyzer.

All feature agents (e.g. CoverageGapAgent) extend BaseAgent.
LLM connection parameters are loaded exclusively from environment variables.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, List
from uuid import UUID

import httpx
from dotenv import load_dotenv
from langchain.agents import AgentState, create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class _CallbackHandler(BaseCallbackHandler):
    """Lightweight callback that logs tool start/end at WARNING level."""

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        tool_name = serialized.get("name", "unknown")
        preview = input_str[:200] + ("..." if len(input_str) > 200 else "")
        logger.warning("[AGENT] Tool started: %s | input: %s", tool_name, preview)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        content = str(getattr(output, "content", output))
        preview = content[:200] + ("..." if len(content) > 200 else "")
        logger.warning("[AGENT] Tool ended | output: %s", preview)


class BaseAgent:
    """Base LangGraph agent.

    Subclasses pass ``tools`` and ``system_prompt`` via kwargs; everything
    else (LLM connection, temperature) is read from environment variables.

    Environment variables
    ---------------------
    OPENAI_API_KEY   : API token for the LLM endpoint (Bearer scheme).
    OPENAI_API_BASE  : Base URL of the LLM API (e.g. https://gpt4ifx.icp.infineon.com).
    LLM_MODEL        : Model name (e.g. gpt-5.2).  Overridable per-instance.
    SSL_CA_BUNDLE    : Path (relative to project root) to the CA bundle .crt file.
                       Set VERIFY_SSL=false to disable verification entirely.
    """

    # Models that reject the temperature parameter (fixed temperature on server side).
    _NO_TEMPERATURE_MODELS = frozenset(
        {
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-5.3-chat",
        }
    )

    def __init__(
        self,
        tools: List[BaseTool] | None = None,
        system_prompt: str = "You are a helpful verification assistant.",
        model_name: str | None = None,
        temperature: float | None = 0,
    ) -> None:
        load_dotenv()

        api_key = os.environ["OPENAI_API_KEY"]
        base_url = os.environ["OPENAI_API_BASE"]
        model = model_name or os.getenv("LLM_MODEL", "gpt-5.2")

        _project_root = Path(__file__).resolve().parents[2]
        if os.getenv("VERIFY_SSL", "").lower() == "false":
            ssl_verify: bool | str = False
        elif ca_bundle := os.getenv("SSL_CA_BUNDLE"):
            ssl_verify = str(_project_root / ca_bundle)
        else:
            ssl_verify = True

        # Build kwargs — omit temperature for models that reject it
        llm_kwargs: dict = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "default_headers": {"Authorization": f"Bearer {api_key}"},
            "http_client": httpx.Client(verify=ssl_verify),
            "http_async_client": httpx.AsyncClient(verify=ssl_verify),
        }
        if model not in self._NO_TEMPERATURE_MODELS and temperature is not None:
            llm_kwargs["temperature"] = temperature

        self.llm = ChatOpenAI(**llm_kwargs)

        self.tools: List[BaseTool] = tools or []
        self.system_prompt = system_prompt
        self._agent_config = {
            "callbacks": [_CallbackHandler()],
        }
        self._graph = self._build_graph()

    def _build_graph(self):
        return create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
        )

    async def chat(self, prompt: str) -> str:
        """Send *prompt* to the agent and return the final response text."""
        state = AgentState(messages=[HumanMessage(content=prompt)])
        response = await self._graph.ainvoke(state, config=self._agent_config)
        messages = response.get("messages", [])
        return messages[-1].content if messages else ""
