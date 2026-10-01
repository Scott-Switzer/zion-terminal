import asyncio, hashlib, json, sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[2]/'deploy/cloudflare-query/src'))
from data_api import dispatch, select_screen_rows

class Object:
 def __init__(self,raw):self.raw=raw
 async def arrayBuffer(self):return self.raw
 async def text(self):return self.raw.decode()
class Bucket:
 def __init__(self,objects):self.objects=objects;self.reads=[]
 async def get(self,key):
  self.reads.append(key)
  return Object(self.objects[key]) if key in self.objects else None

def env():
 facts=[{'metric_id':'revenue','entity_id':'entity:meta','period_type':'quarterly','period_end':f'{year}-12-31','available_at':f'{year+1}-02-01','value_decimal':str(year),'observation_id':str(year),'evidence_id':str(year)} for year in range(2013,2026)]
 prices=[{'session_date':f'{year}-{month:02d}-{day:02d}','available_at':f'{year}-{month:02d}-{day:02d}T20:00:00Z','close':'123.123456789012345678901234','observation_id':f'{year}-{month}-{day}'} for year in [2013,2025] for month in range(1,13) for day in range(1,29)]
 values={'identity/resolver_index.json':{'symbols':{'META':{'entity':'entity:meta','instrument':'instrument:meta','listing':'listing:meta','artifact_path':'entities/meta'}}},'entities/meta/snapshot.json':{'symbol':'META','entity_id':'entity:meta'},'corporate-actions/actions.json':[{'action_id':str(year),'entity_id':'entity:meta','effective_date':str(year)+'-01-01','available_at':str(year)+'-01-01T00:00:00Z'} for year in [2013,2025]],'entities/meta/fundamentals/quarterly.json':facts,'entities/meta/prices/daily/2013.json':prices[:336],'entities/meta/prices/daily/2025.json':prices[336:]}
 objects={f'gold/serving/releases/test/{k}':json.dumps(v).encode() for k,v in values.items()}
 manifest={'schema_version':'financial-serving-v2','serving_release_id':'test','source':{'fundamentals':{'snapshot_id':1},'prices':{'snapshot_id':2}},'artifacts':[{'path':k,'sha256':hashlib.sha256(objects[f'gold/serving/releases/test/{k}']).hexdigest(),'bytes':len(objects[f'gold/serving/releases/test/{k}'])} for k in values]}
 raw=json.dumps(manifest).encode();objects['gold/serving/releases/test/manifest.json']=raw
 objects['gold/serving/CURRENT.json']=json.dumps({'serving_release_id':'test','manifest_key':'gold/serving/releases/test/manifest.json','manifest_sha256':hashlib.sha256(raw).hexdigest()}).encode()
 return SimpleNamespace(MARKET_DATA=Bucket(objects))

def test_rest_history_has_no_500_row_or_latest_release_year_cap():
 result=asyncio.run(dispatch(env(),'GET','/v1/securities/META/prices',{'start_date':'2013-01-01','end_date':'2026-12-31','limit':'1000'},None,'r'))
 assert len(result['data']['prices'])==672
 assert result['data']['prices'][0]['session_date']=='2013-01-01'
 assert result['data']['prices'][0]['close']=='123.123456789012345678901234'
 assert result['page']['total']==672

def test_rest_fundamentals_filters_range_and_pit_before_pagination():
 result=asyncio.run(dispatch(env(),'GET','/v1/securities/META/fundamentals',{'period':'quarterly','start_date':'2013-01-01','as_of':'2015-12-31T23:59:59Z'},None,'r'))
 assert [r['period_end'] for r in result['data']['observations']]==['2013-12-31','2014-12-31']
 assert result['data']['observations'][0]['value']=='2013'

def test_screen_evaluates_every_listing_and_sorts_decimal_not_text():
 rows=[{'symbol':str(i),'values':{'revenue':{'value':'9','unit':'USD'}}} for i in range(101)]+[{'symbol':'META','values':{'revenue':{'value':'100','unit':'USD'}}},{'symbol':'missing','values':{}}]
 result=select_screen_rows(rows,[],[{'field':'revenue','direction':'desc'}],2)
 assert result[0]['symbol']=='META'
 assert result[1]['symbol']!='missing'


