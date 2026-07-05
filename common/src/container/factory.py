from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from pydantic import BaseModel

from container.component import (
    Component,
    InstanceComponent,
    PluginComponent,
    PluginListComponent,
    PluginSetting,
    PropertyComponent,
)
from container.context import BeanName
from container.exceptions import ComponentInstantiationError
from container.resolvable_type import ResolvableType
from container.resolver import ConstructorResolver
from container.validator import PluginEligibilityValidator

if typing.TYPE_CHECKING:
    from container.context import ResolutionSession
    from container.register import PluginDefinition


class ComponentFactory[C: Component[object]](Protocol):
    """すべてのコンポーネントファクトリが共有する、ランタイムエンジン駆動用の多態性共通プロトコル。"""

    def create_instance(
        self, component: C, session: ResolutionSession, raw_config: Mapping[str, object], /
    ) -> object | None: ...


class ConfigFactory(ComponentFactory[PropertyComponent[object]], Protocol):
    """設定オブジェクトのデシリアライズおよび PropertyComponent の解決能力を規定するプロトコル。"""

    def deserialize(self, config_class: type[object], payload: Mapping[str, object], /) -> object: ...


class PluginFactory(ComponentFactory[PluginComponent[object]], Protocol):
    """単一プラグインの直接実体化および PluginComponent の解決能力を規定するプロトコル。"""

    def create_instance_direct(
        self, definition: PluginDefinition[object], setting: PluginSetting, session: ResolutionSession, /
    ) -> object | None: ...


class CollectionFactory(ComponentFactory[PluginListComponent[object, object]], Protocol):
    """プラグイン配列の動的鋳造および PluginListComponent の解決能力を規定するプロトコル。"""

    def create_collection(
        self,
        definitions: Sequence[PluginDefinition[object]],
        raw_config: Mapping[str, object],
        resolvable: ResolvableType,
        session: ResolutionSession,
        naming_strategy: object,
        /,
    ) -> object: ...


class DefaultInstantiationStrategy:
    """具象実装クラスをキーワード引数に基づいて物理実体化する標準生成戦略。"""

    def instantiate[T](self, impl_class: type[T], constructor_kwargs: Mapping[str, object], /) -> T:
        try:
            return impl_class(**constructor_kwargs)
        except Exception as err:
            raise ComponentInstantiationError(
                f"コンストラクタの実行に失敗しました: {impl_class.__name__} ({err})"
            ) from err


class InstanceComponentFactory:
    """InstanceComponentの透過返却を受け持つファクトリ。"""

    def create_instance(
        self, component: InstanceComponent[object], session: ResolutionSession, raw_config: Mapping[str, object], /
    ) -> object | None:
        return component.instance


