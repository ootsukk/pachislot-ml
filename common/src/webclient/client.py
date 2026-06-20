from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Self, cast

from webclient.base import (
    ClientHttpConnector,
    DefaultConnectorExchangeFunction,
    ExchangeFilterFunction,
    ExchangeFunction,
    FilteredExchangeFunction,
    HttpMethod,
    PrioritizedFilter,
)
from webclient.codec import BodyDecoder, BodyEncoder, DefaultBodyDecoder, DefaultBodyEncoder
from webclient.config import WebClientConfig
from webclient.cookies import MemoryCookieStore
from webclient.resolver import UniversalPluginResolver
from webclient.specs import RequestBodySpec, RequestBodyUriSpec, RequestHeadersUriSpec

if TYPE_CHECKING:
    from webclient.base import CookieStore  # type: ignore


class WebClient:
    def __init__(
        self,
        exchange_function: ExchangeFunction,
        encoder: BodyEncoder,
        decoder: BodyDecoder,
        /,
        *,
        base_url: str = "",
        api_version: str = "",
        default_headers: Mapping[str, str] | None = None,
        default_cookies: Mapping[str, str] | None = None,
        default_timeout: float | object | None = None,
        raw_connector_for_close: ClientHttpConnector | None = None,
        cookie_store: CookieStore | None = None,
        filters: Sequence[ExchangeFilterFunction] | None = None,
        context_attributes: Sequence[tuple[ContextVar[object], str]] | None = None,
        config: WebClientConfig | None = None,
    ) -> None:
        self._exchange_function: ExchangeFunction = exchange_function
        self._encoder: BodyEncoder = encoder
        self._decoder: BodyDecoder = decoder

        self._base_url: str = base_url
        self._api_version: str = api_version
        self._default_headers: Mapping[str, str] = default_headers if default_headers is not None else {}
        self._default_cookies: Mapping[str, str] = default_cookies if default_cookies is not None else {}
        self._default_timeout: float | object | None = default_timeout
        self._raw_connector_for_close: ClientHttpConnector | None = raw_connector_for_close
        self._cookie_store: CookieStore | None = cookie_store
        self._filters_seq: Sequence[ExchangeFilterFunction] = filters or []
        self._context_attributes: Sequence[tuple[ContextVar[object], str]] = context_attributes or []
        self._config: WebClientConfig = config if config is not None else WebClientConfig()

    @classmethod
    def builder(cls) -> BaseWebClientBuilder:
        return DefaultWebClientBuilder()

    @classmethod
    def customize(cls, config: WebClientConfig) -> BaseWebClientBuilder:
        return CustomizeWebClientBuilder(config)

    def mutate(self) -> BaseWebClientBuilder:
        builder = DefaultWebClientBuilder()
        builder.base_url(self._base_url)
        builder.api_version(self._api_version)
        builder.default_headers(self._default_headers)
        builder.default_cookies(self._default_cookies)
        builder.plugin_groups(list(self._config.plugin_groups))

        if isinstance(self._default_timeout, float):
            builder.default_timeout(self._default_timeout)

        if self._raw_connector_for_close is not None:
            builder.client_connector(self._raw_connector_for_close)

        builder.body_encoder(self._encoder)
        builder.body_decoder(self._decoder)

        cast_builder = cast(Any, builder)
        if self._filters_seq:
            if PrioritizedFilter not in cast_builder._explicit_pool:
                cast_builder._explicit_pool[PrioritizedFilter] = []
            for f in self._filters_seq:
                cast_builder._explicit_pool[PrioritizedFilter].append(
                    PrioritizedFilter(filter_func=f, priority=getattr(f, "priority", 100))
                )

        if self._context_attributes:
            cast_builder._context_attributes = list(self._context_attributes)

        cast_builder._is_mutated = True
        return builder

    def create(self, base_url: str, /) -> WebClient:
        return self.mutate().base_url(base_url).build()

    def switch_connector(self, connector: ClientHttpConnector, /) -> WebClient:
        return self.mutate().client_connector(connector).build()

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


