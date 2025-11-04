from typing import Optional, Literal
from pydantic import BaseModel, Field

EnergyReferenceType = Literal["PCE", "PDL", "PRM"]


class EnergyInvoiceRecord(BaseModel):
    is_energy_invoice: Optional[bool] = Field(
        None, description="Model judgement whether the document is an energy invoice"
    )
    document_date: Optional[str] = Field(None, description="YYYY-MM-DD si connu")
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
    contract_expiry_date: Optional[str]
    contract_start_date: Optional[str]
    termination_notice: Optional[str]
    renewal_terms: Optional[str]
    client_siren_siret: Optional[str]
    regulated_tariff: Optional[Literal["Oui", "Non"]]


class ExtractionLog(BaseModel):
    file_path: str
    num_pages: int
    supplier_confidence: Optional[float] = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
