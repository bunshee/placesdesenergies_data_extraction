import re
import os
from enum import Enum
from pydantic import BaseModel, Field
from google import genai

class SupplierEnum(str, Enum):
    ALTERNA_ENERGIE = "Alterna énergie"
    DYNEFF = "Dyneff"
    EDF_ENTREPRISES = "EDF Entreprises"
    EKWATEUR = "Ekwateur"
    ELECTRICITE_DE_SAVOIE = "Électricité de Savoie"
    ENDESA_FRANCE = "Endesa France"
    ENERGIES_DU_SANTERRE = "Energies du Santerre"
    ENGIE = "Engie"
    ENGIE_PRO = "Engie Pro"
    ES = "ÉS"
    GAZ_EUROPEEN = "GAZ EUROPEEN"
    GEG = "Gaz électricité de Grenoble (GEG)"
    GEDIA_ENERGIES_SERVICES = "Gedia Energies & Services"
    HELLIO = "Héllio"
    ILEK = "ilek"
    JPME = "JPME"
    LA_BELLENERGIE = "La bellenergie"
    LLUM = "LLUM"
    MINT_ENERGIE = "Mint Energie"
    OHM_ENERGIE = "Ohm Energie Middle Market"
    PICOTY = "PICOTY"
    PRIMEO_ENERGIE = "Primeo Energie"
    PROXELIA = "Proxelia"
    SAVE = "SAVE"
    SEFE = "SEFE"
    SELIA = "SELIA"
    SYNELVA = "SYNELVA"
    TOTALENERGIES = "TotalEnergies"
    VATTENFALL = "Vattenfall"
    YELI = "Yéli"
    UNKNOWN = "UNKNOWN"

class RouteDecision(BaseModel):
    supplier: SupplierEnum = Field(
        description="The exact energy supplier identified from the document."
    )
    confidence: str = Field(
        description="Confidence level: HIGH, MEDIUM, or LOW"
    )
    reasoning: str = Field(
        description="Brief explanation of why this supplier was chosen based on filename or text."
    )

# Fast regex mapping to bypass LLM if filename is obvious
FAST_ROUTE_MAP = {
    SupplierEnum.TOTALENERGIES: ["horizon c5", "totalenergies", "on line", "online"],
    SupplierEnum.ENGIE_PRO: ["latitude", "activert", "engie pro", "gaz evolution", "engie_pro"],
    SupplierEnum.ENGIE: ["engie", "maitriz", "prix fixe gaz"],
    SupplierEnum.EDF_ENTREPRISES: ["edf", "pack performance", "estivia", "contrat expert", "contrat garanti", "tarif bleu"],
    SupplierEnum.EKWATEUR: ["ekwateur"],
    SupplierEnum.DYNEFF: ["dyneff"],
    SupplierEnum.JPME: ["jpme"],
    SupplierEnum.LLUM: ["llum"],
    SupplierEnum.YELI: ["yeli"],
    SupplierEnum.ALTERNA_ENERGIE: ["alterna"],
    SupplierEnum.ELECTRICITE_DE_SAVOIE: ["savoie"],
    SupplierEnum.ENDESA_FRANCE: ["endesa"],
    SupplierEnum.ENERGIES_DU_SANTERRE: ["santerre"],
    SupplierEnum.ES: ["és", " es ", "_es_"],
    SupplierEnum.GAZ_EUROPEEN: ["gaz europeen", "gaz_europeen"],
    SupplierEnum.GEG: ["geg ", "_geg_"],
    SupplierEnum.GEDIA_ENERGIES_SERVICES: ["gedia"],
    SupplierEnum.HELLIO: ["hellio", "héllio"],
    SupplierEnum.ILEK: ["ilek"],
    SupplierEnum.LA_BELLENERGIE: ["labelleenergie", "la bellenergie"],
    SupplierEnum.MINT_ENERGIE: ["mint"],
    SupplierEnum.OHM_ENERGIE: ["ohm "],
    SupplierEnum.PICOTY: ["picoty", "profixe"],
    SupplierEnum.PRIMEO_ENERGIE: ["primeo"],
    SupplierEnum.PROXELIA: ["proxelia"],
    SupplierEnum.SAVE: ["save "],
    SupplierEnum.SEFE: ["sefe"],
    SupplierEnum.SELIA: ["selia"],
    SupplierEnum.SYNELVA: ["synelva"],
    SupplierEnum.VATTENFALL: ["vattenfall", "new green"],
}

def _route_by_filename(filename: str) -> RouteDecision | None:
    filename_lower = filename.lower()
    # Handle specific overrides to prevent false matches (e.g., "Engie" vs "Engie Pro")
    if "engie pro" in filename_lower or "engie_pro" in filename_lower:
        return RouteDecision(supplier=SupplierEnum.ENGIE_PRO, confidence="HIGH", reasoning=f"Filename matched {SupplierEnum.ENGIE_PRO.value}")
    
    for supplier, keywords in FAST_ROUTE_MAP.items():
        if any(keyword in filename_lower for keyword in keywords):
            return RouteDecision(
                supplier=supplier,
                confidence="HIGH",
                reasoning=f"Exact keyword match found in filename: '{filename}'"
            )
    return None

def route_document(filename: str, first_page_text: str | None = None) -> RouteDecision:
    """Smart router that uses regex for speed, and Gemini for complex cases."""
    
    # 1. OPTIMIZATION: Try fast regex routing based on filename first
    fast_decision = _route_by_filename(filename)
    if fast_decision:
        return fast_decision

    # 2. SMART ROUTING: If filename is generic, ask Gemini to classify it
    try:
        # Require api key explicitly or let SDK pick it from os.environ
        api_key = os.environ.get("GEMINI_API_KEY")
        model_name = os.environ.get("MODEL", "gemini-2.5-flash")
        client = genai.Client(api_key=api_key) if api_key else genai.Client()
        
        prompt = f"""
        You are an expert Document Routing Agent. Identify the exact energy supplier.
        Filename: {filename}
        Text Snippet: {first_page_text or 'No text available'}
        """

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": RouteDecision,
                "temperature": 0.1,
            },
        )
        return RouteDecision.model_validate_json(response.text)
    except Exception as e:
        print(f"LLM Routing failed: {e}")
        return RouteDecision(
            supplier=SupplierEnum.UNKNOWN,
            confidence="LOW",
            reasoning=f"Fallback failed due to API error: {e}"
        )
