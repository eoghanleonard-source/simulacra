"""Snowflake stored-procedure orchestration, durable allowances and immutable drafts.

All provider calls and processing occur in the supplied Snowpark session. There is
no local provider, document-store, retry, or generated-output fallback.
"""
from __future__ import annotations

import hashlib
import json
import math
import inspect
import sys
import time
import uuid
from datetime import datetime, timezone
from copy import deepcopy

from . import contracts, schema, versioning

NATIVE_VERSION = "engine-native-v2"
CAPTURE_PROFILE = "worker-reviewer-v9-dev-20261101"
CAPTURE_MODEL = "claude-sonnet-4-6"
CAPTURE_STAGES = frozenset({"worker", "review", "repair", "repair_verify", "reconcile", "reconcile_verify"})
CAPTURE_OPERATIONS = frozenset("capture_evidence_" + stage for stage in CAPTURE_STAGES)
RECORDED_CAPTURE_OPERATIONS = frozenset("capture_recorded_" + stage for stage in CAPTURE_STAGES)
RECORDED_RUN_FORMAT = "recorded-run-responses-v1"
RECORDED_RUN_OPERATIONS = frozenset({"extraction", "extraction_v3_scenario", "extraction_v3_persona", "evidence_v1_scenario"})
RECORDED_RUN_LEDGER_OPERATIONS = frozenset("run_recorded_" + operation for operation in RECORDED_RUN_OPERATIONS)
REVIEW_PLANNER_OPERATIONS = frozenset({"source_review_v1", "practice_objective_map_v2", "practice_objective_select_v2"})
# AI_EMBED requires its model argument to be a SQL literal. Only these supported
# text routes can become SQL text; evidence remains a bound value.
EMBEDDING_MODELS = frozenset({"snowflake-arctic-embed-m-v1.5", "snowflake-arctic-embed-l-v2.0"})
# Absolute implementation guardrails are distinct from the reviewed installation
# allowance. The latter is persisted at install and cannot be raised by a run.
HARD_LIMITS = {"max_sources": 100000, "max_events": 2000000, "max_generation_calls": 100000,
               "max_output_tokens": 16384, "max_embedding_inputs": 1000000,
               "concurrency": 1, "max_sql_statements": 1000000,
               "statement_timeout_seconds": 3600, "max_input_chars": 20000000, "max_run_seconds": 86400}
DEFAULT_LIMITS = {"max_sources": 6, "max_events": 60, "max_generation_calls": 12,
                  "max_output_tokens": 4096, "max_embedding_inputs": 128,
                  "concurrency": 1, "max_sql_statements": 500,
                  "statement_timeout_seconds": 120, "max_input_chars": 60000, "max_run_seconds": 900}


class ControlError(RuntimeError):
    """A prospective safety control refused further work."""


def _json(value):
    return contracts.canonical_json(value)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _rows(rows):
    return [{str(k).upper(): v for k, v in (r.as_dict() if hasattr(r, "as_dict") else r).items()}
            for r in rows]


def _now():
    return datetime.now(timezone.utc).isoformat()


def validate_processing_modes(config):
    """Keep ordering cache-only; allow paid review only in the explicit revision route."""
    revision = config.get("source_revision")
    for field, known in (("input_measurement", "message_content_chars_v1"),
                         ("response_normalization", "unique_review_json_v1")):
        if config.get(field) is not None and (config[field] != known or revision != "source_assertion_revision_v1"):
            raise ValueError(field + " requires source_assertion_revision_v1 and its known processing version")
    if (config.get("input_measurement") is None) != (config.get("response_normalization") is None):
        raise ValueError("input_measurement and response_normalization require the complete known R3 processing pair")
    from .reprocessing import validate_evidence_ordering
    if revision is None:
        validate_evidence_ordering(config)
    else:
        if revision != "source_assertion_revision_v1":
            raise ValueError("source_revision must be source_assertion_revision_v1 or omitted")
        if (config.get("operation", "run") != "run" or config.get("extraction_mode") != "split_v3"
                or config.get("evidence_ordering") != "source_ordinal_v1"
                or config.get("reprocess_retained") is not True
                or not isinstance(config.get("retained_capture_manifest"), dict)
                or type(config.get("enable_model_calls", False)) is not bool):
            raise ValueError("source_revision requires an explicit split_v3 retained capture import with source_ordinal_v1")
        # The frozen ordering-only validator still runs under its original conditions.
        validate_evidence_ordering(dict(config, enable_model_calls=False))
        if config.get("limits", {}).get("max_input_chars", DEFAULT_LIMITS["max_input_chars"]) != 60000:
            raise ValueError("source_revision requires unchanged max_input_chars=60000")
    allowed = config.get("allowed_generation_operations")
    if allowed is not None:
        if (not isinstance(allowed, list) or not allowed
                or any(not isinstance(operation, str) or not operation.strip() for operation in allowed)
                or len(set(allowed)) != len(allowed)
                or set(allowed) - (CAPTURE_OPERATIONS if config.get("operation") == "capture_evidence" else REVIEW_PLANNER_OPERATIONS)):
            raise ValueError("allowed_generation_operations requires unique explicit review/planner operations; extraction is prohibited")
        if revision is not None and "source_review_v1" not in allowed:
            raise ValueError("source_revision allowed_generation_operations must include source_review_v1")


def validate_retained_capture_manifest(config, sources=None, current_versions=None):
    """Verify an explicitly supplied frozen capture import, never a paid ledger.

    The canonical manifest digest binds all original metadata and provenance.
    With canonical sources available, validate the entire batch before any model
    callback or component processing. Old storage lineage may differ deliberately;
    source, mapping, model, prompt and generation settings must match exactly.
    """
    manifest = config.get("retained_capture_manifest")
    binding = config.get("retained_capture_manifest_sha256")
    if manifest is None:
        if binding is not None or config.get("retained_capture_manifest_file_sha256") is not None:
            raise ValueError("retained_capture_manifest digest requires its manifest")
        return None
    if (config.get("operation", "run") != "run" or config.get("extraction_mode") != "split_v3"
            or config.get("evidence_ordering") != "source_ordinal_v1"
            or config.get("reprocess_retained") is not True
            or (config.get("enable_model_calls", False) is not False
                and config.get("source_revision") != "source_assertion_revision_v1")):
        raise ValueError("retained_capture_manifest requires a cache-only split_v3 source_ordinal_v1 run")
    if not isinstance(manifest, dict) or manifest.get("format") != "retained-capture-v1":
        raise ValueError("retained_capture_manifest requires format retained-capture-v1")
    if binding != _digest(manifest):
        raise ValueError("retained_capture_manifest canonical SHA-256 mismatch")

    def sha(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    file_hash = config.get("retained_capture_manifest_file_sha256")
    if file_hash is not None and not sha(file_hash):
        raise ValueError("retained_capture_manifest_file_sha256 must be SHA-256")
    provenance = manifest.get("provenance")
    if (not isinstance(provenance, list) or not provenance
            or any(not isinstance(row, dict) or not isinstance(row.get("path"), str)
                   or not row["path"].strip() or not sha(row.get("sha256")) for row in provenance)):
        raise ValueError("retained_capture_manifest requires frozen artifact provenance paths and SHA-256 hashes")
    entries = manifest.get("sources")
    if not isinstance(entries, list) or not entries:
        raise ValueError("retained_capture_manifest requires a complete nonempty sources list")
    selected, attempt_ids = {}, set()
    operations = {"extraction_v3_scenario", "extraction_v3_persona"}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("source_id"), str)
                or not entry["source_id"].strip() or not sha(entry.get("source_hash"))):
            raise ValueError("retained_capture_manifest source requires source_id and source_hash")
        sid = entry["source_id"]
        if sid in selected:
            raise ValueError("retained_capture_manifest duplicate source_id")
        attempts = entry.get("attempts")
        if (not isinstance(attempts, list) or len(attempts) != 2
                or any(not isinstance(attempt, dict) or not isinstance(attempt.get("operation"), str) for attempt in attempts)
                or {attempt.get("operation") for attempt in attempts} != operations):
            raise ValueError("retained_capture_manifest requires exactly two independent generation attempts per source")
        for attempt in attempts:
            if (not isinstance(attempt.get("attempt_id"), str) or not attempt["attempt_id"].strip()
                    or attempt["attempt_id"] in attempt_ids
                    or not isinstance(attempt.get("raw_response"), str)
                    or attempt.get("model") != config.get("model")
                    or type(attempt.get("max_output_tokens")) is not int
                    or attempt["max_output_tokens"] != 6144
                    or not sha(attempt.get("request_fingerprint"))
                    or not isinstance(attempt.get("supplied_request_key"), str)):
                raise ValueError("retained_capture_manifest invalid or mismatched attempt identity/model/output settings")
            old = attempt.get("versions")
            if versioning.validate_version_set(old):
                raise ValueError("retained_capture_manifest attempt requires complete original versions")
            if old["model"] != attempt["model"]:
                raise ValueError("retained_capture_manifest original version model mismatch")
            attempt_ids.add(attempt["attempt_id"])
        selected[sid] = entry
    if "source_count" in manifest and (type(manifest["source_count"]) is not int or manifest["source_count"] != len(entries)):
        raise ValueError("retained_capture_manifest source_count mismatch")
    if sources is None:
        return selected
    if len(sources) != len(selected) or {source["source_id"] for source in sources} != set(selected):
        raise ValueError("retained_capture_manifest must match the complete canonical source set")
    from . import extraction
    generation_fields = ("input_mapping", "extraction_contract", "framework", "ontology", "prompt", "model", "model_settings")
    if current_versions is None:
        raise ValueError("retained_capture_manifest validation requires current generation versions")
    for source in sources:
        entry = selected[source["source_id"]]
        source_digest = contracts.source_hash(source)
        if entry["source_hash"] != source_digest:
            raise ValueError("retained_capture_manifest source_hash mismatch for " + source["source_id"])
        for attempt in entry["attempts"]:
            old = attempt["versions"]
            if any(versioning.normalize_versions(old[field]) != versioning.normalize_versions(current_versions[field])
                   for field in generation_fields):
                raise ValueError("retained_capture_manifest original mapping/model/prompt/settings mismatch")
            kind = attempt["operation"].removeprefix("extraction_v3_")
            messages = extraction.build_messages_v3(source, kind, config["limits"]["max_input_chars"], "compact")
            supplied = "extraction-v3-" + _digest({"source_hash": source_digest,
                "versions": contracts.CAPTURE_V3_VERSIONS, "kind": kind, "output_style": "compact",
                "max_output_tokens": extraction.MAX_OUTPUT_TOKENS_V3})
            fingerprint = _digest({"operation": attempt["operation"], "key": supplied, "messages": messages,
                "model": attempt["model"], "max_output_tokens": attempt["max_output_tokens"],
                "request_versions": {key: value for key, value in old.items()
                                     if key not in ("code", "package", "code_digest", "processing")}})
            if supplied != attempt["supplied_request_key"] or fingerprint != attempt["request_fingerprint"]:
                raise ValueError("retained_capture_manifest original request key/fingerprint mismatch")
    return selected


def validate_source_relation(config, relation):
    """Allow one explicit relation in the reviewed installation namespace."""
    if not isinstance(relation, str) or len(relation.split(".")) != 3:
        raise ValueError("source_relation must be a three-part unquoted database.schema.relation name")
    parts = [schema.validate_identifier(part) for part in relation.split(".")]
    namespace = [schema.validate_identifier(config["database"]), schema.validate_identifier(config.get("source_schema", config["schema"]))]
    if parts[:2] != namespace:
        raise ValueError("source_relation must use the configured database and explicit source_schema (or engine schema by default)")
    return ".".join(parts)


def recorded_run_install_config(config):
    """Add an ordinary-run fixture capability to the existing immutable metadata."""
    allowed = config.get("allow_recorded_run", False)
    if type(allowed) is not bool:
        raise ValueError("allow_recorded_run must be an installation boolean")
    mode = config.get("permitted_run_execution_mode")
    if not allowed:
        if mode is not None:
            raise ValueError("permitted_run_execution_mode requires allow_recorded_run=true")
        return {}
    if mode != "recorded_smoke":
        raise ValueError("allow_recorded_run requires explicit permitted_run_execution_mode=recorded_smoke")
    return {"allow_recorded_run": True, "permitted_run_execution_mode": mode,
            "recorded_run_contract": RECORDED_RUN_FORMAT}


