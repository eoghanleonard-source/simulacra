# Operating the versioned engine candidate

This guide covers the experimental version 0.3.0 engine in this working tree:
storage `engine-storage-v3`, opt-in split extraction v3, and portable export
`engine-export-v3`. It provides an operator procedure for a fresh candidate
installation, comparison, and rollback by selecting an earlier release rather
than changing it.

This is a code-level contract and proposed procedure, not deployment evidence.
It does not show a Snowflake install, dbt load, candidate run, cache-only replay,
baseline-preservation check, semantic improvement, client approval, or external
platform compatibility. Stage A has completed its local 100-source replay and
independent scoring, but it did not meet the bar for semantic promotion: material
error histories rose from 38 to 54. This guide records the retained mechanical
safeguards and the unchanged default/rollback position. The [current programme
direction](../../artifacts/demo/best-supported-engine-01/planning/direction.json)
requires separate evidence for any later semantic promotion.

## What changes and why

Every retained object and export records the version of each material stage. That
prevents a later prompt, mapping, code, or export change from being treated as
the same result. Operators can compare source hashes, version sets, logical
identities, revisions, and release documents.

The candidate also separates engine and dbt schemas. The engine installer refuses
unrelated objects. dbt relations therefore need a second schema so installation
does not adopt or collide with dbt-owned objects.

## Boundaries and prerequisites

Use two new dedicated schemas in the same database.

| Schema | Contents | Must not contain |
| --- | --- | --- |
| `ENGINE_SCHEMA_V3` | Engine tables, views, package stage, and procedure created by the installer. | dbt models, manual input tables, and unrelated objects. |
| `DBT_STAGE_SCHEMA_V3` | dbt-created relation for `load-table`. | Engine metadata, tables/views, package stage, and procedure. |

Replace these names with unquoted identifiers containing letters, digits, and
underscores. Both schemas must be fresh for the candidate; do not point it at the
baseline schema. The installer checks an existing engine schema for the exact
storage version, manifest hash, and installed budget, and rejects unrelated
objects. It does not adopt or alter an older installation.

The dbt relation must expose this input interface.

| Column | Type | Meaning |
| --- | --- | --- |
| `SOURCE_ID` | `VARCHAR` | Unique, non-empty source identity that matches the reviewed mapping. |
| `RAW_RECORD` | `VARIANT` | Complete source object in the existing raw mapping format. |

The native loader accepts only a three-part relation name in the configured
database and configured `source_schema` (or the engine schema without one). It
validates selected rows before retaining raw records. An allowance breach,
duplicate/malformed row, mismatched mapped identity, or missing selected identity
stops the load before raw writes.

## Configure, install, build, load, and run

This is a procedure, not a completion claim. Start in plan mode; add `--execute`
only after confirming the connection, schema names, reviewed mapping, limits, and
authorization.

1. Create a non-secret configuration for the two fresh schemas. Credentials stay
   in the selected Snowflake environment variables or explicit environment file.

   ```sh
   python3 -m snowflake_engine.cli configure \
     --database CUSTOMER_DB \
     --schema ENGINE_SCHEMA_V3 \
     --source-schema DBT_STAGE_SCHEMA_V3 \
     --warehouse REVIEWED_WAREHOUSE \
     --role REVIEWED_ROLE \
     --client-id reviewed-client-id \
     --model reviewed-generation-model \
     --embedding-model reviewed-embedding-model \
     --output candidate-config.json
   ```

   The generated configuration defaults to legacy extraction. Before installing
   this v3 candidate, make the reviewed configuration explicitly set
   `extraction_mode` to `split_v3` and `limits.max_output_tokens` to exactly
   `6144`. The implementation rejects split v3 with any other output-token
   limit. Make that decision before installation: the installer persists the
   limit profile and later requests cannot raise it.

2. Install the engine before creating dbt relations. The command emits a package
   archive, install plan, schema manifest, and installed configuration. A failed
   partial installation is retained for inspection; it is never dropped.

   ```sh
   python3 -m snowflake_engine.cli install \
     --config candidate-config.json \
     --output candidate-install
   ```

   Add `--execute` only after review. Use
   `candidate-install/installed-config.json` afterward because it includes the
   generated procedure identity.

