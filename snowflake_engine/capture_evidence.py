"""Pure accepted WRv9 capture policy over complete normalized canonical sources.

The caller owns transport, budgets, durable callbacks and replay. This module has
no provider, filesystem or experiment-history dependency and never retries.
"""
from copy import deepcopy
import hashlib

from . import capture_contract as contract
from . import capture_framing
from . import capture_prompts as prompts
from .contracts import validate_source, source_hash as canonical_source_hash

PROFILE_VERSION = 'worker-reviewer-v9-dev-20261101'
MODEL = 'claude-sonnet-4-6'
CAPTURE_CONTRACT = 'capture-evidence-v1'
MAXIMUM_WIRE_BYTES = 90000
# The frozen v5 source hash identifies normalization code; no local file is loaded.
NORMALIZER_SOURCE_SHA256 = '37e35c47b75e9779279b09762f084d0276d33f4108c5d750af6768c875153178'
PROVENANCE = {'original_claim_sha256': contract.ORIGINAL_CLAIM_SHA256,
              'original_prompt_sha256': prompts.ORIGINAL_PROMPT_SHA256,
              'completion_prompt_sha256': prompts.COMPLETION_PROMPT_SHA256,
              'normalizer_source_sha256': NORMALIZER_SOURCE_SHA256,
              'accepted_controller_source_sha256': 'c7fbc425210883c4e59c0599a2ea5b185105b640380ee9d6983238160c83e4f8'}


class CaptureError(ValueError):
    """Terminal capture failure; prior callback records remain valid history."""


def current_projection(records):
    """Project accepted identities without erasing disputes or supersession history."""
    accepted = {row['claim']['claim_id']: row for row in records if row['record_kind'] == 'accepted'}
    superseded = {row['supersedes'] for row in accepted.values()
                  if row.get('supersedes') is not None and row['supersedes'] != row['claim']['claim_id']}
    return {'accepted': [accepted[cid] for cid in sorted(accepted) if cid not in superseded],
            'disputed': [r for r in records if r['record_kind'] == 'disputed'],
            'omissions': [r for r in records if r['record_kind'] == 'omission'],
            'superseded_claim_ids': sorted(superseded)}


