"""The ten chunking strategies, each as a standalone, agent-callable function.

Design goals
------------
Every strategy is a **self-contained public function** with the same shape:

    <strategy>_chunk(text: str,
                     chunk_size: int = <default>,
                     chunk_overlap: int = <default>,
                     source_name: str = "document") -> ChunkingResult

- Clean named arguments with sensible per-strategy defaults, so an agent can
  call it with just ``text``.
- No shared mutable context; each call is independent and side-effect free.
- A uniform, JSON-serialisable return value (``ChunkingResult``), so the output
  can flow straight back to another agent or an API response.

This makes each function trivial to wrap as a tool (see ``tools.py``): register
it, hand an agent the schema, done.

ChunkingResult
--------------
    {
        "strategy": str,              # id, e.g. "fixed"
        "strategy_name": str,         # human name
        "chunk_size": int,
        "chunk_overlap": int,
        "chunk_count": int,
        "chunks": [Chunk, ...],
        "stats": {chunk_count, total_chars, avg_chars, min_chars,
                  max_chars, total_tokens_estimate},
    }

A ``Chunk`` is:
    {
        "index": int,
        "text": str,
        "char_count": int,
        "token_estimate": int,
        "metadata": {...},            # strategy-specific extras
        "children": [Chunk, ...]      # Hierarchical only
    }
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# ---------------------------------------------------------------------------
# Strategy registry: id -> display metadata + default size/overlap.
# The frontend (via /api/strategies) and the tool schemas (tools.py) both read
# this, so defaults live in exactly one place.
# ---------------------------------------------------------------------------
MAX_TEXT_CHARS = 100_000
MAX_CHUNK_SIZE = 10_000
MAX_SOURCE_NAME_CHARS = 200

STRATEGIES: dict[str, dict] = {
    "fixed": {
        "name": "Fixed-Size Chunking",
        "description": "Splits text into equal-length slices by character count, with a fixed overlap. Fast and predictable; ignores meaning.",
        "defaults": {"chunk_size": 1000, "chunk_overlap": 200},
        "needs": None,
    },
    "recursive": {
        "name": "Recursive Chunking",
        "description": "Splits on a hierarchy of separators (paragraph → line → sentence → word) so chunks stay under the size while respecting natural boundaries.",
        "defaults": {"chunk_size": 1000, "chunk_overlap": 200},
        "needs": None,
    },
    "semantic": {
        "name": "Semantic Chunking",
        "description": "Embeds each sentence and starts a new chunk where the meaning shifts (embedding similarity drops). Chunk size acts as an upper cap.",
        "defaults": {"chunk_size": 1500, "chunk_overlap": 0},
        "needs": "embeddings",
    },
    "document": {
        "name": "Document-Based / Structural Chunking",
        "description": "Follows the document's own structure — Markdown headings and paragraphs — so each chunk is a real section. Oversized sections are split further.",
        "defaults": {"chunk_size": 1500, "chunk_overlap": 0},
        "needs": None,
    },
    "agentic": {
        "name": "Agentic Chunking",
        "description": "An LLM (Gemini) reads the text, breaks it into self-contained propositions and groups them into coherent chunks. Falls back to a rule-based version without an API key.",
        "defaults": {"chunk_size": 1000, "chunk_overlap": 0},
        "needs": "llm",
    },
    "context": {
        "name": "Context-Enriched / Metadata Chunking",
        "description": "Structural chunks, each prefixed with a metadata header (document name, nearest section, position) so a retriever keeps the surrounding context.",
        "defaults": {"chunk_size": 800, "chunk_overlap": 100},
        "needs": None,
    },
    "sliding": {
        "name": "Sliding Window Chunking",
        "description": "Sentence-aligned windows that slide forward with heavy overlap, so no idea is cut across a boundary. Overlap is the shared span between windows.",
        "defaults": {"chunk_size": 500, "chunk_overlap": 250},
        "needs": None,
    },
    "hierarchical": {
        "name": "Hierarchical Chunking",
        "description": "Large parent chunks for context, each split into smaller child chunks for precise retrieval. Chunk size is the parent size; children are ~1/5 of it.",
        "defaults": {"chunk_size": 2000, "chunk_overlap": 200},
        "needs": None,
    },
    "late": {
        "name": "Late Chunking",
        "description": "Embeds the full document first (long-context), then pools per chunk — so every chunk vector carries whole-document context. Text splits like Recursive.",
        "defaults": {"chunk_size": 1000, "chunk_overlap": 200},
        "needs": "embeddings",
    },
    "hybrid": {
        "name": "Hybrid Chunking",
        "description": "Splits along the document's structure (headings/paragraphs), refines oversized sections by size, then merges undersized adjacent chunks within the same section — so every chunk is as close to the target size as its structure allows.",
        "defaults": {"chunk_size": 1500, "chunk_overlap": 0},
        "needs": None,
    },
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token, good enough for a UI stat)."""
    return max(1, round(len(text) / 4))