3. Run the environment-owned dbt project to materialize
   `DBT_STAGE_SCHEMA_V3.ENGINE_RAW_RECORDS`, or another reviewed relation, with
   the two required columns. Verify that relation and its selected source count.
   Do not create it in `ENGINE_SCHEMA_V3`.

4. Prepare a thin one-source load plan. Repeat `--source-id` for exact
   identities; the procedure binds values rather than injecting them into SQL.

   ```sh
   python3 -m snowflake_engine.cli load-table \
     --config candidate-install/installed-config.json \
     --relation CUSTOMER_DB.DBT_STAGE_SCHEMA_V3.ENGINE_RAW_RECORDS \
     --mapping reviewed-source-mapping.json \
     --source-id reviewed-source-id \
     --run-id candidate-load-thin-01 \
     --output candidate-load-thin-plan.json
   ```

   Add `--execute` after review. Use a new run ID for every load or processing
   invocation. Omit `--source-id` only for the reviewed full relation and its
   configured source allowance.

5. Use `run` without model calls for cache-only verification. For a bounded
   extraction run, add `--enable-model-calls --execute` only with reviewed
   authorization and budget. The installed budget is an immutable ceiling:
   requests can lower limits but cannot raise them, and failed attempts count.

   ```sh
   python3 -m snowflake_engine.cli run \
     --config candidate-install/installed-config.json \
     --run-id candidate-process-01 \
     --output candidate-process-plan.json
   ```

6. Retrieve a returned release by ID. Retrieval validates shape, identity,
   lineage, attribution, and checksum; it does not approve the release meaning.

   ```sh
   python3 -m snowflake_engine.cli export \
     --config candidate-install/installed-config.json \
     --release-id RELEASE_ID_FROM_RUN \
     --output candidate-release.json \
     --execute
   ```

## Version and identity dictionary

Every v3 version set must contain these exact keys. The export validator rejects
a v3 release with a missing key or an export value other than `engine-export-v3`.

| Version-set key | What it pins |
| --- | --- |
| `storage` | Physical engine contract, currently `engine-storage-v3`. |
| `input_mapping` | Reviewed mapping digest; load-only config may initially say `unresolved-until-input-read`. |
| `extraction_contract` | Extraction schema; intended split candidate is `engine-extraction-schema-v3`. |
| `framework`, `ontology` | Framework-source digest and extraction codebook version. |
| `prompt` | Prompt contract; split v3 uses `engine-extraction-prompt-v3`. |
| `model`, `model_settings`, `embedding_model` | Models and bounded settings that affect output or cache reuse. |
| `code`, `package` | Packaged source digest and package version plus that digest. |
| `aggregation`, `selection` | Grouping/practice versions and their configuration. |
| `export` | Portable release contract, `engine-export-v3`. |

`logical_id` identifies the enduring logical object: a deterministic hash of its
kind and stable business key. `revision_id` identifies exact retained content and
version set: a deterministic hash of logical identity, payload excluding lineage,
and the version set. A changed payload or version gets a new revision without
silently replacing the old one. A derived group with changed membership becomes a
new logical object; it does not claim unchanged meaning.

`RECORD_REVISIONS` preserves earlier states of mutable `RUNS` and `ATTEMPTS`
rows. It never instructs an operator to update a historical release.

## Storage-v3 schema dictionary

All engine tables other than `ENGINE_METADATA`, `LOCKS`, and `RECORD_REVISIONS`
have `LOGICAL_ID`, `REVISION_ID`, and `VERSION_SET` as `VARCHAR`, `VARCHAR`, and
`VARIANT`, in addition to the columns below. Columns can be nullable in storage;
runtime validation decides acceptability. A null means unavailable or inapplicable
evidence, never an inferred value.

