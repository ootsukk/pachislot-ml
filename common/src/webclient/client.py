from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, Self

from webclient.codec import BodyDecoder, BodyEncoder, DefaultBodyDecoder, DefaultBodyEncoder
from webclient.config import WebClientConfig
from webclient.cookies import MemoryCookieStore
from webclient.filter import (
    DefaultConnectorExchangeFunction,
    ExchangeFilterFunction,
    ExchangeFunction,
    FilteredExchangeFunction,
)
from webclient.specs import RequestBodySpec, RequestBodyUriSpec, RequestHeadersUriSpec
from webclient.types import ClientHttpConnector, HttpMethod

if TYPE_CHECKING:
    from webclient.types import CookieStore  # type: ignore


def import_string[R](dotted_path: str, /) -> type[R] | Any:
    """文字列の完全修飾パスから、対応するクラスまたは関数を動的にロードします（Python 3.14 ジェネリクス型パラメータ構文）"""
    try:
        module_path, attr_name = dotted_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    except (ValueError, ImportError, AttributeError) as err:
        raise RuntimeError(f"設定されたパスからのコンポーネントインポートに失敗しました: {dotted_path}") from err


@dataclass(frozen=True)
class PrioritizedFilter:
    """内部でのフィルター順序ソートおよび名前解決を担保するメタデータコンテナ"""
    filter_func: ExchangeFilterFunction
    order: int = 0
    shortcut_name: str | None = None


