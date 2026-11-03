# Snowflake engine data dictionary

This is the field-level reference for the live `engine-storage-v4` schema and
the `engine-export-v3` portable contract. [`snowflake_engine/schema.py`](../../snowflake_engine/schema.py)
owns physical columns, types, and views; this guide records that current
physical contract and its operational meaning. Do not maintain a second DDL
definition.

Storage v4 installs into a new dedicated engine schema. It does not migrate or
overwrite earlier schemas, ledgers, or releases. Readers still accept the
recognised historical storage markers and `engine-export-v1`/
`engine-export-v2` documents in their historical shapes; that is reader
compatibility, not an upgrade of historic output. This document does not report
results from the active 100-source model run, semantic improvement, client
readiness, or external-platform compatibility.

Text fields are `VARCHAR`, structured fields `VARIANT`, and projected lists
`ARRAY` unless stated otherwise. Nullable storage means unavailable or
inapplicable evidence, never an inferred value. `valid` only means deterministic
checks passed; it never means semantic acceptance.

## Identity, version, and status

| Field/concept | Meaning |
| --- | --- |
| `CLIENT_ID` | Operator-supplied tenant boundary for client-owned records. |
| `RUN_ID` | Immutable operator-supplied operation identity. Reuse stops instead of overwriting output. |
| `SOURCE_ID` | Reviewed-mapping identity for a canonical interaction; it is not necessarily a case ID. |
| `OBJECT_ID` | Identity for a scenario/persona component from source content, kind, and local component identity. |
| `LOGICAL_ID` | Deterministic identity for the enduring logical object. Changed group membership creates a new logical object. |
| `REVISION_ID` | Deterministic identity for the exact retained payload plus version set. Changed content/version receives a new revision. |
| `VERSION_SET` | Complete material-stage version object on every current record that has lineage. |
| `VALIDATION_STATUS` | `valid` passed shape/vocabulary/citation/attribution/order checks; `quarantined` retains the failed payload and reasons. Neither means reviewed meaning. |
| review/eligibility | Generated output is `unreviewed` and draft. Machine validation, semantic review, client guidance, and client readiness are separate. |

The current version set has exactly these 14 keys; a v3 export rejects missing,
empty, or additional keys.

| Key | Records |
| --- | --- |
| `storage` | Physical contract: `engine-storage-v4`. |
| `input_mapping` | Mapping digest, or `unresolved-until-input-read` before loading. |
| `extraction_contract` | Extraction schema, including `engine-extraction-schema-v3` for split v3. |
| `framework`, `ontology`, `prompt` | Framework-source digest, codebook version, and prompt contract. |
| `model`, `model_settings`, `embedding_model` | Models plus input/output limits, temperature, extraction mode, and compact-source selection. |
| `code`, `package` | Runtime source digest and package digest/version-plus-digest. |
| `aggregation`, `selection` | Aggregation and practice/coverage selection versions plus configuration. |
| `export` | Portable contract: `engine-export-v3`. |

The `ENGINE_METADATA.BUDGET` profile is the reviewed limit object persisted at
first installation. Later requests can lower but not raise it. Provider
attempts, including failures, remain cumulative. `ENGINE_METADATA.CAPTURE_CONFIG`
is the installation-time capture capability/configuration record. It is stored
with the installation marker so native capture can verify that the requested
configuration matches the dedicated schema; it is not client guidance or a
record of semantic acceptance.

## Storage-v4 tables

Except `ENGINE_METADATA`, `LOCKS`, and `RECORD_REVISIONS`, every engine table
has `LOGICAL_ID`, `REVISION_ID`, and `VERSION_SET`. `INPUT_RAW` and
`INPUT_SOURCES` now also have `RUN_ID`: raw and canonical-source custody append
by run rather than acting as mutable “latest” rows.

