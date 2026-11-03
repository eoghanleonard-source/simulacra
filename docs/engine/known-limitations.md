# Current evidence and limitations

## Stage A result: retain mechanical safeguards, no semantic promotion

Version 0.3.0 implements a fresh `engine-storage-v3` / `engine-export-v3`
candidate, explicit delivery scope, safe original-component lineage, and an
opt-in retained-response replay/import route. The implementation keeps raw
originals and rejections, creates a processing revision rather than rewriting a
source, and leaves the existing default and rollback selection unchanged. It can
sort only recognised citation references using supplied source chronology; it
does not change quotes, attribution, source narrative, duplicates, uncertainty,
or non-citation ordering. A frozen `retained-capture-v1` import validates the
complete source and request identities before processing and does not copy or
alter the prior paid-attempt ledger.

The completed local Stage A replay used the frozen `stage-a/replay-candidate`
packets. It made zero SQL, network, or inference calls. It retained 94 valid
scenarios and 99 valid personas (87 full and 12 explicitly partial), increasing
both-layer delivery from 64 to 93 of 100 histories. Source-support counts moved
from 501 to 520 of 567 issue facts, 190 to 210 of 245 material conditions, and
213 to 213 of 224 observations. On the paired comparison, facts had 24 gains and
5 losses; conditions had 22 gains and 2 losses; observations had 2 gains and 2
losses. These axes overlap and are not a combined accuracy measure.

The limiting result worsened: histories with a material error in a valid record
rose from 38 to 54. The review found zero newly authored claims. Instead, the
replay exposed errors that had previously stayed quarantined. That distinction
explains the mechanism, but does not make the 54-error result acceptable. Keep
the citation-ordering and partial-delivery infrastructure, but do not promote the
combined path semantically or change the default/rollback selection.

Practice was not regenerated in Stage A. The historical result of 2 of 12
objectives exercised therefore remains the current practice finding, not a new
measurement. The frozen packets, [validated summary](../../artifacts/demo/best-supported-engine-01/stage-a/scoring/validated-summary.json), and [aggregate report](../../artifacts/demo/best-supported-engine-01/stage-a/scoring/aggregate-report.json) preserve the scoring evidence. The local v3 export receipt confirms all 100 sources and partial scope were present; no native Stage A deployment has occurred. The optional processor-v3 known-whitelist correction passed 244 focused tests, which verifies that bounded processing behavior, not semantic readiness.

## Historical improvement candidate: rejected for promotion

Version 0.2.1 ran the experimental, opt-in `split_v3` path on the same 100 original ABCD development histories used for the baseline. The histories are human-elicited but fictional; the account, contact, case links and clocks are synthetic. All 100 are development-exposed, and none is client material or client qualification evidence. The candidate used separate scenario and persona calls and retained version lineage plus explicit partial-persona delivery where a valid projection could be separated from a quarantined original.

The candidate increased valid-output support on the fixed reference sets: issue facts rose from 364 to 501, material conditions from 114 to 190, and customer observations from 113 to 213. The axes overlap, so these counts cannot be summed. It produced a valid scenario for 64 histories and a valid persona for 99 histories: 86 full personas and 13 explicitly partial persona projections. These are delivery and coverage results, not a semantic acceptance result.

The same review found the limiting result: histories with a material unsupported claim in a valid record rose from 25 in the baseline to 38 in the candidate. The candidate therefore missed both promotion targets set for the combined capture: 64 both-layer histories against a minimum of 80, and 38 valid-material-error histories against a maximum of 15. The coordination decision rejects the combined candidate for promotion. It retains version lineage and explicit partial-persona preservation as separable capabilities, but neither capability establishes semantic readiness.

The candidate's practice output is also not ready to support learner or client-readiness claims. Its 20 assignments and 16 practice groups exercised only 2 of 12 required objectives; 7 objectives were missing, 2 were partial and 1 appeared only as background. The review found 7 meaningful situation/variant pairs, but says that count is not evidence of adequate trainee preparation. Client-approved handling and assessment criteria remain absent.