def section_plan(source, explicit=None):
    """Contiguous ordered coverage, bounded at 25 primary events per section."""
    ids = [event['event_id'] for event in source['events']]
    if explicit is None:
        return [{'section_id': source['source_id'] + '-section-' + str(index // 25 + 1).zfill(4),
                 'primary_event_ids': ids[index:index + 25]} for index in range(0, len(ids), 25)]
    if not isinstance(explicit, list) or not explicit:
        raise CaptureError('Explicit section plan must be a nonempty ordered list')
    seen, section_ids = [], set()
    for row in explicit:
        if (not isinstance(row, dict) or set(row) != {'section_id', 'primary_event_ids'}
                or not isinstance(row['section_id'], str) or not row['section_id'].strip()
                or row['section_id'] == source['source_id'] or row['section_id'] in section_ids
                or not isinstance(row['primary_event_ids'], list)
                or not 1 <= len(row['primary_event_ids']) <= 25):
            raise CaptureError('Invalid bounded section plan row')
        section_ids.add(row['section_id'])
        seen.extend(row['primary_event_ids'])
    if seen != ids:
        raise CaptureError('Section plan must cover every event exactly once in source order')
    return deepcopy(explicit)


def _invalid_candidate(body, reason, index):
    raw = deepcopy(body)
    cid = 'invalid-' + contract.digest({'body': raw, 'reason': reason, 'index': index})[:24]
    return {'claim': dict(raw, claim_id=cid) if isinstance(raw, dict) else {'claim_id': cid},
            'supersedes': None, 'invalid_record': True, 'original_body': raw,
            'validation_reason': reason, 'original_index': index}


class _Capture:
    def __init__(self, source, complete, settings, versions, on_record, on_checkpoint):
        full = validate_source(source)
        if not isinstance(settings, dict) or not isinstance(versions, dict):
            raise CaptureError('Capture settings and versions must be objects')
        self.response_framing = settings.get('response_framing')
        if self.response_framing not in (None, capture_framing.FRAMING_VERSION):
            raise CaptureError('Unknown capture response framing version')
        self.normalize_message = (capture_framing.normalize_message if self.response_framing
                                  else contract.normalize_message)
        if settings.get('profile_version', PROFILE_VERSION) != PROFILE_VERSION:
            raise CaptureError('Capture requires the accepted development profile')
        if settings.get('model', MODEL) != MODEL or settings.get('temperature', 0) != 0:
            raise CaptureError('Capture model and temperature are pinned by the accepted profile')
        focal = settings.get('customer_side_speaker_ids', full['metadata'].get('customer_side_speaker_ids'))
        speakers = {e['speaker'] for e in full['events']}
        if (not isinstance(focal, list) or not focal or any(not isinstance(x, str) or x not in speakers for x in focal)
                or len(set(focal)) != len(focal)):
            raise CaptureError('Explicit nonempty customer-side speaker IDs must refer to source speakers')
        self.source = contract.source_projection(dict(full, customer_side_speaker_ids=focal))
        self.source_hash = settings.get('source_hash', canonical_source_hash(full))
        if (not isinstance(self.source_hash, str) or len(self.source_hash) != 64
                or any(c not in '0123456789abcdef' for c in self.source_hash)
                or self.source_hash != canonical_source_hash(full)):
            raise CaptureError('Supplied source hash differs from the complete canonical source')
        self.plan = section_plan(self.source, settings.get('section_plan'))
        self.context, self.spans = contract.catalogue(self.source)
        contract.validate_catalogue(self.source, self.spans)
        if not callable(complete) or (on_record is not None and not callable(on_record)) or (on_checkpoint is not None and not callable(on_checkpoint)):
            raise CaptureError('Capture callbacks must be callable')
        contract.canonical(versions)
        self.versions, self.complete = deepcopy(versions), complete
        self.on_record, self.on_checkpoint = on_record, on_checkpoint
        self.records, self.checkpoints, self.request_keys = [], [], []
        self.stage, self.sid = 'source', self.source['source_id']

    def append(self, stage, sid, kind, **data):
        body = {'schema': CAPTURE_CONTRACT, 'source_id': self.source['source_id'],
                'source_hash': self.source_hash, 'profile_version': PROFILE_VERSION,
                'customer_side_speaker_ids': list(self.source['customer_side_speaker_ids']),
                'versions': deepcopy(self.versions), 'provenance': deepcopy(PROVENANCE),
                'model': MODEL, 'rubric_version': prompts.RUBRIC_VERSION,
                'stage': stage, 'section_id': sid, 'record_kind': kind, **deepcopy(data)}
        row = dict(body, record_id='item-' + contract.digest(body))
        if row['record_id'] not in {r['record_id'] for r in self.records}:
            self.records.append(row)
            if self.on_record:
                self.on_record(deepcopy(row))
        return row

    def checkpoint(self, stage, sid, status):
        view = current_projection(self.records)
        body = {'source_id': self.source['source_id'], 'section_id': sid, 'stage': stage,
                'status': status, 'sequence': len(self.checkpoints) + 1,
                'source_hash': self.source_hash, 'profile_version': PROFILE_VERSION,
                'customer_side_speaker_ids': list(self.source['customer_side_speaker_ids']),
                'versions': deepcopy(self.versions), 'request_keys': list(self.request_keys),
                'current_projection': {'accepted_record_ids': [r['record_id'] for r in view['accepted']],
                    'disputed_record_ids': [r['record_id'] for r in view['disputed']],
                    'omission_record_ids': [r['record_id'] for r in view['omissions']],
                    'superseded_claim_ids': view['superseded_claim_ids']}}
        row = dict(body, checkpoint_id='checkpoint-' + contract.digest(body))
        self.checkpoints.append(row)
        if self.on_checkpoint:
            self.on_checkpoint(deepcopy(row))

    def call(self, stage, sid, primary, payload):
        self.stage, self.sid = stage, sid
        messages = prompts.messages(stage, self.context, primary, payload)
        size = len(contract.canonical({'messages': messages}).encode('utf-8'))
        if size > MAXIMUM_WIRE_BYTES:
            raise CaptureError('Complete wire input exceeds 90000 bytes; no source/claim clipping')
        cap = 8192 if stage == 'worker' else 16384
        identity = {'source_hash': self.source_hash, 'chunk_hash': contract.digest(primary),
                    'context_hash': contract.digest(self.context), 'prompt_hash': contract.digest(messages),
                    'model': MODEL, 'temperature': 0, 'rubric': prompts.RUBRIC_VERSION,
                    'output_cap': cap, 'stage': stage, 'section_id': sid,
                    'profile_version': PROFILE_VERSION, 'contract': CAPTURE_CONTRACT,
                    'versions': self.versions, 'provenance': PROVENANCE}
        key = contract.digest(identity)
        self.request_keys.append(key)
        self.checkpoint(stage, sid, 'requesting')
        raw = self.complete(deepcopy(messages), stage, key, max_output_tokens=cap)
        response = self.normalize_message(raw, stage)
        normalizations = list(response['normalizations'])
        if self.response_framing:
            normalizations.insert(0, 'response_framing:' + self.response_framing)
        self.append(stage, sid, 'stage_response', request_cache_key=key,
                    request_identity=identity, input_wire_bytes=size,
                    raw_response_sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                    normalizations=normalizations, body_repair=False,
                    new_inference_calls_for_normalization=0)
        self.checkpoint(stage, sid, 'response_validated')
        return response['payload'], key

    def judge(self, sid, primary, candidates, stage):
        payload, key = self.call(stage, sid, primary, {'CANDIDATES': candidates})
        decisions, omissions, errors = contract.parse_review(payload, candidates, self.source, self.spans, primary)
        if any(error.get('component') == 'source_pass' for error in errors):
            raise CaptureError('Unusable source-first reviewer control response: ' + str(errors))
        targets = []
        for item in candidates:
            decision = decisions[item['claim']['claim_id']]
            accepted = decision['verdict'] == 'accept' and not item.get('invalid_record')
            extra = {'requires_client_guidance': True, 'guidance_status': 'not_generated',
                     'evidence': [self.spans[s] for s in item['claim']['evidence_span_ids']],
                     'review_evidence': [self.spans[s] for s in decision['evidence_span_ids']]} if accepted else {}
            self.append(stage, sid, 'accepted' if accepted else 'disputed', claim=item['claim'],
                        supersedes=item['supersedes'], decision=decision,
                        invalid_record=item.get('invalid_record', False), original_body=item.get('original_body'),
                        request_cache_key=key, **extra)
            if not accepted:
                targets.append(dict(item, decision=decision))
        for omission in omissions:
            self.append(stage, sid, 'omission', **omission, request_cache_key=key,
                        disposition='proposal_requires_separate_verification')
        if errors:
            self.append(stage, sid, 'validation_failure', errors=errors, request_cache_key=key)
        self.checkpoint(stage, sid, 'judged')
        return targets, omissions, errors

    def section(self, section):
        sid, primary = section['section_id'], section['primary_event_ids']
        payload, key = self.call('worker', sid, primary, {})
        candidates, errors = contract.parse_claims(payload, self.source, self.spans, primary)
        if errors:
            self.append('worker', sid, 'validation_failure', errors=errors, request_cache_key=key)
        if not candidates:
            raise CaptureError('Worker has no independently valid claims; no downstream calls')
        candidates += [_invalid_candidate(payload['claims'][e['index']], e['reason'], e['index'])
                       for e in errors if e.get('index') is not None]
        targets, omissions, review_errors = self.judge(sid, primary, candidates, 'review')
        explicit = [t for t in targets if t['decision'].get('request_repair') is True]
        attempted = bool(explicit or omissions)
        if attempted:
            view = current_projection(self.records)
            payload = {'TARGETS': explicit, 'OMISSIONS': omissions,
                       'ACCEPTED_IMMUTABLE': [{'claim_id': r['claim']['claim_id'],
                                              'sha256': contract.digest(r['claim'])} for r in view['accepted']],
                       'VALIDATION_ERRORS': errors + review_errors}
            response, key = self.call('repair', sid, primary, payload)
            known = {t['claim']['claim_id'] for t in explicit}
            changed, errors = contract.parse_claims(response, self.source, self.spans, primary,
                                                   changes=True, known_ids=known)
            accepted_ids = {r['claim']['claim_id'] for r in view['accepted']}
            eligible = [c for c in changed if c['claim']['claim_id'] not in accepted_ids
                        and c['supersedes'] not in accepted_ids
                        and (c['supersedes'] in known or (c['supersedes'] is None and omissions))]
            if errors:
                self.append('repair', sid, 'validation_failure', errors=errors, request_cache_key=key)
            if eligible:
                self.judge(sid, primary, eligible, 'repair_verify')
            elif errors and not changed:
                raise CaptureError('Unusable semantic repair response')
        self.append('review', sid, 'section_terminal', repair_attempts=int(attempted), further_repair_allowed=False)
        self.checkpoint('review', sid, 'section_completed')

    def reconcile(self):
        sid = self.source['source_id']
        primary = [e['event_id'] for e in self.source['events']]
        view = current_projection(self.records)
        accepted = [r['claim'] for r in view['accepted']]
        disputed = {r['claim']['claim_id']: {'claim': r['claim'], 'decision': r['decision'],
                     'invalid_record': r.get('invalid_record', False)} for r in view['disputed']}
        resolved = {r['supersedes']: r['claim']['claim_id'] for r in view['accepted'] if r.get('supersedes')}
        columns = sorted(contract.CLAIM_FIELDS) + ['claim_id']
        active = {c['claim_id'] for c in accepted}
        pending = [v for cid, v in disputed.items() if cid not in resolved and cid not in active]
        omissions = {o['claim']['claim_id']: {'claim': o['claim'], 'reason': o['reason']} for o in view['omissions']
                     if o['claim']['claim_id'] not in active and o['claim']['claim_id'] not in resolved}
        payload = {'ACTIVE_CLAIM_COLUMNS': columns, 'ACCEPTED_ROWS': [[c[k] for k in columns] for c in accepted],
                   'DISPUTED_COLUMNS': ['claim', 'decision', 'invalid_record'],
                   'DISPUTED_ROWS': [[v['claim'], v['decision'], v['invalid_record']] for v in pending],
                   'RESOLVED_DISPUTES': [{'claim_id': cid, 'superseded_by': replacement} for cid, replacement in sorted(resolved.items())],
                   'OMISSIONS': list(omissions.values())}
        response, key = self.call('reconcile', sid, primary, payload)
        changed, errors = contract.parse_claims(response, self.source, self.spans, primary,
                                               changes=True, known_ids=active | set(disputed))
        eligible = [c for c in changed if c['claim']['claim_id'] not in active
                    or (c['supersedes'] is not None and c['supersedes'] != c['claim']['claim_id'])]
        if errors:
            self.append('reconcile', sid, 'validation_failure', errors=errors, request_cache_key=key)
        if errors and not changed:
            raise CaptureError('Unusable reconciliation response')
        if eligible:
            self.judge(sid, primary, eligible, 'reconcile_verify')
        self.append('reconcile', sid, 'reconciliation_terminal',
                    status='completed_with_unresolved' if errors or response['unresolved'] else 'completed',
                    explicitly_unresolved=response['unresolved'])
        self.checkpoint('reconcile', sid, 'completed')


def capture_source(source, complete, *, settings, versions, on_record=None, on_checkpoint=None):
    """Capture one source with strict stage controls and append-only callback records."""
    try:
        runtime = _Capture(source, complete, settings, versions, on_record, on_checkpoint)
    except (ValueError, TypeError, KeyError) as error:
        raise CaptureError(str(error)) from error
    try:
        for section in runtime.plan:
            runtime.section(section)
        runtime.reconcile()
    except Exception as error:
        runtime.append(runtime.stage, runtime.sid, 'capture_failure', error_type=type(error).__name__,
                       reason=str(error), automatic_retry=False)
        runtime.checkpoint(runtime.stage, runtime.sid, 'failed')
        if isinstance(error, CaptureError):
            raise
        raise CaptureError(str(error)) from error
    view = current_projection(runtime.records)
    unresolved = any(r.get('explicitly_unresolved') for r in runtime.records)
    return {'source_id': runtime.source['source_id'], 'records': deepcopy(runtime.records), **deepcopy(view),
            'checkpoints': deepcopy(runtime.checkpoints),
            'status': 'completed_with_unresolved' if view['disputed'] or view['omissions'] or unresolved else 'completed'}
