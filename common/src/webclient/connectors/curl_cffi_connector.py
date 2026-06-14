from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse, urlunparse

from webclient.types import CHARSET_UTF8

if TYPE_CHECKING:
    from curl_cffi import requests

try:
    import curl_cffi  # noqa: F401
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False


from webclient.base import (
    ClientHttpConnector,
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    ConnectorConfig,
    ProxyOptions,
)
from webclient.utility import Named


@Named("curl_cffi")
@dataclass(frozen=True)
class CurlCffiConnectorOptions(ConnectorConfig):
    """
    curl_cffi 駆動の高速・TLS指紋偽装コネクター用設定オプション。
    YAMLの `connector: curl_cffi` セクションと全自動マッピングされます。
    """

    impersonate: str | None = "chrome110"
    verify: bool = True
    timeout: float = 30.0


# =====================================================================
# 📌 2. レスポンス・ブリッジ実装（ClientHttpResponse Protocol 準拠）
# =====================================================================
class CurlCffiClientHttpResponse:
    """curl_cffi の Response オブジェクトをコアの ClientHttpResponse 契約へ変換するラッパー"""

    def __init__(self, response: requests.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return cast(Mapping[str, str], self._response.headers)

    async def read_body(self) -> bytes:
        return self._response.content

    async def stream_lines(self) -> AsyncIterator[str]:
        async for line in cast(AsyncIterator[bytes], self._response.iter_lines()):
            yield line.decode(CHARSET_UTF8, errors="replace")

    async def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]:
        async for chunk in cast(AsyncIterator[bytes], self._response.iter_content(chunk_size)):
            yield chunk

    async def close(self) -> None:
        pass


class CurlCffiClientHttpConnector(ClientHttpConnector, Configurable[CurlCffiConnectorOptions]):
    """
    curl_cffi (libcurl wrapper) 駆動の非同期通信具象コネクター。
    コアのコンポーネントスキャンによって全自動で検知され、DIレジストリへマウントされます。
    """

    def __init__(
        self,
        config: CurlCffiConnectorOptions | None = None,
        proxy_options: ProxyOptions | None = None,
    ) -> None:
        if not _HAS_CURL_CFFI:
            raise ImportError(
                "curl_cffi コネクターを使用するには 'curl_cffi' パッケージのインストールが必要です。\n"
                "コマンド: pip install curl-cffi"
            )

        from curl_cffi import requests

        self.config = config if config is not None else CurlCffiConnectorOptions()
        self.proxy_options = proxy_options

        curl_proxies: dict[str, str] = {}

        if self.proxy_options:
            def _build_proxy_url(base_url: str) -> str:
                if not self.proxy_options or not self.proxy_options.username:
                    return base_url
                parsed = urlparse(base_url)
                auth_str = f"{self.proxy_options.username}:{self.proxy_options.password or ''}"
                netloc = f"{auth_str}@{parsed.netloc}"
                return urlunparse(parsed._replace(netloc=netloc))

            if self.proxy_options.http_url:
                curl_proxies["http"] = _build_proxy_url(self.proxy_options.http_url)
            if self.proxy_options.https_url:
                curl_proxies["https"] = _build_proxy_url(self.proxy_options.https_url)
            if self.proxy_options.no_proxy:
                # libcurl の NOPROXY 判定規則（CURLOPT_NOPROXY）はカンマ区切り文字列です。
                # ユーザーが指定した除外設定をそのまま流し込むだけで、libcurl カーネルが
                # サブドメインのワイルドカード等も含めて超高速に自動バイパス処理を行います。
                curl_proxies["no_proxy"] = self.proxy_options.no_proxy

        # libcurl ベースの高度な非同期セッションをプール初期化
        self._session = requests.AsyncSession(
            verify=self.config.verify,
            impersonate=cast(Any, self.config.impersonate),
            proxies=cast(Any, curl_proxies if curl_proxies else None),
        )

    async def connect(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        """共通リクエストモデル（ClientHttpRequest）を curl_cffi 固有の引数へ翻訳して送信します"""

        timeout = request.timeout if request.timeout is not None else self.config.timeout

        kwargs: dict[str, Any] = {
            "method": request.method,
            "url": request.url,
            "headers": dict(request.headers) if request.headers else None,
            "params": request.params,
            "cookies": request.cookies,
            "timeout": timeout,
            "stream": stream,
        }

        if request.json_body is not None:
            kwargs["json"] = request.json_body
        elif request.data is not None:
            kwargs["data"] = request.data
        elif request.content is not None:
            kwargs["data"] = request.content

        if request.files is not None:
            kwargs["files"] = request.files

        curl_res = await self._session.request(**kwargs)
        return CurlCffiClientHttpResponse(curl_res)

    async def close(self) -> None:
        await self._session.close()