| Table | Principal columns | One row retains |
| --- | --- | --- |
| `ENGINE_METADATA` | `SCHEMA_VERSION`, `MANIFEST_HASH`, `BUDGET`, `INSTALLED_AT` | Installation marker and immutable reviewed limit profile. |
| `INPUT_RAW` | `RAW_ID`, `RUN_ID`, `CLIENT_ID`, `PAYLOAD` | Raw input and mapping custody record. |
| `INPUT_SOURCES` | `SOURCE_ID`, `RUN_ID`, `CLIENT_ID`, `CONTENT_HASH`, `PAYLOAD` | Accepted canonical source in a run. |
| `INPUT_ISSUES` | `ISSUE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `REASON`, `PAYLOAD` | Mapping, normalization, or custody failure. |
| `RUNS` | `RUN_ID`, `CLIENT_ID`, `STATUS`, `CONFIG`, `PAYLOAD` | Native operation, configuration, usage, timing, and result. |
| `ATTEMPTS` | `ATTEMPT_ID`, `RUN_ID`, `CLIENT_ID`, `OPERATION`, `REQUEST_KEY`, `STATUS`, `PAYLOAD` | Reserved/completed provider or cache attempt. |
| `COMPONENTS` | `OBJECT_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `KIND`, `VALIDATION_STATUS`, `PAYLOAD` | Scenario/persona result, including quarantine data. |
| `EMBEDDINGS` | `EMBEDDING_ID`, `RUN_ID`, `CLIENT_ID`, `OBJECT_ID`, `MODEL`, `CONTENT_HASH`, `PAYLOAD` | Persisted embedding input/result. |
| `CANDIDATES` | `CANDIDATE_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD` | Nominated similarity pair; not equivalence. |
| `FAMILIES` | `FAMILY_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD` | Draft group with variants, lineage, differences, review state. |
| `RELATIONS` | `RELATION_ID`, `RUN_ID`, `CLIENT_ID`, `PAYLOAD` | Relationship result and exact evidence/differences. |
| `COMPATIBILITY` | `COMPATIBILITY_ID`, `RUN_ID`, `CLIENT_ID`, `PAYLOAD` | Limited source-and-speaker-supported pairing. |
| `COVERAGE` | `COVERAGE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `STATUS`, `PAYLOAD` | Processing and selection accounting for a source. |
| `GUIDANCE` | `GUIDANCE_ID`, `RUN_ID`, `CLIENT_ID`, `SOURCE_ID`, `AUTHORITY`, `PAYLOAD` | Source-linked guidance and explicit authority. |
| `PRACTICE` | `PRACTICE_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `PAYLOAD` | Draft programme, assignments, selection accounting. |
| `RELEASES` | `RELEASE_ID`, `RUN_ID`, `CLIENT_ID`, `STATUS`, `MANIFEST`, `PAYLOAD` | Immutable machine-validated draft export. |
| `RECORD_REVISIONS` | `REVISION_ID`, `LOGICAL_ID`, `RUN_ID`, `CLIENT_ID`, `KIND`, `VERSION_SET`, `PAYLOAD` | Historical operational-row revision. |
| `LOCKS` | `LOCK_KEY`, `OWNER_RUN_ID`, `ACQUIRED_AT`, `PAYLOAD` | Single engine writer lease. |

Typed marts carry the lineage columns forward. `MART_SCENARIOS` and
`MART_PERSONAS` expose draft extraction fields; `MART_FAMILIES`,
`MART_VARIANTS`, `MART_RELATIONS`, and `MART_COMPATIBILITY` expose grouping and
relationship evidence; `MART_EVIDENCE`, `MART_GUIDANCE`, and `MART_COVERAGE`
make provenance/accounting queryable; `MART_RUNS`, `MART_RELEASES`, and
`MART_PRACTICE` expose operational, release, and practice boundaries.

### Explicit delivery scope and safe original linkage

`MART_SCENARIOS` and `MART_PERSONAS` now expose `DELIVERY_SCOPE`,
`PARTIAL_STATE`, the retained/withheld child detail, projection identity, and
the original component's lineage. An export labels each delivered component as
`full_component`, `validated_children_only`, or `not_recorded`; it never treats
structural validation as proof that a component is complete.

For a partial persona, the mart and export retain a versioned link to the
quarantined original. The join uses client ID, run ID, source ID, and projected
object ID together. This prevents an object ID from accidentally linking to an
older run or another client. A partial original must have exactly one explicit
projection; relabelling that projection as complete is rejected. Scenario scope
is visible, but this release does not add scenario child projections.

## Extraction-v3: separate layers and partial state

Set `extraction_mode` to `split_v3` for the v3 path. It sends two independently
bounded requests for every complete permitted source: scenario and persona. The
implementation reserves a 6,144-token output budget for each call. It does not
omit source events to fit an input limit; an over-limit complete source fails
before inference.

