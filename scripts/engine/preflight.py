"""Read-only native prerequisites; no inference, object creation or credential output."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from snowflake_engine.cli import connect,validate_config,write_json
from snowflake_engine.schema import validate_identifier

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--config',required=True);p.add_argument('--output',required=True);p.add_argument('--env-file');p.add_argument('--execute',action='store_true')
 a=p.parse_args();cfg=validate_config(json.loads(Path(a.config).read_text()));db=validate_identifier(cfg['database'])
 queries=[('context','SELECT CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_WAREHOUSE(), CURRENT_ROLE(), CURRENT_REGION()',[]),('package',f"SELECT PACKAGE_NAME, VERSION FROM {db}.INFORMATION_SCHEMA.PACKAGES WHERE LANGUAGE='python' AND PACKAGE_NAME='snowflake-snowpark-python' AND VERSION='1.26.0'",[]),('schema',f'SELECT SCHEMA_NAME FROM {db}.INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=?',[cfg['schema']])]
 result={'executed':False,'inference_calls':0,'queries':[{'purpose':n,'sql':q,'params':v} for n,q,v in queries]}
 if a.execute:
  s=connect(cfg,a.env_file)
  try:
   s.sql('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=30').collect()
   for item,(name,query,params) in zip(result['queries'],queries):item['rows']=[list(r) for r in s.sql(query,params=params).collect()]
   result.update(executed=True,query_ids=s.query_ids,statement_count=len(s.query_ids))
   result['package_available']=any(row == ['snowflake-snowpark-python','1.26.0'] for row in result['queries'][1]['rows'])
   result['fresh_schema']=not result['queries'][2]['rows']
  finally:s.close()
 write_json(a.output,result)
 if a.execute and not result['package_available']:return 2
 return 0
if __name__=='__main__':raise SystemExit(main())
