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
 rows=[{'symbol':str(i),'values':{'revenue':{'value':'9'}}} for i in range(101)]+[{'symbol':'META','values':{'revenue':{'value':'100'}}},{'symbol':'missing','values':{}}]
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
 result=asyncio.run(dispatch(env(),'POST','/v1/bulk/query',{}, {'queries':[{'path':'/v1/securities/META/fundamentals','params':{'period':'quarterly'}},{'path':'/v1/securities/META/prices'}]},'r'))
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
