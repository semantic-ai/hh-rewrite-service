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

### Context window

`llm.num_ctx` sets the context window in tokens and is only passed to the
`ollama` provider. Hosted providers (`openai`, `mistralai`) have a fixed
context window and ignore it. Ollama's default window is small and it
silently drops the start of longer prompts, which is where the guidelines
are, so set it explicitly for local models, for example `"num_ctx": 8192` or
`LLM__NUM_CTX=8192`. A rewrite combines the system prompt, the guideline, up
to `limits.max_input_length` characters of input and the generated output.

### Prompt labels

The model receives readable labels instead of raw identifiers. Two fixed
dictionaries in `rewrite` map them; persistence still stores the raw values.

- `address_forms` maps the instance's `lpdcExt:dutchLanguageVariant` to the
  address form named in the guidelines: `nl` and `nl-be-x-formal` to `u-vorm`,
  `nl-be-x-informal` to `je-vorm`. Unknown variants are passed unchanged.
- `field_labels` maps a field to the field name used in the HH guidelines,
  such as `Producttitel`, `Voorwaarden` or `Procedure`. Each entry has a
  `field` predicate URI, a `label` and an optional `subject_type`. Because
  nested LPDC resources reuse predicates such as `dct:description`, the
  service reads the RDF types of `related-subject` and first looks for an
  entry matching both type and field. If none matches, it uses an entry
  without `subject_type`. If that also fails, it sends the field URI itself.

```json
"rewrite": {
  "address_forms": { "nl-be-x-formal": "u-vorm", "nl-be-x-informal": "je-vorm" },
  "field_labels": [
    {
      "subject_type": "http://data.europa.eu/m8g/Requirement",
      "field": "http://purl.org/dc/terms/description",
      "label": "Voorwaarden"
    }
  ]
}
```

