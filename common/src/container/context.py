from __future__ import annotations

import contextlib
import dataclasses
import logging
import types
import typing
from collections.abc import Mapping, Sequence
from functools import singledispatchmethod
from threading import Lock
from pydantic import BaseModel

from container.component import (
    Component,
    InstanceComponent,
    PluginComponent,
    PluginListComponent,
    PluginSetting,
    PropertyComponent,
)
from container.constants import ComponentScope
from container.interfaces import (
    ApplicationContext,
    BeanPostProcessor,
    Initializable,
)
from container.register import PluginDefinition, PluginRegistry

_LOGGER = logging.getLogger("container.context")


class ContainerError(Exception):
    """DIコンテナ層におけるすべての例外の基底クラス。"""


class CircularDependencyError(ContainerError):
    """コンポーネントの依存関係グラフに閉路（循環参照）が検出された際の例外。"""


class ComponentInstantiationError(ContainerError):
    """オブジェクトの動的生成または設定値のインジェクションに失敗した際の例外。"""


class DefaultInstantiationStrategy:
    """具象プラグイン実装クラスをキーワード引数に基づいて物理実体化する標準生成戦略。"""

    def instantiate[T](self, impl_class: type[T], constructor_kwargs: Mapping[str, object], /) -> T:
        try:
            return impl_class(**constructor_kwargs)
        except Exception as err:
            raise ComponentInstantiationError(
                f"プラグインコンストラクタの実行に失敗しました: {impl_class.__name__} ({err})"
            ) from err


class PydanticConfigInstantiationStrategy:
    """Pydanticモデルおよび標準データクラスのスキーマ検証を隠蔽して設定オブジェクトを生成する戦略。"""

    def deserialize(self, config_class: type[object], payload: Mapping[str, object], /) -> object:
        if issubclass(config_class, BaseModel):
            try:
                return config_class.model_validate(payload)
            except Exception as err:
                raise ComponentInstantiationError(
                    f"Pydanticモデルのバリデーションに失敗しました: {config_class.__name__} ({err})"
                ) from err

        if dataclasses.is_dataclass(config_class):
            try:
                valid_fields = {f.name for f in dataclasses.fields(config_class)}
                filtered_payload = {k: v for k, v in payload.items() if k in valid_fields}
                return config_class(**filtered_payload)
            except Exception as err:
                raise ComponentInstantiationError(
                    f"データクラス設定オブジェクトの生成に失敗しました: {config_class.__name__} ({err})"
                ) from err

        raise ComponentInstantiationError(f"未対応の設定構造化型が指定されています: {config_class.__name__}")


class PluginEligibilityValidator:
    """プラグイン実装クラスの装飾規約および環境適合性を専門に検証するバリデーター。"""

    def validate(self, definition: PluginDefinition[object], setting: PluginSetting, /) -> bool:
        impl_class = definition.impl_class
        plugin_name = setting.plugin_name

        if not hasattr(impl_class, PluginDefinition.META_ATTR_CONTAINER):
            raise TypeError(
                f"厳格規約違反: 具象実装クラス '{impl_class.__name__}' に @plugin_impl デコレータがありません。"
            )

        dep_meta = getattr(impl_class, "__dependency_meta__", None)
        is_satisfied = bool(getattr(dep_meta, "check_satisfied")()) if dep_meta else True

        if plugin_name and plugin_name != "auto":
            if not is_satisfied:
                module_name = getattr(dep_meta, "module_name", "Unknown") if dep_meta else "Unknown"
                version = getattr(dep_meta, "version", "Unknown") if dep_meta else "Unknown"
                raise LookupError(
                    f"環境構成不調和: 指定されたプラグイン '{plugin_name}' の実行には、外部モジュール '{module_name} ({version})' が必須ですが、インストールされていません。"
                )
            return True

        return is_satisfied


