import base64
import os
from typing import Any, Literal, Optional, TypeVar

from google import genai
from google.api_core import exceptions as google_exceptions
from google.genai import errors as genai_errors
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src import logger
from src.models.schema import EnergyInvoiceRecord
from src.utils.prompts import CLASSIFICATION_PROMPT, EXTRACTION_PROMPT

T = TypeVar("T")


class DocumentRejectedError(Exception):
    """Raised when a document is rejected during classification."""

    def __init__(self, reasoning: str, is_rejection: bool = True):
        self.reasoning = reasoning
        self.is_rejection = is_rejection
        super().__init__(f"Document rejected: {reasoning}")


class DocumentClassification(BaseModel):
    reasoning: str
    decision: Literal["ACCEPT", "REJECT"]
    is_rejection: bool = False  # Whether this is a rejection (skip retries)

    def raise_if_rejected(self) -> None:
        """Raise DocumentRejectedError if the document was rejected."""
        if self.decision == "REJECT":
            raise DocumentRejectedError(self.reasoning, is_rejection=self.is_rejection)


class GeminiExtractor:
    """A class to handle Gemini API calls for document classification and invoice extraction with retry logic."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Gemini extractor.

        Args:
            api_key: Optional API key. If not provided, will use GEMINI_API_KEY environment variable.
        """
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self._api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY is not set in environment or provided"
            )

        try:
            self._client = genai.Client(api_key=self._api_key)
        except Exception as e:
            logger.error(f"Failed to initialize genai.Client: {e}")
            raise

        # Default generation config for extraction
        self._extraction_config = GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=EnergyInvoiceRecord,
            max_output_tokens=8192,  # Allow sufficient space for complete responses
        )

        # Config for classification
        self._classification_config = GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=DocumentClassification,
        )

    @staticmethod
    def _retry_if_server_error(exception: BaseException) -> bool:
        """Determine if the exception should trigger a retry."""
        if isinstance(
            exception,
            (
                genai_errors.ServerError,
                google_exceptions.ServiceUnavailable,
                google_exceptions.TooManyRequests,
                google_exceptions.ResourceExhausted,
                google_exceptions.RetryError,
            ),
        ):
            return True
        return False

    @staticmethod
    def _before_sleep_retry(retry_state: RetryCallState) -> None:
        """Log before retrying."""
        if retry_state.outcome is None:
            return

        exception = retry_state.outcome.exception()
        if exception is None:
            return

        wait = retry_state.next_action.sleep if retry_state.next_action else 0
        logger.warning(
            f"Retrying in {wait:.1f} seconds after {retry_state.attempt_number} attempts. "
            f"Last error: {str(exception)}"
        )

    @retry(
        retry=(
            retry_if_exception_type(genai_errors.ServerError)
            | retry_if_exception_type(google_exceptions.ServiceUnavailable)
            | retry_if_exception_type(google_exceptions.TooManyRequests)
            | retry_if_exception_type(google_exceptions.ResourceExhausted)
            | retry_if_exception_type(google_exceptions.RetryError)
            | retry_if_exception_type(ConnectionError)
            | retry_if_exception_type(TimeoutError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=_before_sleep_retry.__func__,  # type: ignore
        reraise=True,
        retry_error_callback=lambda retry_state: (
            retry_state.outcome.exception()
            if not isinstance(retry_state.outcome.exception(), DocumentRejectedError)
            else None
        ),
    )
    def _call_gemini_api(self, contents: list) -> Any:
        """Make the actual API call to Gemini with retry logic.

        Args:
            contents: The contents to send to the Gemini API

        Returns:
            The response from the Gemini API

        Raises:
            Exception: If all retry attempts are exhausted
        """
        return self._client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=self._generation_config,
        )

    def _classify_document(self, text: str) -> DocumentClassification:
        """Classify if the document is a valid energy invoice.

        Args:
            text: Raw text content extracted from the document.

        Returns:
            DocumentClassification with decision and reasoning.
        """
        if not text or not text.strip():
            return DocumentClassification(
                reasoning="Empty or whitespace-only text provided.",
                decision="REJECT",
                is_rejection=True,  # Mark as rejection to skip retries
            )

        try:
            contents = [
                {
                    "role": "user",
                    "parts": [
                        {"text": CLASSIFICATION_PROMPT},
                        {"text": f"\n\n--- DOCUMENT TEXT TO CLASSIFY ---\n\n{text}"},
                        {
                            "text": "\n\n---\n\nRespond with ONLY the JSON object, no other text or markdown formatting."
                        },
                    ],
                }
            ]

            # Use the flash-lite model for faster classification
            resp = self._client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=contents,
                config=self._classification_config,
            )

            if resp.prompt_feedback and resp.prompt_feedback.block_reason:
                return DocumentClassification(
                    reasoning=f"Content blocked by safety filters: {resp.prompt_feedback.block_reason.name}",
                    decision="REJECT",
                )

            if not resp.candidates or not resp.candidates[0].content.parts:
                return DocumentClassification(
                    reasoning="No valid response from classification model.",
                    decision="REJECT",
                )

            # Get the parsed response or parse it from text if needed
            classification = getattr(resp, "parsed", None)
            if not classification:
                try:
                    raw_json = getattr(resp, "text", "{}")
                    classification = DocumentClassification.model_validate_json(
                        raw_json
                    )
                except Exception as e:
                    logger.error(f"Failed to parse classification response: {str(e)}")
                    return DocumentClassification(
                        reasoning="Failed to parse classification response.",
                        decision="REJECT",
                    )

            return classification

        except Exception as e:
            logger.error(f"Document classification failed: {str(e)}")
            return DocumentClassification(
                reasoning=f"Classification error: {str(e)}", decision="REJECT"
            )

    def extract_invoice_from_text(self, text: str) -> Optional[EnergyInvoiceRecord]:
        """Extract invoice data from text using Gemini.

        Args:
            text: Raw text content extracted from the invoice document.
            skip_classification: If True, skip the document classification step.

        Returns:
            An EnergyInvoiceRecord object if successful, None otherwise.
        """
        if not text or not text.strip():
            logger.warning("Empty or whitespace-only text provided for extraction.")
            return None

        # Classify the document first
        try:
            classification = self._classify_document(text)
            classification.raise_if_rejected()  # Will raise DocumentRejectedError if rejected
            logger.debug(f"Document accepted: {classification.reasoning}")
        except DocumentRejectedError as e:
            logger.warning(f"Document rejected: {e.reasoning}")
            return None

        text_preview = text[:200].replace("\n", " ")
        logger.debug(
            f"Calling Gemini for structured extraction (chars={len(text)}, preview='{text_preview}...')"
        )

        resp = None
        try:
            contents = [
                {
                    "role": "user",
                    "parts": [
                        {"text": EXTRACTION_PROMPT},
                        {"text": f"\n\n--- INVOICE TEXT TO EXTRACT ---\n\n{text}"},
                    ],
                }
            ]

            resp = self._client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=self._extraction_config,
            )

            if resp.prompt_feedback and resp.prompt_feedback.block_reason:
                logger.error(
                    f"Gemini prompt was blocked. Reason: {resp.prompt_feedback.block_reason.name}"
                )
                return None

            if not resp.candidates:
                logger.error(
                    "Gemini returned no candidates. Check prompt or safety settings."
                )
                return None

            candidate = resp.candidates[0]
            if candidate.finish_reason and candidate.finish_reason.name == "SAFETY":
                logger.error("Gemini response was blocked by safety settings.")
                return None

            if candidate.finish_reason and candidate.finish_reason.name not in (
                "STOP",
                "MAX_TOKENS",
            ):
                logger.warning(
                    f"Gemini response finished with unusual reason: {candidate.finish_reason.name}"
                )

            raw_json = getattr(resp, "text", None)
            if not raw_json:
                logger.error("Gemini returned an empty text response.")
                return None

            logger.debug(
                f"Gemini(raw) preview: {(raw_json[:400] + '…') if len(raw_json) > 400 else raw_json}"
            )

            record = getattr(
                resp, "parsed", None
            ) or EnergyInvoiceRecord.model_validate_json(raw_json)

            if not isinstance(record, EnergyInvoiceRecord):
                logger.error(f"Unexpected response type: {type(record)}")
                return None

            # Check if document was rejected during extraction
            if not record.is_valid_energy_invoice:
                logger.warning(
                    f"Document rejected by extraction agent: {record.rejection_reason}"
                )
                return None

            logger.info(
                f"Successfully extracted invoice data for site: {record.site_name}"
            )
            return record

        except genai_errors.ServerError as e:
            error_msg = str(e)
            status_code = getattr(e, "status_code", "unknown")
            logger.error(f"Gemini server error (code {status_code}): {error_msg}")
            return None

        except genai_errors.ClientError as e:
            error_msg = str(e)
            status_code = getattr(e, "status_code", "unknown")
            logger.error(f"Gemini client error (code {status_code}): {error_msg}")
            return None

        except google_exceptions.InvalidArgument as e:
            logger.error(
                f"Gemini API Invalid Argument: {str(e)}. Check schema or prompt."
            )
            return None

        except google_exceptions.ResourceExhausted as e:
            logger.error(f"Gemini API quota exhausted: {str(e)}. Check API limits.")
            return None

        except google_exceptions.RetryError as e:
            logger.error(f"Gemini API call failed after retries: {str(e)}")
            return None

        except (ValueError, TypeError) as e:
            raw_preview = (
                resp.text[:200] if resp and hasattr(resp, "text") else "No response"
            )
            logger.error(
                f"Failed to parse Gemini response: {e}. Raw preview: {raw_preview}"
            )
            return None

        except AttributeError as e:
            logger.error(f"Response object missing expected attributes: {e}")
            return None

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.critical(
                f"Unexpected error during Gemini extraction: {error_type}: {error_msg}",
                exc_info=True,
            )
            return None

    def _classify_document_from_images(
        self, image_bytes_list: list[bytes]
    ) -> DocumentClassification:
        """Classify if the document is a valid energy invoice using images.

        Args:
            image_bytes_list: List of PNG image bytes (one per page).

        Returns:
            DocumentClassification with decision and reasoning.
        """
        if not image_bytes_list:
            return DocumentClassification(
                reasoning="No images provided.",
                decision="REJECT",
                is_rejection=True,
            )

        try:
            # Prepare parts with images (limit to first 5 pages for classification)
            parts = [{"text": CLASSIFICATION_PROMPT}]
            for idx, img_bytes in enumerate(image_bytes_list[:5]):
                # Add inline data for each image
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(img_bytes).decode("utf-8"),
                        }
                    }
                )

            parts.append(
                {
                    "text": "\n\n---\n\nRespond with ONLY the JSON object, no other text or markdown formatting."
                }
            )

            contents = [{"role": "user", "parts": parts}]

            # Use the flash model for classification
            resp = self._client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=contents,
                config=self._classification_config,
            )

            if resp.prompt_feedback and resp.prompt_feedback.block_reason:
                return DocumentClassification(
                    reasoning=f"Content blocked by safety filters: {resp.prompt_feedback.block_reason.name}",
                    decision="REJECT",
                )

            if not resp.candidates or not resp.candidates[0].content.parts:
                return DocumentClassification(
                    reasoning="No valid response from classification model.",
                    decision="REJECT",
                )

            classification = getattr(resp, "parsed", None)
            if not classification:
                try:
                    raw_json = getattr(resp, "text", "{}")
                    classification = DocumentClassification.model_validate_json(
                        raw_json
                    )
                except Exception as e:
                    logger.error(f"Failed to parse classification response: {str(e)}")
                    return DocumentClassification(
                        reasoning="Failed to parse classification response.",
                        decision="REJECT",
                    )

            return classification

        except Exception as e:
            logger.error(f"Document classification from images failed: {str(e)}")
            return DocumentClassification(
                reasoning=f"Classification error: {str(e)}", decision="REJECT"
            )

    def extract_invoice_from_images(
        self, image_bytes_list: list[bytes]
    ) -> Optional[EnergyInvoiceRecord]:
        """Extract invoice data from PDF page images using Gemini vision with built-in validation.

        Args:
            image_bytes_list: List of PNG image bytes (one per page).
            skip_classification: Deprecated - classification is now built into extraction.

        Returns:
            An EnergyInvoiceRecord object if successful, None otherwise.
        """
        if not image_bytes_list:
            logger.warning("No images provided for extraction.")
            return None

        logger.debug(
            f"Calling Gemini for validation + extraction from {len(image_bytes_list)} page(s)"
        )

        resp = None
        try:
            # Prepare parts with images
            parts = [{"text": EXTRACTION_PROMPT}]
            parts.append({"text": "\n\n--- INVOICE IMAGES TO EXTRACT ---\n\n"})

            for idx, img_bytes in enumerate(image_bytes_list):
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(img_bytes).decode("utf-8"),
                        }
                    }
                )

            contents = [{"role": "user", "parts": parts}]

            resp = self._client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=self._extraction_config,
            )

            if resp.prompt_feedback and resp.prompt_feedback.block_reason:
                logger.error(
                    f"Gemini prompt was blocked. Reason: {resp.prompt_feedback.block_reason.name}"
                )
                return None

            if not resp.candidates:
                logger.error(
                    "Gemini returned no candidates. Check prompt or safety settings."
                )
                return None

            candidate = resp.candidates[0]
            if candidate.finish_reason and candidate.finish_reason.name == "SAFETY":
                logger.error("Gemini response was blocked by safety settings.")
                return None

            if candidate.finish_reason and candidate.finish_reason.name not in (
                "STOP",
                "MAX_TOKENS",
            ):
                logger.warning(
                    f"Gemini response finished with unusual reason: {candidate.finish_reason.name}"
                )

            raw_json = getattr(resp, "text", None)
            if not raw_json:
                logger.error("Gemini returned an empty text response.")
                return None

            logger.debug(
                f"Gemini(raw) preview: {(raw_json[:400] + '…') if len(raw_json) > 400 else raw_json}"
            )

            record = getattr(
                resp, "parsed", None
            ) or EnergyInvoiceRecord.model_validate_json(raw_json)

            if not isinstance(record, EnergyInvoiceRecord):
                logger.error(f"Unexpected response type: {type(record)}")
                return None

            # Check if document was rejected during extraction
            if not record.is_valid_energy_invoice:
                logger.warning(
                    f"Document rejected by extraction agent: {record.rejection_reason}"
                )
                return None

            logger.info(
                f"Successfully extracted invoice data for site: {record.site_name}"
            )
            return record

        except genai_errors.ServerError as e:
            error_msg = str(e)
            status_code = getattr(e, "status_code", "unknown")
            logger.error(f"Gemini server error (code {status_code}): {error_msg}")
            return None

        except genai_errors.ClientError as e:
            error_msg = str(e)
            status_code = getattr(e, "status_code", "unknown")
            logger.error(f"Gemini client error (code {status_code}): {error_msg}")
            return None

        except google_exceptions.InvalidArgument as e:
            logger.error(
                f"Gemini API Invalid Argument: {str(e)}. Check schema or prompt."
            )
            return None

        except google_exceptions.ResourceExhausted as e:
            logger.error(f"Gemini API quota exhausted: {str(e)}. Check API limits.")
            return None

        except google_exceptions.RetryError as e:
            logger.error(f"Gemini API call failed after retries: {str(e)}")
            return None

        except (ValueError, TypeError) as e:
            raw_preview = (
                resp.text[:200] if resp and hasattr(resp, "text") else "No response"
            )
            logger.error(
                f"Failed to parse Gemini response: {e}. Raw preview: {raw_preview}"
            )
            return None

        except AttributeError as e:
            logger.error(f"Response object missing expected attributes: {e}")
            return None

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.critical(
                f"Unexpected error during Gemini extraction: {error_type}: {error_msg}",
                exc_info=True,
            )
            return None


