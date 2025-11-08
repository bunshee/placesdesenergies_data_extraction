from __future__ import annotations

from enum import Enum
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from src.helpers.extractor import GeminiExtractor
from src.helpers.loader import (
    load_pdf_as_images_bytes,
    extract_text_from_pdf_bytes,
    PDFToMarkdownOCR,
)
from src.models.schema import EnergyInvoiceRecord
from src.utils.logging import init_logger

# Initialize logger and extractor
logger = init_logger()
extractor = GeminiExtractor()
app = FastAPI(title="Energy Invoice Extraction API")


class ExtractionMethod(str, Enum):
    """Extraction method for processing PDFs."""

    TEXT = "text"
    IMAGE = "image"
    OCR = "ocr"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract", response_model=EnergyInvoiceRecord | None)
async def extract(
    file: Annotated[UploadFile, File(description="PDF file")],
    method: Annotated[
        ExtractionMethod,
        Query(
            description="Extraction method: text (direct text extraction), image (Gemini Vision), ocr (Tesseract OCR)"
        ),
    ] = ExtractionMethod.IMAGE,
):
    """Extract structured invoice data from PDF.

    Supports three extraction methods:
    - text: Direct text extraction from PDF (fast, works for text-based PDFs)
    - image: Gemini Vision on rendered PDF pages (best for complex layouts)
    - ocr: Tesseract OCR on rendered PDF pages (for scanned documents)
    """
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    data = await file.read()
    logger.info(
        "Processing PDF file: name={}, size={} bytes, method={}",
        file.filename,
        len(data),
        method.value,
    )

    rec: EnergyInvoiceRecord | None = None

    try:
        if method == ExtractionMethod.TEXT:
            logger.debug("Extracting text directly from PDF")
            text = extract_text_from_pdf_bytes(data)
            if not text or not text.strip():
                logger.warning(
                    "No text could be extracted from file: {}", file.filename
                )
                return None
            rec = extractor.extract_invoice_from_text(text)

        elif method == ExtractionMethod.OCR:
            logger.debug("Extracting text using Tesseract OCR")
            ocr_processor = PDFToMarkdownOCR(lang="fra", dpi=300)
            text = ocr_processor.convert_bytes(data)
            if not text or not text.strip():
                logger.warning(
                    "No text could be extracted via OCR from file: {}", file.filename
                )
                return None
            rec = extractor.extract_invoice_from_text(text)

        elif method == ExtractionMethod.IMAGE:
            logger.debug("Converting PDF to images for Gemini Vision")
            images = load_pdf_as_images_bytes(data)
            if not images:
                logger.warning(
                    "No pages could be extracted from file: {}", file.filename
                )
                return None
            logger.debug(
                "Sending {} page(s) to Gemini Vision for extraction", len(images)
            )
            rec = extractor.extract_invoice_from_images(images)

    except Exception as e:
        logger.error(
            f"An unexpected error occurred during extraction: {e}", exc_info=True
        )
        # Optionally, you could raise an HTTPException here
        # raise HTTPException(status_code=500, detail=f"An error occurred: {e}")
        return None

    if rec is None:
        logger.warning("Extraction failed for file: {}", file.filename)
    else:
        logger.info(
            "/extract: extracted reference={} supplier={} date={}",
            rec.energy_reference,
            rec.supplier,
            rec.document_date,
        )

    return rec
