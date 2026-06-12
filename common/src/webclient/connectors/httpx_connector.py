from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

import httpx
from webclient.auth import DigestAuth
from webclient.types import ClientHttpConnector, ClientHttpRequest, ClientHttpResponse


class HttpxClientHttpResponse(ClientHttpResponse):
    def __init__(self, response: httpx.Response) -> None:
        self._response: httpx.Response = response

    async def read_body(self) -> bytes:
        return await self._response.aread()

    async def stream_lines(self) -> AsyncIterator[str]:
        async for line in self._response.aiter_lines():
            yield line

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    async def close(self) -> None:
        await self._response.aclose()


class HttpxClientHttpConnector(ClientHttpConnector):
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client: httpx.AsyncClient = client if client is not None else httpx.AsyncClient()
        self._external_client: bool = client is not None

    async def connect(
        self,
        request: ClientHttpRequest,
        *,
        stream: bool = False,
    ) -> ClientHttpResponse:
        httpx_auth: object | None = None

        if isinstance(request.auth, DigestAuth):
            httpx_auth = httpx.DigestAuth(username=request.auth.username, password=request.auth.password)
        elif request.auth is not None:
            httpx_auth = request.auth

        kwargs: dict[str, object] = {
            "params": request.params,
            "cookies": request.cookies,
            "auth": httpx_auth,
        }

        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        if request.json_body is not None:
            kwargs["json"] = request.json_body
        elif request.data is not None:
            kwargs["data"] = request.data
        elif request.content is not None:
            kwargs["content"] = request.content
        if request.files is not None:
            kwargs["files"] = cast(Mapping[str, object], request.files)

        httpx_req = self._client.build_request(
            method=request.method,
            url=request.url,
            headers=dict(request.headers),
            **cast(dict[str, Any], kwargs),
        )
        response = await self._client.send(httpx_req, stream=stream)
        return HttpxClientHttpResponse(response)

    async def close(self) -> None:
        if not self._external_client:
            await self._client.aclose()