| Table | Grain and fields | Meaning |
| --- | --- | --- |
| `ENGINE_METADATA` | `SCHEMA_VERSION`, `MANIFEST_HASH`, `BUDGET`, `CAPTURE_CONFIG`, `INSTALLED_AT`. | Single installation marker and reviewed capture configuration. Mismatched version, manifest, budget, or capture configuration stops reuse. |
| `INPUT_RAW` | `RAW_ID`, `CLIENT_ID`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RUN_ID`. | Raw input and reviewed-mapping custody for that run. A staged raw ID never invents source identity. |
| `INPUT_SOURCES` | `SOURCE_ID`, `CLIENT_ID`, `CONTENT_HASH`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RUN_ID`. | Canonical source, events, metadata, and guidance for that run. Changed source content creates another retained row/revision. |
| `INPUT_ISSUES` | `ISSUE_ID`, `RUN_ID`, `CLIENT_ID`, nullable `SOURCE_ID`, `REASON`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Visible mapping/normalization/custody failure. When a normalized source ID is known, native processing stores that exact current ID. When it is unavailable, the native insert omits the nullable column so Snowflake applies SQL `NULL`; it does not bind Python `None` or infer a source. |
| `RUNS` | `RUN_ID`, `CLIENT_ID`, `STATUS`, `CONFIG`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Load or processing operation. Status: `running`, `complete`, `partial`, or `failed`. |
| `ATTEMPTS` | `ATTEMPT_ID`, `RUN_ID`, `CLIENT_ID`, `OPERATION`, `REQUEST_KEY`, `STATUS`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Provider/cache reservation, written before invocation. Status: `reserved`, `succeeded`, or `failed`. |
| `COMPONENTS` | `OBJECT_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `KIND`, `VALIDATION_STATUS`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Scenario/persona result, including quarantined originals and valid partial projections. |
| `EMBEDDINGS` | `EMBEDDING_ID`, `RUN_ID`, `CLIENT_ID`, `OBJECT_ID`, `MODEL`, `CONTENT_HASH`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Persisted embedding input/result. Unknown returned usage stays unknown. |
| `CANDIDATES` | `CANDIDATE_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Similarity nomination, not equivalence. |
| `FAMILIES` | `FAMILY_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Draft group with full variants, lineage, differences, and review state. |
| `RELATIONS` | `RELATION_ID`, `RUN_ID`, `CLIENT_ID`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Relation result with exact evidence/differences; `uncertain` and `distinct` remain visible. |
| `COMPATIBILITY` | `COMPATIBILITY_ID`, `RUN_ID`, `CLIENT_ID`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Same-source/speaker pairing, never a full scenario-persona cross-product. |
| `COVERAGE` | `COVERAGE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `STATUS`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Source accounting. Status: `processed`, `cached`, `failed`, `excluded`, or `quarantined`. |
| `GUIDANCE` | `GUIDANCE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `AUTHORITY`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Guidance with authority visible; `fictional` is not client-approved. |
| `RELEASES` | `RELEASE_ID`, `RUN_ID`, `CLIENT_ID`, `STATUS`, `MANIFEST`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Immutable validated draft release. |
| `LOCKS` | `LOCK_KEY`, `OWNER_RUN_ID`, `ACQUIRED_AT`, `PAYLOAD`. | Singleton writer lease; the engine never takes it over automatically. |
| `RECORD_REVISIONS` | `REVISION_ID`, `LOGICAL_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `VERSION_SET`, `PAYLOAD`. | Append-only record of current `RUNS`/`ATTEMPTS` state before an insert or merge. It never amends an old release. |
| `PRACTICE` | `PRACTICE_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`. | Draft programme, assignments, omissions, and must-cover accounting. |
| `CAPTURE_ITEMS` | `ITEM_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `RECORD_TYPE`, `STATUS`, `SOURCE_HASH`, `PROFILE_VERSION`, `EXECUTION_MODE`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `PAYLOAD`. | Append-only captured evidence state, separate from finished cards. |
| `CAPTURE_CHECKPOINTS` | `CHECKPOINT_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `RECORD_TYPE`, `STATUS`, `SOURCE_HASH`, `PROFILE_VERSION`, `EXECUTION_MODE`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `PAYLOAD`. | Append-only capture checkpoint state, separate from finished cards. |

## Typed marts

The lists below name every field projected by the current view manifest. A
typed null means a nested payload did not supply a value; it is not a negative
claim about the source. `PAYLOAD` remains the retained structured record where
it is included.