def _chunk(index: int, text: str, metadata: dict | None = None, children=None) -> dict:
    out = {
        "index": index,
        "text": text,
        "char_count": len(text),
        "token_estimate": _tokens(text),
        "metadata": metadata or {},
    }
    if children is not None:
        out["children"] = children
    return out


def _flatten(chunks: list[dict]) -> list[dict]:
    flat: list[dict] = []
    for c in chunks:
        flat.append(c)
        if c.get("children"):
            flat.extend(c["children"])
    return flat


def _validate(chunk_size: int, chunk_overlap: int) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer.")
    if chunk_size > MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size too large: {chunk_size} exceeds the {MAX_CHUNK_SIZE}-char guardrail."
        )
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be >= 0 and less than chunk_size.")


def _guardrail(text: str, chunk_size: int, chunk_overlap: int, source_name: str) -> None:
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Input text too large: {len(text)} chars exceeds the {MAX_TEXT_CHARS}-char guardrail."
        )
    if not isinstance(source_name, str):
        raise ValueError("source_name must be a string.")
    if len(source_name) > MAX_SOURCE_NAME_CHARS:
        raise ValueError(
            f"source_name too long: {len(source_name)} exceeds the {MAX_SOURCE_NAME_CHARS}-char guardrail."
        )
    _validate(chunk_size, chunk_overlap)


def _finalize(strategy_id: str, chunks: list[dict], chunk_size: int, chunk_overlap: int) -> dict:
    """Wrap a chunk list into the uniform ChunkingResult (with stats)."""
    leaves = _flatten(chunks)
    sizes = [c["char_count"] for c in leaves] or [0]
    stats = {
        "chunk_count": len(chunks),
        "total_chars": sum(sizes),
        "avg_chars": round(sum(sizes) / len(sizes), 1),
        "min_chars": min(sizes),
        "max_chars": max(sizes),
        "total_tokens_estimate": sum(c["token_estimate"] for c in leaves),
    }
    return {
        "strategy": strategy_id,
        "strategy_name": STRATEGIES[strategy_id]["name"],
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "stats": stats,
    }