Setting either key in `config.json` replaces the built-in defaults in
[src/config.py](./src/config.py) as a whole, so list every entry you need.

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
`lpdcExt:dutchLanguageVariant`, which is mapped to `u-vorm` or `je-vorm`
before it reaches the model (see [Prompt labels](#prompt-labels)).

The field being rewritten is identified by the pair
`related-subject` and `related-field`. For example, `related-field` may be
`http://purl.org/dc/terms/title`, while `related-subject` identifies the particular procedure or
rule whose title is being rewritten. This is necessary because nested LPDC
resources can use the same predicate. The service reads the RDF types of
`related-subject` to give the model the matching field name.

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

The request reaches the model and triplestore, so configure `LLM__API_KEY` when
required by the selected provider and make sure mu-authorization and the
triplestore are running before using it. Set both `MU_SPARQL_ENDPOINT` and
`MU_SPARQL_UPDATEPOINT` to the SPARQL service address, for example
`http://database:8890/sparql` when the services share a Compose network.


Request handling never uses sudo. Every query carries the caller's session
headers, so mu-authorization is the only access check: a public service or
field generation the user cannot see resolves to a 404 before the model is
called.

At startup, the service waits until the triplestore answers a query without
sudo and then registers its agent:

- With `ALLOW_MU_AUTH_SUDO=true`, `write_agent_info` of
  `decide-ai-service-base` registers a versioned agent linked to a hash of the
  mounted configuration. This needs `COMPOSE_FILE`, `COMPOSE_SERVICE` and the
  compose file mounted into the container.
- With `ALLOW_MU_AUTH_SUDO=false`, the service uses the fixed agent URI
  `http://lblod.data.gift/id/components/hh-rewrite/v1.0.0`. The deployment must
  create this agent before rewrite requests are handled.

Rewrite requests reference the registered URI in their provenance activity.

## Deployment and configuration
Define the service in the deployment stack that provides its mu-services,
triplestore and network. This repository provides the service image definition
and example configuration files; the deployment defines the Compose service.

The container reads its configuration from `CONFIG_PATH`, which defaults to
`/config/config.json`. Mount one of the repository's configuration files (or a
configuration managed by the deployment) at that path, preferably
read-only. The JSON configuration has three sections:

| Section | Purpose |
| --- | --- |
| `llm` | Provider, model, endpoint, credentials, timeout and provider-specific options. |
| `limits` | Maximum input length, maximum attempts and model-call retries. |
| `rewrite` | Guideline URI, prompt templates and optional prompt-facing labels. |

Nested configuration values can be overridden with environment variables using
double underscores. Environment values are parsed as JSON when possible, so
numbers and booleans should be passed as JSON values:

```yaml
environment:
  CONFIG_PATH: /config/config.json
  LLM__PROVIDER: mistralai
  LLM__MODEL_NAME: mistral-small-2603
  LLM__BASE_URL: https://api.example.com/v1
  LLM__API_KEY: ${LLM_API_KEY}
  LLM__TEMPERATURE: "1.0"
  LLM__TIMEOUT: "30"
  LIMITS__MAX_INPUT_LENGTH: "4000"
  REWRITE__GUIDELINE_URI: https://example.org/guidelines/hh/v3
```

The environment override is applied after the JSON file is loaded. This makes
it suitable for secrets and deployment-specific values, while keeping prompts,
limits and model defaults in versioned configuration. Do not put API keys in a
committed JSON file. `LLM__API_KEY` is only needed for providers that require
authentication.

### Hosted providers

Mount a provider configuration and supply its secret through the environment.
Adjust the source path when the deployment files and this repository are laid
out differently:

```yaml
services:
  rewrite:
    image: semantic-ai/hh-rewrite-service:0.1.0
    environment:
      CONFIG_PATH: /config/config.json
      ALLOW_MU_AUTH_SUDO: "false"
      MU_SPARQL_ENDPOINT: http://database:8890/sparql
      MU_SPARQL_UPDATEPOINT: http://database:8890/sparql
      LOG_LEVEL: INFO
      LLM__API_KEY: ${LLM_API_KEY}
    volumes:
      - ./config/config.json:/config/config.json:ro
      - ./docker-compose.yml:/tmp/docker-compose.yaml:ro
    depends_on:
      database:
        condition: service_healthy
```



### Ollama provider

For a local model, use `config-ollama.json` and make the Ollama service
available under the hostname `ollama`. The base URL must be the container
address `http://ollama:11434`, not `http://localhost:11434`:

```yaml
services:
  rewrite:
    image: semantic-ai/hh-rewrite-service:0.1.0
    build:
      context: ../hh-rewrite-service
    environment:
      CONFIG_PATH: /config/config.json
      COMPOSE_FILE: /tmp/docker-compose.yaml
      COMPOSE_SERVICE: rewrite
      ALLOW_MU_AUTH_SUDO: "true"
      MU_SPARQL_ENDPOINT: http://database:8890/sparql
      MU_SPARQL_UPDATEPOINT: http://database:8890/sparql
      LOG_LEVEL: INFO
    volumes:
      - ./config/config-ollama.json:/config/config.json:ro
      - ./docker-compose.yml:/tmp/docker-compose.yaml:ro
    depends_on:
      database:
        condition: service_healthy
      ollama:
        condition: service_started

  ollama:
    image: ollama/ollama:0.30.8
    environment:
      OLLAMA_KEEP_ALIVE: "-1"
      OLLAMA_LOAD_TIMEOUT: "10m"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    volumes:
      - ollama-models:/root/.ollama/models
    entrypoint:
      - /bin/sh
      - -c
      - ollama serve & pid=$$! && sleep 5 && ollama pull gemma4:26b-a4b-it-qat && wait $$pid

volumes:
  ollama-models:
```

The matching `config-ollama.json` selects
`gemma4:26b-a4b-it-qat`, sets `timeout` to 120 seconds and sets `num_ctx` to
8192. `num_ctx` is passed only to Ollama and should be large enough to retain
the guideline and input text in the prompt. This 26B model requires an NVIDIA
GPU with Docker GPU support for practical response times. The Compose device
reservation shown above is therefore required for the intended deployment;
make sure the host has the NVIDIA Container Toolkit and a GPU with sufficient
VRAM. CPU-only execution may work as a fallback, but performance will be very
poor and requests can take an impractically long time. Use an equivalent GPU
runtime only when adapting the deployment, and make sure the model can load
within the available VRAM.

The Ollama service pulls the model when it starts. Persist `/root/.ollama/models`
in a named volume so restarts do not require downloading the model again. If a
different model or Ollama endpoint is used, update both `llm.model_name` and
`llm.base_url` in the mounted configuration, or override them with
`LLM__MODEL_NAME` and `LLM__BASE_URL`.

With `ALLOW_MU_AUTH_SUDO: "true"`, the deployment mounts the Compose file and
sets `COMPOSE_FILE` and `COMPOSE_SERVICE`. Those values allow startup to
register the versioned agent through `decide-ai-service-base`. If the
deployment creates the fixed agent and does not permit sudo, use
`ALLOW_MU_AUTH_SUDO: "false"` instead.

### Guideline and prompt configuration

Before sending a rewrite, the service reads `rewrite.guideline_uri` (or the
request-specific guideline relationship) through the authorized SPARQL
endpoint. The triplestore must already contain an `ext:Guideline` resource with
`mu:uuid`, `dct:title` and `ext:guidelineText`; the service does not create
guidelines. Set `REWRITE__GUIDELINE_URI` when the deployment uses a different
guideline version.

The `rewrite.prompt.system_message` and `rewrite.prompt.user_message` values
are Python format templates. The user template can use `{field}`,
`{address_form}`, `{guidelines}` and `{text}`. Keep the instruction to return
only the rewritten text unless the integration intentionally handles additional
model output.

`rewrite.address_forms` and `rewrite.field_labels` replace the built-in
defaults as complete values when supplied. Include every required entry in an
override, not only the entry being changed. Field labels can be scoped with
`subject_type` when the same predicate is used by different LPDC resource
types; see [Prompt labels](#prompt-labels) for the format.

The runtime also needs both `MU_SPARQL_ENDPOINT` and
`MU_SPARQL_UPDATEPOINT`. The service waits for a triplestore query to succeed
before it accepts rewrite requests. When the deployment is running, the
interactive API documentation is available at
`http://localhost:8080/docs` when the deployment publishes the service on port
8080.