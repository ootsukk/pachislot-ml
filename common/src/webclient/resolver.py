from __future__ import annotations

from functools import singledispatchmethod
import importlib
import importlib.metadata
import inspect
import logging
import operator
import pkgutil
import re
import sys
from collections.abc import Callable, Sequence
from graphlib import TopologicalSorter
from importlib.metadata import entry_points
from typing import Any, cast

from webclient.base import (
    ClientHttpConnector,
    Configurable,
    ExchangeFilter,
    PrioritizedFilter,
    ProxyOptions,
    RedirectOptions,
)
from webclient.codec import BodyDecoder, BodyEncoder
from webclient.component import Component, InstanceComponent, PluginComponent, PluginListComponent, PropertyComponent
from webclient.config import WebClientConfig
from webclient.constants import ENTRY_POINT_TARGET, ROOT_PACKAGE_NAME
from webclient.cookies import CookieStore

# 本モジュール専用のロガーの捕捉
_LOGGER = logging.getLogger(f"{ROOT_PACKAGE_NAME}.resolver")

class TypedAsset[T]:

    def __init__(self, spec_type: type[T], asset: T, name: str | None = None) -> None:
        self._spec_type = spec_type
        self._asset = asset
        self._name = name

    @property
    def spec_type(self) -> type[T]:
        return self._spec_type

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def value(self) -> T:
        return self._asset


class TypedAssetContext:

    def __init__(self, components: list[Component]) -> None:
        self._type_pool: dict[type, TypedAsset[Any]] = {}
        self._name_pool: dict[str, TypedAsset[Any]] = {}
        self._components = components

    def register_asset(self, typed_asset: TypedAsset[Any]) -> None:
        self._type_pool[typed_asset.spec_type] = typed_asset
        if typed_asset.name:
            self._name_pool[typed_asset.name] = typed_asset

    def register[T](self, spec_type: type[T], asset: object, name: str | None = None) -> None:
        self.register_asset(TypedAsset(spec_type, asset, name=name))

    def fetch[T](self, spec_type: type[T]) -> T:
        return self._type_pool[spec_type].value

    def fetch_optional[T](self, spec_type: type[T]) -> T | None:
        asset_wrapper = self._type_pool.get(spec_type)
        return asset_wrapper.value if asset_wrapper is not None else None

    def fetch_sequence[T](self, element_type: type[T]) -> Sequence[T]:
        for c in self._components:
            asset_wrapper = self._type_pool.get(c.plugin_spec_type)
            if asset_wrapper is not None:
                return asset_wrapper.value
        return []

    def find_compatible[T](self, required_type: type[T], param_name: str | None = None) -> object | None:
        if param_name and param_name in self._name_pool:
            return self._name_pool[param_name].value

        wrapper = self._type_pool.get(cast(type, required_type))
        if wrapper is not None:
            return wrapper.value

        for pool_type, wrapper in self._type_pool.items():
            if isinstance(required_type, type) and issubclass(pool_type, required_type):
                return wrapper.value
        return None


