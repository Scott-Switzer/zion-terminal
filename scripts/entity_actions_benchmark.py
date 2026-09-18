from __future__ import annotations
import http.client, json, os, statistics, time
from urllib.parse import urlsplit

CASES={
 "Q1": {"path":"/v1/tools/get_fundamentals","body":{"entity":"MSFT","metrics":["revenue"],"period":"annual","lookback":1}},
 "Q2": {"path":"/v1/tools/get_fundamentals","body":{"entity":"MSFT","metrics":["revenue","operating_income"],"period":"annual","lookback":1}},
 "Q3": {"path":"/v1/tools/get_fundamentals","body":{"entity":"MSFT","metrics":["revenue"],"period":"quarterly","lookback":8}},
 "Q4": {"path":"/v1/tools/get_price_history","body":{"entity":"MSFT","limit":50}},
 "Q5": {"path":"/v1/tools/compare","body":{"entities":["MSFT","NVDA","AMD"],"metric":"operating_margin","period":"quarterly","lookback":4}},
 "RESOLVE": {"path":"/v1/tools/resolve_security","body":{"entity":"BRK-B","as_of":"2026-01-01"}},
}

def run(case,n=100):
 target=urlsplit(os.environ.get('BASE_URL','https://zion-financial-serving-v2-entity-actions-staging.scswitzer.workers.dev')); spec=CASES[case]; payload=json.dumps({**spec['body'],'world':{'world_type':'real','world_id':'us-public-markets'}}).encode(); c=http.client.HTTPSConnection(target.netloc,timeout=30); vals=[]; statuses=[]; releases=[]
 for i in range(n):
  started=time.perf_counter(); c.request('POST',spec['path'],payload,{'content-type':'application/json','connection':'keep-alive','user-agent':'entity-actions-benchmark/1.0','x-request-id':f'entity-actions-{case}-{i}'}); r=c.getresponse(); raw=r.read(); vals.append((time.perf_counter()-started)*1000); statuses.append(r.status); d=json.loads(raw); releases.append((d.get('release') or {}).get('serving_release_id') or (d.get('data') or {}).get('serving_release_id'))
 c.close(); ordered=sorted(vals); pct=lambda f:ordered[min(len(ordered)-1,int((len(ordered)-1)*f))]; return {'case':case,'samples':n,'p50_ms':round(statistics.median(vals),2),'p95_ms':round(pct(.95),2),'p99_ms':round(pct(.99),2),'max_ms':round(max(vals),2),'statuses':sorted(set(statuses)),'release_ids':sorted(set(x for x in releases if x))}

if __name__=='__main__':
 n=int(os.environ.get('SAMPLES','100')); cases=[os.environ['CASE']] if os.environ.get('CASE') else ['Q1','Q2','Q3','Q4','Q5','RESOLVE']; print(json.dumps([run(x,n) for x in cases],sort_keys=True))
