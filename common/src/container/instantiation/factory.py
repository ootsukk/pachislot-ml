from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, cast

from pydantic import BaseModel

from container.common.exceptions import ComponentInstantiationError
from container.definitions.component import (
    CollectionComponent,
    Component,
    InstanceComponent,
    PluginComponent,
    PropertyComponent,
)
from container.definitions.descriptor import PluginDescriptor
from container.definitions.resolvable import ResolvableType

if typing.TYPE_CHECKING:
    from container.core.session import ResolutionSession
    from container.definitions.registry import PluginDefinition


@dataclass(frozen=True)
class ElementMetadata:
    """ファクトリが実体化したオブジェクトに伴う、コンテナ管理用の一次メタデータコンテナ。"""

    instance: object
    priority: int = 0
    name: str | None = None


class MetadataWrapperFactory:
    """実体インスタンスとコンテナメタデータを、要求された任意のラッパークラスへ動的にマッピング・鋳造する専任ファクトリ。"""

    def create_wrapper(
        self,
        requested_type: type[object],
        raw_target_type: type[object] | types.GenericAlias,
        metadata: ElementMetadata,
        /,
    ) -> object:
        """要求された要素型アノテーションを解析し、必要に応じてメタデータを包摂したラッパーオブジェクトを動的に組み立てて返却します。"""
        resolvable = ResolvableType.from_annotation(raw_target_type)
        if resolvable and requested_type is resolvable.origin:
            return metadata.instance

        try:
            sig = inspect.signature(requested_type.__init__)
        except ValueError, TypeError:
            return metadata.instance

        kwargs: dict[str, object] = {}
        for p_name, p_param in sig.parameters.items():
            if p_name == "self":
                continue

            p_resolvable = ResolvableType.from_annotation(p_param.annotation)

            if p_resolvable and p_resolvable.is_assignable_from(type(metadata.instance)):
                kwargs[p_name] = metadata.instance
            elif p_name == "priority" or p_param.annotation is int:
                kwargs[p_name] = metadata.priority
            elif p_name in ("name", "name_key", "plugin_name") or p_param.annotation in (str, str | None):
                kwargs[p_name] = metadata.name
            elif p_param.default is not inspect.Parameter.empty:
                continue
            elif p_resolvable and p_resolvable.is_optional:
                kwargs[p_name] = None

        try:
            return requested_type(**kwargs)
        except Exception as err:
            raise ComponentInstantiationError(
                f"ラッパークラス '{requested_type.__name__}' の動的生成に失敗しました: {err}"
            ) from err


class ComponentFactory[C: Component[object]](Protocol):
    """すべてのコンポーネントファクトリが共有する共通インターフェース規約。"""

    def create_instance(
        self,
        component: C,
        session: ResolutionSession,
        raw_config: Mapping[str, object],
        /,
    ) -> object | None:
        """指定されたコンポーネント定義および設定情報に基づき、インスタンスを組み立てて返却します。"""
        ...

    def create_collection_elements(
        self,
        component: C,
        session: ResolutionSession,
        outer_config: object,
        requested_element_type: type[object],
        /,
    ) -> Sequence[object]:
        """コレクションの構成要素として要求された際、自身に閉じたループ駆動で複数インスタンスを多態的に一括鋳造します。"""
        ...


