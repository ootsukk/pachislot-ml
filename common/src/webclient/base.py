from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from webclient.plugin import plugin
from webclient.types import HttpMethod
from webclient.utility import extract_config_type

# =====================================================================
#  設定オブジェクト用 基底クラス群
# =====================================================================

@dataclass(frozen=True)
class ConnectorConfig:
    """すべての下位通信コネクター構成の基底クラス"""
    pass


@dataclass(frozen=True)
class FilterConfig:
    """すべての自動マウントインターセプター（フィルター）構成の基底クラス"""
    enabled: bool = True
    order: int = 50

@dataclass(frozen=True)
class PrioritizedFilter:
    """内部でのフィルター順序ソートおよび名前解決を担保するメタデータコンテナ"""

    filter_func: ExchangeFilterFunction
    priority: int = 0
    name_key: str | None = None


# =====================================================================
#  コア・データモデル ＆ インターフェース（Protocol）
# =====================================================================

@dataclass(frozen=True)
class MultipartPart:
    """マルチパートリクエストを構成する、個々の独立したパート（要素）を表す不変データコンテナ"""

    name: str
    value: bytes | str
    filename: str | None = None
    content_type: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
class ProxyOptions:
    """すべての通信コネクターで共有される、ネットワークプロキシの共通構成データ構造"""

    http_url: str | None = None
    https_url: str | None = None
    username: str | None = None
    password: str | None = None
    no_proxy: str | None = None

@dataclass(frozen=True)
class RedirectOptions:
    """すべての通信コネクターで一貫して適用される、自動リダイレクト制御の共通構成データ構造"""

    follow_redirects: bool = True
    max_redirects: int = 20

@dataclass(frozen=True)
class ClientHttpRequest:
    """不変（Immutable）なHTTPリクエストデータコンテナ"""
    method: HttpMethod | str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str | Sequence[str]] | None = None
    cookies: Mapping[str, str] | None = None
    auth: ClientHttpAuth | None = None
    timeout: float | object | None = None
    content: bytes | None = None
    data: Mapping[str, object] | None = None
    json_body: object | None = None
    files: Mapping[str, object] | None = None
    multipart_body: Sequence[MultipartPart] | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class ClientHttpAuth(Protocol):
    def apply(self, request: ClientHttpRequest, /) -> ClientHttpRequest:
        return request


@runtime_checkable
class ClientHttpResponse(Protocol):
    @property
    def status_code(self) -> int: ...
    @property
    def headers(self) -> Mapping[str, str]: ...
    async def read_body(self) -> bytes: ...
    def stream_lines(self) -> AsyncIterator[str]: ...
    def stream_chunks(self, chunk_size: int = 4096) -> AsyncIterator[bytes]: ...
    async def close(self) -> None: ...


@runtime_checkable
class ClientHttpConnector(Protocol):
    async def connect(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse: ...
    async def close(self) -> None: ...


@runtime_checkable
class CookieStore(Protocol):
    def save(self, url: str, cookies: Mapping[str, str], /) -> None: ...
    def load(self, url: str, /) -> Mapping[str, str]: ...
    def clear(self) -> None: ...


@runtime_checkable
class BodyEncoder(Protocol):
    def encode(self, body: object, /) -> object: ...


@runtime_checkable
class BodyDecoder(Protocol):
    def decode[T](self, content: bytes, target_type: type[T], /) -> T: ...


# =====================================================================
#  フィルターチェーン用 実行パイプラインインフラ
# =====================================================================

class ExchangeFunction(ABC):
    """リクエストを受け取り、レスポンスを返す実行チェーンの抽象契約"""
    @abstractmethod
    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse: ...


@runtime_checkable
class ExchangeFilter(Protocol):
    """具象フィルターが実装すべきインターフェース契約"""
    async def __call__(
        self, request: ClientHttpRequest, next_exchange: ExchangeFunction, stream: bool, /
    ) -> ClientHttpResponse: ...


ExchangeFilterFunction = (ExchangeFilter | Any)


class DefaultConnectorExchangeFunction(ExchangeFunction):
    """フィルターチェーンの最深部で、最終的に具象コネクターを駆動する終端ロジック"""
    def __init__(self, connector: ClientHttpConnector, /) -> None:
        self._connector: ClientHttpConnector = connector

    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        return await self._connector.connect(request, stream=stream)


class FilteredExchangeFunction(ExchangeFunction):
    """フィルター群を高階関数としてマトリョーシカ状にネスト結合していくインフラクラス"""
    def __init__(self, filter_element: ExchangeFilterFunction, next_exchange: ExchangeFunction, /) -> None:
        self._filter_element: ExchangeFilterFunction = filter_element
        self._next_exchange: ExchangeFunction = next_exchange

    async def exchange(self, request: ClientHttpRequest, *, stream: bool = False) -> ClientHttpResponse:
        return await self._filter_element(request, self._next_exchange, stream)


# =====================================================================
#  自己解決型 DI 抽象基底クラス
# =====================================================================

class Configurable[T]:
    """コンポーネント自身が、対応する設定（Config）の解決・抽出に責任を持つための抽象基底クラス"""

    @classmethod
    def create_config(cls, source: Any, /, *, type_pool: dict[type[Any], Any] | None = None) -> T:
        config_class = extract_config_type(cls)
        if config_class is None:
            raise RuntimeError(f"クラス '{cls.__name__}' のジェネリクスから設定型を逆引き抽出できませんでした。")

        if isinstance(source, config_class):
            return source

        if dataclasses.is_dataclass(source):
            for fld in dataclasses.fields(source):
                val = getattr(source, fld.name, None)
                if isinstance(val, config_class):
                    return cast(T, val)

        if type_pool:
            if config_class in type_pool:
                return cast(T, type_pool[config_class])
            for val in type_pool.values():
                if isinstance(val, config_class):
                    return cast(T, val)

        try:
            return cast(T, config_class())
        except Exception as err:
            raise RuntimeError(f"型 [{config_class.__name__}] のオブジェクト自動抽出に失敗しました。") from err
