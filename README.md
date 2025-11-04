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

Chaque enregistrement doit être rattaché à la date du document (ou période de consommation) pour trancher les conflits et conserver la version la plus récente par référence (PCE/PDL/PRM).

## Vision d’architecture (pipeline)
1) Ingestion des documents
- Entrées: PDF natifs, PDF scannés, images (JPEG/PNG).
- Détection du type (facture énergie vs non pertinent) via un classifieur léger texte+mise en page, avec filtre par mots-clés robustes (exclusion eau/entretien/fioul, etc.).

2) Prétraitement & OCR
- PDFs natifs: extraction texte + positions via `pdfplumber`/`pypdfium2`.
- Scans/images: OCR via `PaddleOCR` ou `Tesseract` (avec prétraitements OpenCV: binarisation, deskew, denoise), et extraction de la mise en page via `layoutparser`/`docTR` si besoin.

3) Détection fournisseur
- Approche hybride:
  - Vision (logo/entête) via classifieur CNN/ViT léger (ex: `timm` ViT-Tiny) fine-tuné sur logos/entêtes.
  - Fallback texte (fuzzy matching) sur mentions: EDF, Engie, Gaz Européen, etc.

4) Extraction de champs (NLP + règles)
- Repérage des blocs sémantiques: « Vos informations client », « Point de livraison », « Données de comptage », etc.
- Regex robustes pour PCE/PDL/PRM (longueurs/formats), code postal, SIREN/SIRET.
- Heuristiques d’adresse multi-lignes; normalisation via `libpostal` (optionnel) et séparation code postal/commune.
- Classification de segment énergie:
  - Gaz vs Électricité (mots-clés + modèle texte court)
  - Offre: Prix Fixe / Contrat Garanti / Offre verte / TRV…
  - Segment: T1–T4 (gaz), C1–C5 (élec.), par règles et motifs fréquents.
- Dates: extraction et normalisation (formats FR), inférence de l’échéance si explicitée; sinon consigner « Souscrit depuis », « Préavis », « Révision annuelle ».

5) Normalisation & validation
- Modèle `Pydantic` pour valider types, formats, contraintes (PCE/PDL/PRM: 14 chiffres, code postal 5 chiffres, etc.).
- Déduction du champ « Tarif réglementé » en Oui/Non à partir des libellés (« Prix non réglementés », « Offre de marché », « TRV », etc.).

6) Déduplication / Actualisation
- Clé d’unicité: référence énergétique (PCE/PDL/PRM).
- Conserver uniquement l’enregistrement lié à la facture la plus récente (date document ou fin de période).

7) Sorties
- JSON Lines (une facture par ligne) et CSV normalisé.
- Journal d’extraction avec scores de confiance par champ et raisons des décisions (ex: pour l’échéance, source textuelle utilisée).

## Choix technologiques (state of the art pragmatique)
- Langage: Python 3.11+
- PDFs natifs: `pdfplumber`, `pypdfium2` (rapide, positions), `pdfminer.six` (fallback).
- OCR: `PaddleOCR` (qualité + vitesse) ou `Tesseract` (large diffusion) avec `opencv-python` pour les prétraitements.
- Layout: `layoutparser` + backends Detectron2 ou `doctr` (Mindee) pour segments de page.
- NLP: `regex`, `spaCy` (FR), `rapidfuzz` (fuzzy), mini-classifieur `scikit-learn` ou petit `transformers` (DistilBERT) pour triage fournisseur/document.
- Vision (logos): `timm`/ViT-Tiny ou MobileNetV3; export onnx possible.
- Normalisation & schéma: `pydantic` v2.
- Orchestration: pipeline modulaire (lib simple) + CLI via `typer`.
- Tests: `pytest`, `hypothesis` (génération), jeux d’essai PDF.

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
    offer_tags: list[str] = []     # ex: ["Contrat Garanti", "Prix Fixe", "Offre verte"]
    tariff_segment: Optional[str]  # ex: T2, C3
    contract_expiry_date: Optional[str]
    contract_start_date: Optional[str]
    termination_notice: Optional[str]  # ex: "30 jours"
    renewal_terms: Optional[str]       # ex: "Durée indéterminée, révision annuelle"
    client_siren_siret: Optional[str]
    regulated_tariff: Optional[Literal["Oui", "Non"]]
