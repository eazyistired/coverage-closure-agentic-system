"""Tests for src/agents/base_agent.py.

Unit tests   — mock the LLM; no network calls, no .env required.
Integration  — real LLM call; requires a valid .env.
              Run with:  pytest -m integration
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestBaseAgentInit:

    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_default_system_prompt(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        agent = BaseAgent()

        assert agent.system_prompt == "You are a helpful verification assistant."

    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_custom_system_prompt(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        agent = BaseAgent(system_prompt="Custom prompt.")

        assert agent.system_prompt == "Custom prompt."

    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_tools_default_to_empty_list(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        agent = BaseAgent()

        assert agent.tools == []

    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_llm_receives_env_vars(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        BaseAgent()

        call_kwargs = mock_llm_cls.call_args.kwargs
        assert call_kwargs["api_key"] == "dummykey"
        assert call_kwargs["base_url"] == "https://fake-llm.example.com"
        assert call_kwargs["model"] == "gpt-test"

    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_model_name_override(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        BaseAgent(model_name="gpt-custom")

        assert mock_llm_cls.call_args.kwargs["model"] == "gpt-custom"

    @patch("src.agents.base_agent.load_dotenv")
    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_missing_api_key_raises(
        self, mock_llm_cls, mock_create, mock_load_dotenv, monkeypatch
    ):
        from src.agents.base_agent import BaseAgent

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_BASE", "https://fake-llm.example.com")

        with pytest.raises(KeyError):
            BaseAgent()

    @patch("src.agents.base_agent.load_dotenv")
    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_missing_api_base_raises(
        self, mock_llm_cls, mock_create, mock_load_dotenv, monkeypatch
    ):
        from src.agents.base_agent import BaseAgent

        monkeypatch.setenv("OPENAI_API_KEY", "dummykey")
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)

        with pytest.raises(KeyError):
            BaseAgent()

    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    def test_create_agent_called_once(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()
        mock_create.return_value = MagicMock()

        BaseAgent()

        mock_create.assert_called_once()


class TestBaseAgentChat:

    @pytest.mark.asyncio
    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    async def test_chat_returns_last_message(self, mock_llm_cls, mock_create, fake_env):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "messages": [
                    MagicMock(content="first"),
                    MagicMock(content="final answer"),
                ]
            }
        )
        mock_create.return_value = mock_graph

        agent = BaseAgent()
        result = await agent.chat("hello")

        assert result == "final answer"

    @pytest.mark.asyncio
    @patch("src.agents.base_agent.create_agent")
    @patch("src.agents.base_agent.ChatOpenAI")
    async def test_chat_empty_messages_returns_empty_string(
        self, mock_llm_cls, mock_create, fake_env
    ):
        from src.agents.base_agent import BaseAgent

        mock_llm_cls.return_value = MagicMock()

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})
        mock_create.return_value = mock_graph

        agent = BaseAgent()
        result = await agent.chat("hello")

        assert result == ""


# ---------------------------------------------------------------------------
# Integration test — requires valid .env
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_base_agent_real_llm_call():
    """Send a minimal prompt to the real LLM endpoint and verify a non-empty response."""
    from src.agents.base_agent import BaseAgent

    agent = BaseAgent(
        system_prompt="You are a helpful assistant. Answer in one sentence."
    )
    response = await agent.chat("Reply with: setup ok")

    assert isinstance(response, str)
    assert len(response) > 0
    print(f"\n[integration] LLM response: {response}")
