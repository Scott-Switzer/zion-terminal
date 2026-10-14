"""Bound decimal input lexically before runtime-specific Decimal construction."""

import re
from urllib.parse import parse_qs, urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_universe_screen import handle as screen


async def handle(env, request):
    try:
        authenticate(env, request)
    except PrivateFinancialsError as error:
        return {"error": {"code": error.code}}, error.status
    if request.method == "GET":
        q = parse_qs(urlparse(request.url).query, keep_blank_values=True)
        for name in ("minimum", "maximum"):
            if name not in q:
                continue
            if len(q[name]) != 1:
                return {"error": {"code": "INVALID_PARAMETER"}}, 400
            value = q[name][0]
            match = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d{1,3}))?", value)
            if (
                len(value) > 128
                or not match
                or (match.group(1) is not None and abs(int(match.group(1))) > 80)
            ):
                return {"error": {"code": "INVALID_SCREEN_THRESHOLD"}}, 422
    return await screen(env, request)
