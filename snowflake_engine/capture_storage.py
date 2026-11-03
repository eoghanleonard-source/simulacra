"""Append-only capture rows and a source-bound, evidence-only portable export.

No storage, model or network calls occur here. Exact item bodies retain the
controller's identity; a separate row identity includes the tenant/run/version
boundary. A successful validation establishes integrity, never factual approval.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from . import capture_contract, contracts, versioning

EXPORT_VERSION = "capture-export-v1"
CAPTURE_VERSION = "capture-evidence-v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {"accepted", "disputed", "omission", "validation_failure", "section_terminal", "reconciliation_terminal",
          "stage_response", "capture_failure"}
_PRIVATE = {"original_text", "guidance", "raw_record", "raw_source", "raw_records", "raw_response",
            "raw_provider_result", "provider_raw", "messages", "request_messages", "recorded_responses"}
_LINEAGE = versioning.LINEAGE_KEYS
_ENVELOPE_FIELDS = {"run_id", "client_id", "source_id", "source_hash", "profile_version", "execution_mode"}


def _required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be a nonempty string")
    return value


def _safe(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _PRIVATE:
                raise ValueError(path + "." + key + " is storage-only and cannot enter capture evidence")
            _safe(child, path + "." + str(key))
    elif isinstance(value, list):
        for child in value:
            _safe(child, path + "[]")
    # Digest rejects non-JSON and nonfinite values as well.


def _context(*, run_id, client_id, source_hash, versions, profile_version, execution_mode):
    for name, value in (("run_id", run_id), ("client_id", client_id), ("profile_version", profile_version)):
        _required(value, name)
    if not isinstance(source_hash, str) or not _HEX.fullmatch(source_hash):
        raise ValueError("source_hash must be a canonical SHA256")
    if execution_mode not in {"live", "recorded_smoke"}:
        raise ValueError("Unknown capture execution_mode")
    errors = versioning.validate_version_set(versions)
    if errors:
        raise ValueError("; ".join(errors))
    return {"run_id": run_id, "client_id": client_id, "source_hash": source_hash,
            "profile_version": profile_version, "execution_mode": execution_mode}


def _body(record, key, context, versions):
    if not isinstance(record, dict):
        raise ValueError(key + " must be an object")
    result = deepcopy(record)
    _safe(result)
    _required(result.get("source_id"), key + ".source_id")
    for name in ("stage", "section_id"):
        _required(result.get(name), key + "." + name)
    for name, expected in context.items():
        if name in result and result[name] != expected:
            raise ValueError(key + "." + name + " differs from storage context")
    for name in ("versions", "version_set"):
        if name in result and versioning.normalize_versions(result[name]) != versioning.normalize_versions(versions):
            raise ValueError(key + "." + name + " differs from storage context")
    if result.get("schema", CAPTURE_VERSION) != CAPTURE_VERSION:
        raise ValueError("Unknown capture record schema")
    if key == "record":
        if result.get("record_kind") not in _KINDS:
            raise ValueError("Unknown capture record_kind")
        if result.get("invalid_record") and (result["record_kind"] != "disputed" or "original_body" not in result):
            raise ValueError("Invalid individual records must remain disputed with their original body")
        identity = result.get("record_id")
        expected = "item-" + versioning.digest({k: v for k, v in result.items() if k != "record_id"})
        if identity != expected:
            raise ValueError("Append-only record_id hash changed")
    else:
        _required(result.get("status"), "checkpoint.status")
        if type(result.get("sequence")) is not int or result["sequence"] < 1:
            raise ValueError("checkpoint.sequence must be a positive integer")
        projection = result.get("current_projection")
        if not isinstance(projection, dict) or set(projection) != {
            "accepted_record_ids", "disputed_record_ids", "omission_record_ids", "superseded_claim_ids"
        }:
            raise ValueError("checkpoint.current_projection must contain all four state references")
        for name, values in projection.items():
            if not isinstance(values, list) or any(not isinstance(x, str) for x in values) or len(set(values)) != len(values):
                raise ValueError("Invalid checkpoint projection references: " + name)
        keys = result.get("request_keys")
        if not isinstance(keys, list) or any(not isinstance(x, str) for x in keys) or len(set(keys)) != len(keys):
            raise ValueError("checkpoint.request_keys must contain distinct request identities")
        identity = "checkpoint-" + versioning.digest({k: v for k, v in result.items() if k != "checkpoint_id"})
        if result.get("checkpoint_id", identity) != identity:
            raise ValueError("Append-only checkpoint_id hash changed")
        result["checkpoint_id"] = identity
    versioning.digest(result)
    return result


def _row(value, key, *, run_id, client_id, source_hash, versions, profile_version, execution_mode):
    context = _context(run_id=run_id, client_id=client_id, source_hash=source_hash, versions=versions,
                       profile_version=profile_version, execution_mode=execution_mode)
    value = _body(value, key, context, versions)
    context["source_id"] = value["source_id"]
    inner_id = value["record_id" if key == "record" else "checkpoint_id"]
    identity_key = {**context, "record_id": inner_id, "versions": versioning.normalize_versions(versions)}
    identity = ("capture-item-" if key == "record" else "capture-checkpoint-") + versioning.digest(identity_key)
    payload = versioning.annotate({**context, key: value}, "capture_" + key, versions, identity_key)
    kind = value["record_kind"] if key == "record" else "checkpoint"
    status = value.get("status", kind)
    # The controller's accepted/disputed label is evidence state, not approval.
    return {"ITEM_ID" if key == "record" else "CHECKPOINT_ID": identity,
            **{name.upper(): field for name, field in context.items()}, "RECORD_TYPE": kind, "STATUS": status,
            "LOGICAL_ID": payload["logical_id"], "REVISION_ID": payload["revision_id"],
            "VERSION_SET": payload["version_set"], "PAYLOAD": payload}


def item_row(record, *, run_id, client_id, source_hash, versions, profile_version, execution_mode):
    """Build one validated immutable row; callers append or reject changed IDs."""
    return _row(record, "record", run_id=run_id, client_id=client_id, source_hash=source_hash,
                versions=versions, profile_version=profile_version, execution_mode=execution_mode)


def checkpoint_row(checkpoint, *, run_id, client_id, source_hash, versions, profile_version, execution_mode):
    """Build a checkpoint row retaining its complete projection/request provenance."""
    return _row(checkpoint, "checkpoint", run_id=run_id, client_id=client_id, source_hash=source_hash,
                versions=versions, profile_version=profile_version, execution_mode=execution_mode)


def _decode(value):
    return json.loads(value) if isinstance(value, str) else deepcopy(value)


def _portable(value, key, context, source_hashes, versions):
    """Accept raw controller objects, PAYLOAD envelopes, or complete stored rows."""
    stored = value if isinstance(value, dict) and "PAYLOAD" in value else None
    payload = _decode(value["PAYLOAD"]) if stored else deepcopy(value)
    if not isinstance(payload, dict):
        raise ValueError("Capture payload must be an object")
    enveloped = key in payload
    body = payload[key] if enveloped else payload
    if not isinstance(body, dict) or body.get("source_id") not in source_hashes:
        raise ValueError("Capture record references an unknown source")
    args = {**context, "source_hash": source_hashes[body["source_id"]], "versions": versions}
    rebuilt = (item_row if key == "record" else checkpoint_row)(body, **args)
    if enveloped and payload != rebuilt["PAYLOAD"]:
        raise ValueError("Capture payload lineage or context changed")
    if stored:
        for name, expected in rebuilt.items():
            actual = _decode(stored.get(name)) if name in {"PAYLOAD", "VERSION_SET"} else stored.get(name)
            if actual != expected:
                raise ValueError("Capture storage row changed: " + name)
    return rebuilt["PAYLOAD"]


def _source(value, client_id, effective_focal=None):
    source = _decode(value["PAYLOAD"]) if isinstance(value, dict) and "PAYLOAD" in value else deepcopy(value)
    # INPUT_SOURCES carries Runtime lineage around the canonical source body.
    # Remove only that wrapper; originals/guidance still participate in its hash.
    source = {key: child for key, child in source.items() if key not in _LINEAGE}
    digest = contracts.source_hash(source)
    if source["client_id"] != client_id:
        raise ValueError("Capture source belongs to a different client")
    if isinstance(value, dict) and "CONTENT_HASH" in value and value["CONTENT_HASH"] != digest:
        raise ValueError("Capture source storage hash changed")
    projected = capture_contract.source_projection(source)
    if effective_focal is not None:
        speakers = {event["speaker"] for event in source["events"]}
        if (not isinstance(effective_focal, list) or not effective_focal
                or any(not isinstance(x, str) or x not in speakers for x in effective_focal)
                or len(set(effective_focal)) != len(effective_focal)):
            raise ValueError("Capture focal speaker IDs must explicitly refer to source speakers")
        projected["customer_side_speaker_ids"] = deepcopy(effective_focal)
    _, spans = capture_contract.catalogue(projected)
    return {**projected, "client_id": client_id, "source_hash": digest,
            "projection_hash": versioning.digest(projected), "spans": list(spans.values())}


def _projection(records):
    from .capture_evidence import current_projection
    view = current_projection(records)
    return {"accepted_record_ids": [r["record_id"] for r in view["accepted"]],
            "disputed_record_ids": [r["record_id"] for r in view["disputed"]],
            "omission_record_ids": [r["record_id"] for r in view["omissions"]],
            "superseded_claim_ids": view["superseded_claim_ids"]}


def _state(records, checkpoints, sources):
    current, ids, checkpoint_ids = {}, set(), set()
    for row in records:
        record = row["record"]
        identity = (record["source_id"], record["record_id"])
        if identity in ids:
            raise ValueError("Duplicate capture record identity")
        ids.add(identity)
    for source in sources:
        sid = source["source_id"]
        own_records = [x["record"] for x in records if x["source_id"] == sid]
        current[sid] = _projection(own_records)
        own_ids = {r["record_id"] for r in own_records}
        by_id = {r["record_id"]: r for r in own_records}
        sequence = 0
        for envelope in checkpoints:
            point = envelope["checkpoint"]
            if point["source_id"] != sid:
                continue
            if ("customer_side_speaker_ids" in point
                    and point["customer_side_speaker_ids"] != source["customer_side_speaker_ids"]):
                raise ValueError("Capture checkpoint focal speaker metadata changed")
            if point["checkpoint_id"] in checkpoint_ids:
                raise ValueError("Duplicate capture checkpoint identity")
            checkpoint_ids.add(point["checkpoint_id"])
            if point["sequence"] <= sequence:
                raise ValueError("Checkpoint sequence is not strictly increasing")
            sequence = point["sequence"]
            for field in ("accepted_record_ids", "disputed_record_ids", "omission_record_ids"):
                if set(point["current_projection"][field]) - own_ids:
                    raise ValueError("Checkpoint references an unknown or cross-source record")
                kind = {"accepted_record_ids": "accepted", "disputed_record_ids": "disputed", "omission_record_ids": "omission"}[field]
                if any(by_id[identity]["record_kind"] != kind for identity in point["current_projection"][field]):
                    raise ValueError("Checkpoint projection changes record state")
        spans = {span["span_id"]: span for span in source["spans"]}
        events = {e["event_id"] for e in source["events"]}
        for record in own_records:
            if ("customer_side_speaker_ids" in record
                    and record["customer_side_speaker_ids"] != source["customer_side_speaker_ids"]):
                raise ValueError("Capture effective focal speaker metadata changed")
            claim = record.get("claim")
            if isinstance(claim, dict) and not record.get("invalid_record"):
                body = {k: v for k, v in claim.items() if k != "claim_id"}
                validated = capture_contract.claim(body, source, spans, events)
                if claim != validated:
                    raise ValueError("Capture claim identity changed")
            # Invalid worker neighbours remain disputed history. Their malformed
            # fields are evidence of failure, never source-valid claims.
            visible = {key: child for key, child in record.items()
                       if not record.get("invalid_record") or key not in {"claim", "original_body"}}
            _references(visible, spans)
    return current


def _references(value, spans):
    if isinstance(value, dict):
        if {"span_id", "event_id", "start", "end", "quote"} <= set(value):
            if value != spans.get(value["span_id"]):
                raise ValueError("Capture evidence quote or source span changed")
        for key, child in value.items():
            if key in {"evidence_span_ids", "counter_span_ids"}:
                capture_contract.references(child, spans)
            else:
                _references(child, spans)
    elif isinstance(value, list):
        for child in value:
            _references(child, spans)


def _effective_focal(records, checkpoints):
    focal = {}
    for key, values in (("record", records), ("checkpoint", checkpoints)):
        for value in values:
            payload = _decode(value["PAYLOAD"]) if isinstance(value, dict) and "PAYLOAD" in value else value
            if not isinstance(payload, dict):
                continue  # _portable supplies the descriptive error.
            body = payload.get(key, payload)
            if isinstance(body, dict) and "customer_side_speaker_ids" in body:
                sid, speakers = body.get("source_id"), body["customer_side_speaker_ids"]
                if sid in focal and focal[sid] != speakers:
                    raise ValueError("Capture effective focal speaker metadata changed")
                focal[sid] = speakers
    return focal


def _ordered(records, checkpoints):
    """Snowflake row order is unspecified; durable request/checkpoint order is not."""
    checkpoints = sorted(checkpoints, key=lambda row: (row["source_id"], row["checkpoint"]["sequence"],
                                                       row["checkpoint"]["checkpoint_id"]))
    requests = {}
    for row in checkpoints:
        sid = row["source_id"]
        prior = requests.get(sid, [])
        keys = row["checkpoint"]["request_keys"]
        if keys[:len(prior)] != prior:
            raise ValueError("Checkpoint cumulative request provenance changed")
        requests[sid] = keys
    ranks = {sid: {key: rank for rank, key in enumerate(keys)} for sid, keys in requests.items()}
    records = sorted(records, key=lambda row: (row["source_id"],
        ranks.get(row["source_id"], {}).get(row["record"].get("request_cache_key"), len(requests.get(row["source_id"], []))),
        row["record"]["record_id"]))
    return records, checkpoints


def build_capture_export(*, run_id, client_id, records, checkpoints, sources, versions, profile_version, execution_mode):
    """Export all append states plus their current source-specific projection."""
    context = {"run_id": run_id, "client_id": client_id, "profile_version": profile_version,
               "execution_mode": execution_mode}
    for name, values in (("records", records), ("checkpoints", checkpoints), ("sources", sources)):
        if not isinstance(values, list):
            raise ValueError(name + " must be an array")
    focal = _effective_focal(records, checkpoints)
    public_sources = []
    for source in sources:
        body = _decode(source["PAYLOAD"]) if isinstance(source, dict) and "PAYLOAD" in source else source
        public_sources.append(_source(source, client_id, focal.get(body.get("source_id"))))
    source_hashes = {s["source_id"]: s["source_hash"] for s in public_sources}
    if len(source_hashes) != len(public_sources):
        raise ValueError("Duplicate capture source identity")
    # Validate even an empty export's run/profile/version boundary.
    _context(**context, source_hash="0" * 64, versions=versions)
    portable = [_portable(row, "record", context, source_hashes, versions) for row in records]
    points = [_portable(row, "checkpoint", context, source_hashes, versions) for row in checkpoints]
    portable, points = _ordered(portable, points)
    public_sources.sort(key=lambda source: source["source_id"])
    document = {"schema_version": EXPORT_VERSION, **context,
                "version_set": versioning.normalize_versions(deepcopy(versions)),
                "requires_client_guidance": True, "client_ready": False, "semantic_approval": "not_established",
                "delivery_scope": "capture_evidence_only", "sources": public_sources,
                "records": portable, "checkpoints": points,
                "current_projection": _state(portable, points, public_sources),
                "manifest": {"source_hashes": source_hashes, "model": versions["model"],
                             "capture_contract": CAPTURE_VERSION, "profile_version": profile_version,
                             "execution_mode": execution_mode,
                             "validation_scope": "integrity_lineage_and_exact_source_spans_only"}}
    document["sha256"] = versioning.digest(document)
    document["export_id"] = "capture-export-" + document["sha256"]
    errors = validate_capture_export(document)
    if errors:
        raise ValueError("; ".join(errors))
    return document


def validate_capture_export(document):
    """Return integrity errors without treating controller acceptance as truth."""
    try:
        if not isinstance(document, dict) or document.get("schema_version") != EXPORT_VERSION:
            raise ValueError("Unsupported capture export schema")
        _safe(document)
        if (document.get("requires_client_guidance") is not True or document.get("client_ready") is not False
                or document.get("semantic_approval") != "not_established"
                or document.get("delivery_scope") != "capture_evidence_only"):
            raise ValueError("Capture evidence approval boundary changed")
        expected = versioning.digest({k: v for k, v in document.items() if k not in {"sha256", "export_id"}})
        if document.get("sha256") != expected or document.get("export_id") != "capture-export-" + expected:
            raise ValueError("Capture export content hash changed")
        context = {name: document[name] for name in ("run_id", "client_id", "profile_version", "execution_mode")}
        versions = document["version_set"]
        _context(**context, source_hash="0" * 64, versions=versions)
        source_hashes = {}
        for source in document["sources"]:
            if set(source) != {"source_id", "client_id", "source_hash", "projection_hash", "spans", "events", "customer_side_speaker_ids"}:
                raise ValueError("Unexpected capture source fields")
            sid = _required(source["source_id"], "source_id")
            if sid in source_hashes or source["client_id"] != context["client_id"]:
                raise ValueError("Duplicate or cross-client capture source")
            if not _HEX.fullmatch(source["source_hash"]):
                raise ValueError("Invalid source commitment")
            projected = {k: source[k] for k in ("source_id", "customer_side_speaker_ids", "events")}
            if any(set(event) != {"event_id", "ordinal", "speaker", "role", "text"} for event in projected["events"]):
                raise ValueError("Unexpected capture source event fields")
            event_ids, prior = set(), None
            for event in projected["events"]:
                if event["event_id"] in event_ids or type(event["ordinal"]) is not int or (prior is not None and event["ordinal"] <= prior):
                    raise ValueError("Capture event identity or order changed")
                event_ids.add(event["event_id"])
                prior = event["ordinal"]
            _, spans = capture_contract.catalogue(projected)
            if source["projection_hash"] != versioning.digest(projected) or source["spans"] != list(spans.values()):
                raise ValueError("Capture source projection/span hash changed")
            source_hashes[sid] = source["source_hash"]
        manifest = document["manifest"]
        for key, expected_value in {"source_hashes": source_hashes, "model": versions["model"],
                                    "capture_contract": CAPTURE_VERSION, "profile_version": context["profile_version"],
                                    "execution_mode": context["execution_mode"],
                                    "validation_scope": "integrity_lineage_and_exact_source_spans_only"}.items():
            if manifest.get(key) != expected_value:
                raise ValueError("Capture manifest changed: " + key)
        records = [_portable(row, "record", context, source_hashes, versions) for row in document["records"]]
        points = [_portable(row, "checkpoint", context, source_hashes, versions) for row in document["checkpoints"]]
        if (records, points) != _ordered(records, points):
            raise ValueError("Capture export records/checkpoints are not in deterministic request order")
        if document["current_projection"] != _state(records, points, document["sources"]):
            raise ValueError("Capture current projection changed")
        return []
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        return [str(error)]
