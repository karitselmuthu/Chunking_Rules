"""Langflow custom component exposing all ten Chunking Rules strategies.

One component with a Strategy dropdown, instead of one file per strategy —
it just calls into ``backend/tools.py``'s existing ``call_tool`` dispatcher,
so the PII/PCI privacy guardrail and input validation run exactly like they
do for the API and the MCP server.

How to use
----------
1. This file must live one directory level under the components root (e.g.
   ``langflow_components/chunking/chunking_rules_component.py``) — Langflow's
   loader only scans ``<components-path>/<category>/*.py``, not the root
   itself, so a flat file directly in ``langflow_components/`` is silently
   never discovered.
2. Run Langflow pointed at the components root, with ``backend/`` on ``PYTHONPATH``
   (Langflow executes this file's code standalone, so it doesn't inherit
   this repo's normal relative-import setup):

       PYTHONPATH=backend langflow run --components-path langflow_components

3. In the Langflow UI you'll see a **Chunking Rules** node (search "Chunking").
4. Drag it into a flow and connect it to:
   - an Input node (feeds ``text``)
   - a **Strategy** dropdown already on the node itself — pick any of the
     ten: fixed, recursive, semantic, document, agentic, context, sliding,
     hierarchical, late, hybrid (agentic needs ``GEMINI_API_KEY`` in
     ``.env``; semantic/late download the MiniLM embedding model on first use)
   - a downstream node (Vector Store / Output) reading the ``Chunks``
     output's ``data["chunks"]`` and ``data["stats"]``

No need to duplicate this file per strategy — the dropdown swaps strategies
without adding nodes.
"""
from __future__ import annotations

import chunkers
import tools

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
