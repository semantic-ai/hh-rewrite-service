# LPDC HH Rewrite Service

## Overview

This service turns an LPDC field value into a rewritten, guideline-aligned text suggestion for Heerlijk Helder (HH). It takes the original text, the owning public service and field, selects the configured rewrite guideline, asks the configured LLM to produce a better phrasing, and persists the result as a generation activity with provenance in the triplestore.

It is meant to support iterative rewriting of the same field: each field keeps a stable generation context, and later attempts can build on earlier suggestions while preserving the source text and provenance trail.

## Configuration

`config.json` contains the provider, model, operational limits and rewrite
prompt. Nested settings can be overridden with double-underscore environment
variables, for example `LLM__PROVIDER=openai`, `LLM__API_KEY=...`,
`LIMITS__RETRIES=2`.

The configured guideline URI is resolved on every rewrite request through the
authorized SPARQL endpoint. The resource must be typed `ext:Guideline` and
provide `mu:uuid`, `dct:title` and `ext:guidelineText`.

## Data model

The service stores one stable field-generation resource and one generation
activity for every rewrite. The activity is typed as `prov:Activity` and has
`dct:type lpdcExt:GenerationActivity`. It contains the attempt number, source
text, generated text and review status, and records the model invocation and
links to the selected guideline and agent. An `ext:AICall` resource records
the provider call details when model metadata is
available.

The prefixes currently used by the writer are:

| Prefix | Namespace | Use |
| --- | --- | --- |
| `lpdcExt` | `https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#` | Field-generation and generation-activity terms. |
| `mu` | `http://mu.semte.ch/vocabularies/core/` | Shared UUID values. |
| `prov` | `http://www.w3.org/ns/prov#` | Activity and provenance relationships. |
| `ext` | `http://mu.semte.ch/vocabularies/ext/` | Guideline and AI-call metadata owned by the shared service conventions. |
| `dct` | `http://purl.org/dc/terms/` | Language metadata. |

The main resources and predicates are:

| Resource | Predicates written by this service |
| --- | --- |
| `lpdcExt:FieldGeneration` | `lpdcExt:relatedSubject`, `lpdcExt:relatedField`, `lpdcExt:fieldGenerationInstance`, `mu:uuid` |
| `prov:Activity` with `dct:type lpdcExt:GenerationActivity` | `lpdcExt:attemptNumber`, `lpdcExt:originalText`, `lpdcExt:generatedText`, `lpdcExt:status`, `lpdcExt:hasGenerationActivity`, `prov:startedAtTime`, `prov:endedAtTime`, `prov:wasAssociatedWith`, `prov:used`, `ext:addressForm`, `dct:language`, `mu:uuid` |
| `ext:AICall` | `ext:endpoint`, `ext:aiModel`, `ext:tokenIn`, `ext:tokenOut`, optional `ext:duration`, `mu:uuid`; linked from the activity with `ext:performedAICall` |

The guideline is read as an existing `ext:Guideline` resource. Its required
properties are `mu:uuid`, `dct:title` and `ext:guidelineText`. The service does
not create or update guideline resources.

For example, one stored generation activity has this shape:

```turtle
@prefix dct: <http://purl.org/dc/terms/> .
@prefix ext: <http://mu.semte.ch/vocabularies/ext/> .
@prefix lpdcExt: <https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#> .
@prefix mu: <http://mu.semte.ch/vocabularies/core/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
<http://data.lblod.info/id/generation-activities/example>
    a prov:Activity ;
  mu:uuid "example" ;
  dct:type lpdcExt:GenerationActivity ;
  lpdcExt:attemptNumber 1 ;
  lpdcExt:originalText "De aanvrager dient het formulier in te vullen." ;
  lpdcExt:generatedText "Vul het formulier in." ;
  lpdcExt:status lpdcExt:notUsed ;
  lpdcExt:hasGenerationActivity <http://data.lblod.info/id/field-generations/example> ;
    prov:wasAssociatedWith <http://lblod.data.gift/id/components/hh-rewrite/v1.0.0> ;
    prov:used <https://example.org/guidelines/hh/v3> ;
    ext:addressForm "nl-be-x-formal" ;
    dct:language "nl" ;
    ext:performedAICall <http://data.lblod.info/id/ai-call/example> .

<http://data.lblod.info/id/ai-call/example>
  a ext:AICall ;
  mu:uuid "example" ;
  ext:endpoint "https://api.mistral.ai/v1" ;
  ext:aiModel <http://data.lblod.info/ontology/airo#mistral-small-2603> ;
  ext:tokenIn 184 ;
  ext:tokenOut 42 ;
  ext:duration 3.75 .
```



