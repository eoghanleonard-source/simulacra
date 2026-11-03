# Native capture evidence

Engine 0.4.0 adds an explicitly selected `capture_evidence` operation. It runs the
owner-accepted worker/reviewer development profile against existing mapped input,
then retains individual claims, disputes, omissions and checkpoints. This gives
operators a durable evidence trail when a review rejects a claim or a replacement
supersedes it. Capture results remain evidence seeds; they do not become finished
scenario or persona cards.

The accepted profile is `worker-reviewer-v9-dev-20261101`, with
`claude-sonnet-4-6` and the frozen prompts and rubric. The accepted development
result still has **4 material error claims in 2 groups and 6 previously correct
fragments lost**. The owner accepted that configuration despite its failed
zero-material-error/no-loss guards. This integration does not change those
measurements, thresholds or historical verdicts, establish client qualification,
or promise that later tuning will correct the errors. The separate
[frozen working baseline](working-baseline.md) retains its original scope.

Each section owns at most 25 consecutive source events and receives its complete
own conversation as context. Explicit section plans must cover every source
event exactly once, in order. Focal customer speaker IDs must be supplied in
mapped metadata or capture settings. The code never guesses them. Prompts receive
only the permitted source ID, focal IDs and ordered event IDs, positions,
speakers, roles and redacted text. Original text and guidance remain outside the
capture prompt and export.

The controller reviews each candidate, permits one targeted repair per section,
and verifies repaired or reconciled claims separately. An unusable global
control response stops further work. Invalid individual neighbours remain
disputed history. There are no automatic retries. Worker requests use at most
8,192 output tokens; other capture stages use at most 16,384. Complete request
wire input must fit within 90,000 bytes; the controller stops instead of clipping
source or claim content. Installed call, credit and output ceilings still apply.

Storage `engine-storage-v4` adds `CAPTURE_ITEMS` and `CAPTURE_CHECKPOINTS` with
separate `MART_CAPTURE_EVIDENCE` and `MART_CAPTURE_CHECKPOINTS` views. Every row
preserves its run, client, source hash, exact controller record, profile,
execution mode and complete version set. A deterministic row identity includes
the run and client; a content revision commits to the payload. The native writer
appends a missing identity, reuses an identical stored row, and rejects changed
content under an existing identity. It never deletes superseded or disputed
history. The marts expose all append states, including failure records; they do
not assert that every row is currently accepted.

`capture-export-v1` contains all append states, checkpoint request references,
source spans and a current projection for each source. The projection lists the
record IDs currently accepted, disputed or omitted, plus superseded claim IDs.
Exact source span checks establish that evidence text matches the permitted
source; they do not establish that the claim is a faithful interpretation.
Exports always say `requires_client_guidance: true`, `client_ready: false`, and
`semantic_approval: not_established`. Original text, guidance, mapping records
and provider messages are withheld. Original canonical source hashes remain
commitments to stored inputs; a recipient cannot reconstruct withheld originals
from the exported projection. Existing scenario/persona marts and generic
`engine-export-v1`, `engine-export-v2` and `engine-export-v3` contracts retain
their existing definitions.

Use a fresh dedicated schema for v4. The installer refuses an older schema
version or a changed manifest before creating objects. It also retains capture
capability in immutable `ENGINE_METADATA.CAPTURE_CONFIG`, separately from the
installed budget. The selected demo target is
`YOUR_DATABASE.ENGINE_CAPTURE_DEMO`, with recorded execution permitted and
live execution disabled by that installed capability. An ordinary installation
has no capture capability. A caller cannot turn a recorded-only installation
into a live installation by changing its request.

These commands create local configurations or plans by default. The coordinator
adds `--execute` for the already-authorized fresh install and one synthetic
recorded smoke. Replace the uppercase paths and run IDs with the reviewed local
inputs and output paths; keep credential material outside configuration files.

```sh
.venv-demo/bin/python3.12 -m snowflake_engine.cli configure \
  --database YOUR_DATABASE --schema ENGINE_CAPTURE_DEMO \
  --warehouse YOUR_WAREHOUSE --role ACCOUNTADMIN --client-id synthetic \
  --model claude-sonnet-4-6 --embedding-model snowflake-arctic-embed-l-v2.0 \
  --capture-evidence --permitted-execution-mode recorded_smoke \
  --allow-recorded-capture --output CONFIG.json

.venv-demo/bin/python3.12 -m snowflake_engine.cli install \
  --config CONFIG.json --output INSTALLDIR

.venv-demo/bin/python3.12 -m snowflake_engine.cli load \
  --config INSTALLDIR/installed-config.json --input RAW.json \
  --mapping MAPPING.json --run-id LOADID --output LOADPLAN.json

.venv-demo/bin/python3.12 -m snowflake_engine.cli capture-evidence \
  --config INSTALLDIR/installed-config.json --run-id CAPTUREID \
  --input-run-id LOADID --execution-mode recorded_smoke \
  --recorded-responses RESPONSES.json --output CAPTUREPLAN.json

.venv-demo/bin/python3.12 -m snowflake_engine.cli capture-export \
  --config INSTALLDIR/installed-config.json --run-id CAPTUREID --output EXPORT.json
```

Optional `--capture-settings SETTINGS.json` supplies explicit focal IDs or a
complete ordered section plan. `recorded_smoke` requires an installed recorded
capability and `enable_model_calls=false`. Recorded responses use distinct
operation/cache provenance and retain a trace of zero provider calls. A passing
smoke proves that the installed package can route synthetic recorded responses,
retain the state and export it. It does not measure model quality, live Cortex
behaviour, client readiness or accuracy on real conversations. Deployment and
smoke results belong in the coordinator's evidence receipts; these commands
alone are not evidence that they ran.

Rollback selects the preserved previous schema, procedure and package. Keep the
old 0.3.3 procedure/package identified by `A7F3…` available alongside this
installation. Do not change its metadata marker, replace its procedure, or
drop/recreate either schema. This route needs no grants or changes to the
previous default. The frozen 20-file baseline archive remains separate from
the new native package and its source release.
