import argparse
import asyncio
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

import pandas as pd
from redis.exceptions import RedisError
from tqdm.asyncio import tqdm

from src.helpers.extractor import GeminiExtractor
from src.helpers.loader import (
    PDFToMarkdownOCR,
    extract_text_from_pdf_bytes,
    extract_text_with_llm,
    load_pdf_as_images_bytes,
)
from src.models.schema import EnergyInvoiceRecord
from src.utils.logging import init_logger
from src.utils.redis_cache import RedisUnavailableError, get_redis_client

logger = init_logger()

# Default configuration
DEFAULT_MAX_CONCURRENT = 15  # Optimized for paid tier
DEFAULT_RETRY_DELAY = 10  # Faster retries on paid tier


class ExtractionMethod(str, Enum):
    """Supported extraction methods for CLI processing."""

    TEXT = "text"
    IMAGE = "image"
    OCR = "ocr"
    LLM = "llm"


class BatchProcessor:
    """High-performance batch processor with concurrent execution and progress tracking."""

    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        method: "ExtractionMethod" = ExtractionMethod.IMAGE,
    ):
        self.max_concurrent = max_concurrent
        self.retry_delay = retry_delay
        self.method = method
        self.extractor = GeminiExtractor()
        self.ocr_processor: Optional[PDFToMarkdownOCR] = (
            PDFToMarkdownOCR(lang="fra", dpi=300)
            if self.method == ExtractionMethod.OCR
            else None
        )

        try:
            self.redis = get_redis_client()
        except RedisUnavailableError as exc:
            raise RuntimeError(
                "Redis is required for caching and WAL persistence."
            ) from exc

        namespace = os.getenv("REDIS_RESULTS_NAMESPACE", "energy_invoices")
        self.redis_processed_refs_key = f"{namespace}:processed_refs"
        self.redis_order_key = f"{namespace}:order"
        self.redis_record_prefix = f"{namespace}:record:"

        # In-memory storage for fast access
        self.results: List[dict] = []
        self.results_lock = asyncio.Lock()
        self.seen_record_keys: set[str] = set()
        self.index_by_source: dict[str, int] = {}
        self.record_key_by_source: dict[str, str] = {}
        self.failed_sources: set[str] = set()
        self.fallback_processed_count: int = 0
        self.fallback_failed_count: int = 0
        self.source_to_path: dict[str, Path] = {}
        self.processed_references: set[str] = set()
        self.skipped_duplicate_references: int = 0

        # Redis-based Write-Ahead Log for crash safety
        self.wal_key: Optional[str] = None
        self.recovered_wal_keys: set[str] = set()

        # Background Excel writer
        self.excel_executor = ThreadPoolExecutor(max_workers=1)

        self.processed_count = 0
        self.failed_count = 0
        self.batch_count = 0

    def _ensure_wal_key(self) -> str:
        """Ensure a Redis WAL key exists for the current run."""
        if self.wal_key is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_suffix = uuid.uuid4().hex[:8]
            self.wal_key = f"wal:{timestamp}:{run_suffix}"
            try:
                self.redis.sadd("wal:index", self.wal_key)
            except RedisError as exc:
                raise RuntimeError(
                    f"Failed to register WAL key {self.wal_key}: {exc}"
                ) from exc
        return self.wal_key

    def _append_to_wal(self, data: dict):
        """Append a record to WAL (crash-safe, minimal latency)."""
        wal_key = self._ensure_wal_key()
        try:
            self.redis.rpush(wal_key, json.dumps(data, default=str))
        except RedisError as exc:
            logger.error("Failed to append record to WAL %s: %s", wal_key, exc)

    def _compute_record_key(self, record: dict) -> str:
        """Compute a stable key to detect duplicate records across restarts."""
        source_file = record.get("source_file")
        if source_file:
            return f"source::{source_file}"

        energy_reference = record.get("energy_reference")
        if energy_reference:
            return f"energy::{energy_reference}"

        # Fallback: deterministic JSON representation
        return json.dumps(record, sort_keys=True, default=str)

    def _get_ocr_processor(self) -> PDFToMarkdownOCR:
        """Lazily instantiate and return the OCR processor."""
        if self.ocr_processor is None:
            self.ocr_processor = PDFToMarkdownOCR(lang="fra", dpi=300)
        return self.ocr_processor

    def _redis_record_key(self, record_identifier: str) -> str:
        return f"{self.redis_record_prefix}{record_identifier}"

    def _build_record_identifier(self, record: dict) -> str:
        reference = record.get("energy_reference")
        if self._is_valid_energy_reference(reference):
            return f"ref::{str(reference).strip().upper()}"

        source = record.get("source_file")
        if source:
            return f"src::{source}"

        existing = record.get("_redis_id")
        if existing:
            return f"id::{existing}"

        generated = uuid.uuid4().hex
        record["_redis_id"] = generated
        return f"id::{generated}"

    def _persist_record_to_redis(
        self,
        record: dict,
        record_identifier: str,
        *,
        previous_identifier: Optional[str] = None,
        previous_reference: Optional[str] = None,
    ) -> None:
        try:
            processed_at = record.get("processed_at")
            score = (
                datetime.fromisoformat(processed_at).timestamp()
                if processed_at
                else datetime.now().timestamp()
            )

            pipeline = self.redis.pipeline()
            pipeline.hset(
                self._redis_record_key(record_identifier),
                mapping={"data": json.dumps(record, default=str)},
            )
            pipeline.zadd(self.redis_order_key, {record_identifier: score})

            reference = record.get("energy_reference")
            if self._is_valid_energy_reference(reference):
                pipeline.sadd(
                    self.redis_processed_refs_key,
                    str(reference).strip().upper(),
                )

            if previous_identifier and previous_identifier != record_identifier:
                pipeline.zrem(self.redis_order_key, previous_identifier)
                pipeline.delete(self._redis_record_key(previous_identifier))

                if self._is_valid_energy_reference(previous_reference):
                    pipeline.srem(
                        self.redis_processed_refs_key,
                        str(previous_reference).strip().upper(),
                    )

            pipeline.execute()

        except RedisError as exc:
            logger.error("Failed to persist record to Redis: %s", exc)

    def _fetch_all_records(self) -> List[dict]:
        try:
            record_ids = self.redis.zrange(self.redis_order_key, 0, -1)
        except RedisError as exc:
            logger.error("Failed to list records from Redis: %s", exc)
            return self.results.copy()

        records: List[dict] = []
        for record_identifier in record_ids:
            try:
                raw = self.redis.hget(self._redis_record_key(record_identifier), "data")
            except RedisError as exc:
                logger.warning(
                    "Failed to read record %s from Redis: %s",
                    record_identifier,
                    exc,
                )
                continue

            if not raw:
                continue

            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.error(
                    "Corrupted JSON for record %s: %s",
                    record_identifier,
                    exc,
                )
                continue

            records.append(record)

        return records

    def _cleanup_wal(self) -> None:
        keys_to_remove = set(self.recovered_wal_keys)
        if self.wal_key:
            keys_to_remove.add(self.wal_key)

        if not keys_to_remove:
            return

        try:
            pipeline = self.redis.pipeline()
            for wal_key in keys_to_remove:
                pipeline.delete(wal_key)
                pipeline.srem("wal:index", wal_key)
            pipeline.execute()
        except RedisError as exc:
            logger.warning("Failed to clean up WAL keys %s: %s", keys_to_remove, exc)

        self.recovered_wal_keys.clear()
        self.wal_key = None

    def _store_result(self, record: dict) -> bool:
        """Store or replace a record in memory and update dedupe tracking.

        Returns:
            bool: True if an existing record was replaced, False if it was new.
        """
        source = record.get("source_file")
        new_key = self._compute_record_key(record)
        replaced = False

        previous_identifier: Optional[str] = None
        previous_reference: Optional[str] = None

        if source and source in self.index_by_source:
            idx = self.index_by_source[source]
            previous_key = self.record_key_by_source.get(source)
            if previous_key:
                self.seen_record_keys.discard(previous_key)

            existing_record = self.results[idx]
            previous_identifier = self._build_record_identifier(existing_record)
            previous_reference = existing_record.get("energy_reference")

            self.results[idx] = record
            replaced = True
        else:
            self.results.append(record)
            idx = len(self.results) - 1

        self.seen_record_keys.add(new_key)

        if source:
            self.index_by_source[source] = idx
            self.record_key_by_source[source] = new_key

        reference = record.get("energy_reference")
        if self._is_valid_energy_reference(reference):
            normalized_ref = str(reference).strip().upper()
            self.processed_references.add(normalized_ref)

        record_identifier = self._build_record_identifier(record)
        self._persist_record_to_redis(
            record,
            record_identifier,
            previous_identifier=previous_identifier,
            previous_reference=previous_reference,
        )

        return replaced

    @staticmethod
    def _is_valid_energy_reference(reference: Optional[str]) -> bool:
        """Validate that the energy reference matches expected patterns."""
        if not reference:
            return False

        ref = str(reference).strip()
        return bool(re.fullmatch(r"\d{14}", ref)) or bool(
            re.fullmatch(r"[A-Za-z0-9]{8}", ref)
        )

    def _collect_invalid_sources(self) -> set[str]:
        """Return the set of PDF sources with invalid or missing energy references."""
        invalid_sources: set[str] = set(self.failed_sources)

        for record in self.results:
            source = record.get("source_file")
            if not source:
                continue
            if not self._is_valid_energy_reference(record.get("energy_reference")):
                invalid_sources.add(source)

        return invalid_sources

    @staticmethod
    def _extract_reference_from_text(text: str) -> Optional[str]:
        if not text:
            return None

        candidates: list[str] = []

        # 14-digit sequences
        candidates.extend(re.findall(r"\b\d{14}\b", text))

        # 8-character alphanumeric containing at least one letter
        candidates.extend(
            match
            for match in re.findall(r"\b[A-Z0-9]{8}\b", text.upper())
            if any(char.isalpha() for char in match)
        )

        if not candidates:
            return None

        return candidates[0]

    def _register_failure(self, source: str, *, is_fallback: bool) -> None:
        """Track failed extractions for potential fallback handling."""
        self.failed_sources.add(source)
        if is_fallback:
            self.fallback_failed_count += 1
        else:
            self.failed_count += 1

    def _recover_from_wal(self) -> None:
        """Recover previously processed records from any WAL log files."""
        wal_keys: List[str]
        try:
            wal_keys = list(self.redis.smembers("wal:index"))
        except RedisError as exc:
            logger.error("Unable to read WAL index from Redis: %s", exc)
            return

        if not wal_keys:
            return

        logger.info(f"Found {len(wal_keys)} WAL segment(s) from previous run")

        for wal_key in wal_keys:
            try:
                recovered = 0
                updated = 0
                entries = self.redis.lrange(wal_key, 0, -1)

                for line_number, entry in enumerate(entries, start=1):
                    if not entry:
                        continue

                    try:
                        record = json.loads(entry)
                    except json.JSONDecodeError as decode_error:
                        logger.error(
                            "Invalid JSON in %s (line %s): %s",
                            wal_key,
                            line_number,
                            decode_error,
                        )
                        continue

                    replaced = self._store_result(record)
                    if replaced:
                        updated += 1
                    else:
                        recovered += 1

                logger.info(
                    f"Recovered {recovered} new record(s) and updated {updated} record(s) from {wal_key}"
                )

                self.recovered_wal_keys.add(wal_key)

            except RedisError as exc:
                logger.error(f"Error recovering from WAL key {wal_key}: {exc}")
            except Exception as exc:  # pragma: no cover
                logger.error(f"Unexpected error recovering from {wal_key}: {exc}")

    def _write_excel_background(self, output_file: Path):
        """Write Excel in background thread (non-blocking)."""
        try:
            records = self._fetch_all_records()
            if not records:
                return

            df = pd.DataFrame(records)

            column_mapping = {
                "site_name": "Nom du site",
                "energy_reference": "Référence Point d'Énergie",
                "address_consumption": "Adresse",
                "postal_code": "Code postal",
                "city": "Commune",
                "energy_segment": "Segment énergie",
                "regulated_tariff": "Tarif reglementé (Oui/Non)",
                "contract_expiry_date": "Date d'échéance du contrat",
                "supplier": "Fournisseur actuel",
                "termination_notice": "Préavis Résiliation",
                "client_siren_siret": "SIREN/SIRET",
                "source_file": "nom pdf",
            }

            available_cols = [col for col in column_mapping.keys() if col in df.columns]
            df_filtered = df[available_cols].copy()
            df_filtered.rename(columns=column_mapping, inplace=True)

            output_file.parent.mkdir(parents=True, exist_ok=True)
            df_filtered.to_excel(output_file, index=False, engine="openpyxl")
            logger.info(f"📊 Excel updated: {len(records)} records")

        except Exception as e:
            logger.error(f"Error writing Excel in background: {e}")

    def _schedule_excel_update(self, output_file: Path):
        """Schedule Excel update in background (non-blocking)."""
        self.excel_executor.submit(self._write_excel_background, output_file)

    def _convert_memory_to_excel(self, output_file: Path):
        """Convert stored data to Excel (final export)."""
        try:
            records = self._fetch_all_records()
            if not records:
                logger.warning("No data to export")
                return

            df = pd.DataFrame(records)

            column_mapping = {
                "site_name": "Nom du site",
                "energy_reference": "Référence Point d'Énergie",
                "address_consumption": "Adresse",
                "postal_code": "Code postal",
                "city": "Commune",
                "energy_segment": "Segment énergie",
                "regulated_tariff": "Tarif reglementé (Oui/Non)",
                "contract_expiry_date": "Date d'échéance du contrat",
                "supplier": "Fournisseur actuel",
                "termination_notice": "Préavis Résiliation",
                "client_siren_siret": "SIREN/SIRET",
                "source_file": "nom pdf",
            }

            available_cols = [col for col in column_mapping.keys() if col in df.columns]
            df_filtered = df[available_cols].copy()
            df_filtered.rename(columns=column_mapping, inplace=True)

            output_file.parent.mkdir(parents=True, exist_ok=True)
            df_filtered.to_excel(output_file, index=False, engine="openpyxl")
            logger.info(f"✅ Exported {len(records)} records to {output_file}")

        except Exception as e:
            logger.error(f"Error converting to Excel: {e}")
            raise
        finally:
            self._cleanup_wal()

    async def process_batch(
        self,
        pdf_files: List[Path],
        output_file: Path,
        fallback_method: Optional[ExtractionMethod] = None,
    ) -> None:
        """Process PDFs with in-memory cache + WAL for crash safety."""
        self.processed_count = 0
        self.failed_count = 0
        self.batch_count = 0
        self.fallback_processed_count = 0
        self.fallback_failed_count = 0
        self.results.clear()
        self.index_by_source.clear()
        self.record_key_by_source.clear()
        self.seen_record_keys.clear()
        self.failed_sources.clear()
        self.processed_references.clear()
        self.skipped_duplicate_references = 0
        self.recovered_wal_keys.clear()
        start_time = datetime.now()

        # Check for previous run recovery
        self._recover_from_wal()

        try:
            # Process files in batches
            with tqdm(total=len(pdf_files), desc="Processing PDFs") as pbar:
                self.source_to_path = {}
                for pdf_path in pdf_files:
                    if pdf_path.name in self.source_to_path:
                        logger.warning(
                            "Duplicate filename detected (%s); fallback may be ambiguous",
                            pdf_path.name,
                        )
                    self.source_to_path[pdf_path.name] = pdf_path

                for i in range(0, len(pdf_files), self.max_concurrent):
                    self.batch_count += 1
                    batch = pdf_files[i : i + self.max_concurrent]
                    tasks = [
                        self._process_single_pdf(pdf_path, pbar, self.method)
                        for pdf_path in batch
                    ]
                    await asyncio.gather(*tasks)

                    # Update Excel every 20 files (non-blocking)
                    if self.processed_count > 0 and self.processed_count % 20 == 0:
                        logger.info(
                            f"📊 Updating Excel preview ({self.processed_count} records)..."
                        )
                        self._schedule_excel_update(output_file)

                    # Log batch completion
                    logger.info(
                        f"Batch {self.batch_count}/{(len(pdf_files) + self.max_concurrent - 1) // self.max_concurrent} completed. "
                        f"Progress: {self.processed_count} successful, {self.failed_count} failed"
                    )

                if fallback_method:
                    invalid_sources = self._collect_invalid_sources()
                    invalid_paths = [
                        self.source_to_path[source]
                        for source in invalid_sources
                        if source in self.source_to_path
                    ]

                    missing_sources = invalid_sources.difference(
                        self.source_to_path.keys()
                    )
                    if missing_sources:
                        logger.warning(
                            "Fallback requested but original PDF path missing for: %s",
                            ", ".join(sorted(missing_sources)),
                        )

                    if invalid_paths:
                        logger.info(
                            "Starting fallback reprocessing for %d file(s) with method=%s",
                            len(invalid_paths),
                            fallback_method.value,
                        )

                        with tqdm(
                            total=len(invalid_paths), desc="Fallback PDFs"
                        ) as fallback_bar:
                            for i in range(0, len(invalid_paths), self.max_concurrent):
                                batch = invalid_paths[i : i + self.max_concurrent]
                                tasks = [
                                    self._process_single_pdf(
                                        pdf_path,
                                        fallback_bar,
                                        fallback_method,
                                        is_fallback=True,
                                    )
                                    for pdf_path in batch
                                ]
                                await asyncio.gather(*tasks)

                        remaining = self._collect_invalid_sources()
                        logger.info(
                            "Fallback complete. Updated: %d, Fallback failures: %d, Remaining unresolved: %d",
                            self.fallback_processed_count,
                            self.fallback_failed_count,
                            len(remaining),
                        )
                    else:
                        logger.info(
                            "No records require fallback reprocessing (all energy references valid)."
                        )

                # Final Excel export (wait for background tasks to complete)
                logger.info("All batches complete. Generating final Excel...")
                self.excel_executor.shutdown(wait=True)  # Wait for any pending writes
                self._convert_memory_to_excel(output_file)

                # Log summary
                duration = (datetime.now() - start_time).total_seconds()
                summary = (
                    f"✅ Processing complete! "
                    f"Processed: {self.processed_count}, "
                    f"Failed: {self.failed_count}, "
                    f"Total batches: {self.batch_count}, "
                    f"Duration: {duration:.2f} seconds"
                )
                if self.skipped_duplicate_references:
                    summary += (
                        f", Skipped duplicates: {self.skipped_duplicate_references}"
                    )
                if fallback_method:
                    summary += (
                        f", Fallback successes: {self.fallback_processed_count}, "
                        f"Fallback failures: {self.fallback_failed_count}"
                    )
                logger.info(summary)

        except Exception as e:
            logger.error(f"Fatal error in batch processing: {str(e)}")
            # Save what we have before raising
            logger.info("Attempting to save progress to Excel before exit...")
            try:
                self.excel_executor.shutdown(wait=True)
                self._convert_memory_to_excel(output_file)
            except Exception as save_error:
                logger.error(f"Failed to save progress: {save_error}")
            raise
        finally:
            self.excel_executor.shutdown(wait=False)

    async def _process_single_pdf(
        self,
        pdf_path: Path,
        progress_bar: tqdm,
        method: ExtractionMethod,
        *,
        is_fallback: bool = False,
    ) -> Optional[dict]:
        try:
            # Read the PDF file as bytes first
            with open(pdf_path, "rb") as f:
                pdf_content = f.read()

            loop = asyncio.get_event_loop()

            extracted_data: Optional[EnergyInvoiceRecord] = None

            if method == ExtractionMethod.IMAGE:
                images = await loop.run_in_executor(
                    None, load_pdf_as_images_bytes, pdf_content
                )
                if not images:
                    logger.error(f"Failed to load PDF: {pdf_path.name}")
                    return None

                extracted_data = await loop.run_in_executor(
                    None, self.extractor.extract_invoice_from_images, images
                )
            else:
                extracted_text: Optional[str]
                if method == ExtractionMethod.TEXT:
                    extracted_text = await loop.run_in_executor(
                        None, extract_text_from_pdf_bytes, pdf_content
                    )
                elif method == ExtractionMethod.OCR:
                    ocr_processor = self._get_ocr_processor()
                    extracted_text = await loop.run_in_executor(
                        None, ocr_processor.convert_bytes, pdf_content
                    )
                elif method == ExtractionMethod.LLM:
                    extracted_text = await loop.run_in_executor(
                        None, extract_text_with_llm, pdf_content
                    )
                else:
                    logger.error(
                        f"Unsupported extraction method {method.value} for {pdf_path.name}"
                    )
                    self._register_failure(pdf_path.name, is_fallback=is_fallback)
                    return None

                if not extracted_text or not extracted_text.strip():
                    logger.warning(
                        f"No text could be extracted from: {pdf_path.name} using method={method.value}"
                    )
                    self._register_failure(pdf_path.name, is_fallback=is_fallback)
                    return None

                extracted_data = await loop.run_in_executor(
                    None, self.extractor.extract_invoice_from_text, extracted_text
                )

            if not extracted_data:
                logger.warning(
                    f"No data extracted from: {pdf_path.name} using method={method.value}"
                )
                self._register_failure(pdf_path.name, is_fallback=is_fallback)
                return None

            # Convert to dict and add metadata
            result = extracted_data.model_dump()
            result["source_file"] = str(pdf_path.name)
            result["processed_at"] = datetime.now().isoformat()
            result["extraction_method"] = method.value

            async with self.results_lock:
                replaced = self._store_result(result)
                self.failed_sources.discard(pdf_path.name)

            self._append_to_wal(result)

            if is_fallback:
                self.fallback_processed_count += 1
                if replaced:
                    logger.info(
                        "Fallback %s updated record for %s",
                        method.value,
                        pdf_path.name,
                    )
            else:
                self.processed_count += 1
                if self.processed_count % 100 == 0:
                    logger.info(f"Processed {self.processed_count} files so far...")
                if replaced:
                    logger.debug(
                        "Primary extraction replaced existing record for %s",
                        pdf_path.name,
                    )

            return result

        except Exception as e:
            self._register_failure(pdf_path.name, is_fallback=is_fallback)
            logger.error(f"Error processing {pdf_path.name}: {str(e)}")
            return None
        finally:
            progress_bar.update(1)


