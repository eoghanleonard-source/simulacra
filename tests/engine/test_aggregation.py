"""Synthetic contract tests, not model semantic-quality or client acceptance tests."""
import copy
import json

import pytest

from snowflake_engine.aggregation import aggregate


def citation(quote="Please refund this", speaker="customer-1", event_id="event-1"):
    return {"event_id": event_id, "quote": quote, "speaker": speaker,
            "role": "customer", "authority": "customer_statement"}


def scenario(object_id, source_id, summary="Refund requested", needs=None):
    return {"object_id": object_id, "source_id": source_id, "kind": "scenario",
            "payload": {"id": object_id, "summary": summary, "unknowns": [], "needs": needs or [
                {"id": "need-1", "kind": "primary_need", "summary": summary,
                 "status": "pending", "related_ids": [], "evidence": [citation()]}]},
            "validation_status": "valid", "issues": []}


def persona(object_id, source_id, speaker="customer-1"):
    return {"object_id": object_id, "source_id": source_id, "kind": "persona",
            "payload": {"id": object_id, "speaker": speaker, "evidence_status": "observed",
                        "actions": [{"id": "action-1", "function": "request_action",
                                     "summary": "Requests refund", "evidence": [citation(speaker=speaker)]}],
                        "sequences": [], "emotional_expression": {"status": "no_expression_detected", "observations": []},
                        "emotional_changes": [], "unknowns": []},
            "validation_status": "valid", "issues": []}


class Model:
    def __init__(self, relation="equivalent", differences=None, confidence="supported", vectors=None):
        self.embedding_calls = []
        self.relationship_calls = []
        self.relation = relation
        self.differences = differences or []
        self.confidence = confidence
        self.vectors = vectors

    def embed(self, texts, request_key):
        self.embedding_calls.append((texts, request_key))
        return self.vectors or [[1.0, 0.0] for _ in texts]

    def complete(self, messages, operation, request_key):
        self.relationship_calls.append((messages, operation, request_key))
        pair = json.loads(messages[1]["content"])
        evidence = []
        for member in pair:
            payload = member["payload"]
            citations = payload["needs"][0]["evidence"] if member["kind"] == "scenario" else payload["actions"][0]["evidence"]
            evidence.append({"object_id": member["object_id"], **citations[0]})
        return json.dumps({"relation": self.relation, "confidence": self.confidence,
                           "evidence": evidence, "material_differences": self.differences,
                           "reason": "Synthetic test judgment"})


def test_embeddings_nominate_separate_inventories_and_default_does_not_merge():
    model = Model()
    components = [scenario("s1", "a"), persona("p1", "a"), scenario("s2", "b"), persona("p2", "b")]
    result = aggregate(components, model.embed, model.complete)
    assert len(model.embedding_calls) == 2
    assert {c["kind"] for c in result["candidates"]} == {"scenario", "persona"}
    assert all(c["semantic_acceptance"] == "unreviewed" for c in result["candidates"])
    assert len(result["families"]) == 4
    assert not model.relationship_calls
    assert all(r["status"] == "unreviewed" for r in result["relations"])


def test_actual_embedding_vectors_control_candidate_nomination():
    model = Model(vectors=[[1, 0], [0, 1], [1, 0]])
    result = aggregate([scenario("a", "x"), scenario("b", "y"), scenario("c", "z")], model.embed, None)
    assert [(c["left_object_id"], c["right_object_id"]) for c in result["candidates"]] == [("a", "c")]


def test_contradictory_material_variants_not_merged_despite_similarity():
    components = [scenario("s1", "a", "Customer wants refund"), scenario("s2", "b", "Customer rejects refund; wants replacement")]
    model = Model(relation="useful_variant", differences=["Refund requested versus refund rejected."])
    result = aggregate(components, model.embed, model.complete, {"relationship_requests": 1})
    assert len(result["candidates"]) == 1
    assert len(result["families"]) == 2
    relation = result["relations"][0]
    assert relation["relation"] == "useful_variant"
    assert relation["status"] == "model_supported"
    assert relation["material_differences"] == model.differences
    assert relation["semantic_review_status"] == "unreviewed"


def test_proposed_equivalence_with_material_difference_and_uncertainty_never_merge():
    for model in [Model(differences=["One needs accessible delivery."]), Model(confidence="uncertain"), Model(relation="uncertain")]:
        result = aggregate([scenario("s1", "a"), scenario("s2", "b")], model.embed, model.complete, {"relationship_requests": 1})
        assert len(result["families"]) == 2
        assert result["relations"][0]["status"] == "uncertain"


def test_complete_link_prevents_transitive_equivalence_and_is_input_order_stable():
    components = [scenario("a", "source-a"), scenario("b", "source-b"), scenario("c", "source-c")]
    model = Model()

    def judge(messages, operation, request_key):
        pair = json.loads(messages[1]["content"])
        model.relation = "distinct" if {m["object_id"] for m in pair} == {"a", "c"} else "equivalent"
        return model.complete(messages, operation, request_key)

    result = aggregate(components, model.embed, judge, {"relationship_requests": 3})
    reverse = aggregate(list(reversed(components)), model.embed, judge, {"relationship_requests": 3})
    assert sorted(len(f["member_ids"]) for f in result["families"]) == [1, 2]
    assert result == reverse
    assert all(set(f["member_ids"]) != {"a", "c"} for f in result["families"])


