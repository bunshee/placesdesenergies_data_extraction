"""
batch_extract.py
----------------
Batch extraction script: processes all PDFs in data/bills/ and outputs
  - data/extraction_results_all.json
  - data/extraction_results_all.csv

Usage:
    python batch_extract.py               # process all PDFs
    python batch_extract.py --resume      # skip PDFs already in output JSON
"""
import csv
import json
import os
import sys
import time
import argparse
from pathlib import Path

# Add src to python path for local imports
sys.path.append(os.path.abspath("src"))

from dotenv import load_dotenv
load_dotenv()

from places_extractor.core.extractor import extract_data, SUPPLIER_RULES

# ── Paths ────────────────────────────────────────────────────────────────────
BILLS_DIR        = Path("data/bills")
ROUTING_FILE     = Path("data/routing_results.json")
RULES_ALL_FILE   = Path("data/supplier_rules_all.json")
OUTPUT_JSON      = Path("data/extraction_results_all.json")
OUTPUT_CSV       = Path("data/extraction_results_all.csv")

# ── CSV columns (maps to all 13 requested fields + extras) ───────────────────
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


def _get_page_range(supplier_name: str) -> tuple[int | None, int | None]:
    """Return (first_page, last_page) from SUPPLIER_RULES, matched by supplier value."""
    supplier_upper = supplier_name.upper()
    for key, rules in SUPPLIER_RULES.items():
        if key.upper() == supplier_upper:
            first, last = rules.get("default_pages", (1, 2))
            return first, last
    # fallback
    return 1, 2


def _build_hints(supplier_name: str, all_rules: dict) -> list[str]:
    """Build hint lines from supplier_rules_all.json for the given supplier."""
    supplier_rules = all_rules.get(supplier_name, [])
    hints: list[str] = []
    if supplier_rules:
        hints.append(f"EXTREMELY IMPORTANT HINTS FROM KNOWLEDGE BASE FOR {supplier_name}:")
        for hint_str in supplier_rules:
            hints.append(f"- {hint_str}")
    else:
        hints.append(
            f"No specific knowledge base rules found for {supplier_name}. Use general extraction."
        )
    return hints


def _flatten_row(filename: str, supplier: str, result: dict) -> dict:
    """Flatten nested extraction result into a flat CSV row matching Excel."""
    row = {col: "" for col in CSV_COLUMNS}
    
    extraction = result.get("extraction") or {}
    adresse_obj = extraction.get("adresse") or {}
    
    # Format the billing street address
    street_number = adresse_obj.get("street_number", "").strip()
    street_name = adresse_obj.get("street_name", "").strip()
    adresse_postale = f"{street_number} {street_name}".strip()

    row["Fait par :"] = "Auto-Extraction"
    row["Nom du fichier"] = filename
    row["Fournisseur d'énergie"] = supplier
    row["SIREN (Emplacement/Libellés/Pièges)"] = extraction.get("siren_siret", "")
    row["Raison sociale (Emplacement/Libellés/Pièges)"] = extraction.get("raison_sociale", "")
    row["Adresse postale (Emplacement/Libellés/Pièges)"] = adresse_postale
    row["Code postal (Emplacement/Libellés/Pièges)"] = extraction.get("code_postal", "")
    row["Commune (Emplacement/Libellés/Pièges)"] = extraction.get("ville", "")
    row["Nom du site (Emplacement/Libellés/Pièges)"] = extraction.get("nom_du_site", "")
    row["Référence Point d’Énergie (PDL/PCE/PRM) (Emplacement/Libellés/Pièges)"] = extraction.get("reference_point_energie", "")
    row["Adresse du site (Emplacement/Libellés/Pièges)"] = extraction.get("adresse_site", "")
    row["Code postal"] = extraction.get("code_postal_site", "")
    row["Commune"] = extraction.get("ville_site", "")
    row["Segment énergie (C1-C5/T1-T4) (Emplacement/Libellés/Pièges)"] = extraction.get("segment_energie", "")
    row["Date d’échéance du contrat (Emplacement/Libellés/Pièges)"] = extraction.get("date_echeance", "")
    row["Distinguer si c'est facture PRO/PART"] = extraction.get("type_client", "")
    return row


