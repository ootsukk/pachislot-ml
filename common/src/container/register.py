from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import pkgutil
import sys
import types
import typing
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Final, Protocol, cast
from pydantic import BaseModel

from container.component import Component
from container.constants import ENTRY_POINT_SUFFIX

_LOGGER = logging.getLogger("container.registry")


@dataclass(frozen=True)
class PluginDefinition[T]:
    """特定のインターフェースに対するプラグイン実装のメタデータおよび依存関係を保持する定義書。"""

    FIELD_SPEC_TYPE: ClassVar[str] = "spec_type"
    FIELD_IMPL_CLASS: ClassVar[str] = "impl_class"
    FIELD_PLUGIN_NAME: ClassVar[str] = "plugin_name"
    FIELD_PRIORITY: ClassVar[str] = "priority"
    FIELD_CONFIG_CLASS: ClassVar[str] = "config_class"
    FIELD_CONSTRUCTOR_DEPS: ClassVar[str] = "constructor_dependencies"
    FIELD_DEPENDS_ON: ClassVar[str] = "depends_on"

    META_ATTR_CONTAINER: ClassVar[str] = "__plugin_impl_meta__"
    META_PROP_VALUE: ClassVar[str] = "value"

    spec_type: type[T]
    impl_class: type[T]
    plugin_name: str
    priority: int = 100
    config_class: type[object] | None = None
    constructor_dependencies: dict[str, type[object]] = field(default_factory=dict)
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
                    PluginDefinition.FIELD_CONFIG_CLASS: f"{def_obj.config_class.__module__}.{def_obj.config_class.__name__}"
                    if def_obj.config_class
                    else None,
                    PluginDefinition.FIELD_DEPENDS_ON: [f"{t.__module__}.{t.__name__}" for t in def_obj.depends_on],
                }
            summary[spec_name] = plugin_info
        return summary


class PluginIndexSerializer:
    """PluginRegistry カタログの内容と外部保存可能インデックスデータとの相互変換を司る専任クラス。"""

    @classmethod
    def serialize(cls, registry: PluginRegistry, /) -> dict[str, dict[str, dict[str, object]]]:
        exported: dict[str, dict[str, dict[str, object]]] = {}

        for spec_path, slot in registry._registry.items():
            spec_str = f"{spec_path.__module__}.{spec_path.__name__}"
            exported[spec_str] = {}
            for name, def_obj in slot.items():
                exported[spec_str][name] = {
                    PluginDefinition.FIELD_IMPL_CLASS: f"{def_obj.impl_class.__module__}.{def_obj.impl_class.__name__}",
                    PluginDefinition.FIELD_PRIORITY: def_obj.priority,
                    PluginDefinition.FIELD_CONFIG_CLASS: f"{def_obj.config_class.__module__}.{def_obj.config_class.__name__}"
                    if def_obj.config_class
                    else None,
                    PluginDefinition.FIELD_CONSTRUCTOR_DEPS: {
                        k: f"{v.__module__}.{v.__name__}" for k, v in def_obj.constructor_dependencies.items()
                    },
                    PluginDefinition.FIELD_DEPENDS_ON: [f"{t.__module__}.{t.__name__}" for t in def_obj.depends_on],
                }
        return exported


class PluginClassDetector(Protocol):
    """環境内からプラグイン候補となる生の具象クラスを探索・検出するための抽象ソースインターフェース。"""

    def detect(self) -> Iterable[type[object]]: ...


class InternalPackageDetector:
    """指定された複数の内部パッケージ空間群を巡回し配下のモジュールからクラスを検出するデテクター。"""

    def __init__(self, root_package_names: Sequence[str], /) -> None:
        self._root_package_names = root_package_names

    def detect(self) -> Iterable[type[object]]:
        for root_package_name in self._root_package_names:
            try:
                root_module = sys.modules.get(root_package_name) or importlib.import_module(root_package_name)
            except ImportError as err:
                _LOGGER.warning("ターゲットパッケージの動的インポートに失敗しました: %s (%s)", root_package_name, err)
                continue

            if not hasattr(root_module, "__path__"):
                continue

            for module_info in pkgutil.walk_packages(root_module.__path__, root_module.__name__ + "."):
                try:
                    mod = importlib.import_module(module_info.name)
                    for _, cls_obj in inspect.getmembers(mod, inspect.isclass):
                        is_target_package = any(
                            cls_obj.__module__.startswith(f"{pkg}.") for pkg in self._root_package_names
                        )
                        if not is_target_package:
                            continue
                        yield cls_obj
                except ImportError:
                    continue


