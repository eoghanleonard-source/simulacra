"""Exercise the real canonical/aggregation schemas without inference or Snowflake."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from snowflake_engine.aggregation import aggregate
from snowflake_engine.contracts import canonical_json, source_hash
from snowflake_engine.export import build_export, validate_export
from snowflake_engine.mapping import normalize


def citation(event, quote=None):
    return {key: event[key] for key in ("event_id", "role", "speaker", "authority")} | {
        "quote": event["text"] if quote is None else quote}


@pytest.fixture
def inputs():
    texts = ["I need access restored.", "Can you confirm the account?", "I am worried. Also keep my existing files."]
    events = [{"event_id": f"event-{i}", "ordinal": i, "timestamp": None,
               "timing_status": "unknown", "kind": "message", "role": role,
               "speaker": speaker, "authority": "participant_message", "text": text,
               "original_text": "SECRET-ORIGINAL-STORAGE-ONLY" if i == 0 else text,
               "redaction_status": "redacted" if i == 0 else "unchanged"}
              for i, (text, role, speaker) in enumerate(zip(texts, ["customer", "agent", "customer"], ["customer-a", "agent-a", "customer-a"]))]
    source = {"source_id": "source-1", "client_id": "client-1", "source_type": "typed_chat",
              "metadata": {}, "events": events,
              "guidance": [{"authority": "fictional", "text": "Fictional example: confirm access and preserve files."}]}
    scenario = {"id": "local-scenario", "summary": "Restore access while retaining files.",
                "needs": [{"id": "primary", "kind": "primary_need", "summary": "Restore access.",
                           "status": "requested", "related_ids": [], "evidence": [citation(events[0])]},
                          {"id": "late", "kind": "late_need", "summary": "Keep existing files.",
                           "status": "pending", "related_ids": ["primary"], "evidence": [citation(events[2])]}],
                "unknowns": ["Access restoration is not confirmed."]}
    persona = {"id": "local-persona", "speaker": "customer-a", "evidence_status": "observed",
               "actions": [{"id": "action-1", "function": "request_action", "summary": "Requests access.",
                            "evidence": [citation(events[0])]}], "sequences": [],
               "emotional_expression": {"status": "observed", "observations": [
                   {"id": "emotion-1", "labels": ["nervousness"], "summary": "Says they are worried.",
                    "mode": "explicit_self_report", "evidence": [citation(events[2], "I am worried.")]}]},
               "emotional_changes": [], "unknowns": ["Cause and intensity are not established."]}
    components = [{"object_id": "scenario-1", "source_id": "source-1", "kind": "scenario",
                   "payload": scenario, "validation_status": "valid", "issues": []},
                  {"object_id": "persona-1", "source_id": "source-1", "kind": "persona",
                   "payload": persona, "validation_status": "valid", "issues": []}]
    hashes = {source["source_id"]: source_hash(source)}
    aggregate_result = aggregate(components, None, None,
                                 {"source_digests": hashes, "expected_sources": [source["source_id"]]})
    records = {"sources": [source], "components": components, **aggregate_result,
               "guidance": [{"guidance_id": "guidance-1", "source_id": "source-1", "client_id": "client-1", **source["guidance"][0]}]}
    manifest = {"run_id": "run-1", "client_id": "client-1", "status": "complete", "model": "configured-model",
                "embedding_model": "configured-embedding", "versions": {"code": "engine-v1", "prompt": "prompt-v1", "schema": "schema-v1"},
                "source_hashes": hashes, "limits": {"max_generation_attempts": 12},
                "usage": {"counts": {"generation_attempts": 1, "embedding_inputs": 0}, "returned_usage": [], "unknowns": ["dollars"]},
                "input_accounting": {"input_record_count": 2, "mapped_source_count": 1, "excluded_record_count": 1}}
    return records, manifest


def reseal(document):
    """Recompute the documented checksum independently of the production sealer."""
    unsigned = deepcopy(document)
    for key in ("release", "manifest"):
        unsigned[key].pop("release_id", None)
        unsigned[key].pop("content_sha256", None)
    digest = hashlib.sha256(canonical_json(unsigned).encode()).hexdigest()
    for key in ("release", "manifest"):
        document[key]["content_sha256"] = digest
        document[key]["release_id"] = "release_" + digest[:24]


def test_actual_canonical_payloads_export_with_full_nested_evidence(inputs):
    document = build_export(*inputs)
    assert validate_export(document) == []
    assert document["schema_version"] == "engine-export-v1"
    assert document["scenarios"][0]["needs"][1]["kind"] == "late_need"
    assert document["scenarios"][0]["needs"][1]["status"] == "pending"
    assert document["personas"][0]["emotional_expression"]["observations"][0]["mode"] == "explicit_self_report"
    assert len(document["evidence"]) == 4
    assert all(set(("quote", "role", "speaker", "authority", "source_id", "object_id", "path")) <= row.keys() for row in document["evidence"])
    assert len(document["variants"]) == len(document["families"]) == 2
    assert document["source_coverage"][0]["valid_component_count"] == 2
    assert document["release"]["review_status"] == "unreviewed"
    assert document["release"]["eligibility_status"] == "requires_client_guidance"
    assert document["release"]["client_ready"] is False
    assert document["release"]["semantic_acceptance"] is False
    assert document["release"]["platform_adapter_status"] == "unverified"


def test_export_is_detached_deterministic_and_content_addressed(inputs):
    before = deepcopy(inputs)
    first = build_export(*inputs)
    second = build_export(*inputs)
    assert first == second
    assert inputs == before
    first["scenarios"][0]["needs"][0]["summary"] = "Changed draft."
    assert inputs == before
    assert any("content digest mismatch" in error for error in validate_export(first))
    assert first["release"]["content_sha256"] == second["manifest"]["content_sha256"]
    changed_records = deepcopy(inputs[0])
    changed_records["issues"].append({"code": "new_limit", "message": "New documented output limit."})
    assert build_export(changed_records, inputs[1])["release"]["release_id"] != second["release"]["release_id"]


def test_variant_integral_prevalence_roundtrip_preserves_native_release_identity(inputs):
    document = build_export(*inputs)
    native_identity = deepcopy(document["release"])
    assert all(type(row["prevalence"]["fraction"]) is float for row in document["families"])
    # The retained native thin run returned 1 for a ratio originally sealed as
    # 1.0. JSON itself retains the distinction; VARIANT storage removed it.
    retrieved = json.loads(json.dumps(document))
    for family in retrieved["families"]:
        assert family["prevalence"]["fraction"] == 1.0
        family["prevalence"]["fraction"] = 1
    before = deepcopy(retrieved)
    assert validate_export(retrieved) == []
    assert retrieved == before
    assert retrieved["release"] == native_identity
    assert retrieved["manifest"]["source_hashes"] == document["manifest"]["source_hashes"]
    retrieved["families"][0]["prevalence"]["fraction"] = 0
    assert any("content digest mismatch" in error for error in validate_export(retrieved))


def test_nonintegral_prevalence_remains_content_addressed(inputs):
    records, manifest = inputs
    records.update(aggregate(records["components"], None, None,
                             {"source_digests": manifest["source_hashes"],
                              "expected_sources": ["source-1", {"source_id": "failed-source", "status": "failed"}]}))
    manifest["status"] = "partial"
    document = build_export(records, manifest)
    assert all(row["prevalence"]["fraction"] == 0.5 for row in document["families"])
    assert validate_export(json.loads(json.dumps(document))) == []
    document["families"][0]["prevalence"]["fraction"] = 0.6
    errors = validate_export(document)
    assert any("content digest mismatch" in error for error in errors)
    assert any("prevalence must count" in error for error in errors)


@pytest.mark.parametrize("fraction", [True, "1"])
def test_prevalence_does_not_coerce_boolean_or_string_even_after_rehash(inputs, fraction):
    document = build_export(*inputs)
    document["families"][0]["prevalence"]["fraction"] = fraction
    reseal(document)
    assert any("prevalence must count" in error for error in validate_export(document))


def test_order_of_input_inventory_does_not_change_release(inputs):
    records, manifest = inputs
    reordered = deepcopy(records)
    for value in reordered.values():
        if isinstance(value, list):
            value.reverse()
    assert build_export(records, manifest) == build_export(reordered, manifest)


def test_originals_and_raw_mapping_records_never_leave_storage(inputs):
    records, manifest = inputs
    records["issues"].append({"raw_id": "broken-raw", "code": "malformed_record", "reason": "Invalid row.",
                              "raw_record": {"text": "SECRET-RAW-RECORD"}})
    manifest["debug"] = {"raw_record": "SECRET-MANIFEST-RAW", "original_text": "SECRET-MANIFEST-ORIGINAL"}
    document = build_export(records, manifest)
    serialized = json.dumps(document)
    assert "SECRET-" not in serialized
    assert "original_text" not in document["sources"][0]["events"][0]
    assert "raw_record" not in document["issues"][0]
    assert document["manifest"]["source_hashes"] == manifest["source_hashes"]
    assert document["manifest"]["export_source_hashes"]["source-1"] != manifest["source_hashes"]["source-1"]
    assert document["manifest"]["input_accounting"]["excluded_record_count"] == 1


def test_actual_mapping_orphan_raw_source_is_withheld_without_losing_issue_accounting(inputs):
    records, manifest = inputs
    root = Path(__file__).resolve().parents[2]
    mapping = json.loads((root / "configs/engine/synthetic_mapping.json").read_text())
    raw = json.loads((root / "fixtures/engine/synthetic_sources.json").read_text())
    orphan = next(row for row in raw if row["record_id"] == "raw-orphan-note")
    marker = "UNREDACTED-ORPHAN-RAW-SOURCE-MUST-STAY-IN-STORAGE"
    orphan["body"] = marker
    mapped = normalize([orphan], mapping, "client-1")
    assert mapped["sources"] == []
    assert any(issue["code"] == "unlinked_record" for issue in mapped["issues"])
    assert all(issue["raw_source"]["body"] == marker for issue in mapped["issues"])
    records["issues"].extend(mapped["issues"])
    manifest["input_accounting"]["records"] = mapped["accounting"]["records"]
    manifest["storage_debug"] = {"raw_records": [orphan]}

    document = build_export(records, manifest)
    assert marker not in json.dumps(document)
    assert document["accounting"]["issue_count"] == len(mapped["issues"])
    assert {(issue["raw_id"], issue["record_index"], issue["code"]) for issue in document["issues"]} == {
        (issue["raw_id"], issue["record_index"], issue["code"]) for issue in mapped["issues"]}
    assert document["manifest"]["input_accounting"]["records"] == mapped["accounting"]["records"]
    assert all("raw_source" not in issue and "raw_record" not in issue for issue in document["issues"])
    assert "raw_records" not in document["manifest"]["storage_debug"]
    assert validate_export(document) == []


@pytest.mark.parametrize("field", ["raw_source", "raw_records"])
def test_validator_rejects_injected_raw_source_custody_fields_even_after_rehash(inputs, field):
    document = build_export(*inputs)
    document["manifest"]["storage_debug"] = {"nested": [{field: {"body": "UNREDACTED-INJECTED"}}]}
    reseal(document)
    assert any(field in error and "storage-only" in error for error in validate_export(document))


@pytest.mark.parametrize("mutate, fragment", [
    (lambda d: d.update(schema_version="invented-v2"), "schema_version"),
    (lambda d: d["manifest"]["versions"].update(prompt=None), "versions.prompt"),
    (lambda d: d["release"].update(client_ready=True), "client_ready"),
    (lambda d: d["release"].update(semantic_acceptance=True), "semantic_acceptance"),
    (lambda d: d["release"].update(platform_adapter_status="verified"), "platform_adapter_status"),
    (lambda d: d["guidance"][0].update(status="client_approved"), "fictional guidance"),
    (lambda d: d["scenarios"][0].update(source_id="missing"), "unknown or invalid source_id"),
    (lambda d: d["scenarios"][0]["needs"][0].update(status="invented"), "invalid canonical label"),
    (lambda d: d["scenarios"][0]["needs"][0].update(related_ids=["missing"]), "related_id"),
    (lambda d: d["scenarios"][0]["needs"][0]["evidence"][0].update(quote="Not said."), "exact permitted source span"),
    (lambda d: d["scenarios"][0]["needs"][0]["evidence"][0].update(authority="client_approved"), "attribution mismatch"),
    (lambda d: d["personas"][0].update(emotional_expression=[]), "wrong type"),
    (lambda d: d["families"][0]["prevalence"].update(unique_source_count=True), "nonnegative integer"),
    (lambda d: d["families"][0]["variants"].clear(), "preserve every member"),
    (lambda d: d["families"][0]["lineage"][0].update(source_digest="0" * 64), "source digest differs"),
    (lambda d: d["compatibility"][0].update(persona_id="missing"), "unknown component"),
    (lambda d: d["compatibility"][0]["evidence"].clear(), "both endpoints"),
    (lambda d: d["source_coverage"].clear(), "every canonical source"),
    (lambda d: d["source_coverage"][0].update(component_ids=[]), "omit or invent"),
    (lambda d: d["evidence"].pop(), "every nested citation"),
    (lambda d: d["sources"][0]["events"][0].update(original_text="exposed"), "storage-only"),
])
def test_independent_validator_rejects_real_contract_errors_even_after_rehash(inputs, mutate, fragment):
    document = build_export(*inputs)
    mutate(document)
    reseal(document)
    assert any(fragment in error for error in validate_export(document)), validate_export(document)


def test_failed_and_quarantined_outputs_remain_accounted_for(inputs):
    records, manifest = inputs
    quarantine = {"object_id": "quarantine-1", "source_id": "source-1", "kind": "persona",
                  "payload": {"malformed": "retained"}, "validation_status": "quarantined", "issues": ["Missing speaker."]}
    records["components"].append(quarantine)
    manifest["status"] = "partial"
    aggregate_result = aggregate(records["components"], None, None,
                                 {"source_digests": manifest["source_hashes"],
                                  "expected_sources": ["source-1", {"source_id": "failed-source", "status": "failed"}]})
    records.update(aggregate_result)
    records["issues"].append({"source_id": "failed-source", "code": "source_failed", "message": "Input limit."})
    document = build_export(records, manifest)
    assert validate_export(document) == []
    assert document["quarantined_components"][0]["payload"] == {"malformed": "retained"}
    assert document["accounting"]["quarantined_component_count"] == 1
    assert document["accounting"]["coverage_status_counts"]["failed"] == 1
    assert document["run_status"]["status"] == "partial"
    assert all(f["prevalence"]["all_source_denominator"] == 2 for f in document["families"])


def test_approved_guidance_still_requires_semantic_review(inputs):
    records, manifest = inputs
    records["sources"][0]["guidance"][0]["authority"] = "client_approved"
    records["guidance"][0]["authority"] = "client_approved"
    manifest["source_hashes"]["source-1"] = source_hash(records["sources"][0])
    records.update(aggregate(records["components"], None, None,
                             {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}))
    document = build_export(records, manifest)
    assert document["release"]["eligibility_status"] == "requires_semantic_review"
    assert document["release"]["client_ready"] is False
    assert document["scenarios"][0]["review_status"] == "unreviewed"


def multiparty_inputs(inputs):
    """Retain distinct participants whose source roles are not named customer."""
    records, manifest = deepcopy(inputs)
    source = records["sources"][0]
    source["metadata"]["customer_side_speaker_ids"] = ["peer-a", "peer-b"]
    for event, speaker in zip(source["events"], ["peer-a", "peer-b", "peer-a"]):
        event.update(role="negotiation_peer", speaker=speaker)
    events = {event["event_id"]: event for event in source["events"]}

    def refresh_refs(value):
        if isinstance(value, dict):
            if all(key in value for key in ("event_id", "quote", "role", "speaker", "authority")):
                event = events[value["event_id"]]
                value.update({key: event[key] for key in ("role", "speaker", "authority")})
            else:
                for child in value.values():
                    refresh_refs(child)
        elif isinstance(value, list):
            for child in value:
                refresh_refs(child)
    refresh_refs(records["components"])
    scenario, persona = records["components"]
    scenario["payload"]["needs"].append({"id": "peer-b-need", "kind": "secondary_need",
                                          "summary": "The second participant asks for confirmation.",
                                          "status": "requested", "related_ids": [],
                                          "evidence": [citation(source["events"][1])]})
    persona["payload"]["speaker"] = "peer-a"
    other = deepcopy(persona)
    other["object_id"] = "persona-2"
    other["payload"].update(id="local-persona-2", speaker="peer-b",
                              actions=[{"id": "peer-b-action", "function": "check_information",
                                        "summary": "Asks for account confirmation.",
                                        "evidence": [citation(source["events"][1])]}],
                              emotional_expression={"status": "no_expression_detected", "observations": []})
    records["components"].append(other)
    manifest["source_hashes"][source["source_id"]] = source_hash(source)
    records.update(aggregate(records["components"], None, None,
                             {"source_digests": manifest["source_hashes"], "expected_sources": [source["source_id"]],
                              "max_candidate_pairs": 0}))
    return records, manifest


def test_compatibility_accepts_two_designated_peers_with_original_roles(inputs):
    document = build_export(*multiparty_inputs(inputs))
    assert validate_export(document) == []
    assert {row["speaker"] for row in document["compatibility"]} == {"peer-a", "peer-b"}
    for row in document["compatibility"]:
        assert all(ref["role"] == "negotiation_peer" and ref["speaker"] == row["speaker"] for ref in row["evidence"])


def test_compatibility_rejects_exact_citation_from_other_designated_peer(inputs):
    document = build_export(*multiparty_inputs(inputs))
    peer_a = next(row for row in document["compatibility"] if row["speaker"] == "peer-a")
    peer_b = next(row for row in document["compatibility"] if row["speaker"] == "peer-b")
    # This exact scenario citation belongs to the endpoint, but to the other peer.
    foreign_ref = next(ref for ref in peer_b["evidence"] if ref["object_id"] == "scenario-1")
    peer_a["evidence"].append(deepcopy(foreign_ref))
    reseal(document)
    assert any("same source-designated customer-side speaker" in error for error in validate_export(document))


@pytest.mark.parametrize("role, kind", [("agent", "message"), ("system", "message"), ("negotiation_peer", "note")])
def test_compatibility_never_upgrades_agent_system_or_note_events(inputs, role, kind):
    records, manifest = multiparty_inputs(inputs)
    source = records["sources"][0]
    event = source["events"][0]
    event.update(role=role, kind=kind)
    for component in records["components"]:
        def change_refs(value):
            if isinstance(value, dict):
                if value.get("event_id") == event["event_id"] and "quote" in value:
                    value["role"] = role
                else:
                    for child in value.values():
                        change_refs(child)
            elif isinstance(value, list):
                for child in value:
                    change_refs(child)
        change_refs(component["payload"])
    manifest["source_hashes"][source["source_id"]] = source_hash(source)
    records.update(aggregate(records["components"], None, None,
                             {"source_digests": manifest["source_hashes"], "expected_sources": [source["source_id"]],
                              "max_candidate_pairs": 0}))
    with pytest.raises(ValueError, match="same source-designated customer-side speaker"):
        build_export(records, manifest)


def test_original_source_hash_mismatch_is_rejected_before_projection(inputs):
    records, manifest = inputs
    manifest["source_hashes"]["source-1"] = "0" * 64
    with pytest.raises(ValueError, match="source_hashes"):
        build_export(records, manifest)


@pytest.mark.parametrize("value", [None, [], {}, {"sources": [None]}, {"sources": [{}]},
                                 {"release": [], "manifest": []}, {"bad": float("nan")}])
def test_malformed_json_shapes_return_errors(value):
    result = validate_export(value)
    assert isinstance(result, list) and result
    assert all(isinstance(error, str) for error in result)