V3 adds a source-linked assertion to every scenario need and persona action:
`actor`, `recipient`, `action`, `scope`, `state`, and `certainty`. It can retain
that an offer is not a request, a reported completion is not a source-recorded
completion, and an explicit statement is not independent proof. Structural
validation of these fields is not proof that the interpretation is correct.

Persona actions, sequences, emotional expression, and emotional changes are
validated independently. An invalid child is quarantined with its reason; it does
not erase valid children or get a blanket pass. When allowed, the candidate may
create a `validated_children_only` persona projection. It identifies
`projection_of`, `partial_layers`, `withheld_layers`, and `partial_state`; the
original quarantined payload remains retained. Unknown emotion remains unknown.

## Cache identity, deterministic replay, and safe reuse

For a provider request, the cache key hashes operation, supplied request key,
complete request content, selected model, full version set, and maximum output
tokens. Embeddings use the selected embedding model in that identity. Requests
that differ in prompt, mapping-derived content, model, relevant settings,
code/version lineage, or output cap therefore cannot collide merely because they
share a source ID.

The default extraction path is unchanged. `source_ordinal_v1` is an explicit,
cache-only opt-in for split v3 processing. It can sort only recognised citation
reference arrays where every reference has the required evidence shape and a
known event ID. It uses supplied source chronology only for those references.
It does not reorder the source narrative, a quote, role, speaker, authority,
event ID, duplicate, uncertainty, assertion, emotional endpoint, or any
non-citation array. If a reference is malformed or unknown, the whole citation
array remains in its original order.

Replay passes the original and processed payloads through the same envelope,
component, child, and citation validators. It creates a new processing revision
with original and processing version lineage; it is not a source edit. The
original raw response, original result, and rejection remain in an immutable
receipt alongside the processed result. A replay may reuse a successful raw
response only when its complete source/request fingerprint, model, prompt,
settings, and output limit match. A failed or unresolved matching key is not
retried automatically. A cache-only miss stops the affected work and cannot
silently make a model call.

For a fresh v3 installation, use an explicit frozen `retained-capture-v1`
manifest rather than copying an old paid-attempt ledger. The CLI accepts the
artifact through `--retained-captures`; it selects `source_ordinal_v1`, retained
reprocessing, and model calls disabled. Before it writes sources or components,
the runtime checks the complete source set, two independent scenario/persona
attempts per source, source hashes, original versions, request keys and complete
request fingerprints. It also binds the canonical manifest digest and the input
file byte digest. Imported responses are recorded as import receipts and usage,
not as new paid attempts; the original paid ledger remains unchanged.

For example, produce a plan first (without `--execute`):

```sh
python3 -m snowflake_engine.cli run \
  --config candidate-install/installed-config.json \
  --run-id candidate-retained-replay-01 \
  --retained-captures frozen-retained-captures.json \
  --output candidate-retained-replay-plan.json
```

Treat the artifact path as provenance only. The digest and the identities above,
not the path text, determine whether the retained evidence is accepted.

## Export-v3 and release dictionary

`engine-export-v3` is a content-addressed portable draft. Its manifest includes
the exact `version_set`; every exported row has `logical_id`, `revision_id`, and
a matching `version_set`. `storage_logical_id` and `storage_revision_id` retain
storage identity separately where a row derives from a stored endpoint.
`release_id` and `content_sha256` detect document mutation, not a signature or
business approval.

The release remains `draft`, `unreviewed`, `client_ready: false`, and
`semantic_acceptance: false`. It records external platform schema as `unknown`
and adapter as `unverified`. Original text and raw mapping records remain in
storage. A structurally valid export does not establish semantic support, client
guidance, readiness, or a working external adapter.

The v3 validator requires the complete version set, recomputes portable
identities, checks retained-endpoint lineage, verifies permitted citations and
source hashes, and checks explicit delivery-scope metadata. Practice output is
allowed in versioned exports. It selects no more than 20 draft assignments; groups do not assert
equivalence, approved handling, or assessment eligibility.

## Older releases and rollback

The export reader recognizes `engine-export-v1`, `engine-export-v2`, and
`engine-export-v3`. V1 and v2 keep their legacy reader paths and are read-only;
the known pre-change v2 storage marker is also read-only. This is compatibility,
not an in-place migration. Existing schemas and releases remain readable and
immutable.

