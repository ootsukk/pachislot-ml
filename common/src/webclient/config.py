from __future__ import annotations

import importlib.util
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, cast


@dataclass(frozen=True)
class ConnectorConfig:
    """すべての下位通信コネクター構成の基底クラス"""

    shortcut_name: ClassVar[str] = ""
    dotted_path: ClassVar[str] = ""


@dataclass(frozen=True)
class HttpxConfig(ConnectorConfig):
    """標準の HTTPX 非同期コネクター用設定モデル"""

    shortcut_name: ClassVar[str] = "httpx"
    dotted_path: ClassVar[str] = "webclient.connectors.httpx_connector.HttpxClientHttpConnector"

    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    verify: bool | str = True
    trust_env: bool = True
    http1: bool = True
    http2: bool = False


@dataclass(frozen=True)
class CurlCffiConfig(ConnectorConfig):
    """TLS擬装（Impersonate）を可能にする高級コネクター用設定モデル"""

    shortcut_name: ClassVar[str] = "curl_cffi"
    dotted_path: ClassVar[str] = "webclient.connectors.curl_cffi_connector.CurlCffiClientHttpConnector"

    impersonate: str | None = "chrome"
    max_clients: int = 10
    verify: bool = True
    trust_env: bool = True
    timeout: float | None = None


@dataclass(frozen=True)
class FilterConfig:
    """すべての自動マウントインターセプター（フィルター）構成の基底クラス"""

    shortcut_name: ClassVar[str] = ""
    dotted_path: ClassVar[str] = ""
    enabled: bool = True
    order: int = 50


@dataclass(frozen=True)
class CookieManagementConfig(FilterConfig):
    """状態維持（有状態セッション）を自動化するクッキー管理フィルター設定"""

    shortcut_name: ClassVar[str] = "cookie_management"
    dotted_path: ClassVar[str] = "webclient.filter.CookieManagementFilter"
    order: int = 40


@dataclass(frozen=True)
class RetryConfig(FilterConfig):
    """一時的なネットワーク障害を自動救済するインテリジェントリトライ設定"""

    shortcut_name: ClassVar[str] = "retry"
    dotted_path: ClassVar[str] = "webclient.filter.RetryFilter"
    order: int = 30
    max_attempts: int = 3
    backoff_factor: float = 0.5


@dataclass(frozen=True)
class LoggingConfig(FilterConfig):
    """リクエスト・レスポンスの核心コンテキストを透過追跡する可視化フィルター設定"""

    shortcut_name: ClassVar[str] = "logging"
    dotted_path: ClassVar[str] = "webclient.filter.LoggingFilter"
    order: int = 20
    show_request_headers: bool = True
    show_response_headers: bool = True
    show_request_body: bool = True
    show_response_body: bool = True
    max_html_body_length: int = 200


