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
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from extractor import extract_data, get_extraction_defaults

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("extraction.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def download_pdf_from_url(url: str) -> Tuple[Optional[io.BytesIO], Optional[str], Optional[str]]:
    """
    Download a PDF from a URL, with special handling for Google Drive links.
    Returns: (content_io, filename, error_message)
    """
    try:
        # Handle Google Drive links
        if "drive.google.com" in url:
            # Extract file ID
            file_id_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
            if file_id_match:
                file_id = file_id_match.group(1)
                url = f"https://drive.google.com/uc?export=download&id={file_id}"
            else:
                # Check for other formats like /open?id= or /uc?id=
                file_id_match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
                if file_id_match:
                    file_id = file_id_match.group(1)
                    url = f"https://drive.google.com/uc?export=download&id={file_id}"

        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        # Check content type if possible
        content_type = response.headers.get("Content-Type", "")
        if "application/pdf" not in content_type.lower() and "application/octet-stream" not in content_type.lower() and "drive.google.com" not in url:
             logger.warning(f"URL might not be a PDF: {content_type}")

        # Get filename from headers or URL
        filename = "downloaded_file.pdf"
        cd = response.headers.get("Content-Disposition")
        if cd:
            fn_match = re.search(r'filename="?([^"]+)"?', cd)
            if fn_match:
                filename = fn_match.group(1)
        
        if filename == "downloaded_file.pdf":
            # Extract from URL path
            path_parts = url.split("?")[0].split("/")
            if path_parts[-1].lower().endswith(".pdf"):
                filename = path_parts[-1]

        return io.BytesIO(response.content), filename, None
    except Exception as e:
        logger.error(f"Error downloading from URL {url}: {str(e)}")
        return None, None, str(e)


def extract_with_retry(
    pdf_file, first_page=None, last_page=None, supplier=None, hints=None, max_retries=3
):
    """
    Extract data with retry mechanism that waits 60 seconds between retries.
    """
    filename = getattr(pdf_file, "name", str(pdf_file))
    logger.info(f"Starting extraction for {filename}")

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1}/{max_retries} for {filename}")
            result = extract_data(pdf_file, first_page, last_page, supplier, hints)

            # Check if extraction was successful
            if isinstance(result, dict) and "error" not in result:
                logger.info(f"Successful extraction for {filename}")
                return result
            elif isinstance(result, dict) and "error" in result:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Extraction failed for {filename}: {result['error']}"
                    )
                    st.warning(
                        f"Extraction failed (attempt {attempt + 1}/{max_retries}): {result['error']}"
                    )
                    st.info("Waiting 60 seconds before retry...")
                    logger.info(f"Waiting 60 seconds before retry for {filename}")
                    time.sleep(60)
                else:
                    logger.error(
                        f"All retries failed for {filename}: {result['error']}"
                    )
                    st.error(
                        f"Extraction failed after {max_retries} attempts: {result['error']}"
                    )
                    return result
            else:
                logger.info(f"Unexpected result format for {filename}")
                return result

        except Exception as e:
            if attempt < max_retries - 1:
                logger.error(f"Exception during extraction for {filename}: {str(e)}")
                st.warning(
                    f"Extraction failed (attempt {attempt + 1}/{max_retries}): {str(e)}"
                )
                st.info("Waiting 60 seconds before retry...")
                logger.info(f"Waiting 60 seconds before retry for {filename}")
                time.sleep(60)
            else:
                logger.error(
                    f"All retries failed with exceptions for {filename}: {str(e)}"
                )
                st.error(f"Extraction failed after {max_retries} attempts: {str(e)}")
                return {"error": str(e)}

    logger.error(f"Max retries exceeded for {filename}")
    return {"error": "Max retries exceeded"}


