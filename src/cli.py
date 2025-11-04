import argparse
import asyncio
import os
from io import BytesIO
from pathlib import Path
from typing import List

from google.genai.errors import ServerError

from src.helpers.excel_exporter import export_to_excel
from src.helpers.extractor import extract_invoice_from_text
from src.helpers.loader import load_pdf_text_bytes
from src.models.schema import EnergyInvoiceRecord
from src.utils.logging import init_logger

logger = init_logger()


async def _process_pdf_file(pdf_path: Path) -> EnergyInvoiceRecord | None:
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    text = load_pdf_text_bytes(pdf_bytes)

    if not text.strip():
        logger.warning(f"No text could be extracted from file: {pdf_path.name}")
        return None

    rec = None
    retries = 3
    for i in range(retries):
        try:
            rec = extract_invoice_from_text(text)
            if rec:
                break
        except ServerError as e:
            logger.error(f"Gemini ServerError: {e}")

        logger.warning(
            f"LLM extraction failed for file: {pdf_path.name}. Retrying in 60 seconds (attempt {i + 1}/{retries})",
            pdf_path.name,
            i + 1,
            retries,
        )
        await asyncio.sleep(60)

    if rec is None:
        logger.warning(
            f"LLM extraction failed after multiple retries for file: {pdf_path.name}"
        )
    return rec


async def main(input_folder: Path, output_file: Path):
    extracted_data: List[EnergyInvoiceRecord] = []
    pdf_files: List[Path] = []

    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(Path(root) / file)

    logger.info(f"Found {len(pdf_files)} PDF files to process.")

    for pdf_file in pdf_files:
        logger.info(f"Processing PDF file: {pdf_file.name}")
        record = await _process_pdf_file(pdf_file)
        if record:
            extracted_data.append(record)

    if not extracted_data:
        logger.warning("No data extracted from provided files.")
        return

    output_buffer = BytesIO()
    export_to_excel(extracted_data, output_buffer)
    output_buffer.seek(0)

    with open(output_file, "wb") as f:
        f.write(output_buffer.read())
    logger.info(f"Successfully extracted data to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process PDF files in batch and extract energy invoice data."
    )
    parser.add_argument(
        "--input_folder",
        type=Path,
        required=True,
        help="Path to the folder containing PDF files.",
    )
    parser.add_argument(
        "--output_file",
        type=Path,
        required=True,
        help="Path to the output XLSX file.",
    )
    args = parser.parse_args()

    asyncio.run(main(args.input_folder, args.output_file))
