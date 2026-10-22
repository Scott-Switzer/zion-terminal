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



# --- historical session serving through the pinned session index ---------------

CURRENT_KEY = 'control/daily-prices/CURRENT.json'
CURRENT_SESSION = '2026-09-30'
PRIOR_SESSION = '2026-09-29'


def _release(session, close):
    payload = {
        'session': session, 'provider': 'alpaca', 'feed': 'sip',
        'counts': {'target': 1, 'returned': 1, 'noSessionBar': 0, 'invalid': 0},
        'selection_policy': 'valid bars only',
        'bars': [{'provider_symbol': 'META', 'security_id': 'meta', 'session_date': session,
                  'close': close, 'open': close, 'high': close, 'low': close, 'volume': 10}],
    }
    raw = json.dumps(payload)
    sha = hashlib.sha256(raw.encode()).hexdigest()
    return raw, sha, 'gold/daily-prices/releases/' + sha + '/data.json'


def _indexed_env(flag='true', *, session_entry=True, tamper_index=False,
                 tamper_release=False, stale=False, drop_index=False):
    current_raw, current_sha, current_key = _release(CURRENT_SESSION, 725.18)
    prior_raw, prior_sha, prior_key = _release(PRIOR_SESSION, 700.5)
    entry = {'release_id': prior_sha, 'data_key': prior_key, 'bytes': len(prior_raw.encode()),
             'rows': 1, 'provider': 'alpaca', 'feed': 'sip',
             'published_at': '2026-09-30T01:00:00.000Z', 'previous_version': None}
    current_entry = {**entry, 'release_id': current_sha, 'data_key': current_key,
                     'bytes': len(current_raw.encode())}
    sessions = {CURRENT_SESSION: current_entry}
    if session_entry:
        sessions[PRIOR_SESSION] = entry
    index = {'schema_version': 1, 'updated_at': '2026-09-30T01:00:00.000Z',
             'retention': {'max_sessions': 300},
             'latest_session': PRIOR_SESSION if stale else CURRENT_SESSION,
             'sessions': sessions}
    index_raw = json.dumps(index)
    if tamper_index:
        index_raw = index_raw.replace('"rows": 1', '"rows": 2')
    index_sha = hashlib.sha256(index_raw.encode()).hexdigest()
    index_key = 'control/daily-prices/sessions/' + index_sha + '.json'
    objects = {CURRENT_KEY: json.dumps({'version': current_sha, 'sha256': current_sha,
                                        'session': CURRENT_SESSION, 'provider': 'alpaca',
                                        'feed': 'sip', 'data_key': current_key,
                                        'sessions_index': {'key': index_key, 'sha256': index_sha,
                                                           'bytes': len(index_raw.encode())}}),
               current_key: current_raw,
               index_key: index_raw}
    if session_entry:
        objects[prior_key] = prior_raw + ('x' if tamper_release else '')
    if drop_index:
        objects.pop(index_key)
    env = SimpleNamespace(MARKET_DATA=Bucket(objects))
    if flag is not None:
        env.SESSION_INDEX_ENABLED = flag
    return env


def _expect(call, code):
    with pytest.raises(ContractError) as err:
        call()
    assert err.value.code == code, err.value.code


def test_indexed_prior_session_is_served_after_hash_verification():
    r = asyncio.run(dispatch(_indexed_env(), 'GET', '/v1/market/prices',
                             {'session': PRIOR_SESSION}, None, 'r'))
    assert r['release']['session'] == PRIOR_SESSION
    assert r['release']['market_release_id'] == hashlib.sha256(
        _release(PRIOR_SESSION, 700.5)[0].encode()).hexdigest()
    assert r['data']['prices'][0]['close'] == '700.5'
    assert r['data']['prices'][0]['session_date'] == PRIOR_SESSION
    assert r['coverage']['index_status'] == 'SESSION_INDEX_VERIFIED'
    assert r['page']['release_id'] == r['release']['market_release_id']


def test_current_session_request_does_not_require_an_index():
    r = asyncio.run(dispatch(_indexed_env(drop_index=True), 'GET', '/v1/market/prices',
                             {'session': CURRENT_SESSION}, None, 'r'))
    assert r['release']['session'] == CURRENT_SESSION
    assert 'index_status' not in r['coverage']


