"""Focused opt-in binding repair and existing catalogue/export integration."""
from copy import deepcopy
import hashlib
import json

import pytest

from snowflake_engine import contracts, evidence_assertions as evidence, extraction, mapping, native
from test_extraction import source
from test_native import storage, Session, config
from test_native_recorded_run import RecordedSession, request


def response(data):
    def bound(value, ids=None):
        return {'value':value,'evidence_ids':ids or ['ev0000']}
    row={'id':'s1','evidence_ids':['ev0000','ev0001'],
         'kind':bound('primary_need'),'actor':bound('buyer'),'recipient':bound('staff'),
         'action':bound('Buyer requests replacement of the damaged lens.'),
         'state':bound('requested'),'certainty':bound('explicit'),
         'conditions':[bound('Blue lens, not red.',['ev0002'])],
         'corrections':[],'unknowns':[]}
    return {'assertions':[row],'capture_status':'complete',
            'read_receipt':{'complete':True,'last_event_id':data['events'][-1]['event_id']}}


def test_missing_overall_reference_and_supplementary_context_preserve_fields():
    data=source();raw=response(data);before=deepcopy(raw)
    result=evidence.parse_response(data,json.dumps(raw));assert len(result['valid'])==1
    component=result['valid'][0];proof=component['binding_projection']
    assert proof['field_union_ids']==['ev0000','ev0002']
    assert proof['supplemental_context_ids']==['ev0001']
    assert proof['effective_ids']==['ev0000','ev0001','ev0002']
    assert result['raw_assertions']==before['assertions']
    assert component['raw_assertion']==before['assertions'][0]
    assert [r['event_id']for r in component['payload']['needs'][0]['evidence']]==['e0','e1','e2']
    assert component['render_lineage'][6]['evidence_ids']==['ev0002']
    assert raw==before and result['semantic_release']is False


@pytest.mark.parametrize('refs',[[],['invented'],['ev0000','ev0000'],['ev0002','ev0000']])
def test_invalid_field_references_remain_quarantined(refs):
    data=source();raw=response(data);raw['assertions'][0]['action']['evidence_ids']=refs
    assert not evidence.parse_response(data,json.dumps(raw))['valid']


def test_exact_saved_response_import_refuses_changed_source_request_and_raw():
    data=source();messages=extraction.build_messages_v3(data,'persona',110000);raw='recorded raw response'
    entry={'source_id':data['source_id'],'source_hash':contracts.source_hash(data),
           'operation':'extraction_v3_persona','model':'claude-sonnet-4-6','temperature':0,'max_output_tokens':6144,
           'request_messages_sha256':hashlib.sha256(contracts.canonical_json(messages).encode()).hexdigest(),
           'raw_response':raw,'raw_response_sha256':hashlib.sha256(raw.encode()).hexdigest(),
           'provenance':{'attempt_id':'original-example'}}
    assert evidence.retained_response(entry,data,messages,'extraction_v3_persona','claude-sonnet-4-6')==raw
    for key,value in [('source_hash','changed'),('request_messages_sha256','changed'),('raw_response','changed')]:
        changed=dict(entry,**{key:value})
        with pytest.raises(ValueError):evidence.retained_response(changed,data,messages,'extraction_v3_persona','claude-sonnet-4-6')


def test_native_exact_import_needs_no_provider_or_paid_reservation():
    data=source();messages=extraction.build_messages_v3(data,'persona',110000);raw='original saved response'
    entry={'source_id':data['source_id'],'source_hash':contracts.source_hash(data),'operation':'extraction_v3_persona',
        'model':'claude-sonnet-4-6','temperature':0,'max_output_tokens':6144,
        'request_messages_sha256':hashlib.sha256(contracts.canonical_json(messages).encode()).hexdigest(),
        'raw_response':raw,'raw_response_sha256':hashlib.sha256(raw.encode()).hexdigest(),
        'provenance':{'attempt_id':'original-example'}}
    session=Session();session.budget.update(max_output_tokens=6144,max_input_chars=110000)
    cfg=config(model='claude-sonnet-4-6',extraction_mode='evidence_v1',enable_model_calls=False,
               evidence_response_cache=[entry],limits=dict(session.budget))
    runtime=native.Runtime(session,cfg);runtime.acquire();runtime.evidence_source=data
    try:
        assert runtime.complete(messages,'extraction_v3_persona','exact-import')==raw
        assert runtime.cache_hits==1 and session.provider_calls==0
        assert session.tables['ATTEMPTS']==[]
    finally:runtime.release()


def test_opt_in_native_recorded_pipeline_reaches_catalogue_export_without_provider():
    cfg=request();cfg.update(extraction_mode='evidence_v1',model='claude-sonnet-4-6')
    sources=mapping.normalize(cfg['raw_records'],cfg['mapping'],cfg['client_id'])['sources']
    for entry in cfg['recorded_run_responses']['sources']:
        data=next(s for s in sources if s['source_id']==entry['source_id'])
        persona=entry['operations']['extraction_v3_persona']
        entry['operations']={'evidence_v1_scenario':[json.dumps(response(data))], 'extraction_v3_persona':persona}
    cfg=native.validate_config(cfg);session=RecordedSession()
    result=native.run(session,cfg)
    assert result['status']=='complete',result
    assert result['usage']['fixture_attempts_this_run']==4
    assert session.provider_calls==0
    assert len(session.tables['COMPONENTS'])==len(session.tables['FAMILIES'])==4
    assert len(session.tables['RELEASES'])==1
    lineage=session.tables['RELEASES'][0]['MANIFEST']['usage']['retained_reprocessing']
    bindings=[r for r in lineage if r['event']=='evidence_field_binding_projection']
    assert len(bindings)==2
    assert all(r['binding_projection']['supplemental_context_ids']==['ev0001']for r in bindings)
    assert all(r['model_assertion']['conditions'][0]['evidence_ids']==['ev0002']for r in bindings)
    assert {r['PAYLOAD']['version_set']['model_settings']['extraction_mode']for r in session.tables['COMPONENTS']}=={'evidence_v1'}
    assert native.validate_config({'database':'DEMO_DB','schema':'ENGINE_TEST','client_id':'synthetic',
                                  'model':'configured-model','embedding_model':'snowflake-arctic-embed-l-v2.0'})['extraction_mode']=='legacy'


def test_invalid_evidence_preserves_quarantine_and_other_layer_in_native_export():
    cfg=request();cfg.update(extraction_mode='evidence_v1',model='claude-sonnet-4-6')
    sources=mapping.normalize(cfg['raw_records'],cfg['mapping'],cfg['client_id'])['sources']
    for index,entry in enumerate(cfg['recorded_run_responses']['sources']):
        data=next(s for s in sources if s['source_id']==entry['source_id']);scenario=response(data)
        if index==0:scenario['assertions'][0]['action']['evidence_ids']=['unknown']
        entry['operations']={'evidence_v1_scenario':[json.dumps(scenario)],
                             'extraction_v3_persona':entry['operations']['extraction_v3_persona']}
    session=RecordedSession();result=native.run(session,native.validate_config(cfg))
    assert result['status']=='partial',result
    assert len(session.tables['COMPONENTS'])==4
    assert sum(r['VALIDATION_STATUS']=='quarantined'for r in session.tables['COMPONENTS'])==1
    assert len(session.tables['RELEASES'])==1
    assert session.provider_calls==0
