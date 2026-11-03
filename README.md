# Simulacra

Turn customer-service and sales conversations into evidence-linked training material: customer situations, observed communication behaviours and draft practice scenarios.

The engine maps source records into a common structure, extracts issues and behaviour with language models, groups related outputs, and stores versioned results in Snowflake. It exports draft scenarios and personas for a training interface. Client-approved guidance supplies correct resolutions; historical conversations alone cannot establish them.

**Current status:** a reusable development foundation for client-specific tuning. Software and delivery checks pass, but independent content evaluation found omissions and meaning errors. Outputs require review before training use. Chat simulation and sales-outcome prediction are outside this repository's supported scope.

## Try the offline demo

Use Python 3.11 or 3.12. From a fresh clone:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install pytest==8.1.1
make demo PYTHON=python
```

The demo runs the engine lifecycle using fictional records, recorded fictional model answers and simulated database execution. It writes `artifacts/demo/offline-lifecycle/proof.json` plus stage outputs and a ledger. Inspect the proof to see the lifecycle checks and provider-call count. It needs no credentials and makes no model or warehouse calls. It demonstrates processing and delivery mechanics; it does not measure fresh extraction quality.

Run the same focused checks as GitHub CI:

```sh
make check PYTHON=python
```

## Install and run on Snowflake

```sh
python -m pip install -e '.[dev]'
python -m snowflake_engine.cli --help
make plan PYTHON=python
```

`make plan` writes an install plan from the fictional configuration without connecting. Follow the [operator guide](docs/snowflake-engine.md) to configure credentials locally, map client data, install the engine, process records and export results. Live execution uses Snowflake services and incurs their charges.

## Find your way around

| Need | Start here |
| --- | --- |
| Map a client's records | [Example mapping](configs/engine/synthetic_mapping.json) and [warehouse intake](docs/engine/warehouse-intake.md) |
| Understand stored fields and outputs | [Data dictionary](docs/engine/data-dictionary.md) |
| Install, process and refresh | [Operator guide](docs/snowflake-engine.md) and [versioned operation](docs/engine/versioned-operation.md) |
| Prepare a client engagement | [Client tuning handover](docs/engine/client-tuning-handover.md) |
| Understand measured limits and versions | [Current source and evaluation decision](docs/engine/git-source-baseline.md) |
| Navigate the code | [Repository layout](docs/repository-layout.md) |
| Make a change | [Contributing](CONTRIBUTING.md) |

## Versions and evidence

Package metadata is **0.4.4** and includes the opt-in `evidence_v1` profile. This Git source is distinct from the archived 0.4.4 rollback and the independently evaluated 0.4.5rc2 candidate. Identify code by commit and archive hash, not the version string alone. The 0.4.5rc2 candidate was rejected for qualification; its [aggregate result](configs/baselines/repaired-baseline-release40-20261103.public.json) is included. Private sources and evaluation references are excluded.

Earlier experiments are documented in [development history](docs/engine/development-history.md). Passing CI establishes software and package checks within their stated scope; it does not establish semantic accuracy or client readiness.

## Source archive

```sh
make release PYTHON=python
```

This creates `dist/snowflake-engine.zip` from an explicit file allowlist. The source archive excludes credentials, private evidence and generated deliveries. The project is licensed under [Apache 2.0](LICENSE).