class ConstructorResolver:
    """具象クラスのコンストラクタシグネチャを解析し、依存関係を自動解決する解析エンジン。"""

    def resolve_dependencies(
        self,
        definition: PluginDefinition[object],
        session: ResolutionSession,
        /,
    ) -> Mapping[str, object]:
        impl_class = definition.impl_class

        if not hasattr(impl_class, "__init__"):
            return {}

        try:
            signature = inspect.signature(impl_class.__init__)
        except (ValueError, TypeError) as err:
            raise ComponentInstantiationError(
                f"クラス '{impl_class.__name__}' のコンストラクタシグネチャをパースできませんでした: {err}"
            ) from err

        resolved_kwargs: dict[str, object] = {}

        for param_name, param in signature.parameters.items():
            if param_name == "self" or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            resolvable = ResolvableType.from_annotation(param.annotation)
            if resolvable is None:
                if param.default is inspect.Parameter.empty:
                    raise ComponentInstantiationError(
                        f"クラス '{impl_class.__name__}' の引数 '{param_name}' に型ヒントが設定されていないため、自動解決できません。"
                    )
                continue

            try:
                dependency_instance = session.resolve_dependency_instance(
                    resolvable, name=definition.plugin_name
                )

                if dependency_instance is not None:
                    resolved_kwargs[param_name] = dependency_instance
                elif param.default is not inspect.Parameter.empty:
                    resolved_kwargs[param_name] = param.default
                elif resolvable.is_optional:
                    resolved_kwargs[param_name] = None
                else:
                    raise ComponentInstantiationError(
                        f"クラス '{impl_class.__name__}' が要求する必須の依存関係 '{param_name}: {resolvable.raw_type}' を解決できませんでした。"
                    )

            except Exception as err:
                if isinstance(err, ComponentInstantiationError):
                    raise err
                raise ComponentInstantiationError(
                    f"クラス '{impl_class.__name__}' の引数 '{param_name}' の依存関係解決中に予期せぬエラーが発生しました: {err}"
                ) from err

        return resolved_kwargs


class InstanceComponentFactory(ComponentFactory[InstanceComponent[object]]):
    """事前に生成済みのシングルトン実体を追加検証なしで透過的にそのまま返却するファクトリ。"""

    def __init__(self, wrapper_factory: MetadataWrapperFactory, /) -> None:
        self._wrapper_factory: Final[MetadataWrapperFactory] = wrapper_factory

    def create_instance(
        self,
        component: InstanceComponent[object],
        session: ResolutionSession,
        raw_config: Mapping[str, object],
        /,
    ) -> object | None:
        return component.instance

    def create_collection_elements(
        self,
        component: InstanceComponent[object],
        session: ResolutionSession,
        outer_config: object,
        requested_element_type: type[object],
        /,
    ) -> Sequence[object]:
        inst = component.instance
        if inst is None:
            return []
        meta = ElementMetadata(instance=inst)
        wrapped = self._wrapper_factory.create_wrapper(requested_element_type, component.target_type, meta)
        return [wrapped]


class PropertyComponentFactory(ComponentFactory[PropertyComponent[object]]):
    """設定オブジェクト（Pydanticモデルまたはデータクラス）の検証およびマッピングを行うファクトリ。"""

    def __init__(self, wrapper_factory: MetadataWrapperFactory, /) -> None:
        self._wrapper_factory: Final[MetadataWrapperFactory] = wrapper_factory

    def deserialize(self, config_class: type[object], payload: Mapping[str, object], /) -> object:
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
        self,
        component: PropertyComponent[object],
        session: ResolutionSession,
        raw_config: Mapping[str, object],
        /,
    ) -> object | None:
        key = component.key
        if key not in raw_config:
            return None

        val = raw_config[key]

        resolvable = ResolvableType.from_annotation(component.target_type)
        if resolvable is None:
            raise ComponentInstantiationError(f"設定型アノテーションを解析できません: {component.target_type}")

        actual_type = resolvable.origin

        if issubclass(actual_type, BaseModel) or dataclasses.is_dataclass(actual_type):
            if not isinstance(val, Mapping):
                raise ComponentInstantiationError(f"キー '{key}' の値は辞書型マッピングである必要があります。")
            payload_map = {str(k): v for k, v in val.items()}
            return self.deserialize(actual_type, payload_map)

        if not isinstance(val, actual_type):
            try:
                converter = cast(typing.Callable[[object], object], actual_type)
                return converter(val)
            except (TypeError, ValueError) as err:
                raise ComponentInstantiationError(f"型変換に失敗しました: {key} ({err})") from err
        return val

    def create_collection_elements(
        self,
        component: PropertyComponent[object],
        session: ResolutionSession,
        outer_config: object,
        requested_element_type: type[object],
        /,
    ) -> Sequence[object]:
        if not isinstance(outer_config, Sequence) or isinstance(outer_config, (str, bytes)):
            return []

        resolvable = ResolvableType.from_annotation(component.target_type)
        if resolvable is None:
            raise ComponentInstantiationError(f"要素の設定型アノテーションを解析できません: {component.target_type}")

        target_class = resolvable.origin
        instances: list[object] = []

        for element_payload in outer_config:
            if isinstance(element_payload, Mapping):
                payload_map = {str(k): v for k, v in element_payload.items()}
                inst = self.deserialize(target_class, payload_map)

                meta = ElementMetadata(instance=inst)
                wrapped = self._wrapper_factory.create_wrapper(requested_element_type, component.target_type, meta)
                instances.append(wrapped)

        return instances