class ConfigInstanceFactory:
    """固有メソッド名 'deserialize' による型安全なデシリアライズ責任と、共通インターフェースを両立する具象ファクトリ。"""

    def deserialize(self, config_class: type[object], payload: Mapping[str, object], /) -> object:
        """【固有API】コンテナ外部からも完全に独立して利用可能な、純粋なデシリアライズ機能。"""
        if issubclass(config_class, BaseModel):
            try:
                return config_class.model_validate(payload)
            except Exception as err:
                raise ComponentInstantiationError(
                    f"Pydanticモデルの検証に失敗しました: {config_class.__name__} ({err})"
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

    def create_instance(
        self, component: PropertyComponent[object], session: ResolutionSession, raw_config: Mapping[str, object], /
    ) -> object | None:
        """【共通API】ComponentFactory プロトコルに対する適合実装。内部で固有APIへデリゲーション。"""
        key = component.key
        if key not in raw_config:
            return None

        val = raw_config[key]
        resolvable = ResolvableType(component.target_type)
        actual_type = resolvable.origin

        if issubclass(actual_type, BaseModel) or dataclasses.is_dataclass(actual_type):
            if not isinstance(val, Mapping):
                raise ComponentInstantiationError(f"キー '{key}' の値は辞書型マッピングである必要があります。")
            payload_map = {str(k): v for k, v in val.items()}
            return self.deserialize(actual_type, payload_map)

        if not isinstance(val, actual_type):
            try:
                converter = typing.cast(typing.Callable[[object], object], actual_type)
                return converter(val)
            except (TypeError, ValueError) as err:
                raise ComponentInstantiationError(f"型変換に失敗しました: {key} ({err})") from err
        return val


class PluginInstanceFactory:
    """固有メソッド名 'create_instance_direct' による詳細な組み立て責任と、共通インターフェースを両立する具象ファクトリ。"""

    def __init__(self) -> None:
        self._validator: Final[PluginEligibilityValidator] = PluginEligibilityValidator()
        self._constructor_resolver: Final[ConstructorResolver] = ConstructorResolver()
        self._instantiation_strategy: Final[DefaultInstantiationStrategy] = DefaultInstantiationStrategy()

    def create_instance_direct(
        self, definition: PluginDefinition[object], setting: PluginSetting, session: ResolutionSession, /
    ) -> object | None:
        """【固有API】単一のプラグイン定義メタデータから物理実体化を行う、高凝集な生成機能。"""
        if not self._validator.validate(definition, setting):
            return None

        kwargs = self._constructor_resolver.resolve_dependencies(definition, session)
        return self._instantiation_strategy.instantiate(definition.impl_class, kwargs)

    def create_instance(
        self, component: PluginComponent[object], session: ResolutionSession, raw_config: Mapping[str, object], /
    ) -> object | None:
        """【共通API】ComponentFactory プロトコルに対する適合実装。内部で固有APIへデリゲーション。"""
        spec_type = component.plugin_spec_type
        definitions = session.resolve_plugin_stream(spec_type)

        raw_payload = raw_config.get(component.key)
        plugin_name_override = session.requested_plugin_name
        setting = PluginSetting(raw_payload, component.naming_strategy, options_key=plugin_name_override)
        target_plugin_name = plugin_name_override if plugin_name_override else setting.plugin_name

        target_def = None
        if target_plugin_name and target_plugin_name != "auto":
            for d in definitions:
                if d.plugin_name == target_plugin_name:
                    target_def = d
                    break
        else:
            if definitions:
                target_def = definitions[0]

        if target_def is None:
            return None

        return self.create_instance_direct(target_def, setting, session)


class CollectionInstanceFactory:
    """固有メソッド名 'create_collection' による配列の動的鋳造責任と、共通インターフェースを両立する具象ファクトリ。"""

    def __init__(self, plugin_factory: PluginFactory, /) -> None:
        self._plugin_factory: Final[PluginFactory] = plugin_factory

    def create_collection(
        self,
        definitions: Sequence[PluginDefinition[object]],
        raw_config: Mapping[str, object],
        resolvable: ResolvableType,
        session: ResolutionSession,
        naming_strategy: object,
        /,
    ) -> object:
        """【固有API】整列済み定義カタログのシーケンスから反復生成とコレクション型変換を行う機能。"""
        instances: list[object] = []
        strategy_adapter = typing.cast(typing.Any, naming_strategy)

        for definition in definitions:
            raw_payload = raw_config.get(definition.plugin_name)
            setting = PluginSetting(raw_payload, strategy_adapter)

            if not setting.enabled:
                continue

            inst = self._plugin_factory.create_instance_direct(definition, setting, session)
            if inst is None:
                continue

            bean_name = BeanName(definition.plugin_name)
            processed_element = session.apply_lifecycle_pipeline(inst, bean_name)
            instances.append(processed_element)

        target_collection_type = resolvable.origin
        match target_collection_type:
            case t if issubclass(t, list):
                return list(instances)
            case t if issubclass(t, tuple):
                return tuple(instances)
            case t:
                try:
                    collection_factory = typing.cast(typing.Callable[[Sequence[object]], object], t)
                    return collection_factory(instances)
                except (TypeError, ValueError) as err:
                    raise ComponentInstantiationError(
                        f"カスタムコレクションへの動的インジェクションに失敗しました: {t.__name__}"
                    ) from err

    def create_instance(
        self,
        component: PluginListComponent[object, object],
        session: ResolutionSession,
        raw_config: Mapping[str, object],
        /,
    ) -> object | None:
        """【共通API】ComponentFactory プロトコルに対する適合実装。内部で固有APIへデリゲーション。"""
        definitions = session.resolve_plugin_stream(component.plugin_spec_type)
        resolvable = ResolvableType(component.target_type)
        return self.create_collection(definitions, raw_config, resolvable, session, component.naming_strategy)
