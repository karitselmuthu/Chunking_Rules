# Chunking Rules — Gaps & Future Enhancements

> A knowledge base of scenarios the app **does not currently cover**, kept for planning
> next steps. This is deliberately a *gap register* — it does not describe what already
> works. Each entry states the current behaviour, why the gap matters, and a suggested
> direction. Effort tags are rough: **S** (hours), **M** (a day or two), **L** (multi-day).
>
> Grounded against the codebase as of the 10-strategy version (adds `hybrid`).

---

## Legend

| Priority | Meaning |
|---|---|
| 🔴 High | Limits real-world / production use, or a correctness/security concern |
| 🟡 Medium | Notable capability gap; valuable enhancement |
| 🟢 Low | Nice-to-have / polish |

---

## 1. Chunking logic & strategy accuracy

### 1.1 🔴 No real tokenizer — everything is character-based
- **Now:** `chunk_size` / `chunk_overlap` are **character** counts. Token counts are a rough
  `len(text) / 4` estimate (`_tokens` in `chunkers.py`).
- **Why it matters:** Retrieval/embedding pipelines are bounded by *tokens*, not characters.
  Char-based sizing over/under-shoots real model limits, and the "~Tokens" stat can be off by
  a wide margin for code, non-English text, or dense punctuation.
- **Direction:** Add an optional real tokenizer (`tiktoken`, or the HF tokenizer of the chosen
  embedding model). Let `chunk_size` be interpreted as tokens when a `unit: "tokens"` flag is set.
  **Effort: M**

### 1.2 🟡 Overlap is ignored by most structure-aware strategies
- **Now:** `document`, `context`, `semantic`, and `hybrid` default to `chunk_overlap = 0`. The
  merge pass in `hybrid` (`_merge_small_peers`) joins peers but **adds no overlap** between the
  resulting chunks; overlap only takes effect when an oversized section is recursively split.
- **Why it matters:** Boundary context loss — a query matching text near a section edge can miss
  neighbouring context.
- **Direction:** Add a post-pass that prepends the trailing *N* chars/sentences of the previous
  chunk to each chunk (config-gated), for the strategies where it makes sense. **Effort: M**

### 1.3 🟡 Late chunking is an approximation, not true late chunking
- **Now:** `late_chunk` embeds the full document and each chunk **separately**, then reports
  `similarity_to_document`. It does **not** pool token-level embeddings from a single long-context
  forward pass (the defining mechanic of late chunking).
- **Why it matters:** The chunk vectors don't actually carry whole-document context; the label
  oversells the behaviour.
- **Direction:** Use a long-context embedding model that exposes token embeddings (e.g. Jina
  `jina-embeddings-v2/v3`), run one forward pass over the whole doc, then mean-pool the token
  embeddings within each chunk span. **Effort: L**

### 1.4 🟡 Semantic chunking is rigid
- **Now:** Breakpoint threshold is hard-fixed at the 25th percentile of adjacent-sentence
  similarity. No min-chunk-size floor, no configurable sensitivity, no cross-paragraph merging.
- **Why it matters:** One threshold doesn't fit all documents; can produce a few huge or many tiny
  chunks depending on prose style.
- **Direction:** Expose `breakpoint_percentile` and `min_chunk_chars`; optionally support the
  "gradient"/"interquartile" breakpoint methods. **Effort: M**

### 1.5 🟡 Agentic chunking: single call, hard 15k-char truncation
- **Now:** `agentic_chunk` sends at most `_AGENTIC_CHAR_LIMIT = 15000` chars to Claude in one call;
  the tail is appended **verbatim as one chunk**, unprocessed. No validation that returned chunks
  respect `chunk_size`; no retry on malformed JSON beyond a single fallback.
- **Why it matters:** Long documents are only partially "agentically" chunked; the verbatim tail
  can be arbitrarily large.
- **Direction:** Window the document and call the LLM per window (with overlap-aware stitching);
  validate/repair the JSON; optionally batch. **Effort: M–L**