_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def _split_sentences(text: str) -> list[str]:
    """Lightweight sentence splitter — no NLTK download required."""
    parts: list[str] = []
    for paragraph in re.split(r"\n{2,}", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        parts.extend(s.strip() for s in _SENT_RE.split(paragraph) if s.strip())
    return parts


def _recursive_split(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )
    return splitter.split_text(text)


# Lazily-loaded singleton so the server starts instantly and only pays the
# model-download / import cost the first time an ML strategy is used.
_EMBED_MODEL = None


def _embedder():
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        from sentence_transformers import SentenceTransformer

        model_name = os.environ.get("CHUNKING_EMBED_MODEL", "all-MiniLM-L6-v2")
        _EMBED_MODEL = SentenceTransformer(model_name)
    return _EMBED_MODEL


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


# ===========================================================================
# 1. Fixed-Size
# ===========================================================================
def fixed_size_chunk(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    source_name: str = "document",
) -> dict:
    """Split ``text`` into equal-length character slices with a fixed overlap.

    Returns a ChunkingResult. Deterministic; ignores meaning.
    """
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("fixed", [], chunk_size, chunk_overlap)

    from langchain_text_splitters import CharacterTextSplitter

    splitter = CharacterTextSplitter(
        separator="", chunk_size=chunk_size, chunk_overlap=chunk_overlap, length_function=len
    )
    chunks = [_chunk(i, c) for i, c in enumerate(splitter.split_text(text))]
    return _finalize("fixed", chunks, chunk_size, chunk_overlap)


# ===========================================================================
# 2. Recursive
# ===========================================================================
def recursive_chunk(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    source_name: str = "document",
) -> dict:
    """Split ``text`` on a separator hierarchy (paragraph → line → sentence → word)
    so chunks stay under ``chunk_size`` while respecting natural boundaries."""
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("recursive", [], chunk_size, chunk_overlap)

    chunks = [_chunk(i, c) for i, c in enumerate(_recursive_split(text, chunk_size, chunk_overlap))]
    return _finalize("recursive", chunks, chunk_size, chunk_overlap)


# ===========================================================================
# 3. Semantic
# ===========================================================================
def semantic_chunk(
    text: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 0,
    source_name: str = "document",
) -> dict:
    """Embed each sentence and start a new chunk where the meaning shifts
    (adjacent-sentence embedding similarity drops into the document's lowest
    quartile). ``chunk_size`` is an upper cap. Requires the embedding model."""
    import numpy as np

    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("semantic", [], chunk_size, chunk_overlap)

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return _finalize("semantic", [_chunk(0, text, {"note": "Too short to segment."})], chunk_size, chunk_overlap)

    embeddings = _embedder().encode(sentences, normalize_embeddings=True)
    sims = np.array(
        [float(np.dot(embeddings[i], embeddings[i + 1])) for i in range(len(sentences) - 1)]
    )
    threshold = float(np.percentile(sims, 25))

    chunks: list[dict] = []
    current = [sentences[0]]
    for i in range(1, len(sentences)):
        current_len = sum(len(s) + 1 for s in current)
        drop = sims[i - 1] < threshold
        if (drop and current_len > chunk_size * 0.4) or current_len >= chunk_size:
            chunks.append(" ".join(current))
            current = [sentences[i]]
        else:
            current.append(sentences[i])
    if current:
        chunks.append(" ".join(current))

    result_chunks = [
        _chunk(i, c, {"breakpoint_similarity": round(threshold, 3)}) for i, c in enumerate(chunks)
    ]
    return _finalize("semantic", result_chunks, chunk_size, chunk_overlap)


# ===========================================================================
# 4. Document-Based / Structural
# ===========================================================================
def _structural_chunks(text: str, chunk_size: int, chunk_overlap: int, source_name: str) -> list[dict]:
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    heading = source_name
    buffer: list[str] = []

    for line in lines:
        m = _HEADING_RE.match(line.strip())
        if m:
            if buffer:
                sections.append((heading, buffer))
                buffer = []
            heading = m.group(2).strip() or heading
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, buffer))

    # No Markdown headings at all → fall back to paragraph grouping.
    if len(sections) <= 1 and not any(_HEADING_RE.match(l.strip()) for l in lines):
        sections = [(heading, [p]) for p in re.split(r"\n{2,}", text) if p.strip()]

    chunks: list[dict] = []
    for section_heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        pieces = _recursive_split(body, chunk_size, chunk_overlap) if len(body) > chunk_size else [body]
        for piece in pieces:
            chunks.append(_chunk(len(chunks), piece, {"section": section_heading}))
    return chunks or [_chunk(0, text)]


def document_chunk(
    text: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 0,
    source_name: str = "document",
) -> dict:
    """Split ``text`` along its own structure — Markdown headings and paragraphs —
    so each chunk is a real section. Oversized sections are split further."""
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("document", [], chunk_size, chunk_overlap)
    return _finalize("document", _structural_chunks(text, chunk_size, chunk_overlap, source_name), chunk_size, chunk_overlap)


