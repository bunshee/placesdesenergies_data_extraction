from typing import Optional, Literal
from pydantic import BaseModel, Field
from datetime import date

EnergyReferenceType = Literal["PCE", "PDL", "PRM"]


class EnergyInvoiceRecord(BaseModel):
    document_date: Optional[date] = Field(None, description="YYYY-MM-DD si connu")
    supplier: Optional[str]
    site_name: Optional[str]
    energy_reference: Optional[str]
    energy_reference_type: Optional[EnergyReferenceType]
    energy_reference_length: Optional[int]
    address_consumption: Optional[str]
    address_billing: Optional[str]
    postal_code: Optional[str]
    city: Optional[str]
    energy_segment: Optional[str]  # Gaz/Électricité
    offer_tags: list[str] = []
    tariff_segment: Optional[str]  # ex: T2, C3
    contract_expiry_date: Optional[date]
    contract_start_date: Optional[str]
    termination_notice: Optional[str]
    renewal_terms: Optional[str]
    client_siren_siret: Optional[str]
    regulated_tariff: Optional[Literal["Oui", "Non"]]
