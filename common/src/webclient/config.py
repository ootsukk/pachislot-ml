from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from webclient.base import ProxyOptions, RedirectOptions
from webclient.types import CHARSET_UTF8


@dataclass(frozen=True)
class WebClientConfig:

    connector_name: str = "auto"
    connector_options: Mapping[str, Any] = field(default_factory=dict)

    base_url: str = ""
    api_version: str = ""
    timeout: float | None = None
    default_headers: Mapping[str, str] = field(default_factory=dict)
    default_cookies: Mapping[str, str] = field(default_factory=dict)
    proxy: ProxyOptions | None = None
    redirect: RedirectOptions = field(default_factory=RedirectOptions)

    filters: Mapping[str, Any] = field(default_factory=dict)

    cookie_store: str = "auto"
    encoder: str = "auto"
    decoder: str = "auto"
    plugin_groups: Sequence[str] = field(default_factory=lambda: ["webclient.plugins"])

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

        raw_groups = config_data.get("plugin_groups", config_data.get("plugin_group", ["webclient.plugins"]))
        plugin_groups_val = (
            [raw_groups]
            if isinstance(raw_groups, str)
            else [str(g) for g in raw_groups]
            if isinstance(raw_groups, (list, tuple))
            else ["webclient.plugins"]
        )

        chosen_connector_name = config_data.get("connector_name", "auto")
        chosen_connector_options: dict[str, Any] = dict(config_data.get("connector_options", {}))

        raw_connector = config_data.get("connector")
        if isinstance(raw_connector, dict):
            for name_key, props in raw_connector.items():
                chosen_connector_name = name_key
                chosen_connector_options = {k: v for k, v in (props or {}).items() if not k.startswith("_")}
                break
        elif isinstance(raw_connector, str) and raw_connector.lower() != "auto":
            chosen_connector_name = raw_connector.lower()

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

        redirect_val = RedirectOptions()
        raw_redirect = config_data.get("redirect")
        if isinstance(raw_redirect, dict):
            redirect_val = RedirectOptions(
                follow_redirects=raw_redirect.get("follow_redirects", True),
                max_redirects=raw_redirect.get("max_redirects", 20),
            )

        filters_section = cast(Mapping[str, Any], config_data.get("filters", {}) or {})

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
            redirect=redirect_val,
            filters=filters_section,
            cookie_store=str(config_data.get("cookie_store", "auto")),
            encoder=str(config_data.get("encoder", "auto")),
            decoder=str(config_data.get("decoder", "auto")),
            plugin_groups=plugin_groups_val,
        )
