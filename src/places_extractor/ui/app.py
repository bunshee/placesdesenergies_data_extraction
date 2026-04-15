import base64
import io
import json
import logging
import re
import tempfile
import threading
import time
import zipfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Tuple

import streamlit as st

from places_extractor.core.extractor import extract_data, get_extraction_defaults

# Configure logging to logs directory
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "extraction.log"),
        logging.StreamHandler()
    ],
)
logger = logging.getLogger(__name__)

def download_pdf_from_url(url: str) -> Tuple[io.BytesIO | None, str | None, str | None]:
    try:
        if "drive.google.com" in url:
            file_id_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
            if file_id_match:
                file_id = file_id_match.group(1)
                url = f"https://drive.google.com/uc?export=download&id={file_id}"
            else:
                file_id_match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
                if file_id_match:
                    file_id = file_id_match.group(1)
                    url = f"https://drive.google.com/uc?export=download&id={file_id}"

        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        filename = "downloaded_file.pdf"
        cd = response.headers.get("Content-Disposition")
        if cd:
            fn_match = re.search(r'filename="?([^"]+)"?', cd)
            if fn_match:
                filename = fn_match.group(1)
        
        return io.BytesIO(response.content), filename, None
    except Exception as e:
        logger.error(f"Error downloading from URL {url}: {e}")
        return None, None, str(e)

def extract_with_retry(
    pdf_file: Any, first_page: int | None = None, last_page: int | None = None, 
    supplier: str | None = None, hints: list[str] | None = None, max_retries: int = 3
) -> dict[str, Any]:
    filename = getattr(pdf_file, "name", str(pdf_file))
    for attempt in range(max_retries):
        try:
            result = extract_data(pdf_file, first_page, last_page, supplier, hints)
            if isinstance(result, dict) and "error" not in result:
                return result
            elif attempt < max_retries - 1:
                time.sleep(60)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(60)
            else:
                return {"error": str(e)}
    return {"error": "Max retries exceeded"}

def parse_filename(filename: str) -> dict[str, str]:
    raison_sociale = ""
    fournisseur_actuel = ""
    parts = filename.split("-")
    if parts:
        raison_sociale = re.sub(r"[0-9_]", "", parts[0]).strip()
    if len(parts) > 1:
        fournisseur_actuel = parts[1].strip()
    return {
        "nom du fichier": filename,
        "Raison sociale": raison_sociale,
        "Fournisseur actuel": fournisseur_actuel,
    }

def display_pdf(file: Any) -> None:
    pdf_bytes = file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="800px" type="application/pdf"></iframe>'
    st.markdown(pdf_display, unsafe_allow_html=True)

def main():
    st.set_page_config(layout="wide", page_title="Extraction Factures")
    st.title("Extraction de données de factures d'énergie")
    
    uploaded_file = st.file_uploader("Télécharger un PDF", type=["pdf"])
    if uploaded_file:
        defaults = get_extraction_defaults(uploaded_file.name)
        if st.button("Lancer l'extraction"):
            with st.spinner("Extraction..."):
                result = extract_with_retry(
                    uploaded_file, 
                    first_page=defaults["first_page"],
                    last_page=defaults["last_page"],
                    supplier=defaults["supplier"],
                    hints=defaults["hints"]
                )
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.json(result["extraction"])

if __name__ == "__main__":
    main()
