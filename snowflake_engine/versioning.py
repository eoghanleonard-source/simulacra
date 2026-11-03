"""Concrete v2 lineage. No migration or historical-installation mutation.

A component logical key uses client/source/kind/model-local ID. It is stable only
while that local ID keeps its meaning. Derived identities commit to membership;
regrouping creates a new logical object rather than claiming semantic identity.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

VERSION_KEYS = frozenset({"storage", "input_mapping", "extraction_contract", "framework", "ontology",
                          "prompt", "model", "model_settings", "embedding_model", "code", "package",
                          "aggregation", "selection", "export"})
LINEAGE_KEYS = frozenset({"logical_id", "revision_id", "version_set"})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def normalize_versions(value):
    """Snowflake VARIANT may round-trip 1.0 as 1; normalise version settings only."""
    if isinstance(value, dict):
        return {key: normalize_versions(child) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_versions(child) for child in value]
    if type(value) is float and value.is_integer():
        return int(value)
    return value


def _version_containers(value):
    if isinstance(value, dict):
        return {key: normalize_versions(child) if key in ("version_set", "versions", "VERSION_SET", "CONFIG")
                else deepcopy(child) if key in ("metadata", "events", "payload", "record", "raw_record", "raw_source", "source")
                else _version_containers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_version_containers(child) for child in value]
    return value


def validate_version_set(versions):
    if not isinstance(versions, dict):
        return ["version_set must be an object"]
    errors = ["version_set missing " + key for key in sorted(VERSION_KEYS - set(versions))]
    errors.extend("version_set empty " + key for key, value in versions.items()
                  if not isinstance(value, (str, dict)) or not value)
    return errors


def revision_id(kind, logical_id, row, versions):
    """Recompute a revision after removing only the three top-level lineage fields."""
    return "revision-" + digest({"kind": kind, "logical_id": logical_id,
                                 "payload": _version_containers({k: v for k, v in row.items() if k not in LINEAGE_KEYS}),
                                 "version_set": normalize_versions(versions)})


def annotate(row, kind, versions, logical_key):
    errors = validate_version_set(versions)
    if errors:
        raise ValueError("; ".join(errors))
    result = deepcopy(row)
    result["logical_id"] = "logical-" + digest({"kind": kind, "key": logical_key})
    result["version_set"] = normalize_versions(deepcopy(versions))
    result["revision_id"] = revision_id(kind, result["logical_id"], result, versions)
    return result


def build_version_set(config, *, code_digest):
    from . import __version__, aggregation, contracts, schema
    split = config.get("extraction_mode", "legacy") in ("split_v3", "evidence_v1")
    contract = getattr(contracts, "CAPTURE_V3_VERSIONS", {}) if split else contracts.VERSIONS
    result = {
        "storage": schema.SCHEMA_VERSION,
        "input_mapping": digest(config["mapping"]) if isinstance(config.get("mapping"), dict) else "unresolved-until-input-read",
        "extraction_contract": contract.get("schema", "engine-extraction-schema-v3" if split else contracts.VERSIONS["schema"]),
        "framework": digest(contracts.FRAMEWORK_SOURCES), "ontology": contract.get("codebook", contracts.VERSIONS["codebook"]),
        "prompt": contract.get("prompt", "engine-extraction-prompt-v3" if split else contracts.VERSIONS["prompt"]),
        "model": config.get("model", "not-used-load-only"),
        "model_settings": {"temperature": 0, "max_output_tokens": config["limits"]["max_output_tokens"],
                           "max_input_chars": config["limits"]["max_input_chars"], "extraction_mode": config.get("extraction_mode", "legacy"),
                           "compact_source_ids": sorted(config.get("compact_source_ids", []))},
        "embedding_model": config.get("embedding_model", "not-used-load-only"),
        "code": code_digest, "package": config.get("package_sha256", __version__ + ":" + code_digest),
        "aggregation": {"version": aggregation.AGGREGATION_VERSION, "config": deepcopy(config.get("aggregation", {}))},
        "selection": {"version": ("engine-practice-objectives-v2" if config.get("practice", {}).get("strategy") == "objectives_v2"
                                   else "engine-practice-v1" if "practice" in config else "source-coverage-v1"),
                      "config": deepcopy(config.get("practice", {}))},
        "export": "engine-export-v3" if schema.SCHEMA_VERSION in {"engine-storage-v3", "engine-storage-v4"} else "engine-export-v2",
    }
    if config.get("extraction_mode") == "evidence_v1":
        from . import evidence_assertions
        result["prompt"] = {"scenario": evidence_assertions.VERSION,
                            "persona": contracts.CAPTURE_V3_VERSIONS["prompt"]}
        result["model_settings"].update(evidence_binding=evidence_assertions.CONVERSION_VERSION,
            input_measurement="message_content_chars_with_conservative_utf8_v1")
    # Ordinary runs retain their existing version-set shape and generic export.
    # Capture adds its own pins only through its explicit opt-in configuration.
    if config.get("operation") == "capture_evidence" or "capture" in config:
        from . import capture_prompts
        capture = config.get("capture", {})
        result["capture_contract"] = "capture-evidence-v1"
        result["capture_export"] = "capture-export-v1"
        result["capture_profile"] = capture.get("profile_version", "worker-reviewer-v9-dev-20261101")
        result["capture_prompt"] = digest({key: value for key, value in vars(capture_prompts).items()
                                            if key.isupper() and isinstance(value, str)})
        result["capture_rubric"] = capture_prompts.RUBRIC_VERSION
        result["capture_execution"] = {"mode": capture.get("execution_mode", "live"), "config": deepcopy(capture)}
    if config.get("evidence_ordering") is not None:
        from .reprocessing import EVIDENCE_ORDERING_VERSION
        if config["evidence_ordering"] != EVIDENCE_ORDERING_VERSION:
            raise ValueError("Unknown evidence_ordering processing version")
        result["processing"] = EVIDENCE_ORDERING_VERSION
    if config.get("source_revision") is not None:
        if config["source_revision"] != "source_assertion_revision_v1" or config.get("evidence_ordering") != "source_ordinal_v1":
            raise ValueError("Unknown source_revision processing version")
        result["processing"] = {"evidence_ordering": "source_ordinal_v1",
                                "semantic_revision": "source_assertion_revision_v1"}
        if (config.get("input_measurement") is None) != (config.get("response_normalization") is None):
            raise ValueError("R3 processing requires input_measurement and response_normalization together")
        for config_key, version_key, known in (("response_normalization", "review_response_format", "unique_review_json_v1"),
                                               ("input_measurement", "input_measurement", "message_content_chars_v1")):
            if config.get(config_key) is not None:
                if config[config_key] != known:
                    raise ValueError("Unknown " + config_key + " processing version")
                result["processing"][version_key] = known
    return result
