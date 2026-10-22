# LPDC HH Rewrite Service

## Overview

This service turns an LPDC field value into a rewritten, guideline-aligned text suggestion for Heerlijk Helder (HH). It takes the original text, the owning public service and field, selects the configured rewrite guideline, asks the configured LLM to produce a better phrasing, and persists the result as a generation attempt with provenance in the triplestore.

It is meant to support iterative rewriting of the same field: each field keeps a stable generation context, and later attempts can build on earlier suggestions while preserving the source text and provenance trail.

## Configuration

`config.json` contains the provider, model, limits, graphs and rewrite prompt. Nested settings can be overridden with double-underscore environment variables, for example `LLM__PROVIDER=openai`, `LLM__API_KEY=...` and `LIMITS__RETRIES=2`.

The configured guideline URI is resolved in the domain graph on every rewrite request. The resource must be typed `ext:Guideline` and provide `mu:uuid`, `dct:title` and `ext:guidelineText`. 



## API

`POST /hh-rewrites` accepts JSON:API with `Content-Type: application/vnd.api+json`:

```json
{
	"data": {
		"type": "generation-attempts",
		"attributes": {
			"original-text": "De aanvrager dient het formulier in te vullen.",
			"related-subject": "http://data.lblod.info/id/procedures/456",
			"related-field": "dct:title"
		},
		"relationships": {
			"public-service": {
				"data": {"type": "public-services", "id": "123"}
			},
			"field-generation": {"data": null},
      "based-on": {"data": null},
      "guideline": {"data": null}
		}
	}
}
```

### Request body explained

The request is a JSON:API document. The top-level `data` object describes
the rewrite attempt being requested:

| Part | Required? | Meaning |
| --- | --- | --- |
| `data.type` | Yes | Must be `generation-attempts`. This is the resource type that the service will create. |
| `data.attributes.original-text` | Yes | The text sent to the model. The caller is responsible for supplying suitable plain text within the configured model limits. |
| `data.attributes.related-subject` | Yes | URI of the concrete resource that owns the field, such as a procedure or rule. |
| `data.attributes.related-field` | Yes | Predicate/name of the field on that subject, such as `dct:title`. |
| `data.relationships` | No as an object | The relationship object may be omitted; missing values will fail during request processing. |

The `related-subject` and `related-field` pair identifies the exact field.
`related-field` alone is not enough because different nested resources can
have the same predicate.

### How the guideline is selected

The caller normally does **not** need to send a `guideline` relationship. The
service selects and fetches one guideline for the request:

1. `rewrite.guideline_uri` in [config.json](./config.json) identifies the
   guideline version, for example `https://example.org/guidelines/hh/v1`.
2. `data.relationships.guideline.data`, when present, identifies the guideline
  resource for that request. Its `id` must be an absolute URI.
3. The service reads the matching `ext:Guideline` resource from the domain
  graph and places its `ext:guidelineText` in the prompt.
4. The selected URI is stored as `prov:used` provenance and returned as the
  activity's `guideline` relationship.

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
| `based-on` | No | Omit it, or send `{ "data": null }`. For a follow-up rewrite, send a `generation-attempts` identifier. | Previous attempt whose text is being refined. Null means this is the first attempt or a new branch from the supplied `original-text`. The referenced attempt must belong to the same field generation. |
| `guideline` | No | Omit it, or send `{ "data": null }`. To select a request-specific guideline, send `{ "data": { "type": "guidelines", "id": "https://example.org/guidelines/hh/v1" } }`. | Versioned `ext:Guideline` resource used to build the prompt. If omitted or null, the service uses `rewrite.guideline_uri`. The identifier must be an absolute URI. |

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
  "based-on": {"data": null},
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
  "based-on": {
    "data": {"type": "generation-attempts", "id": "789"}
  }
}
```

The supplied `field-generation` must identify the same public service,
related subject and related field as the request. The `based-on` attempt must
belong to that same field generation. The service calculates the next
`attempt-number`; clients do not send it.

### Resource hierarchy

The rewrite resources are nested conceptually under an existing LPDC
`public-service` resource:

```text
public-service (the concrete LPDC product/service instance)
├── field-generations (one per addressable field)
│   └── generation-attempts (one per rewrite)
└── decision-documents (source documents; Track 1)
    └── attachments
        └── sub-attachments
```

The corresponding JSON:API resource types are:

| Resource type | Role | Main relationship |
| --- | --- | --- |
| `public-services` | Concrete LPDC service/product instance (`lpdcExt:InstancePublicService`) | Context for the resources below |
| `field-generations` | Stable grouping for one field on that service | Belongs to a `public-service`; contains attempts |
| `generation-attempts` | One immutable AI rewrite result and its input/output text | Belongs to a `field-generation` |
| `decision-documents` | Source document used by the extraction workflow | Belongs to a `public-service` |
| `attachments` | File content attached to a decision document | Belongs to a `decision-document` or another attachment |

`public-services` is not created by this rewrite service. It is an existing
LPDC resource. The service uses its identifier to establish the product
context and reads instance-specific settings such as
`lpdcExt:dutchLanguageVariant`.

The field being rewritten is identified by the pair
`related-subject` and `related-field`. For example, `related-field` may be
`dct:title`, while `related-subject` identifies the particular procedure or
rule whose title is being rewritten. This is necessary because nested LPDC
resources can use the same predicate.

The lifecycle of a rewrite is therefore:

```text
public-service
    └── field-generation
            ├── generation-attempt 1
            ├── generation-attempt 2
            └── generation-attempt 3
```

The `field-generation` is reused for subsequent rewrites of the same
addressable field. Each `generation-attempt` records the text supplied to that
attempt (`original-text`), the model suggestion (`generated-text`) and,
where applicable, the user-selected or corrected value (`final-text`).
`field-generation` and `generation-attempt` are persistence resources exposed
by mu-cl-resources; this service owns the rewrite action and writes the
resulting attempt and provenance activity.

When the container is running, open [http://localhost:8080/docs](http://localhost:8080/docs),
select `POST /hh-rewrites`, click **Try it out**, and use the generated JSON:API example.
The endpoint has one request-body parameter rather than separate Swagger fields because
JSON:API sends all attributes and relationships in one document.

The request reaches the model and triplestore, so configure `LLM__API_KEY` and make
sure the development triplestore is running before using it. The development
compose stack configures both `MU_SPARQL_ENDPOINT` and
`MU_SPARQL_UPDATEPOINT` to `http://triplestore:8890/sparql`; keep these
variables set when deploying with the mu-python-template runtime.


At startup, the service waits for the triplestore and registers the versioned
rewrite agent through `decide-ai-service-base`. Agent configuration hashing and
agent reuse are handled by that package; rewrite requests only reference the
registered agent URI in their provenance activity.

## Compose

`docker compose up --build` starts the service container and a development Virtuoso instance. The FastAPI template image is pinned to the resolved multi-architecture digest in `Dockerfile`. Configure `LLM__API_KEY`, and use `LLM__PROVIDER`, `LLM__MODEL_NAME` and `LLM__BASE_URL` for the selected provider. The configured `gpt-6-luna` deployment only supports the default model temperature, so the example configuration uses `LLM__TEMPERATURE=1.0`.
Virtuoso is exposed on host port `8890` for local SPARQL workbooks and tools.