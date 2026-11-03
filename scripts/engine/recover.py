"""Explicit operator recovery for one proven-ended engine CALL; never resets model attempts."""
from pathlib import Path
import argparse,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from snowflake_engine.cli import connect,read_json,write_json
from snowflake_engine.contracts import canonical_json
from snowflake_engine.native import operator_recovery_plan
from snowflake_engine.schema import qualified_name

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for key in ('config','failed-run-id','failed-call-query-id','inactivity-evidence','output'):p.add_argument('--'+key,required=True)
 p.add_argument('--env-file');p.add_argument('--execute',action='store_true');a=p.parse_args();c=read_json(a.config);proof=read_json(a.inactivity_evidence)
 matching=[r for r in proof.get('anchors',[]) if r.get('QUERY_ID')==a.failed_call_query_id and r.get('QUERY_TYPE')=='CALL']
 if len(matching)!=1 or not matching[0].get('EXECUTION_STATUS','').startswith('FAILED'):
  raise ValueError('Evidence must identify the exact failed, ended CALL')
 if any(r.get('QUERY_TYPE')=='CALL' and r.get('EXECUTION_STATUS') in ('RUNNING','QUEUED','RESUMING_WAREHOUSE','BLOCKED') for r in proof.get('anchors',[])):
  raise ValueError('Evidence contains an active CALL; recovery refused')
 plan=operator_recovery_plan(c,a.failed_run_id,a.failed_call_query_id,a.inactivity_evidence)
 if a.execute:
  s=connect(c,a.env_file);begun=False
  def ledger():
   rows=[r.as_dict() for r in s.sql('SELECT * FROM '+qualified_name(c,'ATTEMPTS')+' ORDER BY ATTEMPT_ID').collect()]
   return hashlib.sha256(canonical_json(rows).encode()).hexdigest(),len(rows)
  try:
   s.sql('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=120').collect()
   before,count=ledger()
   for step in plan['steps']:
    rows=s.sql(step['sql'],params=step['params']).collect()
    if step['sql']=='BEGIN TRANSACTION':begun=True
    if 'expect_rows' in step and [list(r) for r in rows]!=step['expect_rows']:raise ValueError('Recovery row precondition mismatch')
    if 'expect_affected_rows' in step and (len(rows)!=1 or rows[0][0]!=step['expect_affected_rows']):raise ValueError('Recovery affected-row mismatch')
    if step['sql']=='COMMIT':begun=False
   after,after_count=ledger()
   if before!=after or count!=after_count:raise ValueError('Attempt ledger changed; inspect without retrying')
   plan.update(executed=True,query_ids=s.query_ids,attempt_ledger_sha256_before=before,attempt_ledger_sha256_after=after,attempts_before=count,attempts_after=after_count)
  except Exception:
   if begun:s.sql('ROLLBACK').collect()
   raise
  finally:s.close()
 write_json(a.output,plan);return 0
if __name__=='__main__':raise SystemExit(main())
