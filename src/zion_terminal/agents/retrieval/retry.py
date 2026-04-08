"""Shared retry configuration for all data source adapters.

Catches a broader set of transient errors than the previous per-adapter
definitions, including HTTP errors from underlying libraries.
"""

from __future__ import annotations

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Broad set of transient exceptions that warrant a retry.
# Each adapter previously only caught ConnectionError and TimeoutError.
_RETRIABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,            # covers network-level errors (socket errors, etc.)
)

# Try to include requests.exceptions if available (yfinance uses requests)
try:
    from requests.exceptions import (
        ConnectionError as ReqConnectionError,
        HTTPError,
        ReadTimeout,
        Timeout,
    )
    _RETRIABLE_EXCEPTIONS = (
        *_RETRIABLE_EXCEPTIONS,
        ReqConnectionError,
        HTTPError,
        ReadTimeout,
        Timeout,
    )
except ImportError:
    pass

# Try to include urllib3 exceptions (edgartools/httpx may raise these)
try:
    from urllib3.exceptions import (
        MaxRetryError,
        NewConnectionError,
        ProtocolError,
    )
    _RETRIABLE_EXCEPTIONS = (
        *_RETRIABLE_EXCEPTIONS,
        MaxRetryError,
        NewConnectionError,
        ProtocolError,
    )
except ImportError:
    pass


adapter_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=15),
    retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
    reraise=True,
)
"""Standard retry decorator for adapter fetch methods."""
