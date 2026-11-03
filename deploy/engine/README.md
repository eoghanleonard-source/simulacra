# Native engine deployment

Run the repository CLI from the repository root. It packages only `snowflake_engine/*.py`; processing runs inside the installed Snowflake Python procedure. Local installation needs Python 3.11 or 3.12 and the packages in `requirements.txt`. The procedure uses Python 3.11 and declares `snowflake-snowpark-python==1.26.0`; account availability must be checked before executing the reviewed install plan.

Generate configuration and an offline plan:

```sh
python -m snowflake_engine.cli configure --database DEMO_DB --schema ENGINE_DEMO_V1 --warehouse DEMO_WH --role ENGINE_OPERATOR --client-id synthetic --model YOUR_AVAILABLE_MODEL --embedding-model YOUR_AVAILABLE_EMBEDDING_MODEL --output /tmp/engine-config.json
python -m snowflake_engine.cli install --config /tmp/engine-config.json --output /tmp/engine-install
```

`install-plan.json` records exact objects, dependencies, package hash and statements. `install.sql` is a review projection; execute installation through the CLI so its version and ownership checks run first. Review the dedicated target schema before adding `--execute`. Supply credentials through `SNOWFLAKE_*` environment variables or explicitly opt into `--env-file PATH`; plans contain no credentials. Both inline `SNOWFLAKE_PRIVATE_KEY` (PEM or base64 DER) and `SNOWFLAKE_PRIVATE_KEY_FILE` are supported, with optional `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`.

An existing database, warehouse and role are prerequisites. An administrator must supply USAGE on the database and warehouse, CREATE SCHEMA on the database for a fresh schema (or ownership/creation privileges within a pre-created dedicated schema), and the Cortex privileges required by the configured models. The CLI does not create roles, grant access or alter warehouses. The operator must own the objects they install and have permission to use Python packages and invoke Cortex. Exact model/region availability remains an account preflight check.

The default configuration is the bounded synthetic smoke profile: six records, 60 events, 12 generation calls, 4096 output tokens per call and 128 embedding inputs. These defaults do not authorise a larger run. A general installation can use a separately reviewed larger positive `limits` profile within the documented absolute ceilings. The first install stores that profile as an immutable `ENGINE_METADATA.BUDGET`. Each native request must stay within the stored profile; a caller cannot increase its approved allowance. Matching reinstall requires the exact original profile. Changing the profile requires a separately approved new dedicated schema.

A matching installation can be rerun without replacing or deleting objects. An interrupted installation retains its version marker so the same plan can finish. The manifest guard rejects a different schema version or changed physical contract before further DDL. Install an upgrade in a new dedicated schema; retain the old schema, packages and procedures for rollback. Content-addressed package and procedure names preserve earlier code deployments. Concurrent installers are unsupported; native processing uses a separately enforced singleton writer lease.

Use the generated `installed-config.json` for `load`, `run`, `inspect` and `export`. They also default to offline plans and require `--execute` to connect. `load` sends raw records and mapping configuration to the native procedure; normalisation and processing execute in Snowflake. `cleanup-plan` only writes an inventory and review instructions. It never deletes objects. Explicit cleanup authority and review of the exact dedicated schema are required before any separately executed deletion.

See [the operator guide](../../docs/snowflake-engine.md) for the full mapped input-to-export workflow and field dictionary. Offline tests establish packaging, identifier/bind handling and deterministic planning. They do not prove account privileges, dependency availability, native execution or semantic output quality.