def run(resume: bool = False) -> None:
    # ── Load routing results ─────────────────────────────────────────────────
    if not ROUTING_FILE.exists():
        print(f"ERROR: {ROUTING_FILE} not found. Run routing first.")
        sys.exit(1)
    with open(ROUTING_FILE, "r", encoding="utf-8") as f:
        routing: dict = json.load(f)

    # ── Load supplier rules ──────────────────────────────────────────────────
    with open(RULES_ALL_FILE, "r", encoding="utf-8") as f:
        all_rules: dict = json.load(f)

    # ── Load existing results if resuming ────────────────────────────────────
    output_results: dict = {}
    if resume and OUTPUT_JSON.exists():
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            output_results = json.load(f)
        print(f"Resuming: {len(output_results)} already processed.")

    # ── Enumerate all PDFs in bills dir ─────────────────────────────────────
    all_pdfs = sorted(BILLS_DIR.glob("*.pdf"))
    total     = len(all_pdfs)
    processed = 0
    skipped   = 0
    errors    = 0

    print(f"\nFound {total} PDFs in {BILLS_DIR}\n{'─' * 60}")

    for pdf_path in all_pdfs:
        filename = pdf_path.name

        # Resume: skip if already done
        if resume and filename in output_results:
            print(f"[SKIP-RESUME] {filename}")
            skipped += 1
            continue

        # Look up routing result
        routing_entry = routing.get(filename)
        if routing_entry is None:
            print(f"[SKIP-NO-ROUTE] {filename} — not in routing_results.json")
            skipped += 1
            output_results[filename] = {
                "extraction": None,
                "metadata": {"supplier": "N/A", "pages": "N/A"},
                "error": "Not found in routing_results.json",
            }
            continue

        supplier = routing_entry.get("supplier", "UNKNOWN")
        if supplier == "UNKNOWN":
            print(f"[SKIP-UNKNOWN] {filename}")
            skipped += 1
            output_results[filename] = {
                "extraction": None,
                "metadata": {"supplier": "UNKNOWN", "pages": "N/A"},
                "error": "Supplier UNKNOWN — skipped",
            }
            continue

        print(f"[PROCESSING] {filename}  →  {supplier}")
        first_page, last_page = _get_page_range(supplier)
        hints = _build_hints(supplier, all_rules)

        result = extract_data(
            pdf_input=str(pdf_path),
            first_page=first_page,
            last_page=last_page,
            supplier=supplier,
            hints=hints,
        )

        if result.get("error"):
            print(f"  ✗ ERROR: {result['error']}")
            errors += 1
        else:
            extraction = result.get("extraction", {})
            ref = (extraction or {}).get("reference_point_energie", "—")
            seg = (extraction or {}).get("segment_energie", "—")
            print(f"  ✓ OK  |  PDL/PCE: {ref}  |  Segment: {seg}")
            processed += 1

        output_results[filename] = result

        # Save after every PDF so progress is not lost on crash
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(output_results, f, ensure_ascii=False, indent=2)

        # Polite delay to avoid API rate limits
        time.sleep(0.5)

    # ── Build CSV ────────────────────────────────────────────────────────────
    rows = []
    for filename, result in output_results.items():
        routing_entry = routing.get(filename, {})
        supplier = routing_entry.get("supplier", result.get("metadata", {}).get("supplier", ""))
        rows.append(_flatten_row(filename, supplier, result))

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"Total PDFs   : {total}")
    print(f"Processed OK : {processed}")
    print(f"Skipped      : {skipped}")
    print(f"Errors       : {errors}")
    print(f"\nOutputs:")
    print(f"  JSON → {OUTPUT_JSON}")
    print(f"  CSV  → {OUTPUT_CSV}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch extract energy invoice data from PDFs.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip PDFs already present in the output JSON file.",
    )
    args = parser.parse_args()
    run(resume=args.resume)
