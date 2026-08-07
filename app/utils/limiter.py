import types

from starlette.responses import Response

from slowapi import Limiter
from slowapi.util import get_remote_address


limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=True,
)

original_inject_headers = limiter._inject_headers


def patched_inject_headers(self, response, view_rate_limit):
    if isinstance(response, Response):
        return original_inject_headers(response, view_rate_limit)
    else:
        return response

limiter._inject_headers = types.MethodType(patched_inject_headers, limiter)
