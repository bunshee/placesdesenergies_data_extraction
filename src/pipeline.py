from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from src.ingestion.loader import load_pdf_text, load_pdf_text_bytes
from src.models.schema import EnergyInvoiceRecord
from src.genai.extractor import extract_invoice_from_text
from loguru import logger


def is_energy_invoice(_: str) -> bool:
    # Delegated to the LLM; retained for compatibility
    return True


def process_pdf(pdf_path: str | Path) -> tuple[EnergyInvoiceRecord | None, dict]:
    logger.info("Processing PDF file: {}", pdf_path)
    text = load_pdf_text(pdf_path)
    if not text.strip():
        logger.warning("No text extracted from {}", pdf_path)
        return None, {"reason": "no_text"}
    record = extract_invoice_from_text(text)
    if record is None:
        logger.info("Model judged not an energy invoice: {}", pdf_path)
        return None, {"reason": "not_energy_invoice"}
    if record.energy_reference:
        record.energy_reference_length = len(record.energy_reference)
        logger.info(
            "Extracted ref={} supplier={} date={}",
            record.energy_reference,
            record.supplier,
            record.document_date,
        )
    return record, {"file": str(pdf_path)}


def process_pdf_bytes(pdf_bytes: bytes) -> tuple[EnergyInvoiceRecord | None, dict]:
    logger.debug("Processing PDF bytes: size={} bytes", len(pdf_bytes))
    text = load_pdf_text_bytes(pdf_bytes)
    if not text.strip():
        logger.warning("No text extracted from bytes input")
        return None, {"reason": "no_text"}
    record = extract_invoice_from_text(text)
    if record is None:
        logger.info("Model judged not an energy invoice for bytes input")
        return None, {"reason": "not_energy_invoice"}
    if record.energy_reference:
        record.energy_reference_length = len(record.energy_reference)
        logger.debug(
            "Extracted ref={} supplier={} date={}",
            record.energy_reference,
            record.supplier,
            record.document_date,
        )
    return record, {"file": "bytes"}


def deduplicate_latest(
    records: Iterable[EnergyInvoiceRecord],
) -> list[EnergyInvoiceRecord]:
    latest_by_ref: dict[str, EnergyInvoiceRecord] = {}

    def parse_date(d: str | None) -> tuple[int, int, int]:
        if not d:
            return (0, 0, 0)
        # Expect DD/MM/YYYY or DD-MM-YYYY as first pass
        try:
            parts = d.replace("-", "/").split("/")
            if len(parts) == 3:
                dd, mm, yy = parts
                yy = yy if len(yy) == 4 else ("20" + yy[-2:])
                return (int(yy), int(mm), int(dd))
        except Exception:
            return (0, 0, 0)
        return (0, 0, 0)

    for rec in records:
        ref = rec.energy_reference
        if not ref:
            continue
        cur = latest_by_ref.get(ref)
        if cur is None or parse_date(rec.document_date) > parse_date(cur.document_date):
            latest_by_ref[ref] = rec
    return list(latest_by_ref.values())


def records_to_frames(records: list[EnergyInvoiceRecord]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in records])
