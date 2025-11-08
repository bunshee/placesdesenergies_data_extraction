import base64
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import fitz
import pytesseract
from google import genai
from loguru import logger
from pdf2image import convert_from_bytes, convert_from_path
import cv2
import numpy as np
from PIL import Image
from redis.exceptions import RedisError

from src.utils.redis_cache import RedisUnavailableError, get_redis_client


def iter_pdf_pages_as_images(
    pdf_path: str | Path, dpi: int = 200
) -> Iterator[tuple[int, bytes]]:
    """Yield (page_index, image_bytes) for each page as PNG images.

    Args:
        pdf_path: Path to the PDF file.
        dpi: Resolution for rendering (default 150 for good quality/size balance).

    Yields:
        Tuple of (page_index, PNG image bytes) for each page.

    Notes:
        - Works with both text-based and scanned PDFs.
        - Higher DPI = better quality but larger files and slower processing.
    """
    path = Path(pdf_path)
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            # Render page to pixmap at specified DPI
            zoom = dpi / 72  # 72 DPI is the default
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            yield i, img_bytes


def load_pdf_as_images(pdf_path: str | Path, dpi: int = 200) -> list[bytes]:
    """Load all PDF pages as a list of PNG image bytes.

    Args:
        pdf_path: Path to the PDF file.
        dpi: Resolution for rendering (default 150).

    Returns:
        List of PNG image bytes, one per page.
    """
    return [img for _, img in iter_pdf_pages_as_images(pdf_path, dpi)]


def iter_pdf_pages_as_images_bytes(
    pdf_bytes: bytes, dpi: int = 200
) -> Iterator[tuple[int, bytes]]:
    """Yield (page_index, image_bytes) for each page from PDF bytes.

    Args:
        pdf_bytes: PDF file content as bytes.
        dpi: Resolution for rendering (default 150).

    Yields:
        Tuple of (page_index, PNG image bytes) for each page.
    """
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            yield i, img_bytes


def load_pdf_as_images_bytes(pdf_bytes: bytes, dpi: int = 200) -> list[bytes]:
    """Load all PDF pages from bytes as a list of PNG image bytes.

    Args:
        pdf_bytes: PDF file content as bytes.
        dpi: Resolution for rendering (default 150).

    Returns:
        List of PNG image bytes, one per page.
    """
    return [img for _, img in iter_pdf_pages_as_images_bytes(pdf_bytes, dpi)]


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Extract text directly from a PDF file using PyMuPDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted text from all pages.
    """
    path = Path(pdf_path)
    text_parts = []
    with fitz.open(path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text()
            if text.strip():
                text_parts.append(f"## Page {page_num}\n\n{text}\n")
    return "\n".join(text_parts)


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text directly from PDF bytes using PyMuPDF.

    Args:
        pdf_bytes: PDF file content as bytes.

    Returns:
        Extracted text from all pages.
    """
    text_parts = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text()
            if text.strip():
                text_parts.append(f"## Page {page_num}\n\n{text}\n")
    return "\n".join(text_parts)


@lru_cache(maxsize=1)
def _get_gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set")
    return genai.Client(api_key=api_key)


def extract_text_with_llm(pdf_bytes: bytes) -> str:
    """Use Gemini to extract structured markdown text from PDF bytes."""
    try:
        redis_client = get_redis_client()
    except RedisUnavailableError as exc:
        logger.warning("Redis unavailable for LLM cache: %s", exc)
        redis_client = None
    cache_key = f"llm_pdf:{hashlib.sha256(pdf_bytes).hexdigest()}"

    if redis_client:
        try:
            cached_text = redis_client.get(cache_key)
            if cached_text:
                return cached_text
        except RedisError as exc:
            logger.warning("Redis get failed for key %s: %s", cache_key, exc)

    client = _get_gemini_client()

    inline_data = {
        "mime_type": "application/pdf",
        "data": base64.b64encode(pdf_bytes).decode("utf-8"),
    }

    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": "You must extract precisely the content of the given PDF in structured markdown.",
                },
                {"inline_data": inline_data},
            ],
        }
    ]

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=contents,
    )

    result_text = getattr(response, "text", "") or ""

    if redis_client and result_text:
        ttl_env = os.getenv("LLM_CACHE_TTL_SECONDS", "86400")
        try:
            ttl = int(ttl_env)
        except ValueError:
            logger.warning(
                "Invalid LLM_CACHE_TTL_SECONDS value '%s'; defaulting to 86400",
                ttl_env,
            )
            ttl = 86400

        try:
            redis_client.setex(cache_key, ttl, result_text)
        except RedisError as exc:
            logger.warning("Redis set failed for key %s: %s", cache_key, exc)

    return result_text


class PDFToMarkdownOCR:
    """Converts a scanned PDF to structured Markdown using Tesseract OCR."""

    def __init__(self, lang="eng", dpi=300, tesseract_cmd=None):
        # make sure Tesseract can find its data
        if "TESSDATA_PREFIX" not in os.environ:
            os.environ["TESSDATA_PREFIX"] = "/usr/share/tesseract-ocr/5/"
        self.lang = lang
        self.dpi = dpi
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def _ocr_page(self, image: Image.Image) -> str:
        """Run OCR on a single image page with preprocessing."""
        # Convert PIL Image to NumPy array for OpenCV
        open_cv_image = np.array(image)
        # Convert RGB to BGR
        open_cv_image = open_cv_image[:, :, ::-1].copy()

        # 1. Grayscale
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)

        # 2. Gaussian blur
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        # 3. Otsu's thresholding
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Run OCR on the preprocessed image
        return pytesseract.image_to_string(thresh, lang=self.lang)

    def _text_to_markdown(self, text: str) -> str:
        """Convert OCR text into lightly structured Markdown."""
        lines = text.splitlines()
        md_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                md_lines.append("")  # paragraph break
            # Treat short all-caps lines as headings
            elif re.match(r"^[A-Z0-9 ]+$", line) and len(line.split()) <= 8:
                md_lines.append(f"## {line.title()}")
            # Preserve bullets or numbered lists
            elif re.match(r"^(\d+[\.\)]|[-•*])\s", line):
                md_lines.append(f"- {re.sub(r'^(\d+[\.\)]|[-•*])\s*', '', line)}")
            else:
                md_lines.append(line)
        return "\n".join(md_lines)

    def convert(self, pdf_path: str) -> str:
        """Convert a PDF to a Markdown string using Tesseract OCR.

        Args:
            pdf_path: Path to input PDF

        Returns:
            Markdown text as string
        """
        pages = convert_from_path(pdf_path, dpi=self.dpi)
        markdown_output = []

        for i, page in enumerate(pages, start=1):
            text = self._ocr_page(page)
            structured = self._text_to_markdown(text)
            markdown_output.append(f"## Page {i}\n\n{structured}\n")

        final_md = "\n".join(markdown_output)
        return final_md

    def convert_bytes(self, pdf_bytes: bytes) -> str:
        """Convert PDF bytes to a Markdown string using Tesseract OCR.

        Args:
            pdf_bytes: PDF file content as bytes

        Returns:
            Markdown text as string
        """
        pages = convert_from_bytes(pdf_bytes, dpi=self.dpi)
        markdown_output = []

        for i, page in enumerate(pages, start=1):
            text = self._ocr_page(page)
            structured = self._text_to_markdown(text)
            markdown_output.append(f"## Page {i}\n\n{structured}\n")

        final_md = "\n".join(markdown_output)
        return final_md
