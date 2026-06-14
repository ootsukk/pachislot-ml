from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Protocol, Self, cast

from webclient.base import (
    ClientHttpConnector,
    Configurable,
    DefaultConnectorExchangeFunction,
    ExchangeFilter,
    ExchangeFilterFunction,
    ExchangeFunction,
    FilteredExchangeFunction,
    HttpMethod,
    ProxyOptions,
    RedirectOptions,
)
from webclient.codec import BodyDecoder, BodyEncoder, DefaultBodyDecoder, DefaultBodyEncoder
from webclient.config import WebClientConfig
from webclient.connectors.httpx_connector import HttpxClientHttpConnector
from webclient.cookies import MemoryCookieStore
from webclient.specs import RequestBodySpec, RequestBodyUriSpec, RequestHeadersUriSpec
from webclient.utility import extract_config_type, resolve_component_name

if TYPE_CHECKING:
    from webclient.base import CookieStore  # type: ignore

REG_CONNECTORS = "connectors"
REG_FILTERS = "filters"
REG_ENCODERS = "encoders"
REG_DECODERS = "decoders"
REG_COOKIE_STORES = "cookie_stores"

@dataclass(frozen=True)
class PrioritizedFilter:
    """内部でのフィルター順序ソートおよび名前解決を担保するメタデータコンテナ"""
    filter_func: ExchangeFilterFunction
    order: int = 0
    name_key: str | None = None


class WebClient:
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

        if self._context_attributes:
            cast_builder._context_attributes = list(self._context_attributes)

        cast_builder._is_mutated = True
        return cast_builder

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


class WebClientBuilder(Protocol):

    def base_url(self, base_url: str, /) -> Self: ...

    def api_version(self, api_version: str, /) -> Self: ...

    def client_connector(self, connector: ClientHttpConnector, /) -> Self: ...

    def body_encoder(self, encoder: BodyEncoder, /) -> Self: ...

    def body_decoder(self, decoder: BodyDecoder, /) -> Self: ...

    def filter(self, filter_func: ExchangeFilterFunction, /, *, order: int = 0, shortcut_name: str | None = None) -> Self: ...

    def filters(self, *filters: ExchangeFilterFunction) -> Self: ...

    def context_attribute(self, context_var: ContextVar[object], attribute_key: str, /) -> Self: ...

    def default_headers(self, headers: Mapping[str, str], /) -> Self: ...

    def default_cookies(self, cookies: Mapping[str, str], /) -> Self: ...

    def default_timeout(self, timeout: float, /) -> Self: ...

    def build(self) -> WebClient: ...


