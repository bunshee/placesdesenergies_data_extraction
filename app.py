import base64
import json
import re
from pathlib import Path

import streamlit as st

from extractor import extract_data, get_extraction_defaults


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
    This avoids converting to images and resolves st.image warnings.
    """
    # Read file as bytes
    pdf_bytes = file.getvalue()

    # Encode to base64
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    # Create the HTML iframe
    pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="800px" type="application/pdf"></iframe>'

    # Render HTML
    st.markdown(pdf_display, unsafe_allow_html=True)


def main():
    st.title("Extraction de données de factures d'énergie")
    st.markdown(
        "Téléchargez une facture d'énergie pour extraire automatiquement les informations clés."
    )

    st.markdown(
        """
    <style>
        .main .block-container {
            max-width: 1200px;
            padding-top: 2rem;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 40px;
            padding: 0 16px;
        }
    </style>
    """,
        unsafe_allow_html=True,
    )

    uploaded_file = st.file_uploader(
        "Télécharger un PDF", type=["pdf"], accept_multiple_files=False
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

        tab1, tab2 = st.tabs(["📄 Aperçu du PDF", "📋 Données extraites"])

        with tab1:
            # Displaying embedded PDF instead of images
            display_pdf(uploaded_file)

        with tab2:
            st.subheader("Données extraites")

            # Get defaults based on filename
            defaults = get_extraction_defaults(uploaded_file.name)

            # Display detected info and controls
            col1, col2 = st.columns(2)
            with col1:
                st.info(f"**Fournisseur détecté:** {defaults['supplier']}")
            with col2:
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
                    "Pages à analyser (ex: 3, 1-3, ou vide pour tout)",
                    value=default_pages_input,
                    help="Laissez vide pour analyser tout le document. Entrez un numéro (ex: 3) ou une plage (ex: 1-3).",
                )

            if st.button("Lancer l'extraction", type="primary"):
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

                        filename_data = parse_filename(uploaded_file.name)

                        result = extract_data(
                            uploaded_file, first_page=first_page, last_page=last_page
                        )

                        # Check for error in the top-level dictionary
                        if (
                            isinstance(result, dict)
                            and "error" in result
                            and "extraction" not in result
                        ):
                            st.error(f"Erreur lors de l'extraction: {result['error']}")
                            # We continue to show what we can if possible, but usually return here

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
                                f"Extraction terminée pour: {metadata.get('supplier', 'Inconnu')} ({metadata.get('pages', 'Inconnu')})"
                            )

                        # Handle errors that might be returned with metadata
                        if (
                            isinstance(result, dict)
                            and "error" in result
                            and result["error"]
                        ):
                            st.error(f"Erreur lors de l'extraction: {result['error']}")

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
                                        "Fournisseur actuel",
                                        "Adresse",
                                        "Code Postal",
                                        "Ville",
                                        "Référence PDL/PCE",
                                        "Segment",
                                        "Tarif Réglementé",
                                        "Date d'échéance",
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

                                st.subheader("📋 Informations extraites")

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
                                    # Updated according to the warning: use 'stretch' for full width
                                    width="stretch",
                                )

                                json_data = json.dumps(
                                    extracted_pdf_data, indent=2, ensure_ascii=False
                                )
                                st.download_button(
                                    label="Télécharger les données brutes (JSON)",
                                    data=json_data,
                                    file_name=f"extraction_{Path(uploaded_file.name).stem}.json",
                                    mime="application/json",
                                )

                    except Exception as e:
                        st.error(f"Une erreur est survenue: {str(e)}")
                        st.exception(e)


if __name__ == "__main__":
    main()