| View | Current projected fields | Meaning |
| --- | --- | --- |
| `MART_SCENARIOS` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `OBJECT_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `VALIDATION_STATUS`, `DELIVERY_SCOPE`, `PARTIAL_STATE`, `PARTIAL_LAYERS`, `WITHHELD_CHILDREN`, `PROJECTION_OF`, `ORIGINAL_OBJECT_ID`, `ORIGINAL_SOURCE_ID`, `ORIGINAL_LOGICAL_ID`, `ORIGINAL_REVISION_ID`, `ORIGINAL_VERSION_SET`, `ORIGINAL_VALIDATION_STATUS`, `SUMMARY`, `NEEDS`, `UNKNOWNS`, `REVIEW_STATUS`, `ELIGIBILITY_STATUS`, `SCENARIO`, `ISSUES`. | Draft scenario fields. Eligibility is `requires_semantic_review` only with same-run client-approved guidance; otherwise `requires_client_guidance`. |
| `MART_PERSONAS` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `OBJECT_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `VALIDATION_STATUS`, `DELIVERY_SCOPE`, `PARTIAL_STATE`, `PARTIAL_LAYERS`, `WITHHELD_CHILDREN`, `PROJECTION_OF`, `ORIGINAL_OBJECT_ID`, `ORIGINAL_SOURCE_ID`, `ORIGINAL_LOGICAL_ID`, `ORIGINAL_REVISION_ID`, `ORIGINAL_VERSION_SET`, `ORIGINAL_VALIDATION_STATUS`, `SPEAKER`, `EVIDENCE_STATUS`, `ACTIONS`, `SEQUENCES`, `EMOTIONAL_EXPRESSION`, `EMOTIONAL_EXPRESSION_STATUS`, `EMOTIONAL_OBSERVATIONS`, `EMOTIONAL_CHANGES`, `UNKNOWNS`, `REVIEW_STATUS`, `ELIGIBILITY_STATUS`, `PERSONA`, `ISSUES`. | Source-session observation; other speakers can give context but not direct-speech support. |
| `MART_FAMILIES` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `FAMILY_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `GROUPING_STATUS`, `GUIDANCE_STATUS`, `SOURCE_IDS`, `MEMBER_IDS`, `LINEAGE`, `UNIQUE_SOURCE_COUNT`, `ALL_SOURCE_DENOMINATOR`, `KIND_VALID_SOURCE_DENOMINATOR`, `REVIEW_STATUS`, `PAYLOAD`. | Grouping preserves every variant. Prevalence is accounting, not semantic correctness. |
| `MART_VARIANTS` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `FAMILY_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `OBJECT_ID`, `SOURCE_ID`, `VARIANT_KIND`, `VARIANT_PAYLOAD`. | Flattened members of a stored family. |
| `MART_RELATIONS` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RELATION_ID`, `RUN_ID`, `CLIENT_ID`, `CANDIDATE_ID`, `KIND`, `LEFT_OBJECT_ID`, `RIGHT_OBJECT_ID`, `RELATION`, `STATUS`, `EVIDENCE`, `MATERIAL_DIFFERENCES`, `PAYLOAD`. | Relationship proposals remain unreviewed. |
| `MART_COMPATIBILITY` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `COMPATIBILITY_ID`, `RUN_ID`, `CLIENT_ID`, `SCENARIO_ID`, `PERSONA_ID`, `SOURCE_ID`, `SPEAKER`, `REVIEW_STATUS`, `SCOPE`, `STATUS`, `EVIDENCE`, `PAYLOAD`. | Compatible-pair proposals remain unreviewed. |
| `MART_EVIDENCE` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `OBJECT_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `KIND`, `VALIDATION_STATUS`, `EVENT_ID`, `QUOTE`, `ROLE`, `SPEAKER`, `AUTHORITY`, `EVIDENCE_PATH`. | Flattened citations, limited to entries with an event ID and quote. |
| `MART_GUIDANCE` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `GUIDANCE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `AUTHORITY`, `STATUS`, `GUIDANCE_TEXT`, `PAYLOAD`. | Queryable guidance and authority. |
| `MART_COVERAGE` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `COVERAGE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `STATUS`, `REASON`, `COMPONENT_IDS`, `VALID_COMPONENT_COUNT`, `QUARANTINED_COMPONENT_COUNT`, `SCENARIO_COUNT`, `PERSONA_COUNT`, `TRAINING_SELECTION_STATUS`, `PAYLOAD`. | Queryable source processing and counts. |
| `MART_RUNS` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RUN_ID`, `CLIENT_ID`, `STATUS`, `MODEL`, `EMBEDDING_MODEL`, `STARTED_AT`, `COMPLETED_AT`, `USAGE`, `LIMITS`, `CONFIG`, `PAYLOAD`. | Operational boundary. |
| `MART_RELEASES` | `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RELEASE_ID`, `RUN_ID`, `CLIENT_ID`, `STATUS`, `EXPORT_SCHEMA_VERSION`, `ELIGIBILITY`, `ELIGIBILITY_STATUS`, `REVIEW_STATUS`, `CLIENT_READY`, `VERSIONS`, `SOURCE_HASHES`, `LIMITATIONS`, `MANIFEST`, `PAYLOAD`. | Immutable draft-release boundary. |
| `MART_PRACTICE` | `PRACTICE_ID`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD`. | Stored source-preserving practice programme. |
| `MART_CAPTURE_EVIDENCE` | `ITEM_ID`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `RECORD_TYPE`, `STATUS`, `SOURCE_HASH`, `PROFILE_VERSION`, `EXECUTION_MODE`, `RECORD_ID`, `STAGE`, `SECTION_ID`, `CLAIM_ID`, `SUPERSEDES`, `REQUIRES_CLIENT_GUIDANCE`, `DELIVERY_SCOPE`, `PAYLOAD`. | Queryable append-only capture evidence. It is explicitly capture evidence only and still requires client guidance. |
| `MART_CAPTURE_CHECKPOINTS` | `CHECKPOINT_ID`, `LOGICAL_ID`, `REVISION_ID`, `VERSION_SET`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `RECORD_TYPE`, `STATUS`, `SOURCE_HASH`, `PROFILE_VERSION`, `EXECUTION_MODE`, `SECTION_ID`, `STAGE`, `SEQUENCE`, `REQUIRES_CLIENT_GUIDANCE`, `DELIVERY_SCOPE`, `PAYLOAD`. | Queryable append-only capture checkpoints. It is explicitly capture evidence only and still requires client guidance. |