### 1.6 🟢 Hybrid does not merge across sections (by design)
- **Now:** `_merge_small_peers` only merges chunks that share the same `section`. Two short chunks
  under different headings stay separate.
- **Why it matters:** Documents with many one-line headings stay fragmented.
- **Direction:** Optional `cross_section_merge` flag that relaxes the same-section guard and records
  both section names in metadata. (Intentionally left off — see the design note.) **Effort: S**

### 1.7 🟡 English-centric, structure-blind sentence splitting
- **Now:** `_SENT_RE` splits on `.!?` + whitespace + capital. Breaks on abbreviations (`Dr.`,
  `e.g.`, `U.S.`), decimals, and has no support for CJK / scripts without spaced sentences.
- **Why it matters:** Poor chunk boundaries for non-English or technical text; sliding/semantic/
  agentic-fallback all depend on this splitter.
- **Direction:** Optional pluggable splitter (spaCy / PySBD / ICU) behind the same interface.
  **Effort: M**

### 1.8 🟡 No special handling for tables, code blocks, or lists
- **Now:** Markdown tables, fenced code blocks, and lists are split by the same character/sentence
  rules as prose.
- **Why it matters:** A table or code block gets sliced mid-structure, destroying meaning.
- **Direction:** Detect fenced code / table blocks and treat each as an atomic unit (or split on
  logical rows). **Effort: M**

---

## 2. File ingestion & parsing

### 2.1 🔴 Narrow format support
- **Now:** Only `.pdf`, `.docx`, and text-like files (`parsers.py`). Everything else is decoded as
  UTF-8 text.
- **Missing:** `.pptx`, `.xlsx` / `.csv`, `.html`, `.rtf`, `.epub`, `.odt`, `.json`, and images.
- **Direction:** Add per-format parsers; consider a unified extractor (e.g. `unstructured`,
  `Docling`, or `markitdown`) that also preserves structure. **Effort: M–L**

### 2.2 🔴 No OCR — scanned PDFs yield empty text
- **Now:** `_parse_pdf` uses `pypdf.extract_text()`. Image-only / scanned PDFs return little or
  nothing, and the app then errors with "could not extract any text".
- **Direction:** Fall back to OCR (Tesseract / a vision model) when extracted text is near-empty.
  **Effort: L**

### 2.3 🟡 DOCX and PDF drop non-body content
- **Now:** DOCX reads `document.paragraphs` only — **tables, headers/footers, footnotes, and
  comments are dropped**. PDF has no table/column/layout awareness (multi-column PDFs interleave).
- **Direction:** Extract tables (python-docx table API / PDF table libs) and merge in reading order.
  **Effort: M**

### 2.4 🟡 Whole file is read into memory before the size guard applies
- **Now:** `parse_file` fully decodes the upload; the `MAX_TEXT_CHARS` (100k) guardrail is checked
  only **after** parsing. A very large PDF/DOCX can consume memory before truncation/rejection.
- **Direction:** Enforce an upload byte-size limit at the HTTP layer *before* parsing. **Effort: S**

---

## 3. Privacy, security & access control

### 3.1 🔴 PII/PCI scanner is US-centric and pattern-only
- **Now:** `privacy.py` detects emails, US-style phones, US SSNs, and Luhn-valid cards via regex.
- **Missing:** names, physical addresses, dates of birth, IP addresses, passport numbers, IBAN/SWIFT,
  non-US phone formats, API keys / secrets, and any PHI/medical identifiers. No NER.
- **Why it matters:** The guardrail gives a false sense of coverage; lots of PII passes through.
- **Direction:** Add an optional NER-based detector (e.g. Presidio) and country-aware patterns.
  **Effort: M–L**

### 3.2 🟡 Guardrail blocks instead of offering redact-and-proceed
- **Now:** `/api/chunk` **rejects** any input with detected PII/PCI (HTTP 400). Redaction exists
  (`redact_text`, exposed via the MCP `scan_privacy` tool) but is **not wired into the chunk flow**,
  and the error doesn't say *what* or *where* was found.
