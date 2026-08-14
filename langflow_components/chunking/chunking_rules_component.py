"""Langflow custom component exposing all ten Chunking Rules strategies.

One component with a Strategy dropdown, instead of one file per strategy —
it just calls into ``backend/tools.py``'s existing ``call_tool`` dispatcher,
so the PII/PCI privacy guardrail and input validation run exactly like they
do for the API and the MCP server.

Install into a Langflow instance: point its custom components path at this
directory (or copy the file in), e.g.:

    langflow run --components-path /ABS/PATH/langflow_components
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import chunkers  # noqa: E402
import tools  # noqa: E402

from langflow.custom import Component  # noqa: E402
from langflow.io import DropdownInput, IntInput, MessageTextInput, Output  # noqa: E402
from langflow.schema import Data  # noqa: E402


class ChunkingRulesComponent(Component):
    display_name = "Chunking Rules"
    description = "Split text with any of the ten Chunking Rules strategies (fixed, recursive, semantic, document, agentic, context, sliding, hierarchical, late, hybrid)."
    icon = "scissors"
    name = "ChunkingRules"

    inputs = [
        MessageTextInput(name="text", display_name="Input Text", required=True),
        DropdownInput(
            name="strategy",
            display_name="Strategy",
            options=list(chunkers.STRATEGIES.keys()),
            value="recursive",
        ),
        IntInput(name="chunk_size", display_name="Chunk Size", value=1000),
        IntInput(name="chunk_overlap", display_name="Chunk Overlap", value=200),
        MessageTextInput(name="source_name", display_name="Source Name", value="document"),
    ]

    outputs = [
        Output(display_name="Chunks", name="chunks", method="run_chunking"),
    ]

    def run_chunking(self) -> Data:
        result = tools.call_tool(
            self.strategy,
            text=self.text,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            source_name=self.source_name or "document",
        )
        self.status = f"{result['chunk_count']} chunks ({result['strategy_name']})"
        return Data(data=result)
