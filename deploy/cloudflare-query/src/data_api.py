"""REST views over the same immutable, hash-verified canonical release as tools."""
import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import unquote
from contract_v2 import ContractError, calculate_exact
from serving_v2 import load_release, resolve_security, artifact, _as_of, _available, _select_revisions, _serve_row, corporate_actions, ServingV2Error, _identity_covers


def select_screen_rows(records, filters, sorts, limit):
 fields={r['field'] for r in filters+sorts} or {'revenue','operating_margin','net_income','last_price'}
 rows=[]
 for record in records:
  values={f:(record.get('values',{}).get(f) or {}).get('value') for f in fields}
  def matches(item):
   try:
    value=Decimal(str(values.get(item['field'])))
    if not value.is_finite():return False
    op=item['operator']
    if op=='between':return Decimal(str(item['values'][0]))<=value<=Decimal(str(item['values'][1]))
    right=Decimal(str(item['value']))
    return {'eq':value==right,'ne':value!=right,'gt':value>right,'gte':value>=right,'lt':value<right,'lte':value<=right}[op]
   except (InvalidOperation,KeyError,ValueError):return False
  if all(matches(f) for f in filters):rows.append({'symbol':record['symbol'],'values':values,'evidence':[record['values'][f] for f in sorted(fields) if record.get('values',{}).get(f)]})
 for item in reversed(sorts):
  field=item['field'];present=[r for r in rows if r['values'].get(field) is not None];missing=[r for r in rows if r['values'].get(field) is None]
  present.sort(key=lambda r:Decimal(str(r['values'][field])),reverse=item.get('direction','desc')=='desc');rows=present+missing
 return rows[:limit]


def page(rows,params,release):
 try:offset=int(params.get('offset',0));limit=int(params.get('limit',1000))
 except (ValueError,TypeError):raise ContractError('INVALID_ARGUMENT','offset and limit must be integers')
 if offset<0 or not 1<=limit<=1000:raise ContractError('INVALID_ARGUMENT','offset must be nonnegative; limit must be 1–1000')
 return rows[offset:offset+limit],{'offset':offset,'limit':limit,'total':len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None,'serving_release_id':release}


def date_range(params):
 start=params.get('start_date');end=params.get('end_date')
 try:
  for value in (start,end):
   if value:date.fromisoformat(value)
 except (ValueError,TypeError):raise ContractError('INVALID_ARGUMENT','date range must use YYYY-MM-DD')
 if start and end and start>end:raise ContractError('INVALID_ARGUMENT','start_date must precede end_date')
 return start,end


