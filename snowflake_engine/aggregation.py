"""Conservative, source-preserving catalogue aggregation (no I/O at import).

Decision engine-aggregation-v1: embed valid scenarios and personas separately;
cosine similarity only nominates bounded comparisons. With the default zero
relationship requests every component remains a singleton draft family. Optional
model judgments must cite exact retained evidence at both endpoints. Only an
explicit supported equivalence with no material differences can join a family,
and every pair in a family must have that judgment (complete link, never a
transitive chain). A deterministic ID-ordered clique partition resolves overlapping
cliques without forcing a cluster count. This is a machine proposal, not semantic
acceptance. Every member payload remains intact, including rare and late needs.

The caller owns durable model budgets/cache. The default comparison allocation is
128 embedding inputs; an explicit installation profile may increase it up to the
4,096-input computation safety ceiling. At most 128 candidate pairs are retained,
with an explicit omission issue when limits apply; no inventory is discarded.
One invocation makes at most four relationship requests; there are no retries.
"""
from copy import deepcopy
import hashlib
import heapq
import math
from itertools import combinations

from .contracts import DIRECT_AUTHORITIES, canonical_json
from .extraction import _strict_json

AGGREGATION_VERSION = "engine-aggregation-v1"
MAX_EMBEDDING_INPUTS = 4096
_RELATIONS = {"equivalent", "useful_variant", "partial_overlap", "contains", "distinct", "uncertain"}
_CITATION_FIELDS = ("event_id", "quote", "role", "speaker", "authority")


def _json(value):
    return canonical_json(value)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id(prefix, value):
    return prefix + "_" + _digest(value)[:24]


