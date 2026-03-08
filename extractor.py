import io
import json
import os
import tempfile

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pdf2image import convert_from_path

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

IGNORED_PATTERNS = [
    "abypas",
    "adi incendie",
    "adi sas",
    "albasini",
    "tmd securite",
    "secat",
    "scem",
    "sani chauff",
    "s.p.m. nicolai",
    "rym",
    "riva paysages",
    "renovtoiture",
    "plagnol nettoyage",
    "nordtherm_",
    "my renovation et neuf",
    "long chauffage",
    "h.saint paul",
    "gesten",
    "gazflash",
    "albert & fils",
    "bouvier",
    "disdero",
    "delostal",
    "charles pereira",
]

SUPPLIER_RULES = {
    "ENGIE": {
        "keywords": ["engie", "gdf", "gaz de france"],
        "default_pages": (1, 3),
        "hints": [
            "PDL/PRM/PCE: page 3, encadré 'votre point de livraison' (1ère ligne).",
            "Nom du site: page 3, encadré 'votre point de livraison', ligne 'désignation du site'.",
            "Adresse du site: page 3, encadré 'votre point de livraison', ligne 'adresse de livraison'.",
            "Segment: page 3, encadré 'votre contrat', ligne 'Acheminement'.",
            "Date d'échéance: page 3, encadré 'votre contrat', ligne 'Date d'échéance'.",
            "Propriétaire/Raison sociale: page 1, encadré 'vos références' (2ème ligne)."
        ]
    },
    "EDF": {
        "keywords": ["edf"],
        "default_pages": (1, 4),
        "hints": [
            "PDL/PRM/PCE: page 4 (ou 2), encadré 'données point de livraison' ou 'Réf Acheminement'.",
            "Nom du site: n'apparaît pas souvent, sinon page 1 'Nom du client'.",
            "Adresse du site: page 4 (ou 2), encadré 'données point de livraison'.",
            "Segment: page 4 (ou 2), encadré 'données de comptage', ligne 'Acheminement' (ex: C4, C5).",
            "Date d'échéance: page 4 (ou 2), encadré 'venant à échéance le'.",
            "Tarif Bleu: si 'Tarif Bleu' est mentionné, c'est un tarif réglementé."
        ]
    },
    "TOTAL ENERGIES": {
        "keywords": ["total energies", "totalenergies"],
        "default_pages": (1, 3),
        "hints": [
            "PDL/PCE: page 2, intitulé 'ID' juste après 'PDL/PCE'.",
            "Nom du site: page 3, 'Adresse du site'.",
            "Adresse du site: page 2, intitulé 'Lieu de consommation'.",
            "Segment: page 2, intitulé 'Segment'.",
            "Date d'échéance: page 3, intitulé 'Date de fin de contrat'."
        ]
    },
    "EKWATEUR": {
        "keywords": ["ekwateur"],
        "default_pages": (1, 5),
        "hints": [
            "PDL: page 2, tableau 1ère colonne, ou page 3, 2ème tableau.",
            "Nom du site: page 1 ou 2.",
            "Segment: page 3, 2ème tableau (puissance souscrite).",
            "Facture PRO: chercher logo vert avec mention 'PRO'."
        ]
    },
    "ENGIE PRO": {
        "keywords": ["engie pro", "direction des clients professionnels"],
        "default_pages": (1, 2),
        "hints": [
            "PDL/PCE: page 1, encadré 'lieu de consommation'.",
            "Nom du site: page 1, encadré 'lieu de consommation', 2ème flèche.",
            "Segment: page 2, encadré 'votre contrat d'énergie', ligne 'Segment' ou 'Tarif d'acheminement'.",
            "Date d'échéance: page 2, encadré 'votre contrat d'énergie', ligne 'échéance'."
        ]
    },
    "SEFE": {
        "keywords": ["sefe"],
        "default_pages": (1, 2),
        "hints": [
            "PCE: page 2, intitulé 'Réf. du Point de Comptage (PCE)'.",
            "Adresse du site: page 2, intitulé 'Adresse' sous 'Nom du site'.",
            "Segment: page 2, intitulé 'Tarif d'acheminement'.",
            "Date d'échéance: page 2, intitulé 'Echéance contrat'."
        ]
    },
    "GAZ EUROPEEN": {
        "keywords": ["gaz europeen", "gaz européen"],
        "default_pages": (1, 2),
        "hints": [
            "PCE: page 1, encadré 'Mes références', intitulé 'Référence du PCE'.",
            "Lieu de consommation: page 1, encadré 'Mes références'.",
            "Segment: page 2, intitulé 'Profil' (ex: T1, T2)."
        ]
    },
    "PROXELIA": {
        "keywords": ["proxelia"],
        "default_pages": (1, 1),
        "hints": [
            "PDL: page 1, intitulé 'POINT DU LIVRAISON (PDL)'.",
            "Segment: page 1, intitulé 'SEGMENT'.",
            "Date d'échéance: page 1, intitulé 'DATE D'ECHEANCE DU CONTRAT'."
        ]
    },
    "VATTENFALL": {
        "keywords": ["vattenfall"],
        "default_pages": (1, 2),
        "hints": [
            "PRM: page 1, en haut à gauche sous 'vos références'.",
            "Offre: page 1, à côté de 'ECO PRO'."
        ]
    },
    "ILEK": {
        "keywords": ["ilek"],
        "default_pages": (1, 1),
        "hints": [
            "PDL: page 1, au centre à gauche intitulé 'Point de livraison'.",
            "Lieu de consommation: page 1, en haut à gauche."
        ]
    },
    "LA BELLENERGIE": {
        "keywords": ["la bellenergie", "labellenergie"],
        "default_pages": (1, 1),
        "hints": [
            "PDL: page 1, encadré 'VOTRE SITE DE CONSOMMATION' intitulé 'Point de Livraison'."
        ]
    },
    "OHM ENERGIE": {
        "keywords": ["ohm energie", "ohm énergie", "ohmenergie", "ohménergie", "ohm_energie"],
        "default_pages": (1, 2),
        "hints": [
            "PDL: page 2, encadré 'Détail pour le point de livraison'."
        ]
    },
    "YELI": {
        "keywords": ["yeli", "yéli"],
        "default_pages": (1, 1),
        "hints": [
            "Réf ext (PDL): page 1, en haut à gauche intitulé 'réf ext'.",
            "Echéance: page 1, encadré 'Contrat' intitulé 'date d'échéance'."
        ]
    },
    "DYNEFF": {
        "keywords": ["dyneff"],
        "default_pages": (1, 2),
        "hints": [
            "PDL: page 2, en haut au centre intitulé 'Point de livraison (PDL)'.",
            "Segment: page 2, intitulé 'Catégorie de PDL'."
        ]
    },
    "AUTRE": {
        "keywords": [],
        "default_pages": (1, 2),
        "hints": [
            "Chercher le PDL/PCE (14 chiffres).",
            "Chercher la date d'échéance ou fin de contrat.",
            "Identifier le segment (C1-C5 ou T1-T4) basé sur la puissance ou la consommation."
        ]
    }
}


