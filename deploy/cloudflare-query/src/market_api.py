"""Source-qualified whole-market daily prices; separate from PIT canonical facts."""
import hashlib,json,re
from decimal import Decimal
from contract_v2 import ContractError
_CACHE=None
_INDEX_MAX=512*1024
_HISTORY_ON=frozenset({'1','true','yes','enabled'})
def _history_enabled(env):
 return str(getattr(env,'SESSION_INDEX_ENABLED','') or '').strip().lower() in _HISTORY_ON
def _index_error(code,message):
 raise ContractError(code,message,status=503)
async def _index_entry(env,pointer,requested):
 """Return a verified index entry for one session, or raise a typed error."""
 pin=pointer.get('sessions_index')
 if not isinstance(pin,dict):_index_error('SESSION_INDEX_MISSING','CURRENT has no session index pin')
 key=pin.get('key');sha=pin.get('sha256');size=pin.get('bytes')
 if (not isinstance(key,str) or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha)
  or key!='control/daily-prices/sessions/'+sha+'.json' or not isinstance(size,int) or isinstance(size,bool)
  or not 1<=size<=_INDEX_MAX):_index_error('SESSION_INDEX_CORRUPT','session index pin is invalid')
 obj=await env.MARKET_DATA.get(key)
 if obj is None:_index_error('SESSION_INDEX_MISSING','session index object is missing')
 raw=str(await obj.text()).encode('utf-8')
 if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=sha:_index_error('SESSION_INDEX_CORRUPT','session index bytes do not match the pin')
 try:index=json.loads(raw)
 except ValueError:_index_error('SESSION_INDEX_CORRUPT','session index is not JSON')
 if (not isinstance(index,dict) or index.get('schema_version')!=1 or not isinstance(index.get('sessions'),dict)
  or not isinstance(index.get('latest_session'),str)):_index_error('SESSION_INDEX_CORRUPT','session index contract is invalid')
 if index['latest_session']!=pointer.get('session'):_index_error('SESSION_INDEX_STALE','session index does not describe the current release')
 entry=index['sessions'].get(requested) if requested in index['sessions'] else None
 if not isinstance(entry,dict):_index_error('SESSION_NOT_INDEXED','requested session is not indexed')
 release_id=entry.get('release_id');data_key=entry.get('data_key')
 if (not isinstance(release_id,str) or not re.fullmatch('[a-f0-9]{64}',release_id)
  or data_key!='gold/daily-prices/releases/'+release_id+'/data.json'
  or not isinstance(entry.get('bytes'),int) or isinstance(entry.get('bytes'),bool)
  or not isinstance(entry.get('rows'),int) or isinstance(entry.get('rows'),bool)
  or not isinstance(entry.get('provider'),str) or not isinstance(entry.get('feed'),str)):
  _index_error('SESSION_INDEX_CORRUPT','session entry is invalid')
 return entry
async def _indexed_release(env,entry,requested):
 """Load the indexed release and re-check its bytes and metadata before serving."""
 obj=await env.MARKET_DATA.get(entry['data_key'])
 if obj is None:_index_error('SESSION_INDEX_CORRUPT','indexed session release is missing')
 raw=str(await obj.text()).encode('utf-8')
 if len(raw)>16*1024*1024:raise ContractError('RESPONSE_TOO_LARGE','market source exceeds bounded loading budget',status=413)
 if len(raw)!=entry['bytes'] or hashlib.sha256(raw).hexdigest()!=entry['release_id']:_index_error('SESSION_INDEX_CORRUPT','indexed session release does not match its entry')
 try:data=json.loads(raw,parse_float=Decimal)
 except ValueError:_index_error('SESSION_INDEX_CORRUPT','indexed session release is not JSON')
 if (data.get('session')!=requested or data.get('provider')!=entry['provider'] or data.get('feed')!=entry['feed']
  or not isinstance(data.get('bars'),list) or len(data['bars'])!=entry['rows']):
  _index_error('SESSION_INDEX_CORRUPT','indexed session release metadata does not match its entry')
 return data
async def market_dispatch(env,path,params,rid):
 global _CACHE
 if params.get('as_of'):raise ContractError('SOURCE_LIMITED','provider publication has no established row availability timestamps; historical PIT requests require canonical prices',status=422)
 obj=await env.MARKET_DATA.get('control/daily-prices/CURRENT.json')
 if obj is None:raise ContractError('SOURCE_LIMITED','whole-market daily publication is not available',status=503)
 pointer=json.loads(await obj.text());sha=pointer.get('sha256','');key=pointer.get('data_key')
 if not re.fullmatch('[a-f0-9]{64}',sha) or pointer.get('version')!=sha or key!='gold/daily-prices/releases/'+sha+'/data.json':raise ContractError('SOURCE_LIMITED','invalid daily publication pointer',status=503)
 if params.get('release_id') and params['release_id']!=sha:raise ContractError('RELEASE_CHANGED','restart pagination against the current market release',status=409)
 requested=params.get('session')
 if requested is not None and (not isinstance(requested,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',requested)):raise ContractError('INVALID_ARGUMENT','session must be YYYY-MM-DD',status=400)
 historical=bool(requested) and requested!=pointer.get('session')
 if historical:
  if not _history_enabled(env):raise ContractError('SESSION_INDEX_DISABLED','historical session serving is disabled in this environment',status=503)
  entry=await _index_entry(env,pointer,requested)
  data=await _indexed_release(env,entry,requested)
  sha=entry['release_id'];key=entry['data_key']
 elif _CACHE is not None and _CACHE[0] is env.MARKET_DATA and _CACHE[1]==sha:data=_CACHE[2]
 else:
  obj=await env.MARKET_DATA.get(key)
  if obj is None:raise ContractError('SOURCE_LIMITED','daily publication data is missing',status=503)
  raw=str(await obj.text()).encode('utf-8')
  if len(raw)>16*1024*1024:raise ContractError('RESPONSE_TOO_LARGE','market source exceeds bounded loading budget',status=413)
  if hashlib.sha256(raw).hexdigest()!=sha:raise ContractError('SOURCE_LIMITED','daily publication data hash mismatch',status=503)
  data=json.loads(raw,parse_float=Decimal)
  if data.get('session')!=pointer.get('session') or data.get('provider')!=pointer.get('provider') or data.get('feed')!=pointer.get('feed') or not isinstance(data.get('bars'),list):raise ContractError('SOURCE_LIMITED','daily publication metadata mismatch',status=503)
  _CACHE=(env.MARKET_DATA,sha,data)
 result={'release':{'market_release_id':sha,'provider':data['provider'],'feed':data['feed'],'session':data['session'],'data_key':key,'sha256':sha},'coverage':{'status':'SOURCE_QUALIFIED','point_in_time_status':'NOT_ESTABLISHED','counts':data['counts'],'selection_policy':data.get('selection_policy'),'limitations':['Provider daily prices are separate from canonical fundamental coverage; no row publication timestamp is inferred.']},'request_id':rid}
 if historical:result['coverage']['index_status']='SESSION_INDEX_VERIFIED';result['coverage']['session_index_key']=pointer['sessions_index']['key']
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