async def main(
    input_folder: Path,
    output_file: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    retry_delay: int = DEFAULT_RETRY_DELAY,
    method: ExtractionMethod = ExtractionMethod.IMAGE,
    fallback_method: Optional[ExtractionMethod] = None,
    limit: Optional[int] = None,
):
    """Main entry point for batch PDF processing with optimizations."""
    # Find all PDF files
    pdf_files: List[Path] = []
    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(Path(root) / file)

    if limit is not None:
        pdf_files = pdf_files[:limit]

    logger.info(f"Found {len(pdf_files)} PDF files")
    logger.info(
        f"Max concurrent requests: {max_concurrent}, Retry delay: {retry_delay}s, Method: {method.value}"
    )

    if not pdf_files:
        logger.warning("No PDF files found")
        return

    # Process with batch processor
    processor = BatchProcessor(
        max_concurrent=max_concurrent,
        retry_delay=retry_delay,
        method=method,
    )
    await processor.process_batch(
        pdf_files, output_file, fallback_method=fallback_method
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fast batch PDF processing with concurrent execution and progress tracking"
    )
    parser.add_argument(
        "--input_folder",
        type=Path,
        required=True,
        help="Folder containing PDF files",
    )
    parser.add_argument(
        "--output_file",
        type=Path,
        required=True,
        help="Output XLSX file path",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=DEFAULT_MAX_CONCURRENT,
        help=f"Max concurrent API requests (default: {DEFAULT_MAX_CONCURRENT})",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=DEFAULT_RETRY_DELAY,
        help=f"Delay between retries in seconds (default: {DEFAULT_RETRY_DELAY})",
    )
    parser.add_argument(
        "--method",
        type=ExtractionMethod,
        choices=list(ExtractionMethod),
        default=ExtractionMethod.IMAGE,
        help="Extraction method to use (text, image, ocr, llm)",
    )
    parser.add_argument(
        "--fallback-method",
        type=ExtractionMethod,
        choices=list(ExtractionMethod),
        help=(
            "Optional fallback extraction method for records whose energy reference "
            "does not match the expected format (14 digits or 8 alphanumerics)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of files to process",
    )
    args = parser.parse_args()

    asyncio.run(
        main(
            args.input_folder,
            args.output_file,
            args.max_concurrent,
            args.retry_delay,
            args.method,
            args.fallback_method,
            args.limit,
        )
    )
