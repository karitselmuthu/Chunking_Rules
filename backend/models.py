"""Pydantic response schemas for the API."""
from __future__ import annotations

from pydantic import BaseModel


class StrategyInfo(BaseModel):
    id: str
    name: str
    description: str
    chunk_size: int
    chunk_overlap: int
    needs: str | None = None  # "embeddings" | "llm" | None


class Chunk(BaseModel):
    index: int
    text: str
    char_count: int
    token_estimate: int
    metadata: dict = {}
    children: list["Chunk"] | None = None


class Stats(BaseModel):
    strategy: str
    strategy_name: str
    chunk_count: int
    total_chars: int
    avg_chars: float
    min_chars: int
    max_chars: int
    total_tokens_estimate: int
    chunk_size: int
    chunk_overlap: int


class ChunkResponse(BaseModel):
    stats: Stats
    chunks: list[Chunk]
