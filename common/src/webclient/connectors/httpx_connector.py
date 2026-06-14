from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from webclient.base import (
    ClientHttpConnector,
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    ConnectorConfig,
    ProxyOptions,
    RedirectOptions,
)
from webclient.utility import Named


@Named("httpx")
@dataclass(frozen=True)
class HttpxConnectorOptions(ConnectorConfig):
    """標準の HTTPX 非同期コネクター用設定オプション"""

    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    verify: bool | str = True
    trust_env: bool = True
    http1: bool = True
    http2: bool = False


class HttpxClientHttpResponse:
    """HTTPX の Response オブジェクトをコアの ClientHttpResponse 契約へ変換するラッパー"""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    async def read_body(self) -> bytes:
        return await self._response.aread()

    async def stream_lines(self) -> AsyncIterator[str]:
        async for line in self._response.aiter_lines():
            yield line

    def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]:
        return self._response.aiter_bytes(chunk_size=chunk_size)

    async def close(self) -> None:
        await self._response.aclose()


class HttpxClientHttpConnector(ClientHttpConnector, Configurable[HttpxConnectorOptions]):
    """
    HTTPX 駆動の非同期通信具象コネクター。
    コアのコンポーネントスキャンによって全自動で検知・マウントされます。
    """

    def __init__(
        self,
        config: HttpxConnectorOptions | None = None,
        proxy_options: ProxyOptions | None = None,
        redirect_options: RedirectOptions | None = None,
    ) -> None:

        self.config = config if config is not None else HttpxConnectorOptions()
        self.proxy_options = proxy_options if proxy_options is not None else ProxyOptions()
        self.redirect_options = redirect_options if redirect_options is not None else RedirectOptions()

        # 直接通信とプロキシ通信の双方で共有するトランスポートのベース設定を束ねる
        transport_kwargs = {
            "verify": self.config.verify,
            "http1": self.config.http1,
            "http2": self.config.http2,
            "limits": httpx.Limits(
                max_connections=self.config.max_connections,
                max_keepalive_connections=self.config.max_keepalive_connections,
                keepalive_expiry=self.config.keepalive_expiry,
            ),
        }

        # プロキシを通さない「直接通信用」のトランスポートを生成
        direct_transport = httpx.AsyncHTTPTransport(**transport_kwargs)

        # HTTPX固有のURLパターンルーティングマップの初期化
        httpx_mounts: dict[str, httpx.AsyncHTTPTransport] = {}

        if self.proxy_options:
            # ヘルパー関数: ユーザー名とパスワードをURLに結合する
            def _build_proxy_url(base_url: str) -> str:
                if not self.proxy_options or not self.proxy_options.username:
                    return base_url
                parsed = urlparse(base_url)
                auth_str = f"{self.proxy_options.username}:{self.proxy_options.password or ''}"
                netloc = f"{auth_str}@{parsed.netloc}"
                return urlunparse(parsed._replace(netloc=netloc))

            # プロキシ用のトランスポートを設定してマウント
            if self.proxy_options.https_url:
                proxy_url = _build_proxy_url(self.proxy_options.https_url)
                httpx_mounts["https://"] = httpx.AsyncHTTPTransport(proxy=proxy_url, **transport_kwargs)

            if self.proxy_options.http_url:
                proxy_url = _build_proxy_url(self.proxy_options.http_url)
                httpx_mounts["http://"] = httpx.AsyncHTTPTransport(proxy=proxy_url, **transport_kwargs)

            # no_proxy（プロキシ除外）ホストの自動パースと直接通信への上書きマウント
            if self.proxy_options.no_proxy:
                bypass_hosts = [
                    host.strip() for host in self.proxy_options.no_proxy.replace(";", ",").split(",") if host.strip()
                ]

                for host in bypass_hosts:
                    clean_host = host.lstrip("*").lstrip(".")
                    # HTTPXのURLパターンマウント仕様に従い、"all://ホスト" に対して
                    # プロキシ設定のない「direct_transport」をマウントすることで、
                    # 該当ホストのみプロキシを美しく透過バイパスさせます。
                    httpx_mounts[f"all://{clean_host}"] = direct_transport

        # 最終的なクライアントの初期化
        # メイントランスポートとして direct_transport を指定し、
        # プロキシやバイパスのルーティングルールを mounts 引数へ完全委譲します。
        self._client = httpx.AsyncClient(
            transport=direct_transport,
            trust_env=self.config.trust_env,
            mounts=httpx_mounts if httpx_mounts else None,
            follow_redirects=self.redirect_options.follow_redirects
        )
        # HTTPXの仕様に従い、最大リダイレクト回数はインスタンス生成後に内部値を上書き調律
        self._client.max_redirects = self.redirect_options.max_redirects


    async def connect(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        """共通リクエストモデル（ClientHttpRequest）を HTTPX 固有の要求へ翻訳して送信します"""

        timeout = request.timeout if request.timeout is not None else httpx.USE_CLIENT_DEFAULT

        kwargs: dict[str, Any] = {
            "method": request.method,
            "url": request.url,
            "headers": request.headers,
            "params": request.params,
            "cookies": request.cookies,
            "timeout": timeout,
        }

        if request.json_body is not None:
            kwargs["json"] = request.json_body
        elif request.data is not None:
            kwargs["data"] = request.data
        elif request.content is not None:
            kwargs["content"] = request.content

        if request.multipart_body:
            httpx_data: dict[str, Any] = {}
            httpx_files: dict[str, Any] = {}

            for part in request.multipart_body:
                # ファイル名がなく、かつ個別のメディアタイプ（Content-Type）も指定されていない純粋なテキストパート
                if part.filename is None and part.content_type is None and not part.headers:
                    # HTTPXの標準dataフォームフィールドとして積載
                    httpx_data[part.name] = part.value
                else:
                    # 個別ヘッダーの辞書組み立て（Content-Typeの自動マージ）
                    part_headers = dict(part.headers)
                    if part.content_type and "Content-Type" not in part_headers:
                        part_headers["Content-Type"] = part.content_type

                    # HTTPXが要求する高度なファイルマルチパート用の4要素タプル構造へ翻訳
                    # 構造: (ファイル名, コンテンツ, Content-Type, 個別ヘッダー)
                    httpx_files[part.name] = (
                        part.filename,  # Noneであっても透過バインド可能
                        part.value,
                        part.content_type,
                        part_headers if part_headers else None
                    )

            if httpx_data:
                kwargs["data"] = httpx_data
            if httpx_files:
                kwargs["files"] = httpx_files

        httpx_req = self._client.build_request(**kwargs)

        httpx_res = await self._client.send(httpx_req, stream=stream)

        return HttpxClientHttpResponse(httpx_res)

    async def close(self) -> None:
        await self._client.aclose()
