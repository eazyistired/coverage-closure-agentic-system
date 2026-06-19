"""CoverageGapAgent — analyses uncovered coverage bins for a single work package.

Extends BaseAgent with the coverage-gap system prompt.
MCP tools (from the running MCP server) are injected by the middleware at
construction time, so this class stays thin and testable.
"""

from __future__ import annotations

import logging
from typing import List

from langchain_core.tools import BaseTool

from src.agents.base_agent import BaseAgent
from src.contexts.context_paths import CONTEXT_PATHS
from src.tools.kg_tool import query_kg_covergroups, search_kg_variables

logger = logging.getLogger(__name__)


def build_kg_tools() -> list[BaseTool]:
    """Return the knowledge-graph tools ready to be passed to CoverageGapAgent."""
    return [query_kg_covergroups, search_kg_variables]


class CoverageGapAgent(BaseAgent):
    """Agent that uses MCP tools to read uncovered bins and reason over them.

    The caller (middleware) is responsible for:
    - Opening the MultiServerMCPClient context.
    - Fetching MCP tools and merging them with KG tools before passing here.
    - Closing the client context after chat() returns.
    """

    def __init__(
        self,
        tools: List[BaseTool] | None = None,
        **kwargs,
    ) -> None:
        system_prompt = CONTEXT_PATHS["coverage_gap_agent"].read_text(encoding="utf-8")
        super().__init__(tools=tools or [], system_prompt=system_prompt, **kwargs)
        logger.debug(
            "CoverageGapAgent ready with %d tool(s): %s",
            len(self.tools),
            [t.name for t in self.tools],
        )