def process_single_pdf(pdf_file: Path, index: int, total: int) -> Dict[str, Any]:
    """
    Process a single PDF file and return the result with metadata.
    """
    logger.info(f"Processing {pdf_file.name} ({index + 1}/{total})")
    try:
        # Get defaults based on filename
        defaults = get_extraction_defaults(pdf_file.name)
        logger.info(
            f"Defaults for {pdf_file.name}: supplier={defaults['supplier']}, pages={defaults.get('first_page', 'all')}"
        )

        # Extract with retry
        result = extract_with_retry(
            pdf_file,
            first_page=defaults["first_page"],
            last_page=defaults["last_page"],
            supplier=defaults["supplier"],
            hints=defaults.get("hints"),
        )

        # Add filename info to result
        filename_data = parse_filename(pdf_file.name)
        result["filename_info"] = filename_data
        result["filename"] = pdf_file.name
        result["processing_order"] = index + 1

        logger.info(f"Completed processing {pdf_file.name}")
        return result

    except Exception as e:
        logger.error(f"Processing error for {pdf_file.name}: {str(e)}")
        return {
            "filename": pdf_file.name,
            "error": f"Processing error: {str(e)}",
            "processing_order": index + 1,
        }


def batch_extraction(uploaded_files, parallel=False, max_workers=3):
    """
    Process multiple PDFs from uploaded files with improved progress tracking
    and optional parallel processing.

    Args:
        uploaded_files: Either a list of uploaded PDF files or a single ZIP file
        parallel: Whether to use parallel processing
        max_workers: Number of parallel workers
    """
    logger.info(
        f"Starting batch extraction - parallel={parallel}, max_workers={max_workers}"
    )
    results = []

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Handle different input types
        if isinstance(uploaded_files, list):
            # Multiple PDF files uploaded directly
            pdf_files = []
            logger.info(f"Processing {len(uploaded_files)} uploaded PDF files")
            for uploaded_file in uploaded_files:
                if uploaded_file.name.lower().endswith(".pdf"):
                    # Save file to temp directory
                    file_path = temp_path / uploaded_file.name
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getvalue())
                    pdf_files.append(file_path)
                    logger.debug(f"Saved {uploaded_file.name} to temp directory")
        else:
            # Single ZIP file
            logger.info(f"Extracting ZIP file: {uploaded_files.name}")
            with zipfile.ZipFile(uploaded_files, "r") as zip_ref:
                zip_ref.extractall(temp_dir)
            # Find all PDF files
            pdf_files = list(temp_path.glob("*.pdf"))
            logger.info(f"Found {len(pdf_files)} PDF files in ZIP")

        if not pdf_files:
            st.error("No PDF files found in the zip file.")
            return results

        # Sort files for consistent processing
        pdf_files = sorted(pdf_files, key=lambda x: x.name)

        total_files = len(pdf_files)
        st.info(f"Found {total_files} PDF file(s) to process")

        # Create progress tracking components
        progress_bar = st.progress(0)
        status_container = st.container()

        if parallel and total_files > 1:
            # Parallel processing
            logger.info(f"Starting parallel processing with {max_workers} workers")
            with status_container:
                st.write("**Processing files in parallel...**")
                status_text = st.empty()
                completed_count = 0
                lock = threading.Lock()

                def update_progress(filename, index):
                    nonlocal completed_count
                    with lock:
                        completed_count += 1
                        status_text.text(
                            f"✓ Completed: {filename} ({completed_count}/{total_files})"
                        )
                        progress_bar.progress(completed_count / total_files)

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all tasks
                    future_to_file = {
                        executor.submit(process_single_pdf, pdf_file, i, total_files): (
                            pdf_file,
                            i,
                        )
                        for i, pdf_file in enumerate(pdf_files)
                    }

                    # Collect results as they complete
                    for future in as_completed(future_to_file):
                        pdf_file, index = future_to_file[future]
                        try:
                            result = future.result()
                            results.append(result)
                            update_progress(pdf_file.name, index)
                        except Exception as e:
                            st.error(f"Error processing {pdf_file.name}: {str(e)}")
                            results.append(
                                {
                                    "filename": pdf_file.name,
                                    "error": str(e),
                                    "processing_order": index + 1,
                                }
                            )
                            update_progress(pdf_file.name, index)

            # Sort results by processing order
            results = sorted(results, key=lambda x: x.get("processing_order", 0))

        else:
            # Sequential processing with detailed status
            with status_container:
                st.write("**Processing files sequentially...**")
                status_text = st.empty()
                time_estimates = st.empty()

                start_time = time.time()

                for i, pdf_file in enumerate(pdf_files):
                    # Update status
                    status_text.text(
                        f"Processing: {pdf_file.name} ({i + 1}/{total_files})"
                    )

                    # Estimate time remaining
                    if i > 0:
                        elapsed = time.time() - start_time
                        avg_time = elapsed / i
                        remaining = avg_time * (total_files - i)
                        time_estimates.info(
                            f"⏱️ Estimated time remaining: {remaining / 60:.1f} minutes"
                        )

                    # Process the file
                    result = process_single_pdf(pdf_file, i, total_files)
                    results.append(result)

                    # Update progress
                    progress = (i + 1) / total_files
                    progress_bar.progress(progress)

                # Clear time estimates
                time_estimates.empty()
                status_text.text("✓ All files processed!")

        # Display summary
        success_count = sum(
            1 for r in results if "error" not in r or not r.get("error")
        )
        error_count = total_files - success_count

        logger.info(
            f"Batch extraction completed: {success_count} successful, {error_count} errors"
        )

        summary_cols = st.columns(3)
        with summary_cols[0]:
            st.metric("Total Files", total_files)
        with summary_cols[1]:
            st.metric("Successful", success_count, delta_color="normal")
        with summary_cols[2]:
            st.metric("Errors", error_count, delta_color="inverse")

    return results