def validate_recorded_run_config(config):
    """Predetermined fixture transport only; this is not unattended inference."""
    mode = config.get("execution_mode", "live")
    fixtures = config.get("recorded_run_responses")
    if config.get("operation", "run") != "run":
        if fixtures is not None:
            raise ValueError("recorded_run_responses requires ordinary run")
        return
    if mode not in ("live", "recorded_smoke"):
        raise ValueError("Unknown ordinary run execution_mode")
    if mode != "recorded_smoke":
        if fixtures is not None:
            raise ValueError("Live ordinary run cannot consume recorded_run_responses")
        return
    if config.get("enable_model_calls", False) is not False:
        raise ValueError("Recorded ordinary run requires enable_model_calls=false")
    aggregation = config.get("aggregation", {})
    if (not isinstance(aggregation, dict) or type(aggregation.get("max_candidate_pairs")) is not int
            or aggregation["max_candidate_pairs"] != 0):
        raise ValueError("Recorded ordinary run requires aggregation.max_candidate_pairs=0; embeddings are prohibited")
    if config.get("retained_capture_manifest") is not None or config.get("source_revision") is not None:
        raise ValueError("Recorded ordinary run uses extraction fixtures, not retained semantic revision")
    if "practice" in config and config.get("defer_practice") is not True:
        raise ValueError("Recorded ordinary run requires practice omitted or defer_practice=true")
    if not isinstance(fixtures, dict) or set(fixtures) != {"format", "sources"} or fixtures.get("format") != RECORDED_RUN_FORMAT:
        raise ValueError("recorded_run_responses requires format " + RECORDED_RUN_FORMAT + " and sources")
    entries = fixtures["sources"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("recorded_run_responses requires nonempty sources")
    expected = ({"evidence_v1_scenario", "extraction_v3_persona"} if config.get("extraction_mode") == "evidence_v1"
                else {"extraction_v3_scenario", "extraction_v3_persona"} if config.get("extraction_mode") == "split_v3" else {"extraction"})
    seen = set()
    cap = config.get("limits", {}).get("max_output_tokens", DEFAULT_LIMITS["max_output_tokens"])
    count = 0
    def sha(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"source_id", "source_hash", "operations"}
                or not isinstance(entry.get("source_id"), str) or not entry["source_id"].strip()
                or entry["source_id"] in seen or not sha(entry.get("source_hash"))):
            raise ValueError("Recorded run source requires unique source_id, exact source_hash and operations")
        seen.add(entry["source_id"])
        operations = entry["operations"]
        if not isinstance(operations, dict) or set(operations) != expected:
            raise ValueError("Recorded run requires the exact known extraction operations for its contract")
        for values in operations.values():
            # Existing ordinary extraction contracts make exactly one call per operation.
            # Lists preserve the ordinal explicitly without permitting hidden retries.
            if not isinstance(values, list) or len(values) != 1:
                raise ValueError("Recorded run requires exactly one ordered response per extraction operation")
            for value in values:
                raw = value
                if isinstance(value, dict):
                    if (set(value) - {"raw_response", "request_messages_sha256"} or "raw_response" not in value
                            or ("request_messages_sha256" in value and not sha(value["request_messages_sha256"]))):
                        raise ValueError("Recorded run response requires raw_response and optional exact request_messages_sha256")
                    raw = value["raw_response"]
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > cap * 4:
                    raise ValueError("Recorded run response exceeds bounded raw-string output contract")
                count += 1
    if len(entries) > config.get("limits", {}).get("max_sources", DEFAULT_LIMITS["max_sources"]):
        raise ValueError("Recorded run fixture sources exceed source allowance")
    if count > config.get("limits", {}).get("max_generation_calls", DEFAULT_LIMITS["max_generation_calls"]):
        raise ValueError("Recorded run fixture count exceeds logical attempt allowance")


def capture_install_config(config):
    """Immutable installation capability, separate from the numeric allowance."""
    ordinary = recorded_run_install_config(config)
    if type(config.get("capture_enabled", False)) is not bool:
        raise ValueError("capture_enabled must be an installation boolean")
    if not config.get("capture_enabled", False):
        if config.get("allow_recorded_capture", False):
            raise ValueError("allow_recorded_capture requires capture_enabled")
        return ordinary
    mode = config.get("permitted_execution_mode")
    if mode not in ("live", "recorded_smoke"):
        raise ValueError("Capture installation requires explicit permitted_execution_mode")
    if config.get("capture_profile", CAPTURE_PROFILE) != CAPTURE_PROFILE or config.get("model") != CAPTURE_MODEL:
        raise ValueError("Capture installation requires the frozen profile/model")
    if type(config.get("allow_recorded_capture", False)) is not bool:
        raise ValueError("allow_recorded_capture must be an installation boolean")
    if mode == "recorded_smoke" and config.get("allow_recorded_capture") is not True:
        raise ValueError("Recorded installation requires allow_recorded_capture=true")
    return {"capture_enabled": True, "capture_profile": CAPTURE_PROFILE,
            "permitted_execution_mode": mode, "allow_recorded_capture": config.get("allow_recorded_capture", False), **ordinary}


def validate_capture_config(config):
    """Validate the explicit capture route without changing generic defaults."""
    if "reuse_capture_run_id" in config:
        prior = config["reuse_capture_run_id"]
        if (not isinstance(prior, str) or not prior.strip() or prior == config.get("run_id")
                or config.get("operation") != "capture_evidence"
                or config.get("execution_mode", "live") != "live"
                or not isinstance(config.get("capture_settings"), dict)
                or config["capture_settings"].get("response_framing") != "single-fence-v2"):
            raise ValueError("reuse_capture_run_id requires a different prior run and explicit live capture single-fence-v2 framing")
    if "allow_recorded_capture" in config and type(config["allow_recorded_capture"]) is not bool:
        raise ValueError("allow_recorded_capture must be an installation boolean")
    if config.get("operation") not in ("capture_evidence", "capture_export"):
        if (config.get("execution_mode") == "recorded_smoke" and config.get("operation", "run") != "run") or "recorded_responses" in config:
            raise ValueError("Recorded responses require capture_evidence")
        return
    if config.get("operation") == "capture_export":
        if not isinstance(config.get("capture_run_id"), str) or not config["capture_run_id"].strip():
            raise ValueError("capture_export requires capture_run_id")
        return
    if config.get("model") != CAPTURE_MODEL:
        raise ValueError("capture_evidence model is pinned to " + CAPTURE_MODEL)
    config.setdefault("capture_profile", CAPTURE_PROFILE)
    if config["capture_profile"] != CAPTURE_PROFILE:
        raise ValueError("capture_evidence requires the frozen accepted profile")
    mode = config.setdefault("execution_mode", "live")
    if mode not in ("live", "recorded_smoke"):
        raise ValueError("Unknown capture execution_mode")
    if not isinstance(config.get("capture_settings", {}), dict):
        raise ValueError("capture_settings must be an object")
    if mode == "recorded_smoke":
        if config.get("enable_model_calls", False) is not False:
            raise ValueError("recorded_smoke requires enable_model_calls=false")
        responses = config.get("recorded_responses")
        if not isinstance(responses, dict) or not responses:
            raise ValueError("recorded_smoke requires explicit recorded_responses")
        response_count = 0
        for source_id, stages in responses.items():
            if not isinstance(source_id, str) or not source_id or not isinstance(stages, dict):
                raise ValueError("Recorded responses require source IDs and stage maps")
            for stage, values in stages.items():
                if stage not in CAPTURE_STAGES or not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
                    raise ValueError("Recorded responses require known stages and ordered raw-string lists")
                cap = 8192 if stage == "worker" else 16384
                if any(len(value.encode("utf-8")) > cap * 4 for value in values):
                    raise ValueError("Recorded response exceeds bounded stage output bytes")
                response_count += len(values)
        if response_count > config.get("limits", {}).get("max_generation_calls", DEFAULT_LIMITS["max_generation_calls"]):
            raise ValueError("Recorded response count exceeds the logical stage allowance")
    elif "recorded_responses" in config:
        raise ValueError("Live capture cannot consume recorded responses")


