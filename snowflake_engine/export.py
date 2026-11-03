"""Content-addressed draft releases with frozen v1 and explicit versioned v2 readers.

Scenario/persona rows expose the canonical extraction fields directly, alongside
global object identity and review/eligibility. Families preserve source variants;
the separate variants array indexes membership. Evidence indexes every nested
permitted citation with its object, source and JSON path. Source events expose
permitted text and attribution; originals and raw mapping records stay in storage.

``manifest.source_hashes`` commits to original canonical storage inputs. Build
checks those commitments before withholding originals. The independent validator
can verify ``export_source_hashes`` against visible sources, but cannot reconstruct
withheld originals. Neither check establishes semantic support or client readiness.
No external platform schema is assumed. This module has no I/O or model calls.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import re

from .contracts import (
    PERSONA_SCHEMA, SCENARIO_SCHEMA, canonical_json, is_direct_customer, source_hash,
    validate_component, validate_source,
)

EXPORT_SCHEMA_VERSION = "engine-export-v1"
EXPORT_SCHEMA_VERSION_V2 = "engine-export-v2"
EXPORT_SCHEMA_VERSION_V3 = "engine-export-v3"
_VERSION_FIELDS = {"logical_id", "revision_id", "version_set", "storage_logical_id", "storage_revision_id"}
_PARTIAL_FIELDS = {"delivery_scope", "partial_layers", "withheld_layers", "withheld_children", "projection_of", "projection_lineage", "partial_state", "child_records", "child_accounting", "contract_version"}
_STAGE_A_PROCESSING = "source_ordinal_v1"
_STAGE_B_PROCESSING = {
    "evidence_ordering": "source_ordinal_v1",
    "semantic_revision": "source_assertion_revision_v1",
}
_R3_PROCESSING = {
    "evidence_ordering": "source_ordinal_v1",
    "semantic_revision": "source_assertion_revision_v1",
    "review_response_format": "unique_review_json_v1",
    "input_measurement": "message_content_chars_v1",
}
RELEASE_VERSION = 1
VALIDATION_SCOPE = "structure_identity_lineage_exact_attribution_digest_only"
LIMITATIONS = [
    "Machine validation does not establish semantic acceptance or client readiness.",
    "All generated content and grouping decisions remain unreviewed drafts.",
    "Fictional guidance is an example and is never client-approved guidance.",
    "External platform schema is unknown; its adapter is unverified.",
    "Original text and raw mapping records remain in storage; original source hashes cannot be reconstructed from this redacted export.",
]
_CITATION = ("event_id", "quote", "role", "speaker", "authority")
# Mapping retains both the offending row and its complete source for storage
# custody. Native configurations can also contain a complete raw input batch.
# None of these containers is permitted evidence in an interface release.
_WITHHELD = {"original_text", "raw_record", "raw_source", "raw_records"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_COMPONENT_FIELDS = {"object_id", "source_id", "client_id", "run_id", "kind",
                     "validation_status", "issues", "review_status", "eligibility_status"}
_ARRAYS = ("sources", "scenarios", "personas", "families", "variants", "relations",
           "compatibility", "evidence", "guidance", "source_coverage", "candidates",
           "quarantined_components", "issues")


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _public(value):
    """Withhold storage-only fields recursively, including failed-input records."""
    if isinstance(value, dict):
        return {key: _public(child) for key, child in value.items() if key not in _WITHHELD}
    if isinstance(value, list):
        return [_public(child) for child in value]
    return deepcopy(value)


def _sorted(rows):
    return sorted(rows, key=canonical_json)


def _citations(value, path="$"):
    if isinstance(value, dict):
        if all(field in value for field in _CITATION):
            yield path, {field: value[field] for field in _CITATION}
        else:
            for key, child in value.items():
                yield from _citations(child, path + "." + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _citations(child, f"{path}[{index}]")


def _evidence(components, versions=None):
    rows = []
    for component in components:
        kind = component["kind"]
        fields = (SCENARIO_SCHEMA if kind == "scenario" else PERSONA_SCHEMA)["properties"]
        content = {key: component[key] for key in fields if key in component}
        for path, citation in _citations(content):
            rows.append({"evidence_id": "evidence_" + _hash([component["object_id"], path, citation])[:24],
                         "object_id": component["object_id"], "source_id": component["source_id"],
                         "path": path, **citation})
    return _sorted([_version_row(row, "evidence", versions) for row in rows] if versions else rows)


def _variants(families, versions=None):
    rows = [{"variant_id": "variant_" + _hash([family["family_id"], member["object_id"]])[:24],
                     "family_id": family["family_id"], "object_id": member["object_id"],
                     "source_id": member["source_id"], "kind": member["kind"],
                     "review_status": "unreviewed"}
                    for family in families for member in family.get("variants", [])]
    return _sorted([_version_row(row, "variant", versions) for row in rows] if versions else rows)


def _normalise_derived_numbers(row, kind):
    """Restore only defined derived floating-point fields after VARIANT storage."""
    if kind == "family":
        prevalence = row.get("prevalence")
        if isinstance(prevalence, dict) and type(prevalence.get("fraction")) is int:
            prevalence["fraction"] = float(prevalence["fraction"])
    elif kind == "candidate" and type(row.get("similarity")) is int:
        row["similarity"] = float(row["similarity"])
    return row


def _version_row(row, kind, versions):
    """Commit to the visible portable row without changing legacy endpoint IDs."""
    from .versioning import annotate
    row = _normalise_derived_numbers(deepcopy(row), kind)
    if "logical_id" in row:
        row["storage_logical_id"] = row.pop("logical_id")
    if "revision_id" in row:
        row["storage_revision_id"] = row.pop("revision_id")
    row.pop("version_set", None)
    key = row.get("storage_logical_id") or next(
        (row[field] for field in ("object_id", "family_id", "variant_id", "relation_id",
         "compatibility_id", "candidate_id", "evidence_id", "guidance_id", "source_id") if field in row), _hash(row))
    return annotate(row, "export_" + kind, versions, key)


def _content_hash(document):
    unsigned = deepcopy(document)
    for container in (unsigned.get("release"), unsigned.get("manifest")):
        if isinstance(container, dict):
            container.pop("release_id", None)
            container.pop("content_sha256", None)
    # Aggregation derives this ratio by division (1.0), but Snowflake VARIANT
    # returns an integral ratio as an integer (1). Restore its defined numeric
    # representation only in the checksum copy, preserving native release IDs
    # and the retrieved document. Do not coerce booleans, strings or source data.
    families = unsigned.get("families")
    if isinstance(families, list):
        for family in families:
            prevalence = family.get("prevalence") if isinstance(family, dict) else None
            if isinstance(prevalence, dict) and type(prevalence.get("fraction")) is int:
                prevalence["fraction"] = float(prevalence["fraction"])
    if unsigned.get("schema_version") in {EXPORT_SCHEMA_VERSION_V2, EXPORT_SCHEMA_VERSION_V3}:
        # Requested credit limits and the returned output-token credit rate are
        # defined numeric metadata. VARIANT drops integral float representation.
        # Restore only these four paths in the checksum copy. installed_budget
        # already crossed VARIANT before the writer sealed the release, so its
        # stored representation stays exact, like all source and payload data.
        for name in ("manifest", "run_status"):
            container = unsigned.get(name)
            if not isinstance(container, dict):
                continue
            limits = container.get("limits")
            if isinstance(limits, dict) and type(limits.get("max_generation_ai_credits")) is int:
                limits["max_generation_ai_credits"] = float(limits["max_generation_ai_credits"])
            usage = container.get("usage")
            estimate = usage.get("generation_credit_estimate") if isinstance(usage, dict) else None
            rates = estimate.get("rates_ai_credits_per_million") if isinstance(estimate, dict) else None
            if isinstance(rates, dict) and type(rates.get("output")) is int:
                rates["output"] = float(rates["output"])
        # Cosine scores are derived floats too: identical vectors produce 1.0,
        # which native VARIANT storage can return as 1. Keep v1 seals frozen.
        candidates = unsigned.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    _normalise_derived_numbers(candidate, "candidate")
        from .versioning import normalize_versions
        def normalise(value):
            if isinstance(value, dict):
                return {key: deepcopy(child) if key in {"payload", "metadata", "events", "raw_response"}
                        else normalize_versions(child) if key in {"version_set", "versions", "VERSION_SET", "CONFIG"}
                        else normalise(child) for key, child in value.items()}
            if isinstance(value, list):
                return [normalise(child) for child in value]
            return value
        unsigned = normalise(unsigned)
    return _hash(unsigned)


def _seal(document):
    digest = _content_hash(document)
    for container in (document["release"], document["manifest"]):
        container["content_sha256"] = digest
        container["release_id"] = "release_" + digest[:24]


def _accounting(document):
    result = {"source_count": len(document["sources"]),
            "coverage_count": len(document["source_coverage"]),
            "valid_component_count": len(document["scenarios"]) + len(document["personas"]),
            "quarantined_component_count": len(document["quarantined_components"]),
            "issue_count": len(document["issues"]),
            "coverage_status_counts": dict(sorted(Counter(row.get("status") for row in document["source_coverage"]).items()))}
    if document.get("schema_version") in {EXPORT_SCHEMA_VERSION_V2, EXPORT_SCHEMA_VERSION_V3}:
        valid = document["scenarios"] + document["personas"]
        result["partial_delivery_count"] = sum(row.get("delivery_scope") == "validated_children_only" for row in valid)
        result["full_valid_component_count"] = len(valid) - result["partial_delivery_count"]
        result["full_valid_persona_count"] = sum(row.get("delivery_scope") != "validated_children_only" for row in document["personas"])
        result["partial_persona_delivery_count"] = sum(row.get("delivery_scope") == "validated_children_only" for row in document["personas"])
    if document.get("schema_version") == EXPORT_SCHEMA_VERSION_V3:
        result["full_valid_scenario_count"] = sum(row.get("delivery_scope") == "full_component" for row in document["scenarios"])
        result["partial_scenario_delivery_count"] = sum(row.get("delivery_scope") == "validated_children_only" for row in document["scenarios"])
        result["scope_not_recorded_count"] = sum(row.get("delivery_scope") == "not_recorded" for row in valid)
    return result


def _delivery_scope_fields(component):
    """Make delivery completeness explicit without promoting an older record.

    Earlier component contracts did not record delivery scope. A v3 export labels
    those rows ``not_recorded`` rather than deriving completeness from a valid
    validation status. Native split-v3 records retain their stated scope.
    """
    withheld = component.get("withheld_children", component.get("withheld_layers", []))
    result = {
        "delivery_scope": deepcopy(component.get("delivery_scope", "not_recorded")),
        "partial_state": deepcopy(component.get("partial_state", "not_recorded")),
        "partial_layers": deepcopy(component.get("partial_layers", [])),
        "withheld_children": deepcopy(withheld),
        "projection_of": deepcopy(component.get("projection_of")),
        "projection_lineage": None,
        "contract_version": deepcopy(component.get("contract_version")),
    }
    for key in ("child_records", "child_accounting"):
        if key in component:
            result[key] = deepcopy(component[key])
    return result


def build_export(records, manifest):
    """Build a detached, deterministic release; reject an invalid release explicitly.

    ``records`` is the native result dictionary: sources, components, families,
    relations, compatibility, coverage, guidance, candidates and issues arrays.
    ``manifest`` supplies run/client IDs, status, model/embedding_model, versions
    (code/prompt/schema), full source_hashes, limits and durable usage. Optional
    input_accounting retains mapping omissions. A complete version_set with an
    explicit engine-export-v2 pin selects v2; its absence retains v1 output.
    V2 carries portable revisions alongside preserved storage revision references
    and accepts optional source-preserving practice proposals. No missing facts
    are inferred, and format validity never confers semantic acceptance.
    """
    if not isinstance(records, dict) or not isinstance(manifest, dict):
        raise ValueError("records and manifest must be objects")
    for key in ("sources", "components", "families", "relations", "compatibility",
                "coverage", "guidance", "candidates", "issues"):
        if key in records and not isinstance(records[key], list):
            raise ValueError(f"records.{key} must be an array")
    # Validate the original commitment before originals are intentionally withheld.
    for source in records.get("sources", []):
        digest = source_hash(source)
        if manifest.get("source_hashes", {}).get(source["source_id"]) != digest:
            raise ValueError("manifest.source_hashes does not match canonical source " + source["source_id"])
    versioned = manifest.get("version_set") is not None
    versions = manifest.get("version_set") if versioned else None
    export_pin = versions.get("export") if isinstance(versions, dict) else None
    v2 = export_pin == EXPORT_SCHEMA_VERSION_V2
    v3 = export_pin == EXPORT_SCHEMA_VERSION_V3
    schema_version = EXPORT_SCHEMA_VERSION_V3 if v3 else (EXPORT_SCHEMA_VERSION_V2 if v2 else EXPORT_SCHEMA_VERSION)
    release_version = 3 if v3 else (2 if v2 else RELEASE_VERSION)
    if versioned:
        from .versioning import validate_version_set
        version_errors = validate_version_set(versions)
        if version_errors or export_pin not in {EXPORT_SCHEMA_VERSION_V2, EXPORT_SCHEMA_VERSION_V3}:
            raise ValueError("Invalid export version_set: " + "; ".join(version_errors or ["export must pin engine-export-v2 or engine-export-v3"]))
    elif "practice" in records:
        raise ValueError("practice requires engine-export-v2")
    public = _public(records)
    document = {name: [] for name in _ARRAYS}
    document["schema_version"] = schema_version
    document["manifest"] = _public(manifest)
    document["sources"] = _sorted(public.get("sources", []))
    document["guidance"] = _sorted(public.get("guidance", []))
    approved = {row.get("source_id") for row in document["guidance"] if row.get("authority") == "client_approved"}
    for component in public.get("components", []):
        if not isinstance(component, dict):
            raise ValueError("components must contain objects")
        if component.get("validation_status") == "quarantined":
            document["quarantined_components"].append(component)
            continue
        if component.get("kind") not in {"scenario", "persona"} or not isinstance(component.get("payload"), dict):
            raise ValueError("component requires known kind and object payload")
        if set(component["payload"]) & (_COMPONENT_FIELDS | _VERSION_FIELDS | _PARTIAL_FIELDS):
            raise ValueError("component payload conflicts with export identity fields")
        row = {**component["payload"], **{key: component.get(key) for key in
               ("object_id", "source_id", "kind", "validation_status")},
               "client_id": component.get("client_id", manifest.get("client_id")),
               "run_id": component.get("run_id", manifest.get("run_id")),
               "issues": component.get("issues", []), "review_status": "unreviewed",
               "eligibility_status": "requires_semantic_review" if component.get("source_id") in approved else "requires_client_guidance"}
        if v2:
            row.update({key: deepcopy(component[key]) for key in _VERSION_FIELDS | _PARTIAL_FIELDS if key in component})
        elif v3:
            row.update({key: deepcopy(component[key]) for key in _VERSION_FIELDS if key in component})
            row.update(_delivery_scope_fields(component))
        document["scenarios" if row["kind"] == "scenario" else "personas"].append(row)
    for key in ("families", "relations", "compatibility", "candidates", "issues"):
        document[key] = _sorted(public.get(key, []))
    document["source_coverage"] = _sorted(public.get("coverage", []))
    for key in ("scenarios", "personas", "quarantined_components"):
        document[key] = _sorted(document[key])
    document["evidence"] = _evidence(document["scenarios"] + document["personas"])
    try:
        document["variants"] = _variants(document["families"])
    except (KeyError, TypeError) as exc:
        raise ValueError("families require full identified source variants") from exc
    eligibility = "requires_semantic_review" if document["sources"] and all(s["source_id"] in approved for s in document["sources"]) else "requires_client_guidance"
    document["release"] = {"version": release_version, "status": "draft", "review_status": "unreviewed",
                           "eligibility_status": eligibility, "client_ready": False,
                           "machine_validation_status": "valid", "validation_scope": VALIDATION_SCOPE,
                           "semantic_acceptance": False, "platform_schema_status": "unknown",
                           "platform_adapter_status": "unverified"}
    document["manifest"].update({"schema_version": schema_version, "export_version": release_version,
                                  "eligibility": "machine_valid_draft", "review_status": "unreviewed",
                                  "client_ready": False, "platform_adapter_status": "unverified",
                                  "export_source_hashes": {s["source_id"]: _hash(s) for s in document["sources"]},
                                  "source_projection": "permitted_evidence_only_originals_in_storage",
                                  "withheld_fields": sorted(_WITHHELD), "limitations": list(LIMITATIONS)})
    document["run_status"] = {key: deepcopy(document["manifest"].get(key)) for key in
                              ("run_id", "client_id", "status", "limits", "usage")}
    if versioned:
        if v3:
            document["quarantined_components"] = _sorted([
                _version_row(row, _row_kind("quarantined_components"), versions)
                for row in document["quarantined_components"]
            ])
            originals = {row["object_id"]: row for row in document["quarantined_components"]}
            for plural in ("scenarios", "personas"):
                for row in document[plural]:
                    original = originals.get(row.get("projection_of"))
                    if original is not None:
                        row["projection_lineage"] = {key: deepcopy(original[key]) for key in (
                            "object_id", "source_id", "logical_id", "revision_id", "version_set",
                            "storage_logical_id", "storage_revision_id",
                        )}
                document[plural] = _sorted([_version_row(row, _row_kind(plural), versions) for row in document[plural]])
        else:
            for plural in ("scenarios", "personas"):
                document[plural] = _sorted([_version_row(row, _row_kind(plural), versions) for row in document[plural]])
        endpoints = {row["object_id"]: row for row in document["scenarios"] + document["personas"]}
        for plural in _ARRAYS:
            if plural in {"scenarios", "personas", "evidence", "variants"} | ({"quarantined_components"} if v3 else set()):
                continue
            for row in document[plural]:
                for entry in row.get("lineage", []):
                    if isinstance(entry, dict) and entry.get("object_id") in endpoints:
                        target = endpoints[entry["object_id"]]
                        entry.update({key: deepcopy(target[key]) for key in ("logical_id", "revision_id", "version_set")})
            document[plural] = _sorted([_version_row(row, _row_kind(plural), versions) for row in document[plural]])
        document["manifest"]["export_source_hashes"] = {row["source_id"]: _hash(row) for row in document["sources"]}
        # Evidence and membership rows are derived from their retained endpoints.
        document["evidence"] = _evidence(document["scenarios"] + document["personas"], versions)
        document["variants"] = _variants(document["families"], versions)
        if "practice" in public:
            document["practice"] = public["practice"]
    document["accounting"] = _accounting(document)
    _seal(document)
    errors = validate_export(document)
    if errors:
        raise ValueError("Invalid engine export: " + "; ".join(errors))
    return document


def validate_export(document):
    """Return structural, identity, lineage and exact-evidence errors; never approve.

    Untrusted malformed input returns errors rather than throwing. Digest checking
    detects mutation; it is a content checksum, not a digital signature or proof
    that an external reviewer approved the content.
    """
    try:
        return _validate_export(document)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, AttributeError) as exc:
        return ["$: malformed field types prevent validation: " + str(exc)]


def read_stored_export(document):
    """Validate native readback, recovering only a proven numeric representation.

    VARIANT can return an integral ``estimated_ai_credits`` float as an int.
    The writer seals those two metadata values as floats. Try their original
    representation in a detached copy, accepting it only when the existing seal
    and every ordinary validator pass. Never reseal, alter source data, or
    generalize numeric conversion. Already-valid documents are returned intact.
    """
    errors = validate_export(document)
    if not errors:
        return document
    if isinstance(document, dict) and document.get("schema_version") in {
            EXPORT_SCHEMA_VERSION_V2, EXPORT_SCHEMA_VERSION_V3}:
        recovered = deepcopy(document)
        changed = False
        for name in ("manifest", "run_status"):
            container = recovered.get(name)
            usage = container.get("usage") if isinstance(container, dict) else None
            estimate = usage.get("generation_credit_estimate") if isinstance(usage, dict) else None
            value = estimate.get("estimated_ai_credits") if isinstance(estimate, dict) else None
            # Exact conversion only: no bool/string coercion or large-int rounding.
            if type(value) is int and 0 <= value <= 2 ** 53:
                estimate["estimated_ai_credits"] = float(value)
                changed = True
        if changed and not validate_export(recovered):
            return recovered
    raise ValueError("Stored release failed export validation: " + "; ".join(errors))


def _validate_export(document):
    errors = []
    if not isinstance(document, dict):
        return ["$: export must be an object"]
    try:
        canonical_json(document)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return ["$: export must contain finite JSON values"]
    v2 = document.get("schema_version") == EXPORT_SCHEMA_VERSION_V2
    v3 = document.get("schema_version") == EXPORT_SCHEMA_VERSION_V3
    versioned = v2 or v3
    schema_version = EXPORT_SCHEMA_VERSION_V3 if v3 else (EXPORT_SCHEMA_VERSION_V2 if v2 else EXPORT_SCHEMA_VERSION)
    release_version = 3 if v3 else (2 if v2 else RELEASE_VERSION)
    if document.get("schema_version") not in {EXPORT_SCHEMA_VERSION, EXPORT_SCHEMA_VERSION_V2, EXPORT_SCHEMA_VERSION_V3}:
        errors.append("schema_version: unsupported export schema")
    arrays = {}
    for key in _ARRAYS:
        value = document.get(key)
        if not isinstance(value, list):
            errors.append(key + ": must be an array")
            arrays[key] = []
        else:
            arrays[key] = value
            if any(not isinstance(row, dict) for row in value):
                errors.append(key + ": rows must be objects")
                arrays[key] = [row for row in value if isinstance(row, dict)]

    def obj(value, path):
        if not isinstance(value, dict):
            errors.append(path + ": must be an object")
            return {}
        return value

    def text(value, path):
        if not isinstance(value, str) or not value.strip():
            errors.append(path + ": nonempty string required")
            return False
        return True

    def integer(value, path):
        if type(value) is not int or value < 0:
            errors.append(path + ": nonnegative integer required")
            return False
        return True

    def strings(value, path):
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            errors.append(path + ": string array required")
            return []
        if len(value) != len(set(value)):
            errors.append(path + ": duplicate IDs or labels")
        return value

    def enum(value, choices, path):
        if not isinstance(value, str) or value not in choices:
            errors.append(path + ": unsupported status or label")

    def index(rows, key, path):
        result = {}
        for n, row in enumerate(rows):
            value = row.get(key)
            if text(value, f"{path}[{n}].{key}"):
                if value in result:
                    errors.append(f"{path}: duplicate {key} {value}")
                result[value] = row
        return result

    def digest(value, path):
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            errors.append(path + ": lowercase SHA-256 required")

    manifest = obj(document.get("manifest"), "manifest")
    release = obj(document.get("release"), "release")
    run = obj(document.get("run_status"), "run_status")
    for key in ("run_id", "client_id", "model", "embedding_model"):
        text(manifest.get(key), "manifest." + key)
    enum(manifest.get("status"), {"complete", "partial", "failed"}, "manifest.status")
    versions = obj(manifest.get("versions"), "manifest.versions")
    if not versioned:
        for key in ("code", "prompt", "schema"):
            text(versions.get(key), "manifest.versions." + key)
    elif versions != manifest.get("version_set"):
        errors.append("manifest.versions: differs from manifest exact version set")
    obj(manifest.get("limits"), "manifest.limits")
    usage = obj(manifest.get("usage"), "manifest.usage")
    counts = obj(usage.get("counts"), "manifest.usage.counts")
    for key, value in counts.items():
        integer(value, "manifest.usage.counts." + key)
    for key in ("run_id", "client_id", "status", "limits", "usage"):
        if key not in run or run[key] != manifest.get(key):
            errors.append("run_status." + key + ": differs from immutable manifest")
    fixed_release = {"version": release_version, "status": "draft", "review_status": "unreviewed",
                     "client_ready": False, "machine_validation_status": "valid",
                     "validation_scope": VALIDATION_SCOPE, "semantic_acceptance": False,
                     "platform_schema_status": "unknown", "platform_adapter_status": "unverified"}
    for key, expected in fixed_release.items():
        if type(release.get(key)) is not type(expected) or release.get(key) != expected:
            errors.append("release." + key + ": draft validation cannot imply review or client readiness")
    for key, expected in {"schema_version": schema_version, "export_version": release_version,
                          "eligibility": "machine_valid_draft", "review_status": "unreviewed", "client_ready": False,
                          "platform_adapter_status": "unverified",
                          "source_projection": "permitted_evidence_only_originals_in_storage",
                          "withheld_fields": sorted(_WITHHELD), "limitations": LIMITATIONS}.items():
        if type(manifest.get(key)) is not type(expected) or manifest.get(key) != expected:
            errors.append("manifest." + key + ": invalid contract boundary")
    for container, path in ((release, "release"), (manifest, "manifest")):
        digest(container.get("content_sha256"), path + ".content_sha256")
        if container.get("content_sha256") != _content_hash(document):
            errors.append(path + ".content_sha256: content digest mismatch")
        if container.get("release_id") != "release_" + _content_hash(document)[:24]:
            errors.append(path + ".release_id: does not identify content digest")

    def check_withheld(value, path="$"):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in _WITHHELD:
                    errors.append(path + "." + key + ": storage-only evidence must be withheld")
                check_withheld(child, path + "." + key)
        elif isinstance(value, list):
            for n, child in enumerate(value):
                check_withheld(child, f"{path}[{n}]")
    check_withheld(document)

    sources = index(arrays["sources"], "source_id", "sources")
    source_hashes = obj(manifest.get("source_hashes"), "manifest.source_hashes")
    export_hashes = obj(manifest.get("export_source_hashes"), "manifest.export_source_hashes")
    for field, hashes in (("source_hashes", source_hashes), ("export_source_hashes", export_hashes)):
        if set(hashes) != set(sources):
            errors.append("manifest." + field + ": source IDs must match all exported sources")
        for source_id, value in hashes.items():
            digest(value, "manifest." + field + "." + source_id)
    valid_sources = {}
    for source_id, source in sources.items():
        if source.get("client_id") != manifest.get("client_id"):
            errors.append("sources." + source_id + ": client identity mismatch")
        try:
            valid_sources[source_id] = validate_source(source)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append("sources." + source_id + ": " + str(exc))
        if export_hashes.get(source_id) != _hash(source):
            errors.append("sources." + source_id + ": exported source digest mismatch")

    guidance = index(arrays["guidance"], "guidance_id", "guidance")
    approved = set()
    for guidance_id, row in guidance.items():
        path = "guidance." + guidance_id
        if row.get("source_id") not in sources:
            errors.append(path + ": unknown source_id")
        if row.get("client_id") != manifest.get("client_id"):
            errors.append(path + ": client identity mismatch")
        enum(row.get("authority"), {"fictional", "client_approved"}, path + ".authority")
        text(row.get("text"), path + ".text")
        if row.get("authority") == "client_approved":
            approved.add(row.get("source_id"))
        if row.get("authority") == "fictional" and row.get("status") in {"approved", "client_approved"}:
            errors.append(path + ": fictional guidance cannot be client approved")
    for source_id, source in sources.items():
        for original in source.get("guidance", []) if isinstance(source.get("guidance"), list) else []:
            if not isinstance(original, dict) or not any(all(row.get(k) == v for k, v in original.items())
                    for row in guidance.values() if row.get("source_id") == source_id):
                errors.append("guidance: omitted canonical guidance for " + source_id)
    eligibility = "requires_semantic_review" if sources and set(sources) <= approved else "requires_client_guidance"
    if release.get("eligibility_status") != eligibility:
        errors.append("release.eligibility_status: inconsistent with guidance authority")

    capture_v3 = versioned and "v3" in str(manifest.get("version_set", {}).get("extraction_contract", ""))
    if capture_v3:
        from .contracts import SCENARIO_SCHEMA_V3, PERSONA_SCHEMA_V3
        component_schemas = (("scenarios", "scenario", SCENARIO_SCHEMA_V3), ("personas", "persona", PERSONA_SCHEMA_V3))
    else:
        component_schemas = (("scenarios", "scenario", SCENARIO_SCHEMA), ("personas", "persona", PERSONA_SCHEMA))
    components = {}
    for plural, kind, schema in component_schemas:
        for object_id, row in index(arrays[plural], "object_id", plural).items():
            path = plural + "." + object_id
            if object_id in components:
                errors.append(path + ": duplicate global object_id")
            components[object_id] = row
            if row.get("kind") != kind or row.get("validation_status") != "valid":
                errors.append(path + ": only machine-valid objects of the stated kind belong here")
            for key in ("run_id", "client_id"):
                if row.get(key) != manifest.get(key):
                    errors.append(path + "." + key + ": manifest identity mismatch")
            if row.get("review_status") != "unreviewed":
                errors.append(path + ": semantic review has not been performed")
            expected = "requires_semantic_review" if row.get("source_id") in approved else "requires_client_guidance"
            if row.get("eligibility_status") != expected:
                errors.append(path + ": invalid guidance eligibility")
            if not isinstance(row.get("issues"), list):
                errors.append(path + ".issues: array required")
            if set(row) - (_VERSION_FIELDS | _PARTIAL_FIELDS if versioned else set()) != set(schema["properties"]) | _COMPONENT_FIELDS:
                errors.append(path + ": missing or extra typed component fields")
            content = {key: row[key] for key in schema["properties"] if key in row}
            source = valid_sources.get(row.get("source_id"))
            if source is None:
                errors.append(path + ": unknown or invalid source_id")
            else:
                try:
                    validator = validate_component
                    if capture_v3:
                        from .contracts import validate_component_v3
                        validator = validate_component_v3
                    errors.extend(path + ": " + error for error in validator(kind, content, source))
                except (TypeError, KeyError, ValueError) as exc:
                    errors.append(path + ": invalid component: " + str(exc))
    quarantined = index(arrays["quarantined_components"], "object_id", "quarantined_components")
    for object_id, row in quarantined.items():
        if object_id in components:
            errors.append("quarantined_components: object also exported as valid " + object_id)
        if row.get("source_id") not in sources or row.get("validation_status") != "quarantined":
            errors.append("quarantined_components." + object_id + ": invalid source or quarantine status")
        if not isinstance(row.get("issues"), list) or not row["issues"]:
            errors.append("quarantined_components." + object_id + ": retain quarantine reasons")
    all_components = {**components, **quarantined}
    try:
        if arrays["evidence"] != _evidence(list(components.values()), manifest.get("version_set") if versioned else None):
            errors.append("evidence: must index every nested citation exactly with its source, object and path")
    except (KeyError, TypeError):
        errors.append("evidence: cannot index malformed components")
    index(arrays["evidence"], "evidence_id", "evidence")
    citations = {object_id: {canonical_json(c) for _, c in _citations(row)} for object_id, row in components.items()}

    def object_refs(row, fields, path, kind=None):
        found = []
        for key in fields:
            target = components.get(row.get(key)) if isinstance(row.get(key), str) else None
            if target is None or (kind is not None and target.get("kind") != kind):
                errors.append(path + "." + key + ": unknown component or wrong kind")
            else:
                found.append(target)
        return found

    def linked_evidence(row, endpoints, path, require_both=False):
        refs = row.get("evidence")
        if not isinstance(refs, list):
            errors.append(path + ".evidence: array required")
            return
        seen = set()
        for citation in refs:
            if not isinstance(citation, dict):
                errors.append(path + ".evidence: attributed citation object required")
                continue
            object_id = citation.get("object_id")
            if not isinstance(object_id, str) or object_id not in endpoints or not all(k in citation for k in _CITATION):
                errors.append(path + ".evidence: unknown endpoint or incomplete attribution")
                continue
            if canonical_json({k: citation[k] for k in _CITATION}) not in citations.get(object_id, set()):
                errors.append(path + ".evidence: not an exact retained endpoint citation")
            seen.add(object_id)
        if require_both and seen != set(endpoints):
            errors.append(path + ".evidence: both endpoints need exact source evidence")

    families = index(arrays["families"], "family_id", "families")
    original_components = {}
    memberships = Counter()
    for family_id, row in families.items():
        path = "families." + family_id
        enum(row.get("kind"), {"scenario", "persona"}, path + ".kind")
        member_ids = strings(row.get("member_ids"), path + ".member_ids")
        if not member_ids:
            errors.append(path + ": family requires members")
        memberships.update(member_ids)
        for member in member_ids:
            if member not in components or components[member].get("kind") != row.get("kind"):
                errors.append(path + ": missing or wrong-kind member " + member)
        source_ids = strings(row.get("source_ids"), path + ".source_ids")
        expected_sources = {components[m]["source_id"] for m in member_ids if m in components}
        if set(source_ids) != expected_sources:
            errors.append(path + ": unique source IDs do not match members")
        enum(row.get("grouping_status"), {"singleton", "model_supported_equivalence"}, path + ".grouping_status")
        if row.get("grouping_status") != ("singleton" if len(member_ids) == 1 else "model_supported_equivalence"):
            errors.append(path + ": grouping status differs from member count")
        if row.get("review_status") != "unreviewed" or row.get("guidance_status") != "requires_client_guidance":
            errors.append(path + ": aggregation remains an unreviewed draft")
        prevalence = obj(row.get("prevalence"), path + ".prevalence")
        for key in ("unique_source_count", "all_source_denominator", "kind_valid_source_denominator"):
            integer(prevalence.get(key), path + ".prevalence." + key)
        denominator = len(arrays["source_coverage"])
        expected_fraction = len(expected_sources) / denominator if denominator else None
        expected_kind_count = len({c["source_id"] for c in components.values() if c["kind"] == row.get("kind")})
        if (prevalence.get("unique_source_count") != len(expected_sources)
                or prevalence.get("all_source_denominator") != denominator
                or prevalence.get("kind_valid_source_denominator") != expected_kind_count
                or (expected_fraction is not None and type(prevalence.get("fraction")) not in (int, float))
                or prevalence.get("fraction") != expected_fraction
                or prevalence.get("denominator_scope") != "all_expected_and_observed_sources"):
            errors.append(path + ": prevalence must count unique sources against explicit coverage")
        variants = row.get("variants")
        if not isinstance(variants, list) or any(not isinstance(v, dict) for v in variants):
            errors.append(path + ".variants: full component array required")
            continue
        if {v.get("object_id") for v in variants if isinstance(v.get("object_id"), str)} != set(member_ids) or len(variants) != len(member_ids):
            errors.append(path + ".variants: must preserve every member")
        for variant in variants:
            target = components.get(variant.get("object_id")) if isinstance(variant.get("object_id"), str) else None
            if target is None:
                continue
            original_components[variant["object_id"]] = variant
            if versioned and "logical_id" in variant:
                from .versioning import revision_id
                if (variant.get("version_set") != manifest.get("version_set")
                        or target.get("storage_logical_id") != variant.get("logical_id")
                        or target.get("storage_revision_id") != variant.get("revision_id")
                        or variant.get("revision_id") != revision_id(variant.get("kind"), variant.get("logical_id"), variant, manifest.get("version_set"))):
                    errors.append(path + ".variants: retained storage version identity mismatch")
            schema = dict((kind, schema) for _, kind, schema in component_schemas)[target["kind"]]
            if (variant.get("payload") != {key: target.get(key) for key in schema["properties"]}
                    or any(variant.get(key) != target.get(key) for key in ("source_id", "kind", "validation_status"))):
                errors.append(path + ".variants: member content changed or evidence omitted")
    if set(memberships) != set(components) or any(count != 1 for count in memberships.values()):
        errors.append("families: every valid component must belong to exactly one family")
    try:
        if arrays["variants"] != _variants(list(families.values()), manifest.get("version_set") if versioned else None):
            errors.append("variants: membership index differs from retained families")
    except (KeyError, TypeError):
        errors.append("variants: malformed family variant")
    index(arrays["variants"], "variant_id", "variants")

    def check_lineage(row, expected_ids, path):
        entries = row.get("lineage")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            errors.append(path + ".lineage: component/source lineage array required")
            return
        lookup = index(entries, "object_id", path + ".lineage")
        if set(lookup) != set(expected_ids):
            errors.append(path + ".lineage: must identify every endpoint or member")
        for object_id, entry in lookup.items():
            target = components.get(object_id)
            if target is None or entry.get("source_id") != target.get("source_id"):
                errors.append(path + ".lineage: unknown component or source")
                continue
            digest(entry.get("source_digest"), path + ".lineage.source_digest")
            digest(entry.get("component_digest"), path + ".lineage.component_digest")
            if entry.get("source_digest") != source_hashes.get(target["source_id"]):
                errors.append(path + ".lineage: source digest differs from manifest")
            if object_id in original_components and entry.get("component_digest") != _hash(original_components[object_id]):
                errors.append(path + ".lineage: component digest differs from retained variant")

    for family_id, row in families.items():
        check_lineage(row, strings(row.get("member_ids"), "families." + family_id + ".member_ids"), "families." + family_id)

    candidates = index(arrays["candidates"], "candidate_id", "candidates")
    for candidate_id, row in candidates.items():
        path = "candidates." + candidate_id
        object_refs(row, ("left_object_id", "right_object_id"), path, row.get("kind"))
        score = row.get("similarity")
        if type(score) not in (int, float) or not math.isfinite(score) or not -1 <= score <= 1:
            errors.append(path + ".similarity: finite cosine score required")
        enum(row.get("status"), {"nominated_only", "judged"}, path + ".status")
        if row.get("semantic_acceptance") != "unreviewed":
            errors.append(path + ": similarity does not establish semantic acceptance")
        check_lineage(row, [row.get("left_object_id"), row.get("right_object_id")], path)
    relations = index(arrays["relations"], "relation_id", "relations")
    for relation_id, row in relations.items():
        path = "relations." + relation_id
        endpoints = object_refs(row, ("left_object_id", "right_object_id"), path, row.get("kind"))
        if row.get("candidate_id") not in candidates:
            errors.append(path + ": missing candidate reference")
        else:
            candidate = candidates[row["candidate_id"]]
            if any(candidate.get(key) != row.get(key) for key in ("kind", "left_object_id", "right_object_id")):
                errors.append(path + ": relation endpoints differ from nominated candidate")
        enum(row.get("relation"), {"equivalent", "useful_variant", "partial_overlap", "contains", "distinct", "uncertain"}, path + ".relation")
        enum(row.get("status"), {"unreviewed", "uncertain", "model_supported"}, path + ".status")
        if row.get("semantic_review_status") != "unreviewed":
            errors.append(path + ": relation has not been semantically reviewed")
        strings(row.get("material_differences"), path + ".material_differences")
        linked_evidence(row, [c["object_id"] for c in endpoints], path, row.get("status") == "model_supported")
        if row.get("status") == "model_supported" and row.get("relation") == "equivalent" and row.get("material_differences"):
            errors.append(path + ": equivalence cannot discard material differences")
        check_lineage(row, [row.get("left_object_id"), row.get("right_object_id")], path)
    # Merged families need supported judgments for every pair, never transitive similarity.
    for family_id, family in families.items():
        members = family.get("member_ids")
        if not isinstance(members, list) or any(not isinstance(m, str) for m in members):
            continue
        for n, left in enumerate(members):
            for right in members[n + 1:]:
                if not any({r.get("left_object_id"), r.get("right_object_id")} == {left, right}
                           and r.get("relation") == "equivalent" and r.get("status") == "model_supported"
                           for r in relations.values() if isinstance(r.get("left_object_id"), str) and isinstance(r.get("right_object_id"), str)):
                    errors.append("families." + family_id + ": merged pair lacks supported equivalence")
    for compatibility_id, row in index(arrays["compatibility"], "compatibility_id", "compatibility").items():
        path = "compatibility." + compatibility_id
        endpoints = object_refs(row, ("scenario_id", "persona_id"), path)
        if len(endpoints) == 2:
            scenario, persona = endpoints
            if (scenario["kind"] != "scenario" or persona["kind"] != "persona"
                    or scenario["source_id"] != persona["source_id"]
                    or row.get("source_id") != scenario["source_id"]
                    or row.get("speaker") != persona.get("speaker")):
                errors.append(path + ": compatibility requires the same source and persona speaker")
        if row.get("status") != "shared_source_speaker_evidence" or row.get("review_status") != "unreviewed":
            errors.append(path + ": unsupported compatibility or review status")
        linked_evidence(row, [c["object_id"] for c in endpoints], path, True)
        check_lineage(row, [row.get("scenario_id"), row.get("persona_id")], path)
        source = valid_sources.get(row.get("source_id"))
        events = {event["event_id"]: event for event in source["events"]} if source is not None else {}
        for ref in row.get("evidence", []) if isinstance(row.get("evidence"), list) else []:
            if not isinstance(ref, dict):
                continue  # linked_evidence reports malformed citation rows above.
            event = events.get(ref.get("event_id"))
            if (event is None or ref.get("speaker") != row.get("speaker")
                    or event["speaker"] != row.get("speaker")
                    or event["role"] in {"agent", "system"}
                    or not is_direct_customer(event, source)):
                errors.append(path + ": compatibility evidence must identify the same source-designated customer-side speaker in direct speech")

    coverage = index(arrays["source_coverage"], "source_id", "source_coverage")
    if not set(sources) <= set(coverage):
        errors.append("source_coverage: every canonical source requires an outcome")
    for source_id, row in coverage.items():
        path = "source_coverage." + source_id
        enum(row.get("status"), {"processed", "cached", "failed", "excluded", "quarantined"}, path + ".status")
        if source_id not in sources and row.get("status") not in {"failed", "excluded", "quarantined"}:
            errors.append(path + ": unknown source cannot claim processing")
        ids = strings(row.get("component_ids"), path + ".component_ids")
        expected_ids = {key for key, value in all_components.items() if value.get("source_id") == source_id}
        if set(ids) != expected_ids:
            errors.append(path + ": component IDs omit or invent source output")
        actual = {"valid_component_count": sum(key in components for key in expected_ids),
                  "quarantined_component_count": sum(key in quarantined for key in expected_ids),
                  "scenario_count": sum(components[key]["kind"] == "scenario" for key in expected_ids if key in components),
                  "persona_count": sum(components[key]["kind"] == "persona" for key in expected_ids if key in components)}
        for key, count in actual.items():
            integer(row.get(key), path + "." + key)
            if row.get(key) != count:
                errors.append(path + "." + key + ": accounting differs from retained components")
        if source_id in sources and row.get("source_digest") != source_hashes.get(source_id):
            errors.append(path + ": original source lineage differs from manifest")
        if type(row.get("expected")) is not bool:
            errors.append(path + ".expected: boolean required")
    try:
        if document.get("accounting") != _accounting({**document, **arrays}):
            errors.append("accounting: counts differ from retained records")
    except (TypeError, ValueError):
        errors.append("accounting: invalid coverage statuses")
    if versioned:
        errors.extend(_validate_v2(document, arrays, components, quarantined, schema_version))
    elif "practice" in document:
        errors.append("practice: requires a versioned export")
    return errors


def _row_kind(plural):
    return {"scenarios": "scenario", "personas": "persona", "families": "family",
            "quarantined_components": "quarantined_component", "source_coverage": "coverage",
            "sources": "source", "evidence": "evidence", "guidance": "guidance",
            "compatibility": "compatibility"}.get(plural, plural.rstrip("s"))


def _validate_v2(document, arrays, components, quarantined, export_schema=EXPORT_SCHEMA_VERSION_V2):
    """Check portable revisions and exact stage pins independently of the seal."""
    from .versioning import annotate, validate_version_set, VERSION_KEYS
    errors = []
    versions = document["manifest"].get("version_set")
    errors.extend("manifest." + error for error in validate_version_set(versions))
    if not isinstance(versions, dict):
        return errors
    if export_schema == EXPORT_SCHEMA_VERSION_V3:
        # Replay provenance is the one v3 extension. Only the registered Stage
        # A, B, and R3 contracts are portable; unknown keys and values remain
        # invalid rather than becoming silently portable version metadata.
        allowed_keys = VERSION_KEYS | {"processing"}
        if set(versions) - allowed_keys:
            errors.append("manifest.version_set: exact stage keys required")
        if "processing" in versions:
            processing = versions.get("processing")
            if processing != _STAGE_A_PROCESSING and processing not in (_STAGE_B_PROCESSING, _R3_PROCESSING):
                errors.append("manifest.version_set.processing: must be source_ordinal_v1 or an exact registered processing contract")
    elif set(versions) != VERSION_KEYS:
        # Historic v2 identities remain exact. Do not accept replay provenance
        # keys on an older export contract because doing so changes its reader.
        errors.append("manifest.version_set: exact stage keys required")
    if versions.get("export") != export_schema:
        errors.append("manifest.version_set.export: must pin " + export_schema)
    if document["manifest"].get("versions", {}).get("export") != export_schema:
        errors.append("manifest.versions.export: must pin " + export_schema)
    for plural, rows in arrays.items():
        for n, row in enumerate(rows):
            path = f"{plural}[{n}]"
            if row.get("version_set") != versions:
                errors.append(path + ".version_set: differs from manifest exact version set")
            for field, prefix in (("logical_id", "logical-"), ("revision_id", "revision-"),
                                  ("storage_logical_id", "logical-"), ("storage_revision_id", "revision-")):
                if field in row or field in {"logical_id", "revision_id"}:
                    value = row.get(field)
                    if not isinstance(value, str) or not value.startswith(prefix) or not _HEX.fullmatch(value[len(prefix):]):
                        errors.append(path + "." + field + ": typed SHA-256 identity required")
            base = {key: deepcopy(value) for key, value in row.items() if key not in {"logical_id", "revision_id", "version_set"}}
            _normalise_derived_numbers(base, _row_kind(plural))
            key = base.get("storage_logical_id") or next((base[field] for field in (
                "object_id", "family_id", "variant_id", "relation_id", "compatibility_id",
                "candidate_id", "evidence_id", "guidance_id", "source_id") if field in base), _hash(base))
            try:
                expected = annotate(base, "export_" + _row_kind(plural), versions, key)
            except ValueError:
                continue  # Complete version-set diagnostics are above.
            for field in ("logical_id", "revision_id"):
                if row.get(field) != expected[field]:
                    errors.append(path + "." + field + ": does not identify retained portable content")
            if "lineage" in row:
                for entry in row["lineage"] if isinstance(row["lineage"], list) else []:
                    if not isinstance(entry, dict):
                        continue
                    target = components.get(entry.get("object_id"))
                    if target is not None and any(entry.get(field) != target.get(field)
                                                  for field in ("logical_id", "revision_id", "version_set")):
                        errors.append(path + ".lineage: endpoint version identity differs from retained component")
    v3 = export_schema == EXPORT_SCHEMA_VERSION_V3
    for object_id, row in components.items():
        path = "components." + object_id
        if not v3:
            if row.get("delivery_scope") != "validated_children_only":
                if row.get("delivery_scope") not in {None, "full_component"} or set(row) & {"partial_layers", "withheld_layers", "projection_of"}:
                    errors.append(path + ": partial delivery metadata requires explicit validated_children_only scope")
                continue
            original = quarantined.get(row.get("projection_of"))
            if row.get("kind") != "persona" or original is None or original.get("source_id") != row.get("source_id"):
                errors.append("personas." + object_id + ": partial delivery requires retained same-source quarantined persona")
            if not isinstance(row.get("partial_layers"), list) or not row["partial_layers"]:
                errors.append("personas." + object_id + ": partial delivery must identify affected layers")
            if not isinstance(row.get("withheld_layers"), list) or not row["withheld_layers"]:
                errors.append("personas." + object_id + ": partial delivery must identify withheld layers")
            if original is not None and original.get("kind") != "persona":
                errors.append("personas." + object_id + ": projection must identify a persona quarantine")
            continue

        required_scope_fields = {"delivery_scope", "partial_state", "partial_layers", "withheld_children", "projection_of", "projection_lineage"}
        if not required_scope_fields <= set(row):
            errors.append(path + ": explicit delivery scope fields required")
            continue
        scope, state = row.get("delivery_scope"), row.get("partial_state")
        if scope not in {"full_component", "validated_children_only", "not_recorded"}:
            errors.append(path + ": unsupported delivery scope")
            continue
        if not isinstance(row.get("partial_layers"), list) or not isinstance(row.get("withheld_children"), list):
            errors.append(path + ": partial layers and withheld children must be arrays")
            continue
        if scope == "full_component":
            if state != "complete" or row["partial_layers"] or row["withheld_children"] or row.get("projection_of") is not None or row.get("projection_lineage") is not None:
                errors.append(path + ": full delivery cannot carry partial projection metadata")
            continue
        if scope == "not_recorded":
            if state != "not_recorded" or row["partial_layers"] or row["withheld_children"] or row.get("projection_of") is not None or row.get("projection_lineage") is not None:
                errors.append(path + ": unrecorded scope cannot claim partial or complete delivery")
            continue
        original = quarantined.get(row.get("projection_of"))
        if row.get("kind") != "persona" or state != "partial" or original is None or original.get("source_id") != row.get("source_id"):
            errors.append(path + ": partial delivery requires retained same-source quarantined persona")
            continue
        if not row["partial_layers"] or not row["withheld_children"]:
            errors.append(path + ": partial delivery must identify affected layers and withheld children")
        if original.get("kind") != "persona":
            errors.append(path + ": projection must identify a persona quarantine")
        expected_lineage = {key: original.get(key) for key in (
            "object_id", "source_id", "logical_id", "revision_id", "version_set",
            "storage_logical_id", "storage_revision_id",
        )}
        if row.get("projection_lineage") != expected_lineage:
            errors.append(path + ": original projection lineage differs from retained quarantine")
    if v3:
        for original_id, original in quarantined.items():
            if original.get("partial_state") != "partial":
                continue
            projections = [row for row in components.values() if row.get("projection_of") == original_id]
            if len(projections) != 1:
                errors.append("quarantined_components." + original_id + ": partial original requires exactly one explicit delivery projection")
    from .contracts import VERSIONS, CAPTURE_V3_VERSIONS
    if versions.get("extraction_contract") not in {VERSIONS["schema"], CAPTURE_V3_VERSIONS["schema"]}:
        errors.append("manifest.version_set.extraction_contract: unsupported component contract")
    capture_v3 = versions.get("extraction_contract") == CAPTURE_V3_VERSIONS["schema"]
    if capture_v3:
        from .contracts import validate_persona_children_v3, validate_component_v3
        from .extraction import _persona_projection_v3
        sources = {row["source_id"]: row for row in arrays["sources"]}
        for object_id, row in {**components, **quarantined}.items():
            path = "components." + object_id
            if row.get("contract_version") != CAPTURE_V3_VERSIONS["schema"]:
                errors.append(path + ": candidate contract version required")
            if row.get("kind") not in {"scenario", "persona"}:
                errors.append(path + ": unknown quarantined component kind")
                continue
            source = sources.get(row.get("source_id"))
            payload = row.get("payload") if object_id in quarantined else {
                key: row[key] for key in PERSONA_SCHEMA["properties"] if key in row}
            if row.get("kind") == "persona" and source is not None and "child_records" in row:
                expected_children = validate_persona_children_v3(payload, source)
                retained_children = row.get("child_records")
                if ([{key: value for key, value in child.items() if key != "delivery_eligible"} for child in retained_children]
                        != expected_children):
                    errors.append(path + ": child validation records differ from retained raw payload")
                for child in retained_children:
                    eligible = child.get("validation_status") == "valid" and row.get("partial_state") in {"complete", "partial"}
                    if type(child.get("delivery_eligible")) is not bool or child.get("delivery_eligible") != eligible:
                        errors.append(path + ": invalid child delivery eligibility")
                expected_accounting = {"total": len(retained_children),
                    "valid": sum(child.get("validation_status") == "valid" for child in retained_children),
                    "quarantined": sum(child.get("validation_status") == "quarantined" for child in retained_children),
                    "delivery_eligible": sum(child.get("delivery_eligible") is True for child in retained_children)}
                if row.get("child_accounting") != expected_accounting:
                    errors.append(path + ": child counts differ from retained validation records")
            if row.get("delivery_scope") == "validated_children_only":
                original = quarantined.get(row.get("projection_of"))
                if original is None or source is None:
                    continue
                if (original.get("partial_state") != "partial" or
                        original.get("issues") != validate_component_v3("persona", original.get("payload"), source)):
                    errors.append(path + ": partial original must retain only component validation failures")
                expected_children = validate_persona_children_v3(original.get("payload"), source)
                if ([{key: value for key, value in child.items() if key != "delivery_eligible"}
                     for child in original.get("child_records", [])] != expected_children):
                    errors.append(path + ": projection original has invalid child accounting")
                    continue
                expected = _persona_projection_v3(original, source)
                withheld_key = "withheld_children" if v3 else "withheld_layers"
                if expected is None or payload != expected["payload"] or any(
                        row.get(key) != expected[key] for key in ("partial_state", "partial_layers")) or row.get(withheld_key) != expected["withheld_layers"]:
                    errors.append(path + ": partial delivery differs from validated children and withheld-layer accounting")
    if "practice" in document:
        from .practice import validate_programme
        from .versioning import revision_id
        programme = document["practice"]
        originals = {variant["object_id"]: variant for family in arrays["families"]
                     for variant in family.get("variants", []) if isinstance(variant, dict)}
        for object_id, row in quarantined.items():
            original = {key: deepcopy(value) for key, value in row.items()
                        if key not in _VERSION_FIELDS}
            if "storage_logical_id" in row:
                original.update(logical_id=row["storage_logical_id"], revision_id=row.get("storage_revision_id"), version_set=deepcopy(versions))
            originals[object_id] = original
        accounting = programme.get("object_accounting", []) if isinstance(programme, dict) else []
        ordered = [originals.get(row.get("object_id")) for row in sorted(accounting, key=lambda row: row.get("input_index", -1))
                   if isinstance(row, dict)] if isinstance(accounting, list) else []
        if (len(ordered) != len(originals) or any(row is None for row in ordered)
                or len({row.get("object_id") for row in ordered if isinstance(row, dict)}) != len(originals)):
            errors.append("practice: object accounting must identify every retained component exactly once")
        else:
            errors.extend("practice: " + error for error in validate_programme(programme, ordered))
        if isinstance(programme, dict):
            delivered_personas = {row["object_id"]: row for row in arrays["personas"]}
            for assignment in programme.get("assignments", []):
                if not isinstance(assignment, dict) or "persona_refs" not in assignment:
                    continue
                refs = assignment["persona_refs"]
                if not isinstance(refs, list):
                    errors.append("practice: persona_refs must be an array")
                    continue
                for ref in refs:
                    target = delivered_personas.get(ref.get("object_id")) if isinstance(ref, dict) and isinstance(ref.get("object_id"), str) else None
                    if target is None or target.get("source_id") != ref.get("source_id"):
                        errors.append("practice: persona reference must identify the actual final same-source persona table row")
            records = [("practice", programme)] + [("practice_group", row) for row in programme.get("practice_groups", [])] + [
                ("practice_assignment", row) for row in programme.get("assignments", [])]
            for kind, row in records:
                if not isinstance(row, dict):
                    errors.append("practice: versioned records must be objects")
                    continue
                if row.get("version_set") != versions or row.get("versions") != versions:
                    errors.append("practice: record versions differ from manifest exact version set")
                if (not isinstance(row.get("logical_id"), str) or not re.fullmatch(r"logical-[0-9a-f]{64}", row["logical_id"])
                        or row.get("revision_id") != revision_id(kind, row.get("logical_id"), row, versions)):
                    errors.append("practice: record revision does not identify retained programme content")
    return errors
