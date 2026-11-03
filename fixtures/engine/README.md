# Fictional engine seeds

These six source rows let an operator check whether the installed engine preserves small, known examples. Five sources contain 24 complete events. The sixth row is a note without a parent case identity; mapping excludes that row and retains the row and reason. This is execution and small-example evidence, not a measure of real-client quality.

`synthetic_sources.json` contains a JSON list of source envelopes. Each envelope identifies its mapping through `record_type`. The `support_case` mapping combines case context, attributed notes and messages. The `typed_chat` and `call_transcript` mappings describe independent interactions without inventing a support case. The `linked_note` mapping requires its supplied case link. Notes cannot become direct customer speech, even if their author role is customer.

`configs/engine/synthetic_mapping.json` maps source IDs, metadata and every event field through dotted field paths. An event collection's `path` selects an array; null maps the source envelope itself. `fields` assigns supplied values, and `defaults` assigns explicit source-specific constants. Optional `text_fields` maps readable labels to explicitly permitted source field paths; these values join the existing event text and pass through the same redaction rules. The fixture projects subject, case status and channel into case context so material facts outside the description reach extraction without exposing arbitrary metadata. Collections share source-wide ordinals; the mapper orders them by those ordinals and rejects duplicates. If no ordinal is mapped, the supplied collection order is retained. Timestamps remain null with `timing_status=unknown` when none were supplied. The sales source separately retains supplied relative timing in metadata; that does not establish wall-clock time or vocal qualities.

The mapper retains complete original text in `original_text`. Its `text` field contains permitted redacted evidence. A source may map separately supplied originals, redacted text and redaction status. This fixture instead applies one deterministic replacement to a fictional email address. `redaction.reviewed=true` applies only to these fictional examples. Real client use needs its own mapped evidence, redaction rules and review. There is no claim that this one rule detects other personal information.

The support examples distinguish a monthly charge review awaiting a decision from an annual charge with a reported reversal and unknown bank posting date. Other examples include late secondary concerns, corrections, unresolved negotiation conditions and explicit changes in emotional expression. The note-only source has no direct customer speech; scenario evidence exists, but communication-persona evidence is insufficient. The single guidance item is labelled `fictional`, never `client_approved`.

`expected_facts.json` names source facts, event references and forbidden conclusions for the native smoke reviewer. It must never be passed to extraction, embeddings, prompts or other model inputs. It is not a generated output or an independent quality benchmark. Exact expected facts do not establish that an engine output preserves them; the smoke evidence must record what the actual output did.

`configs/engine/example_config.json` uses general resource placeholders and the approved cumulative smoke limits. It contains no connection secrets. `enable_model_calls=false` keeps the example from requesting inference until an operator explicitly enables the reviewed invocation. The supported CLI owns install, load and run commands; the mapper itself performs no SQL or model calls.

Offline validation:

```sh
python -m pytest tests/engine/test_mapping.py -q
```