```

## Règles métier clés
- Unicité par PCE/PDL/PRM. Si plusieurs factures partagent la même référence, ne garder que la plus récente (date document/période).
- Si l’échéance n’existe pas, renseigner ce qui est disponible: date de souscription, préavis, règles de révision.
- Si un champ est introuvable: valeur « N/A » (ou None en JSON) + raison dans le journal.
- Détecter et ignorer les documents non pertinents (ex: eau, entretien, fioul) via classifieur + listes d’exclusion.

## Roadmap détaillée (par phases)
### Phase 0 — Bootstrapping (0.5 sprint)
- Initialiser dépôt, structure du module, CI minimal (lint/test), `.env`.
- Définir schéma Pydantic et formats de sortie (JSONL/CSV).

### Phase 1 — Ingestion & Triage (1 sprint)
- Lecteur PDF/images unifié (pdf natif vs scanné).
- Classifieur documents: énergie vs non-pertinent (baseline: mots-clés + SVM; option: DistilBERT FR).
- Détecteur fournisseur baseline (fuzzy texte) + collecte dataset logos pour fine-tuning ultérieur.

### Phase 2 — OCR & Layout (1–2 sprints)
- Intégrer PaddleOCR + prétraitements OpenCV (deskew, denoise, threshold).
- Extraire zones: en-têtes, blocs client, blocs point de livraison, tableaux contrats.
- Export positions (bbox) pour aider les heuristiques.

### Phase 3 — Extraction des champs (2 sprints)
- Implémenter extracteurs robustes: site_name, PCE/PDL/PRM, adresses, CP/commune, SIREN/SIRET.
- Détecter énergie (gaz/élec), offre (Prix Fixe, Contrat Garanti, Offre verte), segments (T/C).
- Détection des dates: document, échéance, souscription, prochaine facture, préavis.
- Déductions: « Tarif réglementé » Oui/Non.

### Phase 4 — Normalisation, Conflits et Sorties (1 sprint)
- Validation Pydantic, enrichissement (longueur ref), normalisation adresses (libpostal, optionnel).
- Mécanisme d’actualisation par date, déduplication stricte par référence.
- Génération JSONL/CSV + journal d’extraction.

### Phase 5 — Vision & Robustesse (1 sprint)
- Entraîner classifieur logos/entêtes (ViT-Tiny). Intégrer en inference rapide.
- Améliorer rappel/precision sur fournisseurs difficiles.

### Phase 6 — Tests & Qualité (continu, 1 sprint dédié)
- Jeux de tests unitaires par fournisseur et cas limites (scans médiocres, champs manquants, docs non pertinents).
- Mesures: précision par champ, F1 extraction, exact match adresse/CP/ville, rappel du triage.
- Bench performances (temps/page) et empreinte mémoire.

### Phase 7 — Documentation & Livraison
- Doc d’architecture, guide d’utilisation, exemples, matrices de compatibilité.
- Packaging (CLI), versionnage, release initiale.

## Arborescence proposée
```
placesdesenergies_data_extraction/
  ├─ src/
  │  ├─ cli.py
  │  ├─ pipeline.py
  │  ├─ ingestion/
  │  │  ├─ loader.py
  │  │  └─ triage.py
  │  ├─ ocr/
  │  │  ├─ preprocess.py
  │  │  └─ ocr_engine.py
  │  ├─ layout/
  │  │  └─ segmenter.py
  │  ├─ extraction/
  │  │  ├─ fields.py
  │  │  └─ suppliers.py
  │  ├─ normalize/
  │  │  └─ validators.py
  │  ├─ models/
  │  │  ├─ schema.py
  │  │  └─ classifiers.py
  │  └─ utils/
  │     └─ text.py
  ├─ tests/
  │  ├─ data/  # PDFs de test (mock)
  │  ├─ test_triage.py
  │  ├─ test_extraction.py
  │  └─ test_normalize.py
  ├─ docs/
  │  └─ examples.md
  ├─ requirements.txt
  └─ README.md
