import io
import json
import os
import tempfile

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pdf2image import convert_from_path

# Load environment variables
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


def get_extraction_defaults(filename):
    """
    Determine default extraction settings based on filename.
    """
    supplier = "Autre"
    pages_desc = "Tout le document"
    first_page = None
    last_page = None

    filename_lower = filename.lower() if filename else ""

    # Check for ignored patterns
    for pattern in IGNORED_PATTERNS:
        if pattern in filename_lower:
            return {
                "supplier": "IGNORED",
                "pages_description": "N/A",
                "first_page": None,
                "last_page": None,
            }

    if "engie" in filename_lower:
        supplier = "ENGIE"
        pages_desc = "Page 3"
        first_page = 3
        last_page = 3
    elif "total energies" in filename_lower or "totalenergies" in filename_lower:
        supplier = "TOTAL ENERGIES"
        pages_desc = "Page 1"
        first_page = 1
        last_page = 1
    elif "gaz europeen" in filename_lower or "gaz européen" in filename_lower:
        supplier = "GAZ EUROPEEN"
        pages_desc = "Page 1"
        first_page = 1
        last_page = 1
    elif "gaz bordeaux" in filename_lower:
        supplier = "GAZ BORDEAUX"
        pages_desc = "Page 2"
        first_page = 2
        last_page = 2
    elif "gaz de paris" in filename_lower:
        supplier = "GAZ DE PARIS"
        pages_desc = "Tout le document"
        first_page = None
        last_page = None
    elif (
        "gaz tarif reglemente" in filename_lower
        or "gaz tarif réglementé" in filename_lower
    ):
        supplier = "GAZ TARIF REGLEMENTE"
        pages_desc = "Tout le document"
        first_page = None
        last_page = None
    elif "gaz tarif recouvrement" in filename_lower:
        supplier = "GAZ TARIF RECOUVREMENT"
        pages_desc = "Tout le document"
        first_page = None
        last_page = None
    elif "edf" in filename_lower:
        supplier = "EDF"
        pages_desc = "Page 3"
        first_page = 3
        last_page = 3
    elif "sefe" in filename_lower:
        supplier = "SEFE"
        pages_desc = "Page 2"
        first_page = 2
        last_page = 2
    elif (
        "gaz de france provalys" in filename_lower or "gaz de france" in filename_lower
    ):
        supplier = "GAZ DE FRANCE PROVALYS"
        pages_desc = "Tout le document"
        first_page = None
        last_page = None

    return {
        "supplier": supplier,
        "pages_description": pages_desc,
        "first_page": first_page,
        "last_page": last_page,
    }


def extract_data(pdf_input, first_page=None, last_page=None, supplier=None):
    """
    Extract data from a PDF file.

    Args:
        pdf_input: Can be either a file path (str) or a file-like object
        first_page: First page to extract (1-based index)
        last_page: Last page to extract (1-based index)

    Returns:
        dict: Extracted data in the specified schema or error message
    """
    try:
        # Metadata for response
        supplier_detected = "Inconnu"
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

        # If pages not specified, check if we should use defaults based on filename (fallback)
        # However, for the interactive workflow, we expect these to be passed.
        # We'll just use what's passed.

        # 1. Convert PDF to images
        if hasattr(pdf_input, "read"):  # It's a file-like object
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

        if supplier == "GAZ DE FRANCE PROVALYS":
            prompt = """
            Analyze this energy invoice. Extract the following:
            - Address: Split the street line into 'street_number' and 'street_name'.
            - 'Reference Point d'Energie': Look for 'Réf Acheminement Electricité' or 'Référence acheminement' (14 digits).
            - 'Segment' (look for codes like T1, T2, C5, C4 near 'Acheminement').
            - 'Date d'échéance' (Contract end date).
            - 'Tarif reglemente': True only if strictly TRV/Blue Tariff, else False (e.g. for 'Prix Fixe').
            """
        elif supplier in ["GAZ BORDEAUX", "GAZ DE PARIS"]:
            prompt = """
            Analyze this energy invoice. Extract the following:
            - Address: Split the street line into 'street_number' and 'street_name'.
            - 'Reference Point d'Energie': Look for 'N° Point de livraison' (14 digits).
            - 'Segment' (look for codes like T1, T2, C5, C4 near 'Acheminement').
            - 'Date d'échéance' (Contract end date).
            - 'Tarif reglemente': True only if strictly TRV/Blue Tariff, else False (e.g. for 'Prix Fixe').
            """
        elif supplier in ["GAZ TARIF REGLEMENTE", "GAZ TARIF RECOUVREMENT"]:
            prompt = """
            Analyze this energy invoice. Extract the following:
            - Address: Split the street line into 'street_number' and 'street_name'.
            - 'Reference Point d'Energie': Look for 'Point de comptage et d'estimation' or 'Point de comptage et d'estimalion' (14 digits).
            - 'Segment' (look for codes like T1, T2, C5, C4 near 'Acheminement').
            - 'Date d'échéance' (Contract end date).
            - 'Tarif reglemente': True only if strictly TRV/Blue Tariff, else False (e.g. for 'Prix Fixe').
            """
        else:
            prompt = """
            Analyze this energy invoice. Extract the following:
            - Address: Split the street line into 'street_number' and 'street_name'.
            - 'Reference Point d'Energie' or PDL/PCE (14 digits).
            - 'Segment' (look for codes like T1, T2, C5, C4 near 'Acheminement').
            - 'Date d'échéance' (Contract end date).
            - 'Tarif reglemente': True only if strictly TRV/Blue Tariff, else False (e.g. for 'Prix Fixe').
            """

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
