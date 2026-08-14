"""Turn an uploaded file (pdf / docx / txt / md) into plain text."""
from __future__ import annotations

import io
import unicodedata


def parse_file(filename: str, data: bytes) -> str:
    """Extract text from raw file bytes based on the file extension."""
    name = (filename or "").lower()

    if name.endswith(".pdf"):
        return _parse_pdf(data)
    if name.endswith(".docx"):
        return _parse_docx(data)
    # .txt, .md, .markdown, .text and anything else: treat as UTF-8 text.
    return _parse_text(data)


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return _strip_unprintable("\n\n".join(pages)).strip()


def _strip_unprintable(text: str) -> str:
    # Icon fonts used for bullets/glyphs in slide-deck PDFs extract as
    # private-use/control codepoints that render as tofu boxes; drop them.
    return "".join(
        ch for ch in text
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )


def _parse_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs]
    return "\n".join(paragraphs).strip()


def _parse_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    # Last resort: never fail, just drop undecodable bytes.
    return data.decode("utf-8", errors="replace").strip()
