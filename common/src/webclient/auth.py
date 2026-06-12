from __future__ import annotations

from typing import TYPE_CHECKING

from webclient.types import ClientHttpAuth

if TYPE_CHECKING:
    from webclient.types import ClientHttpRequest


class BearerAuth(ClientHttpAuth):

    def __init__(self, token: str, /) -> None:
        self._token: str = token

    def apply(self, request: ClientHttpRequest, /) -> ClientHttpRequest:
        updated_headers = dict(request.headers)
        updated_headers["Authorization"] = f"Bearer {self._token}"
        from dataclasses import replace
        return replace(request, headers=updated_headers)


class DigestAuth(ClientHttpAuth):

    def __init__(self, username: str, password: str, /) -> None:
        self.username: str = username
        self.password: str = password

    def apply(self, request: ClientHttpRequest, /) -> ClientHttpRequest:
        return request
