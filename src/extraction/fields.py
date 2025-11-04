import re
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz


@dataclass
class ExtractedFields:
    supplier: Optional[str] = None
    site_name: Optional[str] = None
    energy_reference: Optional[str] = None
    energy_reference_type: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    address_consumption: Optional[str] = None
    client_siren_siret: Optional[str] = None
    energy_segment: Optional[str] = None
    offer_tags: list[str] = field(default_factory=list)
    tariff_segment: Optional[str] = None
    document_date: Optional[str] = None
    contract_expiry_date: Optional[str] = None
    contract_start_date: Optional[str] = None
    termination_notice: Optional[str] = None
    regulated_tariff: Optional[str] = None


SUPPLIERS = [
    "EDF",
    "ENGIE",
    "GAZ EUROPEEN",
    "GAZ DE PARIS",
]


POSTAL_CODE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")
SIREN_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")
SIRET_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")
PCE_RE = re.compile(r"(?i)(?:PCE|Point\s+de\s+Comptage).*?(\d{14})")
PDL_RE = re.compile(r"(?i)(?:PDL|Point\s+de\s+Livraison|PRM).*?(\d{14})")
DATE_RE = re.compile(r"(?i)(\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4})")


def extract_supplier(text: str) -> Optional[str]:
    best, score = None, 0
    for s in SUPPLIERS:
        sc = fuzz.partial_ratio(s, text.upper())
        if sc > score:
            best, score = s, sc
    return best if score >= 80 else None


def extract_energy_reference(text: str) -> tuple[Optional[str], Optional[str]]:
    m = PCE_RE.search(text)
    if m:
        return m.group(1), "PCE"
    m = PDL_RE.search(text)
    if m:
        return m.group(1), (
            "PDL"
            if "PDL" in m.group(0).upper()
            else ("PRM" if "PRM" in m.group(0).upper() else "PDL")
        )
    return None, None


def extract_postal_and_city(text: str) -> tuple[Optional[str], Optional[str]]:
    m = POSTAL_CODE_RE.search(text)
    if not m:
        return None, None
    code = m.group(1)
    # Try to capture city as following uppercase word(s)
    after = text[m.end() : m.end() + 64]
    city_m = re.search(r"([A-ZÉÈÀÙÎÔÂ\-\s]{2,})", after)
    city = city_m.group(1).strip() if city_m else None
    return code, city


def extract_siren_siret(text: str) -> Optional[str]:
    siret = SIRET_RE.search(text)
    if siret:
        return siret.group(1)
    siren = SIREN_RE.search(text)
    return siren.group(1) if siren else None


def extract_dates(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    # heuristic: first date -> document date, keywords for others can be added later
    dates = DATE_RE.findall(text)
    doc_date = dates[0] if dates else None
    return doc_date, None, None


def extract_offer_and_segment(
    text: str,
) -> tuple[Optional[str], list[str], Optional[str], Optional[str]]:
    energy = (
        "Gaz"
        if re.search(r"(?i)gaz", text)
        else (
            "Électricité" if re.search(r"(?i)(électricité|electricite)", text) else None
        )
    )
    tags: list[str] = []
    if re.search(r"(?i)contrat\s+garanti", text):
        tags.append("Contrat Garanti")
    if re.search(r"(?i)prix\s+fixe", text):
        tags.append("Prix Fixe")
    if re.search(r"(?i)offre\s+verte", text):
        tags.append("Offre verte")
    tariff = None
    m = re.search(r"(?i)\b(T[1-4])\b", text)
    if m:
        tariff = m.group(1)
    m2 = re.search(r"(?i)\b(C[1-5])\b", text)
    if m2:
        tariff = m2.group(1)

    regulated = None
    if re.search(r"(?i)prix\s+non\s+reglement", text):
        regulated = "Non"
    elif re.search(r"(?i)tarif\s+reglemente|TRV", text):
        regulated = "Oui"

    return energy, tags, tariff, regulated


def extract_site_name(text: str) -> Optional[str]:
    # Heuristic: look for lines with typical site name patterns (uppercase dense words)
    candidates = []
    for line in text.splitlines():
        line_s = line.strip()
        if len(line_s) >= 8 and sum(1 for c in line_s if c.isupper()) >= max(
            6, int(0.6 * len(line_s))
        ):
            candidates.append(line_s)
    return candidates[0] if candidates else None