def extract_invoice_from_text(
    text: str, api_key: Optional[str] = None
) -> Optional[EnergyInvoiceRecord]:
    """Convenience function to extract invoice data using Gemini.

    This creates a new GeminiExtractor instance for each call. For better performance,
    create and reuse a GeminiExtractor instance.

    Args:
        text: Raw text content extracted from the invoice document.
        api_key: Optional API key. If not provided, will use GEMINI_API_KEY environment variable.
        skip_classification: If True, skip the document classification step.

    Returns:
        An EnergyInvoiceRecord object if successful, None otherwise.
    """
    try:
        extractor = GeminiExtractor(api_key=api_key)
        return extractor.extract_invoice_from_text(text)
    except Exception as e:
        logger.error(f"Failed to initialize GeminiExtractor: {e}")
        return None


def extract_invoice_from_images(
    image_bytes_list: list[bytes],
    api_key: Optional[str] = None,
) -> Optional[EnergyInvoiceRecord]:
    """Convenience function to extract invoice data from images using Gemini.

    This creates a new GeminiExtractor instance for each call. For better performance,
    create and reuse a GeminiExtractor instance.

    Args:
        image_bytes_list: List of PNG image bytes (one per page).
        api_key: Optional API key. If not provided, will use GEMINI_API_KEY environment variable.
        skip_classification: If True, skip the document classification step.

    Returns:
        An EnergyInvoiceRecord object if successful, None otherwise.
    """
    try:
        extractor = GeminiExtractor(api_key=api_key)
        return extractor.extract_invoice_from_images(image_bytes_list)
    except Exception as e:
        logger.error(f"Failed to initialize GeminiExtractor: {e}")
        return None
