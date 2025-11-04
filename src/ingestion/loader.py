from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF


def iter_pdf_pages_text(pdf_path: str | Path) -> Iterator[tuple[int, str]]:
    """Yield (page_index, text) for each page using PyMuPDF extraction.

    Notes:
        - Only supports PDFs (no OCR). For scanned PDFs without text layer, result may be empty.
    """
    path = Path(pdf_path)
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            # Use text extraction with blocks order for better layout coherence
            text = page.get_text("text")  # or "blocks" -> join; keep simple first
            yield i, text or ""


def load_pdf_text(pdf_path: str | Path) -> str:
    return "\n".join(text for _, text in iter_pdf_pages_text(pdf_path))


def iter_pdf_pages_text_bytes(pdf_bytes: bytes) -> Iterator[tuple[int, str]]:
    """Yield (page_index, text) for each page from PDF bytes using PyMuPDF."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            yield i, text or ""


def load_pdf_text_bytes(pdf_bytes: bytes) -> str:
    return "\n".join(text for _, text in iter_pdf_pages_text_bytes(pdf_bytes))