To roll back:

1. Stop selecting the candidate schema/procedure in the operator configuration.
2. Select the preserved baseline configuration and release ID; do not copy
   candidate rows into it.
3. Read and validate the baseline release with its compatible reader.
4. Retain candidate install plans, manifests, run output, query IDs, releases,
   and failures as comparison evidence. Do not drop the candidate schema merely
   to roll back.

If installation fails partway through, preserve it and its evidence. Recovery is
to rerun the same manifest or have an operator review the retained partial
installation. Deletion needs separate cleanup authority after confirming the
exact dedicated target and no unrelated objects.

## Evidence still required before a decision

| Question | Evidence to retain |
| --- | --- |
| Was the candidate installed safely? | Install plan/manifest, installed configuration, execution result, and Snowflake query IDs for the fresh engine schema. |
| Did dbt and loader use separate schemas? | dbt relation identity/columns, `load-table` result, mapping digest, selected source IDs, and input accounting. |
| Did the candidate improve the frozen corpus? | Frozen version set/configuration, paired source hashes, numerical before/after table, component retain/reject decision, and independently scored outputs. |
| Did replay avoid new inference? | Cache-only result showing retained cache use and zero new generation/embedding attempts, plus attempt ledger. |
| Did retained import preserve the old ledger? | Imported-capture count and immutable import receipt, plus a comparison showing no added or changed paid attempts. |
| What did Stage A show on the frozen 100-source measure? | Local replay of frozen `stage-a/replay-candidate` packets made zero SQL, network, or inference calls. Valid scenarios/personas were 94/99 (87 full and 12 partial); both-layer delivery rose 64→93. Support was 501→520/567 facts, 190→210/245 conditions, and 213→213/224 observations. Paired gains/losses were 24/5, 22/2, and 2/2. Material-error histories rose 38→54, with zero newly authored claims—the replay exposed prior quarantined errors. **Decision:** retain mechanical ordering/partial infrastructure; no combined semantic promotion or default change. |
| Was Stage A locally exportable? | The [local v3 export receipt](../../artifacts/demo/best-supported-engine-01/evidence/stage-a-local-export-receipt.json) verified all 100 sources and explicit partial scope. This is local evidence only; no native Stage A deployment occurred. |
| What remains unresolved? | Practice was not regenerated, so the historical 2-of-12 objective result remains unresolved. The optional processor-v3 known-whitelist correction passed 244 focused tests; that validates bounded processing behavior, not semantic quality. |
| Was the baseline preserved? | Baseline release ID, compatible-reader digest/byte comparison, and proof no baseline schema/release was overwritten. |
| Can outputs be used outside the engine? | Separately verified platform mapping/adapter; none is supplied or implied here. |

Until that evidence exists, call this a candidate implementation and operator
procedure. Do not call it deployed, semantically improved, client-qualified, or
ready for an external platform.

## Implementation references

The contract source is the candidate code, not copied DDL:
[storage manifest](../../snowflake_engine/schema.py), [version/revision functions](../../snowflake_engine/versioning.py),
[native cache and lineage runtime](../../snowflake_engine/native.py), [CLI commands](../../snowflake_engine/cli.py),
[split extraction v3](../../snowflake_engine/extraction.py), [v3 contracts](../../snowflake_engine/contracts.py),
[practice selection](../../snowflake_engine/practice.py), and [export reader/validator](../../snowflake_engine/export.py).

The [data dictionary](data-dictionary.md) documents the current v3 contract.
Older v1/v2 documents remain reader-compatible only; this candidate guide does
not silently migrate or rewrite them.


### Resumed pilot closeout — 29 October 2026

Engine 0.3.3 completed the saved twelve-history correction pilot, reusing five successful responses and making seven new calls. Independent review found material errors in seven histories, compared with six before correction; the maximum allowed was two. Ten histories still delivered both a scenario and a persona. The wider batch did not run, and the default remains unchanged. The source-review and practice-planning options remain experimental. The operational fixes preserved stored paid responses, spending records and every generation request. See `artifacts/demo/best-supported-engine-01/r3-pilot-report.md` in the working repository for the measured result and its limitations. This is development evidence; it does not establish client readiness.
