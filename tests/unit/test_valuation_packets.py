import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / 'deploy/valuation/src'))
from packet_service import serve, PacketError, POINTER


class Bucket:
    def __init__(self, objects):
        self.objects = objects
        self.keys = []

    async def get(self, key):
        self.keys.append(key)
        raw = self.objects.get(key)
        if raw is None:
            return None
        async def text():
            return raw
        return SimpleNamespace(size=len(raw.encode()), text=text)


def fixture():
    raw = json.dumps({'schema_version': 'ppe-valuation-packet-v1', 'ticker': 'MSFT', 'cutoff_date': '2026-01-01'})
    digest = hashlib.sha256(raw.encode()).hexdigest()
    key = f'gold/valuation/releases/{digest}/companies/MSFT.json'
    index = {'schema_version': 'ppe-valuation-index-v1', 'companies': {'MSFT': {'key': key, 'sha256': digest}}}
    return Bucket({POINTER: json.dumps(index), key: raw}), key


def test_compact_packet_reads_only_index_and_company():
    bucket, key = fixture()
    packet, digest = asyncio.run(serve(bucket, 'MSFT', '2026-01-02'))
    assert packet['ticker'] == 'MSFT'
    assert len(digest) == 64
    assert bucket.keys == [POINTER, key]


@pytest.mark.parametrize('failure', ['hash', 'path', 'identity', 'size', 'historical', 'missing'])
def test_fail_closed(failure):
    bucket, key = fixture()
    ticker, cutoff = 'MSFT', '2026-01-02'
    if failure == 'hash':
        bucket.objects[key] += ' '
    elif failure == 'path':
        index = json.loads(bucket.objects[POINTER]);index['companies']['MSFT']['key'] = 'private/data.json';bucket.objects[POINTER] = json.dumps(index)
    elif failure == 'identity':
        ticker = '../MSFT'
    elif failure == 'size':
        bucket.objects[key] = ' ' * 131073
    elif failure == 'historical':
        cutoff = '2025-01-01'
    else:
        ticker = 'AAPL'
    with pytest.raises(PacketError):
        asyncio.run(serve(bucket, ticker, cutoff))
    assert 'private/data.json' not in bucket.keys
