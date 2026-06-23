from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable
from urllib.parse import urlparse, urlunparse

from webclient.plugin import dependency_module, plugin_impl
from webclient.types import CHARSET_UTF8, MediaType

if TYPE_CHECKING:
    from curl_cffi import requests

from webclient.base import (
    ClientHttpConnector,
    ClientHttpRequest,
    ClientHttpResponse,
    Configurable,
    ProxyOptions,
    RedirectOptions,
)


@dataclass(frozen=True)
class CurlCffiConnectorOptions:
    impersonate: str | None = "chrome110"
    verify: bool = True
    timeout: float = 30.0


@dependency_module("curl-cffi", ">=0.25.0")
@plugin_impl(value="curl_cffi", priority=150)
class CurlCffiClientHttpConnector(ClientHttpConnector, Configurable[CurlCffiConnectorOptions]):
    """
    curl_cffi (libcurl wrapper) 駆動の非同期通信具象コネクター。
    コアのコンポーネントスキャンによって全自動で検知され、DIレジストリへマウントされます。
    """

    def __init__(
        self,
        config: CurlCffiConnectorOptions | None = None,
        proxy_options: ProxyOptions | None = None,
        redirect_options: RedirectOptions | None = None,
    ) -> None:

        self.config = config if config is not None else CurlCffiConnectorOptions()
        self.proxy_options = proxy_options if proxy_options is not None else ProxyOptions()
        self.redirect_options = redirect_options if redirect_options is not None else RedirectOptions()

        curl_proxies = self._build_proxy_map()

        self._session = requests.AsyncSession(
            verify=self.config.verify,
            impersonate=cast(Any, self.config.impersonate),
            proxies=cast(Any, curl_proxies if curl_proxies else None),
        )

    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        """共通リクエストモデル（ClientHttpRequest）を curl_cffi 固有の引数へ翻訳して送信します"""

        kwargs = self._build_request_kwargs(request, stream)

        # マルチパートボディ要件がある場合は、関数化されたビルダーで安全にインポーズ
        if request.multipart_body:
            self._inject_multipart_payload(request.multipart_body, kwargs)

        # 手動指定ファイルマップがある場合のマージ（互換性維持）
        if request.files is not None:
            self._merge_explicit_files(request.files, kwargs)

        curl_res = await self._session.request(**kwargs)
        return CurlCffiClientHttpResponse(curl_res)

    async def close(self) -> None:
        await self._session.close()

    def _build_proxy_map(self) -> dict[str, str]:
        """curl_cffi (libcurl) が要求するプロキシマッピング構造を生成します"""
        curl_proxies: dict[str, str] = {}
        if not self.proxy_options:
            return curl_proxies

        if self.proxy_options.http_url:
            curl_proxies["http"] = self._build_proxy_authenticated_url(self.proxy_options.http_url)
        if self.proxy_options.https_url:
            curl_proxies["https"] = self._build_proxy_authenticated_url(self.proxy_options.https_url)

        if self.proxy_options.no_proxy:
            # libcurl の NOPROXY 判定規則（CURLOPT_NOPROXY）はカンマ区切り文字列そのもの。
            # HTTPXのようにホストごとにトランスポートを分離して再マウントする手続き型ハックは一切不要。
            # 除外設定文字列を流し込むだけで、libcurl カーネルが超高速に自動透過バイパスを執行します。
            curl_proxies["no_proxy"] = self.proxy_options.no_proxy

        return curl_proxies

    def _build_proxy_authenticated_url(self, base_url: str) -> str:
        """プロキシURLに対し、ユーザー認証資格情報を安全に埋め込みインポーズします"""
        if not self.proxy_options or not self.proxy_options.username:
            return base_url
        parsed = urlparse(base_url)
        auth_str = f"{self.proxy_options.username}:{self.proxy_options.password or ''}"
        return urlunparse(parsed._replace(netloc=f"{auth_str}@{parsed.netloc}"))

    def _build_request_kwargs(self, request: ClientHttpRequest, stream: bool) -> dict[str, Any]:
        """ClientHttpRequest から curl_cffi 標準リクエスト引数へのプレーンマッピングを執行します"""
        timeout = request.timeout if request.timeout is not None else self.config.timeout

        kwargs: dict[str, Any] = {
            "method": request.method,
            "url": request.url,
            "headers": dict(request.headers) if request.headers else None,
            "params": request.params,
            "cookies": request.cookies,
            "timeout": timeout,
            "stream": stream,
            "allow_redirects": self.redirect_options.follow_redirects,
            "max_redirects": self.redirect_options.max_redirects,
        }

        # 排他積載ボディのマッピング
        if request.json_body is not None:
            kwargs["json"] = request.json_body
        elif request.data is not None:
            kwargs["data"] = request.data
        elif request.content is not None:
            kwargs["data"] = request.content

        return kwargs

    def _inject_multipart_payload(self, multipart_body: Sequence[Any], kwargs_ref: dict[str, Any]) -> None:
        """マルチパートセグメントを、curl_cffi (requests互換) の3要素タプル形式へとパッキングします"""
        curl_data: dict[str, Any] = {}
        curl_files: dict[str, Any] = {}

        for part in multipart_body:
            if part.filename is None and part.content_type is None:
                # 通常のテキストフィールド
                curl_data[part.name] = part.value
            else:
                # 構造: (ファイル名, コンテンツデータ, Content-Type)
                curl_files[part.name] = (
                    part.filename or "",  # 空文字を渡すことで、libcurl側に強制的ファイルパート認識を執行
                    part.value,
                    part.content_type or MediaType.OCTET_STREAM,
                )

        if curl_data:
            kwargs_ref["data"] = curl_data
        if curl_files:
            kwargs_ref["files"] = curl_files

    def _merge_explicit_files(self, explicit_files: Mapping[str, Any], kwargs_ref: dict[str, Any]) -> None:
        """手動指定された生ファイルマップ（request.files）を安全にマージして統合します"""
        existing_files = kwargs_ref.get("files", {})
        merged_files = dict(existing_files) if existing_files else {}
        merged_files.update(explicit_files)
        kwargs_ref["files"] = merged_files


class CurlCffiClientHttpResponse(ClientHttpResponse):
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

    def stream_lines(self) -> AsyncIterator[str]:
        async def _generator() -> AsyncIterator[str]:
            async for line in cast(AsyncIterator[bytes], self._response.iter_lines()):
                yield line.decode(CHARSET_UTF8, errors="replace")

        return _generator()

    def stream_raw_lines(self) -> AsyncIterator[bytes]:
        async def _generator() -> AsyncIterator[bytes]:
            buffer = b""
            async for chunk in self._response.aiter_content():
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    yield line
            if buffer:
                yield buffer

        return _generator()

    def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]:
        return cast(AsyncIterator[bytes], self._response.iter_content(chunk_size))

    async def close(self) -> None:
        pass

@runtime_checkable
class CurlCffiClientCustomizer(Protocol):
    def customize_client(self, client: requests.AsyncSession, /) -> None: ...