def validate_config(value):
    config = _decode(value)
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    config = dict(config)
    for field in ("database", "schema", "client_id"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError("Configuration requires " + field)
    schema.qualified_name(config, "RUNS")
    if "package_sha256" in config and (not isinstance(config["package_sha256"], str) or len(config["package_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in config["package_sha256"])):
        raise ValueError("package_sha256 must be the installed archive SHA-256")
    config.setdefault("operation", "run")
    if config["operation"] not in ("load", "run", "capture_evidence", "capture_export"):
        raise ValueError("Unsupported native operation")
    validate_capture_config(config)
    if config["operation"] == "run":
        for field in ("model", "embedding_model"):
            if not isinstance(config.get(field), str) or not config[field].strip():
                raise ValueError("Configuration requires an explicitly available " + field)
        if config["embedding_model"] not in EMBEDDING_MODELS:
            raise ValueError("embedding_model must name a supported AI_EMBED text model: " + ", ".join(sorted(EMBEDDING_MODELS)))
    config.setdefault("enable_model_calls", False)
    if type(config["enable_model_calls"]) is not bool:
        raise ValueError("enable_model_calls must be an explicit boolean")
    config.setdefault("defer_practice", False)
    if type(config["defer_practice"]) is not bool:
        raise ValueError("defer_practice must be an explicit boolean")
    config.setdefault("persist_request_messages", False)
    if type(config["persist_request_messages"]) is not bool:
        raise ValueError("persist_request_messages must be an explicit boolean")
    compact_ids = config.get("compact_source_ids", [])
    if (not isinstance(compact_ids, list)
            or any(not isinstance(source_id, str) or not source_id.strip() for source_id in compact_ids)
            or len(set(compact_ids)) != len(compact_ids)):
        raise ValueError("compact_source_ids must be a list of unique nonempty source IDs")
    config["compact_source_ids"] = list(compact_ids)
    config.setdefault("extraction_mode", "legacy")
    if config["extraction_mode"] not in ("legacy", "split_v3", "evidence_v1"):
        raise ValueError("extraction_mode must be legacy, split_v3 or evidence_v1")
    if config["extraction_mode"] == "evidence_v1" and config.get("model") != "claude-sonnet-4-6":
        raise ValueError("evidence_v1 baseline pins claude-sonnet-4-6")
    retained_evidence = config.get("evidence_response_cache")
    if retained_evidence is not None:
        if config["extraction_mode"] != "evidence_v1" or not isinstance(retained_evidence, list):
            raise ValueError("evidence_response_cache requires explicit evidence_v1 and a list")
        keys = [(entry.get("source_id"), entry.get("operation")) for entry in retained_evidence if isinstance(entry, dict)]
        if len(keys) != len(retained_evidence) or len(set(keys)) != len(keys):
            raise ValueError("evidence_response_cache entries must be objects with unique source/operation")
        if any(operation not in ("evidence_v1_scenario", "extraction_v3_persona") for _, operation in keys):
            raise ValueError("evidence_response_cache has an unknown extraction operation")
    if config.get("source_revision") is not None:
        config.setdefault("allowed_generation_operations", ["source_review_v1"])
    validate_processing_modes(config)
    if "source_schema" in config:
        config["source_schema"] = schema.validate_identifier(config["source_schema"])
    if "input_run_id" in config and (not isinstance(config["input_run_id"], str) or not config["input_run_id"].strip()):
        raise ValueError("input_run_id must be a nonempty historical load/run ID")
    if "practice" in config and not isinstance(config["practice"], dict):
        raise ValueError("practice must be a configuration object")
    supplied = config.get("limits", {})
    if not isinstance(supplied, dict) or set(supplied) - set(HARD_LIMITS) - {"max_generation_ai_credits"}:
        raise ValueError("Unknown limit; use documented limits keys")
    limits = dict(DEFAULT_LIMITS, **supplied)
    for key, upper in HARD_LIMITS.items():
        if type(limits[key]) is not int or not 1 <= limits[key] <= upper:
            raise ValueError(f"{key} must be an integer between 1 and {upper}")
    if "max_generation_ai_credits" in limits:
        ceiling = limits["max_generation_ai_credits"]
        if type(ceiling) not in (int, float) or not math.isfinite(ceiling) or not 0 < ceiling <= 16.0:
            raise ValueError("max_generation_ai_credits must be a positive number at most 16.0")
    if limits["concurrency"] != 1 or limits["max_sql_statements"] < 40:
        raise ValueError("Concurrency must be one and SQL allowance at least forty")
    config["limits"] = limits
    if config["extraction_mode"] in ("split_v3", "evidence_v1") and limits["max_output_tokens"] != 6144:
        raise ValueError("split_v3/evidence_v1 contract requires exact max_output_tokens=6144")
    validate_retained_capture_manifest(config)
    validate_recorded_run_config(config)
    if config["operation"] == "capture_evidence" and limits["max_output_tokens"] != 16384:
        raise ValueError("capture_evidence requires installed max_output_tokens=16384 for the frozen stage caps")
    if "source_relation" in config:
        if config["operation"] != "load" or "raw_records" in config:
            raise ValueError("source_relation requires load without raw_records")
        config["source_relation"] = validate_source_relation(config, config["source_relation"])
        source_ids = config.get("source_ids", [])
        if (not isinstance(source_ids, list)
                or any(not isinstance(sid, str) or not sid.strip() for sid in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("source_ids must be a list of unique nonempty source IDs")
        if len(source_ids) > limits["max_sources"]:
            raise ValueError("Selected source IDs exceed the configured source allowance")
        config["source_ids"] = sorted(source_ids)
    elif "source_ids" in config:
        raise ValueError("source_ids requires source_relation")
    config.setdefault("run_id", str(uuid.uuid4()))
    if not isinstance(config["run_id"], str) or not config["run_id"].strip():
        raise ValueError("run_id must be a nonempty string")
    return config


class BoundedSession:
    """Count every statement, including schema helpers; reserve room for closeout."""
    def __init__(self, session, maximum, closeout_reserve=12, max_run_seconds=900):
        self.session, self.maximum, self.reserve = session, maximum, closeout_reserve
        self.statements = 0
        self.closing = False
        self.deadline = time.monotonic() + max_run_seconds

    def sql(self, statement, params=None, **kwargs):
        if not self.closing and time.monotonic() >= self.deadline:
            raise ControlError("Run wall-clock allowance exhausted before next SQL statement")
        limit = self.maximum if self.closing else self.maximum - self.reserve
        if self.statements >= limit:
            raise ControlError("SQL statement allowance exhausted before execution")
        self.statements += 1
        return self.session.sql(statement, params=params, **kwargs)


class Runtime:
    def __init__(self, session, config):
        self.config = config
        self.session = BoundedSession(session, config["limits"]["max_sql_statements"], max_run_seconds=config["limits"]["max_run_seconds"])
        self.locked = False
        self._in_transaction = False
        self.scope_owned = False
        self.installed_budget = None
        self.attempts = []
        self.cache_hits = 0
        self.query_ids = []
        self.processing_events = []
        self.capture_reuse_run = None
        self.imported_capture_responses = 0
        self.source_revision_stop = None
        self.extraction_styles = []
        self.last_provider_query_id = None
        # Hash packaged source, not a caller-provided version label. zipimport loaders
        # expose source through inspect inside Snowflake; inability fails closed.
        from . import aggregation, extraction, mapping
        from . import export, practice, reprocessing, source_revision, practice_objectives
        from . import capture_evidence, capture_contract, capture_prompts, capture_storage
        modules = [sys.modules[__name__], contracts, extraction, aggregation, mapping, schema, versioning, export, practice, reprocessing, source_revision, practice_objectives, capture_evidence, capture_contract, capture_prompts, capture_storage]
        if config.get("extraction_mode") == "evidence_v1":
            from . import evidence_assertions
            modules.append(evidence_assertions)
        if config.get("capture_settings", {}).get("response_framing") == "single-fence-v2":
            from . import capture_framing
            modules.append(capture_framing)
        code_digest = _digest({m.__name__: inspect.getsource(m) for m in modules})
        self.versions = versioning.build_version_set(dict(config, capture={"profile_version": CAPTURE_PROFILE, "execution_mode": config["execution_mode"]}) if config["operation"] == "capture_evidence" else config, code_digest=code_digest)
        if config["operation"] == "capture_evidence":
            self.versions.update(extraction_contract="capture-evidence-v1", prompt=CAPTURE_PROFILE,
                                 export="capture-export-v1", capture_profile=CAPTURE_PROFILE)
            self.versions["model_settings"].update(execution_mode=config["execution_mode"],
                worker_output_tokens=8192, nonworker_output_tokens=16384, maximum_wire_bytes=90000)
            if config.get("capture_settings", {}).get("response_framing") == "single-fence-v2":
                self.versions["processing"] = {"capture_response_framing": "single-fence-v2"}
        if config.get("operation") == "run" and config.get("execution_mode") == "recorded_smoke":
            self.versions["model_settings"].update(execution_mode="recorded_smoke", recorded_run_contract=RECORDED_RUN_FORMAT)
        self.recorded_run_allowed = False
        self.recorded_run_source = None
        self.recorded_run_ordinals = {}
        self.recorded_capture_allowed = False
        self.issues = []
        self.started_at = _now()
        self.relation_source_ids = None

    def sql(self, statement, params=None):
        return self.session.sql(statement, params=params).collect()

    def table(self, name):
        return schema.qualified_name(self.config, name)

    def lineage_row(self, table, row):
        row = dict(row)
        if "LOGICAL_ID" not in schema.TABLE_SPECS[table] or table == "RECORD_REVISIONS":
            return row
        for key in ("LOGICAL_ID", "REVISION_ID", "VERSION_SET"):
            row.pop(key, None)
        payload = row.get("PAYLOAD")
        if isinstance(payload, dict) and all(key in payload for key in versioning.LINEAGE_KEYS):
            annotated = payload
        else:
            identity = [(key, value) for key, value in row.items() if key.endswith("_ID") and key not in ("REVISION_ID", "LOGICAL_ID")]
            annotated = versioning.annotate(row, table.lower(), self.versions, identity)
        row.update(LOGICAL_ID=annotated["logical_id"], REVISION_ID=annotated["revision_id"], VERSION_SET=annotated["version_set"])
        return row

    def preserve_state(self, table, row):
        if table in ("RUNS", "ATTEMPTS"):
            schema.insert_row(self.session, self.config, "RECORD_REVISIONS", {
                "REVISION_ID": row["REVISION_ID"], "LOGICAL_ID": row["LOGICAL_ID"],
                "RUN_ID": row.get("RUN_ID"), "CLIENT_ID": row.get("CLIENT_ID"), "KIND": table.lower(),
                "VERSION_SET": row["VERSION_SET"], "PAYLOAD": row})

    def insert(self, table, row):
        row = self.lineage_row(table, row)
        def write():
            self.preserve_state(table, row)
            return schema.insert_row(self.session, self.config, table, row)
        return write() if self._in_transaction else self.transaction(write)

    def merge(self, table, row, keys):
        row = self.lineage_row(table, row)
        def write():
            self.preserve_state(table, row)
            return schema.merge_row(self.session, self.config, table, row, keys)
        return write() if self._in_transaction else self.transaction(write)

    def transaction(self, callback):
        if self._in_transaction:
            return callback()
        self.sql("BEGIN TRANSACTION")
        self._in_transaction = True
        try:
            result = callback()
            self.sql("COMMIT")
            return result
        except Exception:
            # A normal-work cap can be reached after BEGIN. The reserved closeout
            # allowance must still permit ROLLBACK; never leave a scoped transaction open.
            was_closing = self.session.closing
            self.session.closing = True
            try:
                self.sql("ROLLBACK")
            finally:
                self.session.closing = was_closing
            raise
        finally:
            self._in_transaction = False

    def acquire(self):
        # Snowflake scoped transactions cannot be nested in a caller's transaction:
        # an outside rollback must never erase a prospective model reservation.
        existing = self.sql("SELECT CURRENT_TRANSACTION() AS TRANSACTION_ID")
        if existing[0][0] is not None:
            raise ControlError("Invoke outside a caller transaction; reservations require durable commits")
        self.scope_owned = True
        schema.ensure_schema_version(self.session, self.config)
        budgets = self.sql(f"SELECT BUDGET FROM {self.table('ENGINE_METADATA')}")
        if len(budgets) != 1:
            raise ControlError("Installed budget metadata must be a singleton")
        budget = _decode(budgets[0][0])
        if not isinstance(budget, dict) or set(budget) - {"max_generation_ai_credits"} != set(HARD_LIMITS):
            raise ControlError("Installed budget is missing or has unrecognized limits; preserve installation for review")
        for key, absolute in HARD_LIMITS.items():
            if type(budget[key]) is not int or not 1 <= budget[key] <= absolute:
                raise ControlError("Installed budget contains an invalid ceiling: " + key)
            if self.config["limits"][key] > budget[key]:
                raise ControlError("Requested limit exceeds the immutable installed budget: " + key)
        if "max_generation_ai_credits" in budget:
            installed_credits = budget["max_generation_ai_credits"]
            requested_credits = self.config["limits"].get("max_generation_ai_credits")
            if (type(installed_credits) not in (int, float) or not math.isfinite(installed_credits)
                    or not 0 < installed_credits <= 16 or requested_credits is None or requested_credits > installed_credits):
                raise ControlError("Generation credit guard must preserve the immutable installed ceiling")
        elif "max_generation_ai_credits" in self.config["limits"]:
            raise ControlError("Generation credit guard requires a fresh installation with that budget")
        self.installed_budget = budget
        if self.config["operation"] == "capture_evidence":
            settings = self.sql(f"SELECT CAPTURE_CONFIG FROM {self.table('ENGINE_METADATA')}")
            installed = _decode(settings[0][0]) if len(settings) == 1 else None
            if (not isinstance(installed, dict) or installed.get("capture_enabled") is not True
                    or installed.get("capture_profile") != CAPTURE_PROFILE
                    or installed.get("permitted_execution_mode") != self.config["execution_mode"]):
                raise ControlError("Capture profile/mode is not enabled by the immutable installation")
            if self.config["execution_mode"] == "recorded_smoke":
                if installed.get("allow_recorded_capture") is not True:
                    raise ControlError("Recorded capture requires immutable installation allow_recorded_capture=true")
                self.recorded_capture_allowed = True
        if self.config["operation"] == "run" and self.config.get("execution_mode") == "recorded_smoke":
            settings = self.sql(f"SELECT CAPTURE_CONFIG FROM {self.table('ENGINE_METADATA')}")
            installed = _decode(settings[0][0]) if len(settings) == 1 else None
            if (not isinstance(installed, dict) or installed.get("allow_recorded_run") is not True
                    or installed.get("permitted_run_execution_mode") != "recorded_smoke"
                    or installed.get("recorded_run_contract") != RECORDED_RUN_FORMAT):
                raise ControlError("Recorded ordinary run requires immutable installation allow_recorded_run=true and its contract/mode")
            self.recorded_run_allowed = True
        locks = self.sql(f"SELECT LOCK_KEY FROM {self.table('LOCKS')}")
        if len(locks) != 1 or locks[0][0] != "engine":
            raise ControlError("The installed engine singleton lock is missing or duplicated")
        def claim():
            changed = self.sql(f"UPDATE {self.table('LOCKS')} SET OWNER_RUN_ID=?, ACQUIRED_AT=CURRENT_TIMESTAMP() WHERE LOCK_KEY='engine' AND OWNER_RUN_ID IS NULL", [self.config["run_id"]])
            if len(changed) != 1 or int(changed[0][0]) != 1:
                raise ControlError("Another engine invocation owns the lease; no automatic takeover")
        self.transaction(claim)
        self.locked = True
        # Integers are validated; query tag is quoted as a SQL string, never an identifier.
        tag = _json({"engine": NATIVE_VERSION, "run_id": self.config["run_id"]}).replace("'", "''")
        self.sql(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={self.config['limits']['statement_timeout_seconds']}")
        self.sql(f"ALTER SESSION SET STATEMENT_QUEUED_TIMEOUT_IN_SECONDS={self.config['limits']['statement_timeout_seconds']}")
        self.sql(f"ALTER SESSION SET QUERY_TAG='{tag}'")
        rows = _rows(self.sql(f"SELECT * FROM {self.table('ATTEMPTS')}"))
        self.attempts = [dict(r, PAYLOAD=_decode(r["PAYLOAD"])) for r in rows]
        if self.config.get("reuse_capture_run_id") is not None:
            rows = _rows(self.sql(f"SELECT * FROM {self.table('RUNS')} WHERE RUN_ID=? AND CLIENT_ID=?",
                [self.config["reuse_capture_run_id"], self.config["client_id"]]))
            if (len(rows) != 1 or rows[0].get("RUN_ID") != self.config["reuse_capture_run_id"]
                    or rows[0].get("CLIENT_ID") != self.config["client_id"] or rows[0].get("STATUS") != "failed"):
                raise ControlError("Capture continuation requires exactly one tenant-bound failed prior run")
            prior = _decode(rows[0].get("CONFIG"))
            if (not isinstance(prior, dict) or prior.get("operation") != "capture_evidence"
                    or prior.get("execution_mode") != "live" or prior.get("model") != self.config["model"]
                    or prior.get("capture_profile") != self.config["capture_profile"]
                    or ("input_run_id" in prior and prior["input_run_id"] != self.config.get("input_run_id"))):
                raise ControlError("Capture continuation prior run input/profile/model does not match")
            retained = [row for row in self.attempts if row["RUN_ID"] == self.config["reuse_capture_run_id"]]
            if (not retained or any(row["CLIENT_ID"] != self.config["client_id"]
                    or row["OPERATION"] not in CAPTURE_OPERATIONS for row in retained)):
                raise ControlError("Capture continuation prior attempts must be tenant-bound live capture operations")
            self.capture_reuse_run = rows[0]

    def release(self):
        if self.locked:
            self.transaction(lambda: self.sql(f"UPDATE {self.table('LOCKS')} SET OWNER_RUN_ID=NULL, ACQUIRED_AT=NULL WHERE LOCK_KEY='engine' AND OWNER_RUN_ID=?", [self.config["run_id"]]))
            self.locked = False

    def totals(self):
        # Count reserved and failed requests too. No caller can reset this by naming a new run/client.
        return {"completions": sum(r["OPERATION"] != "embedding" and r["OPERATION"] not in RECORDED_RUN_LEDGER_OPERATIONS for r in self.attempts),
                "embedding_inputs": sum(r["PAYLOAD"].get("input_count", 0) for r in self.attempts if r["OPERATION"] == "embedding")}

    def request_key(self, operation, supplied, content, *, max_output_tokens=None):
        return _digest({"operation": operation, "key": supplied, "content": content,
                        "model": self.config["embedding_model"] if operation == "embedding" else self.config["model"],
                        "versions": self.versions, "max_output_tokens": self.config["limits"]["max_output_tokens"] if max_output_tokens is None else max_output_tokens})

    def cached(self, operation, key, fingerprint=None):
        rows = [r for r in self.attempts if r["CLIENT_ID"] == self.config["client_id"]
                and r["OPERATION"] == operation and r["REQUEST_KEY"] == key and r["STATUS"] == "succeeded"]
        if rows:
            self.cache_hits += 1
            return rows[-1]["PAYLOAD"]
        if self.config.get("reprocess_retained") is True and fingerprint is not None:
            retained = [r for r in self.attempts if r["CLIENT_ID"] == self.config["client_id"]
                        and r["OPERATION"] == operation and r["STATUS"] == "succeeded"
                        and r["PAYLOAD"].get("request_fingerprint") == fingerprint]
            if retained:
                self.cache_hits += 1
                self.processing_events.append({"event": "retained_raw_reprocessing", "attempt_id": retained[-1]["ATTEMPT_ID"],
                                               "original_versions": retained[-1]["PAYLOAD"].get("versions"),
                                               "processing_versions": self.versions, "at": _now()})
                return retained[-1]["PAYLOAD"]
        # A failed/reserved key is deliberately not retried in a later run either.
        if any(r["OPERATION"] == operation and r["REQUEST_KEY"] == key for r in self.attempts):
            raise ControlError("A previous request with this exact key failed or is unresolved; automatic retries are prohibited")
        return None

    def continued_capture_response(self, operation, messages, cap):
        """Borrow only exact paid responses from the explicitly selected failed run."""
        if self.config.get("reuse_capture_run_id") is None:
            return None
        if (self.capture_reuse_run is None or self.config.get("operation") != "capture_evidence"
                or self.config.get("execution_mode") != "live" or operation not in CAPTURE_OPERATIONS
                or self.config.get("capture_settings", {}).get("response_framing") != "single-fence-v2"):
            raise ControlError("Capture continuation is not authorized for this route")
        source_id, source_hash = getattr(self, "capture_source_id", None), getattr(self, "capture_source_hash", None)
        if not source_id or not source_hash:
            raise ControlError("Capture continuation requires a validated canonical source identity")
        candidates = [row for row in self.attempts
            if row["RUN_ID"] == self.config["reuse_capture_run_id"]
            and row["CLIENT_ID"] == self.config["client_id"] and row["OPERATION"] == operation
            and row["PAYLOAD"].get("source_id") == source_id]
        if not candidates:
            return None
        if self.generation_credits()["unknown_attempt_ids"]:
            raise ControlError("Capture continuation requires known cumulative generation usage")
        frozen = ("model", "model_settings", "input_mapping", "prompt", "extraction_contract",
                  "framework", "ontology", "capture_contract", "capture_profile", "capture_prompt", "capture_rubric")
        for row in candidates:
            payload, old = row["PAYLOAD"], row["PAYLOAD"].get("versions", {})
            if (row["STATUS"] != "succeeded" or payload.get("execution_mode") != "live"
                    or payload.get("profile_version") != CAPTURE_PROFILE
                    or payload.get("capture_contract") != "capture-evidence-v1"
                    or payload.get("source_hash") != source_hash or payload.get("model") != self.config["model"]
                    or type(payload.get("max_output_tokens")) is not int or payload["max_output_tokens"] != cap
                    or not isinstance(payload.get("request_messages"), list)
                    or _json(payload["request_messages"]) != _json(messages)
                    or payload.get("request_messages_sha256") != _digest(messages)
                    or not isinstance(payload.get("raw_response"), str)
                    or not isinstance(old, dict)
                    or any(field not in old or versioning.normalize_versions(old[field]) !=
                           versioning.normalize_versions(self.versions.get(field)) for field in frozen)):
                continue
            self.cache_hits += 1
            self.processing_events.append({"event": "capture_response_continuation", "attempt_id": row["ATTEMPT_ID"],
                "source_run_id": row["RUN_ID"], "source_id": source_id, "operation": operation,
                "response_framing": "single-fence-v2", "original_versions": old,
                "processing_versions": self.versions, "at": _now()})
            return payload
        raise ControlError("Saved capture response does not match the exact source/request/model/settings; automatic duplicate calls are prohibited")

    def reserve(self, operation, key, count, fingerprint=None, request_messages=None, request_measurements=None, *, recorded=False, max_output_tokens=None):
        if self.source_revision_stop is not None:
            raise ControlError("Paid job stopped after source review failure: " + self.source_revision_stop)
        totals, limits = self.totals(), self.config["limits"]
        recorded_authorized = ((self.recorded_capture_allowed and operation in RECORDED_CAPTURE_OPERATIONS)
                               or (self.recorded_run_allowed and operation in RECORDED_RUN_LEDGER_OPERATIONS))
        if recorded and (not recorded_authorized
                         or self.config.get("execution_mode") != "recorded_smoke" or self.config.get("enable_model_calls") is not False):
            raise ControlError("Recorded request is not authorized by the installed profile")
        if not recorded and self.config.get("enable_model_calls") is not True:
            raise ControlError("Model calls are disabled; an explicit reviewed enable_model_calls=true is required")
        if self.session.statements + 10 > self.session.maximum - self.session.reserve:
            raise ControlError("Insufficient SQL allowance for provider call and durable attempt closeout")
        if operation == "embedding":
            if totals["embedding_inputs"] + count > limits["max_embedding_inputs"]:
                raise ControlError("Cumulative embedding-input allowance exhausted")
        elif (sum(r["OPERATION"] in RECORDED_RUN_LEDGER_OPERATIONS for r in self.attempts) + 1
              if operation in RECORDED_RUN_LEDGER_OPERATIONS else totals["completions"] + 1) > limits["max_generation_calls"]:
            raise ControlError("Cumulative completion allowance exhausted")
        row = {"ATTEMPT_ID": str(uuid.uuid4()), "RUN_ID": self.config["run_id"],
               "CLIENT_ID": self.config["client_id"], "OPERATION": operation,
               "REQUEST_KEY": key, "STATUS": "reserved", "PAYLOAD": {
                   "reserved_at": _now(), "input_count": count, "versions": self.versions,
                   "model": self.config["embedding_model"] if operation == "embedding" else self.config["model"],
                   "max_output_tokens": (limits["max_output_tokens"] if max_output_tokens is None else max_output_tokens) if operation != "embedding" else None,
                   "usage": None, "usage_status": "unknown", "query_id": None, "request_fingerprint": fingerprint}}
        if self.config.get("persist_request_messages") is True and operation in (REVIEW_PLANNER_OPERATIONS | CAPTURE_OPERATIONS | RECORDED_CAPTURE_OPERATIONS | RECORDED_RUN_LEDGER_OPERATIONS):
            if request_messages is None:
                raise ControlError("Exact request messages are required before review/planner reservation")
            row["PAYLOAD"].update(request_messages=deepcopy(request_messages),
                request_messages_sha256=_digest(request_messages),
                request_input_chars=len(_json({"messages": request_messages})))
        if operation in CAPTURE_OPERATIONS | RECORDED_CAPTURE_OPERATIONS:
            row["PAYLOAD"].update(execution_mode="recorded_smoke" if recorded else "live",
                profile_version=CAPTURE_PROFILE, capture_contract="capture-evidence-v1",
                source_id=getattr(self, "capture_source_id", None), source_hash=getattr(self, "capture_source_hash", None))
        if request_measurements is not None:
            row["PAYLOAD"]["request_measurements"] = deepcopy(request_measurements)
        self.transaction(lambda: self.insert("ATTEMPTS", row))
        self.attempts.append(row)
        return row

    def finish_attempt(self, row, status, **payload):
        row["STATUS"] = status
        row["PAYLOAD"].update(payload, completed_at=_now())
        self.transaction(lambda: self.merge("ATTEMPTS", row, ["ATTEMPT_ID"]))

    def provider_query(self, statement, params):
        self.last_provider_query_id = None
        try:
            result = self.sql(statement, params)
        except Exception:
            # A failed native query can still have a Snowflake query ID. Preserve
            # it when the session allows the diagnostic query; never retry inference.
            try:
                self.last_provider_query_id = self.sql("SELECT LAST_QUERY_ID() AS QUERY_ID")[0][0]
                self.query_ids.append(self.last_provider_query_id)
            except Exception:
                pass
            raise
        # Query IDs are read immediately after the model SQL, before any ledger write.
        query_id = self.sql("SELECT LAST_QUERY_ID() AS QUERY_ID")[0][0]
        self.query_ids.append(query_id)
        self.last_provider_query_id = query_id
        return result[0][0], query_id

    def close_scope(self):
        """Refuse to return with an unexpected transaction owned by this call.

        Never touch a caller transaction: acquire establishes ownership only after
        rejecting an existing transaction. All expected writes have their own
        commits, so unexpected residue is rolled back rather than guessed safe.
        """
        if not self.scope_owned:
            return
        current = self.sql("SELECT CURRENT_TRANSACTION() AS TRANSACTION_ID")[0][0]
        if current is not None:
            self.sql("ROLLBACK")
            self._in_transaction = False
            raise ControlError("Unexpected procedure-owned transaction was rolled back at closeout")

    def generation_credits(self):
        credits, input_tokens, output_tokens, unknown = 0.0, 0, 0, []
        for attempt in self.attempts:
            if attempt["OPERATION"] == "embedding" or attempt["OPERATION"] in RECORDED_CAPTURE_OPERATIONS | RECORDED_RUN_LEDGER_OPERATIONS:
                continue
            usage = attempt["PAYLOAD"].get("usage")
            if (attempt["STATUS"] != "succeeded" or not isinstance(usage, dict)
                    or attempt["PAYLOAD"].get("model") != "claude-sonnet-4-6"):
                unknown.append(attempt["ATTEMPT_ID"])
                continue
            prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
            completion = usage.get("completion_tokens", usage.get("output_tokens"))
            if type(prompt) is not int or type(completion) is not int or min(prompt, completion) < 0:
                unknown.append(attempt["ATTEMPT_ID"])
                continue
            input_tokens += prompt
            output_tokens += completion
            credits += (prompt * 1.8 + completion * 9.0) / 1_000_000
        return {"estimated_ai_credits": credits, "returned_input_tokens": input_tokens,
                "returned_output_tokens": output_tokens, "unknown_attempt_ids": unknown,
                "rates_model": "claude-sonnet-4-6", "rates_ai_credits_per_million": {"input": 1.8, "output": 9.0}}

    def guard_generation_credits(self, prompt, *, max_output_tokens=None):
        ceiling = self.config["limits"].get("max_generation_ai_credits")
        if ceiling is None:
            return
        if self.config.get("model") != "claude-sonnet-4-6":
            raise ControlError("AI-credit guard rates are pinned to claude-sonnet-4-6")
        usage = self.generation_credits()
        if usage["unknown_attempt_ids"]:
            raise ControlError("Prior generation usage is failed, pending or unknown; credit guard forbids another call")
        prospective = (len(prompt.encode("utf-8")) * 1.8 + (self.config["limits"]["max_output_tokens"] if max_output_tokens is None else max_output_tokens) * 9.0) / 1_000_000
        if usage["estimated_ai_credits"] + prospective > ceiling:
            raise ControlError("Generation AI-credit ceiling exhausted before request reservation")

    def complete(self, messages, operation, request_key, *, max_output_tokens=None):
        if self.config.get("operation") == "run" and self.config.get("execution_mode") == "recorded_smoke":
            return self.recorded_run_complete(messages, operation, request_key, max_output_tokens=max_output_tokens)
        if operation == "embedding":
            raise ValueError("Use the embedding callback")
        cap = self.config["limits"]["max_output_tokens"] if max_output_tokens is None else max_output_tokens
        if type(cap) is not int or not 1 <= cap <= self.config["limits"]["max_output_tokens"] or (self.installed_budget is not None and cap > self.installed_budget["max_output_tokens"]):
            raise ControlError("Stage output cap exceeds the immutable installed/request allowance")
        if operation in RECORDED_CAPTURE_OPERATIONS:
            raise ControlError("Recorded capture uses its dedicated response path")
        if self.config.get("operation") == "capture_evidence" and operation not in CAPTURE_OPERATIONS:
            raise ControlError("Capture operation is not registered")
        prompt = _json({"messages": messages})
        if operation in CAPTURE_OPERATIONS and len(prompt.encode("utf-8")) > 90000:
            raise ControlError("Complete capture request exceeds 90000-byte wire limit")
        measurement = None
        limit = self.config["limits"]["max_input_chars"]
        if ((self.config.get("input_measurement") == "message_content_chars_v1" and operation in REVIEW_PLANNER_OPERATIONS)
                or self.config.get("extraction_mode") == "evidence_v1"):
            if not isinstance(messages, list) or any(not isinstance(message, dict)
                    or not isinstance(message.get("content"), str) for message in messages):
                raise ControlError("Content measurement requires complete string message contents")
            content_chars = sum(len(message["content"]) for message in messages)
            # JSON escapes one content character into at most six ASCII bytes
            # (e.g. a control character as \\u0000). UTF-8 needs at most four.
            # Keep a separate 1024-byte allowance for the message/role wrapper;
            # any larger wrapper still fails this transport guard unchanged.
            wire_limit = 6 * limit + 1024
            wire_bytes = len(prompt.encode("utf-8"))
            measurement = {"version": "message_content_chars_v1", "content_chars": content_chars,
                "content_char_limit": limit, "transport_chars": len(prompt),
                "transport_bytes": wire_bytes, "transport_byte_limit": wire_limit}
            if content_chars > limit:
                raise ControlError("Complete decoded message contents exceed context-character limit; nothing was sliced")
            if wire_bytes > wire_limit:
                raise ControlError("Complete JSON transport exceeds bounded escaping allowance; nothing was sliced")
            if self.config.get("extraction_mode") == "evidence_v1" and wire_bytes + 1024 > 131072:
                raise ControlError("evidence_v1 complete prompt exceeds conservative input-token guard")
        elif len(prompt) > limit:
            raise ControlError("Complete permitted prompt exceeds context-character limit; nothing was sliced")
        key = self.request_key(operation, request_key, messages, max_output_tokens=cap)
        fingerprint = _digest({"operation": operation, "key": request_key, "messages": messages, "model": self.config["model"], "max_output_tokens": cap, "request_versions": {k: v for k, v in self.versions.items() if k not in ("code", "package", "code_digest", "processing")}})
        cached = self.cached(operation, key, fingerprint)
        if cached is None and self.config.get("extraction_mode") == "evidence_v1":
            source = getattr(self, "evidence_source", None)
            imported = [entry for entry in self.config.get("evidence_response_cache", [])
                        if source is not None and entry["source_id"] == source["source_id"]
                        and entry["operation"] == operation]
            if imported:
                from . import evidence_assertions
                raw = evidence_assertions.retained_response(imported[0], source, messages, operation, self.config["model"])
                self.cache_hits += 1
                self.processing_events.append({"event": "exact_evidence_response_import",
                    "source_id": source["source_id"], "source_hash": contracts.source_hash(source),
                    "operation": operation, "request_messages_sha256": imported[0]["request_messages_sha256"],
                    "raw_response_sha256": imported[0]["raw_response_sha256"],
                    "provenance": deepcopy(imported[0]["provenance"]), "new_provider_call": False})
                return raw
        if cached is None and self.config.get("reuse_capture_run_id") is not None:
            cached = self.continued_capture_response(operation, messages, cap)
        if cached is not None:
            return cached["raw_response"]
        allowed = self.config.get("allowed_generation_operations", ["source_review_v1"] if self.config.get("source_revision") else None)
        if (self.config.get("source_revision") is not None and operation.startswith("extraction")):
            raise ControlError("New extraction generation is prohibited during retained source revision")
        if allowed is not None and operation not in allowed:
            raise ControlError("Generation operation is not in allowed_generation_operations: " + operation)
        self.guard_generation_credits(prompt, max_output_tokens=cap)
        row = self.reserve(operation, key, 1, fingerprint, request_messages=messages, request_measurements=measurement, max_output_tokens=cap)
        try:
            raw, query_id = self.provider_query(
                "SELECT AI_COMPLETE(model => ?, prompt => ?, model_parameters => PARSE_JSON(?), show_details => TRUE) AS RESULT",
                [self.config["model"], prompt, _json({"max_tokens": cap, "temperature": 0})])
            envelope = _decode(raw)
            message = envelope["choices"][0]["messages"]
            if not isinstance(message, str):
                raise ValueError("AI_COMPLETE returned no single-string message")
            usage = envelope.get("usage")
            self.finish_attempt(row, "succeeded", raw_response=message, raw_provider_result=envelope,
                                usage=usage, usage_status="returned" if usage is not None else "unknown", query_id=query_id)
            return message
        except Exception as exc:
            self.finish_attempt(row, "failed", error=str(exc), raw_provider_result=locals().get("raw"),
                                query_id=locals().get("query_id") or self.last_provider_query_id)
            raise

    def embed(self, texts, request_key):
        if self.config.get("execution_mode") == "recorded_smoke" and self.config["operation"] == "run":
            raise ControlError("Recorded ordinary runs prohibit embeddings")
        if not isinstance(texts, list) or any(not isinstance(t, str) or not t for t in texts):
            raise ValueError("Embedding input must be a list of nonempty complete strings")
        if not texts:
            return []
        if any(len(t) > self.config["limits"]["max_input_chars"] for t in texts):
            raise ControlError("Complete embedding input exceeds configured context limit")
        key = self.request_key("embedding", request_key, texts)
        cached = self.cached("embedding", key)
        if cached is not None:
            return cached["vectors"]
        # Each native embedding statement reserves its single input separately. Failure
        # stops the batch, retaining completed vectors and every prospective attempt.
        vectors = []
        for index, text in enumerate(texts):
            part_key = self.request_key("embedding", request_key + ":" + str(index), text)
            fingerprint = _digest({"operation": "embedding", "key": request_key + ":" + str(index), "text": text, "model": self.config["embedding_model"], "request_versions": {k: v for k, v in self.versions.items() if k not in ("code", "package", "code_digest", "processing")}})
            part = self.cached("embedding", part_key, fingerprint)
            if part is not None:
                vectors.append(part["vectors"][0])
                continue
            row = self.reserve("embedding", part_key, 1, fingerprint)
            vector, query_id = None, None
            try:
                model = self.config["embedding_model"]
                if model not in EMBEDDING_MODELS:
                    raise ControlError("Embedding model is not in the supported literal allowlist")
                vector, query_id = self.provider_query(f"SELECT TO_ARRAY(AI_EMBED('{model}', ?)) AS RESULT", [text])
                vector = _decode(vector)
                if not isinstance(vector, list) or not vector or any(type(n) not in (int, float) or not math.isfinite(n) for n in vector):
                    raise ValueError("Embedding returned an invalid vector")
                self.finish_attempt(row, "succeeded", vectors=[vector], query_id=query_id)
                self.insert("EMBEDDINGS", {"EMBEDDING_ID": part_key, "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "OBJECT_ID": None, "MODEL": self.config["embedding_model"], "CONTENT_HASH": _digest(text), "PAYLOAD": {"vector": vector, "attempt_id": row["ATTEMPT_ID"], "query_id": query_id}})
                vectors.append(vector)
            except Exception as exc:
                self.finish_attempt(row, "failed", error=str(exc), raw_provider_result=locals().get("vector"), query_id=locals().get("query_id") or self.last_provider_query_id)
                raise
        return vectors

    def usage(self):
        report = dict(self.totals(), counts=dict(self.totals(), cache_hits=self.cache_hits, sql_statements=self.session.statements), cache_hits=self.cache_hits, sql_statements=self.session.statements,
                    query_ids=self.query_ids, generation_credit_estimate=self.generation_credits(), retained_reprocessing=self.processing_events, extraction_output_styles=self.extraction_styles, returned_usage=[r["PAYLOAD"].get("usage") for r in self.attempts if r["PAYLOAD"].get("usage") is not None],
                    unknowns=["Native account dollar charges unavailable", "Embedding token usage not returned by AI_EMBED"],
                    attempts=[{"attempt_id": r["ATTEMPT_ID"], "operation": r["OPERATION"], "status": r["STATUS"], "query_id": r["PAYLOAD"].get("query_id")} for r in self.attempts])
        if self.config.get("operation") == "run" and self.config.get("execution_mode") == "recorded_smoke":
            fixtures = [r for r in self.attempts if r["OPERATION"] in RECORDED_RUN_LEDGER_OPERATIONS]
            current = [r for r in fixtures if r["RUN_ID"] == self.config["run_id"]]
            report.update(execution_mode="recorded_smoke", provider_model_calls=0,
                          fixture_attempts=len(fixtures), fixture_attempts_this_run=len(current),
                          fixture_status_counts={status: sum(r["STATUS"] == status for r in current)
                                                 for status in ("reserved", "succeeded", "failed")},
                          recorded_run_contract=RECORDED_RUN_FORMAT)
            report["counts"].update(fixture_attempts=len(fixtures), fixture_attempts_this_run=len(current), provider_model_calls=0)
        if self.config.get("retained_capture_manifest") is not None:
            report["imported_capture_responses"] = self.imported_capture_responses
            report["retained_capture_manifest_sha256"] = self.config["retained_capture_manifest_sha256"]
        if self.config.get("source_revision") is not None:
            report["source_revision_stop"] = self.source_revision_stop
            styles = {row["source_id"]: row for row in self.extraction_styles}
            report["source_revision_accounting"] = [{"source_id": entry["source_id"],
                "correction_status": styles.get(entry["source_id"], {}).get("correction_status",
                    "not_reviewed_after_terminal_failure" if self.source_revision_stop is not None else "not_started")}
                for entry in self.config["retained_capture_manifest"]["sources"]]
        if self.config.get("defer_practice") is True:
            report["practice_deferred"] = True
        return report

    def persist_issue(self, issue, index):
        reason = issue.get("reason", issue.get("message", str(issue))) if isinstance(issue, dict) else str(issue)
        row = {"ISSUE_ID": _digest([self.config["run_id"], index, issue]), "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "REASON": str(reason), "PAYLOAD": issue}
        source_id = issue.get("source_id") if isinstance(issue, dict) else None
        # Omit an unknown nullable column rather than binding Python None through
        # Snowpark, which can render it as the VARCHAR text 'None'. No source is
        # inferred for mapping, aggregation or programme-wide issues.
        if source_id is not None:
            row["SOURCE_ID"] = source_id
        self.insert("INPUT_ISSUES", row)

    def revise_imported(self, source, responses, baseline):
        """One review callback per source; any failure keeps all baseline assertions."""
        from . import source_revision
        requests = []
        previous_attempts = {row["ATTEMPT_ID"] for row in self.attempts}

        def review(messages, operation, supplied_key):
            if requests or operation != "source_review_v1":
                raise ControlError("Source revision permits exactly one source_review_v1 callback per history")
            fingerprint = _digest({"operation": operation, "key": supplied_key, "messages": messages,
                "model": self.config["model"], "max_output_tokens": self.config["limits"]["max_output_tokens"],
                "request_versions": {key: value for key, value in self.versions.items()
                                     if key not in ("code", "package", "code_digest", "processing")}})
            request = {"operation": operation, "supplied_request_key": supplied_key,
                       "request_key": self.request_key(operation, supplied_key, messages),
                       "request_fingerprint": fingerprint}
            requests.append(request)
            try:
                return self.complete(messages, operation, supplied_key)
            finally:
                matches = [row for row in self.attempts if row["OPERATION"] == operation
                           and row["PAYLOAD"].get("request_fingerprint") == fingerprint]
                if matches:
                    request.update(attempt_id=matches[-1]["ATTEMPT_ID"], status=matches[-1]["STATUS"],
                                   query_id=matches[-1]["PAYLOAD"].get("query_id"))

        try:
            if self.source_revision_stop is not None:
                raise ControlError("Source not reviewed because the paid job already stopped: " + self.source_revision_stop)
            normalization = ({"response_normalization": self.config["response_normalization"]}
                             if self.config.get("response_normalization") is not None else {})
            result = source_revision.revise_retained(source, responses, review,
                max_input_chars=self.config["limits"]["max_input_chars"], **normalization)
            if len(requests) != 1:
                raise ControlError("Source revision did not perform its one expected review callback")
        except Exception as exc:
            terminal = bool(requests) or isinstance(exc, ControlError) or not isinstance(exc, (ValueError, KeyError, IndexError, TypeError))
            already_stopped = self.source_revision_stop is not None
            if terminal and not already_stopped:
                self.source_revision_stop = str(exc)
            status = ("not_reviewed_after_terminal_failure" if already_stopped
                      else "review_failed" if terminal else "correction_rejected")
            result = deepcopy(baseline)
            receipt = {"revision_version": self.config["source_revision"],
                "source_hash": contracts.source_hash(source), "original_raw_responses": deepcopy(responses),
                "original_results": deepcopy(baseline), "model_review_raw": None,
                "patch_receipt": {"status": status, "patches": [], "errors": [str(exc)]},
                "correction_status": status, "review_callback_count": len(requests),
                "fallback_reason": "Review failed; complete original assertions and validation statuses retained"}
            receipt["revision_id"] = "source-revision-fallback-" + _digest(receipt)
            result.update(source_revision=receipt, correction_status=status)
            self.issues.append({"stage": "source_revision", "source_id": source["source_id"], "reason": str(exc),
                                "fallback": "original_assertions_retained"})
        self.processing_events.append({"event": "source_assertion_revision", "source_id": source["source_id"],
                                       "receipt": result["source_revision"], "review_requests": requests,
                                       "new_generation_attempt_ids": [row["ATTEMPT_ID"] for row in self.attempts
                                                                      if row["ATTEMPT_ID"] not in previous_attempts]})
        for component in result["components"]:
            component["source_revision_revision_id"] = result["source_revision"]["revision_id"]
        return result

    def raw_input(self):
        raw = self.config.get("raw_records")
        mapping = self.config.get("mapping")
        if "source_relation" in self.config:
            # Read only inside the stored procedure. The extra row detects an
            # over-cap relation rather than silently processing a prefix.
            selected = self.config["source_ids"]
            predicate = " WHERE SOURCE_ID IN (" + ", ".join("?" for _ in selected) + ")" if selected else ""
            rows = _rows(self.sql(
                f"SELECT SOURCE_ID, RAW_RECORD FROM {self.config['source_relation']}{predicate} "
                f"ORDER BY SOURCE_ID LIMIT {self.config['limits']['max_sources'] + 1}", selected))
            if len(rows) > self.config["limits"]["max_sources"]:
                raise ControlError("Source relation exceeds the configured source allowance; no rows were loaded")
            ids, raw = [], []
            for row in rows:
                sid = row.get("SOURCE_ID")
                if not isinstance(sid, str) or not sid.strip():
                    raise ValueError("Source relation SOURCE_ID must be nonempty text; no rows were loaded")
                record = _decode(row.get("RAW_RECORD"))
                if not isinstance(record, dict):
                    raise ValueError("Source relation RAW_RECORD must be a nonnull object; no rows were loaded")
                ids.append(sid)
                raw.append(record)
            if len(set(ids)) != len(ids) or len({_digest(record) for record in raw}) != len(raw):
                raise ValueError("Duplicate source relation IDs or records must be resolved before load; no rows were loaded")
            if selected and set(selected) != set(ids):
                raise ValueError("Selected source IDs were not all found in the source relation; no rows were loaded")
            self.relation_source_ids = ids
        elif raw is None:
            stored = _rows(self.sql(f"SELECT RAW_ID, PAYLOAD FROM {self.table('INPUT_RAW')} WHERE CLIENT_ID=? ORDER BY RAW_ID", [self.config["client_id"]]))
            envelopes = [_decode(r["PAYLOAD"]) for r in stored]
            # Append-only custody can hold many loads. Resolve one complete input
            # batch, never union different revisions into an accidental corpus.
            selected_run = self.config.get("input_run_id")
            if envelopes and selected_run is None:
                selected_run = max(envelopes, key=lambda e: e.get("loaded_at", "")).get("input_run_id")
            if selected_run is not None:
                envelopes = [e for e in envelopes if e.get("input_run_id") == selected_run]
            if not envelopes:
                raise ValueError("No retained input batch matches input_run_id")
            self.resolved_input_run_id = selected_run
            envelopes.sort(key=lambda e: e.get("input_index", 0))
            raw = [e["record"] for e in envelopes]
            mappings = {_json(e["mapping"]) for e in envelopes}
            if mapping is None and len(mappings) == 1:
                mapping = json.loads(next(iter(mappings)))
            if len(mappings) > 1 and mapping is None:
                raise ValueError("Stored raw mappings differ; specify the reviewed common mapping")
        if not isinstance(raw, list) or not raw or len(raw) > self.config["limits"]["max_sources"]:
            raise ControlError("Raw input must contain complete source envelopes within the installed source allowance, including malformed records")
        if not isinstance(mapping, dict):
            raise ValueError("Native normalization requires the mapping object")
        return raw, mapping

    def retain_raw(self, raw, mapping_config):
        identities = []
        for record in raw:
            identity = record
            for path in mapping_config.get("raw_id_field", "record_id").split("."):
                identity = identity.get(path) if isinstance(identity, dict) else None
            identities.append(str(identity) if identity is not None else "content-" + _digest(record))
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate raw identities must be resolved before load; no record was overwritten")
        loaded_at = _now()
        for input_index, (identity, record) in enumerate(zip(identities, raw)):
            payload = versioning.annotate({"record": record, "mapping": mapping_config}, "raw_input",
                                          self.versions, [self.config["client_id"], identity])
            payload.update(input_run_id=self.config["run_id"], loaded_at=loaded_at, input_index=input_index)
            # Batch metadata belongs to the revision commitment as well.
            payload = versioning.annotate(payload, "raw_input", self.versions, [self.config["client_id"], identity])
            self.insert("INPUT_RAW", {"RAW_ID": identity, "RUN_ID": self.config["run_id"],
                                      "CLIENT_ID": self.config["client_id"], "PAYLOAD": payload})

    def append_capture(self, table, row, identity):
        """The lease serializes writers; exact duplicates are harmless, changes fail."""
        old = _rows(self.sql(f"SELECT * FROM {self.table(table)} WHERE {identity}=?", [row[identity]]))
        if old:
            if len(old) != 1 or any(versioning.normalize_versions(_decode(old[0].get(key)) if schema.TABLE_SPECS[table][key] == "VARIANT" else old[0].get(key)) != versioning.normalize_versions(value)
                                    for key, value in row.items()):
                raise ControlError("Changed duplicate capture identity refused: " + row[identity])
            return
        self.insert(table, row)

    def recorded_complete(self, messages, stage, request_key, *, max_output_tokens, response):
        """Durable logical stage receipt; never invokes a provider or invents usage."""
        cap = max_output_tokens
        if (stage not in CAPTURE_STAGES or type(cap) is not int or not 1 <= cap <= self.config["limits"]["max_output_tokens"]
                or not self.installed_budget or cap > self.installed_budget["max_output_tokens"]):
            raise ControlError("Recorded stage/output cap is not authorized")
        prompt = _json({"messages": messages})
        if len(prompt) > self.config["limits"]["max_input_chars"] or len(prompt.encode("utf-8")) > 90000:
            raise ControlError("Complete recorded request exceeds the configured input/wire limit")
        # Tokens are provider-specific. Bound raw fixture bytes conservatively;
        # this is a fixture transport bound, not fabricated provider token usage.
        if not isinstance(response, str) or len(response.encode("utf-8")) > cap * 4:
            raise ControlError("Recorded response exceeds bounded stage output bytes")
        operation = "capture_recorded_" + stage
        content = {"messages": messages, "recorded_response_sha256": _digest(response), "execution_mode": "recorded_smoke"}
        key = self.request_key(operation, request_key, content, max_output_tokens=cap)
        fingerprint = _digest({"operation": operation, "key": request_key, "content": content,
                               "max_output_tokens": cap, "versions": self.versions})
        cached = self.cached(operation, key, fingerprint)
        if cached is not None:
            return cached["raw_response"]
        row = self.reserve(operation, key, 1, fingerprint, request_messages=messages, recorded=True, max_output_tokens=cap)
        self.finish_attempt(row, "succeeded", raw_response=response, execution_mode="recorded_smoke",
            provider_model_calls=0, usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            usage_status="recorded_zero_provider_usage", query_id=None, recorded_response_sha256=_digest(response))
        return response

    def recorded_run_complete(self, messages, operation, supplied_key, *, max_output_tokens=None):
        """Replay an exact source-local fixture through the ordinary callback."""
        if (not self.recorded_run_allowed or self.config.get("enable_model_calls") is not False
                or operation not in RECORDED_RUN_OPERATIONS or self.recorded_run_source is None):
            raise ControlError("Recorded ordinary request is not authorized by the installed contract/source")
        source = self.recorded_run_source
        sid, source_hash = source["source_id"], contracts.source_hash(source)
        entry = next((e for e in self.config["recorded_run_responses"]["sources"] if e["source_id"] == sid), None)
        if entry is None or entry["source_hash"] != source_hash:
            raise ControlError("Missing recorded run fixture for exact source_id/source_hash: " + sid)
        ordinal_key = (sid, operation)
        ordinal = self.recorded_run_ordinals.get(ordinal_key, 0)
        values = entry["operations"].get(operation, [])
        if ordinal >= len(values):
            raise ControlError("Missing recorded run response for exact operation/ordinal: " + operation)
        self.recorded_run_ordinals[ordinal_key] = ordinal + 1
        value = values[ordinal]
        response = value["raw_response"] if isinstance(value, dict) else value
        if isinstance(value, dict) and value.get("request_messages_sha256", _digest(messages)) != _digest(messages):
            raise ControlError("Recorded run request_messages_sha256 mismatch")
        cap = self.config["limits"]["max_output_tokens"] if max_output_tokens is None else max_output_tokens
        prompt = _json({"messages": messages})
        if (type(cap) is not int or not 1 <= cap <= self.config["limits"]["max_output_tokens"]
                or not self.installed_budget or cap > self.installed_budget["max_output_tokens"]
                or len(prompt) > self.config["limits"]["max_input_chars"]
                or len(response.encode("utf-8")) > cap * 4):
            raise ControlError("Recorded ordinary request/output exceeds installed bounds")
        ledger_operation = "run_recorded_" + operation
        # The complete source entry, not the entire batch, commits fixture identity.
        content = {"messages": messages, "source_id": sid, "source_hash": source_hash,
                   "fixture_source_sha256": _digest(entry), "ordinal": ordinal,
                   "execution_mode": "recorded_smoke", "fixture_contract": RECORDED_RUN_FORMAT}
        key = self.request_key(ledger_operation, supplied_key, content, max_output_tokens=cap)
        cached = self.cached(ledger_operation, key)
        if cached is not None:
            return cached["raw_response"]
        fingerprint = _digest({"operation": operation, "supplied_request_key": supplied_key, "content": content,
                               "max_output_tokens": cap, "versions": self.versions})
        row = self.reserve(ledger_operation, key, 1, fingerprint, request_messages=messages, recorded=True, max_output_tokens=cap)
        self.finish_attempt(row, "succeeded", raw_response=response, execution_mode="recorded_smoke",
            fixture_contract=RECORDED_RUN_FORMAT, source_id=sid, source_hash=source_hash,
            fixture_source_sha256=_digest(entry), recorded_response_sha256=_digest(response),
            original_operation=operation, response_ordinal=ordinal, supplied_request_key=supplied_key,
            request_messages_sha256=_digest(messages), provider_model_calls=0,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            usage_status="recorded_zero_provider_usage", query_id=None)
        return response

    def process_capture(self, sources, normalized):
        from . import capture_evidence, capture_storage
        mode = self.config["execution_mode"]
        settings = deepcopy(self.config.get("capture_settings", {}))
        # Validate the entire selected canonical batch before any callback.
        for source in sources:
            contracts.validate_source(source)
            focal = settings.get("customer_side_speaker_ids", source["metadata"].get("customer_side_speaker_ids"))
            if (source["client_id"] != self.config["client_id"] or not isinstance(focal, list) or not focal
                    or any(not isinstance(v, str) or not v for v in focal) or len(focal) != len(set(focal))
                    or not set(focal) <= {event["speaker"] for event in source["events"]}):
                raise ValueError("Capture requires tenant-bound canonical source and explicit customer_side_speaker_ids")
        if not sources:
            raise ValueError("Capture requires at least one valid canonical source")
        fixture = self.config.get("recorded_responses", {})
        if mode == "recorded_smoke" and set(fixture) != {source["source_id"] for source in sources}:
            raise ValueError("Recorded responses must match the complete canonical source set")
        results, hashes = [], {}
        for source in sources:
            sid = source["source_id"]
            source_hash = hashes[sid] = contracts.source_hash(source)
            source_record = versioning.annotate(source, "source", self.versions, [self.config["client_id"], sid])
            self.insert("INPUT_SOURCES", {"SOURCE_ID": sid, "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"],
                                         "CONTENT_HASH": source_hash, "PAYLOAD": source_record})
            self.capture_source_id, self.capture_source_hash = sid, source_hash
            ordinals = {}
            def complete(messages, stage, request_key, *, max_output_tokens):
                if stage not in CAPTURE_STAGES or max_output_tokens != (8192 if stage == "worker" else 16384):
                    raise ControlError("Capture requires frozen stage names and output caps")
                if time.monotonic() >= self.session.deadline:
                    raise ControlError("Capture wall-clock allowance exhausted")
                if mode == "recorded_smoke":
                    index = ordinals.get(stage, 0)
                    ordinals[stage] = index + 1
                    values = fixture[sid].get(stage, [])
                    if index >= len(values):
                        raise ControlError("Missing recorded response for source/stage/zero-based ordinal: " + sid + "/" + stage + "/" + str(index))
                    return self.recorded_complete(messages, stage, request_key, max_output_tokens=max_output_tokens, response=values[index])
                return self.complete(messages, "capture_evidence_" + stage, request_key, max_output_tokens=max_output_tokens)
            common = dict(run_id=self.config["run_id"], client_id=self.config["client_id"], source_hash=source_hash,
                          versions=self.versions, profile_version=CAPTURE_PROFILE, execution_mode=mode)
            def record(value):
                self.append_capture("CAPTURE_ITEMS", capture_storage.item_row(value, **common), "ITEM_ID")
            def checkpoint(value):
                self.append_capture("CAPTURE_CHECKPOINTS", capture_storage.checkpoint_row(value, **common), "CHECKPOINT_ID")
            result = capture_evidence.capture_source(source, complete, settings=dict(settings, source_hash=source_hash),
                versions=self.versions, on_record=record, on_checkpoint=checkpoint)
            results.append(result)
        for index, issue in enumerate(self.issues):
            self.persist_issue(issue, index)
        return {"status": "partial" if self.issues or any(r.get("status") != "completed" for r in results) else "complete",
                "operation": "capture_evidence", "capture_contract": "capture-evidence-v1", "capture_export_contract": "capture-export-v1",
                "profile_version": CAPTURE_PROFILE, "model": CAPTURE_MODEL, "execution_mode": mode,
                "provider_model_calls": sum(r["RUN_ID"] == self.config["run_id"] and r["OPERATION"] in CAPTURE_OPERATIONS for r in self.attempts),
                "source_hashes": hashes, "sources": [{"source_id": r["source_id"], "status": r["status"],
                    "record_count": len(r["records"]), "checkpoint_count": len(r["checkpoints"])} for r in results],
                "input_accounting": normalized["accounting"], "eligibility": "source_relative_capture_development_baseline",
                "limitations": ["Known baseline errors retained", "Claims are not finished training cards", "Client guidance not approved"]}

    def process(self):
        raw, mapping_config = self.raw_input()
        self.config["mapping"] = mapping_config
        self.versions["input_mapping"] = versioning.digest(mapping_config)
        if self.config["operation"] == "load":
            from . import mapping
            accounting = mapping.normalize(raw, mapping_config, self.config["client_id"])["accounting"]
            if self.relation_source_ids is not None:
                mapped_ids = [record["source_id"] for record in accounting["records"]]
                if any(mapped is not None and mapped != supplied
                       for mapped, supplied in zip(mapped_ids, self.relation_source_ids)):
                    raise ValueError("Mapped source IDs must match source relation SOURCE_ID; no rows were loaded")
            if accounting["input_event_count"] > self.config["limits"]["max_events"]:
                raise ControlError("Complete raw input, including excluded records, exceeds event allowance")
            self.retain_raw(raw, mapping_config)
            return {"status": "complete", "loaded_raw_records": len(raw), "model_calls": 0, "accounting": accounting}
        from . import aggregation, extraction, export, mapping
        normalized = mapping.normalize(raw, mapping_config, self.config["client_id"])
        sources = normalized["sources"]
        compact_ids = set(self.config["compact_source_ids"])
        unknown_compact = compact_ids - {source["source_id"] for source in sources}
        if unknown_compact:
            raise ValueError("compact_source_ids must select actual canonical sources; unknown IDs: " + ", ".join(sorted(unknown_compact)))
        self.issues.extend(normalized["issues"])
        if len(sources) > self.config["limits"]["max_sources"] or normalized["accounting"]["input_event_count"] > self.config["limits"]["max_events"]:
            raise ControlError("Complete canonical input exceeds frozen source/event allowance")
        imported = validate_retained_capture_manifest(self.config, sources, self.versions)
        if "raw_records" in self.config:
            self.retain_raw(raw, mapping_config)
        if self.config["operation"] == "capture_evidence":
            return self.process_capture(sources, normalized)
        if self.config.get("execution_mode") == "recorded_smoke":
            unknown_fixtures = {entry["source_id"] for entry in self.config["recorded_run_responses"]["sources"]} - {source["source_id"] for source in sources}
            if unknown_fixtures:
                raise ControlError("Recorded run fixture source IDs are absent from canonical input: " + ", ".join(sorted(unknown_fixtures)))
        hashes, components, expected, guidance = {}, [], [], []
        for source in sources:
            sid = source["source_id"]
            hashes[sid] = contracts.source_hash(source)
            source_record = versioning.annotate(source, "source", self.versions, [self.config["client_id"], sid])
            self.insert("INPUT_SOURCES", {"SOURCE_ID": sid, "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "CONTENT_HASH": hashes[sid], "PAYLOAD": source_record})
            before_hits = self.cache_hits
            output_style = self.config["extraction_mode"] if self.config["extraction_mode"] in ("split_v3", "evidence_v1") else ("compact" if sid in compact_ids else "standard")
            style_record = {"source_id": sid, "source_hash": hashes[sid], "output_style": output_style, "status": "pending"}
            self.extraction_styles.append(style_record)
            try:
                if self.config.get("execution_mode") == "recorded_smoke":
                    self.recorded_run_source = source
                if imported is not None:
                    entry = imported[sid]
                    responses = {attempt["operation"].removeprefix("extraction_v3_"): attempt["raw_response"]
                                 for attempt in entry["attempts"]}
                    result = extraction.reprocess_v3(source, responses, evidence_ordering="source_ordinal_v1")
                    if self.config.get("source_revision") is not None:
                        result = self.revise_imported(source, responses, result)
                        style_record.update(source_revision=self.config["source_revision"],
                                            correction_status=result["correction_status"])
                    receipt = versioning.annotate({"format": "retained-capture-import-receipt-v1",
                        "manifest_sha256": self.config["retained_capture_manifest_sha256"],
                        "manifest_file_sha256": self.config.get("retained_capture_manifest_file_sha256"),
                        "provenance": self.config["retained_capture_manifest"]["provenance"],
                        "original_source_entry": entry, "processing": result["processing"],
                        "imported_response_count": len(responses), "new_extraction_attempts": 0},
                        "retained_capture_import", self.versions, [self.config["client_id"], sid,
                                                                  self.config["retained_capture_manifest_sha256"]])
                    self.processing_events.append({"event": "retained_capture_import", "source_id": sid, "receipt": receipt})
                    self.imported_capture_responses += len(responses)
                    for component in result["components"]:
                        component["retained_import_revision_id"] = receipt["revision_id"]
                    style_record.update(imported_responses=len(responses), retained_import_revision_id=receipt["revision_id"])
                elif self.config["extraction_mode"] == "evidence_v1":
                    from . import evidence_assertions
                    self.evidence_source = source
                    result = evidence_assertions.extract(source, self.complete,
                        max_input_chars=self.config["limits"]["max_input_chars"])
                elif self.config["extraction_mode"] == "split_v3":
                    processing_options = ({"evidence_ordering": self.config["evidence_ordering"]}
                                          if self.config.get("evidence_ordering") is not None else {})
                    result = extraction.extract_v3(source, self.complete, max_input_chars=self.config["limits"]["max_input_chars"],
                                                   **processing_options)
                    if "processing" in result:
                        self.processing_events.append({"event": "retained_evidence_ordering", "source_id": sid,
                                                       "processing": result["processing"]})
                else:
                    result = extraction.extract(source, self.complete, max_input_chars=self.config["limits"]["max_input_chars"], output_style=output_style)
                # The extraction result belongs to this exact normalized source;
                # parser failures otherwise carry only a code and reason.
                self.issues.extend(dict(issue, source_id=sid) for issue in result["issues"])
                objects = result["components"]
                status = "quarantined" if not objects or any(c["validation_status"] != "valid" for c in objects) else ("cached" if self.cache_hits > before_hits else "processed")
                style_record.update(status=status, cache_reused=self.cache_hits > before_hits, versions=result.get("versions", {}))
                for component in objects:
                    payload = component.get("payload")
                    local_id = payload.get("id", component["object_id"]) if isinstance(payload, dict) else component["object_id"]
                    # A projection is a separate logical record from its quarantined original.
                    component = versioning.annotate(component, component["kind"], self.versions,
                        [self.config["client_id"], sid, component["kind"], local_id, "validated_children_only" if component.get("delivery_scope") == "validated_children_only" else "original"])
                    if self.config["extraction_mode"] == "evidence_v1" and component["kind"] == "scenario":
                        from . import evidence_assertions
                        self.processing_events.append(evidence_assertions.portable_binding_lineage(component, source))
                    components.append(component)
                    self.insert("COMPONENTS", {"OBJECT_ID": component["object_id"], "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "SOURCE_ID": sid, "KIND": component["kind"], "VALIDATION_STATUS": component["validation_status"], "PAYLOAD": component})
                expected.append({"source_id": sid, "status": status, "source_digest": hashes[sid], "format_normalizations": result.get("format_normalizations", [])})
            except Exception as exc:
                style_record.update(status="failed", error=str(exc))
                self.issues.append({"source_id": sid, "reason": str(exc), "stage": "extraction"})
                expected.append({"source_id": sid, "status": "failed", "source_digest": hashes[sid], "reason": str(exc)})
            for item in source.get("guidance", []):
                record = dict(item, source_id=sid, client_id=self.config["client_id"])
                record.setdefault("guidance_id", _digest([sid, item]))
                record.setdefault("authority", "requires_client_guidance")
                guidance.append(record)
        aggregate_config = dict(self.config.get("aggregation", {}), expected_sources=expected, source_digests=hashes)
        aggregate_config.setdefault("max_embedding_inputs", min(self.config["limits"]["max_embedding_inputs"], getattr(aggregation, "MAX_EMBEDDING_INPUTS", 4096)))
        try:
            grouped = aggregation.aggregate(components, self.embed, self.complete, aggregate_config)
        except Exception as exc:
            # Keep source objects and visible failed aggregation; never fabricate families.
            grouped = {key: [] for key in ("families", "relations", "compatibility", "coverage", "candidates", "issues")}
            self.issues.append({"stage": "aggregation", "reason": str(exc)})
        self.issues.extend(grouped.get("issues", []))
        covered = {r["source_id"] for r in grouped["coverage"]}
        grouped["coverage"].extend(r for r in expected if r["source_id"] not in covered)
        practice = None
        if self.config.get("defer_practice") is True:
            self.issues.append({"stage": "practice", "practice_deferred": True,
                                "reason": "Practice generation explicitly deferred; no practice programme completed"})
        elif "practice" in self.config:
            from . import practice as practice_module
            try:
                practice = practice_module.build_programme(components, grouped, self.complete, self.config["practice"], self.versions)
                self.issues.extend(practice.get("issues", []))
                # Preserve the full programme, including coverage/missing accounting.
                practice = versioning.annotate(practice, "practice", self.versions,
                    [self.config["client_id"], sorted(c["logical_id"] for c in components), self.config["practice"]])
                self.insert("PRACTICE", {"PRACTICE_ID": practice["logical_id"], "RUN_ID": self.config["run_id"],
                    "CLIENT_ID": self.config["client_id"], "KIND": "programme", "PAYLOAD": practice})
            except Exception as exc:
                self.issues.append({"stage": "practice", "reason": str(exc)})
        for key, table, id_key in (("families", "FAMILIES", "family_id"), ("relations", "RELATIONS", "relation_id"), ("compatibility", "COMPATIBILITY", "compatibility_id"), ("candidates", "CANDIDATES", "candidate_id"), ("coverage", "COVERAGE", "coverage_id")):
            for index, item in enumerate(grouped[key]):
                identity = item.get(id_key, _digest(item))
                membership = item.get("member_ids") or [item.get("left_object_id"), item.get("right_object_id"),
                    item.get("scenario_id"), item.get("persona_id"), item.get("source_id")]
                membership = sorted(str(value) for value in membership if value is not None)
                item = versioning.annotate(item, key, self.versions, [self.config["client_id"], item.get("kind"), membership or identity])
                grouped[key][index] = item
                row = {id_key.upper(): identity, "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "PAYLOAD": item}
                if table in ("FAMILIES", "CANDIDATES"):
                    row["KIND"] = item.get("kind")
                if table == "COVERAGE":
                    row.update(SOURCE_ID=item.get("source_id"), STATUS=item.get("status", "unknown"))
                self.insert(table, row)
        for index, record in enumerate(guidance):
            record = versioning.annotate(record, "guidance", self.versions, [self.config["client_id"], record["source_id"], record["guidance_id"]])
            guidance[index] = record
            self.insert("GUIDANCE", {"GUIDANCE_ID": record["guidance_id"], "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "SOURCE_ID": record["source_id"], "AUTHORITY": record["authority"], "PAYLOAD": record})
        for index, issue in enumerate(self.issues):
            self.persist_issue(issue, index)
        status = "partial" if self.issues or any(r["status"] in ("failed", "quarantined") for r in expected) else "complete"
        if self.source_revision_stop is not None or not sources or not any(c["validation_status"] == "valid" for c in components):
            status = "failed"
        manifest = {"run_id": self.config["run_id"], "client_id": self.config["client_id"], "status": status, "model": self.config["model"], "embedding_model": self.config["embedding_model"], "versions": self.versions, "version_set": self.versions, "input_run_id": getattr(self, "resolved_input_run_id", self.config["run_id"]), "source_hashes": hashes, "limits": self.config["limits"], "installed_budget": self.installed_budget, "usage": self.usage(), "input_accounting": normalized.get("accounting", {}), "eligibility": "machine_valid_draft_unreviewed", "limitations": ["Semantic review unperformed", "Client approval not granted", "External platform mapping unverified"]}
        if self.config.get("execution_mode") == "recorded_smoke":
            manifest.update(execution_mode="recorded_smoke", recorded_run_contract=RECORDED_RUN_FORMAT,
                            unattended_inference_qualified=False)
            manifest["limitations"].append("Predetermined recorded fixtures; unattended inference not qualified")
        if self.config.get("defer_practice") is True:
            manifest["practice_deferred"] = True
        records = dict(grouped, components=components, guidance=guidance, sources=sources, issues=self.issues)
        if practice is not None:
            records["practice"] = practice
        release_id = None
        if status != "failed":
            document = export.build_export(records, manifest)
            errors = export.validate_export(document)
            if errors:
                self.issues.append({"stage": "export", "reason": "Export contract failed", "errors": errors})
                status = "partial"
            else:
                release_id = document["release"]["release_id"]
                manifest = document["manifest"]
                self.insert("RELEASES", {"RELEASE_ID": release_id, "RUN_ID": self.config["run_id"], "CLIENT_ID": self.config["client_id"], "STATUS": "draft", "MANIFEST": manifest, "PAYLOAD": document})
        return {"status": status, "release_id": release_id, "source_hashes": hashes, "coverage": grouped["coverage"], "issues": self.issues, "accounting": normalized.get("accounting", {})}


def capture_export(session, config):
    """Read-only export of one tenant-bound capture run; no lease or ledger writes."""
    from . import capture_storage
    bounded = BoundedSession(session, config["limits"]["max_sql_statements"], closeout_reserve=0,
                             max_run_seconds=config["limits"]["max_run_seconds"])
    schema.ensure_schema_version(bounded, config)
    def read(table, fields="*"):
        ordering = {"RUNS": "RUN_ID", "INPUT_SOURCES": "SOURCE_ID", "CAPTURE_ITEMS": "SOURCE_ID, ITEM_ID",
                    "CAPTURE_CHECKPOINTS": "SOURCE_ID, PAYLOAD:checkpoint:sequence::NUMBER"}[table]
        return _rows(bounded.sql(f"SELECT {fields} FROM {schema.qualified_name(config, table)} WHERE RUN_ID=? AND CLIENT_ID=? ORDER BY {ordering}",
                    params=[config["capture_run_id"], config["client_id"]]).collect())
    runs = read("RUNS")
    if len(runs) != 1:
        raise ValueError("Expected exactly one tenant-bound capture run")
    original = _decode(runs[0]["CONFIG"])
    payload = _decode(runs[0]["PAYLOAD"])
    if original.get("operation") != "capture_evidence":
        raise ValueError("Selected run is not a capture_evidence run")
    sources = [_decode(row["PAYLOAD"]) for row in read("INPUT_SOURCES")]
    document = capture_storage.build_capture_export(run_id=config["capture_run_id"], client_id=config["client_id"],
        records=read("CAPTURE_ITEMS"), checkpoints=read("CAPTURE_CHECKPOINTS"), sources=sources,
        versions=payload["version_set"], profile_version=original["capture_profile"], execution_mode=original["execution_mode"])
    errors = capture_storage.validate_capture_export(document)
    if errors:
        raise ValueError("Stored capture failed export validation: " + "; ".join(errors))
    return document


def run(session, config_json):
    """Snowflake handler. Every run ID is immutable; rerun with a new run ID."""
    config = validate_config(config_json)
    if config["operation"] == "capture_export":
        return capture_export(session, config)
    runtime = Runtime(session, config)
    result = None
    run_written = False
    try:
        runtime.acquire()
        existing = runtime.sql(f"SELECT RUN_ID FROM {runtime.table('RUNS')} WHERE RUN_ID=?", [config["run_id"]])
        if existing:
            raise ControlError("run_id already exists; choose a new run ID to preserve historical results")
        # Do not repeat raw input in the run config; INPUT_RAW retains source custody.
        retained_config = {k: v for k, v in config.items() if k not in ("raw_records", "recorded_responses", "recorded_run_responses")}
        runtime.insert("RUNS", {"RUN_ID": config["run_id"], "CLIENT_ID": config["client_id"], "STATUS": "running", "CONFIG": retained_config, "PAYLOAD": {"started_at": runtime.started_at}})
        run_written = True
        result = runtime.process()
    except Exception as exc:
        result = {"status": "failed", "error": str(exc), "issues": runtime.issues}
    finally:
        runtime.session.closing = True
        if result is None:
            result = {"status": "failed", "error": "Run terminated without a result"}
        try:
            runtime.close_scope()
        except Exception as exc:
            result.update(status="failed", transaction_cleanup_error=str(exc))
        result.update(run_id=config["run_id"], version_set=runtime.versions, usage=runtime.usage(), started_at=runtime.started_at, completed_at=_now())
        try:
            if run_written:
                runtime.transaction(lambda: runtime.merge("RUNS", {"RUN_ID": config["run_id"], "CLIENT_ID": config["client_id"], "STATUS": result["status"], "CONFIG": retained_config, "PAYLOAD": result}, ["RUN_ID"]))
        except Exception as exc:
            result.update(status="failed", closeout_error=str(exc))
        try:
            runtime.release()
        except Exception as exc:
            result.update(status="failed", lease_release_error=str(exc))
        try:
            runtime.close_scope()
        except Exception as exc:
            result.update(status="failed", transaction_cleanup_error=str(exc))
        result["usage"]["sql_statements"] = runtime.session.statements
        result["usage"]["counts"]["sql_statements"] = runtime.session.statements
    return result


def operator_recovery_plan(config, failed_run_id, failed_call_query_id, inactivity_evidence):
    """Build, never execute, an exact stale-lease recovery plan for an operator.

    The operator must first prove that the failed CALL has ended and no engine
    invocation is active. There is no expiry, takeover or attempt-ledger reset.
    Execute each step in order, checking rows/affected-row counts; on any mismatch
    issue ROLLBACK instead of continuing. Recovery is outside normal run routing.
    """
    for key, value in (("failed_run_id", failed_run_id), ("failed_call_query_id", failed_call_query_id),
                       ("inactivity_evidence", inactivity_evidence)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Operator recovery requires " + key)
    client_id = config.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("Operator recovery requires client_id")
    runs, locks = (schema.qualified_name(config, table) for table in ("RUNS", "LOCKS"))
    recovery = {"failed_run_id": failed_run_id, "failed_call_query_id": failed_call_query_id,
                "inactivity_evidence": inactivity_evidence, "recorded_at": _now(),
                "reason": "Operator closed a failed CALL and released only its exact retained lease; attempts and raw results preserved"}
    return {"operation": "operator_recovery_plan", "executed": False,
            "prerequisite": "Verify the failed CALL has ended and no engine invocation is active; retain that query evidence before executing",
            "rollback_on_any_mismatch": True, "attempt_ledger_mutations": 0,
            "steps": [
                {"sql": "BEGIN TRANSACTION", "params": []},
                {"sql": f"SELECT RUN_ID, CLIENT_ID, STATUS FROM {runs} WHERE RUN_ID=? AND CLIENT_ID=?", "params": [failed_run_id, client_id],
                 "expect_rows": [[failed_run_id, client_id, "running"]]},
                {"sql": f"SELECT LOCK_KEY, OWNER_RUN_ID FROM {locks}", "params": [],
                 "expect_rows": [["engine", failed_run_id]]},
                {"sql": f"UPDATE {runs} SET STATUS='failed', PAYLOAD=OBJECT_INSERT(COALESCE(PAYLOAD, OBJECT_CONSTRUCT()), 'operator_recovery', PARSE_JSON(?), TRUE) WHERE RUN_ID=? AND CLIENT_ID=? AND STATUS='running'",
                 "params": [_json(recovery), failed_run_id, client_id], "expect_affected_rows": 1},
                {"sql": f"UPDATE {locks} SET OWNER_RUN_ID=NULL, ACQUIRED_AT=NULL, PAYLOAD=OBJECT_INSERT(COALESCE(PAYLOAD, OBJECT_CONSTRUCT()), 'operator_recovery', PARSE_JSON(?), TRUE) WHERE LOCK_KEY='engine' AND OWNER_RUN_ID=?",
                 "params": [_json(recovery), failed_run_id], "expect_affected_rows": 1},
                {"sql": "COMMIT", "params": []}],
            "after_commit": {"run_id": failed_run_id, "run_status": "failed", "lock_owner": None,
                             "required_checks": ["ATTEMPTS identities/statuses/counts/raw results unchanged", "Only the exact failed RUNS row and exact engine lease changed"]}}
