"""Shared HTTP helpers for source plugins."""

import requests

from config import HTTP_TIMEOUT, USER_AGENT, proxies, tls_verify

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

# Route every source lookup through the corporate proxy and custom CA bundle
# when configured (see config.py). Setting these on the session makes the
# behaviour explicit and lets the RELEASERADAR_-prefixed overrides apply, rather
# than relying solely on requests' implicit handling of the proxy env vars.
_proxies = proxies()
if _proxies:
    _session.proxies.update(_proxies)
_session.verify = tls_verify()


def get(url, **kwargs):
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    return _session.get(url, **kwargs)
