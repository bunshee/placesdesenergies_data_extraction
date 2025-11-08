EXTRACTION_PROMPT = """
**TASK:** Extract structured data from French energy invoices (gas or electricity).

**CRITICAL FIELDS:**

1. **energy_reference** (Référence Point d'Énergie - PRM/PDL/PCE):
   - Unique delivery point identifier
   - **Gas (GRDF)**: PCE (Point de Comptage et d'Estimation)
     * Usually 14 digits: "25841823335979"
     * Sometimes shorter alphanumeric: "GI024906", "GI123456"
     * Look for: "PCE", "Référence du PCE", "Point de Comptage et d'Estimation"
   - **Electricity (Enedis)**: PDL or PRM (Point De Livraison / Point de Référence Mesure)
     * Always 14 digits: "25841823335979"
     * Look for: "PDL", "PRM", "Point de livraison"
   - Common locations:
     * "Données de comptage" section
     * "Informations Point de Livraison"
     * "Caractéristiques du compteur"
   - Format: Remove spaces/dashes, keep alphanumeric characters
   - Examples:
     * "Point de Comptage et d'Estimation: 25 841 823 335 979" → "25841823335979"
     * "Point de livraison": "25129667026730"
     * "Référence du PCE : GI024906" → "GI024906"

**IMPORTANT FIELDS:**

2. **site_name** (Nom du site):
   - Name of the entity/building receiving the invoice
   - Can be: copropriété name, company name, residence name
   - Found in: "Vos informations client", "Nom du client" section, header
   - Example: "SDC LE JARDIN DU CEDRE"

3. **address_consumption** (Adresse):
   - FULL street address of the CONSUMPTION location (where energy is used)
   - Prioritize consumption address over billing address if different
   - Look for: "Lieu de consommation", "Adresse de consommation"
   - Exclude city and postal code (separate fields)
   - Example: "107 AVENUE CHARLES DE GAULLE LE JARDIN DU CEDRE A"

4. **postal_code** (Code postal):
   - 5-digit postal code from CONSUMPTION address
   - Example: "84130"

5. **city** (Commune):
   - City/commune name from CONSUMPTION address
   - Example: "LE PONTET"

6. **energy_segment** (Segment énergie):
   - Energy type + tariff segment + contract type
   - Gas tariff segments: T1, T2, T3, T4 (based on annual consumption)
   - Electricity segments: C5, C4, C3, C2, C1 (based on subscribed power)
   - Contract types: "Prix Fixe", "Contrat Garanti", "Offre verte"
   - Example: "Gaz, Contrat Garanti, Tarif T2"

7. **contract_expiry_date** (Date d'échéance du contrat):
   - Contract end date or relevant contract timing information
   - If no specific end date, extract:
     * Contract start date: "Souscrit depuis le DD/MM/YYYY"
     * Next invoice date: "Prochaine facture vers le DD/MM/YYYY"
     * If unlimited duration: mention "Durée indéterminée avec révision annuelle"
   - Example: "Souscrit depuis le 13/06/2019"

8. **termination_notice** (Préavis de résiliation):
   - Minimum notice period required to terminate the contract
   - Look for: "Préavis", "Délai de préavis de résiliation"
   - Example: "30 jours", "1 mois"

9. **supplier** (Fournisseur actuel):
   - Name of the energy provider company
   - Usually in header/footer with logo
   - Example: "EDF", "Energie", "Gaz Européen", "TotalEnergies"

10. **client_siren_siret** (SIREN/SIRET du client):
    - CLIENT's company identification number (NOT the supplier's)
    - SIREN: 9 digits (company level) - Format: "123456789"
    - SIRET: 14 digits (establishment level) - Format: "12345678901234"
    - Common locations in invoice:
      * "Vos informations client" section
      * "Informations du Gestionnaire de Facture"
      * "Client" or "Souscripteur" section
      * Near client name/address at top of invoice
    - Labels to look for:
      * "SIREN :", "SIREN:", "Nº SIREN"
      * "SIRET :", "SIRET:", "Nº SIRET"
      * "Informations du Gestionnaire de Facture SIREN :"
    - IMPORTANT: Extract CLIENT's SIREN/SIRET only, NOT the supplier's
    - Supplier's SIREN/SIRET is usually in footer/legal mentions - ignore it
    - Prefer SIRET (14 digits) if available, otherwise SIREN (9 digits)
    - Example: "Informations du Gestionnaire de Facture SIREN : 349759647" → "349759647"

11. **regulated_tariff** (Tarif réglementé):
    - Indicates if price is government-regulated ("Oui") or market offer ("Non")
    - "Oui" if: "TRV", "Tarif Réglementé de Vente", "tarif réglementé"
    - "Non" if: "Prix non réglementés", "Offre de marché", "Prix de marché", "Prix fixe"
    - Note: Regulated gas tariff no longer exists for professionals in France
    - Example: "Prix non réglementés" → "Non"

**OUTPUT FORMAT:**
- Valid JSON only, no markdown
- Use "N/A" for missing strings, null for missing dates
- Validate formats before output
"""

CLASSIFICATION_PROMPT = ""