The baseline remains preserved rather than overwritten. It ran the 100 histories through five relational seed tables, a dbt SQL mapping view, native Snowflake processing, typed marts and immutable draft export. Of its inputs, 37 had both valid-format scenario and persona records, 37 had scenario only, five had persona only and 21 had no valid component. It produced 77 valid-format scenario cards and 42 valid-format persona cards; 19 responses reached the 4,096-token ceiling and could not be parsed. Independent development review found 32 distinct material unsupported predicates in valid records across 25 inputs. A history is counted if it has at least one such claim; this is not a claim that 25 percent of all facts were wrong.

Both scored runs kept failed and quarantined inputs in their denominators. Their fixed references and independent reviews are development evidence, not a blind generalisation, client review or human-consensus annotation. The candidate evidence is retained under `artifacts/demo/source-grounded-improvement-01/`; the baseline evidence remains under `artifacts/demo/source-grounded-snowflake-01/`. Corpus and programme records are excluded from the portable source release.

## Preserved baseline operating findings

The baseline catalogue retains 119 singleton families and their 119 variants. Of 269 similarity nominations, 32 were retained and four received model judgments. No supported equivalence merge occurred. Review of the retained pairs found useful variants and partial overlaps; 19 overlapping source-only practice groups are a separate manual reference, not an expected engine cluster count or approved handling policy.

The unchanged-input baseline rerun reused 223 cached results, added zero generation or embedding attempts, preserved logical object/family IDs and left the original release identical. The full allocation used 104 generation attempts and 119 embedding inputs. These are bounded operational results.

A diagnosed reader defect treated the derived prevalence ratio `1.0` as different from the same value stored by Snowflake as `1`. The checksum reader now normalises that derived field without changing source/document bytes or tested native release IDs. An externally authored legacy seal using an integer ratio may differ; no external adapter qualification is claimed. The installed baseline writer already produced correct checksums. In-place reinstallation was refused after dbt added co-owned objects to the schema; that guard was not bypassed. Install before creating input objects in a fresh schema. In-place upgrades with co-located source objects need a separately designed ownership contract.

## Preserved synthetic-seed findings

The engine has demonstrated native Snowflake installation, mapped synthetic loading, model-backed extraction, typed marts, immutable draft export and a cache-only unchanged rerun. These are bounded operational results, not client qualification.

The retained default still has three known material interpretation errors in its five-history synthetic seed:

- An opening offer is described as a request.
- A concern about security review becomes a firm signing condition.
- Permission to submit a review becomes a claim that review is the only option.

A later instruction change fixed the third error but retained the first two and introduced regressions. That candidate was rejected and the previous engine restored. Exact attributed citations matched 85/85 in the retained seed, and 152 focused software tests passed before repository cleanup. Those checks do not establish that summaries preserve source meaning. Cleanup adds packaging checks, not a new semantic benchmark.

The retained release has six scenario drafts, six persona drafts and twelve conservative singleton families/variants. Suggested relationships have not been accepted as safe merges. There is no accepted, scored twenty-assignment training catalogue or real-client persona qualification. The original-dialogue exercise above reports coverage of selected source-only observations, not exhaustive population recall.

Client-owned approved handling guidance, representative client data, operating qualification and a verified external platform adapter remain necessary. Historical chats do not supply approved resolution criteria. Output remains draft/unreviewed, requires client guidance and is not client-ready.

The working repository keeps the detailed programme scoreboard and original experiment evidence outside the portable source release. The source release intentionally includes only fictional seeds and supported engine material.


### Resumed pilot closeout — 29 October 2026

Engine 0.3.3 completed the saved twelve-history correction pilot, reusing five successful responses and making seven new calls. Independent review found material errors in seven histories, compared with six before correction; the maximum allowed was two. Ten histories still delivered both a scenario and a persona. The wider batch did not run, and the default remains unchanged. The source-review and practice-planning options remain experimental. The operational fixes preserved stored paid responses, spending records and every generation request. See `artifacts/demo/best-supported-engine-01/r3-pilot-report.md` in the working repository for the measured result and its limitations. This is development evidence; it does not establish client readiness.