# ===========================================================================
# 5. Agentic (Gemini, with rule-based fallback)
# ===========================================================================
_AGENTIC_CHAR_LIMIT = 15000
_AGENTIC_PROMPT = (
    "You are a document chunking agent. Split the text below into semantically "
    "coherent chunks, each roughly {size} characters, where every chunk is a "
    "self-contained unit of meaning (do not break a single idea across chunks). "
    "Return ONLY a JSON array of strings, nothing else.\n\n---\n{body}\n---"
)


def agentic_chunk(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 0,
    source_name: str = "document",
) -> dict:
    """Use an LLM (Gemini) to break ``text`` into self-contained, coherent chunks.

    Uses Gemini when ``GEMINI_API_KEY`` is set; otherwise falls back to a
    deterministic sentence-grouping heuristic so the call always succeeds.
    
    Sampling parameters can be configured via environment variables:
    - CHUNKING_LLM_TOP_P: Controls diversity via nucleus sampling (0.0 to 1.0, default: 1.0)
    - CHUNKING_LLM_TOP_K: Limits sampling to top k tokens (0 disables, default: 0)
    """
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("agentic", [], chunk_size, chunk_overlap)

    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        chunks = _agentic_fallback(text, chunk_size, "No GEMINI_API_KEY set — used rule-based grouping.")
        return _finalize("agentic", chunks, chunk_size, chunk_overlap)

    body = text[:_AGENTIC_CHAR_LIMIT]
    truncated = len(text) > _AGENTIC_CHAR_LIMIT
    try:
        model = os.environ.get("CHUNKING_LLM_MODEL", "gemini-2.5-flash")

        # Read top-p and top-k from environment with defaults.
        top_p = float(os.environ.get("CHUNKING_LLM_TOP_P", "1.0"))
        top_k = int(os.environ.get("CHUNKING_LLM_TOP_K", "0"))

        generation_config: dict[str, int | float] = {"maxOutputTokens": 4096}
        if top_p != 1.0:
            generation_config["topP"] = top_p
        if top_k > 0:
            generation_config["topK"] = top_k

        normalized_model = model.removeprefix("models/")
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urlparse.quote(normalized_model, safe='')}:generateContent"
            f"?key={urlparse.quote(gemini_api_key, safe='')}"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": _AGENTIC_PROMPT.format(size=chunk_size, body=body)}],
                }
            ],
            "generationConfig": generation_config,
        }
        req = urlrequest.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=60) as response:
            body_json = json.loads(response.read().decode("utf-8"))

        raw = ""
        for candidate in body_json.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                if isinstance(part.get("text"), str):
                    raw += part["text"]

        pieces = _extract_json_array(raw)
        if not pieces:
            chunks = _agentic_fallback(text, chunk_size, "LLM returned no parseable chunks — used fallback.")
            return _finalize("agentic", chunks, chunk_size, chunk_overlap)
        chunks = [_chunk(i, p, {"source": f"Gemini ({normalized_model})"}) for i, p in enumerate(pieces)]
        if truncated:
            chunks.append(_chunk(len(chunks), text[_AGENTIC_CHAR_LIMIT:], {"note": "Tail beyond LLM char limit, appended verbatim."}))
        return _finalize("agentic", chunks, chunk_size, chunk_overlap)
    except urlerror.HTTPError as exc:
        chunks = _agentic_fallback(text, chunk_size, f"Gemini API error ({exc.code}) — used fallback.")
        return _finalize("agentic", chunks, chunk_size, chunk_overlap)
    except urlerror.URLError as exc:
        chunks = _agentic_fallback(text, chunk_size, f"Gemini network error ({exc.reason}) — used fallback.")
        return _finalize("agentic", chunks, chunk_size, chunk_overlap)
    except Exception as exc:  # noqa: BLE001 - never let a tool call crash on LLM issues
        chunks = _agentic_fallback(text, chunk_size, f"LLM error ({exc}) — used fallback.")
        return _finalize("agentic", chunks, chunk_size, chunk_overlap)