def test_all_rare_details_and_source_variants_retained_without_mutating_inputs():
    rare = {"id": "late", "kind": "late_need", "summary": "Delivery must avoid stairs",
            "status": "pending", "related_ids": ["need-1"], "evidence": [citation("No stairs please", event_id="late-event")]}
    first = scenario("a", "source-a")
    first["payload"]["needs"].append(rare)
    first["payload"]["unknowns"].append("Delivery outcome unknown")
    components = [first, scenario("b", "source-b")]
    original = copy.deepcopy(components)
    model = Model(relation="useful_variant", differences=["One source has a late accessibility requirement."])
    result = aggregate(components, model.embed, model.complete, {"relationship_requests": 1})
    retained = [v for family in result["families"] for v in family["variants"]]
    assert retained == original
    assert components == original
    assert retained[0]["payload"]["needs"][-1] == rare


def test_equivalence_preserves_unique_source_prevalence_and_failed_denominator():
    components = [scenario("a", "same-source"), scenario("b", "same-source"), scenario("c", "other-source")]
    quarantined = scenario("q", "quarantined-source")
    quarantined["validation_status"] = "quarantined"
    model = Model()
    config = {"relationship_requests": 3, "expected_sources": ["same-source", "other-source", "quarantined-source", {"source_id": "failed-source", "status": "failed"}],
              "source_digests": {"same-source": "digest-1"}}
    result = aggregate(components + [quarantined], model.embed, model.complete, config)
    assert len(result["families"]) == 1
    family = result["families"][0]
    assert len(family["variants"]) == 3
    assert family["prevalence"] == {"unique_source_count": 2, "all_source_denominator": 4,
                                    "denominator_scope": "all_expected_and_observed_sources",
                                    "kind_valid_source_denominator": 2, "fraction": 0.5}
    coverage = {row["source_id"]: row for row in result["coverage"]}
    assert coverage["failed-source"]["status"] == "failed"
    assert coverage["quarantined-source"]["quarantined_component_count"] == 1
    assert coverage["quarantined-source"]["status"] == "quarantined"
    assert family["lineage"][0]["source_digest"] == "digest-1"
    assert all(row["training_selection_status"] == "not_selected_by_aggregation" for row in result["coverage"])


def test_compatibility_requires_same_source_and_customer_speaker_evidence():
    components = [scenario("s-a", "a"), scenario("s-b", "b"), persona("p-a", "a"), persona("p-b", "b"), persona("p-other", "a", "other-customer")]
    insufficient = persona("p-insufficient", "a")
    insufficient["payload"]["actions"] = []
    insufficient["payload"]["evidence_status"] = "insufficient_evidence"
    components.append(insufficient)
    result = aggregate(components, Model().embed, None)
    assert {(link["scenario_id"], link["persona_id"]) for link in result["compatibility"]} == {("s-a", "p-a"), ("s-b", "p-b")}
    assert all(link["evidence"] and link["review_status"] == "unreviewed" for link in result["compatibility"])


def test_fabricated_or_one_sided_relationship_evidence_is_not_accepted():
    components = [scenario("a", "x"), scenario("b", "y")]
    model = Model()

    def fabricated(messages, operation, request_key):
        answer = json.loads(model.complete(messages, operation, request_key))
        answer["evidence"][1]["quote"] = "Invented quote"
        return json.dumps(answer)

    result = aggregate(components, model.embed, fabricated, {"relationship_requests": 1})
    assert len(result["families"]) == 2
    assert result["relations"][0]["status"] == "uncertain"
    assert "exact retained citation" in result["relations"][0]["reason"]

    def one_sided(messages, operation, request_key):
        answer = json.loads(model.complete(messages, operation, request_key))
        answer["evidence"] = answer["evidence"][:1]
        return json.dumps(answer)

    result = aggregate(components, model.embed, one_sided, {"relationship_requests": 1})
    assert len(result["families"]) == 2


def test_request_caps_embedding_bounds_and_no_retry_on_failure():
    model = Model()
    components = [scenario(str(i), "source-" + str(i)) for i in range(8)]
    result = aggregate(components, model.embed, model.complete, {"relationship_requests": 4, "max_embedding_inputs": 5})
    assert len(model.relationship_calls) == 4
    assert sum(len(call[0]) for call in model.embedding_calls) == 5
    assert any(issue["code"] == "embedding_input_limit" for issue in result["issues"])
    assert sum(len(f["variants"]) for f in result["families"]) == 8
    with pytest.raises(ValueError, match="relationship_requests"):
        aggregate(components, model.embed, model.complete, {"relationship_requests": 21})

    attempts = []

    def fail(texts, request_key):
        attempts.append(request_key)
        raise RuntimeError("Embedding budget exhausted")

    failed = aggregate(components, fail, model.complete)
    assert len(attempts) == 1
    assert len(failed["families"]) == 8
    assert failed["issues"][0]["code"] == "embedding_failed"


