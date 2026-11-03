"""Frozen production instructions; all stages share the same source-only rubric."""
from .capture_contract import canonical

RUBRIC_VERSION = 'atomic-source-evidence-v1'
BASE = '''You extract inspectable evidence from one human conversation, not a polished narrative.
Use only SOURCE below. SOURCE contains exact redacted turn text split into contiguous span parts.
Every part has an immutable span ID. Cite those IDs; do not copy, fix or invent quotes.
Actor and recipient must be stable speaker IDs from SOURCE, or "unknown". Seller statements
remain seller statements; a question, tentative offer, request or acknowledgement is not
acceptance, completion or a customer commitment. Preserve conditions, alternatives, amounts,
timing, scope, polarity, uncertainty and who would act for whom. Capture one atomic assertion
per claim. Keep materially different assertions separate. Include observed communication only
as concrete source-backed acts in their interaction. Do not infer personality, enduring traits,
demographics, motivation, emotion or future guidance. Do not add outside knowledge.
For kind=communication the observed actor must be one of SOURCE.customer_side_speaker_ids.
Those IDs explicitly designate the focal customer. Do not infer customer identity from role names
such as Partner. Other speakers' relevant speech acts may remain attributed issue evidence.
Use full conversation context to interpret claims, but create claims only for PRIMARY_EVENTS.
Every primary event needs exactly one coverage row, even a backchannel or context-only event.
Context overlap is the same source, never another case. No reference answer is provided.
Return one strict JSON object, no prose or Markdown. Empty lists are valid abstention.
CLAIM schema (all keys required):
{"primary_event_id":"event ID","kind":"issue|communication","actor":"speaker ID|unknown",
"recipient":"speaker ID|unknown","action":"bounded factual act","object":"what the act concerns",
"conditions":["material source condition"],"time":null,"certainty":"asserted|tentative|question|unknown",
"polarity":"positive|negative|unknown","status":"observed|proposed|requested|agreed|declined|reported_completed|unknown",
"functions":["report|offer|question|hypothetical|request|acknowledgement|acceptance|decline|observed_communication|unknown"],
"evidence_span_ids":["source span ID"]}.
Do not put the pipe-separated alternatives literally into an enum field. action/object<=600
characters each; <=8 conditions of <=400 characters each; time null or <=300 characters.
Coverage schema: {"event_id":"ID","disposition":"claim|context_only|no_material","reason":"<=400 chars"}.
functions contains 1..4 distinct source speech-act labels. Separate a speaker's report, offer,
question, hypothetical, request, acknowledgement and acceptance. Every status is source-relative:
reported_completed records what a speaker said was completed, never independently verified world
truth. An acknowledgement does not establish acceptance. At most 40 claims. Evidence must include
the claimed primary event. Span ID references must be exact.
'''
WORKER = '''Propose small source-backed atomic claims and nothing beyond them.
Response: {"claims":[CLAIM],"coverage":[COVERAGE]}.
'''
REVIEW = '''You are the source-checking reviewer. Follow this order inside this request:
FIRST read SOURCE and PRIMARY_EVENTS and independently scan the material assertions and
observed communication; record compact event/span-linked meanings in source_pass. Search for omissions, exceptions, contrary
spans, corrections, actor/recipient reversals, conditions and timeline changes across the full call.
ONLY THEN inspect CANDIDATES and judge each claim against source. This is method separation
within one request; you use the same model as the worker, so this is not model independence.
Accept only an entirely supported atomic claim with reasoned source evidence. Reject unsupported
claims with a reason and relevant counterspans when present. Mark ambiguity unresolved. A valid
neighbour does not justify another claim. Do not accept inferred traits, biographies or guidance.
source_pass is {"read_event_ids":["every PRIMARY_EVENT ID in original order"],
"material_findings":[{"evidence_span_ids":["ID"],"meaning":"<=160 chars"}]}
and appears FIRST in the response. read_event_ids must equal the complete PRIMARY_EVENTS list,
with no missing or duplicated ID. Record only material findings: at most twelve short entries,
each anchored to 1..4 exact source span IDs. Combine related material meanings compactly; do not
write a prose row for every backchannel or duplicate full worker claims. This compact scan is
not an exhaustive list of accepted claims. Keep per-candidate judgments and actual omissions
separate below; every candidate still requires its own reasoned decision and exact source spans.
For EVERY candidate return exactly one decision: {"claim_id":"ID","verdict":"accept|reject|unresolved",
"reason":"<=800 chars, explain support or failure","evidence_span_ids":["ID"],"counter_span_ids":["ID"]}.
Only for actual omissions return {"claim":CLAIM,"reason":"<=600 chars"}. These findings are
proposals, never directly accepted evidence; they require worker/reconciliation plus separate verification.
Response: {"source_pass":...,"decisions":[...],"omissions":[...]}.
'''
REPAIR = '''Make the section's only targeted repair. Use TARGETS, reviewer reasons, omissions and
source, preserving every already accepted claim in ACCEPTED unchanged. Return only new or
changed claims, never accepted claims again. Every changed claim names its prior claim ID in
supersedes; a genuinely new omission has supersedes null. An uncertain item may remain unresolved.
Response: {"changes":[{"supersedes":null,"claim":CLAIM}],"coverage":[COVERAGE],
"unresolved":[{"reason":"<=600 chars","evidence_span_ids":["ID"]}]}.
There is no further repair. A separate source-checking request must verify each returned change.
'''
RECONCILE = '''Read the entire conversation and the accepted/disputed item view. Reconcile
cross-section omissions, repeated assertions, contradiction, attribution, timelines, and conditions.
Keep supported accepted items; propose only necessary additions or amendments. Do not rewrite
the whole store or create summaries. A new/changed claim cannot become accepted in this request.
Every amendment names its old claim ID in supersedes; a new assertion uses null. Preserve the
identity of repeated claims unless meaning materially changes. Record unresolved contradictions.
Response: {"changes":[{"supersedes":null,"claim":CLAIM}],"coverage":[COVERAGE],
"unresolved":[{"reason":"<=600 chars","evidence_span_ids":["ID"]}]}.
All proposed additions/changes will receive a separate source-first verification call.
'''


