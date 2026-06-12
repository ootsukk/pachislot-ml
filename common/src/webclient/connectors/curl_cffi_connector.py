from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from webclient.types import ClientHttpConnector, ClientHttpRequest, ClientHttpResponse

_curl_cffi_import_error: ImportError | None = None
if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession, Response
else:
    try:
        from curl_cffi.requests import AsyncSession, Response
    except ImportError as _err:
        _curl_cffi_import_error = _err
        AsyncSession = None  # type: ignore
        Response = None      # type: ignore


@dataclass(frozen=True)
class CurlCffiConfig:
    impersonate: str | None = "chrome"
    max_clients: int = 10
    verify: bool = True
    trust_env: bool = True
    timeout: float | None = None


class CurlCffiClientHttpResponse(ClientHttpResponse):

    def __init__(self, response: Response) -> None:
        self._response: Response = response

    async def read_body(self) -> bytes:
        if hasattr(self._response, "content") and self._response.content:
            return self._response.content

        body_chunks: list[bytes] = []
        if hasattr(self._response, "iter_content"):
            async for chunk in self._response.aiter_content():
                body_chunks.append(chunk)
            return b"".join(body_chunks)

        return getattr(self._response, "content", b"")

    async def stream_lines(self) -> AsyncIterator[str]:
        async for raw_line in self._response.aiter_lines():
            if isinstance(raw_line, bytes):
                yield raw_line.decode("utf-8", errors="replace")
            elif isinstance(raw_line, str):
                yield raw_line

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return cast(Mapping[str, str], self._response.headers)

    async def close(self) -> None:
        if hasattr(self._response, "close"):
            self._response.close()


class CurlCffiClientHttpConnector(ClientHttpConnector):

    def __init__(
        self,
        session: AsyncSession | None = None,
        config: CurlCffiConfig | None = None,
        /
    ) -> None:
        if _curl_cffi_import_error is not None or AsyncSession is None:
            raise RuntimeError(
                "curl_cffi コネクターを使用するには 'curl_cffi' パッケージが必要です。 "
                "pip install curl_cffi を実行してください。"
            ) from _curl_cffi_import_error

        self._external_session: bool = session is not None

        if session is not None:
            self._session = session
        else:
            cffi_config = config if config is not None else CurlCffiConfig()

            session_kwargs: dict[str, Any] = {
                "max_clients": cffi_config.max_clients,
                "verify": cffi_config.verify,
                "trust_env": cffi_config.trust_env,
            }
            if cffi_config.timeout is not None:
                session_kwargs["timeout"] = cffi_config.timeout

            self._session = AsyncSession(**session_kwargs)
            self._default_impersonate: str | None = cffi_config.impersonate

    async def connect(
        self,
        request: ClientHttpRequest,
        *,
        stream: bool = False,
    ) -> ClientHttpResponse:
        cffi_auth: Any = None
        if request.auth is not None:
            if isinstance(request.auth, tuple):
                cffi_auth = request.auth
            elif hasattr(request.auth, "username") and hasattr(request.auth, "password"):
                auth_any = cast(Any, request.auth)
                cffi_auth = (auth_any.username, auth_any.password)
            else:
                cffi_auth = request.auth

        kwargs: dict[str, Any] = {
            "params": request.params,
            "cookies": request.cookies,
            "auth": cffi_auth,
            "stream": stream,
        }

        impersonate_target = request.attributes.get(
            "impersonate",
            getattr(self, "_default_impersonate", "chrome")
        )
        if impersonate_target:
            kwargs["impersonate"] = impersonate_target

        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        if request.json_body is not None:
            kwargs["json"] = request.json_body
        elif request.data is not None:
            kwargs["data"] = request.data
        elif request.content is not None:
            kwargs["data"] = request.content
        if request.files is not None:
            kwargs["files"] = request.files

        response = await self._session.request(
            method=request.method.value,
            url=request.url,
            headers=dict(request.headers),
            **kwargs,
        )
        return CurlCffiClientHttpResponse(response)

    async def close(self) -> None:
        if not self._external_session:
            await self._session.close()
