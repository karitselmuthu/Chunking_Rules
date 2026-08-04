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
import s3_loader
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
    s3_uri: str | None = Form(None),
    s3_region: str | None = Form(None),
) -> ChunkResponse:
    if strategy not in chunkers.STRATEGIES:
        raise HTTPException(400, f"Unknown strategy '{strategy}'.")
    if chunk_size <= 0:
        raise HTTPException(400, "Chunk size must be positive.")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise HTTPException(400, "Chunk overlap must be >= 0 and < chunk size.")

    has_file = file is not None and bool(file.filename)
    has_text = bool(text and text.strip())
    has_s3 = bool(s3_uri and s3_uri.strip())
    source_count = int(has_file) + int(has_text) + int(has_s3)

    if source_count == 0:
        raise HTTPException(400, "Provide a file, some text, or an S3 URI to chunk.")
    if source_count > 1:
        raise HTTPException(400, "Provide exactly one input source: file, text, or S3 URI.")

    filename = "Pasted text"
    if has_file:
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
    elif has_text:
        document_text = text
    else:
        def _parse_s3_folder(folder_uri: str) -> str:
            try:
                s3_documents = s3_loader.fetch_s3_documents(folder_uri, s3_region)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(422, str(exc)) from exc

            parsed_docs: list[str] = []
            for object_uri, s3_filename, s3_data in s3_documents:
                try:
                    parsed_text = parsers.parse_file(s3_filename, s3_data)
                except Exception as exc:  # noqa: BLE001 — mirror file parse error behavior
                    raise HTTPException(
                        422,
                        f"Could not parse '{object_uri}'. The object may be corrupt, "
                        f"empty, encrypted, or unsupported ({exc}).",
                    ) from exc
                if parsed_text.strip():
                    parsed_docs.append(f"\n\n### Source: {object_uri}\n\n{parsed_text.strip()}")

            if not parsed_docs:
                raise HTTPException(422, f"No extractable text found under '{folder_uri}'.")
            return "".join(parsed_docs).strip()

        s3_uri_value = s3_uri.strip()
        if s3_uri_value.endswith("/"):
            document_text = _parse_s3_folder(s3_uri_value)
            filename = s3_uri_value
        else:
            try:
                s3_filename, s3_data = s3_loader.fetch_s3_document(s3_uri_value, s3_region)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                # Friendly fallback: treat "s3://bucket/prefix" as folder mode when a key is missing.
                if "NoSuchKey" in str(exc):
                    folder_uri = f"{s3_uri_value}/"
                    document_text = _parse_s3_folder(folder_uri)
                    filename = folder_uri
                else:
                    raise HTTPException(422, str(exc)) from exc
            else:
                try:
                    document_text = parsers.parse_file(s3_filename, s3_data)
                except Exception as exc:  # noqa: BLE001 — mirror file parse error behavior
                    raise HTTPException(
                        422,
                        f"Could not parse '{s3_uri_value}'. The object may be corrupt, "
                        f"empty, encrypted, or unsupported ({exc}).",
                    ) from exc
                filename = s3_uri_value

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