class DefaultWebClientBuilder:

    # グローバルなインポートキャッシュを保持して、同一プロセス内での重複インポートを最小化します
    _registry_cache: dict[str, dict[str, dict[str, type[Any]]]] = {}

    def __init__(self, config: WebClientConfig | None = None, /) -> None:
        self._config = config if config is not None else WebClientConfig()
        self._base_url = self._config.base_url
        self._api_version = self._config.api_version
        self._default_headers = self._config.default_headers
        self._default_cookies = self._config.default_cookies
        self._default_timeout = self._config.timeout
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
        self._prioritized_filters.append(PrioritizedFilter(filter_func=filter_func, order=order, name_key=shortcut_name))
        return self

    def filters(self, *filters: ExchangeFilterFunction) -> Self:
        for f in filters:
            self._prioritized_filters.append(PrioritizedFilter(filter_func=f, order=0))
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

    def build(self) -> WebClient:

        regs = self._load_registries()

        encoder = self._resolve_encoder(regs[REG_ENCODERS])
        decoder = self._resolve_decoder(regs[REG_DECODERS])
        cookie_store = self._resolve_cookie_store(regs[REG_COOKIE_STORES])

        base_type_pool: dict[type[Any], Any] = {
            WebClientConfig: self._config,
            CookieStore: cookie_store,
            BodyEncoder: encoder,
            BodyDecoder: decoder,
            ProxyOptions: self._config.proxy,
        }

        connector = self._resolve_connector(regs[REG_CONNECTORS], base_type_pool)

        exchange_pipeline, sorted_filters = self._assemble_filter_chain(connector, regs[REG_FILTERS], base_type_pool)

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
            filters=sorted_filters,
            context_attributes=list(self._context_attributes),
        )

    def _load_registries(self) -> dict[str, dict[str, type[Any]]]:

        cache_key = "|".join(sorted(self._config.plugin_groups)) if self._config.plugin_groups else "default_group"
        cls = self.__class__

        if cache_key in cls._registry_cache:
            return {category: slot.copy() for category, slot in cls._registry_cache[cache_key].items()}

        new_registries = {
            REG_CONNECTORS: {},
            REG_FILTERS: {},
            REG_ENCODERS: {},
            REG_DECODERS: {},
            REG_COOKIE_STORES: {}
        }

        import webclient

        for module_info in pkgutil.walk_packages(webclient.__path__, webclient.__name__ + "."):
            try:
                mod = importlib.import_module(module_info.name)
                for _, cls_obj in inspect.getmembers(mod, inspect.isclass):
                    if not cls_obj.__module__.startswith("webclient."):
                        continue
                    self._classify_and_register(cls_obj, new_registries)
            except ImportError:
                continue

        if self._config.plugin_groups:
            for group_name in self._config.plugin_groups:
                try:
                    for ep in entry_points(group=group_name):
                        plugin_class = ep.load()
                        self._classify_and_register(plugin_class, new_registries)
                except Exception:
                    continue

        cls._registry_cache[cache_key] = new_registries

        return {category: slot.copy() for category, slot in new_registries.items()}


    def _classify_and_register(self, cls_obj: type[Any], registries: dict[str, dict[str, type[Any]]]) -> None:
        """クラスが適合している Protocol/ABC を検証し、レジストリへ分類マウントします"""
        if inspect.isabstract(cls_obj):
            return

        target_name = resolve_component_name(cls_obj)
        if issubclass(cls_obj, ClientHttpConnector) and cls_obj is not ClientHttpConnector:
            registries[REG_CONNECTORS][target_name] = cls_obj
        elif issubclass(cls_obj, ExchangeFilter) and cls_obj is not ExchangeFilter:
            registries[REG_FILTERS][target_name] = cls_obj
        elif issubclass(cls_obj, BodyEncoder) and cls_obj is not BodyEncoder:
            registries[REG_ENCODERS][target_name] = cls_obj
        elif issubclass(cls_obj, BodyDecoder) and cls_obj is not BodyDecoder:
            registries[REG_DECODERS][target_name] = cls_obj
        elif issubclass(cls_obj, CookieStore) and cls_obj is not CookieStore:
            registries[REG_COOKIE_STORES][target_name] = cls_obj


    def _resolve_cookie_store(self, registry: dict[str, type[Any]]) -> CookieStore:
        if self._cookie_store is not None:
            return self._cookie_store
        store_cls = registry.get(self._config.cookie_store, MemoryCookieStore)
        if issubclass(store_cls, Configurable):
            store_cfg = store_cls.create_config(self._config)
            dynamic_store_cls = cast(Any, store_cls)
            return cast(CookieStore, dynamic_store_cls(store_cfg))

        return cast(CookieStore, cast(Any, store_cls)())


    def _resolve_encoder(self, registry: dict[str, type[Any]]) -> BodyEncoder:
        if self._encoder is not None:
            return self._encoder
        return registry.get(self._config.encoder, DefaultBodyEncoder)()


    def _resolve_decoder(self, registry: dict[str, type[Any]]) -> BodyDecoder:
        if self._decoder is not None:
            return self._decoder
        return registry.get(self._config.decoder, DefaultBodyDecoder)()


    def _resolve_connector(self, registry: dict[str, type[Any]], type_pool: dict[type[Any], Any]) -> ClientHttpConnector:
        if self._connector is not None:
            return self._connector

        target_name = self._config.connector_name
        connector_class = registry.get(target_name)

        if connector_class:
            if issubclass(connector_class, Configurable):
                config_class = extract_config_type(connector_class)
                source_input: Any = self._config.connector_options
                if config_class and isinstance(source_input, dict):
                    source_input = config_class(**{k: v for k, v in source_input.items() if not k.startswith("_")})

                conn_config = connector_class.create_config(source_input, type_pool=type_pool)

                type_pool[RedirectOptions] = self._config.redirect

                sig = inspect.signature(connector_class)
                kwargs: dict[str, Any] = {}
                for param_name, param in sig.parameters.items():
                    if param_name in ("self", "cls"):
                        continue
                    p_type = param.annotation
                    if p_type in type_pool:
                        kwargs[param_name] = type_pool[p_type]
                    elif p_type is type(conn_config):
                        kwargs[param_name] = conn_config

                dynamic_cls = cast(Any, connector_class)
                try:
                    return cast(ClientHttpConnector, dynamic_cls(conn_config, proxy_options=type_pool.get(ProxyOptions)))
                except TypeError:
                    return cast(ClientHttpConnector, dynamic_cls(conn_config))

            return cast(ClientHttpConnector, cast(Any, connector_class)())
        return HttpxClientHttpConnector()


    def _assemble_filter_chain(
        self, connector: ClientHttpConnector, registry: dict[str, type[Any]], type_pool: dict[type[Any], Any]
    ) -> tuple[ExchangeFunction, Sequence[ExchangeFilterFunction]]:
        all_filters = list(self._prioritized_filters)

        # ContextAttributesFilterの自動注入
        if self._context_attributes:
            from webclient.filters.context_attributes_filter import ContextAttributesFilter

            for c_var, attr_key in self._context_attributes:
                all_filters.append(
                    PrioritizedFilter(
                        filter_func=ContextAttributesFilter(c_var, attr_key), order=10, name_key="context_attribute"
                    )
                )

        # YAML/辞書経由ですでに型安全にインスタンス化されている全 FilterConfig を処理対象とする
        if not self._is_mutated and self._config.filters:
            for name_key, f_config in self._config.filters.items():
                if not f_config.enabled:
                    continue

                filter_class = registry.get(name_key)
                if filter_class:
                    filter_func = self._instantiate_filter(filter_class, f_config, type_pool)
                    all_filters.append(
                        PrioritizedFilter(filter_func=filter_func, order=f_config.order, name_key=name_key)
                    )

        all_filters.sort(key=lambda x: x.order)
        sorted_raw_filters = [p.filter_func for p in all_filters]

        exchange_pipeline: ExchangeFunction = DefaultConnectorExchangeFunction(connector)
        for raw_filter in reversed(sorted_raw_filters):
            exchange_pipeline = FilteredExchangeFunction(raw_filter, exchange_pipeline)

        return exchange_pipeline, sorted_raw_filters


    def _instantiate_filter(self, filter_class: type[Any], f_config: Any, type_pool: dict[type[Any], Any]) -> Any:
        if issubclass(filter_class, Configurable):
            cfg = filter_class.create_config(f_config, type_pool=type_pool)
            dynamic_filter_cls = cast(Any, filter_class)
            return dynamic_filter_cls(cfg)

        sig = inspect.signature(filter_class)
        kwargs: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            p_type = param.annotation
            if p_type in type_pool:
                kwargs[param_name] = type_pool[p_type]
            elif p_type is type(f_config):
                kwargs[param_name] = f_config

        return cast(Any, filter_class)(**kwargs)