async def dispatch(env,method,path,params,body,rid,*,_release_manifest=None):
 allowed={"q","period","metrics","metric","start_date","end_date","as_of","release_id","offset","limit"}
 if not isinstance(params,dict) or set(params)-allowed or any(not isinstance(v,str) and not (k in {"offset","limit"} and isinstance(v,int) and not isinstance(v,bool)) for k,v in params.items()):raise ContractError("INVALID_ARGUMENT","unsupported or invalid query parameters")
 if path=="/v1/search":path="/v1/securities"
 for domain in ("prices","fundamentals","revisions","entities","filings"):
  prefix="/v1/"+domain+"/"
  if path.startswith(prefix):path="/v1/securities/"+path.removeprefix(prefix)+("" if domain=="entities" else "/"+domain);break
 if path=='/v1/calculate' and method=='POST':
  if not isinstance(body,dict):raise ContractError('INVALID_ARGUMENT','JSON object required')
  values=body.get('values',[])
  if not isinstance(values,list) or len(values)>1000:raise ContractError('INVALID_ARGUMENT','bounded values array required')
  if any(isinstance(value,(float,bool)) or not isinstance(value,(int,str)) for value in values):raise ContractError('INVALID_ARGUMENT','values must be JSON integers or exact decimal strings')
  try:finite=all(Decimal(str(value)).is_finite() for value in values)
  except InvalidOperation:finite=False
  if not finite:raise ContractError('INVALID_ARGUMENT','values must be finite decimals')
  nums=[Decimal(str(v)) for v in values]
  integer_digits=max([max(1,n.adjusted()+1) for n in nums] or [1]);fraction_digits=max([max(0,-n.as_tuple().exponent) for n in nums] or [0])
  if integer_digits+fraction_digits>4096:raise ContractError('INVALID_ARGUMENT','decimal expansion exceeds 4096 digits')
  precision=max(64,integer_digits+fraction_digits+len(str(len(nums)))+2)
  with localcontext() as context:
   context.prec=precision
   calculated=calculate_exact(body.get('operation'),values)
   calculated['arithmetic']={'precision':precision,'rounding':context.rounding,'division_policy':'rounded to stated precision'}
  return {'data':calculated,'request_id':rid}
 if _release_manifest is None:_,manifest=await load_release(env)
 else:manifest=_release_manifest
 release=manifest['serving_release_id']
 if params.get('release_id') and params['release_id']!=release:raise ContractError('RELEASE_CHANGED','restart pagination against the current release',status=409)
 result={'release':{'serving_release_id':release,'source':manifest['source']},'request_id':rid}
 if method=='GET' and path in ('/v1/metrics','/v1/archive/securities','/v1/coverage'):
  filename={'/v1/metrics':'catalog/metrics.json','/v1/archive/securities':'archive/index.json','/v1/coverage':'catalog/coverage.json'}[path]
  if not any(a['path']==filename for a in manifest['artifacts']):raise ContractError('DATA_NOT_PUBLISHED','catalog is not published in this release',status=422)
  rows=await artifact(env,manifest,filename)
  if isinstance(rows,list):
   q=params.get('q','').casefold();rows=[row for row in rows if not q or q in str(row).casefold()]
   result['data'],result['page']=page(rows,params,release)
  else:result['data']=rows
  return result
 if method=='GET' and path.startswith('/v1/archive/prices/'):
  symbol=unquote(path.removeprefix('/v1/archive/prices/')).upper()
  index=await artifact(env,manifest,'archive/index.json');entry=next((row for row in index if row['symbol']==symbol),None)
  if entry is None:raise ContractError('SECURITY_NOT_FOUND','source security is not published',status=404)
  start,end=date_range(params);cutoff=_as_of(params.get('as_of'));prefix='archive/'+symbol+'/prices/';rows=[];excluded=0
  for item in sorted(manifest['artifacts'],key=lambda a:a['path']):
   filename=item['path'];year=filename.removeprefix(prefix).removesuffix('.json')
   if not filename.startswith(prefix) or (start and item.get('last_date',year)<(start if item.get('last_date') else start[:4])) or (end and item.get('first_date',year)>(end if item.get('first_date') else end[:4])):continue
   payload=await artifact(env,manifest,filename)
   defaults=payload.get('defaults',{}) if isinstance(payload,dict) else {}
   records=payload.get('rows',[]) if isinstance(payload,dict) else payload
   for compact in records:
    row={**defaults,**compact}
    if (start and row['session_date']<start) or (end and row['session_date']>end):continue
    if not _available(row,cutoff):excluded+=1;continue
    rows.append({**row,'provenance':{'serving_release_id':release,'artifact':filename,'storage_key':item.get('storage_key')}})
  rows.sort(key=lambda row:row['session_date']);result['coverage']={'source_security':entry,'pit_excluded':excluded,'identity_policy':'source-qualified; no inferred current issuer merge'}
  rows,result['page']=page(rows,params,release);result['data']={'prices':rows};return result
 if path=='/v1/bulk/query' and method=='POST':
  queries=body.get('queries') if isinstance(body,dict) else None
  if not isinstance(queries,list) or not 1<=len(queries)<=20:raise ContractError('INVALID_ARGUMENT','queries must contain 1–20 requests')
  results=[]
  for query in queries:
   if not isinstance(query,dict) or not isinstance(query.get('path'),str) or not (query['path'].startswith('/v1/securities/') or query['path'].startswith('/v1/archive/prices/') or any(query['path'].startswith('/v1/'+domain+'/') for domain in ('prices','fundamentals','revisions','entities','filings'))):raise ContractError('INVALID_ARGUMENT','bulk requests require security REST paths')
   parameters=query.get('params',{})
   if not isinstance(parameters,dict):raise ContractError('INVALID_ARGUMENT','bulk params must be objects')
   if parameters.get('release_id') and parameters['release_id']!=release:raise ContractError('RELEASE_CHANGED','bulk query release does not match the pinned release',status=409)
   results.append(await dispatch(env,'GET',query['path'],{**parameters,'release_id':release},None,rid,_release_manifest=manifest))
  result['data']={'results':results};return result
 if path.startswith('/v1/evidence/') and method=='GET':
  evidence_id=unquote(path.removeprefix('/v1/evidence/'));result['data']={'evidence':await evidence_rows(env,manifest,evidence_id)};return result
 if path=='/v1/securities' and method=='GET':
  index=await artifact(env,manifest,'identity/resolver_index.json');q=params.get('q','').upper().strip()
  names={}
  if any(a['path']=='identity/entities.json' for a in manifest['artifacts']):
   names={e['entity_id']:e.get('legal_name') for e in await artifact(env,manifest,'identity/entities.json')}
  rows=[{'symbol':s,**entry,'display_name':names.get(entry.get('entity'))} for s,entry in sorted(index['symbols'].items()) if _identity_covers(entry,params.get('as_of')) and (not q or q in s or q in str(entry.get('entity','')).upper() or q in str(names.get(entry.get('entity')) or '').upper())]
  result['data'],result['page']=page(rows,params,release);return result
 if method!='GET' or not path.startswith('/v1/securities/'):raise ContractError('NOT_FOUND','REST route not found',status=404)
 parts=path.removeprefix('/v1/securities/').split('/');symbol=unquote(parts[0]).upper();kind=parts[1] if len(parts)==2 else 'snapshot' if len(parts)==1 else ''
 resolved=await resolve_security(env,manifest,symbol,params.get('as_of'));key=resolved['identity']['artifact_path'];result['identity']=resolved['identity']
 if not any(a['path']==key+'/snapshot.json' for a in manifest['artifacts']):raise ContractError('ENTITY_NOT_FOUND','security is not in this release',status=404)
 if kind=='snapshot':
  # Snapshot is explicitly current. PIT requests must use filtered history views.
  if params.get('as_of'):raise ContractError('INVALID_ARGUMENT','use fundamentals or prices for as_of requests')
  snapshot=await artifact(env,manifest,key+'/snapshot.json')
  result['data']={**snapshot,'source_artifact_release_id':snapshot.get('serving_release_id'),'serving_release_id':release};return result
 start,end=date_range(params);cutoff=_as_of(params.get('as_of'))
 if kind=='filings':
  prefix=key+'/filings/';files=[a for a in manifest['artifacts'] if a['path'].startswith(prefix)]
  if not files:raise ContractError('DATA_NOT_PUBLISHED','filing metadata is not published for this issuer',status=422)
  _,pagination=page([],params,release);offset=pagination['offset'];limit=pagination['limit'];total=0;selected=[]
  for item in sorted(files,key=lambda a:a['path']):
   month=item['path'].removeprefix(prefix).removesuffix('.json')
   if (start and (item.get('last_date',month)<(start if item.get('last_date') else start[:7]))) or (end and (item.get('first_date',month)>(end if item.get('first_date') else end[:7]))):continue
   for row in await artifact(env,manifest,item['path']):
    day=row.get('filing_date') or ''
    if not _available(row,cutoff) or (start and day<start) or (end and day>end):continue
    if offset<=total<offset+limit:selected.append({**row,'provenance':{'serving_release_id':release,'artifact':item['path']}})
    total+=1
  pagination.update({'total':total,'next_offset':offset+limit if offset+limit<total else None});result['page']=pagination;result['data']={'filings':selected};return result
 if kind in ('fundamentals','revisions'):
  period=params.get('period','quarterly')
  if period not in ('annual','quarterly'):raise ContractError('INVALID_ARGUMENT','period must be annual or quarterly')
  artifact_path=f'{key}/fundamentals/{period}.json';rows=await artifact(env,manifest,artifact_path)
  metrics=set(params.get('metrics',params.get('metric','')).split(','))-{''}
  available_metrics={row.get('metric_id') for row in rows if row.get('metric_id')}
  missing=sorted(metrics-available_metrics)
  result['coverage']={'requested_metrics':sorted(metrics),'available_metrics':sorted(available_metrics),'missing_metrics':missing,'status':'PARTIAL' if missing and metrics & available_metrics else 'SOURCE_LIMITED' if missing else 'AVAILABLE','limitations':[{'metric':metric,'code':'METRIC_NOT_AVAILABLE'} for metric in missing]}
  rows=[r for r in rows if _available(r,cutoff) and (not metrics or r.get('metric_id') in metrics) and (not start or r.get('period_end','')>=start) and (not end or r.get('period_end','')<=end)]
  if kind=='fundamentals':rows=_select_revisions(rows,cutoff)
  rows=sorted(rows,key=lambda r:(r.get('period_end',''),r.get('metric_id',''),r.get('available_at',''),r.get('observation_id','')))
  rows=[{**r,**_serve_row(r,release_id=release,artifact_path=artifact_path,source_snapshot_id=manifest['source']['fundamentals']['snapshot_id'])} for r in rows]
  rows,result['page']=page(rows,params,release);result['data']={('observations' if kind=='fundamentals' else 'revisions'):rows};return result
 if kind=='prices':
  prefix=f'{key}/prices/daily/';rows=[]
  for item in sorted(manifest['artifacts'],key=lambda x:x['path']):
   filename=item['path'];year=filename.removeprefix(prefix).removesuffix('.json')
   if not filename.startswith(prefix) or (start and year<start[:4]) or (end and year>end[:4]):continue
   for r in await artifact(env,manifest,filename):
    if _available(r,cutoff) and (not start or r['session_date']>=start) and (not end or r['session_date']<=end):rows.append({**r,'provenance':{'serving_release_id':release,'source_snapshot_id':manifest['source']['prices']['snapshot_id'],'artifact':filename}})
  rows.sort(key=lambda r:(r['session_date'],r.get('available_at',''),r.get('observation_id','')))
  rows,result['page']=page(rows,params,release);result['data']={'prices':rows};return result
 if kind=='corporate-actions':return await corporate_actions(env,symbol=symbol,start=start,end=end,as_of=params.get('as_of'),request_id=rid,manifest=manifest)
 raise ContractError('NOT_FOUND','REST route not found',status=404)


async def evidence_rows(env,manifest,evidence_id):
 filename='evidence/index/'+hashlib.sha256(evidence_id.encode()).hexdigest()[:2]+'.json'
 if not any(a['path']==filename for a in manifest['artifacts']):raise ContractError('EVIDENCE_INDEX_NOT_AVAILABLE','this release has no evidence index',status=422)
 index=await artifact(env,manifest,filename);paths=index.get(evidence_id,[])
 rows={}
 for path in paths:
  for row in await artifact(env,manifest,path):
   if row.get('evidence_id')==evidence_id:rows.setdefault(row['observation_id'],row)
 if not rows:raise ContractError('ENTITY_NOT_FOUND','evidence was not found',status=404)
 return list(rows.values())