def test_stale_index_falls_back_instead_of_serving_a_session():
    _expect(lambda: asyncio.run(dispatch(_indexed_env(stale=True), 'GET', '/v1/market/prices',
                                         {'session': PRIOR_SESSION}, None, 'r')),
            'SESSION_INDEX_STALE')


def test_unindexed_session_is_not_synthesised():
    _expect(lambda: asyncio.run(dispatch(_indexed_env(session_entry=False), 'GET', '/v1/market/prices',
                                         {'session': '2026-09-28'}, None, 'r')),
            'SESSION_NOT_INDEXED')


def test_missing_index_object_fails_closed():
    _expect(lambda: asyncio.run(dispatch(_indexed_env(drop_index=True), 'GET', '/v1/market/prices',
                                         {'session': PRIOR_SESSION}, None, 'r')),
            'SESSION_INDEX_MISSING')


def test_tampered_index_bytes_are_rejected():
    _expect(lambda: asyncio.run(dispatch(_indexed_env(tamper_index=True), 'GET', '/v1/market/prices',
                                         {'session': PRIOR_SESSION}, None, 'r')),
            'SESSION_INDEX_CORRUPT')


def test_tampered_indexed_release_is_rejected():
    _expect(lambda: asyncio.run(dispatch(_indexed_env(tamper_release=True), 'GET', '/v1/market/prices',
                                         {'session': PRIOR_SESSION}, None, 'r')),
            'SESSION_INDEX_CORRUPT')


@pytest.mark.parametrize('flag', [None, '', 'false'])
def test_historical_serving_stays_off_without_the_staging_flag(flag):
    _expect(lambda: asyncio.run(dispatch(_indexed_env(flag=flag), 'GET', '/v1/market/prices',
                                         {'session': PRIOR_SESSION}, None, 'r')),
            'SESSION_INDEX_DISABLED')


def test_session_parameter_is_rejected_outside_market_routes():
    _expect(lambda: asyncio.run(dispatch(_indexed_env(), 'GET', '/v1/metrics',
                                         {'session': PRIOR_SESSION}, None, 'r')),
            'INVALID_ARGUMENT')


# --- producer-generated fixture (cross-language contract) ---------------------

FIXTURE_PATH = Path(__file__).parents[1] / 'fixtures' / 'market-session-index.json'


def _fixture_env(flag='true'):
    fixture = json.loads(FIXTURE_PATH.read_text())
    env = SimpleNamespace(MARKET_DATA=Bucket(fixture['objects']))
    if flag is not None:
        env.SESSION_INDEX_ENABLED = flag
    return fixture, env


def test_producer_generated_index_is_served_by_the_thin_worker():
    fixture, env = _fixture_env()
    prior = fixture['prior_session']
    r = asyncio.run(dispatch(env, 'GET', '/v1/market/prices', {'session': prior}, None, 'r'))
    assert r['release']['session'] == prior
    assert r['release']['market_release_id'] == r['page']['release_id']
    assert r['data']['prices'] and all(p['session_date'] == prior for p in r['data']['prices'])
    assert r['coverage']['index_status'] == 'SESSION_INDEX_VERIFIED'


def test_producer_fixture_current_session_still_serves():
    fixture, env = _fixture_env()
    r = asyncio.run(dispatch(env, 'GET', '/v1/market/coverage', {}, None, 'r'))
    assert r['release']['session'] == fixture['latest_session']


def test_producer_fixture_tampered_index_is_rejected():
    fixture, env = _fixture_env()
    pointer = json.loads(fixture['objects'][CURRENT_KEY])
    key = pointer['sessions_index']['key']
    objects = dict(fixture['objects'])
    objects[key] = objects[key].replace('"max_sessions":300', '"max_sessions":299', 1)
    tampered = SimpleNamespace(MARKET_DATA=Bucket(objects), SESSION_INDEX_ENABLED='true')
    _expect(lambda: asyncio.run(dispatch(tampered, 'GET', '/v1/market/prices',
                                         {'session': fixture['prior_session']}, None, 'r')),
            'SESSION_INDEX_CORRUPT')
