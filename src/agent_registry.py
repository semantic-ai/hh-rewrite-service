"""Agent and configuration registration for the HH rewrite service.

Ported from decide-ai-service-base v0.3.4 (util.py, ai_logging.py and
sparql_config.py). This module intentionally avoids framework request state so
registration is safe during Flask worker startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from string import Template
from typing import Any

import yaml


logger = logging.getLogger("hh-rewrite-service.agent-registry")

EXT = "http://mu.semte.ch/vocabularies/ext/"
FOAF = "http://xmlns.com/foaf/0.1/"
MU = "http://mu.semte.ch/vocabularies/core/"
PROV = "http://www.w3.org/ns/prov#"
SCHEMA = "https://schema.org/"
TCS = "https://w3id.org/tcs#"

JOB_GRAPH = os.getenv("JOB_GRAPH", "http://mu.semte.ch/graphs/jobs")
BASE_AGENT_URI = os.getenv("BASE_AGENT_URI", "http://lblod.data.gift/id/components/")
BASE_CONFIG_URI = os.getenv("BASE_CONFIG_URI", "http://lblod.data.gift/id/configurations/")
COMPOSE_FILE = os.getenv("COMPOSE_FILE", "docker-compose.yaml")
COMPOSE_SERVICE = os.getenv("COMPOSE_SERVICE", "ai")
IGNORE_MOUNT_REGEX = os.getenv("IGNORE_MOUNT_REGEX", "^(/data)|(/app)")
ALLOW_MU_AUTH_SUDO = os.getenv("ALLOW_MU_AUTH_SUDO", "false").lower() == "true"

_agent_uri: str | None = None
_config_uri: str | None = None


def _sparql_escape_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def _sparql_escape_uri(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace(">", "\\>")
    return f"<{escaped}>"


def _get_prefixes(*names: str) -> str:
    prefixes = {
        "ext": EXT,
        "foaf": FOAF,
        "mu": MU,
        "prov": PROV,
        "schema": SCHEMA,
        "tcs": TCS,
    }
    return "\n".join(f"PREFIX {name}: <{prefixes[name]}>" for name in names) + "\n"


def _sparql(query: str, *, update: bool = False, sudo: bool = False) -> dict[str, Any]:
    """Execute one startup SPARQL operation without using Flask request state."""

    from SPARQLWrapper import JSON, POST, SPARQLWrapper

    endpoint = os.getenv(
        "MU_SPARQL_UPDATEPOINT" if update else "MU_SPARQL_ENDPOINT"
    )
    wrapper = SPARQLWrapper(endpoint)
    wrapper.setQuery(query)
    wrapper.setReturnFormat(JSON)
    if update:
        wrapper.setMethod(POST)
    if sudo:
        wrapper.addCustomHttpHeader("mu-auth-sudo", "true")
    response = wrapper.query()
    return {} if update else response.convert()


def wait_for_triplestore() -> None:
    """Wait until the triplestore answers a non-sudo query."""

    logger.info("Waiting for triplestore...")
    while True:
        try:
            result = _sparql("SELECT ?s WHERE { ?s ?p ?o . } LIMIT 1")
            if result.get("results", {}).get("bindings"):
                break
        except Exception:
            logger.info("Triplestore not live yet, retrying...")
        time.sleep(1)
    logger.info("Triplestore ready!")


def get_agent_uri() -> str:
    if not _agent_uri:
        raise ValueError("Requested agent uri, but the agent was not registered.")
    return _agent_uri


def set_agent_uri(uri: str) -> None:
    global _agent_uri
    _agent_uri = uri


def _ensure_config_uri() -> str:
    global _config_uri
    if _config_uri:
        return _config_uri

    config = fetch_config()
    config_as_string = json.dumps(config, sort_keys=True, indent=2)
    hashed_config = hashlib.sha256(config_as_string.encode()).hexdigest()
    existing_config_uri = _get_existing_config_uri(hashed_config)
    if existing_config_uri:
        _config_uri = existing_config_uri
        return existing_config_uri

    config_id = str(uuid.uuid4())
    config_uri = BASE_CONFIG_URI + config_id
    _store_config_uri(config_uri, config_id, config, hashed_config)
    _config_uri = config_uri
    return config_uri


def _get_existing_config_uri(hashed_config: str) -> str | None:
    query = Template(
        _get_prefixes("ext")
        + """
        SELECT ?agent_config WHERE {
            ?agent_config a ext:AgentConfig ;
                ext:configHash $hashed_config .
        }
        """
    ).substitute(hashed_config=_sparql_escape_string(hashed_config))
    result = _sparql(query, sudo=True)
    bindings = result.get("results", {}).get("bindings", [])
    return bindings[0]["agent_config"]["value"] if bindings else None


def _store_config_uri(config_uri: str, config_id: str, config: dict[str, Any], hashed_config: str) -> None:
    query = Template(
        _get_prefixes("ext", "mu")
        + """
        INSERT DATA {
            GRAPH $graph {
                $configuration_uri a ext:AgentConfig ;
                    mu:uuid $config_id ;
                    ext:configHash $config_hash .
            }
        }
        """
    ).substitute(
        graph=_sparql_escape_uri(JOB_GRAPH),
        configuration_uri=_sparql_escape_uri(config_uri),
        config_id=_sparql_escape_string(config_id),
        config_hash=_sparql_escape_string(hashed_config),
    )
    for key, value in config["config"].items():
        _store_config_part(config_uri, value, key)
    compose = json.dumps(config["compose"], sort_keys=True, indent=2)
    _store_config_part(config_uri, compose, "docker-compose.yml:service")
    _sparql(query, update=True, sudo=True)


def _store_config_part(config_uri: str, config: str, path: str) -> None:
    part_id = str(uuid.uuid4())
    part_uri = BASE_CONFIG_URI + "part/" + part_id
    query = Template(
        _get_prefixes("mu", "ext")
        + """
        INSERT DATA {
            GRAPH $graph {
                $configuration_uri ext:hasConfigFile $part_uri .
                $part_uri a ext:ConfigPart ;
                    mu:uuid $part_id ;
                    ext:configPath $config_path ;
                    ext:configText $config_as_string .
            }
        }
        """
    ).substitute(
        graph=_sparql_escape_uri(JOB_GRAPH),
        part_uri=_sparql_escape_uri(part_uri),
        part_id=_sparql_escape_string(part_id),
        configuration_uri=_sparql_escape_uri(config_uri),
        config_path=_sparql_escape_string(path),
        config_as_string=_sparql_escape_string(config),
    )
    _sparql(query, update=True, sudo=True)


def fetch_config() -> dict[str, Any]:
    """Collect the compose service and mounted configuration file contents."""

    try:
        with open(COMPOSE_FILE, encoding="utf-8") as compose_file:
            raw_compose = yaml.safe_load(compose_file)
            compose = raw_compose["services"][COMPOSE_SERVICE]
    except Exception as error:
        raise ValueError(
            f"Could not find valid compose file at {COMPOSE_FILE} with service "
            f"{COMPOSE_SERVICE}. A compose file with a service is necessary "
            "to find the image version and configuration of the ai system used"
        ) from error

    mounts: dict[str, str] = {}
    for volume in compose.get("volumes", []):
        mount_point = volume.split(":")[1]
        if mount_point == COMPOSE_FILE or re.search(IGNORE_MOUNT_REGEX, mount_point):
            continue
        if Path(mount_point).is_file():
            track_file_content(mounts, mount_point)
        else:
            for root, _, files in os.walk(mount_point):
                for file in files:
                    track_file_content(mounts, file, root)
    return {"compose": compose, "config": mounts}


def track_file_content(mounts: dict[str, str], file: str, root: str = "/") -> None:
    file_path = Path(root) / file
    mounts[str(file_path)] = file_path.read_text(encoding="utf-8")


def write_agent_info(service_base: str) -> None:
    """Register or reuse the config and single service agent."""

    global _agent_uri
    agent_config_uri = _ensure_config_uri()
    query = Template(
        _get_prefixes("foaf", "ext", "schema", "tcs", "prov")
        + """
        SELECT ?agent_uri {
            GRAPH $graph {
                ?agent_uri a tcs:InstancePipelineComponent, foaf:Agent ;
                    prov:specializationOf $service_base ;
                    ext:hasConfig $agent_config_uri .
            }
        } LIMIT 1
        """
    ).substitute(
        graph=_sparql_escape_uri(JOB_GRAPH),
        agent_config_uri=_sparql_escape_uri(agent_config_uri),
        service_base=_sparql_escape_uri(service_base),
    )
    result = _sparql(query, sudo=True)
    bindings = result.get("results", {}).get("bindings", [])
    if bindings:
        _agent_uri = bindings[0]["agent_uri"]["value"]
        return

    agent_id = str(uuid.uuid4())
    agent_uri = BASE_AGENT_URI + agent_id
    query = Template(
        _get_prefixes("foaf", "ext", "schema", "tcs", "prov", "mu")
        + """
        INSERT DATA {
            GRAPH $graph {
                $agent_uri a tcs:InstancePipelineComponent, foaf:Agent ;
                    mu:uuid $agent_id ;
                    prov:specializationOf $base_agent_uri ;
                    ext:hasConfig $agent_config_uri .
            }
        }
        """
    ).substitute(
        graph=_sparql_escape_uri(JOB_GRAPH),
        agent_uri=_sparql_escape_uri(agent_uri),
        agent_id=_sparql_escape_string(agent_id),
        base_agent_uri=_sparql_escape_uri(service_base),
        agent_config_uri=_sparql_escape_uri(agent_config_uri),
    )
    _sparql(query, update=True, sudo=True)
    _agent_uri = agent_uri


def model_name_to_uri(model_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", model_name).strip("-").lower()
    return f"http://data.lblod.info/ontology/airo#{safe}"