def test_bad_vectors_and_failed_model_response_are_visible_without_merge():
    model = Model(vectors=[[1, 0], [float("nan"), 0]])
    failed = aggregate([scenario("a", "x"), scenario("b", "y")], model.embed, model.complete, {"relationship_requests": 1})
    assert len(failed["families"]) == 2
    assert failed["issues"][0]["code"] == "embedding_failed"
    assert not model.relationship_calls
    model = Model()
    calls = []

    def fail(messages, operation, request_key):
        calls.append(request_key)
        raise RuntimeError("Generation budget exhausted")

    failed = aggregate([scenario("a", "x"), scenario("b", "y")], model.embed, fail, {"relationship_requests": 1})
    assert len(calls) == 1
    assert len(failed["families"]) == 2
    assert failed["relations"][0]["status"] == "uncertain"
    assert "budget exhausted" in failed["relations"][0]["reason"]


def test_duplicate_components_do_not_inflate_coverage_or_prevalence():
    component = scenario("a", "x")
    result = aggregate([component, copy.deepcopy(component)], Model().embed, None)
    assert len(result["families"]) == 1
    assert result["coverage"][0]["valid_component_count"] == 1
    assert result["families"][0]["prevalence"]["unique_source_count"] == 1
    assert result["issues"][0]["code"] == "duplicate_component"


def test_large_finite_and_zero_vectors_do_not_create_nan_candidates():
    components = [scenario("a", "x"), scenario("b", "y"), scenario("c", "z")]
    model = Model(vectors=[[1e308, 1e308], [1e308, 1e308], [0, 0]])
    result = aggregate(components, model.embed, None)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["similarity"] == pytest.approx(1.0)
    assert json.dumps(result, allow_nan=False)


def test_compatibility_preserves_source_designated_customer_original_role():
    components = [scenario("a", "x"), persona("b", "x")]
    components[0]["payload"]["needs"][0]["evidence"][0]["role"] = "buyer"
    components[1]["payload"]["actions"][0]["evidence"][0]["role"] = "buyer"
    result = aggregate(components, Model().embed, None)
    assert len(result["compatibility"]) == 1
    assert {e["role"] for e in result["compatibility"][0]["evidence"]} == {"buyer"}


def test_larger_explicit_embedding_allocation_keeps_owner_budget_and_bounded_candidates():
    components = [scenario(f"object-{i:03}", f"source-{i:03}") for i in range(129)]
    model = Model()
    result = aggregate(components, model.embed, None, {"max_embedding_inputs": 256})
    assert sum(len(call[0]) for call in model.embedding_calls) == 129
    assert len(result["candidates"]) == 32
    assert not any(issue["code"] == "embedding_input_limit" for issue in result["issues"])
    assert any(issue["code"] == "candidate_pair_limit" and issue["nominated_pair_count"] == 8256 for issue in result["issues"])
    assert sum(len(family["variants"]) for family in result["families"]) == 129

    # The larger comparison setting grants no provider budget. The caller's
    # existing reservation callback may still reject the request, without retry.
    calls = []

    def exhausted(texts, request_key):
        calls.append(len(texts))
        raise RuntimeError("Owner embedding allowance exhausted")

    failed = aggregate(components, exhausted, None, {"max_embedding_inputs": 256})
    assert calls == [129]
    assert failed["issues"][0]["code"] == "embedding_failed"
    assert len(failed["families"]) == 129


def test_relationship_accepts_one_exact_outer_json_fence_and_preserves_raw_response():
    model = Model()
    raw_responses = []

    def fenced(messages, operation, request_key):
        raw = "```json\n" + model.complete(messages, operation, request_key) + "\n```"
        raw_responses.append(raw)
        return raw

    result = aggregate([scenario("a", "x"), scenario("b", "y")], model.embed, fenced,
                       {"relationship_requests": 1})
    relation = result["relations"][0]
    assert relation["status"] == "model_supported"
    assert relation["relation"] == "equivalent"
    assert relation["raw_response"] == raw_responses[0]
    assert relation["format_normalizations"] == ["removed_single_outer_json_fence"]
    assert len(result["families"]) == 1
    assert len(model.relationship_calls) == 1


@pytest.mark.parametrize("invalid_format", ["prose", "second_fence"])
def test_relationship_rejects_prose_or_second_fence_and_retains_failed_response(invalid_format):
    model = Model()
    raw_responses = []

    def malformed(messages, operation, request_key):
        answer = model.complete(messages, operation, request_key)
        raw = ("Here is the judgment:\n" + answer if invalid_format == "prose"
               else "```json\n" + answer + "\n```\n```json\n{}\n```")
        raw_responses.append(raw)
        return raw

    result = aggregate([scenario("a", "x"), scenario("b", "y")], model.embed, malformed,
                       {"relationship_requests": 1})
    relation = result["relations"][0]
    assert relation["status"] == "uncertain"
    assert relation["raw_response"] == raw_responses[0]
    assert relation["evidence"] == []
    assert "format_normalizations" not in relation
    assert len(result["families"]) == 2
    assert len(model.relationship_calls) == 1
