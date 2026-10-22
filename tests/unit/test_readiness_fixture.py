import gzip
import hashlib
import json
from copy import deepcopy

import pytest

from tests.readiness_fixture import load_readiness_fixture, load_archive_context

RELEASE = 'a' * 64


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def complete(tmp_path):
    rows = [{'cik': '0000000001', 'status': 'CLASSIFIED'}, {'cik': '0000000002', 'status': 'CLASSIFIED'}]
    raw = gzip.compress(('\n'.join(json.dumps(r) for r in rows) + '\n').encode(), mtime=0)
    (tmp_path / 'part-0000.jsonl.gz').write_bytes(raw)
    manifest = {'release_id': RELEASE, 'issuers': 2, 'parts': [{'part': 'part-0000.jsonl.gz', 'issuers': 2, 'bytes': len(raw), 'sha256': sha(raw)}]}
    def save(value):
        body = json.dumps(value).encode()
        (tmp_path / 'manifest.json').write_bytes(body)
        receipt = {'release_id': RELEASE, 'bucket':'financial-system-datasets', 'prefix':f'gold/sec-readiness/releases/{RELEASE}/', 'artifacts':[
            {'key':f'gold/sec-readiness/releases/{RELEASE}/manifest.json','bytes':len(body),'sha256':sha(body)},
            {'key':f'gold/sec-readiness/releases/{RELEASE}/part-0000.jsonl.gz','bytes':len(raw),'sha256':sha(raw)}]}
        (tmp_path / 'publication.json').write_text(json.dumps(receipt))
    save(manifest)
    return tmp_path, manifest, save


def test_loads_only_the_explicit_complete_release(complete, tmp_path):
    folder, manifest, _ = complete
    (tmp_path / 'newer').mkdir(); (tmp_path / 'newer/summary.json').write_text('{}')
    assert load_readiness_fixture(folder, RELEASE) == manifest


def test_missing_shard_is_incomplete_not_empty_data(complete):
    folder, _, _ = complete
    (folder / 'part-0000.jsonl.gz').unlink()
    with pytest.raises(ValueError, match='READINESS_FIXTURE_INCOMPLETE'):
        load_readiness_fixture(folder, RELEASE)


@pytest.mark.parametrize('change', ['hash', 'length', 'release', 'count', 'traversal', 'duplicate'])
def test_rejects_corrupt_or_wrong_release_fixture(complete, change):
    folder, original, save = complete
    manifest = deepcopy(original)
    if change == 'hash':
        (folder / 'part-0000.jsonl.gz').write_bytes(b'corrupt')
    elif change == 'length':
        manifest['parts'][0]['bytes'] += 1; save(manifest)
    elif change == 'release':
        manifest['release_id'] = 'b'*64; save(manifest)
    elif change == 'count':
        manifest['parts'][0]['issuers'] = 3; save(manifest)
    elif change == 'traversal':
        manifest['parts'][0]['part'] = '../part-0000.jsonl.gz'; save(manifest)
    else:
        manifest['parts'] *= 2; save(manifest)
    with pytest.raises(ValueError, match='READINESS_FIXTURE_INTEGRITY'):
        load_readiness_fixture(folder, RELEASE)


def test_archive_context_must_match_pinned_release_and_index(complete):
    folder, manifest, _ = complete
    engine = {'release_id':'e'*64,'date_to':'2026-09-24','source':{'bucket':'private','key':'archive.zip','bytes':123,'etag':'etag','declared_sha256':'d'*64}}
    index = {'0000000001':{'name':'one.json'}}
    manifest.update(engine_release_id='e'*64, archive=engine['source'],archive_cutoff='2026-09-24')
    engine_raw = json.dumps(engine).encode(); index_raw = json.dumps(index).encode()
    (folder/'engine-manifest.json').write_bytes(engine_raw); (folder/'member-index.json').write_bytes(index_raw)
    context={'engine_sha256':sha(engine_raw),'member_index_sha256':sha(index_raw)}
    (folder/'audit-context.json').write_text(json.dumps(context))
    assert load_archive_context(folder,manifest)==(engine,index)
    (folder/'member-index.json').write_text('{}')
    with pytest.raises(ValueError,match='READINESS_FIXTURE_INTEGRITY'):
        load_archive_context(folder,manifest)
