import asyncio,json,hashlib,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[2]/'deploy/cloudflare-query/src'))
from data_api import dispatch
from contract_v2 import ContractError
class Obj:
 def __init__(self,s):self.s=s
 async def text(self):return self.s
class Bucket:
 def __init__(self,objects):self.objects=objects
 async def get(self,key):return Obj(self.objects[key]) if key in self.objects else None
def source_env(corrupt=False):
 v={'session':'2026-09-30','provider':'alpaca','feed':'sip','counts':{'target':3,'returned':2,'noSessionBar':0,'invalid':1},'selection_policy':'valid bars only','bars':[{'provider_symbol':'META','security_id':'meta','session_date':'2026-09-30','close':725.18,'open':720,'high':730,'low':715,'volume':123},{'provider_symbol':'HBAN','security_id':'hban','session_date':'2026-09-30','close':15.26,'volume':234}]}
 raw=json.dumps(v);sha=hashlib.sha256(raw.encode()).hexdigest();key='gold/daily-prices/releases/'+sha+'/data.json'
 return SimpleNamespace(MARKET_DATA=Bucket({'control/daily-prices/CURRENT.json':json.dumps({'version':sha,'sha256':sha,'session':'2026-09-30','provider':'alpaca','feed':'sip','data_key':key}),key:raw+'x' if corrupt else raw}))
def test_whole_market_prices_are_source_qualified_and_query_multiple_symbols():
 r=asyncio.run(dispatch(source_env(),'GET','/v1/market/prices',{'q':'META,HBAN'},None,'r'))
 assert len(r['data']['prices'])==2
 assert next(p for p in r['data']['prices'] if p['symbol']=='META')['close']=='725.18'
 assert r['coverage']['point_in_time_status']=='NOT_ESTABLISHED'
 assert r['coverage']['counts']['invalid']==1
 assert all(p['available_at'] is None for p in r['data']['prices'])
def test_market_prices_do_not_claim_historical_pit_eligibility():
 with pytest.raises(ContractError,match='availability'):
  asyncio.run(dispatch(source_env(),'GET','/v1/market/prices/META',{'as_of':'2026-09-30T21:00:00Z'},None,'r'))
def test_market_release_rejects_modified_data_bytes():
 with pytest.raises(ContractError,match='hash'):
  asyncio.run(dispatch(source_env(True),'GET','/v1/market/coverage',{},None,'r'))
