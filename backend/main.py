"""FastAPI app: serves the frontend and the chunking API."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import chunkers
import parsers
import privacy
from models import ChunkResponse, StrategyInfo

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="Chunking Rules", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/strategies", response_model=list[StrategyInfo])
def list_strategies() -> list[StrategyInfo]:
    """Strategy list + per-type default size/overlap for the frontend."""
    return [
        StrategyInfo(
            id=sid,
            name=meta["name"],
            description=meta["description"],
            chunk_size=meta["defaults"]["chunk_size"],
            chunk_overlap=meta["defaults"]["chunk_overlap"],
            needs=meta["needs"],
        )
        for sid, meta in chunkers.STRATEGIES.items()
    ]


@app.post("/api/chunk", response_model=ChunkResponse)
async def chunk(
    strategy: str = Form(...),
    chunk_size: int = Form(...),
    chunk_overlap: int = Form(...),
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
) -> ChunkResponse:
    if strategy not in chunkers.STRATEGIES:
        raise HTTPException(400, f"Unknown strategy '{strategy}'.")
    if chunk_size <= 0:
        raise HTTPException(400, "Chunk size must be positive.")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise HTTPException(400, "Chunk overlap must be >= 0 and < chunk size.")

    filename = "Pasted text"
    if file is not None and file.filename:
        data = await file.read()
        try:
            document_text = parsers.parse_file(file.filename, data)
        except Exception as exc:  # noqa: BLE001 — surface parse errors as a clean 422
            raise HTTPException(
                422,
                f"Could not read '{file.filename}'. The file may be corrupt, "
                f"empty, password-protected, or not a real PDF/DOCX ({exc}).",
            ) from exc
        filename = file.filename
    elif text and text.strip():
        document_text = text
    else:
        raise HTTPException(400, "Provide a file or some text to chunk.")

    if not document_text.strip():
        raise HTTPException(422, "Could not extract any text from the input.")

    scan = privacy.scan_text(document_text)
    if scan["has_pii"] or scan["has_pci"]:
        raise HTTPException(
            400,
            "This request contains PII/PCI-like content and is blocked by the privacy guardrail before chunking.",
        )

    try:
        # Each strategy is a standalone function; chunk() dispatches by id and
        # returns a complete, self-contained ChunkingResult (chunks + stats).
        result = chunkers.chunk(strategy, document_text, chunk_size, chunk_overlap, source_name=filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Chunking failed: {exc}") from exc

    stats = {
        "strategy": result["strategy"],
        "strategy_name": result["strategy_name"],
        "chunk_size": result["chunk_size"],
        "chunk_overlap": result["chunk_overlap"],
        **result["stats"],
    }
    return ChunkResponse(stats=stats, chunks=result["chunks"])


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
