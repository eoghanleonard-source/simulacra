"""Check retained engine smoke artefacts offline; semantic seed review stays explicit."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from snowflake_engine.export import validate_export

def read(path):return json.loads(Path(path).read_text())
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for key in ('run','rerun','export','rerun-export','expected','output'):p.add_argument('--'+key,required=True)
 a=p.parse_args();r,rr,d,dd,e=map(read,(a.run,a.rerun,a.export,a.rerun_export,a.expected));checks={}
 checks['export_contract']=not validate_export(d)
 checks['rerun_export_contract']=not validate_export(dd)
 checks['run_export_identity']=d.get('manifest',{}).get('run_id')==r.get('run_id') and dd.get('manifest',{}).get('run_id')==rr.get('run_id')
 checks['run_release_identity']=d.get('release',{}).get('release_id')==r.get('release_id') and dd.get('release',{}).get('release_id')==rr.get('release_id')
 checks['run_export_source_hashes']=d.get('manifest',{}).get('source_hashes')==r.get('source_hashes') and dd.get('manifest',{}).get('source_hashes')==rr.get('source_hashes')
 checks['input_accounting']=all(r.get('accounting',{}).get(k)==v for k,v in e['expected_accounting'].items())
 checks['rerun_accounting']=r.get('accounting')==rr.get('accounting')
 checks['unchanged_source_hashes']=r.get('source_hashes')==rr.get('source_hashes')
 u,uu=r.get('usage',{}),rr.get('usage',{})
 checks['generation_allowance']=u.get('completions',999)<=12 and uu.get('completions',999)<=12
 checks['embedding_allowance']=u.get('embedding_inputs',999)<=128 and uu.get('embedding_inputs',999)<=128
 old_ids=[x.get('attempt_id') for x in u.get('attempts',[])];new_ids=[x.get('attempt_id') for x in uu.get('attempts',[])]
 checks['attempt_identity_recorded']=bool(old_ids) and all(old_ids) and len(old_ids)==len(set(old_ids)) and all(new_ids)
 checks['rerun_no_new_attempts']=set(old_ids)==set(new_ids)
 checks['rerun_no_new_generation']=u.get('completions')==uu.get('completions')
 checks['rerun_no_new_embeddings']=u.get('embedding_inputs')==uu.get('embedding_inputs')
 def ids(doc):return sorted((x['kind'],x['object_id']) for key in ('scenarios','personas','quarantined_components') for x in doc.get(key,[]))
 checks['stable_logical_objects']=ids(d)==ids(dd)
 checks['no_duplicate_objects']=bool(ids(d)) and len(ids(d))==len(set(ids(d))) and len(ids(dd))==len(set(ids(dd)))
 checks['draft_boundary']=d.get('release',{}).get('client_ready') is False and dd.get('release',{}).get('client_ready') is False
 result={'status':'mechanical_checks_passed' if all(checks.values()) else 'mechanical_checks_failed','checks':checks,'semantic_seed_review':'required_separately','semantic_acceptance':False,'client_ready':False,'scope':'Shipped native path and bounded synthetic inputs only; citation checks do not prove meaning or completeness.','artifacts':{k:getattr(a,k) for k in ('run','rerun','export','rerun_export','expected')}}
 Path(a.output).write_text(json.dumps(result,indent=2)+'\n');print(result['status']);return 0 if all(checks.values()) else 2
if __name__=='__main__':raise SystemExit(main())