class PluginComponentFactory(ComponentFactory[PluginComponent[object]]):
    """単一プラグインの環境選出と、コンストラクタインジェクションに基づく組み立てを担当するファクトリ。"""

    def __init__(self, wrapper_factory: MetadataWrapperFactory, /) -> None:
        from container.instantiation.validator import (
            PluginEligibilityValidator,
        )

        self._wrapper_factory: Final[MetadataWrapperFactory] = wrapper_factory
        self._validator: Final[PluginEligibilityValidator] = PluginEligibilityValidator()
        self._constructor_resolver: Final[ConstructorResolver] = ConstructorResolver()

    def create_instance_direct(
        self,
        definition: PluginDefinition[object],
        setting: PluginDescriptor,
        session: ResolutionSession,
        /,
    ) -> object | None:
        if not self._validator.validate(definition, setting):
            return None

        kwargs = self._constructor_resolver.resolve_dependencies(definition, session)
        try:
            return definition.impl_class(**kwargs)
        except Exception as err:
            raise ComponentInstantiationError(
                f"コンストラクタの実行に失敗しました: {definition.impl_class.__name__} ({err})"
            ) from err

    def create_instance(
        self,
        component: PluginComponent[object],
        session: ResolutionSession,
        raw_config: Mapping[str, object],
        /,
    ) -> object | None:
        spec_type = component.plugin_spec_type
        definitions = session.resolve_plugin_stream(spec_type)

        raw_payload = raw_config.get(component.key)
        plugin_name_override = session.requested_plugin_name
        setting = PluginDescriptor(
            raw_payload,
            component.naming_strategy,
            options_key=plugin_name_override,
        )
        target_plugin_name = plugin_name_override if plugin_name_override else setting.plugin_name

        target_def = None
        if target_plugin_name and target_plugin_name != "auto":
            for d in definitions:
                if d.plugin_name == target_plugin_name:
                    target_def = d
                    break

            if target_def is None:
                raise ComponentInstantiationError(
                    f"指定されたプラグイン名 '{target_plugin_name}' は、仕様インターフェース '{spec_type.__name__}' の実装レジストリに存在しません。"
                )
        else:
            if definitions:
                target_def = definitions[0]

        if target_def is None:
            return None

        return self.create_instance_direct(target_def, setting, session)

    def create_collection_elements(
        self,
        component: PluginComponent[object],
        session: ResolutionSession,
        outer_config: object,
        requested_element_type: type[object],
        /,
    ) -> Sequence[object]:
        outer_map = outer_config if isinstance(outer_config, Mapping) else {}
        definitions = session.resolve_plugin_stream(component.plugin_spec_type)
        instances: list[object] = []

        for definition in definitions:
            raw_payload = outer_map.get(definition.plugin_name)
            setting = PluginDescriptor(raw_payload, component.naming_strategy)
            if not setting.enabled:
                continue

            inst = self.create_instance_direct(definition, setting, session)
            if inst is not None:
                meta = ElementMetadata(instance=inst, priority=definition.priority, name=definition.plugin_name)
                wrapped = self._wrapper_factory.create_wrapper(requested_element_type, component.target_type, meta)
                instances.append(wrapped)

        return instances


