# Load source records from Snowflake

`load-table` lets a Snowflake table or view supply raw source records to the native engine. The CLI sends the relation name, mapping and optional source IDs to the stored procedure. The procedure reads and normalizes the records inside Snowflake, then retains them in `INPUT_RAW` for the existing `run` command. The CLI does not fetch raw source rows.

The existing JSON `load` command remains available. dbt is optional: it can build the input relation, but the engine does not require dbt to load or process it.

The relation must expose these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `SOURCE_ID` | `VARCHAR` | Unique, nonempty source identity matching the identity produced by the mapping |
| `RAW_RECORD` | `VARIANT` | Complete, nonnull source object in the existing raw mapping format |

Use an explicit three-part name such as `YOUR_DATABASE.ENGINE_DEMO.ENGINE_RAW_RECORDS`. All three parts must use unquoted letters, digits and underscores, with a letter or underscore first. The database and schema must match the engine configuration. Install the engine in its dedicated schema before creating dbt source tables and models there: the installer checks for unrelated pre-existing objects.

Prepare a plan for one source first:

```sh
python -m snowflake_engine.cli load-table \
  --config installed-config.json \
  --relation YOUR_DATABASE.ENGINE_DEMO.ENGINE_RAW_RECORDS \
  --mapping source-mapping.json \
  --source-id reviewed-source-id \
  --run-id source-load-thin-01 \
  --output source-load-thin-plan.json
```

Add `--execute` to perform the load using the configured Snowflake connection. Repeat `--source-id` to select several exact identities; values are bound parameters. Every selected identity must exist. Omit `--source-id` to load the complete relation within the configured source allowance. Each load and processing invocation needs a new run ID.

The native procedure selects only `SOURCE_ID` and `RAW_RECORD`, orders by source ID, and reads at most `max_sources + 1` rows. That extra row detects a relation above the reviewed source allowance; it causes failure before raw records are written. Duplicate IDs or raw objects, null or malformed objects, mismatched mapped identities, and missing selected IDs also stop the load before those writes. The existing mapping still reports excluded records and counts all supplied events; an event allowance violation stops the load before writes.

After a successful load, use the existing `run` command with `--enable-model-calls` only when inference is authorized. A thin load and processing run can precede a complete load and processing run in the same installation. Unchanged source requests can reuse the retained inference cache. A later run without `--enable-model-calls` verifies cache-only operation; a missing cache entry stops the affected work under the existing controls.

Loading preserves source event timestamps as supplied. A missing timestamp remains null with unknown timing. Any generated demonstration clock belongs in separate metadata and must not be presented as an observed source event time.

This interface does not change models, extraction requests, prompts, output contracts, aggregation thresholds or installed allowances. It also does not replace old packages, reset attempt ledgers or establish semantic quality. Review the retained load accounting and processing results before making quality claims.