class ConstructorResolver:
    """コンストラクタ引数仕様を走査しコンテナキャッシュ宇宙から最適な依存アセットを自動探索してkwargsを確定させるリゾルバ。"""

    def resolve_dependencies(
        self, definition: PluginDefinition[object], config_instance: object | None, session: ResolutionSession, /
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        impl_class = definition.impl_class

        if definition.config_class is not None and config_instance is not None:
            for p_name, p_type in definition.constructor_dependencies.items():
                if p_type is definition.config_class:
                    kwargs[p_name] = config_instance
                    break

        for param_name, dep_type in definition.constructor_dependencies.items():
            if param_name in kwargs:
                continue
            try:
                kwargs[param_name] = session.resolve_dependency_node(dep_type, param_name)
            except Exception as err:
                raise ComponentInstantiationError(
                    f"プラグインの引数解決に失敗しました。クラス: {impl_class.__name__}, 引数: {param_name} ({err})"
                ) from err

        return kwargs


class ComponentInstantiator:
    """関数型ポリモーフィズムを用いてコンポーネント仕様に応じた個別生成ルートへと処理をディスパッチするファクトリ。"""

    def __init__(self, registry: PluginRegistry, raw_config: Mapping[str, object], /) -> None:
        self._registry = registry
        self._raw_config = raw_config
        self._validator = PluginEligibilityValidator()
        self._constructor_resolver = ConstructorResolver()
        self._instantiation_strategy = DefaultInstantiationStrategy()
        self._config_strategy = PydanticConfigInstantiationStrategy()

    @singledispatchmethod
    def instantiate_component[T](self, component: Component[T], session: ResolutionSession, /) -> object | None:
        raise ComponentInstantiationError(
            f"サポートされていないコンポーネント仕様タイプです: {component.__class__.__name__}"
        )

    @instantiate_component.register(InstanceComponent)
    def _(self, component: InstanceComponent[object], session: ResolutionSession, /) -> object:
        return component.instance

    @instantiate_component.register(PropertyComponent)
    def _(self, component: PropertyComponent[object], session: ResolutionSession, /) -> object | None:
        key = component.key
        if key not in self._raw_config:
            if component.mandatory:
                raise ComponentInstantiationError(f"必須のプロパティ設定キーが見つかりません: {key}")
            return None

        val = self._raw_config[key]

        raw_target: object = component.target_type
        match raw_target:
            case types.GenericAlias() as alias:
                actual_type = alias.__origin__
            case type() as t:
                actual_type = t
            case _:
                actual_type = raw_target

        if not isinstance(val, actual_type):
            try:
                converter = typing.cast(typing.Callable[[object], object], actual_type)
                return converter(val)
            except (TypeError, ValueError) as err:
                raise ComponentInstantiationError(f"プロパティの型変換に失敗しました。キー: {key} ({err})") from err
        return val

    @instantiate_component.register(PluginComponent)
    def _(self, component: PluginComponent[object], session: ResolutionSession, /) -> object | None:
        spec_type = component.plugin_spec_type
        raw_payload = self._raw_config.get(component.key)

        plugin_name_override = session.requested_plugin_name
        setting = PluginSetting(raw_payload, component.naming_strategy, options_key=plugin_name_override)

        target_plugin_name = plugin_name_override if plugin_name_override else setting.plugin_name

        definition = self._registry.get_definition(spec_type, target_plugin_name)
        if definition is None:
            raise ComponentInstantiationError(
                f"対応するプラグイン実装が登録されていません: {spec_type.__name__} (名: {target_plugin_name})"
            )

        if not self._validator.validate(definition, setting):
            return None

        config_instance = None
        if definition.config_class is not None:
            config_instance = self._config_strategy.deserialize(definition.config_class, setting.options_payload)

        kwargs = self._constructor_resolver.resolve_dependencies(definition, config_instance, session)
        return self._instantiation_strategy.instantiate(definition.impl_class, kwargs)

    @instantiate_component.register(PluginListComponent)
    def _(self, component: PluginListComponent[object, object], session: ResolutionSession, /) -> object:
        spec_type = component.plugin_spec_type
        definitions = self._registry.get_all_definitions(spec_type)
        sorted_defs = sorted(definitions, key=lambda d: d.priority, reverse=True)

        instances: list[object] = []
        for definition in sorted_defs:
            raw_payload = self._raw_config.get(definition.plugin_name)
            setting = PluginSetting(raw_payload, component.naming_strategy)

            if not setting.enabled or not self._validator.validate(definition, setting):
                continue

            config_instance = None
            if definition.config_class is not None:
                config_instance = self._config_strategy.deserialize(definition.config_class, setting.options_payload)

            kwargs = self._constructor_resolver.resolve_dependencies(definition, config_instance, session)
            inst = self._instantiation_strategy.instantiate(definition.impl_class, kwargs)
            instances.append(inst)

        raw_target: object = component.target_type
        match raw_target:
            case types.GenericAlias() as alias:
                target_collection_type = alias.__origin__
            case type() as t:
                target_collection_type = t
            case _:
                target_collection_type = raw_target

        match target_collection_type:
            case type() as t if issubclass(t, list):
                return instances
            case type() as t if issubclass(t, tuple):
                return tuple(instances)
            case type() as t:
                try:
                    collection_factory = typing.cast(typing.Callable[[Sequence[object]], object], t)
                    return collection_factory(instances)
                except (TypeError, ValueError) as err:
                    raise ComponentInstantiationError(
                        f"カスタムコレクションへのインジェクションに失敗しました: {t.__name__}"
                    ) from err
            case _:
                raise ComponentInstantiationError("不正なコレクション型が指定されました。")


class ResolutionSession:
    """単一の解決要求のライフサイクルをホールドしBPPチェーンの適用を統括する実行状態コンテキスト。"""

    def __init__(
        self,
        container: Container,
        instantiator: ComponentInstantiator,
        stack: set[type[object] | types.GenericAlias | tuple[type[object], str]],
        requested_plugin_name: str | None = None,
        /,
    ) -> None:
        self._container = container
        self._instantiator = instantiator
        self._stack = stack
        self._requested_plugin_name = requested_plugin_name

    @property
    def requested_plugin_name(self) -> str | None:
        return self._requested_plugin_name

    def resolve_dependency_node(
        self, target_type: type[object] | types.GenericAlias, param_name: str | None = None, /
    ) -> object:
        return self._container._get_internal_instance(target_type, self._stack, plugin_name=param_name)

    def apply_lifecycle_pipeline(self, instance: object, bean_name: str, /) -> object:
        current_bean = instance

        for bpp in self._container._bpp_chain:
            current_bean = bpp.post_process_before_initialization(current_bean, bean_name)

        if isinstance(current_bean, Initializable):
            current_bean.initialize()

        for bpp in self._container._bpp_chain:
            current_bean = bpp.post_process_after_initialization(current_bean, bean_name)

        return current_bean


class Container(contextlib.AbstractContextManager["Container"], ApplicationContext):
    """DIコンテナ空間の実体でありシングルトンストアを完全に隠蔽してApplicationContextに準拠するスレッドセーフなレジストリ。"""

    def __init__(
        self,
        components: Sequence[Component[object]],
        registry: PluginRegistry,
        raw_config: Mapping[str, object],
        post_processors: Sequence[BeanPostProcessor] = (),
        /,
    ) -> None:
        self._components_map: dict[type[object] | types.GenericAlias, Component[object]] = {
            c.target_type: c for c in components
        }
        self._spec_components_map: dict[type[object], Component[object]] = {}
        for c in components:
            if hasattr(c, "plugin_spec_type") and c.plugin_spec_type is not None:
                self._spec_components_map[c.plugin_spec_type] = c

        self._instantiator = ComponentInstantiator(registry, raw_config)
        self._instances: dict[type[object] | types.GenericAlias | tuple[type[object], str], object] = {}
        self._bpp_chain = tuple(sorted(post_processors, key=lambda x: x.priority))
        self._lock = Lock()
        self._exit_stack = contextlib.ExitStack()

    def get_component[T](self, target_type: type[T] | types.GenericAlias, /, *, plugin_name: str | None = None) -> T:
        return self._get_internal_instance(target_type, set(), plugin_name=plugin_name)

    def get_components_by_spec[T](self, spec_type: type[T], /) -> Sequence[T]:
        """仕様型に紐づくコレクションアセット（PluginListComponentなど）を動的に引き当てて要素を安全に返却します。"""
        if spec_type in self._spec_components_map:
            comp = self._spec_components_map[spec_type]
            resolved_collection = self.get_component(comp.target_type)
            return typing.cast(Sequence[T], resolved_collection)

        results: list[T] = []
        for k, v in self._instances.items():
            match k:
                case type() as t if issubclass(t, spec_type):
                    results.append(typing.cast(T, v))
                case _:
                    pass
        return results

    def _get_internal_instance[T](
        self,
        target_type: type[T] | types.GenericAlias,
        stack: set[type[object] | types.GenericAlias | tuple[type[object], str]],
        *,
        plugin_name: str | None = None,
    ) -> T:
        cache_key: type[object] | types.GenericAlias | tuple[type[object], str] = target_type
        match target_type:
            case type() as t if plugin_name is not None:
                cache_key = (t, plugin_name)
            case _:
                pass

        if cache_key in self._instances:
            return typing.cast(T, self._instances[cache_key])

        lookup_type = target_type
        match lookup_type:
            case type() as t if t not in self._components_map and plugin_name is not None:
                if t in self._spec_components_map:
                    lookup_type = self._spec_components_map[t].target_type
            case _:
                pass

        if lookup_type not in self._components_map:
            if plugin_name:
                return typing.cast(T, self._get_internal_instance(target_type, stack, plugin_name=None))
            type_name = lookup_type.__name__ if isinstance(lookup_type, type) else str(lookup_type)
            raise ComponentInstantiationError(f"指定された仕様型はコンテナに登録されていません: {type_name}")

        component = self._components_map[lookup_type]

        match target_type:
            case types.GenericAlias() as alias:
                bean_name = (
                    component.key
                    if component.key
                    else f"{alias.__origin__.__name__.lower()}_of_{alias.__args__[0].__name__.lower()}"
                )
            case type() as t:
                bean_name = (
                    f"{t.__name__.lower()}:{plugin_name}"
                    if plugin_name
                    else component.key
                    if component.key
                    else t.__name__.lower()
                )
            case _:
                bean_name = str(target_type)

        if component.scope == ComponentScope.TRANSIENT:
            session = ResolutionSession(self, self._instantiator, stack, plugin_name)
            raw_inst = self._instantiator.instantiate_component(component, session)
            if raw_inst is None:
                raise ComponentInstantiationError(f"コンポーネントの生成に失敗しました: {bean_name}")
            return typing.cast(T, session.apply_lifecycle_pipeline(raw_inst, bean_name))

        with self._lock:
            if cache_key in self._instances:
                return typing.cast(T, self._instances[cache_key])

            if cache_key in stack:
                type_name = str(cache_key)
                raise CircularDependencyError(f"循環依存が検出されました。型閉路パス: {type_name}")

            stack.add(cache_key)
            try:
                session = ResolutionSession(self, self._instantiator, stack, plugin_name)
                raw_inst = self._instantiator.instantiate_component(component, session)
                if raw_inst is None:
                    raise ComponentInstantiationError(f"コンポーネントの現物化に失敗しました: {bean_name}")

                processed_bean = session.apply_lifecycle_pipeline(raw_inst, bean_name)
                self._register_resource(processed_bean)
                self._instances[cache_key] = processed_bean

                if cache_key != target_type and target_type not in self._instances:
                    self._instances[target_type] = processed_bean

                return typing.cast(T, processed_bean)
            finally:
                stack.remove(cache_key)

    def _register_resource(self, instance: object, /) -> None:
        if (close_method := getattr(instance, "close", None)) is not None and callable(close_method):
            self._exit_stack.callback(close_method)
        elif (cleanup_method := getattr(instance, "cleanup", None)) is not None and callable(cleanup_method):
            self._exit_stack.callback(cleanup_method)

    def close(self) -> None:
        with self._lock:
            self._exit_stack.close()
            self._instances.clear()

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: types.TracebackType | None, /
    ) -> bool | None:
        self.close()
        return None
