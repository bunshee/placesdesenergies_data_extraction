import os

from src import logger
from src.models.schema import EnergyInvoiceRecord
from src.utils.prompts import EXTRACTION_PROMPT

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
assert GEMINI_API_KEY is not None, "GEMINI_API_KEY is not set in environment"


def extract_invoice_from_text(text: str) -> EnergyInvoiceRecord | None:
    """Call Gemini to parse text into EnergyInvoiceRecord using structured output."""
    api_key = GEMINI_API_KEY
    logger.debug("Calling Gemini for structured extraction (chars={})", len(text))

    from google import genai
    from google.genai.types import GenerateContentConfig

    client = genai.Client(api_key=api_key)
    cfg = GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=EnergyInvoiceRecord,
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            {"role": "user", "parts": [{"text": EXTRACTION_PROMPT}, {"text": text}]}
        ],
        config=cfg,
    )
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
    return record