class AssetResolver:

    def __init__(
        self,
        orchestrator_cls: type[UniversalPluginResolver],
        context: TypedAssetContext,
        config: WebClientConfig,
        provided_instances: dict[type, object],
    ) -> None:
        self.orchestrator = orchestrator_cls
        self.context = context
        self.config = config
        self.provided_instances = provided_instances

    @singledispatchmethod
    def resolve(self, component: Component) -> TypedAsset[Any] | None:
        raise NotImplementedError(
            f"【コンポーネント未調和】サポートされていない未知のコンポーネント型です: {type(component)}"
        )

    @resolve.register
    def _(self, component: InstanceComponent) -> TypedAsset[Any] | None:
        if component.target_type is WebClientConfig:
            return TypedAsset(component.target_type, self.config)
        real_obj = self.provided_instances.get(component.target_type)
        if real_obj is not None:
            return TypedAsset(component.target_type, real_obj)
        return None

    @resolve.register
    def _(self, component: PropertyComponent) -> TypedAsset[Any] | None:
        if not hasattr(self.config, component.key):
            if component.mandatory:
                raise LookupError(f"必須プロパティキー '{component.key}' が存在しません。")
            return None
        val = getattr(self.config, component.key)
        if val is None and component.mandatory:
            raise LookupError(f"必須プロパティキー '{component.key}' の値が空です。")
        return TypedAsset(component.target_type, val, name=component.key)

    @resolve.register
    def _(self, component: PluginComponent) -> TypedAsset[Any] | None:
        return self.orchestrate_flat_resolution(component)

    @resolve.register
    def _(self, component: PluginListComponent) -> TypedAsset[Any] | None:
        raw_list = getattr(self.config, component.key, None)
        if not raw_list:
            if component.mandatory:
                raise LookupError(f"必須構成リストキー '{component.key}' が存在しません。")
            return TypedAsset(component.target_type, [])

        resolved_elements: list[Any] = []
        for item in raw_list:
            asset = self.orchestrate_flat_resolution(component.nested_component, raw_setting=item)
            if asset is not None:
                resolved_elements.append(asset.value)

        if component.ordered:
            resolved_elements.sort(
                key=lambda x: getattr(
                    x, "priority", getattr(getattr(x, "__plugin_impl_meta__", None), "priority", 100)
                ),
                reverse=True,
            )
        return TypedAsset(component.target_type, resolved_elements)

    def orchestrate_flat_resolution(
        self,
        component: Component,
        raw_setting: dict[str, object] | str | None = None,
    ) -> TypedAsset[Any] | None:
        setting_value = (
            raw_setting
            if raw_setting is not None
            else (getattr(self.config, component.key, None) if component.key else None)
        )

        spec_type = component.plugin_spec_type

        plugin_name: str | None = (
            setting_value
            if isinstance(setting_value, str)
            else (component.key if isinstance(setting_value, dict) else None)
        )

        regs = self.orchestrator._get_plugin_registry()
        spec_registry = regs.get(spec_type, {})

        impl_class = self._resolve_implementation_class(spec_registry=spec_registry, plugin_name=plugin_name)

        if impl_class is None:
            if component.mandatory:
                raise LookupError(
                    f"生存に必須な仕様 '{spec_type.__name__}' に対する "
                    f"有効な実装プラグイン（指定名: '{plugin_name}'）が環境内に見つかりません。"
                )
            return None

        config_object = self._build_configurable_options(
            component=component,
            impl_class=impl_class,
            raw_setting=setting_value,
        )

        if config_object is not None:
            config_class = self.orchestrator._extract_config_type(impl_class)
            if config_class is not None:
                self.context.register(type(config_object), config_object)
                self.context.register(config_class, config_object)

        return self._inject_dependencies_and_instantiate(
            spec_type=spec_type,
            impl_class=impl_class,
        )

    def _resolve_implementation_class(self, spec_registry: dict[str, type], plugin_name: str | None) -> type | None:
        if not plugin_name or plugin_name == "auto":
            available_classes = list(spec_registry.values())
            if not available_classes:
                return None

            available_classes.sort(key=lambda c: getattr(c.__plugin_impl_meta__, "priority", 100), reverse=True)
            return available_classes[0]

        return spec_registry.get(plugin_name)

    def _build_configurable_options(
        self,
        component: Component,
        impl_class: type,
        raw_setting: dict[str, object] | str | None,
    ) -> object | None:
        if not issubclass(impl_class, Configurable):
            return None

        config_class = self.orchestrator._extract_config_type(impl_class)
        if not config_class:
            return None

        source_input: dict[str, object] = {}
        if isinstance(raw_setting, dict):
            source_input = raw_setting
        else:
            strategy_key = component.key.replace("_name", "") + "_options"
            options_dict = getattr(self.config, strategy_key, None)
            if isinstance(options_dict, dict):
                source_input = cast(dict[str, object], options_dict)

        clean_input: dict[str, object] = {k: v for k, v in source_input.items() if not k.startswith("_")}
        options_instance = config_class(**clean_input)

        return impl_class.create_config(options_instance, type_pool=None)

    def _inject_dependencies_and_instantiate[T](self, spec_type: type[T], impl_class: type) -> TypedAsset[T]:
        sig = inspect.signature(impl_class)
        kwargs: dict[str, object] = {}

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            p_type = param.annotation
            resolved_val = self.context.find_compatible(p_type, param_name=param_name)
            if resolved_val is not None:
                kwargs[param_name] = resolved_val

        try:
            asset = impl_class(**kwargs)
        except TypeError:
            asset = impl_class()

        return TypedAsset(spec_type, asset)


