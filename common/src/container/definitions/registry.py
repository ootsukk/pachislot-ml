from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class PluginDefinition[T]:
    """特定のインターフェースに対するプラグイン実装のメタデータおよび依存関係を保持する不変の定義書。"""

    FIELD_SPEC_TYPE: ClassVar[str] = "spec_type"
    FIELD_IMPL_CLASS: ClassVar[str] = "impl_class"
    FIELD_PLUGIN_NAME: ClassVar[str] = "plugin_name"
    FIELD_PRIORITY: ClassVar[str] = "priority"
    FIELD_CONSTRUCTOR_DEPS: ClassVar[str] = "constructor_dependencies"
    FIELD_DEPENDS_ON: ClassVar[str] = "depends_on"

    META_ATTR_CONTAINER: ClassVar[str] = "__plugin_impl_meta__"
    META_PROP_VALUE: ClassVar[str] = "value"

    spec_type: type[T]
    impl_class: type[T]
    plugin_name: str
    priority: int = 100
    constructor_dependencies: dict[str, type[object] | types.GenericAlias] = field(default_factory=dict)
    depends_on: tuple[type[object], ...] = field(default_factory=tuple)


class PluginRegistry:
    """スキャン済みのプラグイン実装定義を集中管理し検索を提供するカタログクラス。"""

    def __init__(self, registry_map: Mapping[type[object], Mapping[str, PluginDefinition[object]]], /) -> None:
        self._registry: dict[type[object], dict[str, PluginDefinition[object]]] = {
            spec: dict(slot) for spec, slot in registry_map.items()
        }

    def get_definition(self, spec_type: type[object], plugin_name: str, /) -> PluginDefinition[object] | None:
        return self._registry.get(spec_type, {}).get(plugin_name)

    def get_all_definitions(self, spec_type: type[object], /) -> list[PluginDefinition[object]]:
        return list(self._registry.get(spec_type, {}).values())

    def get_registration_summary(self) -> dict[str, dict[str, dict[str, object]]]:
        summary: dict[str, dict[str, dict[str, object]]] = {}
        for spec_type, slot in self._registry.items():
            spec_name = f"{spec_type.__module__}.{spec_type.__name__}"
            plugin_info: dict[str, dict[str, object]] = {}

            for name, def_obj in slot.items():
                plugin_info[name] = {
                    PluginDefinition.FIELD_IMPL_CLASS: f"{def_obj.impl_class.__module__}.{def_obj.impl_class.__name__}",
                    PluginDefinition.FIELD_PRIORITY: def_obj.priority,
                    PluginDefinition.FIELD_DEPENDS_ON: [f"{t.__module__}.{t.__name__}" for t in def_obj.depends_on],
                }
            summary[spec_name] = plugin_info
        return summary