`MART_VARIANTS` flattens members of a stored family. Its lineage columns identify that parent family revision; use the family revision together with `OBJECT_ID`, or the v3 export’s independently versioned variant row, to identify a particular member. This is parent-family lineage, not a new assertion that a variant has an independent storage revision.

`MART_SCENARIOS` and `MART_PERSONAS` now expose `DELIVERY_SCOPE`, `PARTIAL_STATE`, `PARTIAL_LAYERS`, `WITHHELD_CHILDREN`, `PROJECTION_OF`, and the linked original's object/source/logical/revision/version/status columns. The original join requires the same `CLIENT_ID`, `RUN_ID`, `SOURCE_ID`, and projected object ID, so a historical or different-client original cannot be attached by object ID alone. `not_recorded` means an older component did not state delivery scope; it is not evidence of complete delivery. `full_component` with `complete` is explicitly recorded full delivery. `validated_children_only` with `partial` is a projection whose original quarantined component remains visible. In particular, `VALIDATION_STATUS=valid` alone never establishes completeness.

## Canonical source and extraction v3

`INPUT_SOURCES.PAYLOAD` contains `source_id`, `client_id`, `source_type`,
`metadata`, ordered `events`, and `guidance`. Events retain source event ID,
ordinal, nullable timestamp/timing status, kind, role, speaker, authority,
permitted text, `original_text`, and redaction status. Null timing remains
unknown; ordinal retains supplied order.

The opt-in `split_v3` mode issues separate scenario and persona calls against the
complete permitted source and pins `contract_version` to
`engine-extraction-schema-v3`. It does not silently clip an over-limit source or
repair an invalid response.

| Field | Meaning and limit |
| --- | --- |
| scenario `needs[]` and persona `actions[]` | Each v3 item has `assertion`: `actor`, `recipient`, `action`, `scope`, `state`, and `certainty`. Actor/recipient are source speaker IDs or `unknown`; the assertion retains attribution, action state, scope, alternatives, and uncertainty. |
| `assertion.state` | `offered`, `requested`, `accepted`, `declined`, `proposed`, `in_progress`, `pending`, `reported_completed`, `source_recorded_completed`, `reported`, `negated`, `conditional`, `uncertain`, or `unknown`. `source_recorded_completed` needs cited source action evidence. |
| `assertion.certainty` | `explicit`, `reported`, `possible`, `uncertain`, or `unknown`. `explicit` means stated, not independently true. |
| persona `child_records[]` | Independent action/sequence/emotion-expression/emotion-change validation records: `kind`, `local_id`, `payload`, `validation_status`, `issues`, `delivery_eligible`. Invalid children remain retained. |
| `child_accounting` | Exact `total`, `valid`, `quarantined`, and `delivery_eligible` counts. These are delivery/validation counts, not semantic measures. |
| full component | `delivery_scope: full_component` and `partial_state: complete` when valid or `quarantined` when invalid. A mixed persona can retain `partial_state: partial` while its original remains quarantined. |
| partial persona projection | Valid `delivery_scope: validated_children_only` record with `partial_state: partial`, same-source `projection_of` original, non-empty `partial_layers`, and non-empty `withheld_children` (child kind/local ID/issues). The v3 export also carries `projection_lineage` for that original's object and version identities. It contains only valid children and does not hide the original. |
| emotion and citations | Unknown/ambiguous/no-expression/insufficient-evidence emotion has no invented label. Citations are exact `{event_id, quote, role, speaker, authority}`; matching shape never proves interpretation. |