- **Direction:** Offer a "redact and continue" path in the API + UI; return match locations/types
  (never raw values) so the user can decide. **Effort: M**

### 3.3 🔴 No authentication, rate limiting, or request-size limits on the API
- **Now:** `CORSMiddleware` allows all origins (`allow_origins=["*"]`); there is no auth, no rate
  limiting, and no per-request size cap. The README explains sharing via a public Cloudflare tunnel.
- **Why it matters:** Anyone with the URL can drive the LLM/embedding endpoints (cost/abuse) and
  submit arbitrary uploads.
- **Direction:** Add an API-key or token gate, per-IP rate limiting, tightened CORS, and an upload
  size cap for any non-local deployment. **Effort: M**

### 3.4 🟡 Error responses can leak internal detail
- **Now:** Several handlers surface raw exception text (`500 f"Chunking failed: {exc}"`, parse errors
  include `{exc}`).
- **Direction:** Log full detail server-side; return a generic message + correlation id to clients.
  **Effort: S**

---

## 4. Backend architecture & operations

### 4.1 🟡 Fully stateless — no persistence, history, or export
- **Now:** Each request is independent; nothing is saved. No run history, no result export.
- **Direction:** Optional store (SQLite/Postgres) for runs; a JSON/CSV export endpoint for chunks.
  **Effort: M**

### 4.2 🟡 No "compare strategies" batch endpoint
- **Now:** `/api/chunk` runs exactly one strategy per call.
- **Why it matters:** The core value of the app is *comparing* strategies; that requires N calls today.
- **Direction:** A `/api/compare` endpoint that runs several strategies on one document and returns a
  keyed result set. **Effort: M**

### 4.3 🟡 Synchronous long-running calls, no job queue or progress
- **Now:** Agentic (LLM) and first-run embedding calls block the request. First use of `semantic` /
  `late` downloads the MiniLM model (~90 MB) with no progress signal.
- **Why it matters:** Long requests can hit client/proxy timeouts; the UI just spins.
- **Direction:** Background job + polling/SSE for long strategies; a model warmup on startup.
  **Effort: M–L**

### 4.4 🟡 Embedding model is a lazy global singleton
- **Now:** `_EMBED_MODEL` is initialised on first use (`_embedder()`), with no warmup and no explicit
  concurrency handling around first-load.
- **Direction:** Optional eager warmup at startup; confirm thread-safety under concurrent first hits.
  **Effort: S–M**

### 4.5 🟢 No caching
- **Now:** Identical document+strategy+params re-computes every time (including embeddings).
- **Direction:** Content-hash cache for results and embeddings. **Effort: M**

### 4.6 🟡 No observability
- **Now:** No `/health` / readiness endpoint, no metrics, no structured logging.
- **Direction:** Add health/readiness, request logging, and basic timing metrics. **Effort: S–M**

---

## 5. Frontend & UX

### 5.1 🔴 No retrieval demo (query → which chunk wins)
- **Now:** The app shows *how* text is split, but never demonstrates retrieval — the actual reason
  chunking strategy matters.
- **Why it matters:** Users can't see which strategy retrieves better for a given question.
- **Direction:** Add a query box that embeds the query and highlights the top-k chunks per strategy.
  **Effort: L**

### 5.2 🟡 No side-by-side strategy comparison
- **Now:** One strategy renders at a time; switching replaces the view.
- **Direction:** A comparison layout (columns per strategy) once `/api/compare` (4.2) exists.
  **Effort: M**

### 5.3 🟡 No export / copy of chunks
- **Now:** Chunks are display-only; no download (JSON/CSV/JSONL) and no per-chunk copy button.
- **Direction:** "Download chunks" (incl. a LangChain-`Document`-friendly JSONL) and copy buttons.
  **Effort: S–M**

