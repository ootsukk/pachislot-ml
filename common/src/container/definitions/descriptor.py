from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from container.common.constants import (
    YAML_KEY_ENABLED,
    YAML_KEY_PLUGIN_NAME,
    YAML_VAL_AUTO,
)
from container.definitions.naming import NamingStrategy


class PluginDescriptor:
    """生の設定データのトポロジーをカプセル化し、一貫したドメインセマンティクスを提供する不変の値オブジェクト。"""

    def __init__(
        self,
        raw_value: object,
        strategy: NamingStrategy,
        /,
        *,
        options_key: str | None = None,
    ) -> None:
        self._raw_value: Final[object] = raw_value
        self._strategy: Final[NamingStrategy] = strategy

        match raw_value:
            case None:
                self._plugin_name: str = YAML_VAL_AUTO
                self._enabled: bool = True
                self._options_dict: dict[str, object] = {}
            case str() as s:
                resolved_name = strategy.get_key(dynamic_name=s)
                self._plugin_name = resolved_name if resolved_name is not None else YAML_VAL_AUTO
                self._enabled = True
                self._options_dict = {}
            case Mapping() as m:
                config_map: dict[str, object] = {str(k): v for k, v in m.items()}

                enabled_raw = config_map.get(YAML_KEY_ENABLED, True)
                self._enabled = bool(enabled_raw) if isinstance(enabled_raw, bool) else True

                plugin_name_raw = config_map.get(YAML_KEY_PLUGIN_NAME)
                match plugin_name_raw:
                    case None:
                        resolved_name = strategy.get_key(dynamic_name=YAML_VAL_AUTO)
                    case _:
                        resolved_name = strategy.get_key(dynamic_name=str(plugin_name_raw))
                self._plugin_name = resolved_name if resolved_name is not None else YAML_VAL_AUTO

                target_options_key = options_key
                if target_options_key is None:
                    derived_key = strategy.get_options_key(dynamic_name=self._plugin_name)
                    if derived_key:
                        target_options_key = derived_key

                match target_options_key:
                    case str() as k if k in config_map:
                        nested_payload = config_map.get(k)
                        match nested_payload:
                            case Mapping() as nm:
                                self._options_dict = {
                                    str(nk): nv for nk, nv in nm.items() if not str(nk).startswith("_")
                                }
                            case _:
                                self._options_dict = {}
                    case _:
                        self._options_dict = {
                            k: v
                            for k, v in config_map.items()
                            if not k.startswith("_") and k not in (YAML_KEY_ENABLED, YAML_KEY_PLUGIN_NAME)
                        }
            case _:
                self._plugin_name = YAML_VAL_AUTO
                self._enabled = True
                self._options_dict = {}

    @property
    def is_empty(self) -> bool:
        return self._raw_value is None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def plugin_name(self) -> str:
        return self._plugin_name

    @property
    def options_payload(self) -> dict[str, object]:
        return self._options_dict.copy()