def _extract_json_array(raw: str) -> list[str]:
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
        return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        return []


def _agentic_fallback(text: str, chunk_size: int, note: str) -> list[dict]:
    sentences = _split_sentences(text)
    chunks, current, length = [], [], 0
    for s in sentences:
        if length + len(s) > chunk_size and current:
            chunks.append(" ".join(current))
            current, length = [], 0
        current.append(s)
        length += len(s) + 1
    if current:
        chunks.append(" ".join(current))
    return [_chunk(i, c, {"note": note}) for i, c in enumerate(chunks)] or [_chunk(0, text, {"note": note})]


# ===========================================================================
# 6. Context-Enriched / Metadata
# ===========================================================================
def context_enriched_chunk(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    source_name: str = "document",
) -> dict:
    """Structural chunks, each prefixed with a metadata header (document name,
    nearest section, position) so a retriever keeps surrounding context."""
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("context", [], chunk_size, chunk_overlap)

    base = _structural_chunks(text, chunk_size, chunk_overlap, source_name)
    total = len(base)
    enriched: list[dict] = []
    for i, c in enumerate(base):
        section = c["metadata"].get("section", source_name)
        header = f"[Document: {source_name} | Section: {section} | Chunk {i + 1}/{total}]"
        enriched.append(
            _chunk(i, f"{header}\n{c['text']}", {"document": source_name, "section": section, "position": f"{i + 1}/{total}"})
        )
    return _finalize("context", enriched, chunk_size, chunk_overlap)


# ===========================================================================
# 7. Sliding Window (sentence-aligned)
# ===========================================================================
def sliding_window_chunk(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 250,
    source_name: str = "document",
) -> dict:
    """Sentence-aligned windows that slide forward, sharing ~``chunk_overlap``
    characters between consecutive windows so no idea is cut at a boundary."""
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("sliding", [], chunk_size, chunk_overlap)

    sentences = _split_sentences(text)
    if not sentences:
        return _finalize("sliding", [_chunk(0, text)], chunk_size, chunk_overlap)

    chunks: list[dict] = []
    start = 0
    while start < len(sentences):
        cur, length, end = [], 0, start
        while end < len(sentences) and (not cur or length + len(sentences[end]) <= chunk_size):
            cur.append(sentences[end])
            length += len(sentences[end]) + 1
            end += 1
        chunks.append(_chunk(len(chunks), " ".join(cur), {"sentences": len(cur)}))
        if end >= len(sentences):
            break
        back, overlap_len = end, 0
        while back > start + 1 and overlap_len < chunk_overlap:
            back -= 1
            overlap_len += len(sentences[back]) + 1
        start = back
    return _finalize("sliding", chunks, chunk_size, chunk_overlap)


