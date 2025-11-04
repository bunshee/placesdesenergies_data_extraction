## placesdesenergies_data_extraction

Extraction structurée de données issues de factures d’énergie (EDF, Gaz Européen, Engie, etc.) avec une approche robuste et hybride (OCR + NLP + règles + modèles ML) pour produire un format standardisé, dédupliquer par référence (PCE/PDL/PRM) et conserver la facture la plus récente.

### Objectif
Développer un script ou module capable d’extraire des informations spécifiques des factures d’énergie françaises (gaz et électricité), provenant de différents fournisseurs, et de les structurer dans un format unique et exploitable.

### Données à extraire (schéma cible)
- Nom du site
- Référence Point d’Énergie: PRM/PDL (électricité) ou PCE (gaz) + type + longueur
- Adresse de consommation (prioritaire) et, si présent, adresse de facturation
- Code postal (lieu de consommation)
- Commune (lieu de consommation)
- Segment énergie: Gaz/Électricité, type d’offre (ex: Prix Fixe, Contrat Garanti, Offre verte), et segment tarifaire (T1–T4, C1–C5)
- Date d’échéance du contrat, ou sinon: date de souscription, préavis de résiliation, modalités (ex: durée indéterminée, révision annuelle)
- Fournisseur actuel
- SIREN/SIRET du client (si présent)
- Tarif réglementé: Oui/Non (déduit du libellé de l’offre)

## Pipeline d'extraction
Le pipeline est simple et direct:

1. **Entrée PDF**: L'utilisateur soumet un fichier PDF via l'API
2. **Extraction de texte**: PyMuPDF (`pymupdf`) extrait le texte du PDF
3. **Extraction structurée**: Le texte est envoyé à Google Gemini (`gemini-2.5-flash`) avec un prompt pour extraire les données structurées
4. **Sortie**: Retour d'un objet JSON conforme au schéma `EnergyInvoiceRecord`

Le schéma Pydantic valide automatiquement les types et formats.

## Choix technologiques (state of the art pragmatique)
- Langage: Python 3.11+
- PDFs natifs: `pymupdf` pour texte.
- Extraction structurée: `google-genai` avec modèle `gemini-2.5-flash` et schéma Pydantic.
- Normalisation & schéma: `pydantic` v2.
- API: FastAPI pour exposer l'endpoint d'extraction.
- Tests: `pytest` pour les tests unitaires.

Remarque: on combine règles + ML pour la robustesse cross-fournisseurs, tout en gardant un coût d’inférence raisonnable.

## Schéma de données (Pydantic)
```python
from typing import Optional, Literal
from pydantic import BaseModel, Field

EnergyReferenceType = Literal["PCE", "PDL", "PRM"]

class EnergyInvoiceRecord(BaseModel):
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
    termination_notice: Optional[str]  # ex: "30 jours"
    renewal_terms: Optional[str]       # ex: "Durée indéterminée, révision annuelle"
    client_siren_siret: Optional[str]
    regulated_tariff: Optional[Literal["Oui", "Non"]]
```

## Règles métier clés
- Les champs sont extraits tels que présents dans le document
- Si un champ est introuvable: valeur `null` ou `N/A` dans le JSON
- Le LLM analyse le document et remplit automatiquement le schéma structuré

## Fonctionnalités
- **Extraction structurée**: Conversion automatique de PDFs de factures d'énergie en données JSON structurées
- **API REST**: Interface HTTP simple avec FastAPI
- **Validation automatique**: Schéma Pydantic pour garantir la cohérence des données
- **Logging**: Traçabilité complète des extractions via Loguru

## Arborescence
```
placesdesenergies_data_extraction/
  ├─ src/
  │  ├─ api/
  │  │  └─ main.py          # FastAPI endpoints
  │  ├─ genai/
  │  │  └─ extractor.py     # LLM extraction logic
  │  ├─ ingestion/
  │  │  └─ loader.py        # PDF text extraction
  │  ├─ models/
  │  │  └─ schema.py        # Pydantic schema
  │  └─ utils/
  │     └─ logging.py       # Logger configuration
  ├─ tests/
  ├─ requirements.txt
  ├─ main.py                # Server entry point
  └─ README.md
```

## Installation (proposée)
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
export GEMINI_API_KEY=your_api_key_here
```

Les dépendances essentielles:
```
pymupdf>=1.24.9         # Extraction de texte PDF
pydantic>=2.7           # Schéma de validation
fastapi>=0.115          # API REST
uvicorn[standard]>=0.30 # Serveur ASGI
google-genai>=0.3.0     # Client Gemini
loguru>=0.7.2           # Logging
pytest>=8.2             # Tests
```

## API FastAPI (server)
Exposer l’extraction via endpoints HTTP (Python 3.12):

### Lancer le serveur
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints
- `GET /health` → Vérification du statut de l'API
- `POST /extract` → Extrait les données structurées d'une facture PDF
  - Input: fichier PDF (multipart/form-data)
  - Output: objet JSON `EnergyInvoiceRecord` ou `null` si extraction échoue

Exemple d'utilisation:
```bash
curl -F "file=@/path/facture.pdf" http://localhost:8000/extract
```

## Performance
- Temps de traitement typique: 2-5 secondes par document (selon la taille)
- Dépend de la latence API Gemini et de la complexité du PDF

## Gestion des cas limites
- PDFs scannés: non supportés (nécessiteraient OCR)
- Champs absents: retourne `null` dans le JSON
- Texte vide: retourne `null` comme résultat

## Sécurité
- Aucune persistance des documents uploadés
- Données sensibles (SIREN/SIRET, adresses) à protéger selon vos besoins
- Variable d'environnement `GEMINI_API_KEY` requise

## Exemple de sortie
```json
{
  "document_date": "2025-10-13",
  "supplier": "EDF",
  "site_name": "SDC LE JARDIN DU CEDRE",
  "energy_reference": "25841823335979",
  "energy_reference_type": "PCE",
  "energy_reference_length": 14,
  "address_consumption": "107 AVENUE CHARLES DE GAULLE LE JARDIN DU CEDRE A, 84130 LE PONTET",
  "postal_code": "84130",
  "city": "LE PONTET",
  "energy_segment": "Gaz",
  "offer_tags": [],
  "tariff_segment": "T2",
  "contract_expiry_date": null,
  "contract_start_date": "2019-06-13",
  "termination_notice": "30 jours",
  "client_siren_siret": "349759647",
  "regulated_tariff": "Non"
}
