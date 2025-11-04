from __future__ import annotations

import html as html_lib
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from src.ingestion.loader import load_pdf_text_bytes
from src.models.schema import EnergyInvoiceRecord
from src.pipeline import deduplicate_latest, process_pdf_bytes

app = FastAPI(title="Energy Invoice Extraction API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract", response_model=EnergyInvoiceRecord | None)
async def extract(file: Annotated[UploadFile, File(description="PDF file")]):
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Unsupported media type")
    data = await file.read()
    rec, meta = process_pdf_bytes(data)
    return rec


@app.post("/extract/batch")
async def extract_batch(files: list[UploadFile]):
    results: list[EnergyInvoiceRecord] = []
    for f in files:
        if f.content_type not in {"application/pdf", "application/octet-stream"}:
            continue
        data = await f.read()
        rec, _ = process_pdf_bytes(data)
        if rec is not None:
            results.append(rec)
    dedup = deduplicate_latest(results)
    return JSONResponse(content=[r.model_dump() for r in dedup])


@app.post("/text")
async def pdf_text(
    file: Annotated[UploadFile, File(description="PDF file")],
    format: str = Query(
        "plain",
        pattern="^(plain|json|html)$",
        description="Return format: plain|json|html",
    ),
):
    """Return extracted text from the PDF without field extraction.

    - plain: text/plain body with newlines preserved
    - json: {"text": "..."}
    - html: <pre> wrapped, HTML-escaped appropriately
    """
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Unsupported media type")
    data = await file.read()
    raw_text = load_pdf_text_bytes(data)
    # Unescape any HTML entities that might appear; keep unicode as-is
    unescaped = html_lib.unescape(raw_text)

    if format == "plain":
        return PlainTextResponse(content=unescaped)
    if format == "html":
        # Escape for HTML display then wrap in <pre>
        escaped = html_lib.escape(unescaped)
        return HTMLResponse(content=f"<pre>{escaped}</pre>")
    # default json
    return JSONResponse(content={"text": unescaped})