class BaseWebClientBuilder(ABC):

    def __init__(self) -> None:
        self._plugin_groups: list[str] = ["webclient.plugins"]
        self._base_url: str = ""
        self._api_version: str = "v1"
        self._default_headers: Mapping[str, str] = {}
        self._default_cookies: Mapping[str, str] = {}
        self._default_timeout: float | object | None = 30.0

        # コード側から手動で直撃インジェクション（上書き指定）された生アセットを保持する優先プール
        self._explicit_pool: dict[type[Any], Any] = {}

        self._context_attributes: list[tuple[ContextVar[object], str]] = []
        self._is_mutated: bool = False

    def base_url(self, base_url: str, /) -> Self:
        self._base_url = base_url
        return self

    def api_version(self, api_version: str, /) -> Self:
        self._api_version = api_version
        return self

    def client_connector(self, connector: ClientHttpConnector, /) -> Self:
        self._explicit_pool[ClientHttpConnector] = connector
        return self

    def body_encoder(self, encoder: BodyEncoder, /) -> Self:
        self._explicit_pool[BodyEncoder] = encoder
        return self

    def body_decoder(self, decoder: BodyDecoder, /) -> Self:
        self._explicit_pool[BodyDecoder] = decoder
        return self

    def plugin_groups(self, groups: list[str], /) -> Self:
        self._plugin_groups = list(groups)
        return self

    def filter(
        self, filter_func: ExchangeFilterFunction, /, *, priority: int = 0, shortcut_name: str | None = None
    ) -> Self:
        if PrioritizedFilter not in self._explicit_pool:
            self._explicit_pool[PrioritizedFilter] = []
        self._explicit_pool[PrioritizedFilter].append(
            PrioritizedFilter(filter_func=filter_func, priority=priority, name_key=shortcut_name)
        )
        return self

    def filters(self, *filters: ExchangeFilterFunction) -> Self:
        if PrioritizedFilter not in self._explicit_pool:
            self._explicit_pool[PrioritizedFilter] = []
        for f in filters:
            self._explicit_pool[PrioritizedFilter].append(PrioritizedFilter(filter_func=f, priority=100))
        return self

    def context_attribute(self, context_var: ContextVar[object], attribute_key: str, /) -> Self:
        self._context_attributes.append((context_var, attribute_key))
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

    @abstractmethod
    def build(self) -> WebClient:
        pass

    def _create_client_core(self, resolved_pool: dict[type[Any], Any], config: WebClientConfig) -> WebClient:
        """子クラスの build() からキックされる、最終マージパイプライン"""

        connector = resolved_pool.get(ClientHttpConnector)
        encoder = resolved_pool.get(BodyEncoder, DefaultBodyEncoder())
        decoder = resolved_pool.get(BodyDecoder, DefaultBodyDecoder())
        cookie_store = resolved_pool.get(CookieStore, MemoryCookieStore())
        sorted_raw_filters = resolved_pool.get(PrioritizedFilter, [])

        # コンテキスト属性フィルターの動的合流
        if self._context_attributes:
            from webclient.filters.context_attributes_filter import ContextAttributesFilter

            for c_var, attr_key in self._context_attributes:
                sorted_raw_filters.append(ContextAttributesFilter(c_var, attr_key))

        # フィルターパイプラインの結合（後ろから順にネスト）
        exchange_pipeline: ExchangeFunction = DefaultConnectorExchangeFunction(connector) # type: ignore
        for raw_filter in reversed(sorted_raw_filters):
            exchange_pipeline = FilteredExchangeFunction(raw_filter, exchange_pipeline)

        return WebClient(
            exchange_pipeline,
            encoder,
            decoder,
            base_url=self._base_url,
            api_version=self._api_version,
            default_headers=self._default_headers,
            default_cookies=self._default_cookies,
            default_timeout=self._default_timeout,
            raw_connector_for_close=connector,
            cookie_store=cookie_store,
            filters=sorted_raw_filters,
            context_attributes=list(self._context_attributes),
        )

class DefaultWebClientBuilder(BaseWebClientBuilder):

    def build(self) -> WebClient:

        fake_empty_config = WebClientConfig(plugin_groups=self._plugin_groups)
        resolved_pool = UniversalPluginResolver.resolve_all(
            fake_empty_config, type_pool={}, explicit_pool=self._explicit_pool
        )
        return self._create_client_core(resolved_pool, fake_empty_config)


class CustomizeWebClientBuilder(BaseWebClientBuilder):
    """外部設定ファイルのパースデータ（WebClientConfig）をそのまま受け止め、
    上書きのタイムラインをリゾルバーへ丸投げするクッション。
    """
    def __init__(self, config: WebClientConfig, /) -> None:
        super().__init__()
        self._config = config

        # Config に書かれている明示的な基本パラメータを親クラスの不変セッター経由で同期
        if config.plugin_groups:
            self.plugin_groups(list(config.plugin_groups))
        if config.base_url:
            self.base_url(config.base_url)
        if config.api_version:
            self.api_version(config.api_version)
        if config.timeout is not None:
            self.default_timeout(config.timeout)
        if config.default_headers:
            self.default_headers(config.default_headers)
        if config.default_cookies:
            self.default_cookies(config.default_cookies)

    def build(self) -> WebClient:
        resolved_pool = UniversalPluginResolver.resolve_all(
            self._config, type_pool={}, explicit_pool=self._explicit_pool
        )
        return self._create_client_core(resolved_pool, self._config)
