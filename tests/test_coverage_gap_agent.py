"""Unit and integration tests for CoverageGapAgent and middleware."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unit tests — no network, no LLM
# ---------------------------------------------------------------------------


class TestCoverageGapAgent:
    @patch("src.agents.base_agent.load_dotenv")
    @patch("src.agents.base_agent.ChatOpenAI")
    @patch("src.agents.base_agent.create_agent")
    def test_inherits_base_agent(self, mock_create, mock_llm, mock_dotenv, fake_env):
        """CoverageGapAgent is a BaseAgent subclass."""
        from src.agents.base_agent import BaseAgent
        from src.agents.coverage_gap_agent import CoverageGapAgent

        agent = CoverageGapAgent()
        assert isinstance(agent, BaseAgent)

    @patch("src.agents.base_agent.load_dotenv")
    @patch("src.agents.base_agent.ChatOpenAI")
    @patch("src.agents.base_agent.create_agent")
    def test_system_prompt_loaded_from_context(
        self, mock_create, mock_llm, mock_dotenv, fake_env
    ):
        """System prompt comes from coverage_gap_agent_context.md."""
        from src.agents.coverage_gap_agent import CoverageGapAgent

        agent = CoverageGapAgent()
        assert (
            "uncovered" in agent.system_prompt.lower()
            or "coverage" in agent.system_prompt.lower()
        )

    @patch("src.agents.base_agent.load_dotenv")
    @patch("src.agents.base_agent.ChatOpenAI")
    @patch("src.agents.base_agent.create_agent")
    def test_tools_passed_through(self, mock_create, mock_llm, mock_dotenv, fake_env):
        """Tools provided at construction are stored on the agent."""
        from src.agents.coverage_gap_agent import CoverageGapAgent

        fake_tool = MagicMock()
        fake_tool.name = "fake_mcp_tool"
        agent = CoverageGapAgent(tools=[fake_tool])
        assert len(agent.tools) == 1
        assert agent.tools[0].name == "fake_mcp_tool"

    @patch("src.agents.base_agent.load_dotenv")
    @patch("src.agents.base_agent.ChatOpenAI")
    @patch("src.agents.base_agent.create_agent")
    def test_no_tools_defaults_to_empty(
        self, mock_create, mock_llm, mock_dotenv, fake_env
    ):
        from src.agents.coverage_gap_agent import CoverageGapAgent

        agent = CoverageGapAgent()
        assert agent.tools == []


class TestMiddleware:
    def test_get_all_wps_returns_list(self):
        """get_all_wps() reads sett.yaml and returns WP names."""
        from src.middleware import get_all_wps

        wps = get_all_wps()
        assert isinstance(wps, list)
        assert len(wps) > 0
        assert all(isinstance(w, str) for w in wps)

    def test_get_all_wps_includes_known_wps(self):
        from src.middleware import get_all_wps

        wps = get_all_wps()
        assert "CAN" in wps
        assert "DFT" in wps

    @patch("src.middleware._analyze_wp", new_callable=AsyncMock)
    def test_run_analysis_calls_analyze_per_wp(self, mock_analyze):
        """run_analysis fires one _analyze_wp task per WP."""
        mock_analyze.return_value = {"wp": "CAN", "raw_response": '{"wp": "CAN"}'}

        from src.middleware import run_analysis

        asyncio.run(run_analysis(["CAN"], "inputs/test.report"))
        mock_analyze.assert_called_once()
        call_args = mock_analyze.call_args
        assert call_args[0][0] == "CAN"

    @patch("src.middleware._analyze_wp", new_callable=AsyncMock)
    def test_run_analysis_handles_exception_per_wp(self, mock_analyze):
        """A failure in one WP does not crash the others."""
        mock_analyze.side_effect = [
            RuntimeError("MCP unreachable"),
            {"wp": "SPI", "raw_response": '{"wp": "SPI"}'},
        ]

        from src.middleware import run_analysis

        saved = asyncio.run(run_analysis(["CAN", "SPI"], "inputs/test.report"))
        # CAN failed, SPI saved
        assert len(saved) == 1

    def test_extract_json_valid(self):
        from src.middleware import _extract_json

        raw = 'Sure! Here is the result: {"wp": "CAN", "covergroups": []} done.'
        result = _extract_json(raw, "CAN")
        assert result["wp"] == "CAN"

    def test_extract_json_invalid_falls_back(self):
        from src.middleware import _extract_json

        raw = "This is not JSON at all."
        result = _extract_json(raw, "CAN")
        assert result["wp"] == "CAN"
        assert "raw_response" in result


# ---------------------------------------------------------------------------
# Integration test — requires MCP server running + valid .env
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_tools_integration():
    """Connect to the live MCP server and verify tool discovery.

    Requires: MCP server running on MCP_SERVER_URL (default http://localhost:8080/mcp).
    """
    import os
    from dotenv import load_dotenv
    from langchain_mcp_adapters.client import MultiServerMCPClient

    load_dotenv()
    mcp_url = os.getenv("MCP_SERVER_URL", "http://localhost:8080/mcp")

    client = MultiServerMCPClient(
        {"coverage-gap-analyzer": {"transport": "sse", "url": mcp_url}}
    )
    tools = await client.get_tools()

    tool_names = [t.name for t in tools]
    assert len(tools) > 0, "No tools returned from MCP server"
    assert any(
        "wps" in n or "covergroup" in n or "bins" in n for n in tool_names
    ), f"Expected coverage tools, got: {tool_names}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_wp_analysis():
    """End-to-end: MCP server + LLM — analyze one WP and verify JSON output.

    Requires: MCP server running, valid .env with LLM credentials.
    """
    import os
    from dotenv import load_dotenv
    from src.middleware import run_analysis, get_all_wps

    load_dotenv()

    report = next(Path("inputs").glob("*.report"), None)
    assert report is not None, "No .report file found in inputs/"

    wps = get_all_wps()
    assert wps, "No WPs in sett.yaml"

    # Run on just the first WP to keep the test fast
    saved = await run_analysis([wps[0]], str(report))

    assert len(saved) == 1, "Expected one report to be saved"
    assert saved[0].exists()

    with open(saved[0], encoding="utf-8") as f:
        data = json.load(f)
    assert "wp" in data