def _citations(value):
    """Return only full attributed extraction citations; no invented evidence."""
    result = []
    if isinstance(value, dict):
        if all(k in value for k in _CITATION_FIELDS) and value.get("event_id") and value.get("quote"):
            result.append({k: value[k] for k in _CITATION_FIELDS})
        else:
            for child in value.values():
                result.extend(_citations(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_citations(child))
    return sorted({_json(c): c for c in result}.values(), key=_json)


def _limit(config, key, default, maximum, minimum=0):
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    return value


def _lineage(members, digests):
    return [{"object_id": m["object_id"], "source_id": m["source_id"],
             "source_digest": digests.get(m["source_id"]),
             "component_digest": _digest(m)} for m in members]


def _coverage(components, expected, digests):
    by_source = {}
    for component in components:
        if isinstance(component, dict) and component.get("source_id"):
            members = by_source.setdefault(component["source_id"], [])
            if component not in members:
                members.append(component)
    declared = {}
    for entry in expected:
        if isinstance(entry, str):
            declared[entry] = {}
        elif isinstance(entry, dict) and entry.get("source_id"):
            declared[entry["source_id"]] = entry
        else:
            raise ValueError("expected_sources entries require a source_id")
    rows = []
    for source_id in sorted(set(by_source) | set(declared)):
        members = by_source.get(source_id, [])
        valid = [m for m in members if m.get("object_id") and isinstance(m.get("payload"), dict)
                 and m.get("validation_status") == "valid" and m.get("kind") in {"scenario", "persona"}]
        quarantined = [m for m in members if m not in valid]
        declared_status = declared.get(source_id, {}).get("status")
        status = declared_status or ("quarantined" if quarantined and not valid else "processed" if members else "failed")
        rows.append({"source_id": source_id, "source_digest": digests.get(source_id),
                     "status": status, "expected": source_id in declared,
                     "component_ids": sorted({m["object_id"] for m in members if m.get("object_id")}),
                     "valid_component_count": len(valid), "quarantined_component_count": len(quarantined),
                     "scenario_count": sum(m["kind"] == "scenario" for m in valid),
                     "persona_count": sum(m["kind"] == "persona" for m in valid),
                     "training_selection_status": "not_selected_by_aggregation"})
    return rows


def _cosine(left, right):
    # Scale before squaring so finite provider values cannot overflow into a
    # NaN similarity (which could otherwise bypass the threshold comparison).
    left_scale, right_scale = max(abs(x) for x in left), max(abs(x) for x in right)
    if not left_scale or not right_scale:
        return None
    left = [x / left_scale for x in left]
    right = [x / right_scale for x in right]
    denominator = math.sqrt(sum(x*x for x in left)) * math.sqrt(sum(x*x for x in right))
    return max(-1.0, min(1.0, sum(x*y for x, y in zip(left, right)) / denominator))


def _nominate(inventory, embed, config, issues, digests):
    max_inputs = _limit(config, "max_embedding_inputs", 128, MAX_EMBEDDING_INPUTS, minimum=1)
    max_pairs = _limit(config, "max_candidate_pairs", 32, 128)
    threshold = config.get("similarity_threshold", 0.8)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("similarity_threshold must be between -1 and 1")
    selected = inventory[:max_inputs]
    if len(selected) < len(inventory):
        issues.append({"code": "embedding_input_limit", "message": "Some components were not compared; all remain in their families.",
                       "uncompared_object_ids": [m["object_id"] for m in inventory[max_inputs:]]})
    candidates = []
    nominated_pair_count = 0
    lineage = {m["object_id"]: _lineage([m], digests)[0] for m in selected}
    # Separate provider batches keep the two inventories explicit. Budget remains
    # cumulative across both batches; never call embeddings for a singleton.
    for kind in ("scenario", "persona"):
        members = [m for m in selected if m["kind"] == kind]
        if len(members) < 2 or max_pairs == 0:
            continue
        texts = [_json(m["payload"]) for m in members]
        request_key = _id("embedding", [AGGREGATION_VERSION, kind, texts])
        try:
            vectors = embed(texts, request_key)
            if not isinstance(vectors, list) or len(vectors) != len(members):
                raise ValueError("embedding response count differs from input count")
            dimensions = set()
            for vector in vectors:
                if not isinstance(vector, (list, tuple)) or not vector or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
                    raise ValueError("embedding response has a non-finite or invalid vector")
                dimensions.add(len(vector))
            if len(dimensions) != 1:
                raise ValueError("embedding dimensions differ")
            for i, j in combinations(range(len(members)), 2):
                similarity = _cosine(vectors[i], vectors[j])
                if similarity is None or similarity < threshold:
                    continue
                pair = [members[i], members[j]]
                pair_lineage = [lineage[m["object_id"]] for m in pair]
                candidate = {"candidate_id": _id("candidate", [AGGREGATION_VERSION, pair_lineage]),
                                   "kind": kind, "left_object_id": pair[0]["object_id"], "right_object_id": pair[1]["object_id"],
                                   "similarity": similarity, "embedding_request_key": request_key,
                                   "status": "nominated_only", "semantic_acceptance": "unreviewed",
                                   "lineage": pair_lineage}
                nominated_pair_count += 1
                # Retain only the best bounded records while scanning. Reverse
                # the ID tie break so the heap root is the worst retained pair.
                entry = (similarity, -int(candidate["candidate_id"].split("_")[-1], 16), candidate)
                if len(candidates) < max_pairs:
                    heapq.heappush(candidates, entry)
                elif entry[:2] > candidates[0][:2]:
                    heapq.heapreplace(candidates, entry)
        except Exception as exc:
            issues.append({"code": "embedding_failed", "kind": kind, "message": str(exc),
                           "object_ids": [m["object_id"] for m in members]})
    if nominated_pair_count > max_pairs:
        issues.append({"code": "candidate_pair_limit", "nominated_pair_count": nominated_pair_count,
                       "retained_pair_count": max_pairs,
                       "message": "Only the bounded highest-similarity comparisons were retained; all components remain."})
    return sorted((entry[2] for entry in candidates), key=lambda c: (-c["similarity"], c["candidate_id"]))


def _judge(candidate, pair, complete, digests):
    record = {"relation_id": _id("relation", [AGGREGATION_VERSION, candidate["candidate_id"]]),
              "candidate_id": candidate["candidate_id"], "kind": candidate["kind"],
              "left_object_id": pair[0]["object_id"], "right_object_id": pair[1]["object_id"],
              "relation": "uncertain", "status": "unreviewed", "evidence": [],
              "material_differences": [], "lineage": _lineage(pair, digests),
              "semantic_review_status": "unreviewed", "raw_response": None}
    if complete is None:
        return record
    system = ("Compare two source-backed components of the same kind. Similarity is advisory. "
              "Return only JSON: {relation:equivalent|useful_variant|partial_overlap|contains|distinct|uncertain, "
              "confidence:supported|uncertain, evidence:[{object_id,event_id,quote,role,speaker,authority}], "
              "material_differences:[string],reason:string}. Cite exact supplied citations at BOTH endpoints. "
              "Equivalent requires all needs, constraints, corrections, outcomes, unknowns and observed behaviour "
              "to be interchangeable with no material differences. Retain rare/late details; never infer correct "
              "handling or stable traits. If support is incomplete choose uncertain. contains is directional: "
              "left contains right. This is a draft model proposal, never human approval.")
    request_key = _id("relationship", [AGGREGATION_VERSION, system, pair])
    record["request_key"] = request_key
    try:
        raw = complete([{"role": "system", "content": system},
                        {"role": "user", "content": _json(pair)}], "relationship", request_key)
        record["raw_response"] = raw
        answer, normalizations = _strict_json(raw)
        if normalizations:
            record["format_normalizations"] = normalizations
        if not isinstance(answer, dict) or answer.get("relation") not in _RELATIONS:
            raise ValueError("invalid relationship decision")
        differences = answer.get("material_differences")
        if not isinstance(differences, list) or any(not isinstance(d, str) for d in differences):
            raise ValueError("material_differences must be a string array")
        evidence = answer.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("relationship evidence must be an array")
        allowed = {m["object_id"]: {_json(c) for c in _citations(m["payload"])} for m in pair}
        seen = set()
        for citation in evidence:
            if not isinstance(citation, dict) or citation.get("object_id") not in allowed:
                raise ValueError("relationship evidence refers to an unknown object")
            if not all(field in citation for field in _CITATION_FIELDS):
                raise ValueError("relationship evidence lacks attribution")
            if _json({k: citation[k] for k in _CITATION_FIELDS}) not in allowed[citation["object_id"]]:
                raise ValueError("relationship evidence is not an exact retained citation")
            seen.add(citation["object_id"])
        record.update({"evidence": deepcopy(evidence), "material_differences": differences,
                       "reason": answer.get("reason") if isinstance(answer.get("reason"), str) else None})
        supported = answer.get("confidence") == "supported" and seen == set(allowed)
        if answer["relation"] == "equivalent" and differences:
            supported = False
            record["reason"] = "Proposed equivalence has material differences; preserve separate variants."
        record["relation"] = answer["relation"]
        record["status"] = "model_supported" if supported and answer["relation"] != "uncertain" else "uncertain"
    except Exception as exc:
        record.update({"status": "uncertain", "reason": str(exc)})
    return record


def _families(inventory, relations, coverage, digests):
    equivalent = {frozenset((r["left_object_id"], r["right_object_id"])) for r in relations
                  if r["relation"] == "equivalent" and r["status"] == "model_supported"}
    families = []
    for kind in ("scenario", "persona"):
        groups = []
        for member in [m for m in inventory if m["kind"] == kind]:
            group = next((g for g in groups if all(frozenset((member["object_id"], other["object_id"])) in equivalent for other in g)), None)
            if group is None:
                groups.append([member])
            else:
                group.append(member)
        kind_sources = {m["source_id"] for m in inventory if m["kind"] == kind}
        for group in groups:
            lineage = _lineage(group, digests)
            source_ids = sorted({m["source_id"] for m in group})
            families.append({"family_id": _id("family", [AGGREGATION_VERSION, kind, lineage]),
                             "kind": kind, "member_ids": [m["object_id"] for m in group], "source_ids": source_ids,
                             "lineage": lineage, "variants": deepcopy(group),
                             "grouping_status": "model_supported_equivalence" if len(group) > 1 else "singleton",
                             "review_status": "unreviewed", "guidance_status": "requires_client_guidance",
                             "prevalence": {"unique_source_count": len(source_ids),
                                            "all_source_denominator": len(coverage),
                                            "denominator_scope": "all_expected_and_observed_sources",
                                            "kind_valid_source_denominator": len(kind_sources),
                                            "fraction": len(source_ids)/len(coverage) if coverage else None},
                             "training_selection_status": "not_selected_by_aggregation"})
    return families


def _compatibility(inventory, digests):
    links = []
    for scenario in [m for m in inventory if m["kind"] == "scenario"]:
        for persona in [m for m in inventory if m["kind"] == "persona" and m["source_id"] == scenario["source_id"]]:
            speaker = persona["payload"].get("speaker")
            if not speaker or persona["payload"].get("evidence_status") != "observed":
                continue
            # Persona validation already establishes that this is the source's
            # designated customer speaker; preserve their original role label.
            scenario_evidence = [c for c in _citations(scenario["payload"]) if c["speaker"] == speaker and c["authority"] in DIRECT_AUTHORITIES]
            persona_evidence = [c for c in _citations(persona["payload"]) if c["speaker"] == speaker and c["authority"] in DIRECT_AUTHORITIES]
            if not scenario_evidence or not persona_evidence:
                continue
            pair = [scenario, persona]
            links.append({"compatibility_id": _id("compatibility", [AGGREGATION_VERSION, _lineage(pair, digests), speaker]),
                          "scenario_id": scenario["object_id"], "persona_id": persona["object_id"],
                          "source_id": scenario["source_id"], "speaker": speaker,
                          "status": "shared_source_speaker_evidence", "review_status": "unreviewed",
                          "evidence": [{"object_id": m["object_id"], **citation} for m, citations in
                                       ((scenario, scenario_evidence), (persona, persona_evidence)) for citation in citations],
                          "lineage": _lineage(pair, digests),
                          "scope": "Observed together in this source; transfer to other scenarios is unverified."})
    return links


def aggregate(components, embed, complete, config=None):
    """Build draft catalogues; callbacks are caller-budgeted, optional judgments bounded.

    config: relationship_requests (0..20, default 0), max_embedding_inputs (1..4096,
    default 128; caller supplies its installed comparison allocation),
    max_candidate_pairs (0..128), similarity_threshold (-1..1), expected_sources
    (IDs or {source_id,status,source_digest}), and source_digests (ID -> digest).
    Expected sources without components are failed unless their explicit status
    says otherwise. Coverage never silently removes failed/quarantined sources.
    """
    config = config or {}
    request_limit = _limit(config, "relationship_requests", 0, 20)
    components = deepcopy(list(components))
    expected = config.get("expected_sources", [])
    if not isinstance(expected, list):
        raise ValueError("expected_sources must be an array")
    digests = dict(config.get("source_digests", {}))
    for source in expected:
        if isinstance(source, dict) and source.get("source_id") and source.get("source_digest"):
            digests.setdefault(source["source_id"], source["source_digest"])
    for component in components:
        if isinstance(component, dict) and component.get("source_id") and (component.get("source_digest") or component.get("source_hash")):
            digests.setdefault(component["source_id"], component.get("source_digest") or component["source_hash"])
    issues = []
    by_id = {}
    for component in components:
        if not isinstance(component, dict) or not component.get("object_id") or not component.get("source_id"):
            issues.append({"code": "invalid_component", "message": "Component lacks source or object identity."})
            continue
        if component.get("validation_status") != "valid" or component.get("kind") not in {"scenario", "persona"} or not isinstance(component.get("payload"), dict):
            continue
        if component["object_id"] in by_id:
            if component != by_id[component["object_id"]]:
                raise ValueError("conflicting components share an object_id")
            issues.append({"code": "duplicate_component", "object_id": component["object_id"]})
        by_id[component["object_id"]] = component
    inventory = sorted(by_id.values(), key=lambda m: (m["kind"], m["object_id"]))
    coverage = _coverage(components, expected, digests)
    candidates = _nominate(inventory, embed, config, issues, digests)
    relations = []
    for index, candidate in enumerate(candidates):
        pair = [by_id[candidate["left_object_id"]], by_id[candidate["right_object_id"]]]
        relation = _judge(candidate, pair, complete if index < request_limit else None, digests)
        relations.append(relation)
        candidate["status"] = "judged" if relation.get("request_key") else "nominated_only"
    return {"families": _families(inventory, relations, coverage, digests), "relations": relations,
            "compatibility": _compatibility(inventory, digests), "coverage": coverage,
            "candidates": candidates, "issues": issues}
