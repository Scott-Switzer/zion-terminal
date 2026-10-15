"""Seed the local R2 simulator with the published objects a local dev run reads.

`thin/wrangler.dev.jsonc` binds the thin Worker's ``MARKET_DATA`` to local storage, while
``SEC_CORPUS`` and ``SEC_ARCHIVE`` stay on their real, read-only buckets. The reason is the
derived query cache: ``sec_engine_api`` authenticates every cached envelope with the
deployment's private credential, so a local run with a local credential cannot read what a
deployment wrote, and it must never write its own envelope into a bucket deployments read.
Local development therefore reads a *copy* of the immutable published releases instead.

Every object copied here is verified the same way the Worker verifies it at request time --
against the digest the pinned manifest names -- and nothing is written to the remote bucket.

    python3 scripts/seed_dev_market_data.py --ciks 0000320193,0000789019,0001652044 \
        --persist-to /tmp/zion-dev-r2

Then start the thin Worker against the same state directory:

    cd deploy/cloudflare-query
    wrangler dev -c thin/wrangler.dev.jsonc --port 8790 --persist-to /tmp/zion-dev-r2
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUERY_DIR = REPO / 'deploy/cloudflare-query'
DEV_CONFIG = QUERY_DIR / 'thin/wrangler.dev.jsonc'
BUCKET = 'financial-system-datasets'


def wrangler(*args):
    result = subprocess.run(['npx', 'wrangler', *args], cwd=QUERY_DIR,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'wrangler {" ".join(args)} failed:\n{result.stderr[-2000:]}')
    return result.stdout + result.stderr


def pins():
    """The two pinned release pointers, read from the dev harness that uses them."""
    text = DEV_CONFIG.read_text()
    values = {}
    for name in ('SEC_ENGINE_MANIFEST_KEY', 'SEC_ENGINE_MANIFEST_SHA256',
                 'SEC_COMPANYFACTS_MANIFEST_KEY', 'SEC_COMPANYFACTS_MANIFEST_SHA256'):
        match = re.search(r'"%s"\s*:\s*"([^"]+)"' % name, text)
        if not match:
            raise SystemExit(f'{DEV_CONFIG} does not pin {name}')
        values[name] = match.group(1)
    return values


def fetch(key, scratch, *, remote=True, persist_to=None):
    """Download one object from the remote bucket, or read back the local copy."""
    target = scratch / hashlib.sha256(key.encode()).hexdigest()
    if remote:
        flags = ['--remote']
    else:
        flags = ['--local', '--persist-to', str(persist_to)]
    wrangler('r2', 'object', 'get', f'{BUCKET}/{key}', *flags, '--file', str(target))
    return target.read_bytes()


def store(key, raw, persist_to):
    """Write one object into the local simulator under its published key."""
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(raw)
        staged = Path(handle.name)
    wrangler('r2', 'object', 'put', f'{BUCKET}/{key}', '--local',
             '--persist-to', str(persist_to), '--content-type', 'application/json',
             '--file', str(staged))
    staged.unlink()
    # Read the local copy back so the receipt proves what the Worker will actually see.
    return fetch(key, Path(persist_to) / 'readback', remote=False, persist_to=persist_to)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def reference(node, what):
    require(isinstance(node, dict) and isinstance(node.get('key'), str)
            and isinstance(node.get('sha256'), str),
            f'{what} does not name a pinned object: {json.dumps(node)[:200]}')
    return node


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ciks', required=True,
                        help='comma-separated ten-digit CIKs to seed')
    parser.add_argument('--persist-to', required=True,
                        help='the local state directory the dev server is started with')
    parser.add_argument('--receipt', type=Path,
                        help='where to write the sanitised seed receipt')
    args = parser.parse_args()
    ciks = [cik.strip() for cik in args.ciks.split(',') if cik.strip()]
    for cik in ciks:
        require(re.fullmatch(r'\d{10}', cik), f'{cik} is not a ten-digit CIK')

    config = pins()
    persist_to = Path(args.persist_to).expanduser().resolve()
    persist_to.mkdir(parents=True, exist_ok=True)
    (persist_to / 'readback').mkdir(exist_ok=True)
    receipt = []

    def copy(key, sha256, what):
        raw = fetch(key, scratch)
        digest = hashlib.sha256(raw).hexdigest()
        require(digest == sha256,
                f'{what} at {key} hashes to {digest}, not the pinned {sha256}')
        stored = store(key, raw, persist_to)
        require(hashlib.sha256(stored).hexdigest() == sha256,
                f'{what} did not land intact in the local store at {key}')
        receipt.append(dict(object=key, sha256=digest, bytes=len(raw), role=what))

    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)

        engine_key = config['SEC_ENGINE_MANIFEST_KEY']
        engine_sha = config['SEC_ENGINE_MANIFEST_SHA256']
        copy(engine_key, engine_sha, 'engine_manifest')
        engine = json.loads(fetch(engine_key, scratch))

        catalogs = {}
        for cik in ciks:
            shard = reference(engine['shards'].get(cik[-2:]),
                              f'engine shard for {cik}')
            if shard['key'] not in catalogs:
                copy(shard['key'], shard['sha256'], f'engine_catalog_{cik[-2:]}')
                catalogs[shard['key']] = json.loads(fetch(shard['key'], scratch))
            entry = catalogs[shard['key']].get(cik)
            require(isinstance(entry, dict), f'{cik} is not in {shard["key"]}')
            materialized = reference(entry.get('materialized'),
                                     f'engine company payload for {cik}')
            copy(materialized['key'], materialized['sha256'],
                 f'engine_companies_{cik}')

        facts_key = config['SEC_COMPANYFACTS_MANIFEST_KEY']
        facts_sha = config['SEC_COMPANYFACTS_MANIFEST_SHA256']
        copy(facts_key, facts_sha, 'companyfacts_manifest')
        facts = json.loads(fetch(facts_key, scratch))
        for cik in ciks:
            member = reference(facts.get('members', {}).get(cik),
                               f'company facts member for {cik}')
            require(member['sha256'] == member.get('engine_member_sha256'),
                    f'{cik} is not a dual-release member: the companion release and the '
                    'engine release do not name the same bytes')
            copy(member['key'], member['sha256'], f'companyfacts_companies_{cik}')

    summary = dict(schema_version='zion-dev-market-data-seed-v1',
                   bucket=BUCKET, local_only=True, persist_to=str(persist_to),
                   ciks=ciks, objects=receipt,
                   total_bytes=sum(item['bytes'] for item in receipt))
    print(json.dumps(summary, indent=1, sort_keys=True))
    if args.receipt:
        args.receipt.write_text(json.dumps(summary, indent=1, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
