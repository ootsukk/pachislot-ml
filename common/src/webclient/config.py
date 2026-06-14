from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from webclient.base import ConnectorConfig, FilterConfig, ProxyOptions
from webclient.types import CHARSET_UTF8
from webclient.utility import discover_config_classes


@dataclass(frozen=True)
class WebClientConfig:
    """YAMLやPython辞書などのデータ構造と1対1でマッピングされる、不変でスレッドセーフな最上位構成ルートモデル"""

    connector_name: str = "httpx"
    connector_options: Mapping[str, Any] = field(default_factory=dict)

    base_url: str = ""
    api_version: str = ""
    timeout: float | None = None
    default_headers: Mapping[str, str] = field(default_factory=dict)
    default_cookies: Mapping[str, str] = field(default_factory=dict)
    proxy: ProxyOptions | None = None
    filters: Mapping[str, FilterConfig] = field(default_factory=dict)
    cookie_store: str = "memory"
    encoder: str = "default"
    decoder: str = "default"
    plugin_groups: Sequence[str] = field(default_factory=lambda: ["webclient.plugins"])

    def __getattr__(self, name: str) -> Any:
        if name in self.filters:
            return self.filters[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    @classmethod
    def load(cls, source: str | Path | Mapping[str, Any], /) -> WebClientConfig:
        """ファイルパス(YAML)または生の辞書(Mapping)を自動判別し、WebClientConfigを構築します。"""
        if isinstance(source, (str, Path)):
            try:
                import yaml
            except ImportError as err:
                raise ImportError("YAMLファイルの解析には 'pyyaml' パッケージが必要です。") from err

            path = Path(source)
            if not path.exists():
                return cls()
            with path.open(encoding=CHARSET_UTF8) as f:
                raw_mapping: Any = yaml.safe_load(f) or {}
        else:
            raw_mapping = source

        config_data = raw_mapping.get("webclient", raw_mapping) if hasattr(raw_mapping, "get") else raw_mapping
        if not isinstance(config_data, dict):
            config_data = {}

        # plugin_groups の先行パース
        raw_groups = config_data.get("plugin_groups", config_data.get("plugin_group", ["webclient.plugins"]))
        plugin_groups_val = (
            [raw_groups]
            if isinstance(raw_groups, str)
            else [str(g) for g in raw_groups]
            if isinstance(raw_groups, (list, tuple))
            else ["webclient.plugins"]
        )

        available_connectors = discover_config_classes(ConnectorConfig, plugin_groups_val)
        available_filters = discover_config_classes(FilterConfig, plugin_groups_val)

        chosen_connector_name = "httpx"
        chosen_connector_options: dict[str, Any] = {}
        raw_connector = config_data.get("connector", "auto")

        if isinstance(raw_connector, dict):
            for name_key, props in raw_connector.items():
                if name_key in available_connectors:
                    chosen_connector_name = name_key
                    chosen_connector_options = {k: v for k, v in (props or {}).items() if not k.startswith("_")}
                    break
        elif (
            isinstance(raw_connector, str)
            and raw_connector.lower() != "auto"
            and raw_connector.lower() in available_connectors
        ):
            chosen_connector_name = raw_connector.lower()

        # プロキシセクションの自動パース
        proxy_val: ProxyOptions | None = None
        raw_proxy = config_data.get("proxy")
        if isinstance(raw_proxy, dict):
            proxy_val = ProxyOptions(
                http_url=raw_proxy.get("http_url"),
                https_url=raw_proxy.get("https_url"),
                username=raw_proxy.get("username"),
                password=raw_proxy.get("password"),
                no_proxy=raw_proxy.get("no_proxy"),
            )

        # フィルターの動的パース＆自動インスタンス化
        filter_instances: dict[str, FilterConfig] = {}
        for name_key, config_class in available_filters.items():
            filter_instances[name_key] = config_class()

        filters_section = config_data.get("filters") or {}
        for name_key, config_props in filters_section.items():
            config_class = available_filters.get(name_key)
            if config_class is not None:
                filter_instances[name_key] = config_class(
                    **{k: v for k, v in (config_props or {}).items() if not k.startswith("_")}
                )

        raw_timeout = config_data.get("timeout")
        return cls(
            connector_name=chosen_connector_name,
            connector_options=chosen_connector_options,
            base_url=str(config_data.get("base_url", "")),
            api_version=str(config_data.get("api_version", "")),
            timeout=float(raw_timeout) if raw_timeout is not None else None,
            default_headers=cast(Mapping[str, str], config_data.get("default_headers", {})),
            default_cookies=cast(Mapping[str, str], config_data.get("default_cookies", {})),
            proxy=proxy_val,
            filters=filter_instances,
            cookie_store=str(config_data.get("cookie_store", "memory")),
            encoder=str(config_data.get("encoder", "default")),
            decoder=str(config_data.get("decoder", "default")),
            plugin_groups=plugin_groups_val,
        )
