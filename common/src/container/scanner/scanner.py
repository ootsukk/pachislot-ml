from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import sys
import types
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Protocol

from container.common.constants import ENTRY_POINT_SUFFIX
from container.definitions.component import Component
from container.definitions.registry import PluginDefinition, PluginRegistry
from container.definitions.resolvable import ResolvableType
from container.scanner.serializer import CacheIndexDefinitionReader

_LOGGER: Final[logging.Logger] = logging.getLogger("container.scanner")


class PluginClassDetector(Protocol):
    """環境内からプラグイン候補となる生の具象クラスを探索・検出するための抽象ソースインターフェース。"""

    def detect(self) -> Iterable[type[object]]: ...


class InternalPackageDetector:
    """指定された複数の内部パッケージ空間群を巡回し配下のモジュールからクラスを検出するデテクター。"""

    def __init__(self, package_names: Sequence[str], /) -> None:
        self._root_package_names = package_names

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
                except (ImportError, AttributeError, ValueError) as err:
                    _LOGGER.warning("外部プラグインのロードに失敗しました: %s (%s)", group_name, err)
        except ImportError:
            pass


class PluginDefinitionReader(Protocol):
    """プラグイン定義のストリームを生成しレジストリへ直接供給する最上位の読み込みインターフェース。"""

    def read_definitions(self) -> Iterable[PluginDefinition[object]]: ...


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
                dependencies: dict[str, type[object] | types.GenericAlias] = {}

                for param_name, param in sig.parameters.items():
                    if param_name in ("self", "cls"):
                        continue

                    resolvable = ResolvableType.from_annotation(param.annotation)
                    if resolvable is None:
                        continue

                    dependencies[param_name] = resolvable.raw_type

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
                    constructor_dependencies=dependencies,
                    depends_on=depends_on_tuple,
                )

    def _load_external_extension_components(self, root_package_name: str) -> None:
        try:
            from importlib.metadata import entry_points

            for ep in entry_points(group=f"{root_package_name}.{ENTRY_POINT_SUFFIX}"):
                try:
                    ep.load()
                except (ImportError, AttributeError, ValueError) as err:
                    _LOGGER.warning("外部拡張コンポーネントのロードに失敗しました: %s (%s)", root_package_name, err)
        except ImportError:
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