class CollectionComponentFactory(ComponentFactory[CollectionComponent[object, object]]):
    """中央レジストリを内包し、一切の型条件分岐を完全に撤廃したポリモーフィックな一括鋳造ファクトリ。"""

    def __init__(self) -> None:
        self._factory_registry: ComponentFactoryRegistry | None = None

    def set_registry(self, registry: ComponentFactoryRegistry) -> None:
        self._factory_registry = registry

    def create_instance(
        self,
        component: CollectionComponent[object, object],
        session: ResolutionSession,
        raw_config: Mapping[str, object],
        /,
    ) -> object | None:
        if self._factory_registry is None:
            raise ComponentInstantiationError("CollectionComponentFactory に中央レジストリがバインドされていません。")

        outer_config = raw_config.get(component.key)

        resolvable = ResolvableType.from_annotation(component.target_type)
        if resolvable is None:
            raise ComponentInstantiationError(f"コレクション型アノテーションを解析できません: {component.target_type}")

        requested_element_type = resolvable.first_generic_argument
        nested = component.nested_component

        factory = self._factory_registry.get_factory(type(nested))
        if factory is None or not hasattr(factory, "create_collection_elements"):
            raise ComponentInstantiationError(
                f"ネストコンポーネント仕様 '{type(nested).__name__}' に対応する要素ファクトリを取得できませんでした。"
            )

        instances = factory.create_collection_elements(nested, session, outer_config, requested_element_type)

        target_collection_type = resolvable.origin
        match target_collection_type:
            case t if issubclass(t, list):
                return list(instances)
            case t if issubclass(t, tuple):
                return tuple(instances)
            case t:
                try:
                    collection_factory = cast(typing.Callable[[Sequence[object]], object], t)
                    return collection_factory(instances)
                except (TypeError, ValueError) as err:
                    raise ComponentInstantiationError(
                        f"カスタムコレクションへの動的インジェクションに失敗しました: {t.__name__} ({err})"
                    ) from err

    def create_collection_elements(
        self,
        component: CollectionComponent[object, object],
        session: ResolutionSession,
        outer_config: object,
        requested_element_type: type[object],
        /,
    ) -> Sequence[object]:
        res = self.create_instance(component, session, {component.key: outer_config})
        return cast(Sequence[object], res) if isinstance(res, Sequence) else []


class ComponentFactoryRegistry:
    """外部DIを完全廃止し、コンポーネント型と対応ファクトリの関係を内部に完璧にカプセル化した最速の中央レジストリ。"""

    def __init__(self) -> None:
        wrapper_factory = MetadataWrapperFactory()

        instance_factory = InstanceComponentFactory(wrapper_factory)
        property_factory = PropertyComponentFactory(wrapper_factory)
        plugin_factory = PluginComponentFactory(wrapper_factory)
        collection_factory = CollectionComponentFactory()

        self._registry_map: Final[dict[type[Component[object]], ComponentFactory[typing.Any]]] = {
            InstanceComponent: instance_factory,
            PropertyComponent: property_factory,
            PluginComponent: plugin_factory,
            CollectionComponent: collection_factory,
        }

        collection_factory.set_registry(self)

    def get_factory[C: Component[object]](self, component_type: type[C], /) -> ComponentFactory[C] | None:
        """指定されたコンポーネント型に基づき、対応するファクトリを定数時間で選出します。"""
        factory = self._registry_map.get(component_type)
        return cast(ComponentFactory[C], factory) if factory is not None else None
