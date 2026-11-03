# Repository layout

The supported product in this repository is the Snowflake evidence engine. Its native seed-to-export path has been demonstrated, but three material meaning errors remain in the restored default. A later fidelity candidate was rejected and rolled back. Passing software and citation checks do not establish semantic acceptance or client readiness; [current evidence and limitations](engine/known-limitations.md) explains the qualification boundary. The detailed programme scoreboard remains in the working repository.

## Supported engine

- `snowflake_engine/` contains the engine and CLI.
- `deploy/engine/` contains deployment support and dependency declarations.
- `configs/engine/` and `fixtures/engine/` contain the example configuration, mapping and fictional seed sources.
- `scripts/engine/` contains engine operational and source-release tooling.
- `tests/engine/` contains offline engine tests.
- [The operator guide](snowflake-engine.md) explains use; the [data dictionary](engine/data-dictionary.md) describes stored data, typed marts and exports.

The portable source release is built with `python scripts/engine/package_release.py --output dist/snowflake-engine.zip`. It contains the engine materials needed for that release, not the research archive or the former application.

## Archived prototypes

`archive/legacy/` preserves the former dbt project, training simulation, Streamlit entry point and their historical root deployment and dependency files. The original root README is preserved at `archive/legacy/root/README.md`. These are historical reference material, not the supported engine route. The runtime simulation is outside the current engine scope.

## Retained research and coordination

`artifacts/`, `datasets/`, `evidence/`, `acceptance/`, `FEATURES/`, `dispatch/` and other programme records remain in the working repository as evidence and coordination history. The approximately 1.4 GB of existing artifacts is retained in place, rather than deleted, and is excluded from the portable release. These records can include historical decisions and rejected candidates; consult the scoreboard for the current decision rather than treating any single historical report as current qualification.
