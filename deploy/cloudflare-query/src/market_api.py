"""Source-qualified whole-market daily prices; separate from PIT canonical facts."""
import hashlib,json,re
from decimal import Decimal
from contract_v2 import ContractError
_CACHE=None
async def market_dispatch(env,path,params,rid):
 global _CACHE
 if params.get('as_of'):raise ContractError('SOURCE_LIMITED','provider publication has no established row availability timestamps; historical PIT requests require canonical prices',status=422)
 obj=await env.MARKET_DATA.get('control/daily-prices/CURRENT.json')
 if obj is None:raise ContractError('SOURCE_LIMITED','whole-market daily publication is not available',status=503)
 pointer=json.loads(await obj.text());sha=pointer.get('sha256','');key=pointer.get('data_key')
 if not re.fullmatch('[a-f0-9]{64}',sha) or pointer.get('version')!=sha or key!='gold/daily-prices/releases/'+sha+'/data.json':raise ContractError('SOURCE_LIMITED','invalid daily publication pointer',status=503)
 if params.get('release_id') and params['release_id']!=sha:raise ContractError('RELEASE_CHANGED','restart pagination against the current market release',status=409)
 if _CACHE is not None and _CACHE[0] is env.MARKET_DATA and _CACHE[1]==sha:data=_CACHE[2]
 else:
  obj=await env.MARKET_DATA.get(key)
  if obj is None:raise ContractError('SOURCE_LIMITED','daily publication data is missing',status=503)
  raw=str(await obj.text()).encode('utf-8')
  if len(raw)>16*1024*1024:raise ContractError('RESPONSE_TOO_LARGE','market source exceeds bounded loading budget',status=413)
  if hashlib.sha256(raw).hexdigest()!=sha:raise ContractError('SOURCE_LIMITED','daily publication data hash mismatch',status=503)
  data=json.loads(raw,parse_float=Decimal)
  if data.get('session')!=pointer.get('session') or data.get('provider')!=pointer.get('provider') or data.get('feed')!=pointer.get('feed') or not isinstance(data.get('bars'),list):raise ContractError('SOURCE_LIMITED','daily publication metadata mismatch',status=503)
  _CACHE=(env.MARKET_DATA,sha,data)
 result={'release':{'market_release_id':sha,'provider':data['provider'],'feed':data['feed'],'session':data['session'],'data_key':key,'sha256':sha,'published_at':pointer.get('published_at')},'coverage':{'status':'SOURCE_QUALIFIED','point_in_time_status':'NOT_ESTABLISHED','counts':data['counts'],'selection_policy':data.get('selection_policy'),'limitations':['Provider daily prices are separate from canonical fundamental coverage; no row publication timestamp is inferred.']},'request_id':rid}
 if path=='/v1/market/coverage':result['data']={'session':data['session'],'counts':data['counts'],'price_qualified_securities':len(data['bars'])};return result
 if path not in ['/v1/market/prices','/v1/market/securities'] and not path.startswith('/v1/market/prices/'):raise ContractError('NOT_FOUND','market route not found',status=404)
 query=path.removeprefix('/v1/market/prices/') if path.startswith('/v1/market/prices/') else params.get('q','')
 symbols={s.strip().upper() for s in query.split(',') if s.strip()}
 if len(symbols)>100 or any(not re.fullmatch('[A-Z0-9][A-Z0-9.-]{0,19}',s) for s in symbols):raise ContractError('INVALID_ARGUMENT','request up to 100 valid symbols')
 rows=[r for r in data['bars'] if not symbols or r.get('provider_symbol') in symbols]
 missing=sorted(symbols-{r.get('provider_symbol') for r in rows});result['coverage']['missing_symbols']=missing
 if params.get('start_date') and data['session']<params['start_date'] or params.get('end_date') and data['session']>params['end_date']:rows=[]
 rows.sort(key=lambda r:r['provider_symbol'])
 try:offset=int(params.get('offset',0));limit=int(params.get('limit',100))
 except (ValueError,TypeError):raise ContractError('INVALID_ARGUMENT','integer offset and limit required')
 if offset<0 or not 1<=limit<=1000:raise ContractError('INVALID_ARGUMENT','offset must be nonnegative; limit must be 1-1000')
 total=len(rows);rows=rows[offset:offset+limit];result['page']={'offset':offset,'limit':limit,'total':total,'next_offset':offset+limit if offset+limit<total else None,'release_id':sha}
 if path=='/v1/market/securities':result['data']={'securities':[{'symbol':r['provider_symbol'],'security_id':r['security_id'],'session':r['session_date'],'price_status':'SOURCE_QUALIFIED'} for r in rows]}
 else:
  prices=[]
  for r in rows:
   price={**r,'symbol':r['provider_symbol'],'available_at':None,'source_id':'Alpaca '+data['feed']+' daily bars','unit':'USD/share','currency':'USD','provenance':{'market_release_id':sha,'data_key':key,'sha256':sha}}
   for field in ['open','high','low','close','vwap']:
    if r.get(field) is not None:price[field]=str(r[field])
   prices.append(price)
  result['data']={'prices':prices}
 return result
