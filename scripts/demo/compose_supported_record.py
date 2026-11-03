#!/usr/bin/env python3
"""Copy an explicitly selected prior persona unit without authoring semantics.

Pure composition uses frozen exports, original family variant components, and
canonical storage sources. No provider, warehouse, planner, or network is used.
Schema/source conflicts raise ValueError; the CLI records them as unresolved.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snowflake_engine.aggregation import aggregate
from snowflake_engine.contracts import PERSONA_SCHEMA, canonical_json, source_hash, validate_component
from snowflake_engine.export import build_export, validate_export
from snowflake_engine.practice import link_assignment_personas, validate_programme

VERSION = "exact-supported-unit-composition-v1"


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pointer(document, path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("an explicit non-root JSON pointer is required")
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        document = document[int(token)] if isinstance(document, list) else document[token]
    return document


def original_components(document):
    """Recover actual components; require their frozen practice digest proof."""
    lookup = {}
    for family in document["families"]:
        for row in family["variants"]:
            oid = row["object_id"]
            if oid in lookup and lookup[oid] != row:
                raise ValueError("conflicting original family variants")
            lookup[oid] = deepcopy(row)
    for row in document.get("quarantined_components", []):
        if row["object_id"] in lookup:
            raise ValueError("quarantined/valid component identity conflict")
        lookup[row["object_id"]] = deepcopy(row)
    accounting = document["practice"]["object_accounting"]
    if len(lookup) != len(accounting):
        raise ValueError("original components do not resolve complete practice accounting")
    components = [lookup[row["object_id"]] for row in accounting]
    errors = validate_programme(document["practice"], components)
    if errors:
        raise ValueError("frozen component custody failed: " + "; ".join(errors))
    return components


def _remap(value, old, new):
    if isinstance(value, dict):
        return {key: _remap(child, old, new) for key, child in value.items()}
    if isinstance(value, list):
        return [_remap(child, old, new) for child in value]
    return new if value == old else deepcopy(value)


def compose_exact_unit(base_export, prior_export, canonical_sources, operation, *, input_commitments=None):
    """Return (validated export, disclosed ledger, reusable native records).

    operation requires prior_persona_pointer, destination_persona_pointer,
    unit_pointer (relative to both persona rows), and a nonempty reason.
    Only same-source/speaker/local-persona/local-unit-ID replacement is allowed.
    """
    for name, doc in (("base", base_export), ("prior", prior_export)):
        errors = validate_export(doc)
        if errors:
            raise ValueError(name + " export invalid: " + "; ".join(errors))
    sources = canonical_sources.get("sources") if isinstance(canonical_sources, dict) else canonical_sources
    if not isinstance(sources, list):
        raise ValueError("canonical sources must be an array or sources envelope")
    hashes = {row["source_id"]: source_hash(row) for row in sources}
    if len(hashes) != len(sources) or hashes != base_export["manifest"]["source_hashes"] or hashes != prior_export["manifest"]["source_hashes"]:
        raise ValueError("both frozen exports must retain the exact canonical source cohort and commitments")
    if not isinstance(operation.get("reason"), str) or not operation["reason"].strip():
        raise ValueError("composition requires a disclosed reason")
    prior = pointer(prior_export, operation["prior_persona_pointer"])
    target = pointer(base_export, operation["destination_persona_pointer"])
    for field in ("kind", "source_id", "id", "speaker"):
        if prior.get(field) != target.get(field):
            raise ValueError("prior and destination persona identity conflict: " + field)
    if prior.get("kind") != "persona":
        raise ValueError("composition is restricted to a typed persona unit")
    unit_path = operation["unit_pointer"]
    if len(unit_path.split("/")) != 3 or unit_path.split("/")[1] not in {"actions", "sequences", "emotional_changes"}:
        raise ValueError("unit_pointer must name one complete supported persona unit")
    old_unit, replaced_unit = pointer(prior, unit_path), pointer(target, unit_path)
    if not isinstance(old_unit, dict) or old_unit.get("id") != replaced_unit.get("id"):
        raise ValueError("unit identities differ; ambiguous replacement rejected")
    components = original_components(base_export)
    old_oid = target["object_id"]
    matches = [c for c in components if c["object_id"] == old_oid]
    if len(matches) != 1:
        raise ValueError("destination component is not uniquely retained")
    component = matches[0]
    original_payload = {k: deepcopy(target[k]) for k in PERSONA_SCHEMA["properties"] if k in target}
    if component["payload"] != original_payload:
        raise ValueError("exported persona differs from its original component")
    original_component = deepcopy(component)
    layer, index = unit_path[1:].split("/")
    component["payload"][layer][int(index)] = deepcopy(old_unit)
    canonical_source = next(row for row in sources if row["source_id"] == target["source_id"])
    errors = validate_component("persona", component["payload"], canonical_source)
    if errors:
        raise ValueError("copied unit violates schema/source proof: " + "; ".join(errors))
    new_oid = "persona-" + digest([VERSION, old_oid, hashes[target["source_id"]], component["payload"]])[:24]
    component["object_id"] = new_oid
    versions = deepcopy(base_export["manifest"]["version_set"])
    # Preserve the exact original practice and focal content. Only the derived
    # persona identity and its input-accounting digest change before relinking.
    programme = _remap(base_export["practice"], old_oid, new_oid)
    for row, actual in zip(programme["object_accounting"], components):
        row["component_digest"] = digest(actual)
    programme = link_assignment_personas(programme, components, versions)
    grouped = aggregate(components, None, None, {
        "expected_sources": [{"source_id": row["source_id"], "source_digest": hashes[row["source_id"]], "status": row["status"]}
                             for row in base_export["source_coverage"]],
        "source_digests": hashes, "max_candidate_pairs": 0, "relationship_requests": 0,
    })
    ledger = {
        "version": VERSION, "operation": "replace_exact_typed_unit", "reason": operation["reason"],
        "inputs": deepcopy(input_commitments or {}),
        "prior_persona_pointer": operation["prior_persona_pointer"], "prior_object_id": prior["object_id"],
        "destination_persona_pointer_before": operation["destination_persona_pointer"],
        "destination_object_id_before": old_oid, "destination_object_id_after": new_oid,
        "unit_pointer": unit_path, "unit_id": old_unit["id"], "unit_canonical_sha256": digest(old_unit),
        "summary_utf8_sha256": hashlib.sha256(old_unit["summary"].encode("utf-8")).hexdigest(),
        "replaced_unit_canonical_sha256": digest(replaced_unit),
        "source_id": target["source_id"], "source_canonical_sha256": hashes[target["source_id"]],
        "prior_release": deepcopy(prior_export["release"]), "base_release": deepcopy(base_export["release"]),
        "exactness": "Complete unit canonical JSON identity; summary UTF-8 bytes and ordered evidence are unchanged. File indentation is not part of unit identity.",
        "source_proof": "Both frozen source-hash maps equal the supplied canonical cohort. Existing validate_component checks exact quotes, speaker attribution and supplied event order.",
        "preservation": "All other component payload fields are unchanged; the current persona's other sequence retains its full source history.",
        "version_pins": "Original extraction/packaging pins retained; composition helper and inputs are separately file-hashed by the CLI.",
        "usage": {"model_calls": 0, "hosted_embedding_calls": 0, "warehouse_queries": 0},
        "acceptance": "machine checks only; independent semantic preservation decision remains with EVALUATION",
    }
    manifest = deepcopy(base_export["manifest"])
    for key in ("release_id", "content_sha256"):
        manifest.pop(key, None)
    manifest.setdefault("input_accounting", {})["exact_record_composition"] = deepcopy(ledger)
    # Keep original row run attribution; the new release is content-addressed.
    records = {"sources": deepcopy(sources), "components": components, **grouped,
               "guidance": deepcopy(base_export["guidance"]), "practice": programme,
               "issues": deepcopy(base_export["issues"])}
    document = build_export(records, manifest)
    destination_index = next(i for i, row in enumerate(document["personas"]) if row["object_id"] == new_oid)
    ledger["destination_persona_pointer_after"] = f"/personas/{destination_index}"
    final_unit = pointer(document["personas"][destination_index], unit_path)
    if canonical_json(final_unit) != canonical_json(old_unit):
        raise ValueError("export did not preserve the exact prior unit")
    expected = deepcopy(original_component["payload"])
    expected[layer][int(index)] = deepcopy(old_unit)
    if expected != component["payload"]:
        raise ValueError("composition changed fields outside the selected unit")
    return document, ledger, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("base-export", "prior-export", "canonical-sources", "operation", "outdir"):
        parser.add_argument("--" + key, required=True, type=Path)
    args = parser.parse_args(argv)
    if args.outdir.exists() and any(args.outdir.iterdir()):
        parser.error("outdir must be absent or empty; frozen artifacts are never overwritten")
    args.outdir.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        (args.outdir / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    inputs = {key: {"path": str(getattr(args, key)), "file_sha256": file_digest(getattr(args, key))}
              for key in ("base_export", "prior_export", "canonical_sources", "operation")}
    inputs["helper"] = {"path": str(Path(__file__)), "file_sha256": file_digest(__file__)}
    try:
        base, prior, sources, operation = [json.loads(getattr(args, key).read_text(encoding="utf-8"))
                                            for key in ("base_export", "prior_export", "canonical_sources", "operation")]
        document, ledger, records = compose_exact_unit(base, prior, sources, operation, input_commitments=inputs)
    except (ValueError, KeyError, TypeError, IndexError) as error:
        write("unresolved.json", [{"status": "composition_rejected", "reason": str(error), "inputs": inputs}])
        return 1
    validation = {"version": VERSION, "export_errors": validate_export(document),
        "practice_errors": validate_programme(document["practice"], records["components"]),
        "prior_unit_canonical_identity": True, "source_commitments_unchanged": document["manifest"]["source_hashes"] == base["manifest"]["source_hashes"],
        "scenario_cards_canonical_identity": document["scenarios"] == base["scenarios"],
        "unchanged_persona_count": sum(row in base["personas"] for row in document["personas"]),
        "counts": {"sources": len(document["sources"]), "scenarios": len(document["scenarios"]), "personas": len(document["personas"]),
                   "assignments": len(document["practice"]["assignments"]), "components": len(records["components"])},
        "release_id": document["release"]["release_id"], "content_sha256": document["release"]["content_sha256"],
        "usage": ledger["usage"], "semantic_acceptance": "not asserted"}
    for name, value in (("final-export.json", document), ("persona-cards.json", document["personas"]), ("scenario-cards.json", document["scenarios"]),
                        ("canonical-sources.json", sources), ("records.json", records), ("composition-ledger.json", ledger),
                        ("validation.json", validation), ("unresolved.json", [])):
        write(name, value)
    print(json.dumps(validation, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
