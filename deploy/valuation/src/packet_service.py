"""Compact public-SEC packet contract. No general R2 or licensed-data API."""
import hashlib
import json
import re
from datetime import date, datetime, timezone

POINTER = 'control/valuation/CURRENT.json'


class PacketError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


async def read_json(bucket, key, maximum, digest=None, on_read=None):
    obj = await bucket.get(key)
    if obj is None:
        return None
    if obj.size > maximum:
        raise PacketError('Published packet exceeds its size budget.')
    raw = await obj.text()
    if len(raw.encode()) > maximum:
        raise PacketError('Published packet exceeds its size budget.')
    if digest and hashlib.sha256(raw.encode()).hexdigest() != digest:
        raise PacketError('Published packet integrity check failed.')
    try:
        value = json.loads(raw)
    except ValueError:
        raise PacketError('Published packet is invalid JSON.') from None
    if on_read is not None:
        await on_read(raw)
    return value


async def serve(bucket, ticker, asof, read=read_json):
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}', ticker):
        raise PacketError('Invalid ticker.', 400)
    try:
        cutoff = date.fromisoformat(asof)
        if cutoff.isoformat() != asof or cutoff > datetime.now(timezone.utc).date():
            raise ValueError()
    except ValueError:
        raise PacketError('Invalid valuation date.', 400) from None
    pointer = await read(bucket, POINTER, 524288)
    if not isinstance(pointer, dict) or pointer.get('schema_version') != 'ppe-valuation-index-v1' or not isinstance(pointer.get('companies'), dict):
        raise PacketError('Valuation publication unavailable.')
    entry = pointer['companies'].get(ticker)
    if entry is None:
        raise PacketError('No published valuation packet for this ticker.', 404)
    if not isinstance(entry, dict):
        raise PacketError('Invalid packet reference.')
    key, digest = entry.get('key'), entry.get('sha256')
    if not isinstance(key, str) or not isinstance(digest, str) or not re.fullmatch(
            r'gold/valuation/releases/[a-f0-9]{64}/companies/' + re.escape(ticker) + r'\.json', key
            ) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise PacketError('Invalid packet reference.')
    packet = await read(bucket, key, 131072, digest)
    if not isinstance(packet, dict) or packet.get('schema_version') != 'ppe-valuation-packet-v1' or packet.get('ticker') != ticker:
        raise PacketError('Published packet identity mismatch.')
    try:
        selected = date.fromisoformat(packet['cutoff_date'])
        if selected > cutoff:
            raise PacketError('Latest packet cannot establish this historical cutoff.', 422)
    except (KeyError, TypeError, ValueError):
        raise PacketError('Invalid published cutoff.') from None
    # The consumer additionally validates every field's units, dates and method semantics.
    return packet, digest
