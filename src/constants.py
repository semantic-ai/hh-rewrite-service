"""Shared RDF namespaces, types and concept URIs.

Kept free of template and framework dependencies so any module (including
web.py and the unit tests) can import it at module level.
"""

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

ADMS = "http://www.w3.org/ns/adms#"
CPSV = "http://purl.org/vocab/cpsv#"
DCT = "http://purl.org/dc/terms/"
EXT = "http://mu.semte.ch/vocabularies/ext/"
FOAF = "http://xmlns.com/foaf/0.1/"
LPDC = "http://data.lblod.info/vocabularies/lpdc/"
LPDCEXT = "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#"
M8G = "http://data.europa.eu/m8g/"
MU = "http://mu.semte.ch/vocabularies/core/"
PROV = "http://www.w3.org/ns/prov#"
SCHEMA = "https://schema.org/"
TASK = "http://redpencil.data.gift/vocabularies/tasks/"
TCS = "https://w3id.org/tcs#"

# Base for newly minted resource URIs: f"{DATA_URI_BASE}<type>/<uuid>".
DATA_URI_BASE = "http://data.lblod.info/id/"

# ---------------------------------------------------------------------------
# Types and predicates
# ---------------------------------------------------------------------------

INSTANCE_PUBLIC_SERVICE = f"{LPDCEXT}InstancePublicService"
FIELD_GENERATION = f"{LPDC}FieldGeneration"
GENERATION_ACTIVITY = f"{LPDC}GenerationActivity"
HAS_GENERATION_ACTIVITY = f"{LPDC}hasGenerationActivity"

# ---------------------------------------------------------------------------
# Concepts
# ---------------------------------------------------------------------------

NOT_USED = "http://lblod.data.gift/concepts/0ec7b4a8-c281-4ff7-8b50-c02063a1e0dc"