## Practice

`PRACTICE.PAYLOAD` holds `practice_groups`, `assignments`,
`object_accounting`, `coverage`, `must_cover_accounting`, `planner`, `issues`,
`versions`, and `selection_summary`. The planner can make up to four bounded
shards and selects at most 20 draft assignments. Groups keep
`equivalence_status: not_asserted` and
`guidance_status: requires_client_guidance`. Assignments are not assessment
eligible and have no client-approved resolution criterion. Exact need-reference
coverage is not semantic acceptance or proof that training will work.

## Export-v3

A v3 release is an immutable `engine-export-v3` document. Its `release` is
always `draft` and `unreviewed`, with `client_ready: false` and
`semantic_acceptance: false`. `platform_schema_status` is `unknown` and
`platform_adapter_status` is `unverified`. Machine validation therefore does not
establish approval, readiness, or a working external adapter.

`manifest.version_set` is the exact 14-key set above and
`manifest.versions` must equal it. The manifest/release checksum and
content-addressed `release_id` detect mutation, not a signature or approval.

Every exported v3 row in the standard arrays has portable `logical_id`,
`revision_id`, and `version_set` equal to the manifest. Where it comes from
storage, `storage_logical_id` and `storage_revision_id` retain the storage
identity separately. The portable revision is recomputed from the exported
projection, so it may differ without losing the relationship. Endpoint lineage
uses the endpoint’s portable identity.

| Array/field | Meaning |
| --- | --- |
| `sources[]` | Permitted canonical-source projection. `original_text`, `raw_record`, `raw_source`, and `raw_records` are withheld recursively. |
| `scenarios[]` / `personas[]` | Valid components, including portable/storage identity and v3 partial metadata where applicable. |
| `quarantined_components[]` | Failed deterministic components, retained separately with payload, issues, identity, and v3 child/original/projection fields where present. |
| `families[]`, `relations[]`, `compatibility[]`, `candidates[]` | Draft aggregation records with lineage/evidence and portable identity. |
| `variants[]` / `evidence[]` | Derived family-membership and nested-citation indexes with portable identity and endpoint linkage. |
| `guidance[]` / `source_coverage[]` / `issues[]` | Guidance, source outcomes, and retained issues after storage-only fields are withheld. |
| `practice` | Optional versioned programme; it cannot appear in v1 exports. |
| `accounting` | Source, coverage, valid, quarantined, issue, and coverage-status counts. V3 also has `partial_delivery_count`, `full_valid_component_count`, `full_valid_persona_count`, `partial_persona_delivery_count`, `full_valid_scenario_count`, `partial_scenario_delivery_count`, and `scope_not_recorded_count`. These are delivery and disclosure counts, not score results. |

The validator checks arrays, client/source scope, citations, component contracts,
variant membership, family prevalence, compatibility endpoints, withheld fields,
digests, version sets, and portable/storage identities. It validates this engine
contract only; it neither publishes externally nor validates an external adapter.

## Runtime payload notes

| Record/path | Meaning |
| --- | --- |
| `ATTEMPTS.PAYLOAD.versions` | Request version set. Raw response/provider result, usage, query ID, error, and fingerprint are operational custody, not acceptance evidence. Missing usage is `unknown`, not zero. |
| `ATTEMPTS.PAYLOAD.request_fingerprint` | Allows deliberate retained-response reprocessing only when it matches. Failed or reserved matches are not retried automatically. |
| `RUNS.CONFIG` | Requested operation, models, limits, and versioned configuration; raw input stays in `INPUT_RAW`. |
| `RUNS.PAYLOAD` | Start/finish times, status, source hashes, coverage, issues, accounting, installed budget, usage, and release reference when produced. |
| `RUNS.PAYLOAD.usage` | Completion/embedding counts, cache hits, SQL statements, query IDs, retained reprocessing, returned usage, and explicit unknown measures. |
| `COVERAGE.PAYLOAD` | Source digest, expected flag, component IDs/counts, status/reason, and selection state. Zero components does not prove no source concern. |

For exact schema fields, use the linked manifest. For install, cache, and rollback
procedure, see [the versioned operation guide](versioned-operation.md).
