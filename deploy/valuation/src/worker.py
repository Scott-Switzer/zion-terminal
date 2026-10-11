"""Private service-binding entry point for Zion's compact valuation data."""
import json
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
from workers import WorkerEntrypoint, Response
from packet_service import PacketError, serve, read_json, POINTER


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        try:
            url = urlparse(request.url)
            if request.method != 'GET':
                return Response('Method not allowed', status=405)
            if url.path == '/healthz':
                return Response(json.dumps({'service': 'zion-valuation', 'status': 'ok', 'contract': 'ppe-valuation-packet-v1'}), headers={'Content-Type': 'application/json'})
            prefix = '/v1/valuation/company/'
            if not url.path.startswith(prefix):
                return Response('Not found', status=404)
            query = parse_qs(url.query, keep_blank_values=True)
            if set(query) - {'as_of'} or any(len(v) != 1 for v in query.values()):
                raise PacketError('Unsupported query parameters.', 400)
            asof = query.get('as_of', [datetime.now(timezone.utc).date().isoformat()])[0]
            packet, digest = await serve(self.env.VALUATION_DATA, url.path[len(prefix):], asof, self.cached_read)
            return Response(json.dumps(packet, separators=(',', ':'), allow_nan=False), headers={
                'Content-Type': 'application/json', 'ETag': '"' + digest + '"',
                'Cache-Control': 'private, max-age=60', 'X-Valuation-Transport': 'zion-service-binding'})
        except PacketError as exc:
            return Response(json.dumps({'error': str(exc)}), status=exc.status, headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
        except Exception:
            return Response('{"error":"Valuation storage temporarily unavailable."}', status=503, headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})

    async def cached_read(self, bucket, key, maximum, digest=None):
        import hashlib
        from js import caches, Request, Response as JSResponse, Object
        from pyodide.ffi import to_js
        cache_key = Request.new('https://zion-valuation.internal/cache/' + hashlib.sha256(key.encode()).hexdigest())
        try:
            cached = await caches.default.match(cache_key)
            if cached:
                raw = str(await cached.text())
                if len(raw.encode()) <= maximum and (not digest or hashlib.sha256(raw.encode()).hexdigest() == digest):
                    return json.loads(raw)
        except Exception:
            pass
        async def store(raw):
            try:
                ttl = 60 if key == POINTER else 31536000
                response = JSResponse.new(raw, to_js({'headers': {'Cache-Control': f'public, max-age={ttl}'}}, dict_converter=Object.fromEntries))
                await caches.default.put(cache_key, response)
            except Exception:
                pass
        return await read_json(bucket, key, maximum, digest, on_read=store)