def parse_filename(filename):
    nom_du_fichier = filename
    raison_sociale = ""
    fournisseur_actuel = ""

    parts = filename.split("-")
    if len(parts) > 0:
        raison_sociale_raw = parts[0]
        raison_sociale = re.sub(r"[0-9_]", "", raison_sociale_raw).strip()
    if len(parts) > 1:
        fournisseur_actuel = parts[1].strip()

    return {
        "nom du fichier": nom_du_fichier,
        "Raison sociale": raison_sociale,
        "Fournisseur actuel": fournisseur_actuel,
    }


def display_pdf(file):
    """
    Embeds the PDF file directly into the Streamlit app using an HTML iframe.
    """
    pdf_bytes = file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="800px" type="application/pdf"></iframe>'
    st.markdown(pdf_display, unsafe_allow_html=True)


def export_results_csv(results: List[Dict[str, Any]]) -> str:
    """
    Convert results to CSV format.
    """
    import csv
    from io import StringIO

    output = StringIO()

    if not results:
        return ""

    # Define CSV columns
    fieldnames = [
        "Fichier",
        "Raison sociale",
        "Fournisseur actuel",
        "Fournisseur",
        "Adresse",
        "Code Postal",
        "Ville",
        "PDL/PCE",
        "Segment",
        "Tarif Réglementé",
        "Echéance",
        "Statut",
        "Erreur",
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for result in results:
        filename_info = result.get("filename_info", {})
        extracted_data = result.get("extraction", {})

        if isinstance(extracted_data, str):
            try:
                extracted_data = json.loads(extracted_data)
            except json.JSONDecodeError:
                extracted_data = {}

        # Handle address
        address = extracted_data.get("adresse", {})
        if isinstance(address, str):
            full_address = address
        else:
            street_number = address.get("street_number", "")
            street_name = address.get("street_name", "")
            full_address = f"{street_number} {street_name}".strip()

        row = {
            "Fichier": filename_info.get("nom du fichier", result.get("filename", "")),
            "Raison sociale": filename_info.get("Raison sociale", ""),
            "Fournisseur actuel": filename_info.get("Fournisseur actuel", ""),
            "Fournisseur": extracted_data.get("fournisseur_actuel", ""),
            "Adresse": full_address,
            "Code Postal": extracted_data.get("code_postal", ""),
            "Ville": extracted_data.get("ville", ""),
            "PDL/PCE": extracted_data.get("reference_point_energie", ""),
            "Segment": extracted_data.get("segment_energie", ""),
            "Tarif Réglementé": (
                "Oui" if extracted_data.get("tarif_reglemente") else "Non"
            ),
            "Echéance": extracted_data.get("date_echeance", ""),
            "Statut": "Erreur" if result.get("error") else "Succès",
            "Erreur": result.get("error", ""),
        }

        writer.writerow(row)

    return output.getvalue()


def export_results_excel(results: List[Dict[str, Any]]) -> bytes:
    """
    Convert results to Excel format (XLSX).
    """
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Extraction Results"

    if not results:
        output = BytesIO()
        wb.save(output)
        return output.getvalue()

    # Define columns (same as CSV)
    headers = [
        "Fichier",
        "Raison sociale",
        "Fournisseur actuel",
        "Fournisseur",
        "Adresse",
        "Code Postal",
        "Ville",
        "PDL/PCE",
        "Segment",
        "Tarif Réglementé",
        "Echéance",
        "Statut",
        "Erreur",
    ]
    ws.append(headers)

    for result in results:
        filename_info = result.get("filename_info", {})
        extracted_data = result.get("extraction", {})

        if isinstance(extracted_data, str):
            try:
                extracted_data = json.loads(extracted_data)
            except json.JSONDecodeError:
                extracted_data = {}

        # Handle address
        address = extracted_data.get("adresse", {})
        if isinstance(address, str):
            full_address = address
        else:
            street_number = address.get("street_number", "")
            street_name = address.get("street_name", "")
            full_address = f"{street_number} {street_name}".strip()

        row = [
            filename_info.get("nom du fichier", result.get("filename", "")),
            filename_info.get("Raison sociale", ""),
            filename_info.get("Fournisseur actuel", ""),
            extracted_data.get("fournisseur_actuel", ""),
            full_address,
            extracted_data.get("code_postal", ""),
            extracted_data.get("ville", ""),
            extracted_data.get("reference_point_energie", ""),
            extracted_data.get("segment_energie", ""),
            "Oui" if extracted_data.get("tarif_reglemente") else "Non",
            extracted_data.get("date_echeance", ""),
            "Erreur" if result.get("error") else "Succès",
            result.get("error", ""),
        ]
        ws.append(row)

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def main():
    st.set_page_config(layout="wide", page_title="Extraction Factures")

    # Sidebar navigation
    st.sidebar.title("Navigation")
    page = st.sidebar.radio(
        "Choisir une page", ["Extraction Simple", "Extraction par Lot"]
    )

    if page == "Extraction Simple":
        single_pdf_page()
    else:
        batch_extraction_page()


def single_pdf_page():
    st.title("Extraction de données de factures d'énergie")
    st.markdown(
        "Téléchargez une facture d'énergie pour extraire automatiquement les informations clés."
    )

    st.markdown(
        """
    <style>
        .main .block-container {
            max-width: 100%;
            padding-top: 2rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # Input selection
    input_method = st.radio(
        "Méthode d'importation",
        ["📁 Télécharger un fichier", "🔗 Importer via un lien"],
        horizontal=True,
        key="input_method_radio"
    )

    uploaded_file = None
    url_input = ""

    if input_method == "📁 Télécharger un fichier":
        # Clear downloaded if switching to upload
        if "downloaded_pdf" in st.session_state and st.session_state.get("last_input_method") != "upload":
            del st.session_state.downloaded_pdf
            del st.session_state.downloaded_filename
        st.session_state.last_input_method = "upload"
        
        uploaded_file = st.file_uploader(
            "Télécharger un PDF", type=["pdf"], accept_multiple_files=False
        )
    else:
        st.session_state.last_input_method = "url"
        url_input = st.text_input(
            "Coller un lien (ex: Google Drive)",
            placeholder="https://drive.google.com/file/d/...",
            help="Prend en charge les liens Google Drive directs ou partagés."
        )

    with st.expander("Comment utiliser cette application"):
        st.markdown(
            """
        1. Cliquez sur "Parcourir les fichiers" pour sélectionner un fichier PDF de facture d'énergie
        2. Attendez que l'extraction des données soit terminée
        3. Vérifiez les informations extraites
        4. Téléchargez les données brutes au format JSON si nécessaire

        **Note** : L'application analyse la page 3 pour les factures ENGIE, et tout le document pour les autres fournisseurs.
        """
        )

    if uploaded_file is not None:
        st.success("Fichier téléchargé avec succès!")

        # Create two columns: 60% for PDF, 40% for results
        col_pdf, col_results = st.columns([0.6, 0.4], gap="medium")

        with col_pdf:
            st.subheader("📄 Aperçu du PDF")
            display_pdf(uploaded_file)
    
    elif url_input.strip():
        # Handle URL input
        if st.button("Charger le PDF depuis le lien", type="secondary"):
            with st.spinner("Téléchargement du fichier..."):
                pdf_content, filename, error = download_pdf_from_url(url_input)
                if error:
                    st.error(f"Erreur de téléchargement : {error}")
                else:
                    # Treat downloaded content as an uploaded file for consistent processing
                    # We store it in session state to keep it across reruns
                    st.session_state.downloaded_pdf = pdf_content
                    st.session_state.downloaded_filename = filename
                    st.success(f"Fichier '{filename}' chargé avec succès!")

    # Check if we have a file (either uploaded or downloaded)
    active_pdf = None
    active_filename = ""

    if uploaded_file:
        active_pdf = uploaded_file
        active_filename = uploaded_file.name
    elif "downloaded_pdf" in st.session_state:
        active_pdf = st.session_state.downloaded_pdf
        active_filename = st.session_state.downloaded_filename

    if active_pdf:
        # If we just switched or cleared, handle that UI-wise if needed
        # (Streamlit handles most of this automatically)

        # Create two columns: 60% for PDF, 40% for results
        col_pdf, col_results = st.columns([0.6, 0.4], gap="medium")

        with col_pdf:
            st.subheader("📄 Aperçu du PDF")
            # For BytesIO, we need to create a wrapper that has a 'getvalue' method if display_pdf expects it
            # Actually display_pdf uses file.getvalue() which BytesIO has.
            # But it also needs to be reset to 0
            if hasattr(active_pdf, "seek"):
                active_pdf.seek(0)
            display_pdf(active_pdf)

        with col_results:
            st.subheader("📋 Données extraites")

            # Get defaults based on filename
            defaults = get_extraction_defaults(active_filename)

            # Display detected info and controls
            c1, c2 = st.columns(2)
            with c1:
                st.info(f"**Fournisseur:** {defaults['supplier']}")
            with c2:
                # Default value for input
                default_pages_input = ""
                if defaults["first_page"] and defaults["last_page"]:
                    if defaults["first_page"] == defaults["last_page"]:
                        default_pages_input = str(defaults["first_page"])
                    else:
                        default_pages_input = (
                            f"{defaults['first_page']}-{defaults['last_page']}"
                        )

                pages_input = st.text_input(
                    "Pages",
                    value=default_pages_input,
                    help="Ex: 3, 1-3",
                    label_visibility="collapsed",
                    placeholder="Pages (ex: 3)",
                )

            if st.button(
                "Lancer l'extraction", type="primary", use_container_width=True
            ):
                with st.spinner("Extraction des données en cours..."):
                    try:
                        # Parse page input
                        first_page = None
                        last_page = None

                        if pages_input.strip():
                            try:
                                if "-" in pages_input:
                                    parts = pages_input.split("-")
                                    first_page = int(parts[0].strip())
                                    last_page = int(parts[1].strip())
                                else:
                                    first_page = int(pages_input.strip())
                                    last_page = int(pages_input.strip())
                            except ValueError:
                                st.warning(
                                    "Format de page invalide. Analyse de tout le document."
                                )
                                first_page = None
                                last_page = None

                        filename_data = parse_filename(active_filename)

                        result = extract_data(
                            active_pdf,
                            first_page=first_page,
                            last_page=last_page,
                            supplier=defaults["supplier"],
                            hints=defaults.get("hints"),
                        )

                        # Check for error in the top-level dictionary
                        if (
                            isinstance(result, dict)
                            and "error" in result
                            and "extraction" not in result
                        ):
                            st.error(f"Erreur: {result['error']}")

                        extracted_pdf_data = (
                            result.get("extraction")
                            if isinstance(result, dict)
                            else None
                        )
                        metadata = (
                            result.get("metadata", {})
                            if isinstance(result, dict)
                            else {}
                        )

                        # Display metadata (actual used values)
                        if metadata:
                            st.success(
                                f"Extraction terminée ({metadata.get('pages', 'Inconnu')})"
                            )

                        # Handle errors that might be returned with metadata
                        if (
                            isinstance(result, dict)
                            and "error" in result
                            and result["error"]
                        ):
                            st.error(f"Erreur: {result['error']}")

                        if not extracted_pdf_data and (
                            not isinstance(result, dict) or "error" not in result
                        ):
                            st.error("Aucune donnée extraite.")

                        if extracted_pdf_data:
                            if isinstance(extracted_pdf_data, str):
                                try:
                                    extracted_pdf_data = json.loads(extracted_pdf_data)
                                except json.JSONDecodeError:
                                    st.error("Erreur de format des données extraites")
                                    extracted_pdf_data = None

                            if extracted_pdf_data and not isinstance(
                                extracted_pdf_data, dict
                            ):
                                st.error("Format de données inattendu")
                                extracted_pdf_data = None

                            if extracted_pdf_data:
                                address = extracted_pdf_data.get("adresse", {})
                                if isinstance(address, str):
                                    full_address = address
                                else:
                                    street_number = address.get("street_number", "")
                                    street_name = address.get("street_name", "")
                                    full_address = (
                                        f"{street_number} {street_name}".strip()
                                    )

                                table_data = {
                                    "Champ": [
                                        "Fichier",
                                        "Raison sociale",
                                        "Fournisseur",
                                        "Adresse",
                                        "Code Postal",
                                        "Ville",
                                        "PDL/PCE",
                                        "Segment",
                                        "Tarif Réglementé",
                                        "Echéance",
                                    ],
                                    "Valeur": [
                                        filename_data["nom du fichier"],
                                        filename_data["Raison sociale"],
                                        extracted_pdf_data.get(
                                            "fournisseur_actuel", "Non spécifié"
                                        ),
                                        full_address,
                                        extracted_pdf_data.get(
                                            "code_postal", "Non spécifié"
                                        ),
                                        extracted_pdf_data.get(
                                            "ville", "Non spécifiée"
                                        ),
                                        extracted_pdf_data.get(
                                            "reference_point_energie", "Non spécifiée"
                                        ),
                                        extracted_pdf_data.get(
                                            "segment_energie", "Non spécifié"
                                        ),
                                        (
                                            "Oui"
                                            if extracted_pdf_data.get(
                                                "tarif_reglemente"
                                            )
                                            else "Non"
                                        ),
                                        extracted_pdf_data.get(
                                            "date_echeance", "Non spécifiée"
                                        ),
                                    ],
                                }

                                st.dataframe(
                                    table_data,
                                    column_config={
                                        "Champ": st.column_config.TextColumn(
                                            "Champ", width="medium"
                                        ),
                                        "Valeur": st.column_config.TextColumn(
                                            "Valeur", width="large"
                                        ),
                                    },
                                    hide_index=True,
                                    width="stretch",
                                )

                                json_data = json.dumps(
                                    extracted_pdf_data, indent=2, ensure_ascii=False
                                )
                                st.download_button(
                                    label="Télécharger JSON",
                                    data=json_data,
                                    file_name=f"extraction_{Path(active_filename).stem}.json",
                                    mime="application/json",
                                    use_container_width=True,
                                )

                    except Exception as e:
                        st.error(f"Erreur: {str(e)}")
                        st.exception(e)


def batch_extraction_page():
    st.title("Extraction par Lot de Factures d'Énergie")
    st.markdown(
        "Téléchargez plusieurs fichiers PDF ou un fichier ZIP contenant des factures d'énergie pour extraire automatiquement les informations clés."
    )

    st.markdown(
        """
    <style>
        .main .block-container {
            max-width: 100%;
            padding-top: 2rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }
    </style>
    """,
        unsafe_allow_html=True,
    )

    with st.expander("Comment utiliser l'extraction par lot"):
        st.markdown(
            """
        **Option 1 : Télécharger plusieurs fichiers PDF**
        1. Cliquez sur "Parcourir les fichiers" et sélectionnez plusieurs fichiers PDF
        2. Vous pouvez sélectionner plusieurs fichiers en maintenant Ctrl (Windows/Linux) ou Cmd (Mac)
        
        **Option 2 : Télécharger un fichier ZIP**
        1. Créez un fichier ZIP contenant tous vos fichiers PDF de factures d'énergie
        2. Cliquez sur "Parcourir les fichiers" pour sélectionner le fichier ZIP
        
        **Traitement**
        3. Choisissez le mode de traitement (séquentiel ou parallèle)
        4. Attendez que l'extraction des données soit terminée pour tous les fichiers
        5. Vérifiez les informations extraites pour chaque facture
        6. Téléchargez les résultats au format JSON ou CSV

        **Notes** :
        - Le mode parallèle est plus rapide pour plusieurs fichiers mais utilise plus de ressources
        - L'application utilise un mécanisme de retry avec 60 secondes d'attente en cas d'échec
        - Un estimé du temps restant est affiché pendant le traitement séquentiel
        """
        )

    # Upload method selection
    upload_method = st.radio(
        "Méthode de téléchargement",
        ["📁 Plusieurs fichiers PDF", "📦 Fichier ZIP"],
        horizontal=True,
    )

    uploaded_files = None

    if upload_method == "📁 Plusieurs fichiers PDF":
        uploaded_files = st.file_uploader(
            "Télécharger des fichiers PDF",
            type=["pdf"],
            accept_multiple_files=True,
            help="Sélectionnez plusieurs fichiers PDF (Ctrl+clic ou Cmd+clic pour sélection multiple)",
        )

        if uploaded_files:
            st.success(f"✓ {len(uploaded_files)} fichier(s) PDF téléchargé(s)")
            # Display list of uploaded files
            with st.expander("📋 Liste des fichiers"):
                for i, file in enumerate(uploaded_files, 1):
                    file_size = len(file.getvalue()) / 1024  # Size in KB
                    st.write(f"{i}. {file.name} ({file_size:.1f} KB)")

    else:  # ZIP file
        uploaded_zip = st.file_uploader(
            "Télécharger un fichier ZIP",
            type=["zip"],
            accept_multiple_files=False,
            help="Sélectionnez un fichier ZIP contenant des PDFs",
        )

        if uploaded_zip:
            st.success("✓ Fichier ZIP téléchargé avec succès!")
            st.info(f"📦 Nom du fichier: {uploaded_zip.name}")
            uploaded_files = uploaded_zip

    if uploaded_files:
        # Processing options
        col1, col2 = st.columns(2)
        with col1:
            processing_mode = st.radio(
                "Mode de traitement",
                ["Séquentiel", "Parallèle"],
                help="Le mode parallèle traite plusieurs fichiers simultanément (plus rapide mais plus gourmand en ressources)",
            )
        with col2:
            if processing_mode == "Parallèle":
                max_workers = st.slider(
                    "Nombre de workers parallèles",
                    min_value=2,
                    max_value=5,
                    value=3,
                    help="Nombre de fichiers traités simultanément",
                )
            else:
                max_workers = 1

        if st.button(
            "🚀 Lancer l'extraction par lot",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Extraction par lot en cours..."):
                results = batch_extraction(
                    uploaded_files,
                    parallel=(processing_mode == "Parallèle"),
                    max_workers=max_workers,
                )

            if results:
                st.success(f"✓ Extraction terminée! {len(results)} fichiers traités.")

                # Filter and display options
                st.markdown("---")
                st.subheader("📊 Résultats de l'extraction")

                # Filter options
                col1, col2, col3 = st.columns(3)
                with col1:
                    show_successful = st.checkbox("Afficher les succès", value=True)
                with col2:
                    show_errors = st.checkbox("Afficher les erreurs", value=True)
                with col3:
                    expand_all = st.checkbox("Tout développer", value=False)

                # Filter results
                filtered_results = []
                for result in results:
                    has_error = "error" in result and result.get("error")
                    if (has_error and show_errors) or (
                        not has_error and show_successful
                    ):
                        filtered_results.append(result)

                # Display results
                for i, result in enumerate(filtered_results):
                    has_error = "error" in result and result.get("error")
                    status_icon = "❌" if has_error else "✅"
                    filename = result.get("filename", f"Fichier {i + 1}")

                    with st.expander(
                        f"{status_icon} {filename}",
                        expanded=expand_all,
                    ):
                        # Check for errors
                        if has_error:
                            st.error(f"**Erreur:** {result['error']}")
                        else:
                            # Display extracted data
                            extracted_pdf_data = result.get("extraction")
                            filename_data = result.get("filename_info", {})
                            metadata = result.get("metadata", {})

                            if metadata:
                                st.success(
                                    f"Extraction réussie ({metadata.get('pages', 'Inconnu')})"
                                )

                            if extracted_pdf_data:
                                if isinstance(extracted_pdf_data, str):
                                    try:
                                        extracted_pdf_data = json.loads(
                                            extracted_pdf_data
                                        )
                                    except json.JSONDecodeError:
                                        st.error(
                                            "Erreur de format des données extraites"
                                        )
                                        extracted_pdf_data = None

                                if extracted_pdf_data and isinstance(
                                    extracted_pdf_data, dict
                                ):
                                    address = extracted_pdf_data.get("adresse", {})
                                    if isinstance(address, str):
                                        full_address = address
                                    else:
                                        street_number = address.get("street_number", "")
                                        street_name = address.get("street_name", "")
                                        full_address = (
                                            f"{street_number} {street_name}".strip()
                                        )

                                    table_data = {
                                        "Champ": [
                                            "Fichier",
                                            "Raison sociale",
                                            "Fournisseur",
                                            "Adresse",
                                            "Code Postal",
                                            "Ville",
                                            "PDL/PCE",
                                            "Segment",
                                            "Tarif Réglementé",
                                            "Echéance",
                                        ],
                                        "Valeur": [
                                            filename_data.get(
                                                "nom du fichier", "Non spécifié"
                                            ),
                                            filename_data.get(
                                                "Raison sociale", "Non spécifié"
                                            ),
                                            extracted_pdf_data.get(
                                                "fournisseur_actuel", "Non spécifié"
                                            ),
                                            full_address,
                                            extracted_pdf_data.get(
                                                "code_postal", "Non spécifié"
                                            ),
                                            extracted_pdf_data.get(
                                                "ville", "Non spécifiée"
                                            ),
                                            extracted_pdf_data.get(
                                                "reference_point_energie",
                                                "Non spécifiée",
                                            ),
                                            extracted_pdf_data.get(
                                                "segment_energie", "Non spécifié"
                                            ),
                                            "Oui"
                                            if extracted_pdf_data.get(
                                                "tarif_reglemente"
                                            )
                                            else "Non",
                                            extracted_pdf_data.get(
                                                "date_echeance", "Non spécifiée"
                                            ),
                                        ],
                                    }

                                    st.dataframe(
                                        table_data,
                                        column_config={
                                            "Champ": st.column_config.TextColumn(
                                                "Champ", width="medium"
                                            ),
                                            "Valeur": st.column_config.TextColumn(
                                                "Valeur", width="large"
                                            ),
                                        },
                                        hide_index=True,
                                        use_container_width=True,
                                    )

                # Download options
                st.markdown("---")
                st.subheader("💾 Télécharger les résultats")

                col1, col2, col3 = st.columns(3)

                with col1:
                    # JSON download
                    json_data = json.dumps(results, indent=2, ensure_ascii=False)
                    filename_base = (
                        Path(uploaded_files.name).stem
                        if not isinstance(uploaded_files, list)
                        else "batch_extraction"
                    )
                    st.download_button(
                        label="📄 Télécharger JSON",
                        data=json_data,
                        file_name=f"{filename_base}.json",
                        mime="application/json",
                        use_container_width=True,
                    )

                with col2:
                    # CSV download
                    csv_data = export_results_csv(results)
                    st.download_button(
                        label="📊 Télécharger CSV",
                        data=csv_data,
                        file_name=f"{filename_base}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                with col3:
                    # Excel download
                    excel_data = export_results_excel(results)
                    st.download_button(
                        label="📗 Télécharger Excel",
                        data=excel_data,
                        file_name=f"{filename_base}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )


if __name__ == "__main__":
    main()
