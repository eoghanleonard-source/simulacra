"""Offline portable-version and original-reader regressions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_export import inputs, reseal
from snowflake_engine.aggregation import aggregate
from snowflake_engine.export import build_export, validate_export
from snowflake_engine.versioning import annotate, VERSION_KEYS


def version_set():
    return {key: ("engine-export-v2" if key == "export" else
                  "engine-extraction-schema-v1" if key == "extraction_contract" else
                  {"temperature": 0} if key == "model_settings" else "test-" + key)
            for key in VERSION_KEYS}


@pytest.fixture
def v2_inputs(inputs):
    records, manifest = inputs
    versions = version_set()
    records["components"] = [annotate(row, row["kind"], versions,
        ["client-1", row["source_id"], row["kind"], row["payload"]["id"]]) for row in records["components"]]
    records.update(aggregate(records["components"], None, None,
        {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}))
    manifest["version_set"] = versions
    manifest["versions"] = deepcopy(versions)
    return records, manifest


def test_export_v2_has_exact_stage_pins_and_retains_original_endpoint_ids(v2_inputs):
    before = deepcopy(v2_inputs)
    document = build_export(*v2_inputs)
    assert validate_export(document) == []
    assert v2_inputs == before
    assert document["schema_version"] == "engine-export-v2"
    assert document["release"]["version"] == 2
    for name in ("sources", "scenarios", "personas", "families", "variants", "compatibility", "evidence", "source_coverage", "guidance"):
        for row in document[name]:
            assert row["version_set"] == document["manifest"]["version_set"]
            assert row["logical_id"].startswith("logical-")
            assert row["revision_id"].startswith("revision-")
    assert {row["object_id"] for row in document["scenarios"] + document["personas"]} == {"scenario-1", "persona-1"}
    assert document["scenarios"][0]["storage_revision_id"] == next(row["revision_id"] for row in v2_inputs[0]["components"] if row["kind"] == "scenario")
    assert document["compatibility"][0]["scenario_id"] == "scenario-1"


@pytest.mark.parametrize("mutate, fragment", [
    (lambda d: d["personas"][0].update(revision_id="revision-" + "0" * 64), "revision_id"),
    (lambda d: d["families"][0]["lineage"][0].update(revision_id="revision-" + "0" * 64), "endpoint version identity"),
    (lambda d: d["families"][0]["lineage"][0].update(component_digest="0" * 64), "component digest differs"),
    (lambda d: d["evidence"][0]["version_set"].update(selection="wrong"), "exact version set"),
    (lambda d: d["manifest"]["version_set"].pop("model_settings"), "version_set missing model_settings"),
    (lambda d: d["manifest"]["version_set"].update(unregistered="x"), "exact stage keys"),
    (lambda d: d["scenarios"][0]["needs"][0]["evidence"][0].update(quote="Not said."), "exact permitted source span"),
    (lambda d: d["personas"][0]["emotional_expression"]["observations"][0].update(labels=["invented"]), "invalid canonical label"),
])
def test_versioned_reader_rejects_tamper_after_reseal(v2_inputs, mutate, fragment):
    document = build_export(*v2_inputs)
    mutate(document)
    reseal(document)
    assert any(fragment in error for error in validate_export(document)), validate_export(document)


def test_v2_variant_roundtrip_preserves_release_and_portable_revision(v2_inputs):
    document = build_export(*v2_inputs)
    retrieved = json.loads(json.dumps(document))
    for family in retrieved["families"]:
        family["prevalence"]["fraction"] = int(family["prevalence"]["fraction"])
    assert validate_export(retrieved) == []
    assert retrieved["release"] == document["release"]


def test_writer_rejects_missing_export_pin(v2_inputs):
    v2_inputs[1]["version_set"]["export"] = "engine-export-v1"
    with pytest.raises(ValueError, match="export must pin"):
        build_export(*v2_inputs)


def test_v1_reader_remains_detached_and_stable(inputs):
    first = build_export(*inputs)
    frozen = json.dumps(first, sort_keys=True, separators=(",", ":"))
    assert validate_export(first) == []
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == frozen
    assert build_export(*inputs) == first
    assert first["schema_version"] == "engine-export-v1"
    assert all("logical_id" not in row for row in first["scenarios"])


def test_v1_cannot_silently_add_practice(inputs):
    records, manifest = inputs
    records["practice"] = {}
    with pytest.raises(ValueError, match="practice requires"):
        build_export(records, manifest)


def v3_records(inputs, invalid_emotion=False):
    from snowflake_engine.contracts import CAPTURE_V3_VERSIONS
    from snowflake_engine.extraction import parse_response_v3
    records, manifest = deepcopy(inputs)
    versions = version_set()
    versions["extraction_contract"] = CAPTURE_V3_VERSIONS["schema"]
    source = records["sources"][0]
    assertion = {"actor": "customer-a", "recipient": "agent-a", "action": "Requests access.",
                 "scope": "Existing account; preserve files.", "state": "requested", "certainty": "explicit"}
    scenario, persona = records["components"]
    for need in scenario["payload"]["needs"]:
        need["assertion"] = deepcopy(assertion)
    persona["payload"]["actions"][0]["assertion"] = deepcopy(assertion)
    if invalid_emotion:
        persona["payload"]["emotional_expression"]["observations"][0]["labels"] = ["unsupported-invented-emotion"]
    parsed = []
    for kind, component in (("scenario", scenario), ("persona", persona)):
        envelope = {"scenarios" if kind == "scenario" else "personas": [component["payload"]],
                    "capture_status": "complete", "read_receipt": {"complete": True,
                    "last_event_id": source["events"][-1]["event_id"]}}
        parsed.extend(parse_response_v3(source, json.dumps(envelope), kind)["components"])
    records["components"] = [annotate(row, row["kind"], versions, [row["object_id"]]) for row in parsed]
    records.update(aggregate(records["components"], None, None,
        {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}))
    manifest.update(version_set=versions, versions=deepcopy(versions), status="partial" if invalid_emotion else "complete")
    return records, manifest


def test_v3_assertions_and_partial_persona_are_explicit_and_raw_failure_retained(inputs):
    records, manifest = v3_records(inputs, invalid_emotion=True)
    original = next(row for row in records["components"] if row["validation_status"] == "quarantined")
    original_payload = deepcopy(original["payload"])
    document = build_export(records, manifest)
    assert validate_export(document) == []
    assert len(document["scenarios"]) == len(document["personas"]) == len(document["quarantined_components"]) == 1
    delivered = document["personas"][0]
    assert delivered["delivery_scope"] == "validated_children_only"
    assert delivered["partial_state"] == "partial"
    assert delivered["emotional_expression"] == {"status": "unknown", "observations": []}
    assert delivered["actions"]
    assert delivered["withheld_layers"]
    assert document["quarantined_components"][0]["payload"] == original_payload
    assert original_payload["emotional_expression"]["observations"][0]["labels"] == ["unsupported-invented-emotion"]


@pytest.mark.parametrize("mutate, fragment", [
    (lambda d: d["personas"][0].update(projection_of="missing"), "same-source quarantined persona"),
    (lambda d: d["personas"][0].update(withheld_layers=[]), "withheld layers"),
    (lambda d: d["quarantined_components"][0]["child_records"][0].update(validation_status="quarantined"), "child validation records"),
    (lambda d: d["personas"][0]["actions"][0]["assertion"].update(actor="invented"), "unknown source speaker"),
    (lambda d: d["personas"][0]["actions"][0]["assertion"].update(state="source_recorded_completed"), "cited source action record"),
])
def test_v3_reader_rejects_partial_or_assertion_tamper(inputs, mutate, fragment):
    document = build_export(*v3_records(inputs, invalid_emotion=True))
    mutate(document)
    reseal(document)
    assert any(fragment in error for error in validate_export(document)), validate_export(document)


def test_original_full_release_reader_accepts_without_modifying_any_bytes():
    import hashlib
    path = Path(__file__).resolve().parents[2] / "artifacts/demo/source-grounded-snowflake-01/evidence/full-export.json"
    if not path.exists():
        pytest.skip("Retained development baseline is not included in source package")
    before = path.read_bytes()
    document = json.loads(before)
    frozen = deepcopy(document)
    assert validate_export(document) == []
    assert document == frozen
    assert path.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == "82f93640c8f7e4a9ca7412ba543e428d32f2c86a87e996cf24dafe1068c9a489"


def with_practice(v2_inputs):
    from snowflake_engine.practice import build_programme
    records, manifest = deepcopy(v2_inputs)
    def planner(messages, operation, request_key):
        return json.dumps({"groups": [{"label": "Restore access while preserving files", "member_ids": ["scenario-1"],
            "candidates": [{"situation_ref": {"object_id": "scenario-1", "need_id": "primary"},
                            "variant_ref": {"object_id": "scenario-1", "need_id": "late"},
                            "consequential_need_refs": []}]}], "unassigned_object_ids": []})
    records["practice"] = build_programme(records["components"], records, planner,
        {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}, manifest["version_set"])
    return records, manifest


def test_v2_practice_roundtrip_retains_exact_source_payloads_and_versions(v2_inputs):
    records, manifest = with_practice(v2_inputs)
    assert records["practice"]["assignments"]
    document = build_export(records, manifest)
    assert validate_export(document) == []
    assert document["practice"] == records["practice"]
    assert document["practice"]["assignments"][0]["situation"]["payload"] == records["components"][0]["payload"]


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p["assignments"][0]["situation"]["payload"].update(summary="Invented handling"), "unchanged source component"),
    (lambda p: p["assignments"][0].update(assessment_eligible=True), "assessment eligibility"),
    (lambda p: p["object_accounting"].pop(), "every retained component"),
    (lambda p: p["practice_groups"][0]["version_set"].update(selection="invented"), "exact version set"),
])
def test_practice_tamper_is_rejected_even_after_release_reseal(v2_inputs, mutate, fragment):
    document = build_export(*with_practice(v2_inputs))
    mutate(document["practice"])
    reseal(document)
    assert any(fragment in error for error in validate_export(document)), validate_export(document)


def test_version_settings_integral_float_roundtrip_does_not_change_revision_or_release(v2_inputs):
    records, manifest = v2_inputs
    # A native version setting has numeric meaning, while arbitrary source data
    # and original source commitments keep their exact representations.
    manifest["version_set"]["model_settings"]["temperature"] = 0.0
    manifest["versions"] = deepcopy(manifest["version_set"])
    document = build_export(records, manifest)
    retrieved = deepcopy(document)
    def roundtrip(value):
        if isinstance(value, dict):
            return {key: roundtrip(child) for key, child in value.items()}
        if isinstance(value, list):
            return [roundtrip(child) for child in value]
        if type(value) is float and value.is_integer():
            return int(value)
        return value
    retrieved = roundtrip(retrieved)
    assert validate_export(retrieved) == []
    assert retrieved["release"] == document["release"]


def test_partial_delivery_accounting_does_not_claim_a_full_valid_persona(inputs):
    document = build_export(*v3_records(inputs, invalid_emotion=True))
    assert document["accounting"]["partial_persona_delivery_count"] == 1
    assert document["accounting"]["full_valid_persona_count"] == 0
    assert document["accounting"]["full_valid_component_count"] == 1
    assert document["accounting"]["partial_delivery_count"] == 1


@pytest.mark.parametrize("target,value", [("child_records", [1]), ("child_accounting", []), ("withheld_layers", {}), ("partial_state", [])])
def test_malformed_candidate_metadata_returns_errors_instead_of_throwing(inputs, target, value):
    document = build_export(*v3_records(inputs, invalid_emotion=True))
    document["quarantined_components"][0][target] = value
    assert validate_export(document)


def test_unknown_component_contract_is_rejected_even_when_all_rows_and_seal_are_recomputed(v2_inputs):
    records, manifest = deepcopy(v2_inputs)
    manifest["version_set"]["extraction_contract"] = "undeclared-contract"
    manifest["versions"] = deepcopy(manifest["version_set"])
    with pytest.raises(ValueError, match="unsupported component contract"):
        build_export(records, manifest)


def test_version_normalization_does_not_normalize_arbitrary_source_metadata(v2_inputs):
    from snowflake_engine.contracts import source_hash
    records, manifest = deepcopy(v2_inputs)
    source = records["sources"][0]
    source["metadata"]["versions"] = {"observed_number": 1.0}
    manifest["source_hashes"][source["source_id"]] = source_hash(source)
    records.update(aggregate(records["components"], None, None,
        {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}))
    document = build_export(records, manifest)
    document["sources"][0]["metadata"]["versions"]["observed_number"] = 1
    assert any("content digest mismatch" in error for error in validate_export(document))
    assert any("exported source digest mismatch" in error for error in validate_export(document))


def candidate_inputs(v2_inputs):
    records, manifest = deepcopy(v2_inputs)
    original = next(row for row in records["components"] if row["kind"] == "scenario")
    second = {key: deepcopy(value) for key, value in original.items()
              if key not in {"logical_id", "revision_id", "version_set"}}
    second["object_id"] = "scenario-2"
    second["payload"]["id"] = "local-scenario-2"
    records["components"].append(annotate(second, "scenario", manifest["version_set"],
        ["client-1", second["source_id"], "scenario", second["payload"]["id"]]))
    # Exercise real candidate nomination. Identical vectors produce cosine 1.0;
    # the default aggregation policy retains separate source-specific objects.
    records.update(aggregate(records["components"], lambda texts, key: [[1.0, 0.0] for _ in texts], None,
        {"source_digests": manifest["source_hashes"], "expected_sources": ["source-1"]}))
    assert len(records["candidates"]) == 1
    assert type(records["candidates"][0]["similarity"]) is float
    assert records["candidates"][0]["similarity"] == 1.0
    return records, manifest


def test_real_candidate_integral_similarity_roundtrip_preserves_seal_and_revision(v2_inputs):
    records, manifest = candidate_inputs(v2_inputs)
    document = build_export(records, manifest)
    retrieved = deepcopy(document)
    retrieved["candidates"][0]["similarity"] = 1
    before_validation = deepcopy(retrieved)
    assert validate_export(retrieved) == []
    assert retrieved == before_validation
    assert retrieved["release"] == document["release"]
    assert retrieved["candidates"][0]["revision_id"] == document["candidates"][0]["revision_id"]
    assert retrieved["manifest"]["source_hashes"] == manifest["source_hashes"]
    # Reading an integer-valued native candidate also yields the same portable
    # revision and release when the writer receives it directly.
    records["candidates"][0]["similarity"] = 1
    assert build_export(records, manifest) == document


@pytest.mark.parametrize("score", [True, "1", 2])
def test_candidate_similarity_normalisation_does_not_accept_invalid_score_types(v2_inputs, score):
    document = build_export(*candidate_inputs(v2_inputs))
    document["candidates"][0]["similarity"] = score
    reseal(document)
    assert any("finite cosine score required" in error for error in validate_export(document))


def test_candidate_similarity_meaning_change_still_invalidates_seal_and_revision(v2_inputs):
    document = build_export(*candidate_inputs(v2_inputs))
    document["candidates"][0]["similarity"] = 0.5
    errors = validate_export(document)
    assert any("content digest mismatch" in error for error in errors)
    assert any("revision_id: does not identify retained portable content" in error for error in errors)


@pytest.fixture
def retained_pilot_export():
    path = Path(__file__).resolve().parents[2] / "artifacts/demo/source-grounded-improvement-01/evidence/pilot-export-raw.json"
    if not path.exists():
        pytest.skip("Retained live pilot is not included in the portable source package")
    return path, path.read_bytes()


def test_retained_native_pilot_validates_with_original_release_and_file_unchanged(retained_pilot_export):
    import hashlib
    path, raw = retained_pilot_export
    document = json.loads(raw)
    before = deepcopy(document)
    assert document["release"]["release_id"] == "release_a88e81cb3d46fbf22e4db704"
    assert document["release"]["content_sha256"] == "a88e81cb3d46fbf22e4db704bdd0dc5db02465c43393d65d28c1da8ac4f9097d"
    assert validate_export(document) == []
    assert document == before
    assert path.read_bytes() == raw
    assert hashlib.sha256(raw).hexdigest() == "473f6dfc72e0a2a347bf242fcfb79107c1da7db2da2026d31765675afa3f7587"


@pytest.mark.parametrize("root", ["manifest", "run_status"])
@pytest.mark.parametrize("field,value", [("limit", 15), ("limit", True), ("limit", "16"),
                                          ("output_rate", 8), ("output_rate", True), ("output_rate", "9")])
def test_pilot_budget_or_rate_meaning_and_type_tamper_still_rejected(retained_pilot_export, root, field, value):
    _, raw = retained_pilot_export
    document = json.loads(raw)
    if field == "limit":
        document[root]["limits"]["max_generation_ai_credits"] = value
    else:
        document[root]["usage"]["generation_credit_estimate"]["rates_ai_credits_per_million"]["output"] = value
    assert any("content digest mismatch" in error for error in validate_export(document))


@pytest.mark.parametrize("value", [16.0, 15, True, "16"])
def test_installed_budget_representation_is_not_coerced(retained_pilot_export, value):
    _, raw = retained_pilot_export
    document = json.loads(raw)
    assert type(document["manifest"]["installed_budget"]["max_generation_ai_credits"]) is int
    document["manifest"]["installed_budget"]["max_generation_ai_credits"] = value
    assert any("content digest mismatch" in error for error in validate_export(document))


def test_writer_and_reader_share_only_the_four_metadata_normalisations(v2_inputs):
    records, manifest = deepcopy(v2_inputs)
    manifest["limits"]["max_generation_ai_credits"] = 16.0
    manifest["installed_budget"] = {"max_generation_ai_credits": 16}
    manifest["usage"]["generation_credit_estimate"] = {
        "estimated_ai_credits": 0.125, "rates_ai_credits_per_million": {"input": 1.8, "output": 9.0}}
    document = build_export(records, manifest)
    retrieved = deepcopy(document)
    for root in ("manifest", "run_status"):
        retrieved[root]["limits"]["max_generation_ai_credits"] = 16
        retrieved[root]["usage"]["generation_credit_estimate"]["rates_ai_credits_per_million"]["output"] = 9
    assert validate_export(retrieved) == []
    assert retrieved["release"] == document["release"]
    assert retrieved["manifest"]["source_hashes"] == document["manifest"]["source_hashes"]
    manifest["limits"]["max_generation_ai_credits"] = 16
    manifest["usage"]["generation_credit_estimate"]["rates_ai_credits_per_million"]["output"] = 9
    assert build_export(records, manifest)["release"] == document["release"]


def test_metadata_number_normalisation_does_not_change_legacy_v1_hashing(inputs):
    records, manifest = deepcopy(inputs)
    manifest["limits"]["max_generation_ai_credits"] = 16.0
    manifest["usage"]["generation_credit_estimate"] = {"rates_ai_credits_per_million": {"output": 9.0}}
    first = build_export(records, manifest)
    manifest["limits"]["max_generation_ai_credits"] = 16
    manifest["usage"]["generation_credit_estimate"]["rates_ai_credits_per_million"]["output"] = 9
    second = build_export(records, manifest)
    assert first["schema_version"] == second["schema_version"] == "engine-export-v1"
    assert first["release"]["release_id"] != second["release"]["release_id"]
    assert validate_export(first) == validate_export(second) == []
