import asyncio
import importlib
import sys
from types import SimpleNamespace
from pathlib import Path


def test_compare_pins_one_release_and_returns_its_identity(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2]/'deploy/cloudflare-query/src'))
    monkeypatch.setitem(sys.modules,'workers',SimpleNamespace(Response=object,WorkerEntrypoint=object))
    thin=importlib.import_module('thin_worker')
    calls=[]
    async def load(env):
        calls.append('CURRENT')
        return {},{'serving_release_id':'pinned'}
    async def facts(env,symbol,metrics,**kwargs):
        manifest=kwargs.get('manifest')
        return {'observations':[{'entity_id':symbol}], 'release':{'serving_release_id':manifest['serving_release_id'] if manifest else 'changed'}}
    monkeypatch.setattr(thin,'load_release',load)
    monkeypatch.setattr(thin,'fundamentals',facts)
    worker=thin.Default();worker.env=object()
    result=asyncio.run(worker._tool('compare',{'entities':['META','AAPL'],'metric':'revenue','period':'annual'},'test'))
    assert calls==['CURRENT']
    assert result['release']['serving_release_id']=='pinned'
    assert len(result['evidence'])==2
