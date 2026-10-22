"""Retrieve existing immutable readiness audit artifacts. No acquisition or publishing."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.readiness_fixture import (  # noqa: E402
    MAX_METADATA_BYTES, MAX_PART_BYTES, digest, fail, load_archive_context,
    load_readiness_fixture, manifest_parts, read_file, validate_receipt,
)


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        fail()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.fixture-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize(receipt_path: Path, output: Path, storage, expected_release_id: str, receipt_sha256: str) -> dict:
    receipt_raw = read_file(Path(receipt_path), MAX_METADATA_BYTES)
    if digest(receipt_raw) != receipt_sha256:
        fail()
    receipt = json.loads(receipt_raw)
    references = validate_receipt(receipt, expected_release_id)
    output = Path(output)
    if output.is_symlink():
        fail()
    output.mkdir(parents=True, exist_ok=True)

    def obtain(name):
        reference = references[name]
        path = output / name
        if path.is_file() and not path.is_symlink() and path.stat().st_size == reference['bytes']:
            raw = read_file(path, reference['bytes'])
            if digest(raw) == reference['sha256']:
                return raw
        try:
            response = storage.get_object(Bucket=receipt['bucket'], Key=reference['key'])
            body = response['Body']
            try:
                raw = body.read(reference['bytes'] + 1)
            finally:
                body.close()
        except Exception:
            raise ValueError('READINESS_FIXTURE_FETCH_FAILED') from None
        if len(raw) != reference['bytes'] or digest(raw) != reference['sha256']:
            fail()
        atomic_write(path, raw)
        return raw

    manifest = json.loads(obtain('manifest.json'))
    parts = manifest_parts(manifest, expected_release_id, references)
    for part in parts:
        obtain(part['part'])
    if 'summary.json' in references:
        obtain('summary.json')
    atomic_write(output / 'publication.json', receipt_raw)
    try:
        return load_readiness_fixture(output, expected_release_id)
    except Exception:
        (output / 'publication.json').unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('receipt', 'release-id', 'receipt-sha256', 'output', 'engine-manifest', 'member-index', 'member-index-sha256'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        from reconcile_financial_inventory import client
        manifest = materialize(Path(args.receipt), Path(args.output), client(), args.release_id, args.receipt_sha256)
        engine_raw = read_file(Path(args.engine_manifest), MAX_PART_BYTES)
        index_raw = read_file(Path(args.member_index), MAX_PART_BYTES)
        if digest(index_raw) != args.member_index_sha256:
            fail()
        output = Path(args.output)
        atomic_write(output / 'engine-manifest.json', engine_raw)
        atomic_write(output / 'member-index.json', index_raw)
        context = {'engine_sha256': digest(engine_raw), 'member_index_sha256': digest(index_raw)}
        atomic_write(output / 'audit-context.json', json.dumps(context, sort_keys=True).encode())
        load_archive_context(output, manifest)
        print(json.dumps({'release_id': manifest['release_id'], 'manifest_sha256': digest((output/'manifest.json').read_bytes()),
                          'issuers': manifest['issuers'], 'verified_parts': len(manifest['parts']), **context}))
    except Exception as error:
        code = str(error)
        parser.exit(1, (code if code.startswith('READINESS_FIXTURE_') else 'READINESS_FIXTURE_SETUP_FAILED') + '\n')


if __name__ == '__main__':
    main()