class ExternalEntryPointDetector:
    """Python環境のパッケージメタデータに登録された外部エントリーポイントからクラスを検出するデテクター。"""

    def __init__(self, plugin_groups: Sequence[str], /) -> None:
        self._plugin_groups = plugin_groups

    def detect(self) -> Iterable[type[object]]:
        try:
            from importlib.metadata import entry_points

            for group_name in self._plugin_groups:
                try:
                    for ep in entry_points(group=group_name):
                        yield ep.load()
                except Exception as err:
                    _LOGGER.warning("外部プラグインのロードに失敗しました: %s (%s)", group_name, err)
        except Exception:
            pass


class PluginDefinitionReader(Protocol):
    """プラグイン定義のストリームを生成しレジストリへ直接供給する最上位の読み込みインターフェース。"""

    def read_definitions(self) -> Iterable[PluginDefinition[object]]: ...


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

    def read_definitions(self) -> Iterable[PluginDefinition[object]]:
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
                    config_class_path = def_data.get(PluginDefinition.FIELD_CONFIG_CLASS)
                    config_class = self._resolve_type_path(str(config_class_path)) if config_class_path else None

                    raw_deps = def_data.get(PluginDefinition.FIELD_CONSTRUCTOR_DEPS)
                    dependencies: dict[str, type[object]] = {}
                    if isinstance(raw_deps, Mapping):
                        for param_name, type_path in raw_deps.items():
                            dependencies[str(param_name)] = self._resolve_type_path(str(type_path))

                    raw_depends = def_data.get(PluginDefinition.FIELD_DEPENDS_ON)
                    depends_on_list: list[type[object]] = []
                    if isinstance(raw_depends, Sequence):
                        for dep_path in raw_depends:
                            depends_on_list.append(self._resolve_type_path(str(dep_path)))

                    yield PluginDefinition(
                        spec_type=spec_type,
                        impl_class=impl_class,
                        plugin_name=name,
                        priority=cast(int, def_data.get(PluginDefinition.FIELD_PRIORITY, 100)),
                        config_class=config_class,
                        constructor_dependencies=dependencies,
                        depends_on=tuple(depends_on_list),
                    )
                except (ImportError, AttributeError) as err:
                    _LOGGER.warning("インデックスからのプラグイン要素の復元に失敗しました: %s (%s)", name, err)
                    continue

    def _resolve_type_path(self, type_path: str, /) -> type[object]:
        module_part, class_part = type_path.rsplit(".", 1)
        mod = importlib.import_module(module_part)
        resolved_class = getattr(mod, class_part)
        if isinstance(resolved_class, type):
            return resolved_class
        raise AttributeError(f"指定されたパスはクラスオブジェクトではありません: {type_path}")