## API

`POST /hh-rewrites` accepts JSON:API with `Content-Type: application/vnd.api+json`:

```json
{
	"data": {
		"type": "generation-activities",
		"attributes": {
			"original-text": "De aanvrager dient het formulier in te vullen.",
			"related-subject": "http://data.lblod.info/id/procedures/456",
			"related-field": "http://purl.org/dc/terms/title"
		},
		"relationships": {
			"public-service": {
				"data": {"type": "public-services", "id": "123"}
			},
			"field-generation": {"data": null},
      "guideline": {"data": null}
    }
  }
}
```

### Request body explained

The request is a JSON:API document. The top-level `data` object describes
the generation activity being requested:

| Part | Required? | Meaning |
| --- | --- | --- |
| `data.type` | Yes | Must be `generation-activities`. This is the resource type that the service will create. |
| `data.attributes.original-text` | Yes | Plain text sent to the model. Empty text and markup are rejected, and the service enforces `limits.max_input_length`. |
| `data.attributes.related-subject` | Yes | URI of the concrete resource that owns the field, such as a procedure or rule. |
| `data.attributes.related-field` | Yes | Absolute URI of the field predicate on that subject, such as `http://purl.org/dc/terms/title`. |
| `data.relationships` | `public-service` is required for a successful request | The rewrite cannot proceed without `data.relationships.public-service`. Send the `public-service` relationship shown below. |

The `related-subject` and `related-field` pair identifies the exact field.
`related-field` alone is not enough because different nested resources can
have the same predicate. The service requires `related-field` to be an
absolute URI.

### How the guideline is selected

The caller normally does **not** need to send a `guideline` relationship. The
service selects and fetches one guideline for the request:

1. `rewrite.guideline_uri` in [config.json](./config.json) identifies the
  guideline version, for example `https://example.org/guidelines/hh/v3`.
2. `data.relationships.guideline.data`, when present, identifies the guideline
  resource for that request. Its `id` must be an absolute URI.
3. The service reads the matching `ext:Guideline` resource through
  mu-authorization and places its `ext:guidelineText` in the prompt.
4. The selected URI is stored as `prov:used` provenance and returned as the
  task's `guideline` relationship.

Guidelines are assumed to be present in the triplestore. A missing or empty
resource returns HTTP 404 before a model call or persistence write.
Environment overrides can change the configured default before startup,
including `REWRITE__GUIDELINE_URI`.

### Request relationships

The relationships do not all need to be filled in:

| Relationship | Required? | What to send | Meaning |
| --- | --- | --- | --- |
| `public-service` | **Yes** | `{ "data": { "type": "public-services", "id": "123" } }` | Existing LPDC product/service instance that provides the context for the rewrite. The service reads settings such as its Dutch language variant from this resource. |
| `field-generation` | No | Omit it, or send `{ "data": null }`. To reuse a known generation, send `{ "data": { "type": "field-generations", "id": "..." } }`. | Stable parent grouping all rewrite attempts for the same field. If omitted or null, the service finds or creates it from `public-service`, `related-subject` and `related-field`. |
| `guideline` | No | Omit it, or send `{ "data": null }`. To select a request-specific guideline, send `{ "data": { "type": "guidelines", "id": "https://example.org/guidelines/hh/v3" } }`. | Versioned `ext:Guideline` resource used to build the prompt. If omitted or null, the service uses `rewrite.guideline_uri`. The identifier must be an absolute URI. |

An identifier can be a short JSON:API id such as `"123"` or an absolute
resource URI. Relationship types are interpreted by the caller and should
match the expected JSON:API type. The `guideline` relationship must use the
absolute URI of the external guideline resource.

For a first rewrite, this is the minimal valid relationship shape:

