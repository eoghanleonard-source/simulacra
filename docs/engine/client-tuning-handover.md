# Reusable foundation for client tuning

This handover freezes a working development foundation for mapping conversations and case notes into a versioned library of customer situations, observed behaviour, personas and practice assignments. It is the starting point for a client engagement. The owner's “roughly eighty percent” describes that intent; it is not a measured readiness score.

## What is ready to reuse

The central engine and command-line tools install on Snowflake through configuration and mappings. Input history, exact evidence, unresolved records, guidance status, source versions and earlier releases remain visible. The same implementation handles the fictional service and sales examples without a source-specific engine branch.

The final sales and composed service examples have been independently reviewed on exposed development data. The local reference consumer ingested them, reopened its storage, resolved all 28 assignments and 44 persona joins, and reproduced their exports. An earlier service interaction was restored exactly in the persona library; the current assignments do not exercise that persona. The original regression result remains part of the history.

These examples include source-reviewed manual additions and corrections. Their quality does not prove that a new unattended model run will reproduce it. Communication observations describe what happened in a conversation, not a customer's enduring personality or hidden motivation.

Recorded-response tests exercise initial processing, unchanged reuse, changed history and malformed answers for both domains. The actual Snowflake sales repair installed engine 0.4.4 in the dedicated development schema `YOUR_DATABASE.ENGINE_DELIVERY_DEMO`; the earlier service proof used installed 0.4.2. Both preserve previous releases. Reader 0.4.3 introduced a narrow numeric-representation recovery that 0.4.4 retains; it accepts the original release checksum and never reseals a changed document. Fictional recorded tests establish software operation, not fresh-model quality.

## Start an engagement

1. Agree which work the client wants trainees to recognise and practise. Include consequential exceptions and differences that change handling, rather than treating every similar conversation as another exercise.
2. Obtain representative, authorised case notes and conversations. Map stable history IDs, event IDs, order, roles and customer-side speakers using the [input mapping guide](warehouse-intake.md) and [field dictionary](data-dictionary.md). Validate attribution and redaction on a small sample before loading a population.
3. Supply approved policies, handling guidance and review ownership. Historical statements must not become policy or a claim that an issue was resolved. Keep missing guidance explicitly unresolved.
4. Select a dedicated schema, an existing approved warehouse and role, the model and finite limits. Keep credentials outside the source package. Follow [portable installation and recorded smoke instructions](portable-delivery.md) and [versioned operation](versioned-operation.md). The fictional configuration keeps model calls disabled; a recorded pass does not enable live inference.
5. Run a small, declared live cohort only under a bounded allocation. Review whether essential conditions, corrections, uncertainty and communication differences survive; tune the mapping, extraction and selection with the client. Reuse saved valid answers before regenerating them.
6. Connect the intended learning platform or downstream application. Resolve actual scenario/persona/assignment joins, source evidence, unresolved status and guidance through that adapter, then test its export and access rules. The included SQLite consumer is a reference implementation, not this client integration.

## Inspect and deliver outputs

Use the typed marts for scenarios, personas, evidence, guidance, coverage, draft families and practice records. Their exact fields and capture-only boundaries are in the [dictionary](data-dictionary.md). A valid row is not semantic approval; a draft release is not client readiness.

Export an existing release through the engine CLI, using the installation's saved non-secret configuration:

```sh
python -m snowflake_engine.cli export \
  --config installed-config.json --release-id RELEASE_ID \
  --output release.json --execute
python scripts/engine/reference_consumer.py --store consumer.sqlite ingest release.json
python scripts/engine/reference_consumer.py --store consumer.sqlite inspect RELEASE_ID
python scripts/engine/reference_consumer.py --store consumer.sqlite resolve RELEASE_ID ASSIGNMENT_ID
python scripts/engine/reference_consumer.py --store consumer.sqlite export RELEASE_ID rebuilt-release.json
```

Use an existing authorised credentials environment or the CLI's explicit `--env-file` option; never put credentials into the archive. The consumer refuses an existing export destination. Retain rejected input and its errors when validation fails.

## What remains client work

Representative client data, correct mappings, approved handling, the actual platform interface and access rules have not been provided. The client must also choose review effort, operating volume, latency, change ownership and rollback policy. New/unfamiliar-source quality and unattended operation remain to be demonstrated within that declared population.

Exact cost per independently useful retained situation remains unknown. Query-level compute records can be delayed; warehouse idle time, AI usage, account prices and human review are separate costs. Do not divide fictional smoke costs by manually authored development examples to claim production margins. The closeout cost evidence records a later eligible metering check and a bounded reconciliation proposal. There is no new budget or inference round in this handover.

## Preserve and roll back

Retain this source archive, its content manifest, installation configuration, input/response custody and immutable releases. Keep previous archives and schemas. To roll back downstream use, select the prior release explicitly. Do not overwrite an old release, reuse its run ID, suspend a shared warehouse, or change roles/grants to force a successful run. Further client tuning should create a new version and preserve its comparison evidence.

This closes the autonomous foundation-building round. Remaining qualification gaps are disclosed engagement inputs; no default, production or client-ready promotion is implied.
