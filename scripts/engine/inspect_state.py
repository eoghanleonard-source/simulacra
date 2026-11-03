"""Bounded read-only engine state and native query-ID capture; never invokes inference."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from snowflake_engine.cli import connect,read_json,write_json,validate_config
from snowflake_engine.schema import qualified_name,validate_identifier

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for key in ('config','output','run-id'):p.add_argument('--'+key,required=True)
 p.add_argument('--env-file');p.add_argument('--execute',action='store_true');a=p.parse_args();c=validate_config(read_json(a.config));db=validate_identifier(c['database'])
 names=['INPUT_RAW','INPUT_SOURCES','INPUT_ISSUES','RUNS','ATTEMPTS','COMPONENTS','EMBEDDINGS','FAMILIES','RELATIONS','RELEASES']
 query=' UNION ALL '.join("SELECT '"+name+"' AS OBJECT_NAME,COUNT(*) AS ROW_COUNT FROM "+qualified_name(c,name) for name in names)
 history=f"SELECT QUERY_ID, QUERY_TYPE, EXECUTION_STATUS, TOTAL_ELAPSED_TIME FROM TABLE({db}.INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT=>1000)) WHERE TRY_PARSE_JSON(QUERY_TAG):run_id::VARCHAR=? ORDER BY START_TIME LIMIT 500"
 lock='SELECT LOCK_KEY, OWNER_RUN_ID, ACQUIRED_AT FROM '+qualified_name(c,'LOCKS')
 r={'executed':False,'inference_calls':0,'queries':[{'sql':query,'params':[]},{'sql':history,'params':[a.run_id]},{'sql':lock,'params':[]}]}
 if a.execute:
  s=connect(c,a.env_file)
  try:
   s.sql('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=30').collect()
   for q in r['queries']:q['rows']=[x.as_dict() for x in s.sql(q['sql'],params=q['params']).collect()]
   r.update(executed=True,query_ids=s.query_ids,statement_count=len(s.query_ids))
  finally:s.close()
 write_json(a.output,r);return 0
if __name__=='__main__':raise SystemExit(main())