class WebClient:
    """Spring WebClientのPython版コアクラス

    リクエスト仕様（Spec層）の生成、およびフィルターチェーンの起点となる
    スレッドセーフかつ不変（Immutable）なファサードコンポーネントです。
    """

    def __init__(
        self,
        config: WebClientConfig,
        exchange_function: ExchangeFunction,
        encoder: BodyEncoder,
        decoder: BodyDecoder,
        /,
        *,
        base_url: str | None = None,
        api_version: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        default_cookies: Mapping[str, str] | None = None,
        default_timeout: float | object | None = None,
        raw_connector_for_close: ClientHttpConnector | None = None,
        cookie_store: CookieStore | None = None,
        filters: Sequence[ExchangeFilterFunction] | None = None,
        context_attributes: Sequence[tuple[ContextVar[object], str]] | None = None,
    ) -> None:
        """【100%完全復元】元の厳格な位置専用・キーワード専用セパレータと拡張メタデータ引数"""
        self._config: WebClientConfig = config
        self._exchange_function: ExchangeFunction = exchange_function
        self._encoder: BodyEncoder = encoder
        self._decoder: BodyDecoder = decoder

        self._base_url: str = base_url if base_url is not None else config.base_url
        self._api_version: str = api_version if api_version is not None else config.api_version
        self._default_headers: Mapping[str, str] = default_headers if default_headers is not None else config.default_headers
        self._default_cookies: Mapping[str, str] = default_cookies if default_cookies is not None else config.default_cookies
        self._default_timeout: float | object | None = default_timeout if default_timeout is not None else config.timeout
        self._raw_connector_for_close: ClientHttpConnector | None = raw_connector_for_close

        self._cookie_store: CookieStore | None = cookie_store
        self._filters_seq: Sequence[ExchangeFilterFunction] = filters or []
        self._context_attributes: Sequence[tuple[ContextVar[object], str]] = context_attributes or []

    @classmethod
    def builder(cls, config: WebClientConfig | None = None, /) -> WebClientBuilder:
        return DefaultWebClientBuilder(config)

    def mutate(self) -> WebClientBuilder:
        """既存のWebClientの全コンテキストをProtocolビルダーへ安全に引き渡して派生させます"""
        builder = DefaultWebClientBuilder(self._config)
        builder.base_url(self._base_url)
        builder.api_version(self._api_version)
        builder.default_headers(self._default_headers)
        builder.default_cookies(self._default_cookies)

        if isinstance(self._default_timeout, float):
            builder.default_timeout(self._default_timeout)

        if self._raw_connector_for_close is not None:
            builder.client_connector(self._raw_connector_for_close)

        builder.body_encoder(self._encoder)
        builder.body_decoder(self._decoder)

        cast_builder = builder
        if self._filters_seq:
            cast_builder.filters(*self._filters_seq)

        cast_builder._is_mutated = True
        return cast_builder

    def create(self, base_url: str, /) -> WebClient:
        return self.mutate().base_url(base_url).build()

    def get(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.GET,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def post(self) -> RequestBodyUriSpec:
        return RequestBodyUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.POST,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def put(self) -> RequestBodyUriSpec:
        return RequestBodyUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.PUT,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def delete(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.DELETE,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def patch(self) -> RequestBodyUriSpec:
        return RequestBodyUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.PATCH,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def head(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.HEAD,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def options(self) -> RequestHeadersUriSpec:
        return RequestHeadersUriSpec(
            self._exchange_function, self._encoder, self._decoder, HttpMethod.OPTIONS,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    def method(self, method: HttpMethod, /) -> RequestBodySpec:
        return RequestBodySpec(
            self._exchange_function, self._encoder, self._decoder, method,
            self._api_version, self._default_headers, self._default_cookies, self._default_timeout
        )

    async def close(self) -> None:
        if self._raw_connector_for_close is not None:
            await self._raw_connector_for_close.close()


class WebClientBuilder(Protocol):
    """Springの WebClient.Builder 思想を100%体現する構造的サブタイピング（Protocol）インターフェース"""

    def base_url(self, base_url: str, /) -> Self: ...

    def api_version(self, api_version: str, /) -> Self: ...

    def client_connector(self, connector: ClientHttpConnector, /) -> Self: ...

    def body_encoder(self, encoder: BodyEncoder, /) -> Self: ...

    def body_decoder(self, decoder: BodyDecoder, /) -> Self: ...

    def filter(self, filter_func: ExchangeFilterFunction, /, *, order: int = 0, shortcut_name: str | None = None) -> Self: ...

    def filters(self, *filters: ExchangeFilterFunction) -> Self: ...

    def default_headers(self, headers: Mapping[str, str], /) -> Self: ...

    def default_cookies(self, cookies: Mapping[str, str], /) -> Self: ...

    def default_timeout(self, timeout: float, /) -> Self: ...

    def build(self) -> WebClient: ...


class DefaultWebClientBuilder:
    """WebClientBuilder Protocolの仕様を満たす、流れるような標準構成具象実装"""

    def __init__(self, config: WebClientConfig | None = None, /) -> None:
        if config is not None:
            self._config = config
            self._base_url = config.base_url
            self._api_version = config.api_version
            self._default_headers = config.default_headers
            self._default_cookies = config.default_cookies
            self._default_timeout = config.timeout
        else:
            self._config = WebClientConfig()
            self._base_url = ""
            self._api_version = ""
            self._default_headers = {}
            self._default_cookies = {}
            self._default_timeout = 20.0

        self._connector: ClientHttpConnector | None = None
        self._encoder: BodyEncoder | None = None
        self._decoder: BodyDecoder | None = None
        self._cookie_store: CookieStore | None = None
        self._context_attributes: list[tuple[ContextVar[object], str]] = []
        self._prioritized_filters: list[PrioritizedFilter] = []
        self._is_mutated: bool = False

    def base_url(self, base_url: str, /) -> Self:
        self._base_url = base_url
        return self

    def api_version(self, api_version: str, /) -> Self:
        self._api_version = api_version
        return self

    def client_connector(self, connector: ClientHttpConnector, /) -> Self:
        self._connector = connector
        return self

    def body_encoder(self, encoder: BodyEncoder, /) -> Self:
        self._encoder = encoder
        return self

    def body_decoder(self, decoder: BodyDecoder, /) -> Self:
        self._decoder = decoder
        return self

    def filter(self, filter_func: ExchangeFilterFunction, /, *, order: int = 0, shortcut_name: str | None = None) -> Self:
        self._prioritized_filters.append(
            PrioritizedFilter(filter_func=filter_func, order=order, shortcut_name=shortcut_name)
        )
        return self

    def filters(self, *filters: ExchangeFilterFunction) -> Self:
        for f in filters:
            self._prioritized_filters.append(PrioritizedFilter(filter_func=f, order=0))
        return self

    def default_headers(self, headers: Mapping[str, str], /) -> Self:
        merged = dict(self._default_headers)
        merged.update(headers)
        self._default_headers = merged
        return self

    def default_cookies(self, cookies: Mapping[str, str], /) -> Self:
        merged = dict(self._default_cookies)
        merged.update(cookies)
        self._default_cookies = merged
        return self

    def default_timeout(self, timeout: float, /) -> Self:
        self._default_timeout = timeout
        return self

    def build(self) -> WebClient:
        if self._connector is None and self._config.connector.dotted_path:
            connector_class = import_string(self._config.connector.dotted_path)
            self._connector = connector_class(self._config.connector)

        if self._connector is None:
            try:
                from webclient.connectors.httpx_connector import HttpxClientHttpConnector
                self._connector = HttpxClientHttpConnector()
            except ImportError as err:
                raise RuntimeError("デフォルトのHTTPXコネクターをロードできません。") from err

        encoder = self._encoder if self._encoder is not None else DefaultBodyEncoder()
        decoder = self._decoder if self._decoder is not None else DefaultBodyDecoder()

        cookie_store = self._cookie_store if self._cookie_store is not None else MemoryCookieStore()

        all_filters = list(self._prioritized_filters)

        if not self._is_mutated and self._config.all_filters:
            for f_config in self._config.all_filters:
                if not f_config.enabled:
                    continue

                if f_config.dotted_path:
                    filter_factory_or_class = import_string(f_config.dotted_path)
                    filter_func = filter_factory_or_class(f_config)

                    all_filters.append(
                        PrioritizedFilter(
                            filter_func=filter_func,
                            order=f_config.order,
                            shortcut_name=f_config.shortcut_name
                        )
                    )

        all_filters.sort(key=lambda x: x.order)

        sorted_raw_filters = [p.filter_func for p in all_filters]

        exchange_pipeline: ExchangeFunction = DefaultConnectorExchangeFunction(self._connector)
        for raw_filter in reversed(sorted_raw_filters):
            exchange_pipeline = FilteredExchangeFunction(raw_filter, exchange_pipeline)

        return WebClient(
            self._config,
            exchange_pipeline,
            encoder,
            decoder,
            base_url=self._base_url,
            api_version=self._api_version,
            default_headers=self._default_headers,
            default_cookies=self._default_cookies,
            default_timeout=self._default_timeout,
            raw_connector_for_close=self._connector,
            cookie_store=cookie_store,
            filters=sorted_raw_filters,
            context_attributes=list(self._context_attributes)
        )
