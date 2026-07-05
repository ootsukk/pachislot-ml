from __future__ import annotations

import importlib
import logging
import types
from collections.abc import Mapping, Sequence
from typing import Final, cast

from container.definitions.registry import PluginDefinition, PluginRegistry

_LOGGER: Final[logging.Logger] = logging.getLogger("container.registry")


class PluginIndexSerializer:
    """PluginRegistry カタログの内容と外部保存可能インデックスデータとの相互変換を司る専任クラス。"""

    @classmethod
    def serialize(cls, registry: PluginRegistry, /) -> dict[str, dict[str, dict[str, object]]]:
        exported: dict[str, dict[str, dict[str, object]]] = {}

        for spec_path, slot in registry._registry.items():
            spec_str = f"{spec_path.__module__}.{spec_path.__name__}"
            exported[spec_str] = {}
            for name, def_obj in slot.items():
                serialized_deps: dict[str, str] = {}
                for k, v in def_obj.constructor_dependencies.items():
                    if isinstance(v, types.GenericAlias):
                        serialized_deps[k] = f"{v.__origin__.__module__}.{v.__origin__.__name__}"
                    else:
                        serialized_deps[k] = f"{v.__module__}.{v.__name__}"

                exported[spec_str][name] = {
                    PluginDefinition.FIELD_IMPL_CLASS: f"{def_obj.impl_class.__module__}.{def_obj.impl_class.__name__}",
                    PluginDefinition.FIELD_PRIORITY: def_obj.priority,
                    PluginDefinition.FIELD_CONSTRUCTOR_DEPS: serialized_deps,
                    PluginDefinition.FIELD_DEPENDS_ON: [f"{t.__module__}.{t.__name__}" for t in def_obj.depends_on],
                }
        return exported


class CacheIndexDefinitionReader:
    """事前生成された静的インデックスデータを解凍しリフレクションなしで定義をダイレクトに復元するリーダー。"""

    def __init__(
        self,
        cache_data: Mapping[str, Mapping[str, Mapping[str, object]]],
        spec_types: set[type[object]],
        ignored_types: set[type[object]],
        /,
    ) -> None:
        self._cache_data = cache_data
        self._spec_types = spec_types
        self._ignored_types = ignored_types

    def read_definitions(self) -> Sequence[PluginDefinition[object]]:
        results: list[PluginDefinition[object]] = []
        for spec_path, slot_data in self._cache_data.items():
            try:
                spec_type = self._resolve_type_path(spec_path)
            except (ImportError, AttributeError) as err:
                _LOGGER.warning("インデックス内の仕様型の復元に失敗したためスキップします: %s (%s)", spec_path, err)
                continue

            if spec_type not in self._spec_types or spec_type in self._ignored_types:
                continue

            for name, def_data in slot_data.items():
                try:
                    impl_class = self._resolve_type_path(str(def_data.get(PluginDefinition.FIELD_IMPL_CLASS)))

                    raw_deps = def_data.get(PluginDefinition.FIELD_CONSTRUCTOR_DEPS)
                    dependencies: dict[str, type[object] | types.GenericAlias] = {}
                    if isinstance(raw_deps, Mapping):
                        for param_name, type_path in raw_deps.items():
                            dependencies[str(param_name)] = self._resolve_type_path(str(type_path))

                    raw_depends = def_data.get(PluginDefinition.FIELD_DEPENDS_ON)
                    depends_on_list: list[type[object]] = []
                    if isinstance(raw_depends, Sequence):
                        for dep_path in raw_depends:
                            depends_on_list.append(self._resolve_type_path(str(dep_path)))

                    results.append(
                        PluginDefinition(
                            spec_type=spec_type,
                            impl_class=impl_class,
                            plugin_name=name,
                            priority=cast(int, def_data.get(PluginDefinition.FIELD_PRIORITY, 100)),
                            constructor_dependencies=dependencies,
                            depends_on=tuple(depends_on_list),
                        )
                    )
                except (ImportError, AttributeError) as err:
                    _LOGGER.warning("インデックスからのプラグイン要素の復元に失敗しました: %s (%s)", name, err)
                    continue
        return results

    def _resolve_type_path(self, type_path: str, /) -> type[object]:
        module_part, class_part = type_path.rsplit(".", 1)
        mod = importlib.import_module(module_part)
        resolved_class = getattr(mod, class_part)
        if isinstance(resolved_class, type):
            return resolved_class
        raise AttributeError(f"指定されたパスはクラスオブジェクトではありません: {type_path}")