```

## Installation (proposée)
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
# Option OCR:
# - Tesseract: sudo apt-get install tesseract-ocr tesseract-ocr-fra
# - ou PaddleOCR: pip install "paddleocr>=2.8.0" "opencv-python>=4.10"
```

Exemple de `requirements.txt` (indicatif):
```
pdfplumber>=0.11
pypdfium2>=4.30
pdfminer.six>=202312
paddleocr>=2.8; extra == "paddle"
opencv-python>=4.10
layoutparser[layoutmodels]>=0.3
python-doctr[torch]>=0.8
spacy>=3.7
fr-core-news-lg @ https://github.com/explosion/spacy-models/releases/download/fr_core_news_lg-3.7.0/fr_core_news_lg-3.7.0-py3-none-any.whl
rapidfuzz>=3.9
scikit-learn>=1.5
transformers>=4.44
pydantic>=2.7
typer>=0.12
pytest>=8.2
hypothesis>=6.100
python-libpostal; platform_system != "Windows"
```

## Utilisation CLI (proposée)
```bash
python -m src.cli extract \
  --input /path/aux/documents \
  --output-jsonl out/invoices.jsonl \
  --output-csv out/invoices.csv \
  --keep-logs
```

## API FastAPI (server)
Exposer l’extraction via endpoints HTTP (Python 3.12):

### Lancer le serveur
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints
- `GET /health` → statut simple.
- `POST /extract` (multipart/form-data, champ `file`: PDF) → retourne un `EnergyInvoiceRecord` ou `null` si non pertinent/aucun texte.
- `POST /extract/batch` (plusieurs fichiers) → retourne la liste dédupliquée par référence et la plus récente.
- `POST /text?format=plain|json|html` (champ `file`: PDF) → renvoie le texte brut.

Exemple (curl):
```bash
curl -F "file=@/path/doc.pdf" http://localhost:8000/extract
```

Texte brut (plain):
```bash
curl -F "file=@/path/doc.pdf" "http://localhost:8000/text?format=plain"
```

HTML formaté:
```bash
curl -F "file=@/path/doc.pdf" "http://localhost:8000/text?format=html"
```

## Stratégie d’évaluation
- Triage: précision/rappel ≥ 0.98/0.97 sur énergie vs non pertinent.
- Détection fournisseur: exact match ≥ 0.98.
- Extraction par champ (accuracy):
  - PCE/PDL/PRM ≥ 0.995
  - CP/commune ≥ 0.98 (exact) / ≥ 0.995 (normalisé)
  - SIREN/SIRET ≥ 0.995
  - Segments/Offre ≥ 0.95
  - Dates (doc/échéance/souscription/préavis) ≥ 0.95
- Déduplication: 100% correct par date.
- Performance: ≤ 2s/page en moyenne sur CPU moderne (OCR inclus pour scans lisibles).

## Gestion des cas limites
- Scans de mauvaise qualité: rehaussement contraste, binarisation adaptive, deskew.
- Champs absents ou masqués: retourner « N/A » + raison; pas de valeur par défaut.
- Formats variés: combiner positions (layout), règles et modèles pour généraliser.
- Ambiguïtés (adresse/CP/ville): préférer l’adresse de consommation; fallback sur libellés « Adresse de facturation ».

## Sécurité & conformité
- Aucune persistance de documents source par défaut; journal expurgé des données sensibles si configuré.
- Traiter SIREN/SIRET et adresses comme données sensibles en sortie (chiffrement optionnel au repos).

## Tests
- Unitaires par fournisseur et par champ; dossiers de fixtures avec PDF natifs et scannés.
- Tests de bout en bout (E2E) sur un lot mixte (énergie et non pertinent) pour valider triage, extraction et déduplication.

## Exemples de sortie (JSONL, 1 ligne)
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
  "offer_tags": ["Contrat Garanti"],
  "tariff_segment": "T2",
  "contract_expiry_date": null,
  "contract_start_date": "2019-06-13",
  "termination_notice": "30 jours",
  "client_siren_siret": "349759647",
  "regulated_tariff": "Non"
}
```

## Contribution
- Ouvrir des issues avec échantillons (anonymisés si possible) et attentes d’extraction.
- PRs bienvenues: inclure tests + mise à jour de la documentation.

## Licence
À définir par le client (privé par défaut).