def get_extraction_defaults(filename):
    """
    Determine default extraction settings based on filename.
    """
    supplier = "Autre"
    pages_desc = "Tout le document"
    first_page = None
    last_page = None

    filename_lower = filename.lower() if filename else ""

    for pattern in IGNORED_PATTERNS:
        if pattern in filename_lower:
            return {
                "supplier": "IGNORED",
                "pages_description": "N/A",
                "first_page": None,
                "last_page": None,
                "hints": []
            }

    # Detect supplier from keywords
    detected_key = "AUTRE"
    for key, rules in SUPPLIER_RULES.items():
        if any(kw in filename_lower for kw in rules.get("keywords", [])):
            detected_key = key
            break

    rules = SUPPLIER_RULES[detected_key]
    first, last = rules.get("default_pages", (None, None))
    
    return {
        "supplier": detected_key,
        "pages_description": f"Pages {first}-{last}" if first and last else "Tout le document",
        "first_page": first,
        "last_page": last,
        "hints": rules.get("hints", [])
    }


def extract_data(pdf_input, first_page=None, last_page=None, supplier=None, hints=None):
    """
    Extract data from a PDF file.

    Args:
        pdf_input: Can be either a file path (str) or a file-like object
        first_page: First page to extract (1-based index)
        last_page: Last page to extract (1-based index)
        supplier: Energy supplier for specific extraction rules
        hints: Optional specific extraction hints for the supplier

    Returns:
        dict: Extracted data in the specified schema or error message
    """
    try:
        # Metadata for response
        supplier_detected = supplier or "Inconnu"
        if supplier == "IGNORED":
            return {
                "extraction": None,
                "metadata": {
                    "supplier": supplier_detected,
                    "pages": "N/A",
                },
                "error": "Document ignoré : ce fichier n'est pas une facture d'énergie reconnue.",
            }

        pages_description = (
            f"Pages {first_page}-{last_page}"
            if first_page and last_page
            else "Tout le document"
        )

        # 1. Convert PDF to images
        if hasattr(pdf_input, "read"):
            # Save to a temporary file since convert_from_path needs a file path
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                pdf_input.seek(0)  # Ensure we're at the start of the file
                tmp.write(pdf_input.read())
                tmp_path = tmp.name

            try:
                pages = convert_from_path(
                    tmp_path, first_page=first_page, last_page=last_page, dpi=200
                )
            except Exception as e:
                return {
                    "extraction": None,
                    "metadata": {
                        "supplier": supplier_detected,
                        "pages": pages_description,
                    },
                    "error": f"Error converting PDF to image: {str(e)}",
                }
            finally:
                # Clean up the temporary file
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        else:  # It's a file path
            print(f"Processing PDF: {pdf_input}")
            try:
                pages = convert_from_path(
                    str(pdf_input), first_page=first_page, last_page=last_page, dpi=200
                )
            except Exception as e:
                return {
                    "extraction": None,
                    "metadata": {
                        "supplier": supplier_detected,
                        "pages": pages_description,
                    },
                    "error": f"Error opening PDF file: {str(e)}",
                }

        if not pages:
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": pages_description},
                "error": "No pages found in the PDF. Make sure the PDF has at least 3 pages.",
            }

        # 2. Prepare Images for API
        image_parts = []
        try:
            for page_img in pages:
                img_byte_arr = io.BytesIO()
                page_img.save(img_byte_arr, format="JPEG")
                img_bytes = img_byte_arr.getvalue()
                image_parts.append(
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                )
        except Exception as e:
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": pages_description},
                "error": f"Error preparing images for processing: {str(e)}",
            }

        # Check if API key is set
        if not GEMINI_API_KEY:
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": pages_description},
                "error": "GEMINI_API_KEY environment variable is not set. "
                "Please create a .env file with your API key.",
            }

        # 3. Define Schema & Prompt
        schema = {
            "type": "OBJECT",
            "properties": {
                "adresse": {
                    "type": "OBJECT",
                    "properties": {
                        "street_number": {
                            "type": "STRING",
                            "description": "The number part of the address (e.g. 643)",
                        },
                        "street_name": {
                            "type": "STRING",
                            "description": "The street name part (e.g. AVENUE DE MAZARGUES)",
                        },
                    },
                    "required": ["street_number", "street_name"],
                },
                "code_postal": {"type": "STRING"},
                "ville": {"type": "STRING"},
                "fournisseur_actuel": {"type": "STRING"},
                "nom_du_site": {"type": "STRING"},
                "reference_point_energie": {"type": "STRING"},
                "segment_energie": {"type": "STRING"},
                "tarif_reglemente": {"type": "BOOLEAN"},
                "date_echeance": {"type": "STRING", "description": "Format YYYY-MM-DD"},
            },
            "required": ["adresse", "code_postal", "ville"],
        }

        # Base prompt
        prompt = f"""
        Analyze this energy invoice from supplier: {supplier_detected}.
        Extract the following:
        - Address: Split the street line into 'street_number' and 'street_name'.
        - 'Reference Point d'Energie' or PDL/PCE (14 digits).
        - 'Segment': FORCIBLY return one of these codes ONLY: C1, C2, C3, C4, C5 (Electricity) or T1, T2, T3, T4 (Gas).
          Use these rules to identify/verify:
          * C5: Power <= 36 kVA, Low Voltage (BT)
          * C4: Power 37-250 kVA, Low Voltage (BT)
          * C3: Power <= 250 kVA, High Voltage (HTA)
          * C2: Power > 250 kVA, High Voltage (HTA)
          * C1: Power > 40,000 kVA, High Voltage (HT)
          * T1: Gas < 6 MWh/year
          * T2: Gas 6-300 MWh/year
          * T3: Gas 300-5000 MWh/year
          * T4: Gas > 5000 MWh/year
        - 'Date d'échéance' (Contract end date). Format: YYYY-MM-DD.
        - 'Tarif reglemente': True only if strictly TRV/Blue Tariff, else False (e.g. for 'Prix Fixe').
        """

        # Add specific hints if available
        if hints:
            prompt += "\nSpecific extraction hints for this supplier:\n"
            for hint in hints:
                prompt += f"- {hint}\n"

        # Special rule for eDF_Facture files (historical check)
        check_name = getattr(pdf_input, "name", str(pdf_input))
        if "eDF_Facture" in os.path.basename(check_name):
            prompt += "\n- 'nom_du_site': Look for value under 'Nom du client'."
            prompt += "\n- 'date_echeance': Look for date near 'Venant à échéance' or 'Date d'échéance'."

        # 4. Call Gemini 2.5 Flash
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)

            # Construct content parts: prompt + all images
            content_parts = [types.Part.from_text(text=prompt)] + image_parts

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Content(
                        role="user",
                        parts=content_parts,
                    )
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.0,
                ),
            )

            # Parse the response
            if hasattr(response, "text"):
                try:
                    # If it's a JSON string, parse it
                    result = json.loads(response.text)
                    # Ensure the result has the expected structure
                    if not isinstance(result, dict):
                        return {
                            "extraction": None,
                            "metadata": {
                                "supplier": supplier_detected,
                                "pages": pages_description,
                            },
                            "error": f"Unexpected response format: {response.text[:200]}...",
                        }

                    # Return result AND metadata
                    return {
                        "extraction": result,
                        "metadata": {
                            "supplier": supplier_detected,
                            "pages": pages_description,
                        },
                    }
                except (json.JSONDecodeError, AttributeError) as e:
                    return {
                        "extraction": None,
                        "metadata": {
                            "supplier": supplier_detected,
                            "pages": pages_description,
                        },
                        "error": f"Error parsing API response: {str(e)}\nResponse: {response.text[:200]}...",
                    }

        except Exception as e:
            return {"error": f"Error calling Gemini API: {str(e)}"}

    except Exception as e:
        return {"error": f"Error processing PDF: {str(e)}"}


if __name__ == "__main__":
    # Run
    filename = "./Citya Perier Immobilier_8280_285_20250515 - ENGIE - GDF-GAZ (VRT) - ENGIE.pdf"
    print(extract_data(filename))
