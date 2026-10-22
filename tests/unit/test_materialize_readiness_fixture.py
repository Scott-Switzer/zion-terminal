import io
import json

import pytest

from tests.unit.test_readiness_fixture import complete, RELEASE, sha  # noqa: F401
from scripts.materialize_readiness_test_fixture import materialize
from tests.readiness_fixture import load_readiness_fixture


class Storage:
    def __init__(self, objects):
        self.objects, self.reads = objects, []
    def get_object(self, *, Bucket, Key):
        assert Bucket == 'financial-system-datasets'
        self.reads.append(Key)
        if Key not in self.objects:
            raise FileNotFoundError('missing source')
        return {'Body':io.BytesIO(self.objects[Key])}
    def put_object(self, **kwargs):
        pytest.fail('fixture retrieval must never write remote data')
    def list_objects_v2(self, **kwargs):
        pytest.fail('fixture retrieval must never list remote prefixes')


def source(complete):
    folder, _, _ = complete
    receipt = json.loads((folder/'publication.json').read_text())
    return folder, Storage({a['key']:(folder/a['key'].rsplit('/',1)[1]).read_bytes() for a in receipt['artifacts']})


def test_retrieves_only_pinned_artifacts_and_reuses_a_verified_cache(complete, tmp_path):
    folder, storage = source(complete)
    output = tmp_path/'cache'
    receipt = folder/'publication.json'
    result = materialize(receipt, output, storage, RELEASE, sha(receipt.read_bytes()))
    assert result['release_id'] == RELEASE
    assert load_readiness_fixture(output,RELEASE)['issuers'] == 2
    assert len(storage.reads) == 2
    storage.objects.clear()
    assert materialize(receipt,output,storage,RELEASE,sha(receipt.read_bytes()))['release_id'] == RELEASE
    assert len(storage.reads) == 2


@pytest.mark.parametrize('defect',['corrupt','missing','wrong_prefix','receipt_changed'])
def test_invalid_source_never_becomes_a_complete_fixture(complete,tmp_path,defect):
    folder, storage = source(complete)
    receipt_path=folder/'publication.json'; receipt=json.loads(receipt_path.read_text())
    expected=sha(receipt_path.read_bytes())
    key=f'gold/sec-readiness/releases/{RELEASE}/part-0000.jsonl.gz'
    if defect=='corrupt':storage.objects[key]=b'bad'
    elif defect=='missing':del storage.objects[key]
    elif defect=='wrong_prefix':
        receipt['artifacts'][1]['key']='raw/unrelated.parquet';receipt_path.write_text(json.dumps(receipt));expected=sha(receipt_path.read_bytes())
    else:receipt_path.write_text('{}')
    output=tmp_path/'cache'
    with pytest.raises((ValueError,FileNotFoundError)):
        materialize(receipt_path,output,storage,RELEASE,expected)
    assert not (output/'publication.json').exists()