def test_pagination_rejects_mixing_releases_and_invalid_range():
 from contract_v2 import ContractError
 with pytest.raises(ContractError,match='restart pagination'):
  asyncio.run(dispatch(env(),'GET','/v1/securities/META/prices',{'release_id':'old'},None,'r'))
 with pytest.raises(ContractError,match='YYYY-MM-DD'):
  asyncio.run(dispatch(env(),'GET','/v1/securities/META/prices',{'start_date':'2013-02-30'},None,'r'))

def test_bulk_uses_history_views_and_calculator_rejects_floats():
 from contract_v2 import ContractError
 result=asyncio.run(dispatch(env(),'POST','/v1/bulk/query',{}, {'queries':[{'path':'/v1/fundamentals/META','params':{'period':'quarterly'}},{'path':'/v1/prices/META'}]},'r'))
 assert all(r['release']['serving_release_id']=='test' for r in result['data']['results'])
 with pytest.raises(ContractError):
  asyncio.run(dispatch(env(),'POST','/v1/calculate',{}, {'operation':'sum','values':[0.1]},'r'))


def test_calculator_preserves_long_numerals_and_reports_division_precision():
 exact='123456789012345678901234567890.123456789'
 result=asyncio.run(dispatch(None,'POST','/v1/calculate',{}, {'operation':'sum','values':[exact,'0']},'r'))
 assert result['data']['value_decimal']==exact
 result=asyncio.run(dispatch(None,'POST','/v1/calculate',{}, {'operation':'ratio','values':['1','3']},'r'))
 assert result['data']['arithmetic']['precision']==64
 assert result['data']['value_decimal']=='0.'+'3'*64

def test_actions_remain_pinned_and_apply_date_range():
 source=env()
 result=asyncio.run(dispatch(source,'GET','/v1/securities/META/corporate-actions',{'start_date':'2025-01-01','end_date':'2025-12-31'},None,'r'))
 assert [r['action_id'] for r in result['data']['actions']]==['2025']
 assert source.MARKET_DATA.reads.count('gold/serving/CURRENT.json')==1

def test_unknown_prices_and_nonstring_bulk_params_fail_clearly():
 from contract_v2 import ContractError
 with pytest.raises(ContractError,match='not in this release'):
  asyncio.run(dispatch(env(),'GET','/v1/prices/UNKNOWN',{},None,'r'))
 with pytest.raises(ContractError,match='query parameters'):
  asyncio.run(dispatch(env(),'GET','/v1/fundamentals/META',{'metrics':[]},None,'r'))


def test_tool_price_history_does_not_stop_on_rows_unavailable_at_pit_cutoff():
 from serving_v2 import price_history
 result=asyncio.run(price_history(env(),'META',limit=100,as_of='2014-01-01T00:00:00Z'))
 assert len(result['prices'])==100
 assert all(r['session_date'].startswith('2013-') for r in result['prices'])


def test_immutable_artifact_reference_reuses_hash_verified_parent_object():
 from serving_v2 import artifact, ServingV2Error
 e=env();key='gold/serving/releases/test/entities/meta/prices/daily/2013.json';raw=e.MARKET_DATA.objects[key]
 manifest={'serving_release_id':'child','artifacts':[{'path':'history.json','storage_key':key,'sha256':hashlib.sha256(raw).hexdigest()}]}
 assert len(asyncio.run(artifact(e,manifest,'history.json')))==336
 manifest['artifacts'][0]['storage_key']='raw/private/secret.json'
 with pytest.raises(ServingV2Error):asyncio.run(artifact(e,manifest,'history.json'))


