from typing import TypedDict, Optional, List, Union, Any

class Address(TypedDict):
    street_number: str
    street_name: str

class ExtractionResult(TypedDict):
    # --- Billing address (adresse de facturation) ---
    adresse: Address          # street number + street name of billing address
    code_postal: str          # postal code of billing address
    ville: str                # city/commune of billing address
    # --- Site / consumption address (lieu de livraison / consommation) ---
    adresse_site: Optional[str]       # full street address of consumption site
    code_postal_site: Optional[str]   # postal code of consumption site
    ville_site: Optional[str]         # city/commune of consumption site
    # --- Contract / identification fields ---
    fournisseur_actuel: str
    nom_du_site: str
    reference_point_energie: str
    segment_energie: str
    tarif_reglemente: bool
    date_echeance: str  # Format YYYY-MM-DD
    siren_siret: Optional[str]
    raison_sociale: Optional[str]
    type_client: Optional[str]
    transcription_pdl_area: Optional[str]

type ExtractionDefaults = dict[str, Any]
type APIResult = dict[str, Any]