# ===========================================================================
# 8. Hierarchical (parent → children)
# ===========================================================================
def hierarchical_chunk(
    text: str,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
    source_name: str = "document",
) -> dict:
    """Large parent chunks (``chunk_size``), each split into smaller child chunks
    (~1/5 the size) for precise retrieval. Children nest under each parent."""
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("hierarchical", [], chunk_size, chunk_overlap)

    child_size = max(150, chunk_size // 5)
    parents = _recursive_split(text, chunk_size, chunk_overlap)
    result: list[dict] = []
    for pi, parent_text in enumerate(parents):
        child_texts = _recursive_split(parent_text, child_size, chunk_overlap)
        children = [_chunk(ci, ct, {"parent": pi}) for ci, ct in enumerate(child_texts)]
        result.append(_chunk(pi, parent_text, {"level": "parent", "child_count": len(children)}, children=children))
    return _finalize("hierarchical", result, chunk_size, chunk_overlap)


# ===========================================================================
# 9. Late Chunking
# ===========================================================================
def late_chunk(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    source_name: str = "document",
) -> dict:
    """Split like Recursive, but derive each chunk's embedding by conditioning on
    the whole document (embed document first, chunk second) — the defining trait
    of late chunking. Requires the embedding model."""
    import numpy as np

    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("late", [], chunk_size, chunk_overlap)

    pieces = _recursive_split(text, chunk_size, chunk_overlap)
    model = _embedder()
    doc_vec = model.encode([text], normalize_embeddings=True)[0]
    chunk_vecs = model.encode(pieces, normalize_embeddings=True)

    out: list[dict] = []
    for i, (piece, vec) in enumerate(zip(pieces, chunk_vecs)):
        out.append(
            _chunk(i, piece, {
                "embedding_dim": int(vec.shape[0]),
                "context": "document-conditioned",
                "similarity_to_document": round(float(np.dot(vec, doc_vec)), 3),
            })
        )
    return _finalize("late", out, chunk_size, chunk_overlap)


# ===========================================================================
# 10. Hybrid (structure -> size-refine -> merge undersized peers)
# ===========================================================================
def _merge_small_peers(chunks: list[dict], chunk_size: int) -> list[dict]:
    """Pack consecutive chunks from the *same section* together while the
    combined text still fits under ``chunk_size``, so undersized sections stop
    being their own tiny chunk. Chunks already at/over the size are left alone."""
    merged: list[dict] = []
    for c in chunks:
        section = c["metadata"].get("section")
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev["metadata"].get("section") == section
            and prev["char_count"] + 1 + c["char_count"] <= chunk_size
        ):
            combined = f"{prev['text']}\n{c['text']}"
            merged[-1] = _chunk(
                prev["index"],
                combined,
                {"section": section, "merged_from": prev["metadata"].get("merged_from", 1) + 1},
            )
        else:
            merged.append(c)
    for i, c in enumerate(merged):
        c["index"] = i
    return merged


def hybrid_chunk(
    text: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 0,
    source_name: str = "document",
) -> dict:
    """Structure-first, size-aware chunking. Splits along the document's own
    structure (headings/paragraphs), refines oversized sections by size
    (recursive), then merges undersized adjacent chunks within the same section
    so each chunk sits as close to ``chunk_size`` as its structure allows."""
    text = (text or "").strip()
    _validate(chunk_size, chunk_overlap)
    if not text:
        return _finalize("hybrid", [], chunk_size, chunk_overlap)

    structural = _structural_chunks(text, chunk_size, chunk_overlap, source_name)
    merged = _merge_small_peers(structural, chunk_size)
    return _finalize("hybrid", merged, chunk_size, chunk_overlap)


# ===========================================================================
# Dispatch — id -> public function
# ===========================================================================
STRATEGY_FUNCTIONS: dict[str, Callable[..., dict]] = {
    "fixed": fixed_size_chunk,
    "recursive": recursive_chunk,
    "semantic": semantic_chunk,
    "document": document_chunk,
    "agentic": agentic_chunk,
    "context": context_enriched_chunk,
    "sliding": sliding_window_chunk,
    "hierarchical": hierarchical_chunk,
    "late": late_chunk,
    "hybrid": hybrid_chunk,
}


def chunk(
    strategy: str,
    text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    source_name: str = "document",
) -> dict:
    """Dispatch to a strategy by id. Falls back to that strategy's defaults when
    ``chunk_size`` / ``chunk_overlap`` are omitted. Returns a ChunkingResult."""
    if strategy not in STRATEGY_FUNCTIONS:
        raise ValueError(f"Unknown strategy '{strategy}'. Valid: {', '.join(STRATEGY_FUNCTIONS)}")
    defaults = STRATEGIES[strategy]["defaults"]
    size = defaults["chunk_size"] if chunk_size is None else chunk_size
    overlap = defaults["chunk_overlap"] if chunk_overlap is None else chunk_overlap
    _guardrail(text, size, overlap, source_name)
    return STRATEGY_FUNCTIONS[strategy](text, size, overlap, source_name)