def test_metrics_and_source_archive_prices_are_paginated_and_pit_safe():
 e=env()
 values={'catalog/metrics.json':[{'metric_id':'revenue','issuer_count':439}],'archive/index.json':[{'symbol':'FB','rows':1,'pit_status':'SOURCE_LIMITED'}],'archive/FB/prices/2012.json':[{'session_date':'2012-05-18','available_at':None,'close':'38.22999954223633','source_symbol':'FB','evidence_id':'e'}]}
 key='gold/serving/releases/test/manifest.json';manifest=json.loads(e.MARKET_DATA.objects[key])
 for path,value in values.items():
  raw=json.dumps(value).encode();e.MARKET_DATA.objects['gold/serving/releases/test/'+path]=raw;manifest['artifacts'].append({'path':path,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
 raw=json.dumps(manifest).encode();e.MARKET_DATA.objects[key]=raw;e.MARKET_DATA.objects['gold/serving/CURRENT.json']=json.dumps({'serving_release_id':'test','manifest_key':key,'manifest_sha256':hashlib.sha256(raw).hexdigest()}).encode()
 result=asyncio.run(dispatch(e,'GET','/v1/metrics',{},None,'r'));assert result['data'][0]['metric_id']=='revenue'
 result=asyncio.run(dispatch(e,'GET','/v1/archive/prices/FB',{},None,'r'));assert result['data']['prices'][0]['close']=='38.22999954223633'
 result=asyncio.run(dispatch(e,'GET','/v1/archive/prices/FB',{'as_of':'2018-01-01T00:00:00Z'},None,'r'));assert result['data']['prices']==[];assert result['coverage']['pit_excluded']==1


def test_screen_evidence_matches_selected_metric():
 row={'symbol':'META','values':{'cash':{'value':'100','unit':'USD','evidence_id':'cash-proof'},'revenue':{'value':'300','unit':'USD','evidence_id':'revenue-proof'}},'evidence_ids':[{'evidence_id':'wrong'}]}
 result=select_screen_rows([row],[{'field':'cash','operator':'gt','value':'1'}],[],1)
 assert [r['evidence_id'] for r in result[0]['evidence']]==['cash-proof']


def test_filings_keep_date_only_metadata_and_filter_pit_before_paging():
 e=env();key='gold/serving/releases/test/manifest.json';m=json.loads(e.MARKET_DATA.objects[key]);path='entities/meta/filings/2025-01.json';rows=[{'filing_id':'1','filing_date':'2025-01-01','available_at':'2025-01-01','metadata_source':'SEC_COMPANYFACTS_FILED_DATE'},{'filing_id':'2','filing_date':'2025-01-02','available_at':'2025-01-02'}];raw=json.dumps(rows).encode();e.MARKET_DATA.objects['gold/serving/releases/test/'+path]=raw;m['artifacts'].append({'path':path,'sha256':hashlib.sha256(raw).hexdigest()});raw=json.dumps(m).encode();e.MARKET_DATA.objects[key]=raw;e.MARKET_DATA.objects['gold/serving/CURRENT.json']=json.dumps({'serving_release_id':'test','manifest_key':key,'manifest_sha256':hashlib.sha256(raw).hexdigest()}).encode()
 r=asyncio.run(dispatch(e,'GET','/v1/securities/META/filings',{'as_of':'2025-01-02T00:00:00Z','limit':'1'},None,'r'));assert r['page']['total']==1;assert r['data']['filings'][0]['filing_id']=='1';assert r['data']['filings'][0]['metadata_source']=='SEC_COMPANYFACTS_FILED_DATE'


def test_snapshot_reports_current_release_when_reusing_parent_bytes():
 e=env();key='gold/serving/releases/test/entities/meta/snapshot.json';raw=json.dumps({'symbol':'META','serving_release_id':'parent'}).encode();e.MARKET_DATA.objects[key]=raw;manifest_key='gold/serving/releases/test/manifest.json';m=json.loads(e.MARKET_DATA.objects[manifest_key]);next(a for a in m['artifacts'] if a['path']=='entities/meta/snapshot.json')['sha256']=hashlib.sha256(raw).hexdigest();raw=json.dumps(m).encode();e.MARKET_DATA.objects[manifest_key]=raw;e.MARKET_DATA.objects['gold/serving/CURRENT.json']=json.dumps({'serving_release_id':'test','manifest_key':manifest_key,'manifest_sha256':hashlib.sha256(raw).hexdigest()}).encode()
 result=asyncio.run(dispatch(e,'GET','/v1/securities/META',{},None,'r'))
 assert result['data']['serving_release_id']=='test'
 assert result['data']['source_artifact_release_id']=='parent'


def test_compact_archive_retains_exact_values_and_shared_provenance():
 e=env();values={'archive/index.json':[{'symbol':'FB','rows':1}],'archive/FB/prices/all.json':{'schema_version':'zion-archive-compact-v1','defaults':{'source_id':'archive','available_at':None},'rows':[{'session_date':'2012-05-18','close':'38.22999954223633'}]}};m=json.loads(e.MARKET_DATA.objects['gold/serving/releases/test/manifest.json'])
 for path,v in values.items():
  raw=json.dumps(v).encode();e.MARKET_DATA.objects['gold/serving/releases/test/'+path]=raw;m['artifacts'].append({'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'first_date':'2012-05-18','last_date':'2012-05-18'})
 raw=json.dumps(m).encode();e.MARKET_DATA.objects['gold/serving/releases/test/manifest.json']=raw;e.MARKET_DATA.objects['gold/serving/CURRENT.json']=json.dumps({'serving_release_id':'test','manifest_key':'gold/serving/releases/test/manifest.json','manifest_sha256':hashlib.sha256(raw).hexdigest()}).encode()
 r=asyncio.run(dispatch(e,'GET','/v1/archive/prices/FB',{'start_date':'2012-01-01','end_date':'2012-12-31'},None,'r'));assert r['data']['prices'][0]['source_id']=='archive';assert r['data']['prices'][0]['close']=='38.22999954223633'


def test_bulk_resolves_current_once_and_pins_all_subqueries():
 e=env();r=asyncio.run(dispatch(e,'POST','/v1/bulk/query',{}, {'queries':[{'path':'/v1/prices/META','params':{'limit':'2'}},{'path':'/v1/fundamentals/META','params':{'period':'quarterly','limit':'2'}}]},'r'))
 assert all(x['release']['serving_release_id']=='test' for x in r['data']['results'])
 assert e.MARKET_DATA.reads.count('gold/serving/CURRENT.json')==1


def test_multi_metric_history_reports_missing_metric_without_dropping_valid_data():
 r=asyncio.run(dispatch(env(),'GET','/v1/fundamentals/META',{'metrics':'revenue,gross_profit','period':'quarterly'},None,'r'))
 assert r['data']['observations']
 assert r['coverage']['missing_metrics']==['gross_profit']
 assert r['coverage']['status']=='PARTIAL'
 assert r['coverage']['limitations'][0]['code']=='METRIC_NOT_AVAILABLE'


def test_sec_date_only_availability_waits_for_next_new_york_midnight():
 from serving_v2 import _available,_as_of,_select_revisions
 row={'available_at':'2025-01-01','source_id':'SEC','metric_id':'revenue','period_type':'quarterly','period_end':'2024-12-31','value':'1'}
 assert not _available(row,_as_of('2025-01-02T04:59:59Z'))
 assert _available(row,_as_of('2025-01-02T05:00:00Z'))
 earlier={**row,'available_at':'2025-01-02T01:00:00Z','value':'2'}
 assert _select_revisions([row,earlier],_as_of('2025-01-02T06:00:00Z'))[0]['value']=='1'


def test_monetary_screen_uses_comparable_usd_units_and_returns_units():
 rows=[{'symbol':'USD','values':{'revenue':{'value':'10','unit':'USD'}}},{'symbol':'CAD','values':{'revenue':{'value':'1000','unit':'CAD'}}},{'symbol':'unknown','values':{'revenue':{'value':'9000'}}}]
 result=select_screen_rows(rows,[],[{'field':'revenue','direction':'desc'}],10)
 assert [r['symbol'] for r in result]==['USD']
 assert result[0]['units']=={'revenue':'USD'}
