from io import BytesIO
from typing import List

from openpyxl import Workbook

from src.models.schema import EnergyInvoiceRecord


def _clean_value(value):
    """Replace 'N/A' with empty string, keep None as None."""
    if value == "N/A":
        return ""
    return value


def export_to_excel(data: List[EnergyInvoiceRecord], output: BytesIO):
    wb = Workbook()
    ws = wb.active
    ws.title = "Extracted Energy Invoices"

    # Define headers
    headers = [
        "Nom du site",
        "Référence Point d’Énergie",
        "Adresse",
        "Code postal",
        "Commune",
        "Segment énergie",
        "Tarif reglementé (Oui/Non)",
        "Date d’échéance du contrat",
        "Fournisseur actuel",
        "Préavis Résiliation",
        "SIREN/SIRET",
    ]
    ws.append(headers)

    # Append data
    for record in data:
        ws.append(
            [
                _clean_value(record.site_name),
                _clean_value(record.energy_reference),
                _clean_value(record.address_consumption),
                _clean_value(record.postal_code),
                _clean_value(record.city),
                _clean_value(record.energy_segment),
                "Oui" if record.regulated_tariff == "Oui" else "Non",
                (
                    record.contract_expiry_date.strftime("%Y-%m-%d")
                    if record.contract_expiry_date
                    else ""
                ),
                _clean_value(record.supplier),
                _clean_value(record.termination_notice),
                _clean_value(record.client_siren_siret),
            ]
        )

    wb.save(output)
