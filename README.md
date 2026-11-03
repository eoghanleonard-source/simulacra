# Snowflake evidence engine

Current source baseline: package metadata remains **0.4.4**, with an explicit
opt-in evidence-binding profile. The separately frozen **0.4.5rc2** development
candidate failed independent forty-source qualification and was not promoted to
the default. A working export does not establish content quality. See
[Git baseline, offline CI and the retained evaluation decision](docs/engine/git-source-baseline.md).

Run the focused source-only checks with `python scripts/engine/offline_ci.py`
after installing `pytest==8.1.1`. They need no Snowflake account or credentials.

The engine turns permitted source records into source-backed scenario and communication-persona drafts, typed Snowflake marts, and an immutable draft export. The supported path uses the Python CLI and a Snowflake stored procedure; dbt and the former training simulation are not needed.

Native installation, processing, typed marts, export and a cache-only rerun were first exercised on 100 original human-elicited fictional ABCD dialogues with neutral synthetic client records. That baseline retained 77 scenario drafts and 42 persona drafts in valid format; 19 responses hit the output limit, and independent review found material overstatements in valid records from 25 inputs.

Version 0.2.1 was then exercised on the same 100 development histories with the experimental, opt-in `split_v3` extraction path. It improved valid-output support for issue facts from 364 to 501, material conditions from 114 to 190, and customer observations from 113 to 213. Those are separate, overlapping scoring axes and must not be added together. It retained valid scenarios for 64 histories and personas for 99 (86 full and 13 explicitly partial). However, the number of histories with a material error in a valid record rose from 25 to 38. The combined candidate was rejected for promotion: it fell short of the 80-history target for both layers (64 observed) and exceeded the maximum of 15 material-error histories (38 observed). Its draft practice programme exercised only 2 of 12 required objectives.

Version 0.3.0 adds experimental infrastructure: `engine-storage-v3`,
`engine-export-v3`, explicit full/partial delivery scope, safe original-component
lineage, and opt-in retained-response replay/import. Replay orders only
recognised citation references by supplied source chronology; it preserves raw
originals and rejections and creates a processing revision rather than editing a
source. A frozen `retained-capture-v1` file can be selected with
`run --retained-captures` for zero-inference replay; it validates the complete
preserved identity set and does not alter the original paid-attempt ledger.

The completed local Stage A replay made zero SQL, network, or inference calls.
It increased both-layer delivery from 64 to 93 of 100 histories, with 94 valid
scenarios and 99 valid personas (87 full, 12 partial). Source support moved from
501 to 520 of 567 issue facts and 190 to 210 of 245 material conditions, while
observations stayed at 213 of 224. The paired changes were 24 gains/5 losses for
facts, 22/2 for conditions, and 2/2 for observations. However, histories with a
material error in a valid record rose from 38 to 54. The review found no newly
authored claims; replay surfaced errors that had previously been quarantined.
That does not justify accepting the higher error result. The mechanical
ordering/partial infrastructure is retained, but the combined path is not
semantically promoted and the default/rollback selection is unchanged. Practice
was not regenerated, so the previous 2-of-12 objective result remains
unresolved. All 100 histories are fictional development material; no client
qualification is claimed. See [current evidence and
limitations](docs/engine/known-limitations.md) and the [versioned operator
guide](docs/engine/versioned-operation.md).

## Get started locally

Use Python 3.11 or 3.12 in a virtual environment:

```sh
python -m pip install -e '.[dev]'
python -m snowflake_engine.cli --help
python -m pytest tests/engine -q
```

The CLI plans operations without connecting by default. Live Snowflake actions require explicit execution and appropriate authority. Follow the [operator guide](docs/snowflake-engine.md) for configuration, review, installation, loading, processing and export.

The fictional seed comprises [example configuration](configs/engine/example_config.json), [mapping](configs/engine/synthetic_mapping.json), and [source records](fixtures/engine/synthetic_sources.json). The [data dictionary](docs/engine/data-dictionary.md) describes the typed marts, their fields and provenance.

## Source release and repository layout

Build the portable engine source release locally:

```sh
python scripts/engine/package_release.py --output dist/snowflake-engine.zip
```

The [repository layout](docs/repository-layout.md) explains the supported engine, archived prototypes, and retained research and coordination material. Evidence and datasets remain in the working repository and are excluded from the portable source release. The release does not include the legacy simulation.

An optional repository example in `examples/source_grounded_dbt/` uses isolated dbt Core to prepare source tables in Snowflake. It is separate from the engine runtime and portable source release.


### Resumed pilot closeout — 29 October 2026

Engine 0.3.3 completed the saved twelve-history correction pilot, reusing five successful responses and making seven new calls. Independent review found material errors in seven histories, compared with six before correction; the maximum allowed was two. Ten histories still delivered both a scenario and a persona. The wider batch did not run, and the default remains unchanged. The source-review and practice-planning options remain experimental. The operational fixes preserved stored paid responses, spending records and every generation request. See `artifacts/demo/best-supported-engine-01/r3-pilot-report.md` in the working repository for the measured result and its limitations. This is development evidence; it does not establish client readiness.
