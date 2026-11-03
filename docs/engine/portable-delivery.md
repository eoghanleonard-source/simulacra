# Portable delivery package

This package gives an operator a bounded, offline way to inspect the engine,
build an installation or load plan, exercise wholly fictional recorded fixtures,
and read a released export with the reference consumer. It does not contain
credentials, client records, programme transcripts, or a usable connection
configuration. Historical account and object examples remain in documentation
and fictional templates, so the package does not claim to exclude every
account-specific identifier. No credential was found by the portable review.
The consequence is that it can be reviewed and copied safely, but it cannot
prove a client deployment or run against a live warehouse by itself.

## What the package contains

The deterministic source archive contains the engine source, the command-line
planner, the data dictionary, `reference_consumer.py`, and a fictional recorded
delivery fixture. The fixture has two invented histories: a telescope-lens
inspection request and an observatory-lamp quotation request. It is labelled
fictional so it cannot be mistaken for customer evidence.

The archive also retains two bounded verification helpers:

- `scripts/demo/compose_supported_record.py` copies a selected, already
  supported persona unit between frozen exports only when its source, speaker,
  schema and exact evidence all agree. It does not write new semantic content.
- `scripts/demo/prove_recorded_lifecycle.py` runs baseline, unchanged, changed,
  and malformed-response stages through `native.run`. It imports the packaged
  `tests/engine/test_native.py` session double to simulate the Snowpark storage
  boundary. The helper needs the development test dependency (`pytest`) because
  that test module imports it.

The fixture files under `deploy/engine/fixtures/recorded-delivery/` are useful
for reviewing the mapping and fictional input boundary. The lifecycle proof has
its own embedded fictional seeds so its historical evidence stays reproducible.

## Install and make an offline plan

Use Python 3.11 or 3.12. Install the source package with development tools when
you intend to run the lifecycle proof:

```sh
python -m pip install '.[dev]'
python -m snowflake_engine.cli install \
  --config configs/engine/example_config.json \
  --output install-plan
```

This writes a reviewable plan. It does not connect or install anything until an
operator adds the CLI's explicit execution option with a reviewed, local
configuration. Keep credentials outside this archive.

To inspect the fictional input mapping or construct one stage of its recorded
request, run:

```sh
python scripts/demo/compose_supported_record.py --help
python scripts/demo/prove_recorded_lifecycle.py --output lifecycle-proof
```

The second command is local-only and should show four stages with fixture
attempts `4, 0, 2, 2`, cache hits `0, 4, 2, 2`, statuses `complete, complete,
complete, partial`, and zero provider calls. The final partial stage is
intentional: malformed recorded JSON is retained as visible input issues.

## Mapping and data dictionary

Map an input only through a reviewed configuration. Each record type defines a
stable source ID, event order, speaker/role fields, and the customer-side
speaker identifiers. The portable fictional mapping is in
`deploy/engine/fixtures/recorded-delivery/lifecycle-fixture.json`; it has no
client-specific field names or identities. A production mapping requires a
separate review because a field mismatch can attach evidence to the wrong
source or speaker.

The output marts are query-oriented views over retained records:

| Mart group | What it exposes | Important boundary |
| --- | --- | --- |
| `MART_SCENARIOS`, `MART_PERSONAS` | Valid and quarantined capture components, including delivery scope | Structural validity is not semantic approval. |
| `MART_EVIDENCE`, `MART_GUIDANCE`, `MART_COVERAGE` | Quotes, guidance status and source-processing accounting | A source link is not evidence that guidance is approved. |
| `MART_FAMILIES`, `MART_VARIANTS`, `MART_RELATIONS`, `MART_COMPATIBILITY` | Draft grouping and limited pairing records | Similarity does not establish equivalence. |
| `MART_RUNS`, `MART_RELEASES`, `MART_PRACTICE` | Run, immutable release, and practice-programme boundaries | A release remains draft unless separately reviewed. |
| `MART_CAPTURE_EVIDENCE`, `MART_CAPTURE_CHECKPOINTS` | Append-only capture evidence and checkpoint state | Capture evidence still requires client guidance; it is not a finished or accepted card. |

Field-level meanings, version identities, partial-delivery fields and export
shape are defined in `docs/engine/data-dictionary.md`.

## Read and export a release

The reference consumer stores the original accepted bytes, reconstructs the
canonical document from its rows, and resolves assignment links. It never
approves content or writes to an external learning platform.

```sh
python scripts/engine/reference_consumer.py --store consumer.sqlite ingest release.json
python scripts/engine/reference_consumer.py --store consumer.sqlite inspect RELEASE_ID
python scripts/engine/reference_consumer.py --store consumer.sqlite export RELEASE_ID rebuilt-release.json
```

