# Recorded delivery fixtures

These fixtures are wholly fictional. They demonstrate the engine's recorded
`split_v3` path with two small histories: a telescope inspection request and an
observatory-lamp quotation request. They contain no credentials, account names,
customer material, or provider output.

`fictional-mapping.json`, `fictional-raw-records.json`, and
`baseline-recorded-responses.json` form one runnable baseline fixture set.
`recorded-smoke-config.template.json` pins the only compatible recorded-run
profile: split v3, 6,144 output tokens, no model calls, two sources, six
events, eight generation calls, 1,000 SQL statements, and no aggregation.
`lifecycle-fixture.json` records the same source text and the four deliberately
bounded lifecycle stages. The recorded response JSON was authored from these
fictional facts; it is not a model call or a semantic review.

The stages are:

- `baseline`: records four fixture responses for two sources.
- `unchanged`: reuses the same four retained responses.
- `changed`: changes one fictional source and records two new responses for it.
- `failed`: sends deliberately malformed fixture JSON for the changed source;
  the engine retains the resulting issues while preserving the other source.

The fixture is suitable for a local, offline smoke proof only. It does not
establish Snowflake compatibility, provider metering, model quality, client
mapping, client approval, or real-world operational readiness.
