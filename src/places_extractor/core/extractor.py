import re
import io
import json
import os
import logging
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
import fitz  # PyMuPDF
from PIL import Image, ImageEnhance

from places_extractor.core.models import ExtractionResult, ExtractionDefaults, APIResult

# Initialize environment and variables
load_dotenv()
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
MODEL_NAME: str = os.getenv("MODEL", "gemini-2.5-flash")

# Configure logging for the core module
logger = logging.getLogger(__name__)

IGNORED_PATTERNS: list[str] = [
    "abypas", "adi incendie", "adi sas", "albasini", "tmd securite",
    "secat", "scem", "sani chauff", "s.p.m. nicolai", "rym",
    "riva paysages", "renovtoiture", "plagnol nettoyage", "nordtherm_",
    "my renovation et neuf", "long chauffage", "h.saint paul", "gesten",
    "gazflash", "albert & fils", "bouvier", "disdero", "delostal",
    "charles pereira",
]

SUPPLIER_RULES: dict[str, dict[str, Any]] = {
    "ENGIE": {
        "keywords": ["engie", "gdf", "gaz de france"],
        "default_pages": (1, 3),
        "hints": [
            # SIREN: present only on Maitriz'Elec offer (page 1 'vos références' 3ème ligne); absent on Formule Prix Fixe gaz
            "SIREN/SIRET: Si offre Maitriz'Elec → SIRET page 1 haut gauche encadré 'vos références' 3ème ligne. Si Formule Prix Fixe gaz → non présent sur la facture.",
            # Raison sociale
            "Raison sociale: Si offre Maitriz'Elec → page 1 haut gauche encadré 'vos références' 2ème ligne ('Titulaire du contrat') et en haut au milieu. Si Formule Prix Fixe gaz → page 3 encadré 'votre contrat' ligne 2 'Titulaire du contrat'.",
            # Billing address
            "Adresse postale (facturation): page 1 haut milieu sous la raison sociale.",
            "Code postal (facturation): page 1 haut milieu avec l'adresse.",
            "Commune (facturation): page 1 haut milieu avec l'adresse.",
            # Site / consumption
            "Nom du site: page 3 gauche milieu encadré 'votre point de livraison' 2ème ligne: désignation du site.",
            "PDL/PRM (reference_point_energie): page 3 gauche milieu encadré 'votre point de livraison' 1ère ligne.",
            "Adresse du site (adresse_site): page 3 gauche milieu encadré 'votre point de livraison' 3ème ligne: adresse de livraison.",
            "Code postal du site (code_postal_site): page 3 gauche milieu encadré 'votre point de livraison' 3ème ligne adresse de livraison.",
            "Commune du site (ville_site): page 3 gauche milieu encadré 'votre point de livraison' 3ème ligne adresse de livraison.",
            # Segment & date
            "Segment énergie: page 3 encadré 'votre contrat' haut gauche 5ème ligne: Acheminement.",
            "Date d'échéance: page 3 encadré 'votre contrat' haut gauche 3ème ligne: Date d'échéance.",
            # PRO/PART
            "PRO/PART: il est noté 'Engie Entreprises & Collectivités' → PRO.",
        ]
    },
    "EDF": {
        "keywords": ["edf"],
        "default_pages": (1, 5),
        "hints": [
            # SIREN: varies by offer
            "SIREN/SIRET: Sur la page 1 à gauche dans l'encadré 'Vos informations clients'. Absent sur l'offre Tarif Bleu pour clients non résidentiels.",
            # Raison sociale
            "Raison sociale: Page 1 en haut (au milieu ou à droite selon l'offre). Aussi dans 'Vos informations client' section 'Nom du client' ou en haut à droite au-dessus de l'adresse postale.",
            # Billing address
            "Adresse postale (facturation): Page 1 en haut sous la raison sociale (milieu ou droite selon l'offre).",
            "Code postal (facturation): Page 1 sous l'adresse postale.",
            "Commune (facturation): Page 1 sous l'adresse, à côté du code postal.",
            # Nom du site
            "Nom du site: Souvent absent ('n'apparait pas'). Si présent: page 3 première ligne de l'encadré en haut de la page (offre Contrat Expert multi-pages).",
            # PDL/PRM/PCE — varies heavily by offer
            "PDL/PCE/PRM (reference_point_energie): Selon l'offre: "
            "Pack performance → Page 4 haut gauche 'Réf Acheminement Electricité' sous 'données point de livraison'. "
            "Tarif Bleu → Page 2 encadré haut gauche 'réf acheminement Electricité'. "
            "Contrat garanti Gaz → Page 4 bas 'données de comptage' ligne 2 'Point de Comptage et d'Estimation'. "
            "Contrat Gaz Durable → Page 2 bas 'données de comptage' ligne 2 'Point de Comptage et d'Estimation'. "
            "Contrat Expert (page 1) → Page 1 'Vos informations client' 'Lieu de consommation' Référence d'acheminement (14 chiffres). "
            "Contrat Expert (multi-pages) → Page 3 encadré haut 'Réf Acheminement Electricité' (14 chiffres).",
            # Adresse site
            "Adresse du site (adresse_site): Selon l'offre: "
            "Pack performance → Page 4 haut gauche 'données point de livraison'. "
            "Tarif Bleu → Page 2 encadré haut gauche sous 'données Point de livraison'. "
            "Contrat garanti Gaz → Page 4 haut gauche 'données Point de livraison'. "
            "Contrat Gaz Durable → Page 2 haut gauche 'données Point de livraison'. "
            "Contrat Expert (page 1) → Page 1 'Vos informations client' 'Lieu de consommation' sous Référence acheminement. "
            "Contrat Expert (multi-pages) → Page 3 encadré haut section 'Données du point de livraison'.",
            # Code postal site
            "Code postal du site (code_postal_site): Idem adresse du site, à côté de la rue.",
            # Commune site
            "Commune du site (ville_site): Idem adresse du site, à côté du code postal.",
            # Segment
            "Segment énergie: "
            "Pack performance → Page 4 bas gauche 'données de comptage' Acheminement (ex: C4). "
            "Tarif Bleu → Page 2 bas gauche 'données de comptage' Acheminement (ex: C5). "
            "Contrat garanti Gaz → Page 4 bas 'données de comptage' ligne 4 Tarif. "
            "Contrat Gaz Durable → Page 2 bas 'données de comptage' ligne 4 Tarif. "
            "Contrat Expert (plusieurs pages) → Page 3 'Groupe de sites'. "
            "Contrat Expert / Estivia / Garanti (page 1) → n'apparaît pas sur la facture.",
            # Date
            "Date d'échéance: "
            "Pack performance → Page 4 haut gauche 'venant à échéance le...'. "
            "Tarif Bleu → tarif bleu sans échéance (laisser vide). "
            "Contrat Gaz Durable → sans échéance mais délai de résil 30 jours (laisser vide). "
            "Contrat Expert (page 1) / Estivia / Garanti → Page 2 encadré haut 'Venant à échéance le'. "
            "Contrat Expert (multi-pages) → Page 3 encadré haut 'Venant à échéance le'.",
            # PRO/PART
            "PRO/PART: Page 1 encadré 'vos contacts' adresse mail edfentreprises@edf.fr ou 'Par internet' avec mention 'entreprise' → PRO. 'Tarif bleu pour clients non résidentiels' → PRO.",
        ]
    },
    "TOTAL ENERGIES": {
        "keywords": ["total energies", "totalenergies"],
        "default_pages": (1, 3),
        "hints": [
            # SIREN
            "SIREN/SIRET: Non repris sur la facture.",
            # Raison sociale
            "Raison sociale: Page 1 haut droite accompagnée de l'adresse.",
            # Billing address
            "Adresse postale (facturation): Page 1 haut droite accompagnée de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite sous la rue.",
            "Commune (facturation): Page 1 haut droite après le code postal.",
            # Nom du site
            "Nom du site: Si offre HORIZON C5 → page 3 haut centre 'Adresse du site'. Si offre ONLINE GAZ → page 3 haut gauche 'Adresse du site'.",
            # PDL
            "PDL/PCE (reference_point_energie): Page 2 dans le cadre, intitulé 'ID' juste après PDL (offre elec) ou PCE (offre gaz).",
            # Adresse site
            "Adresse du site (adresse_site): Page 2 dans le cadre, intitulé 'Lieu de consommation' juste après 'Segment'.",
            "Code postal du site (code_postal_site): Page 2 dans le cadre, 1er élément du 'Lieu de consommation'.",
            "Commune du site (ville_site): Page 2 dans le cadre, après le code postal dans 'Lieu de consommation'.",
            # Segment
            "Segment énergie: Si HORIZON C5 → page 2 après 'Date de fin de conso' intitulé 'Segement'. Si ONLINE GAZ → page 2 intitulé 'Tarif'.",
            # Date
            "Date d'échéance: Si HORIZON C5 → page 3 haut quasi au centre intitulé 'Date de fin de contrat'. Si ONLINE GAZ → non repris.",
            # PRO/PART
            "PRO/PART: Page 1 haut gauche 'Service Client Pro Premium' (HORIZON C5) ou 'Email, indication pro' (ONLINE GAZ) → PRO.",
        ]
    },
    "EKWATEUR": {
        "keywords": ["ekwateur"],
        "default_pages": (1, 5),
        "hints": [
            # SIREN: two different formats
            "SIREN/SIRET: Format 1 (facture simple) → Page 1 haut gauche. Format 2 (facture tableau) → Page 1 haut gauche encadré 'Données administratives' 3ème ligne intitulée 'SIREN/SIRET'.",
            # Raison sociale
            "Raison sociale: Page 1 haut droite 1ère ligne (format 2) ou page 1 haut gauche au-dessus du SIREN (format 1).",
            # Billing address
            "Adresse postale (facturation): Page 1 haut droite accompagnée de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite sous la rue.",
            "Commune (facturation): Page 1 haut droite après le code postal.",
            # Nom du site
            "Nom du site: Format 1 → page 1 haut droite + gauche + page 2 tableau 4ème case. Format 2 → page 2 4ème colonne sous 'NOM DU SITE'.",
            # PDL
            "PDL/PCE/RAE (reference_point_energie): Format 1 → page 2 haut droite 1ère colonne du tableau + page 3 2ème tableau 1ère colonne. Format 2 → page 2 sous 'RAE'.",
            # Adresse site
            "Adresse du site (adresse_site): Format 1 → page 2 tableau 5ème case + page 5 haut droite. Format 2 → page 2 sous 'adresse'.",
            "Code postal du site (code_postal_site): Format 1 → page 2 tableau 5ème case. Format 2 → page 2 sous 'adresse'.",
            "Commune du site (ville_site): Format 1 → page 2 tableau 5ème case. Format 2 → page 2 sous 'adresse'.",
            # Segment
            "Segment énergie: Format 1 (S 51VAGUE ELEC) → page 3 2ème tableau 2ème case (puissance souscrite). Format 2 → n'apparait pas.",
            # Date
            "Date d'échéance: n'apparait pas sur la facture.",
            # PRO/PART
            "PRO/PART: Format 1 → encadré 'PRO' carré vert dans le logo haut gauche. Format 2 → page 5 haut droite Segment 'professionnel'.",
        ]
    },
    "ENGIE PRO": {
        "keywords": ["engie pro", "direction des clients professionnels"],
        "default_pages": (1, 2),
        "hints": [
            # SIREN
            "SIREN/SIRET: Offre latitude elec → page 1 encadré 'vos informations client' 3ème flèche 'autres informations' SIRET du siège social. Offres GAZ NATUREL et ACTIVERT → non présent.",
            # Raison sociale
            "Raison sociale: Page 1 haut milieu de la facture.",
            # Billing address
            "Adresse postale (facturation): Page 1 haut milieu sous la raison sociale.",
            "Code postal (facturation): Page 1 haut milieu sous l'adresse.",
            "Commune (facturation): Page 1 haut milieu à côté du code postal.",
            # Nom du site
            "Nom du site: Page 1 haut gauche encadré 'vos informations client' 2ème flèche 'lieu de consommation'.",
            # PDL
            "PDL/PCE (reference_point_energie): Page 1 haut gauche encadré 'vos informations client' 2ème flèche 'lieu de consommation'. "
            "Offre latitude elec → 'electricite point de livraison'. Offre GAZ NATUREL → 'Gaz naturel'. Offre ACTIVERT → 'Electricité'.",
            # Adresse site
            "Adresse du site (adresse_site): Page 1 haut gauche encadré 'vos informations client' 2ème flèche 'lieu de consommation' sous le nom de site.",
            "Code postal du site (code_postal_site): Page 1 'lieu de consommation' sous le nom de site, sous le numéro et nom de rue.",
            "Commune du site (ville_site): Page 1 'lieu de consommation' sous le nom de site, à côté du code postal.",
            # Segment
            "Segment énergie: Page 2 encadré 'votre contrat d'énergie'. Offre latitude elec et ACTIVERT → 3ème tiret 'Segment'. Offre GAZ NATUREL → 3ème tiret 'Tarif d'acheminement'.",
            # Date
            "Date d'échéance: Page 2 encadré 'votre contrat d'énergie' 1er tiret 'échéance'.",
            # PRO/PART
            "PRO/PART: Page 1 mention 'DIRECTION DES CLIENTS PROFESSIONNELS' → PRO.",
        ]
    },
    "SEFE": {
        "keywords": ["sefe"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite en dessous du numéro de facture.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 haut gauche encadré 'Mon contrat' intitulé 'Mon entité'.",
            "PCE (reference_point_energie): Page 2 haut centre intitulé 'Réf. du Point de Comptage (PCE)'.",
            "Adresse du site (adresse_site): Page 2 haut gauche intitulé 'Adresse' en dessous du 'Nom du site'.",
            "Code postal du site (code_postal_site): Page 2 haut gauche en dessous de la rue.",
            "Commune du site (ville_site): Page 2 haut gauche à côté du code postal.",
            "Segment énergie: Page 2 centre intitulé 'Tarif d'acheminement'.",
            "Date d'échéance: Page 2 centre intitulé 'Echéance contrat' en dessous du 'Tarif d'acheminement'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "GAZ EUROPEEN": {
        "keywords": ["gaz europeen", "gaz européen"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 haut gauche encadré 'Mes références' intitulé 'Nom du client'.",
            "PCE (reference_point_energie): Page 1 haut gauche encadré 'Mes références' intitulé 'Référence du PCE'.",
            "Adresse du site (adresse_site): Page 1 haut gauche encadré 'Mes références' intitulé 'Lieu de consommation'.",
            "Code postal du site (code_postal_site): Page 1 'Mes références' 'Lieu de consommation' en dessous de la rue.",
            "Commune du site (ville_site): Page 1 'Mes références' 'Lieu de consommation' à côté du code postal.",
            "Segment énergie: Non repris en code C/T. Le profil apparaît page 2 haut droite intitulé 'Profil' (ex: T1, T2) — utiliser T1/T2 etc.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "PROXELIA": {
        "keywords": ["proxelia"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: Page 1 haut gauche en dessous de la ligne intitulée 'Numéro SIRET'. Absent sur certaines factures.",
            "Raison sociale: Page 1 haut droite 1ère ligne.",
            "Adresse postale (facturation): Page 1 haut droite 2ème ligne.",
            "Code postal (facturation): Page 1 haut droite 3ème ligne.",
            "Commune (facturation): Page 1 haut droite 3ème ligne.",
            "Nom du site: Page 1 milieu gauche intitulé 'TITULAIRE DU CONTRAT'.",
            "PDL (reference_point_energie): Page 1 milieu gauche intitulé 'POINT DU LIVRAISON (PDL)'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche intitulé 'LIEU DE CONSOMMATION'.",
            "Code postal du site (code_postal_site): Page 1 milieu gauche 'LIEU DE CONSOMMATION' en dessous de la rue.",
            "Commune du site (ville_site): Page 1 milieu gauche 'LIEU DE CONSOMMATION' à côté du code postal.",
            "Segment énergie: Page 1 milieu gauche intitulé 'SEGMENT'.",
            "Date d'échéance: Page 1 milieu gauche intitulé 'DATE D'ECHEANCE DU CONTRAT'.",
            "PRO/PART: Page 1 haut gauche à côté du nom du fournisseur, indication 'proxelia PRO' → PRO.",
        ]
    },
    "VATTENFALL": {
        "keywords": ["vattenfall"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite accompagnée de l'adresse.",
            "Adresse postale (facturation): Page 1 haut droite accompagnée de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite sous la rue.",
            "Commune (facturation): Page 1 haut droite après le code postal.",
            "Nom du site: Page 1 haut gauche sous 'vos références'.",
            "PRM (reference_point_energie): Page 1 haut gauche 'vos références' 5ème ligne ou page 2 gauche à côté 'ref acheminement PRM'.",
            "Adresse du site (adresse_site): Page 1 gauche sous 'adresse de consommation'.",
            "Code postal du site (code_postal_site): Page 1 sous lieu de consommation gauche.",
            "Commune du site (ville_site): Page 1 sous lieu de consommation gauche.",
            "Segment énergie: N'apparaît pas sur la facture.",
            "Date d'échéance: Page 1 sous adresse de consommation.",
            "PRO/PART: Page 1 gauche à côté de offre: 'eco PRO new green' → PRO.",
        ]
    },
    "ILEK": {
        "keywords": ["ilek"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: SIRET page 1 haut gauche dans l'encadré 'Mes références client' + haut droite.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous du SIRET.",
            "Code postal (facturation): Page 1 haut droite sous la rue.",
            "Commune (facturation): Page 1 haut droite après le code postal.",
            "Nom du site: Non repris sur la facture.",
            "PDL (reference_point_energie): Page 1 centre gauche intitulé 'Point de livraison'.",
            "Adresse du site (adresse_site): Page 1 haut gauche sous le SIRET, intitulé 'Lieu de consommation'.",
            "Code postal du site (code_postal_site): Page 1 haut gauche sous la rue.",
            "Commune du site (ville_site): Page 1 haut gauche à côté du code postal.",
            "Segment énergie: Non repris sur la facture.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Page 1 encadré 'Mes références client' dans 'offre' → PRO si mention pro.",
        ]
    },
    "LA BELLENERGIE": {
        "keywords": ["la bellenergie", "labellenergie"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut au centre.",
            "Adresse postale (facturation): Page 1 haut centre en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut centre en dessous de la rue.",
            "Commune (facturation): Page 1 haut centre après le code postal.",
            "Nom du site: Page 1 milieu gauche encadré 'VOTRE SITE DE CONSOMMATION' intitulé 'Lieu de consommation'.",
            "PDL (reference_point_energie): Page 1 milieu gauche encadré 'VOTRE SITE DE CONSOMMATION' intitulé 'Point de Livraison'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche 'VOTRE SITE DE CONSOMMATION' intitulé 'Lieu de consommation'.",
            "Code postal du site (code_postal_site): Page 1 milieu gauche 'VOTRE SITE DE CONSOMMATION' sous la rue.",
            "Commune du site (ville_site): Page 1 milieu gauche 'VOTRE SITE DE CONSOMMATION' à côté du code postal.",
            "Segment énergie: Page 1 milieu gauche 'VOTRE SITE DE CONSOMMATION' intitulé 'Segment'.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Non repris sur la facture.",
        ]
    },
    "OHM ENERGIE": {
        "keywords": ["ohm energie", "ohm énergie", "ohmenergie", "ohménergie", "ohm_energie"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut au centre.",
            "Adresse postale (facturation): Page 1 haut centre en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut centre en dessous de la rue.",
            "Commune (facturation): Page 1 haut centre après le code postal.",
            "Nom du site: Non repris sur la facture.",
            "PDL (reference_point_energie): Page 2 haut encadré 'Détail pour le point de livraison' à côté de l'intitulé.",
            "Adresse du site (adresse_site): Page 2 haut 'Détail pour le point de livraison' sous l'intitulé 'adresse de livraison'.",
            "Code postal du site (code_postal_site): Page 2 'adresse de livraison' après la rue.",
            "Commune du site (ville_site): Page 2 'adresse de livraison' après le code postal.",
            "Segment énergie: Page 2 haut 'Détail pour le point de livraison' intitulé 'Segment'.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Non repris sur la facture.",
        ]
    },
    "YELI": {
        "keywords": ["yeli", "yéli"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut au centre.",
            "Adresse postale (facturation): Page 1 haut centre en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut centre en dessous de la rue.",
            "Commune (facturation): Page 1 haut centre après le code postal.",
            "Nom du site: Page 1 milieu gauche encadré 'Contrat' intitulé 'espace de livraison'.",
            "PDL (reference_point_energie): Page 1 haut gauche intitulé 'réf ext'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche encadré 'Contrat' 'espace de livraison' après la raison sociale.",
            "Code postal du site (code_postal_site): Non repris sauf si adresse postale = adresse de livraison, alors reprendre le code postal du haut page 1.",
            "Commune du site (ville_site): Page 1 milieu gauche 'Contrat' 'espace de livraison' après la rue.",
            "Segment énergie: Non repris sur la facture.",
            "Date d'échéance: Page 1 milieu gauche encadré 'Contrat' intitulé 'date d'échéance'.",
            "PRO/PART: Page 1 haut gauche indication dans l'adresse mail intitulé 'courriel'.",
        ]
    },
    "DYNEFF": {
        "keywords": ["dyneff"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: SIRET page 1 gauche centre intitulé 'N° SIRET'.",
            "Raison sociale: Page 1 haut droite intitulé 'A L'ATTENTION DE :'.",
            "Adresse postale (facturation): Page 1 haut droite sous la raison sociale.",
            "Code postal (facturation): Page 1 haut droite sous la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 centre partie 'Lieu de consommation'.",
            "PDL (reference_point_energie): Page 2 haut centre intitulé 'Point de livraison (PDL)'.",
            "Adresse du site (adresse_site): Page 1 centre 'Lieu de consommation' en dessous du nom du site.",
            "Code postal du site (code_postal_site): Page 1 centre 'Lieu de consommation' en dessous de la rue.",
            "Commune du site (ville_site): Page 1 centre 'Lieu de consommation' à côté du code postal.",
            "Segment énergie: Page 2 haut centre intitulé 'Catégorie de PDL'.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Page 1 en haut dans le nom de l'offre intitulé 'MON OFFRE'.",
        ]
    },
    "LLUM": {
        "keywords": ["llum"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut milieu de la facture.",
            "Adresse postale (facturation): Page 1 haut milieu sous la raison sociale.",
            "Code postal (facturation): Page 1 haut milieu sous l'adresse.",
            "Commune (facturation): Page 1 haut milieu à côté du code postal.",
            "Nom du site: Page 1 haut gauche encadré 'Titulaire du contrat' intitulé 'Nom et prénom ou raison sociale'.",
            "PDL (reference_point_energie): Page 1 haut gauche encadré 'Votre contrat' intitulé 'reference PDL'.",
            "Adresse du site (adresse_site): Page 1 haut gauche encadré 'Titulaire du contrat' intitulé 'adresse du point de livraison'.",
            "Code postal du site (code_postal_site): Page 1 'Titulaire du contrat' 'adresse du point de livraison'.",
            "Commune du site (ville_site): Page 1 'Titulaire du contrat' 'adresse du point de livraison'.",
            "Segment énergie: N'apparaît pas sur la facture.",
            "Date d'échéance: N'apparaît pas sur la facture.",
            "PRO/PART: Page 1 haut gauche encadré 'Votre contrat' intitulé 'USAGE'.",
        ]
    },
    "ENDESA FRANCE": {
        "keywords": ["endesa france", "endesa"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut milieu de la facture.",
            "Adresse postale (facturation): Page 1 haut milieu de la facture.",
            "Code postal (facturation): Page 1 haut milieu de la facture.",
            "Commune (facturation): Page 1 haut milieu de la facture.",
            "Nom du site: Page 1 encadré 'Informations contrat' gauche sous 'Lieu de consommation' 1ère ligne.",
            "PDL (reference_point_energie): Page 1 encadré 'Informations contrat' gauche en dessous de la ligne 'N° point de relève de mesure'.",
            "Adresse du site (adresse_site): Page 1 'informations contrat' gauche sous 'Lieu de consommation' 2ème ligne.",
            "Code postal du site (code_postal_site): Page 1 'Lieu de consommation' 3ème ligne.",
            "Commune du site (ville_site): Page 1 'information contrat' sous 'Lieu de consommation' 3ème ligne.",
            "Segment énergie: Page 2 en dessous de la ligne intitulée 'Segment'.",
            "Date d'échéance: Page 1 encadré 'informations contrat' en dessous de 'Date d'échéance du contrat'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "ÉS": {
        "keywords": ["és", "es energies"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite 1ère ligne.",
            "Adresse postale (facturation): Page 1 haut droite 2ème ligne.",
            "Code postal (facturation): Page 1 haut droite 3ème ligne.",
            "Commune (facturation): Page 1 haut droite 3ème ligne.",
            "Nom du site: Page 1 haut gauche en dessous de la ligne 'Site de consommation N°xxx'.",
            "PDL (reference_point_energie): Page 1 haut milieu en dessous de 'Référence GRD du Point de livraison' (format xxxxx/xx/xxxxxxx).",
            "Adresse du site (adresse_site): Page 1 haut gauche en dessous de 'Site de consommation N°xxx'.",
            "Code postal du site (code_postal_site): N'apparait pas sur la facture.",
            "Commune du site (ville_site): Page 1 haut gauche sous 'Site de consommation N°xxx' 2ème ligne.",
            "Segment énergie: N'apparait pas sur la facture.",
            "Date d'échéance: Page 1 haut milieu intitulée 'échéance'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "HÉLLIO": {
        "keywords": ["héllio", "hellio"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 encadré haut droite ligne intitulée 'Nom entreprise'.",
            "Adresse postale (facturation): Page 1 encadré haut droite ligne intitulée 'Adresse'.",
            "Code postal (facturation): Page 1 encadré haut droite ligne 'Adresse' 2ème ligne.",
            "Commune (facturation): Page 1 encadré haut droite ligne 'Adresse' 3ème ligne.",
            "Nom du site: Page 1 milieu gauche encadré 'Informations client' en dessous de 'Nom du client'.",
            "PDL/PRM/RAE (reference_point_energie): Page 1 milieu gauche 'Informations client' sous 'Référence interne' 1ère donnée, OU page 2 haut encadré 'Information client' ligne 'N° PRM/RAE/PDL'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche 'Informations client' sous 'Adresse de livraison'.",
            "Code postal du site (code_postal_site): Page 1 milieu gauche 'Informations client' sous 'Adresse de livraison'.",
            "Commune du site (ville_site): Page 1 milieu gauche 'Informations client' sous 'Adresse de livraison'.",
            "Segment énergie: Page 2 haut encadré 'Information client' ligne intitulée 'Segment'.",
            "Date d'échéance: Page 2 haut encadré 'Information client' ligne intitulée 'Date d'échéance'.",
            "PRO/PART: Pas d'indication directe.",
        ]
    },
    "JPME": {
        "keywords": ["jpme"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: Page 1 haut gauche encadré 'Informations client' ligne intitulée 'N° SIRET'.",
            "Raison sociale: Page 1 haut gauche encadré 'Informations client' en dessous de 'Raison sociale'.",
            "Adresse postale (facturation): Page 1 haut gauche encadré 'Informations client' à côté d'une icône maison.",
            "Code postal (facturation): Page 1 haut gauche 'Informations client' à côté icône maison.",
            "Commune (facturation): Page 1 haut gauche 'Informations client' à côté icône maison.",
            "Nom du site: Page 1 haut gauche 'Informations client' en dessous de 'Raison sociale'.",
            "PDL (reference_point_energie): Page 1 milieu gauche encadré 'Infos consommation' à côté 'Numéro point de livraison', OU page 2 haut gauche 2ème ligne 'PDL'.",
            "Adresse du site (adresse_site): Page 1 haut gauche 'Informations client' à côté icône maison (même que l'adresse de facturation).",
            "Code postal du site (code_postal_site): Page 1 haut gauche 'Informations client' à côté icône maison.",
            "Commune du site (ville_site): Page 1 haut gauche 'Informations client' à côté icône maison.",
            "Segment énergie: N'apparait pas sur la facture.",
            "Date d'échéance: N'apparait pas sur la facture.",
            "PRO/PART: Page 1 bas gauche ligne 'Par email' adresse mail service-clientpro@jpme.fr → PRO.",
        ]
    },
    "MINT ENERGIE": {
        "keywords": ["mint energie", "mint énergie"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: Page 1 haut gauche 3ème encadré 'Vos informations' ligne intitulée 'SIRET'.",
            "Raison sociale: Page 1 haut droite 1ère ligne.",
            "Adresse postale (facturation): Page 1 haut droite 2ème ligne.",
            "Code postal (facturation): Page 1 haut droite 3ème ligne.",
            "Commune (facturation): Page 1 haut droite 3ème ligne.",
            "Nom du site: N'apparait pas sur la facture.",
            "PRM/PDL (reference_point_energie): Page 1 haut gauche 3ème encadré 'vos informations' ligne 'PRM', OU page 2 encadré haut gauche ligne 'PDL'.",
            "Adresse du site (adresse_site): Page 1 haut gauche 2ème encadré 'vos informations' en dessous de 'Lieu de consommation'.",
            "Code postal du site (code_postal_site): Page 1 2ème encadré 'vos informations' 3ème ligne sous 'Lieu de consommation'.",
            "Commune du site (ville_site): Page 1 2ème encadré 'vos informations' 3ème ligne sous 'Lieu de consommation'.",
            "Segment énergie: Page 2 encadré haut gauche ligne intitulée 'Segment'.",
            "Date d'échéance: N'apparait pas sur la facture.",
            "PRO/PART: Page 1 milieu gauche encadré 'nous contacter' mail pro@mint-energie.com → PRO.",
        ]
    },
    "PRIMEO ENERGIE": {
        "keywords": ["primeo energie", "primeo énergie"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: Page 1 haut gauche encadré 'Vos références' 3ème ligne intitulée 'N°SIRET'.",
            "Raison sociale: Page 1 haut droite 1ère ligne encadré 'Vos références'.",
            "Adresse postale (facturation): Page 1 haut droite 2ème ligne encadré 'Vos références'.",
            "Code postal (facturation): Page 1 haut droite 3ème ligne encadré 'Vos références'.",
            "Commune (facturation): Page 1 haut droite 3ème ligne encadré 'Vos références'.",
            "Nom du site: Page 1 haut gauche encadré 'Vos références' ligne intitulée 'Adresse du point de livraison'.",
            "RAE (reference_point_energie): Page 1 haut gauche encadré 'Vos références' ligne 'Réf. Acheminement Electricité (RAE)'.",
            "Adresse du site (adresse_site): Page 1 haut gauche 'Vos références' ligne 'Adresse du point de livraison' 2ème ligne (absente sur certaines factures).",
            "Code postal du site (code_postal_site): Page 1 haut gauche 'Vos références' 'Adresse du point de livraison' 3ème ligne.",
            "Commune du site (ville_site): Page 1 haut gauche 'Vos références' 'Adresse du point de livraison' 3ème ligne.",
            "Segment énergie: N'apparait pas sur la facture.",
            "Date d'échéance: Page 1 haut droite.",
            "PRO/PART: Page 1 haut droite.",
        ]
    },
    "PICOTY": {
        "keywords": ["picoty", "picoty gaz"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite accompagnée de l'adresse.",
            "Adresse postale (facturation): Page 1 haut droite accompagnée de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite sous la rue.",
            "Commune (facturation): Page 1 haut droite après le code postal.",
            "Nom du site: Page 1 gauche dans 'lieu de consommation'.",
            "PCE (reference_point_energie): Page 1 encadré 'gaz naturel' sous l'échéance.",
            "Adresse du site (adresse_site): Page 1 sous lieu de consommation gauche.",
            "Code postal du site (code_postal_site): Page 1 sous lieu de consommation gauche.",
            "Commune du site (ville_site): Page 1 sous lieu de consommation gauche.",
            "Segment énergie: Page 1 gauche encadré 'gaz naturel': PCE (gaz → T1-T4).",
            "Date d'échéance: Page 1 gauche encadré 'gaz naturel' 3ème ligne: échéance.",
            "PRO/PART: Pas d'indication.",
        ]
    },
    "ENERGIES DU SANTERRE": {
        "keywords": ["energies du santerre"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 centre intitulé 'Titulaire du contrat'.",
            "PDL (reference_point_energie): Page 1 milieu gauche intitulé 'Point de livraison'.",
            "Adresse du site (adresse_site): Page 1 milieu droite en dessous du 'Titulaire du contrat' intitulé 'Situé'.",
            "Code postal du site (code_postal_site): Page 1 milieu droite sous la rue.",
            "Commune du site (ville_site): Page 1 milieu droite à côté du code postal.",
            "Segment énergie: Page 1 à côté du Numéro de contrat intitulé 'Tarif'.",
            "Date d'échéance: Page 1 après le tarif entre parenthèses '(Fin prévue :)'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "GAZ ÉLECTRICITÉ DE GRENOBLE (GEG)": {
        "keywords": ["gaz électricité de grenoble", "geg"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 centre intitulé 'Client titulaire'.",
            "PDL (reference_point_energie): Page 1 haut gauche intitulé 'réf ext'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche encadré 'Contrat' intitulé 'espace de livraison' après la raison sociale.",
            "Code postal du site (code_postal_site): Non repris sur la facture.",
            "Commune du site (ville_site): Page 1 milieu gauche 'Contrat' 'espace de livraison' après la rue.",
            "Segment énergie: Page 1 encadré 'contrat', le secteur est repris dans le nom de l'offre intitulé 'offre'.",
            "Date d'échéance: Page 1 encadré 'contrat' intitulé 'date d'échéance'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "ALTERNA ÉNERGIE": {
        "keywords": ["alterna énergie", "alterna"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 haut gauche partie 'Votre contrat' intitulé 'Titulaire du contrat'.",
            "PDL (reference_point_energie): Page 1 haut droite partie 'Vos références' intitulé 'Point de Livraison (PDL)'.",
            "Adresse du site (adresse_site): Page 1 haut gauche partie 'Votre contrat' intitulé 'Lieu de consommation'.",
            "Code postal du site (code_postal_site): Page 1 'Votre contrat' 'Lieu de consommation' après la rue.",
            "Commune du site (ville_site): Page 1 'Votre contrat' 'Lieu de consommation' après le code postal.",
            "Segment énergie: Page 1 haut gauche 'Votre contrat' intitulé 'Segment'.",
            "Date d'échéance: Page 1 haut gauche 'Votre contrat' intitulé 'Date de fin du contrat'.",
            "PRO/PART: Pas d'indication directe (offre de marché professionnel).",
        ]
    },
    "ÉLECTRICITÉ DE SAVOIE": {
        "keywords": ["électricité de savoie", "electricite de savoie"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 milieu droite intitulé 'Titulaire du contrat'.",
            "PDL (reference_point_energie): Page 1 milieu gauche intitulé 'Point de Livraison'.",
            "Adresse du site (adresse_site): Page 1 milieu droite en dessous du 'Titulaire du contrat' intitulé 'Situé'.",
            "Code postal du site (code_postal_site): Page 1 milieu droite 'Situé' après la rue.",
            "Commune du site (ville_site): Page 1 milieu droite 'Situé' après le code postal.",
            "Segment énergie: Page 1 milieu gauche intitulé 'Segment de votre contrat'.",
            "Date d'échéance: Page 1 milieu droite entre parenthèses intitulé 'Fin de contrat'.",
            "PRO/PART: Page 1 haut droite mention 'FACTURE DE CONTRAT DE FOURNITURE D'ENERGIE ELECTRIQUE N°PRO+Numéro' → PRO.",
        ]
    },
    "SAVE": {
        "keywords": ["save", "save energies"],
        "default_pages": (1, 2),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 milieu gauche 'DESCRIPTIF DU SITE DE FACTURATION' intitulé 'Nom du site'.",
            "PCE/PDL (reference_point_energie): Page 1 milieu gauche 'DESCRIPTIF DU SITE DE FACTURATION' intitulé 'PCE' ou 'PDL'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche 'DESCRIPTIF DU SITE DE FACTURATION' intitulé 'Adresse'.",
            "Code postal du site (code_postal_site): Page 1 'DESCRIPTIF DU SITE DE FACTURATION' intitulé 'Code postal-Ville'.",
            "Commune du site (ville_site): Page 1 'DESCRIPTIF DU SITE DE FACTURATION' intitulé 'Code postal-Ville'.",
            "Segment énergie: Page 2 haut encadré 'Données PCE' ou 'Données PDL' intitulé 'Option tarifaire'.",
            "Date d'échéance: Page 2 haut encadré 'Lieu du site de consommation' intitulé 'Date d'échéance du contrat'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "SYNELVA": {
        "keywords": ["synelva"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: N'apparait pas sur la facture.",
            "Raison sociale: Page 1 haut au centre.",
            "Adresse postale (facturation): Page 1 haut centre en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut centre en dessous de la rue.",
            "Commune (facturation): Page 1 haut centre en dessous de la rue.",
            "Nom du site: Page 1 haut centre à côté du code postal.",
            "PDL/PDS (reference_point_energie): Page 1 haut gauche intitulé 'Ref PDS'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche encadré 'Contrat n°' intitulé 'espace de livraison'.",
            "Code postal du site (code_postal_site): Non repris sur la facture.",
            "Commune du site (ville_site): Non repris sur la facture.",
            "Segment énergie: Non repris sur la facture.",
            "Date d'échéance: Page 1 milieu gauche encadré 'Contrat n°' intitulé 'date d'échéance'.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "GEDIA": {
        "keywords": ["gedia", "gedia energies", "gedia énergies"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: Page 1 milieu gauche encadré 'contrat n°' intitulé 'siren'.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 milieu partie 'contrat n°' intitulé 'client titulaire'.",
            "PDL (reference_point_energie): Page 1 haut droite intitulé 'référence' juste après le numéro de téléphone de dépannage Enedis.",
            "Adresse du site (adresse_site): Page 1 milieu gauche encadré 'contrat n°' intitulé 'espace de livraison'.",
            "Code postal du site (code_postal_site): Non repris sur la facture.",
            "Commune du site (ville_site): Page 1 encadré 'contrat n°' 'espace de livraison' en dessous de la rue.",
            "Segment énergie: Non repris sur la facture.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "SELIA": {
        "keywords": ["selia"],
        "default_pages": (1, 1),
        "hints": [
            "SIREN/SIRET: Page 1 haut gauche encadré 'n° contrat' intitulé 'siren'.",
            "Raison sociale: Page 1 haut droite.",
            "Adresse postale (facturation): Page 1 haut droite en dessous de la raison sociale.",
            "Code postal (facturation): Page 1 haut droite en dessous de la rue.",
            "Commune (facturation): Page 1 haut droite à côté du code postal.",
            "Nom du site: Page 1 milieu gauche encadré 'contrat n°' intitulé 'client titulaire'.",
            "PDL (reference_point_energie): Page 1 haut gauche intitulé 'réf ext'.",
            "Adresse du site (adresse_site): Page 1 milieu gauche encadré 'contrat n°' intitulé 'espace de livraison'.",
            "Code postal du site (code_postal_site): Non repris sur la facture.",
            "Commune du site (ville_site): Page 1 milieu gauche 'Contrat' 'espace de livraison' après la rue.",
            "Segment énergie: Non repris sur la facture.",
            "Date d'échéance: Non repris sur la facture.",
            "PRO/PART: Pas d'indication sur la facture.",
        ]
    },
    "AUTRE": {
        "keywords": [],
        "default_pages": (1, 2),
        "hints": [
            "PDL/PCE (reference_point_energie): Chercher un numéro de 14 chiffres.",
            "Adresse du site (adresse_site): Chercher 'Lieu de consommation', 'Lieu de livraison', 'Adresse du site'.",
            "Code postal du site (code_postal_site): Code postal à 5 chiffres associé au lieu de consommation.",
            "Commune du site (ville_site): Ville associée au lieu de consommation.",
            "Date d'échéance: Chercher 'Date de fin de contrat', 'Echéance', 'Fin prévue'.",
            "Segment énergie: Identifier C1-C5 (électricité) ou T1-T4 (gaz) selon la puissance ou le profil.",
        ]
    }
}

def get_extraction_defaults(filename: str) -> ExtractionDefaults:
    """Determine default extraction settings based on filename using Python 3.12+ features."""
    filename_lower: str = filename.lower()

    if any(pattern in filename_lower for pattern in IGNORED_PATTERNS):
        return {
            "supplier": "IGNORED",
            "pages_description": "N/A",
            "first_page": None,
            "last_page": None,
            "hints": []
        }

    detected_key: str = "AUTRE"
    for key, rules in SUPPLIER_RULES.items():
        if any(kw in filename_lower for kw in rules.get("keywords", [])):
            detected_key = key
            break

    rules: dict[str, Any] = SUPPLIER_RULES[detected_key]
    first, last = rules.get("default_pages", (None, None))
    
    return {
        "supplier": detected_key,
        "pages_description": f"Pages {first}-{last}" if first and last else "Tout le document",
        "first_page": first,
        "last_page": last,
        "hints": rules.get("hints", [])
    }

def extract_data(
    pdf_input: Any, 
    first_page: int | None = None, 
    last_page: int | None = None, 
    supplier: str | None = None, 
    hints: list[str] | None = None
) -> APIResult:
    """Extract data from a PDF file using PyMuPDF dynamic routing and Gemini API."""
    try:
        supplier_detected: str = supplier or "Inconnu"
        if supplier == "IGNORED":
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": "N/A"},
                "error": "Document ignoré : ce fichier n'est pas une facture d'énergie reconnue.",
            }

        # 1. Open the PDF using PyMuPDF (fitz)
        try:
            if hasattr(pdf_input, "read"):
                pdf_input.seek(0)
                pdf_bytes = pdf_input.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            else:
                doc = fitz.open(pdf_input)
        except Exception as e:
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": "ERROR"},
                "error": f"Error opening PDF: {e}",
            }
            
        max_pages = doc.page_count
        if max_pages == 0:
            doc.close()
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": "0"},
                "error": "No pages found in the PDF.",
            }

        # 2. Page Targeting Strategy: Parse hints for specific requested pages
        target_pages = set()
        if hints:
            for hint in hints:
                matches = re.findall(r'page\s*(\d+)', hint.lower())
                for m in matches:
                    page_num = int(m)
                    if 1 <= page_num <= max_pages:
                        target_pages.add(page_num)
        
        # Fallback to provided basic boundaries or just page 1 if no specific pages found in hints
        if not target_pages:
            start = first_page or 1
            end = last_page or max_pages
            # Cap at first 3 pages if no limits provided to save costs
            if not first_page and not last_page:
                 end = min(3, max_pages)
                 
            for p in range(start, end + 1):
                if 1 <= p <= max_pages:
                    target_pages.add(p)

        target_pages = sorted(list(target_pages))
        pages_description = f"Pages: {', '.join(map(str, target_pages))}"
        logger.info(f"Targeted PyMuPDF extraction for {supplier_detected} on {pages_description}")

        # 3. Convert only the targeted pages to images into memory
        image_parts = []
        try:
            for page_num in target_pages:
                # fitz pages are 0-indexed
                page = doc[page_num - 1]
                # dpi=200 equivalent scaling, render in Grayscale for accuracy
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                img_bytes = pix.tobytes("jpeg")
                
                # Pillow manipulation: increase contrast to remove "noisy" backgrounds
                img_pil = Image.open(io.BytesIO(img_bytes))
                enhancer = ImageEnhance.Contrast(img_pil)
                img_pil = enhancer.enhance(2.0)  # Double the contrast
                
                out_io = io.BytesIO()
                img_pil.save(out_io, format="JPEG")
                final_bytes = out_io.getvalue()
                
                image_parts.append(
                    types.Part.from_bytes(data=final_bytes, mime_type="image/jpeg")
                )
        except Exception as e:
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": pages_description},
                "error": f"Error rendering PDF pages: {e}",
            }
        finally:
            doc.close()

        if not GEMINI_API_KEY:
            return {
                "extraction": None,
                "metadata": {"supplier": supplier_detected, "pages": pages_description},
                "error": "GEMINI_API_KEY is not set.",
            }

        schema = {
            "type": "OBJECT",
            "properties": {
                "transcription_pdl_area": {"type": "STRING"},
                "adresse": {
                    "type": "OBJECT",
                    "properties": {
                        "street_number": {"type": "STRING"},
                        "street_name": {"type": "STRING"},
                    },
                    "required": ["street_number", "street_name"],
                },
                "code_postal": {"type": "STRING"},
                "ville": {"type": "STRING"},
                "adresse_site": {"type": "STRING"},
                "code_postal_site": {"type": "STRING"},
                "ville_site": {"type": "STRING"},
                "fournisseur_actuel": {"type": "STRING"},
                "nom_du_site": {"type": "STRING"},
                "reference_point_energie": {"type": "STRING"},
                "segment_energie": {"type": "STRING"},
                "tarif_reglemente": {"type": "BOOLEAN"},
                "date_echeance": {"type": "STRING"},
                "siren_siret": {"type": "STRING"},
                "raison_sociale": {"type": "STRING"},
                "type_client": {"type": "STRING"}
            },
            "required": ["adresse", "code_postal", "ville"],
        }

        prompt = f"""
        Analyze this energy invoice from supplier: {supplier_detected}.

        CRITICAL PRE-PROCESSING STEP (Chain-of-Thought):
        Before extracting anything else, in the 'transcription_pdl_area' field, transcribe the entire block of text specifically around the labels "Point de Livraison", "PDL", "PCE", "PRM", or "Lieu de consommation" exactly as it appears visually in the image. 
        Only after this transcription step should you extract the 'reference_point_energie' (which must be around 14 digits) and the 'adresse_site' components.

        Extract the following fields:

        BILLING ADDRESS (adresse de facturation — the address the invoice is sent to):
        - 'adresse': Split the billing street line into 'street_number' and 'street_name'.
        - 'code_postal': Postal code of the billing address.
        - 'ville': City/commune of the billing address.

        SITE / CONSUMPTION ADDRESS (lieu de livraison ou de consommation — where the energy is delivered):
        - 'adresse_site': Full street address of the consumption site (e.g. "12 rue de la Paix"). If identical to billing, copy it.
        - 'code_postal_site': Postal code of the consumption site. If identical to billing, copy it.
        - 'ville_site': City/commune of the consumption site. If identical to billing, copy it.

        OTHER FIELDS:
        - 'reference_point_energie': PDL/PCE/PRM reference number (typically 14 digits).
        - 'nom_du_site': Name/designation of the consumption site if present.
        - 'segment_energie': FORCIBLY return one of ONLY: C1, C2, C3, C4, C5 (Electricity) or T1, T2, T3, T4 (Gas).
        - 'date_echeance': Contract end date. Format: YYYY-MM-DD.
        - 'tarif_reglemente': True ONLY if strictly TRV/Tarif Bleu, else False.
        - 'siren_siret': SIREN or SIRET of the customer if present.
        - 'raison_sociale': Customer company name.
        - 'type_client': 'PRO' if professional/business invoice, 'PART' if individual/residential.
        - 'fournisseur_actuel': Name of the energy supplier.
        """
        if hints:
            prompt += "\nHints:\n" + "\n".join(f"- {h}" for h in hints)

        client = genai.Client(api_key=GEMINI_API_KEY)
        content_parts = [types.Part.from_text(text=prompt)] + image_parts

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[types.Content(role="user", parts=content_parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.0,
            ),
        )

        if hasattr(response, "text"):
            result = json.loads(response.text)
            return {
                "extraction": result,
                "metadata": {"supplier": supplier_detected, "pages": pages_description},
            }

        return {"error": "No response from Gemini API"}

    except Exception as e:
        logger.error(f"Error processing PDF: {e}")
        return {"error": f"Error processing PDF: {e}"}

if __name__ == "__main__":
    filename = "test.pdf"
    if os.path.exists(filename):
        print(extract_data(filename))