`export` refuses to overwrite an existing file. Add `--original-bytes` only
when the exact initially accepted JSON bytes, rather than canonical JSON, are
needed for an audit comparison.

Use the engine `export` command with explicit execution only after an installed
native procedure has returned a release ID. Exports retain source, version and
delivery-scope lineage; they do not establish client approval.

## Recorded versus live operation

Recorded smoke mode accepts only an installation-authorised recorded-response
contract and rejects live provider calls, embeddings, unknown operations, and
extra fixture calls. It is suitable for deterministic control-path checks.

Live operation needs separate authority, a reviewed mapping, a dedicated schema,
verified account privileges and a bounded installed budget. The portable package
does not contain those inputs. It therefore does not itself establish account
charges, model quality, unattended semantic correctness, external platform
mapping, or client readiness. Separate same-cycle native evidence may establish
its own stated SQL-execution facts; do not treat that evidence as coming from
this portable archive.

## Clean native install and recorded run

Use a new dedicated schema. Start from
`deploy/engine/fixtures/recorded-delivery/recorded-smoke-config.template.json`;
replace its fictional database, schema, warehouse and role with reviewed
identifiers while retaining its client ID, provider-disabled mode, split-v3
mode, aggregation settings and limits. The exact response fixture is bound to
that fictional client ID and model label. These commands first create plans;
append `--execute` only after the account, privilege, mapping and budget review
is complete. The archive has no credentials and never creates roles or grants
access.

```sh
python -m snowflake_engine.cli install \
  --config deploy/engine/fixtures/recorded-delivery/recorded-smoke-config.template.json \
  --output recorded-install

python -m snowflake_engine.cli load \
  --config recorded-install/installed-config.json \
  --input deploy/engine/fixtures/recorded-delivery/fictional-raw-records.json \
  --mapping deploy/engine/fixtures/recorded-delivery/fictional-mapping.json \
  --run-id fictional-load-001 --output recorded-load-plan.json

python -m snowflake_engine.cli run \
  --config recorded-install/installed-config.json \
  --run-id fictional-recorded-001 --execution-mode recorded_smoke \
  --recorded-run-responses deploy/engine/fixtures/recorded-delivery/baseline-recorded-responses.json \
  --output recorded-run-plan.json
```

For an authorised native smoke execution, rerun `install`, `load`, and `run`
with `--execute` one operation at a time, preserving each generated receipt.
`recorded_smoke` remains provider-disabled: the fixture responses are exact
pre-authored JSON tied to source IDs, hashes, and the fictional
`fictional-lifecycle-only` client ID, not live model output. Use a separate
reviewed client ID and a separately generated recorded fixture for any other
environment.

### Version boundary for the 2 November 2026 delivery

The installed native procedure used for the previous recorded evidence remains
version `0.4.2`; this package does not alter that installed procedure. The
previous portable reader/source was version `0.4.3`. The new portable source
archive is version `0.4.4`. It retains the narrow reader restoration path for
two usage-credit values where a Snowflake `VARIANT` representation changed JSON
`0.0` to `0` during readback. Restoration is permitted only when the original
release seal and every export validator agree; it never reseals, edits, or
broadens a release.

Version `0.4.4` also makes one generic native persistence correction. A known
normalized source ID is now stored exactly on an input issue. When no source is
known, the insert omits nullable `SOURCE_ID`, allowing Snowflake to apply SQL
`NULL` rather than converting a Python `None` bind to text. It does not infer a
source, alter the storage-v4 schema, modify semantic content, or revise prior
recorded results.

The new `0.4.4` native installation completed at 06:32:29 UTC on 2 November
2026 in fresh schema `WR_DELIVERY_20261102_R02`, using procedure
`RUN_ENGINE_V2_A24E92C99495` and core package SHA-256
`a24e92c99495106406d60b1d4941111ad3e565e0c9494acd061250abd93223b0`.
The separately retained native evidence records all 13 checks passing: four
valid original-seal exports, unchanged service controls, changed and failed
sales handling, two truthful known-source issue links, and prior-schema
immutability. This establishes those bounded native facts; it does not change
the limits on model quality, client mapping, semantic acceptance, or readiness.

## Roll back safely

Do not overwrite an old release or reuse its run ID. Preserve the old source
archive, installed configuration and release bytes, then select the previous
immutable release for downstream consumption. Install a code or budget change
in a new dedicated schema; do not alter the existing schema to force an
upgrade. A recorded or local failure should remain visible as a retained
attempt, issue or proof artifact rather than being deleted.