```json
"relationships": {
  "public-service": {
    "data": {"type": "public-services", "id": "123"}
  }
}
```

The following is equivalent and makes the optional relationships explicit:

```json
"relationships": {
  "public-service": {
    "data": {"type": "public-services", "id": "123"}
  },
  "field-generation": {"data": null},
  "guideline": {"data": null}
}
```

For a subsequent rewrite using existing resources:

```json
"relationships": {
  "public-service": {
    "data": {"type": "public-services", "id": "123"}
  },
  "field-generation": {
    "data": {"type": "field-generations", "id": "456"}
  },
}
```

The supplied `field-generation` must identify the same public service,
related subject and related field as the request. The service calculates the
next `attempt-number`; clients do not send it. Attempts within a field
generation are ordered by that number and do not branch.

### Resource hierarchy

The rewrite resources are nested conceptually under an existing LPDC
`public-service` resource:

```text
public-service (the concrete LPDC product/service instance)
├── field-generations (one per addressable field)
│   └── generation-activities (one per rewrite)
└── decision-documents (source documents; Track 1)
    └── attachments
        └── sub-attachments
```

The corresponding JSON:API resource types are:

| Resource type | Role | Main relationship |
| --- | --- | --- |
| `public-services` | Concrete LPDC service/product instance (`lpdcExt:InstancePublicService`) | Context for the resources below |
| `field-generations` | Stable grouping for one field on that service | Belongs to a `public-service`; contains generation activities |
| `generation-activities` | One AI rewrite activity with its input/output text and review status | Belongs to a `field-generation` |
| `decision-documents` | Source document used by the extraction workflow | Belongs to a `public-service` |
| `attachments` | File content attached to a decision document | Belongs to a `decision-document` or another attachment |

`public-services` is not created by this rewrite service. It is an existing
LPDC resource. The service uses its identifier to establish the product
context and reads instance-specific settings such as
`lpdcExt:dutchLanguageVariant`.

The field being rewritten is identified by the pair
`related-subject` and `related-field`. For example, `related-field` may be
`http://purl.org/dc/terms/title`, while `related-subject` identifies the particular procedure or
rule whose title is being rewritten. This is necessary because nested LPDC
resources can use the same predicate.

The lifecycle of a rewrite is therefore:

```text
public-service
    └── field-generation
            ├── generation-activity 1
            ├── generation-activity 2
            └── generation-activity 3
```

The `field-generation` is reused for subsequent rewrites of the same
addressable field. Each `generation-activity` records the text supplied to that
activity (`original-text`), the model suggestion (`generated-text`) and its
status (`notUsed` or `used`). The rewrite service does not write
`final-text`.
`field-generation` and `generation-activity` are persistence resources exposed
by mu-cl-resources; this service owns the rewrite action and writes the
resulting activity and provenance data.

When the container is running, open [http://localhost:8080/docs](http://localhost:8080/docs),
select `POST /hh-rewrites`, click **Try it out**, and use the generated JSON:API example.
The endpoint has one request-body parameter rather than separate Swagger fields because
JSON:API sends all attributes and relationships in one document.

The request reaches the model and triplestore, so configure `LLM__API_KEY` and make
sure mu-authorization and the development triplestore are running before using
it. The development compose stack configures both `MU_SPARQL_ENDPOINT` and
`MU_SPARQL_UPDATEPOINT` to `http://database:8890/sparql`; keep these variables
set when deploying with the mu-python-template runtime.


At startup, the service waits for the triplestore and registers the versioned
rewrite agent through `decide-ai-service-base`. Agent configuration hashing and
agent reuse are handled by that package; rewrite requests only reference the
registered agent URI in their provenance activity.

## Compose

`docker compose up --build` starts the service container and a development Virtuoso instance. The FastAPI template image is pinned to the resolved multi-architecture digest in `Dockerfile`. Configure `LLM__API_KEY`, and use `LLM__PROVIDER`, `LLM__MODEL_NAME` and `LLM__BASE_URL` for the selected provider. The configured `gpt-6-luna` deployment only supports the default model temperature, so the example configuration uses `LLM__TEMPERATURE=1.0`.
Virtuoso is exposed on host port `8890` for local SPARQL workbooks and tools.