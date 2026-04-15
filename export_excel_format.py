import json
import csv
from pathlib import Path

INPUT_JSON = Path("data/extraction_results_all.json")
OUTPUT_CSV = Path("data/extraction_results_excel_format.csv")

# Exact columns requested by the user
CSV_COLUMNS = [
    "Fait par :",
    "Nom du fichier",
    "Fournisseur d'énergie",
    "SIREN (Emplacement/Libellés/Pièges)",
    "Raison sociale (Emplacement/Libellés/Pièges)",
    "Adresse postale (Emplacement/Libellés/Pièges)",
    "Code postal (Emplacement/Libellés/Pièges)",
    "Commune (Emplacement/Libellés/Pièges)",
    "Nom du site (Emplacement/Libellés/Pièges)",
    "Référence Point d’Énergie (PDL/PCE/PRM) (Emplacement/Libellés/Pièges)",
    "Adresse du site (Emplacement/Libellés/Pièges)",
    "Code postal",
    "Commune",
    "Segment énergie (C1-C5/T1-T4) (Emplacement/Libellés/Pièges)",
    "Date d’échéance du contrat (Emplacement/Libellés/Pièges)",
    "Distinguer si c'est facture PRO/PART"
]

def run():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for filename, result in data.items():
        # Get metadata
        supplier = result.get("metadata", {}).get("supplier", "UNKNOWN")
        
        # Get extraction dict
        extraction = result.get("extraction") or {}
        adresse_obj = extraction.get("adresse") or {}
        
        # Format the billing street address
        street_number = adresse_obj.get("street_number", "").strip()
        street_name = adresse_obj.get("street_name", "").strip()
        adresse_postale = f"{street_number} {street_name}".strip()

        row = {
            "Fait par :": "Auto-Extraction",
            "Nom du fichier": filename,
            "Fournisseur d'énergie": supplier,
            "SIREN (Emplacement/Libellés/Pièges)": extraction.get("siren_siret", ""),
            "Raison sociale (Emplacement/Libellés/Pièges)": extraction.get("raison_sociale", ""),
            "Adresse postale (Emplacement/Libellés/Pièges)": adresse_postale,
            "Code postal (Emplacement/Libellés/Pièges)": extraction.get("code_postal", ""),
            "Commune (Emplacement/Libellés/Pièges)": extraction.get("ville", ""),
            "Nom du site (Emplacement/Libellés/Pièges)": extraction.get("nom_du_site", ""),
            "Référence Point d’Énergie (PDL/PCE/PRM) (Emplacement/Libellés/Pièges)": extraction.get("reference_point_energie", ""),
            "Adresse du site (Emplacement/Libellés/Pièges)": extraction.get("adresse_site", ""),
            "Code postal": extraction.get("code_postal_site", ""),
            "Commune": extraction.get("ville_site", ""),
            "Segment énergie (C1-C5/T1-T4) (Emplacement/Libellés/Pièges)": extraction.get("segment_energie", ""),
            "Date d’échéance du contrat (Emplacement/Libellés/Pièges)": extraction.get("date_echeance", ""),
            "Distinguer si c'est facture PRO/PART": extraction.get("type_client", ""),
        }
        rows.append(row)

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} rows to {OUTPUT_CSV}")

if __name__ == "__main__":
    run()
