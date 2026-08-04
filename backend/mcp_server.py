"""MCP server exposing the ten chunking strategies as agent tools.

This wraps the existing ``tools.call_tool`` dispatcher (which already validates
arguments and runs the PII/PCI privacy guardrail) as a Model Context Protocol
server. Any MCP-compatible agent — Claude Desktop, Claude Code, or a custom
client — can discover and call the chunkers with no glue code.

Run
---
    # stdio (default) — for `claude mcp add` / Claude Desktop
    python mcp_server.py

    # streamable HTTP — for remote / networked agents
    python mcp_server.py --http --host 127.0.0.1 --port 8765

Each strategy is registered as a tool named after its chunker function
(``fixed_size_chunk``, ``semantic_chunk``, …). Inputs: ``text`` (required),
``chunk_size``, ``chunk_overlap``, ``source_name`` — each with the strategy's
own sensible defaults. Output is the uniform JSON chunking result
(chunks + stats), identical to what ``call_tool`` returns.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import chunkers
import privacy
import tools

# Load ANTHROPIC_API_KEY etc. from the project .env, matching the FastAPI app,
# so agentic_chunk can use Claude when a key is configured.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

mcp = FastMCP(
    "chunking-rules",
    instructions=(
        "Tools for splitting document text into chunks using ten different "
        "strategies (fixed-size, recursive, semantic, document-based, agentic, "
        "context-enriched, sliding-window, hierarchical, late, hybrid). Pick the "
        "strategy that fits the downstream task; each returns the chunks plus "
        "statistics. Input text is scanned for PII/PCI and rejected if found."
    ),
)


def _register(strategy_id: str, tool_name: str, description: str) -> None:
    """Register one chunker as an MCP tool with its per-strategy defaults."""
    defaults = chunkers.STRATEGIES[strategy_id]["defaults"]

    # Defaults are baked into the signature at def-time from the closure, so
    # each tool advertises the right chunk_size / chunk_overlap in its schema.
    def chunk_tool(
        text: str,
        chunk_size: int = defaults["chunk_size"],
        chunk_overlap: int = defaults["chunk_overlap"],
        source_name: str = "document",
    ) -> dict:
        return tools.call_tool(
            strategy_id,
            text=text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            source_name=source_name,
        )

    chunk_tool.__name__ = tool_name
    chunk_tool.__doc__ = description
    mcp.add_tool(chunk_tool, name=tool_name, description=description)


for _schema in tools.TOOL_SCHEMAS:
    _register(_schema["strategy_id"], _schema["name"], _schema["description"])


@mcp.tool(
    name="scan_privacy",
    description=(
        "Pre-check text for PII/PCI before sending it anywhere (chunkers, other "
        "tools, external services). Detects emails, phone numbers, SSNs, and "
        "Luhn-valid card numbers. Returns whether sensitive content is present, "
        "a count of each type, the location of each match, and — when "
        "redact=True — a safe copy of the text with matches replaced by "
        "[REDACTED_*] placeholders. Raw matched values are never returned."
    ),
)
def scan_privacy(text: str, redact: bool = True) -> dict:
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    if len(text) > tools.MAX_TEXT_CHARS:
        raise ValueError(
            f"Input text too large: {len(text)} chars exceeds the "
            f"{tools.MAX_TEXT_CHARS}-char guardrail."
        )

    scan = privacy.scan_text(text)

    counts: dict[str, int] = {}
    findings = []
    for m in scan["matches"]:
        counts[m["type"]] = counts.get(m["type"], 0) + 1
        # Location + type only — never echo the raw matched value back.
        findings.append(
            {
                "type": m["type"],
                "start": m["start"],
                "end": m["end"],
                "replacement": m["replacement"],
            }
        )

    result = {
        "has_pii": scan["has_pii"],
        "has_pci": scan["has_pci"],
        "is_safe": not (scan["has_pii"] or scan["has_pci"]),
        "counts": counts,
        "findings": findings,
    }
    if redact:
        result["redacted_text"] = privacy.redact_text(text)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunking Rules MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over streamable HTTP instead of stdio.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (with --http).")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (with --http).")
    args = parser.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
