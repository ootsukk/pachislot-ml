from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from webclient.base import CookieStore


class MemoryCookieStore(CookieStore):
    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def save(self, url: str, cookies: Mapping[str, str], /) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return
        if host not in self._store:
            self._store[host] = {}
        self._store[host].update(cookies)

    def load(self, url: str, /) -> Mapping[str, str]:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return {}
        return self._store.get(host, {})