@dataclass(frozen=True)
class WebClientConfig:
    """YAMLと1対1でマッピングされる、不変でスレッドセーフな最上位構成ルートモデル"""

    connector: ConnectorConfig = field(default_factory=HttpxConfig)
    base_url: str = ""
    api_version: str = ""
    timeout: float | None = None
    default_headers: Mapping[str, str] = field(default_factory=dict)
    default_cookies: Mapping[str, str] = field(default_factory=dict)
    cookie_management: CookieManagementConfig = field(default_factory=CookieManagementConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    all_filters: Sequence[FilterConfig] = field(default_factory=list)

    @classmethod
    def from_yaml(
        cls,
        file_path: str | Path,
        /,
        *,
        custom_configs: Sequence[type[FilterConfig]] = (),
        custom_connectors: Sequence[type[ConnectorConfig]] = (),
    ) -> WebClientConfig:
        """YAMLから構成セクション（connector, filters）を厳密にオブジェクトマッピングし、オートコンフィグを駆動します"""
        try:
            import yaml
        except ImportError as err:
            raise ImportError("YAMLファイルの解析には 'pyyaml' パッケージが必要です。") from err

        path = Path(file_path)
        if not path.exists():
            return cls()

        with path.open(encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        config_data = raw_data.get("webclient", {})
        raw_timeout = config_data.get("timeout")
        timeout_val = float(raw_timeout) if raw_timeout is not None else None

        # 1. コネクターレジストリの構築とマッピング
        connector_registry: dict[str, type[ConnectorConfig]] = {
            HttpxConfig.shortcut_name: HttpxConfig,
            CurlCffiConfig.shortcut_name: CurlCffiConfig,
        }
        for c_conn in custom_connectors:
            if c_conn.shortcut_name:
                connector_registry[c_conn.shortcut_name] = c_conn

        raw_connector = config_data.get("connector", "auto")
        chosen_connector_config: ConnectorConfig | None = None

        if isinstance(raw_connector, dict):
            for shortcut, props in raw_connector.items():
                conn_class = connector_registry.get(shortcut)
                if conn_class is not None:
                    valid_props = {k: v for k, v in (props or {}).items() if not k.startswith("_")}
                    chosen_connector_config = conn_class(**valid_props)
                    break
        elif isinstance(raw_connector, str):
            shortcut = raw_connector.lower()
            if shortcut == "auto":
                if importlib.util.find_spec("httpx") is not None:
                    chosen_connector_config = HttpxConfig()
                elif importlib.util.find_spec("curl_cffi") is not None:
                    chosen_connector_config = CurlCffiConfig()
            else:
                conn_class = connector_registry.get(shortcut)
                if conn_class is not None:
                    chosen_connector_config = conn_class()

        if chosen_connector_config is None:
            chosen_connector_config = HttpxConfig()

        # 2. フィルターマッピングレジストリの構築
        filters_section = config_data.get("filters") or {}
        if not isinstance(filters_section, dict):
            filters_section = {}

        filter_registry: dict[str, type[FilterConfig]] = {
            CookieManagementConfig.shortcut_name: CookieManagementConfig,
            RetryConfig.shortcut_name: RetryConfig,
            LoggingConfig.shortcut_name: LoggingConfig,
        }
        for c_meta in custom_configs:
            if c_meta.shortcut_name:
                filter_registry[c_meta.shortcut_name] = c_meta

        # あらかじめ組み込みの3つのコアフィルターをデフォルト値で初期化マップに展開（オートコンフィグ）
        filter_instances: dict[str, FilterConfig] = {
            CookieManagementConfig.shortcut_name: CookieManagementConfig(),
            RetryConfig.shortcut_name: RetryConfig(),
            LoggingConfig.shortcut_name: LoggingConfig(),
        }

        # YAMLに明示的なオーバーライド記述がある場合は、そのパラメータでプロパティを上書き
        for shortcut, config_props in filters_section.items():
            if not isinstance(config_props, dict):
                config_props = {}
            config_class = filter_registry.get(shortcut)
            if config_class is None:
                continue

            valid_props = {k: v for k, v in config_props.items() if not k.startswith("_")}
            filter_instances[shortcut] = config_class(**valid_props)

        cookie_cfg = cast(CookieManagementConfig, filter_instances[CookieManagementConfig.shortcut_name])
        retry_cfg = cast(RetryConfig, filter_instances[RetryConfig.shortcut_name])
        log_cfg = cast(LoggingConfig, filter_instances[LoggingConfig.shortcut_name])

        return cls(
            connector=chosen_connector_config,
            base_url=str(config_data.get("base_url", "")),
            api_version=str(config_data.get("api_version", "")),
            timeout=timeout_val,
            default_headers=cast(Mapping[str, str], config_data.get("default_headers", {})),
            default_cookies=cast(Mapping[str, str], config_data.get("default_cookies", {})),
            cookie_management=cookie_cfg,
            retry=retry_cfg,
            logging=log_cfg,
            all_filters=list(filter_instances.values()),
        )
