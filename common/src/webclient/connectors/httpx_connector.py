from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse, urlunparse

import httpx
from webclient.base import (
    ClientHttpConnector,
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    ProxyOptions,
    RedirectOptions,
)
from webclient.plugin import dependency_module, plugin_impl


@dataclass(frozen=True)
class HttpxConnectorOptions:
    """標準の HTTPX 非同期コネクター用設定オプション"""

    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    verify: bool | str = True
    trust_env: bool = True
    http1: bool = True
    http2: bool = False

@dependency_module("httpx", ">=0.24.0")
@plugin_impl(value="httpx", priority=100)
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

        transport_kwargs = self._build_transport_kwargs()
        direct_transport = httpx.AsyncHTTPTransport(**transport_kwargs)
        httpx_mounts = self._build_mount_routing_map(transport_kwargs, direct_transport)

        # クライアントの初期化
        self._client = httpx.AsyncClient(
            transport=direct_transport,
            trust_env=self.config.trust_env,
            mounts=httpx_mounts if httpx_mounts else None,
            follow_redirects=self.redirect_options.follow_redirects
        )
        self._client.max_redirects = self.redirect_options.max_redirects
        # HTTPXの仕様に従い、最大リダイレクト回数はインスタンス生成後に内部値を上書き調律
        self._client.max_redirects = self.redirect_options.max_redirects


    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        """共通リクエストモデル(ClientHttpRequest)を HTTPX 固有の要求へ翻訳して送信します"""

        kwargs = self._build_request_kwargs(request)

        # マルチパートボディ要件がある場合は、関数化されたビルダーで安全にインポーズ
        if request.multipart_body:
            self._inject_multipart_payload(request.multipart_body, kwargs)

        httpx_req = self._client.build_request(**kwargs)
        httpx_res = await self._client.send(httpx_req, stream=stream)

        return HttpxClientHttpResponse(httpx_res)

    async def close(self) -> None:
        await self._client.aclose()


    def _build_transport_kwargs(self) -> dict[str, Any]:
        """HTTPXの基本接続制限およびトランスポートパラメータを美しく隔離生成します"""
        return {
            "verify": self.config.verify,
            "http1": self.config.http1,
            "http2": self.config.http2,
            "limits": httpx.Limits(
                max_connections=self.config.max_connections,
                max_keepalive_connections=self.config.max_keepalive_connections,
                keepalive_expiry=self.config.keepalive_expiry,
            ),
        }

    def _build_mount_routing_map(
        self, transport_kwargs: dict[str, Any], direct_transport: httpx.AsyncHTTPTransport
    ) -> dict[str, httpx.AsyncHTTPTransport]:
        """プロキシ、バイパス(no_proxy)を含む高度な URL ルーティングマップを構築します"""
        httpx_mounts: dict[str, httpx.AsyncHTTPTransport] = {}
        if not self.proxy_options:
            return httpx_mounts

        # HTTP / HTTPS プロキシトランスポートの設定
        if self.proxy_options.https_url:
            proxy_url = self._build_proxy_authenticated_url(self.proxy_options.https_url)
            httpx_mounts["https://"] = httpx.AsyncHTTPTransport(proxy=proxy_url, **transport_kwargs)

        if self.proxy_options.http_url:
            proxy_url = self._build_proxy_authenticated_url(self.proxy_options.http_url)
            httpx_mounts["http://"] = httpx.AsyncHTTPTransport(proxy=proxy_url, **transport_kwargs)

        # no_proxy を検知した場合、直接通信用トランスポートを部分上書きマウントして透過バイパス
        if self.proxy_options.no_proxy:
            self._inject_no_proxy_bypass_rules(httpx_mounts, direct_transport)

        return httpx_mounts

    def _build_proxy_authenticated_url(self, base_url: str) -> str:
        """プロキシURLに対し、ユーザー認証資格情報を安全に埋め込みインポーズします"""
        if not self.proxy_options or not self.proxy_options.username:
            return base_url
        parsed = urlparse(base_url)
        auth_str = f"{self.proxy_options.username}:{self.proxy_options.password or ''}"
        return urlunparse(parsed._replace(netloc=f"{auth_str}@{parsed.netloc}"))

    def _inject_no_proxy_bypass_rules(
        self, mounts_map: dict[str, httpx.AsyncHTTPTransport], direct_transport: httpx.AsyncHTTPTransport
    ) -> None:
        """セミコロン/カンマ区切りの文字列をパースし、HTTPXのall://マウント規約へ移送マウントします"""
        if not self.proxy_options or not self.proxy_options.no_proxy:
            return

        bypass_hosts = [
            host.strip() for host in self.proxy_options.no_proxy.replace(";", ",").split(",") if host.strip()
        ]
        for host in bypass_hosts:
            clean_host = host.lstrip("*").lstrip(".")
            mounts_map[f"all://{clean_host}"] = direct_transport


    def _build_request_kwargs(self, request: ClientHttpRequest) -> dict[str, Any]:
        """ClientHttpRequest から HTTPX 標準リクエスト引数へのプレーンマッピングを執行します"""
        timeout = request.timeout if request.timeout is not None else httpx.USE_CLIENT_DEFAULT

        kwargs: dict[str, Any] = {
            "method": request.method,
            "url": request.url,
            "headers": request.headers,
            "params": request.params,
            "cookies": request.cookies,
            "timeout": timeout,
        }

        # 排他排積載ボディのスマートマッピング
        if request.json_body is not None:
            kwargs["json"] = request.json_body
        elif request.data is not None:
            kwargs["data"] = request.data
        elif request.content is not None:
            kwargs["content"] = request.content

        return kwargs

    def _inject_multipart_payload(self, multipart_body: Sequence[Any], kwargs_ref: dict[str, Any]) -> None:
        """複雑なマルチパートセグメントを、HTTPXが要求する高度な4要素タプル配列へとパッキングします"""
        httpx_data: dict[str, Any] = {}
        httpx_files: dict[str, Any] = {}

        for part in multipart_body:
            # メタデータを持たない純粋な文字列フォームフィールド
            if part.filename is None and part.content_type is None and not part.headers:
                httpx_data[part.name] = part.value
            else:
                # Content-Typeマージを含む個別パートヘッダーの構築
                part_headers = dict(part.headers)
                if part.content_type and "Content-Type" not in part_headers:
                    part_headers["Content-Type"] = part.content_type

                # 構造: (ファイル名, 生データ, Content-Type, パートヘッダー)
                httpx_files[part.name] = (
                    part.filename,
                    part.value,
                    part.content_type,
                    part_headers if part_headers else None,
                )

        if httpx_data:
            kwargs_ref["data"] = httpx_data
        if httpx_files:
            kwargs_ref["files"] = httpx_files


class HttpxClientHttpResponse(ClientHttpResponse):
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

    def stream_lines(self) -> AsyncIterator[str]:
        return self._response.aiter_lines()

    def stream_raw_lines(self) -> AsyncIterator[bytes]:
        async def _generator() -> AsyncIterator[bytes]:
            buffer = b""
            async for chunk in self._response.aiter_bytes():
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    yield line
            if buffer:
                yield buffer

        return _generator()

    def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]:
        return self._response.aiter_bytes(chunk_size)

    async def close(self) -> None:
        await self._response.aclose()

@runtime_checkable
class HttpxClientCustomizer(Protocol):

    def customize_client(self, client: httpx.AsyncClient, /) -> None:
        """生成直後の AsyncClient インスタンスを生で受け取り、event_hooks などの処理を実行します。"""
        ...