def _original_messages(stage, source_context, primary, payload):
    instruction = {'worker': WORKER, 'review': REVIEW, 'repair': REPAIR,
                   'repair_verify': REVIEW, 'reconcile': RECONCILE,
                   'reconcile_verify': REVIEW}[stage]
    # Source precedes candidates in both message order and requested response order.
    return [{'role': 'system', 'content': BASE + instruction},
            {'role': 'user', 'content': 'SOURCE\n' + canonical(source_context)
             + '\nPRIMARY_EVENTS\n' + canonical(primary) + '\n' + canonical(payload)}]

"""One completion protocol, with an explicit kind contract and compact reviews."""
from copy import deepcopy
RUBRIC_VERSION = 'atomic-source-evidence-completion-v1'


def wire_payload(payload):
    """Remove only byte-for-byte duplicate historical bodies, never claim meaning."""
    value = deepcopy(payload)
    for key in ('CANDIDATES', 'TARGETS'):
        for item in value.get(key, []):
            if isinstance(item, dict) and isinstance(item.get('claim'), dict):
                body = {k: v for k, v in item['claim'].items() if k != 'claim_id'}
                if item.get('original_body') == body:
                    item.pop('original_body', None)
    return value


def messages(stage, context, primary, payload):
    rows = _original_messages(stage, context, primary, wire_payload(payload))
    rows[0]['content'] += '''
Completion clarification: kind=issue records material facts, reports, offers, questions,
requests, conditions and commitments from ANY speaker. kind=communication records only
observed CUSTOMER interaction behaviour, with an actor in customer_side_speaker_ids.
Being spoken is not enough to make a material issue a communication observation.
Invalid retained records have invalid_record=true and remain disputed. Never accept them
as they stand or silently relabel their kind. You may explicitly request a semantic repair;
the worker must return a new claim version and a separate reviewer must check its source.
Keep source-relative status. No invented future advice; all accepted material carries
requires_client_guidance metadata outside this extraction, rather than invented guidance.
ACCEPTED_IMMUTABLE gives claim IDs and content fingerprints held unchanged in the store.
Do not return or supersede any of these IDs. Repair only the explicit TARGETS or OMISSIONS.
For whole-call reconciliation, ACTIVE_CLAIM_COLUMNS names fields in order and each row in
ACCEPTED_ROWS supplies ALL field values in that order. This is lossless claim data.
DISPUTED_ROWS uses DISPUTED_COLUMNS in the same way. RESOLVED_DISPUTES names historical
versions already replaced by separately verified accepted claims; their full history is retained.
'''
    if stage in {'review', 'repair_verify', 'reconcile_verify'}:
        rows[0]['content'] += '''
For compact decisions use reason<=240 characters, <=4 evidence_span_ids and <=4
counter_span_ids. Add request_repair:boolean to EVERY decision. Set it true only when
an explicit targeted semantic repair should be attempted; explain the correction in reason.
Invalid retained records can only be reject/unresolved, never accept. Review EVERY valid
or disputed candidate independently. Source_pass still appears FIRST. No prose repetition.
Only actual missing material belongs in omissions; use reason<=240 characters.
'''
    return rows

ORIGINAL_PROMPT_SHA256 = '4b52f634696aef52b3a73e2e79ec6970cf39aa44cd63314128b46f814fac52b9'
COMPLETION_PROMPT_SHA256 = '3c0c1a9f579627d4047c63b304850d0b6b937c5d3b74342a951464b96f2a8ef5'
