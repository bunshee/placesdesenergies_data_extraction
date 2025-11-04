from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
import asyncio
from src.helpers.extractor import extract_invoice_from_text
from src.helpers.loader import load_pdf_text_bytes
from src.models.schema import EnergyInvoiceRecord
from src.utils.logging import init_logger

# Initialize logger
logger = init_logger()
app = FastAPI(title="Energy Invoice Extraction API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract", response_model=EnergyInvoiceRecord | None)
async def extract(
    file: Annotated[UploadFile, File(description="PDF file")],
):
    """Extract structured invoice data from PDF.

    Pipeline: PDF -> pymupdf text extraction -> LLM -> structured output
    """
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    data = await file.read()
    logger.info("Processing PDF file: name={}, size={} bytes", file.filename, len(data))

    # Step 1: Extract text from PDF using pymupdf
    text = load_pdf_text_bytes(data)

    if not text.strip():
        logger.warning("No text could be extracted from file: {}", file.filename)
        return None

    # Step 2: Pass text to LLM with prompt for structured extraction
    logger.debug("Sending text to LLM for extraction ({} chars)", len(text))

    rec = None
    retries = 3
    for i in range(retries):
        rec = extract_invoice_from_text(text)
        if rec:
            break
        logger.warning(
            "LLM extraction failed for file: {}. Retrying in 60 seconds (attempt {}/{})",
            file.filename,
            i + 1,
            retries,
        )
        await asyncio.sleep(60)

    if rec is None:
        logger.warning("LLM extraction failed for file: {}", file.filename)
    else:
        logger.info(
            "/extract: extracted reference={} supplier={} date={}",
            rec.energy_reference,
            rec.supplier,
            rec.document_date,
        )

    return rec