### 5.4 🟡 Large result sets can freeze the browser
- **Now:** `render()` builds the full chunk list as one HTML string with no pagination/virtualization.
  A 100k-char doc at small chunk sizes = thousands of DOM nodes.
- **Direction:** Virtualize or paginate the chunk list. **Effort: M**

### 5.5 🟢 Only size & overlap are tunable from the UI
- **Now:** Strategy-specific knobs (semantic percentile, hierarchical child ratio, agentic model)
  aren't surfaced.
- **Direction:** Per-strategy advanced settings panel. **Effort: M**

### 5.6 🟢 No overlap/section visualization, no real token count
- **Now:** Overlap regions aren't highlighted; token counts are the char-based estimate.
- **Direction:** Highlight shared spans between consecutive chunks; show real tokenizer counts.
  **Effort: M**

### 5.7 🟢 No first-run model-download feedback, limited a11y/mobile testing
- **Now:** The first semantic/late request can hang silently during model download; accessibility and
  mobile layouts are untested.
- **Direction:** Surface a "downloading model…" state; run an a11y + responsive pass. **Effort: S–M**

---

## 6. RAG completeness (beyond chunking)

### 6.1 🟡 Chunks are never indexed / no vector store
- **Now:** Output is chunks + stats; nothing is embedded-and-stored for retrieval.
- **Direction:** Optional vector-store integration (FAISS/Chroma) to enable the retrieval demo (5.1).
  **Effort: M**

### 6.2 🟡 No evaluation / quality metrics
- **Now:** No way to score strategies (recall@k, boundary quality, redundancy) against a query set.
- **Direction:** A small eval harness over a labelled Q/chunk set. **Effort: L**

### 6.3 🟢 Single-document only
- **Now:** One document per request; no corpus / multi-file ingestion or cross-document dedup.
- **Direction:** Multi-file upload + corpus-level chunk management. **Effort: L**

---

## 7. Testing, packaging & repo hygiene

### 7.1 🔴 No automated tests and no CI
- **Now:** No test suite anywhere in the repo; no CI config.
- **Why it matters:** Every strategy edit (like adding `hybrid`) is verified only by hand.
- **Direction:** `pytest` unit tests per strategy (shape, size bounds, edge cases: empty, single
  sentence, no headings, oversized section) + a CI workflow. **Effort: M**

### 7.2 🟡 No containerization / reproducible deploy
- **Now:** Run instructions are manual `uvicorn` + `cloudflared`; no Dockerfile or compose.
- **Direction:** Dockerfile + compose; pin a lockfile. **Effort: S–M**

### 7.3 🟢 Repo artifacts & missing project files
- **Now:** A stray `path/to/venv/` directory is committed alongside `.venv/`; there is no `.gitignore`,
  no `pyproject.toml`, and no lockfile. (The workspace is not currently a git repo.)
- **Direction:** Remove `path/to/venv/`, add `.gitignore` (ignore `.venv`, `__pycache__`, `.env`),
  add `pyproject.toml` + lockfile, and `git init`. **Effort: S**

### 7.4 🟢 No lint / format / type-check config
- **Now:** No ruff/black/mypy configuration.
- **Direction:** Add ruff + black + mypy and wire into CI. **Effort: S**

---

## Suggested sequencing (if picking up next)

1. **Repo hygiene + tests** (7.1, 7.3) — safety net before further feature work.
2. **Real tokenizer** (1.1) — unblocks accurate sizing and honest token stats.
3. **Retrieval demo + vector store** (5.1, 6.1) — turns the app from "shows splits" into "shows why splits matter."
4. **Compare endpoint + side-by-side UI** (4.2, 5.2) — the natural comparison workflow.
5. **Security hardening** (3.3, 3.4) — before any shared/public deployment.
6. **Broader ingestion + OCR** (2.1, 2.2) — real-world documents.

---

*Maintenance note:* when a gap here is closed, delete its entry (this file is a register of what's
**missing**, not a changelog). Add new gaps as they're discovered.