class DynamicScanDefinitionReader:
    """複数のパッケージ空間群をマルチ巡回しリフレクション分析によって動的に定義をビルドする標準リーダー。"""

    def __init__(
        self,
        root_package_names: Sequence[str],
        plugin_groups: Sequence[str],
        spec_types: set[type[object]],
        ignored_types: set[type[object]],
        /,
    ) -> None:
        self._root_package_names = root_package_names
        self._spec_types = spec_types
        self._ignored_types = ignored_types
        self._detectors: Sequence[PluginClassDetector] = [
            InternalPackageDetector(root_package_names),
            ExternalEntryPointDetector(plugin_groups),
        ]

    def read_definitions(self) -> Iterable[PluginDefinition[object]]:
        for root_package_name in self._root_package_names:
            self._load_external_extension_components(root_package_name)

        merged_raw: dict[type[object], dict[str, type[object]]] = {
            spec: {} for spec in self._spec_types if spec not in self._ignored_types
        }

        for detector in self._detectors:
            for discovered_class in detector.detect():
                self._classify_and_register(discovered_class, merged_raw)

        for spec_type, slot in merged_raw.items():
            for name, impl_class in slot.items():
                sig = inspect.signature(impl_class)
                dependencies: dict[str, type[object]] = {}
                detected_config_class: type[object] | None = None

                for param_name, param in sig.parameters.items():
                    if param_name in ("self", "cls"):
                        continue

                    p_type = self._extract_core_type(param.annotation)
                    if p_type is None:
                        continue

                    dependencies[param_name] = p_type

                    is_pydantic_model = issubclass(p_type, BaseModel)
                    is_std_dataclass = dataclasses.is_dataclass(p_type)
                    is_custom_type = p_type.__module__ != "builtins" and p_type not in self._spec_types

                    if is_pydantic_model or is_std_dataclass or is_custom_type:
                        detected_config_class = p_type

                priority = 100
                depends_on_tuple: tuple[type[object], ...] = ()

                match getattr(impl_class, PluginDefinition.META_ATTR_CONTAINER, None):
                    case object() as impl_meta:
                        match getattr(impl_meta, PluginDefinition.FIELD_PRIORITY, None):
                            case int() | str() as p_val:
                                priority = int(p_val)
                        match getattr(impl_meta, PluginDefinition.FIELD_DEPENDS_ON, None):
                            case list() | tuple() | set() as raw_depends:
                                depends_on_tuple = tuple(raw_depends)
                            case type() as raw_depends:
                                depends_on_tuple = (raw_depends,)

                yield PluginDefinition(
                    spec_type=spec_type,
                    impl_class=impl_class,
                    plugin_name=name,
                    priority=priority,
                    config_class=detected_config_class,
                    constructor_dependencies=dependencies,
                    depends_on=depends_on_tuple,
                )

    def _load_external_extension_components(self, root_package_name: str) -> None:
        try:
            from importlib.metadata import entry_points

            for ep in entry_points(group=f"{root_package_name}.{ENTRY_POINT_SUFFIX}"):
                try:
                    ep.load()
                except Exception as err:
                    _LOGGER.warning("外部拡張コンポーネントのロードに失敗しました: %s (%s)", root_package_name, err)
        except Exception:
            pass

    def _classify_and_register(
        self, cls_obj: type[object], registries: dict[type[object], dict[str, type[object]]]
    ) -> None:
        if inspect.isabstract(cls_obj):
            return
        for spec_type in registries:
            if cls_obj is spec_type or not issubclass(cls_obj, spec_type):
                continue

            key = cls_obj.__name__.lower()
            match getattr(cls_obj, PluginDefinition.META_ATTR_CONTAINER, None):
                case object() as impl_meta:
                    match getattr(impl_meta, PluginDefinition.META_PROP_VALUE, None):
                        case str() | int() as meta_val:
                            key = str(meta_val)

            if key in registries[spec_type]:
                existing_cls = registries[spec_type][key]
                if existing_cls is not cls_obj:
                    _LOGGER.warning(
                        "プラグイン名の衝突を検出しました。既存の実装 '%s.%s' は、新規実装 '%s.%s' により上書きされます。仕様型: %s, キー: %s",
                        existing_cls.__module__,
                        existing_cls.__name__,
                        cls_obj.__module__,
                        cls_obj.__name__,
                        spec_type.__name__,
                        key,
                    )

            registries[spec_type][key] = cls_obj

    @classmethod
    def _extract_core_type(cls, annotation: object, /) -> type[object] | None:
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)

        if origin is typing.Union or isinstance(annotation, types.UnionType):
            for arg in args:
                if arg is type(None):
                    continue
                unwrapped = cls._extract_core_type(arg)
                if unwrapped is not None:
                    return unwrapped
            return None

        if origin is not None:
            if isinstance(origin, type):
                return origin
            return None

        if isinstance(annotation, type):
            return annotation

        return None


class PluginScanner:
    """複数パッケージのマルチリーダ戦略を切り替えて起動しカタログオブジェクトをビルドする主走査クラス。"""

    def __init__(
        self,
        root_package_names: Sequence[str],
        plugin_groups: Sequence[str],
        components: Sequence[Component[object]],
        /,
        *,
        ignored_types: Sequence[type[object]] = (),
    ) -> None:
        self._root_package_names = root_package_names
        self._plugin_groups = plugin_groups
        self._ignored_types = set(ignored_types)

        self._spec_types: set[type[object]] = set()
        for c in components:
            if hasattr(c, "plugin_spec_type"):
                self._spec_types.add(c.plugin_spec_type)
            self._spec_types.add(c.target_type)

        for t in ignored_types:
            self._spec_types.add(t)

    def scan(self, cache_index_data: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None) -> PluginRegistry:
        reader: PluginDefinitionReader

        if cache_index_data is not None:
            reader = CacheIndexDefinitionReader(cache_index_data, self._spec_types, self._ignored_types)
        else:
            reader = DynamicScanDefinitionReader(
                self._root_package_names, self._plugin_groups, self._spec_types, self._ignored_types
            )

        registry_map: dict[type[object], dict[str, PluginDefinition[object]]] = {
            spec: {} for spec in self._spec_types if spec not in self._ignored_types
        }

        for definition in reader.read_definitions():
            registry_map[definition.spec_type][definition.plugin_name] = definition

        return PluginRegistry(registry_map)