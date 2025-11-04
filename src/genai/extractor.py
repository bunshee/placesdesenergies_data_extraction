from __future__ import annotations

import os
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from src.models.schema import EnergyInvoiceRecord


def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment")
    return key


PROMPT = (
    "Vous êtes un extracteur pour des factures d'énergie françaises (gaz/électricité). "
    "À partir du texte destiné à la facture, retournez un objet strictement conforme au schéma. "
    "Remplir les champs quand ils existent; sinon utilisez N/A (ou null pour les dates inconnues). "
    "Indiquez is_energy_invoice=false si le document n'est pas une facture d'énergie. "
    "Attention aux PCE/PDL/PRM (14 chiffres), au code postal (5 chiffres), et au fournisseur."
)


def extract_invoice_from_text(
    text: str, accept_non_invoice: bool = False
) -> Optional[EnergyInvoiceRecord]:
    """Call Gemini to parse text into EnergyInvoiceRecord using structured output.

    Returns None if judged not an energy invoice.
    """
    api_key = _get_api_key()
    logger.debug("Calling Gemini for structured extraction (chars={})", len(text))
    logger.debug("Input preview: {}", (text[:400] + "…") if len(text) > 400 else text)

    # Prefer the new google-genai client if available, otherwise fallback to google-generativeai
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig

        client = genai.Client(api_key=api_key)
        cfg = GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EnergyInvoiceRecord,
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": PROMPT}, {"text": text}]}],
            config=cfg,
        )
        # Try to log raw JSON/text if available for debugging
        try:
            raw_json = getattr(resp, "text", None)
            if raw_json:
                logger.debug(
                    "Gemini(raw) preview: {}",
                    (raw_json[:400] + "…") if len(raw_json) > 400 else raw_json,
                )
        except Exception:
            pass
        record: EnergyInvoiceRecord = resp.parsed
        logger.debug("Gemini response parsed via google-genai")
    except Exception:
        logger.warning(
            "google-genai client not available or failed; falling back to google-generativeai"
        )
        # Fallback to older SDK
        import google.generativeai as genai2

        genai2.configure(api_key=api_key)
        model = genai2.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={
                "response_mime_type": "application/json",
                # The older SDK cannot accept a Pydantic class directly, so rely on model schema
            },
            system_instruction=PROMPT,
        )
        resp = model.generate_content(text)
        try:
            data = resp.text  # JSON string
        except Exception:
            data = resp.candidates[0].content.parts[0].text
        if isinstance(data, str):
            logger.debug(
                "Gemini(raw) preview: {}",
                (data[:400] + "…") if len(data) > 400 else data,
            )
        try:
            record = EnergyInvoiceRecord.model_validate_json(data)
        except ValidationError:
            logger.error("ValidationError while parsing Gemini output")
            return None

    if record.is_energy_invoice is False and not accept_non_invoice:
        return None
    return record
