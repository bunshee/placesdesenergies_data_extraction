from io import BytesIO
from typing import List

from openpyxl import Workbook
from src.models.schema import EnergyInvoiceRecord


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
                record.site_name,
                record.energy_reference,
                record.address_consumption,
                record.postal_code,
                record.city,
                record.energy_segment,
                "Oui" if record.regulated_tariff == "Oui" else "Non",
                (
                    record.contract_expiry_date.strftime("%Y-%m-%d")
                    if record.contract_expiry_date
                    else None
                ),
                record.supplier,
                record.termination_notice,
                record.client_siren_siret,
            ]
        )

    wb.save(output)
