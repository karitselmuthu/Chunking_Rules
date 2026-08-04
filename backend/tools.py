"""Agent-ready tool layer over the chunking functions.

Each strategy in ``chunkers.py`` is already a standalone function. This module
wraps them as *tools*: a JSON-schema description plus a uniform dispatcher, so
any agent framework can discover and call them.

Quick use
---------
    from tools import TOOL_SCHEMAS, call_tool, anthropic_tools

    # 1) Framework-agnostic: list of {name, description, input_schema}
    TOOL_SCHEMAS

    # 2) Call one by name with keyword args (validated, JSON-serialisable out)
    result = call_tool("fixed_size_chunk", text="...", chunk_size=800)

    # 3) Hand straight to Claude's Messages API:
    client.messages.create(model=..., tools=anthropic_tools(), messages=[...])

Each tool name is the function name (e.g. ``semantic_chunk``), so an agent can
map a tool-use request directly onto ``call_tool(name, **arguments)``.
"""
from __future__ import annotations

from typing import Any

import chunkers
import privacy

MAX_TEXT_CHARS = chunkers.MAX_TEXT_CHARS
MAX_CHUNK_SIZE = chunkers.MAX_CHUNK_SIZE
MAX_SOURCE_NAME_CHARS = chunkers.MAX_SOURCE_NAME_CHARS


def _validate_tool_arguments(arguments: dict[str, Any]) -> None:
    """Enforce a basic safety guardrail before any tool executes."""
    text = arguments.get("text")
    if text is not None:
        if not isinstance(text, str):
            raise ValueError("text must be a string.")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(
                f"Input text too large: {len(text)} chars exceeds the {MAX_TEXT_CHARS}-char guardrail."
            )
        scan = privacy.scan_text(text)
        if scan["has_pii"] or scan["has_pci"]:
            raise ValueError(
                "Input contains PII/PCI-like content and is blocked by the privacy guardrail before tool execution."
            )

    chunk_size = arguments.get("chunk_size")
    if chunk_size is not None:
        if not isinstance(chunk_size, int) or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer.")
        if chunk_size > MAX_CHUNK_SIZE:
            raise ValueError(
                f"chunk_size too large: {chunk_size} exceeds the {MAX_CHUNK_SIZE}-char guardrail."
            )

    chunk_overlap = arguments.get("chunk_overlap")
    if chunk_overlap is not None:
        if not isinstance(chunk_overlap, int) or chunk_overlap < 0:
            raise ValueError("chunk_overlap must be a non-negative integer.")
        if chunk_size is not None and chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be >= 0 and < chunk_size.")

    source_name = arguments.get("source_name", "document")
    if source_name is not None:
        if not isinstance(source_name, str):
            raise ValueError("source_name must be a string.")
        if len(source_name) > MAX_SOURCE_NAME_CHARS:
            raise ValueError(
                f"source_name too long: {len(source_name)} exceeds the {MAX_SOURCE_NAME_CHARS}-char guardrail."
            )


# name -> callable. The tool name is exactly the function name.
TOOL_FUNCTIONS: dict[str, Any] = {
    fn.__name__: fn for fn in chunkers.STRATEGY_FUNCTIONS.values()
}

# strategy id -> tool (function) name, for callers that think in ids.
STRATEGY_ID_TO_TOOL: dict[str, str] = {
    sid: fn.__name__ for sid, fn in chunkers.STRATEGY_FUNCTIONS.items()
}


def _input_schema(strategy_id: str) -> dict:
    d = chunkers.STRATEGIES[strategy_id]["defaults"]
    return {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The raw document text to split into chunks.",
                "maxLength": MAX_TEXT_CHARS,
            },
            "chunk_size": {
                "type": "integer",
                "description": "Target/maximum chunk size in characters.",
                "default": d["chunk_size"],
                "minimum": 1,
                "maximum": MAX_CHUNK_SIZE,
            },
            "chunk_overlap": {
                "type": "integer",
                "description": "Overlap between consecutive chunks in characters (must be < chunk_size).",
                "default": d["chunk_overlap"],
                "minimum": 0,
            },
            "source_name": {
                "type": "string",
                "description": "Name/label of the source document, used in metadata.",
                "default": "document",
                "maxLength": MAX_SOURCE_NAME_CHARS,
            },
        },
        "required": ["text"],
    }


def _build_schemas() -> list[dict]:
    schemas: list[dict] = []
    for sid, fn in chunkers.STRATEGY_FUNCTIONS.items():
        schemas.append(
            {
                "name": fn.__name__,
                "strategy_id": sid,
                "description": chunkers.STRATEGIES[sid]["description"],
                "input_schema": _input_schema(sid),
            }
        )
    return schemas


# Framework-agnostic tool descriptors: {name, strategy_id, description, input_schema}.
TOOL_SCHEMAS: list[dict] = _build_schemas()


def call_tool(name: str, **arguments: Any) -> dict:
    """Invoke a chunking tool by name. ``name`` may be the function name
    (``"fixed_size_chunk"``) or the strategy id (``"fixed"``)."""
    if name in STRATEGY_ID_TO_TOOL:  # allow calling by strategy id too
        name = STRATEGY_ID_TO_TOOL[name]
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        raise ValueError(f"Unknown tool '{name}'. Valid tools: {', '.join(TOOL_FUNCTIONS)}")

    _validate_tool_arguments(arguments)
    return fn(**arguments)


def anthropic_tools() -> list[dict]:
    """Tool definitions shaped for Claude's Messages API (`tools=`)."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in TOOL_SCHEMAS
    ]


if __name__ == "__main__":
    # Tiny self-demo: list tools and run one.
    print(f"{len(TOOL_SCHEMAS)} chunking tools available:")
    for t in TOOL_SCHEMAS:
        print(f"  - {t['name']:24s} ({t['strategy_id']})")
    demo = call_tool("recursive_chunk", text="Hello world. " * 40, chunk_size=120, chunk_overlap=20)
    print(f"\nrecursive_chunk demo -> {demo['chunk_count']} chunks, "
          f"avg {demo['stats']['avg_chars']} chars")
