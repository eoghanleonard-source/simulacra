# Offline engine validation

Run these checks from the source root after installing `.[dev]`:

```sh
python -m pytest tests/engine -q
python -m snowflake_engine.cli --help
python scripts/engine/package_release.py --output dist/snowflake-engine.zip
```

The focused tests check mapping, extraction contracts, aggregation, exports, native execution controls, installation plans and source-release packaging. The packaging checks extract the release and exercise offline CLI operations without relying on the older application or dbt code. No credentials, SQL or model calls are needed.

The CLI generates configuration, installation DDL, loading, processing, inspection, export and cleanup plans without connecting unless `--execute` is supplied. Follow the operator guide for those commands. Generated DDL comes from `snowflake_engine/schema.py`; dictionary changes must track that source and the export contract.

These checks establish software and packaging behaviour. They do not establish model interpretation accuracy, safe aggregation, real-client coverage or client readiness. A separate native seed run previously demonstrated bounded operation, but semantic review found unresolved errors; see [current limitations](known-limitations.md).

The working repository preserves original validation receipts and failed experiments under `artifacts/demo/`. Those receipts are excluded from the portable release. Repository cleanup does not turn earlier failed semantic outcomes into passes.
