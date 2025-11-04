from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from src.ingestion.loader import load_pdf_text, load_pdf_text_bytes
from src.extraction.fields import (
    extract_supplier,
    extract_energy_reference,
    extract_postal_and_city,
    extract_siren_siret,
    extract_dates,
    extract_offer_and_segment,
    extract_site_name,
)
from src.normalize.validators import normalize_reference, normalize_city
from src.models.schema import EnergyInvoiceRecord


def is_energy_invoice(text: str) -> bool:
    text_u = text.upper()
    if any(
        k in text_u
        for k in ["EDF", "ENGIE", "GAZ EUROPEEN", "GAZ DE PARIS", "ENEDIS", "GRDF"]
    ):
        if any(
            k in text_u
            for k in ["FACTURE", "FACTURATION", "FACTURE N", "N\u00b0 DE FACTURE"]
        ):
            return True
    # fallback keyword pairs
    return (
        any(k in text_u for k in ["POINT DE LIVRAISON", "PCE", "PDL", "PRM"])
        and "FACT" in text_u
    )


def process_pdf(pdf_path: str | Path) -> tuple[EnergyInvoiceRecord | None, dict]:
    text = load_pdf_text(pdf_path)
    if not text.strip():
        return None, {"reason": "no_text"}
    if not is_energy_invoice(text):
        return None, {"reason": "not_energy_invoice"}

    supplier = extract_supplier(text)
    energy_ref, energy_ref_type = extract_energy_reference(text)
    energy_ref = normalize_reference(energy_ref)

    postal_code, city = extract_postal_and_city(text)
    city = normalize_city(city)

    siren_siret = extract_siren_siret(text)
    doc_date, expiry, start_date = extract_dates(text)
    energy_segment, tags, tariff, regulated = extract_offer_and_segment(text)
    site_name = extract_site_name(text)

    record = EnergyInvoiceRecord(
        document_date=doc_date,
        supplier=supplier,
        site_name=site_name,
        energy_reference=energy_ref,
        energy_reference_type=energy_ref_type,
        energy_reference_length=len(energy_ref) if energy_ref else None,
        postal_code=postal_code,
        city=city,
        energy_segment=energy_segment,
        offer_tags=tags or [],
        tariff_segment=tariff,
        contract_expiry_date=expiry,
        contract_start_date=start_date,
        client_siren_siret=siren_siret,
        regulated_tariff=regulated,
    )

    return record, {"file": str(pdf_path)}


def process_pdf_bytes(pdf_bytes: bytes) -> tuple[EnergyInvoiceRecord | None, dict]:
    text = load_pdf_text_bytes(pdf_bytes)
    if not text.strip():
        return None, {"reason": "no_text"}
    if not is_energy_invoice(text):
        return None, {"reason": "not_energy_invoice"}

    supplier = extract_supplier(text)
    energy_ref, energy_ref_type = extract_energy_reference(text)
    energy_ref = normalize_reference(energy_ref)

    postal_code, city = extract_postal_and_city(text)
    city = normalize_city(city)

    siren_siret = extract_siren_siret(text)
    doc_date, expiry, start_date = extract_dates(text)
    energy_segment, tags, tariff, regulated = extract_offer_and_segment(text)
    site_name = extract_site_name(text)

    record = EnergyInvoiceRecord(
        document_date=doc_date,
        supplier=supplier,
        site_name=site_name,
        energy_reference=energy_ref,
        energy_reference_type=energy_ref_type,
        energy_reference_length=len(energy_ref) if energy_ref else None,
        postal_code=postal_code,
        city=city,
        energy_segment=energy_segment,
        offer_tags=tags or [],
        tariff_segment=tariff,
        contract_expiry_date=expiry,
        contract_start_date=start_date,
        client_siren_siret=siren_siret,
        regulated_tariff=regulated,
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
