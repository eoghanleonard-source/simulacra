# Repository layout

This public repository contains the deployable engine, fictional demo inputs, operational tooling and software tests.

| Path | Purpose |
| --- | --- |
| `snowflake_engine/` | Python engine, CLI, extraction contracts, Snowflake schema and execution, grouping and export |
| `configs/engine/` | Fictional configuration and source mapping |
| `fixtures/engine/` | Fictional source records and expected facts |
| `deploy/engine/` | Installation support and recorded fictional delivery fixtures |
| `scripts/engine/` | Packaging, offline CI, preflight, recovery and export inspection |
| `scripts/demo/` | Offline lifecycle proof and supported-record composition helper |
| `tests/engine/` | Software tests; some historical checks skip without private artifacts |
| `docs/engine/` | Data dictionary, operations, current limitations and development evidence |
| `configs/baselines/` | Aggregate historical evaluation decision; no raw evaluation sources |
| `.github/workflows/` | Offline source checks on pushes and pull requests |

Start with the [README](../README.md), then the [operator guide](snowflake-engine.md). The [source and evaluation decision](engine/git-source-baseline.md) identifies the current code and its limits.

Local credentials, datasets, private references, research archives and generated exports are excluded. Historical documents may refer to local evidence paths unavailable in this public checkout. Earlier dbt and simulation prototypes are preserved separately in the private working archive, not distributed here.

The source ZIP uses the explicit allowlist in `scripts/engine/package_release.py`. Adding a runtime dependency or document needed in that ZIP requires updating the allowlist. Repository-only collaboration files need not be shipped with the engine.
