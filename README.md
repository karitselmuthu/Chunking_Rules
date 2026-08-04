# 🧩 Chunking Rules

A single-page app to upload a document (or load one from **Amazon S3**), pick one of **ten chunking strategies**, tune **Chunk Size** and **Chunk Overlap** (each strategy ships sensible defaults), and see exactly how the text is split.

Built with a **FastAPI** backend (real embeddings + real LLM) and a **vanilla-JS** frontend — no build step.

## The ten strategies

| Strategy | What it does | Backed by |
|---|---|---|
| Fixed-Size | Equal character slices with overlap | strings |
| Recursive | Splits on paragraph → line → sentence → word | LangChain |
| Semantic | New chunk where sentence-embedding similarity drops | MiniLM embeddings |
| Document-Based | Follows Markdown headings / sections | strings |
| Agentic | Gemini decomposes text into coherent chunks | **Gemini API** |
| Context-Enriched | Chunks prefixed with document/section metadata | strings |
| Sliding Window | Sentence-aligned overlapping windows | strings |
| Hierarchical | Parent chunks split into nested children | LangChain |
| Late | Embeds whole document first, then pools per chunk | MiniLM embeddings |
| Hybrid | Structure-split, size-refined, then merges undersized peers | LangChain |

## Setup

```bash
cd Chunking_Rules
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Then open **http://localhost:8000**.

- First use of **Semantic** or **Late** downloads the MiniLM model (~90 MB) once.
- **Agentic** uses Gemini when `GEMINI_API_KEY` is set; otherwise it automatically falls back to a rule-based grouping, so the app always works.

## Load documents from S3 (local app → AWS region)

The UI supports direct S3 input:
- Enter an object path like `s3://my-bucket/docs/manual.pdf`
- Or enter a folder/prefix ending with `/`, like `s3://my-bucket/docs/day03_08_26/`
- Optionally provide an AWS region (for example `us-east-1`)

For folder/prefix input, the app loads all supported document objects under that
prefix (`.pdf`, `.docx`, `.txt`, `.md`, `.markdown`, `.text`) and chunks the
combined corpus.

Credential resolution uses the normal AWS SDK chain (`aws configure`, env vars,
IAM role, or `AWS_PROFILE`).

## Share the running app (Cloudflare Tunnel)

To let someone in another location open the UI, expose your local server with a
free [Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) —
no account or signup required.

```bash
# 1) install once (macOS)
brew install cloudflared

# 2) start the app WITHOUT an API key so visitors can't spend your credits
#    (Agentic falls back to rule-based; every other strategy works fully)
cd backend
env -u GEMINI_API_KEY ../.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000

# 3) in a second terminal, open the public tunnel
cloudflared tunnel --url http://localhost:8000
```

`cloudflared` prints a `https://<random>.trycloudflare.com` URL — send that to
your friend. Notes:

- Your Mac must stay awake with **both** commands running; `caffeinate -i`
  prevents sleep while sharing.
- The URL is **temporary** — a new random one is generated on each restart.
- DNS can take a minute or two to propagate; an early "can't find server" just
  means retry shortly.
- Anyone with the link can use it — it's unguessable, fine for a friend, but
  don't post it publicly. Stop sharing by `Ctrl-C`-ing both commands.