class UniversalPluginResolver:
    """規約と Component の依存グラフに基づいて、一元的にDI解決を行うエンジン。"""

    _registry_cache: dict[str, dict[type, dict[str, type]]] = {}
    _DYNAMIC_COMPONENTS: list[Component] = []

    @classmethod
    def register_component(cls, component: Component) -> None:
        if component not in cls._DYNAMIC_COMPONENTS:
            cls._DYNAMIC_COMPONENTS.append(component)

    @classmethod
    def _sort_components(cls, components: list[Component]) -> list[Component]:
        component_map: dict[type, Component] = {c.target_type: c for c in components}
        graph: dict[type, list[type]] = {}

        for c in components:
            target = c.target_type
            meta = getattr(target, "__plugin_meta__", None)
            depends_on: list[type] = meta.depends_on if meta else []

            valid_dependencies: list[type] = []
            for dep in depends_on:
                is_registered = dep in component_map
                is_plugin_spec = any(xc.target_type == dep for xc in components)

                if not (is_registered or is_plugin_spec):
                    continue

                node_type = component_map[dep].target_type if dep in component_map else dep
                valid_dependencies.append(node_type)

            graph[c.target_type] = valid_dependencies

        ts = TopologicalSorter(graph)
        sorted_specs: list[type] = list(ts.static_order())
        return [component_map[spec] for spec in sorted_specs if spec in component_map]

    @classmethod
    def resolve_all(
        cls,
        config: WebClientConfig,
        provided_instances: dict[type, object],
    ) -> TypedAssetContext:
        ordered_components = config._ordered_components
        context = TypedAssetContext(ordered_components)

        resolver_worker = AssetResolver(cls, context, config, provided_instances)

        for component in ordered_components:
            typed_asset = resolver_worker.resolve(component)
            if typed_asset is not None:
                context.register_asset(typed_asset)

        return context

    @classmethod
    def _get_plugin_registry(cls) -> dict[type, dict[str, type]]:
        from webclient.component import get_all_active_components

        plugin_groups = [f"{ROOT_PACKAGE_NAME}.plugins"]

        cache_key = "|".join(sorted(plugin_groups))
        if cache_key in cls._registry_cache:
            return {spec: slot.copy() for spec, slot in cls._registry_cache[cache_key].items()}

        cls._load_external_extension_components()

        new_registries: dict[type, dict[str, type]] = {}

        for c in get_all_active_components():
            new_registries[c.plugin_spec_type] = {}

        cls._scan_internal_packages(plugin_groups, new_registries)
        cls._scan_external_entry_points(plugin_groups, new_registries)

        cls._registry_cache[cache_key] = new_registries
        return {spec: slot.copy() for spec, slot in new_registries.items()}

    @classmethod
    def _load_external_extension_components(cls) -> None:
        for ep in entry_points(group=f"{ROOT_PACKAGE_NAME}.components"):
            try:
                ep.load()
            except Exception as err:
                _LOGGER.warning(f"外部コンポーネントのロードに失敗しました (EntryPt: {ep.name}): {err}")

    @classmethod
    def _scan_internal_packages(cls, plugin_groups: list[str], registries: dict[type, dict[str, type]]) -> None:
        root_module = sys.modules.get(ROOT_PACKAGE_NAME)
        if not (root_module and hasattr(root_module, "__path__")):
            return

        for module_info in pkgutil.walk_packages(root_module.__path__, root_module.__name__ + "."):
            try:
                mod = importlib.import_module(module_info.name)
                for _, cls_obj in inspect.getmembers(mod, inspect.isclass):
                    if not cls_obj.__module__.startswith(f"{ROOT_PACKAGE_NAME}."):
                        continue
                    cls._classify_and_register(cls_obj, registries)
            except ImportError:
                continue

    @classmethod
    def _scan_external_entry_points(cls, plugin_groups: list[str], registries: dict[type, dict[str, type]]) -> None:
        for group_name in plugin_groups:
            try:
                for ep in entry_points(group=group_name):
                    cls._classify_and_register(ep.load(), registries)
            except Exception as err:
                _LOGGER.warning(f"プラグイングループのロード中にエラーが発生しました (Group: {group_name}): {err}")
                continue

    @classmethod
    def _classify_and_register(cls, cls_obj: type, registries: dict[type, dict[str, type]]) -> None:
        if inspect.isabstract(cls_obj):
            return

        for spec_type in registries:
            if cls_obj is spec_type or not issubclass(cls_obj, spec_type):
                continue

            impl_meta = getattr(cls_obj, "__plugin_impl_meta__", None)
            if not impl_meta:
                raise TypeError(
                    f"【厳格規約違反】具象実装クラス '{cls_obj.__name__}' に "
                    f"@plugin_impl デコレータが付与されていません。義務付けられています。"
                )

            if cls_obj.__name__ == "ClientHttpConnector" or any(
                base.__name__ == "ClientHttpConnector" for base in cls_obj.__mro__
            ):
                if not cls._is_connector_dependency_satisfied(cls_obj):
                    return

            registries[spec_type][impl_meta.value] = cls_obj
            break

    @classmethod
    def _is_connector_dependency_satisfied(cls, connector_class: type) -> bool:
        dep_meta = getattr(connector_class, "__dependency_meta__", None)
        if not dep_meta:
            raise TypeError(
                f"【厳格規約違反】コネクター具象クラス '{connector_class.__name__}' に "
                f"@dependency_module デコレータが付与されていません。義務付けられています。"
            )

        try:
            for dist in importlib.metadata.distributions():
                normalized_dist_name = dist.metadata["Name"].lower().replace("-", "_")
                normalized_target_name = dep_meta.module_name.lower().replace("-", "_")

                if normalized_dist_name == normalized_target_name and cls.evaluate_version(
                    dist.version, dep_meta.version
                ):
                    return True
        except Exception:
            return False

        return False

    @classmethod
    def _extract_config_type(cls, impl_class: type) -> type | None:
        """具象クラスが Configurable[T] を継承している場合、その型引数 T を安全に抽出します。"""
        orig_bases = getattr(impl_class, "__orig_bases__", [])
        for base in orig_bases:
            origin = getattr(base, "__origin__", None)

            if origin is not None and getattr(origin, "__name__", None) == "Configurable":
                args = getattr(base, "__args__", None)
                if args and isinstance(args[0], type):
                    return args[0]

        return None

    @classmethod
    def evaluate_version(cls, actual_version: str, constraint_expr: str) -> bool:
        match = re.match(r"^([>=<!]+)\s*([\d.]+)", constraint_expr.strip())
        if not match:
            return True

        op_str, required_str = match.groups()

        actual_parts = [int(x) for x in re.findall(r"\d+", actual_version)]
        required_parts = [int(x) for x in re.findall(r"\d+", required_str)]

        max_len = max(len(actual_parts), len(required_parts))
        actual_parts += [0] * (max_len - len(actual_parts))
        required_parts += [0] * (max_len - len(required_parts))

        version_operators: dict[str, Callable[[list[int], list[int]], bool]] = {
            "==": operator.eq,
            ">=": operator.ge,
            "<=": operator.le,
            ">": operator.gt,
            "<": operator.lt,
        }

        comp_func = version_operators.get(op_str)
        if comp_func:
            return comp_func(actual_parts, required_parts)

        return False