For a **stable** URL that survives restarts, create a named tunnel instead
(free, needs a Cloudflare login): see the
[named tunnel guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

## Optional environment variables

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Enables real Agentic chunking | *(unset → rule-based fallback)* |
| `CHUNKING_LLM_MODEL` | Gemini model for Agentic | `gemini-2.5-flash` |
| `CHUNKING_EMBED_MODEL` | sentence-transformers model | `all-MiniLM-L6-v2` |
| `CHUNKING_LLM_TOP_P` | Agentic sampling top-p | `1.0` |
| `CHUNKING_LLM_TOP_K` | Agentic sampling top-k (`0` = off) | `0` |
| `AWS_REGION` | Default AWS region for S3 fetches | *(unset)* |
| `AWS_PROFILE` | Named AWS profile to use for S3 fetches | *(unset)* |

```bash
export GEMINI_API_KEY=AIza...
```

## Each strategy is a standalone, agent-callable function

Every strategy in `backend/chunkers.py` is an independent function with clean
named arguments, per-strategy defaults, and a uniform JSON-serialisable return —
so you can call one directly, or register it as a tool for other agents.

```python
from chunkers import semantic_chunk, chunk

# Call a strategy directly — only `text` is required; the rest default.
result = semantic_chunk("Your document text...", chunk_size=1500)

# Or dispatch by id (defaults fill in when size/overlap are omitted):
result = chunk("hierarchical", "Your text...")

# result = {strategy, strategy_name, chunk_size, chunk_overlap,
#           chunk_count, chunks: [...], stats: {...}}
```

The ten functions: `fixed_size_chunk`, `recursive_chunk`, `semantic_chunk`,
`document_chunk`, `agentic_chunk`, `context_enriched_chunk`,
`sliding_window_chunk`, `hierarchical_chunk`, `late_chunk`, `hybrid_chunk`.

### Use them as agent tools

`backend/tools.py` wraps each function as a tool with a JSON schema and a
dispatcher — ready for any agent framework:

```python
from tools import TOOL_SCHEMAS, call_tool, llm_tools

# Framework-agnostic descriptors: [{name, strategy_id, description, input_schema}]
TOOL_SCHEMAS

# Invoke by tool name or strategy id, with keyword args:
out = call_tool("semantic_chunk", text="...", chunk_size=1200)

# Hand straight to an LLM API that accepts tool schemas:
client.messages.create(model="gemini-2.5-flash", tools=llm_tools(), messages=[...])
# ...then on a tool_use block: call_tool(block.name, **block.input)
```

Run `python backend/tools.py` to list the tools and see a demo call.

## Use from other agents (MCP server)

`backend/mcp_server.py` exposes all ten chunkers as a **Model Context Protocol**
server, so any MCP-compatible agent (Claude Desktop, Claude Code, custom clients)
can discover and call them. Each tool wraps `call_tool`, so the PII/PCI privacy
guardrail runs before any text is chunked.

```bash
# stdio (default) — how Claude Desktop / `claude mcp add` launch it
python backend/mcp_server.py

# streamable HTTP — for remote / networked agents
python backend/mcp_server.py --http --host 127.0.0.1 --port 8765
```

Register it with Claude Code:

```bash
claude mcp add chunking-rules -- /ABS/PATH/.venv/bin/python /ABS/PATH/backend/mcp_server.py
```

Or add it to any client's `mcpServers` config:

```json
{
  "mcpServers": {
    "chunking-rules": {
      "command": "/ABS/PATH/.venv/bin/python",
      "args": ["/ABS/PATH/backend/mcp_server.py"]
    }
  }
}
```

Tools appear as `fixed_size_chunk`, `recursive_chunk`, `semantic_chunk`, … each
taking `text` (required) plus optional `chunk_size` / `chunk_overlap` /
`source_name`, and returning the uniform chunks + stats result.

An eleventh tool, **`scan_privacy`**, lets an agent pre-check text for PII/PCI before
sending it anywhere. It returns `has_pii` / `has_pci` / `is_safe`, per-type
counts, match locations, and (when `redact=True`) a `[REDACTED_*]` copy of the
text — the raw matched values are never returned.

## Feeding it a multi-day corpus

`../Day_Document_Generation/rag_corpus_enterprise/` simulates a RAG corpus that
churns daily: `day1/`, `day2/`, … each a full snapshot (`documents/` +
`manifest.txt`) where documents get added, updated in place, or retired
between days.

`../Day_Document_Generation/reconcile.py` collapses all `dayN/` snapshots into
one authoritative `current/` folder — the input this app's chunkers should
point at, so day-folder logic never leaks into the chunking pipeline:

```bash
cd ../Day_Document_Generation
python3 reconcile.py rag_corpus_enterprise
# -> rag_corpus_enterprise/current/{documents/,manifest.txt}
```

Logic: walk `day1..dayN` in order, keep a `{document_id: file_path}` dict,
and replace it wholesale with each day's manifest — so a later update
overwrites the doc's path, and an id missing from a later manifest drops out
(retired). Whatever's left after the last day is the current state. Re-run
the command any time a new `dayN` folder lands to refresh `current/`.

## Project layout

```
Chunking_Rules/
├── backend/
│   ├── main.py        # FastAPI routes + serves the frontend
│   ├── chunkers.py    # 10 standalone strategy functions + chunk() dispatcher
│   ├── tools.py       # agent-tool wrappers (JSON schemas + call_tool)
│   ├── mcp_server.py  # MCP server exposing the 10 chunkers to agents
│   ├── parsers.py     # pdf / docx / txt / md → text
│   ├── privacy.py     # PII/PCI scan + redaction guardrail
│   └── models.py      # response schemas
├── frontend/
│   └── index.html     # the single-page UI
└── requirements.txt
```
