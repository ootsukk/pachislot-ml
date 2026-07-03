from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
import types
from typing import Protocol

from container.constants import (
    YAML_KEY_ENABLED,
    YAML_KEY_PLUGIN_NAME,
    YAML_SUFFIX_OPTIONS,
    YAML_VAL_AUTO,
    ComponentScope,
)


class NamingStrategy(Protocol):
    """クラス型から構成ファイル上のキー名を導出するための共通戦略インターフェース。"""

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None: ...
    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None: ...


class FixedKeyStrategy:
    """明示的に指定された固定文字列を最優先で返す具象命名戦略。"""

    def __init__(self, key: str, /) -> None:
        self._key = key

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == YAML_VAL_AUTO:
            return None
        return self._key

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == YAML_VAL_AUTO or self._key == "":
            return None
        return f"{self._key}{YAML_SUFFIX_OPTIONS}"


class PluginNameKeyStrategy:
    """実装クラスのメタデータからキー名を抽出する具象命名戦略。"""

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name and dynamic_name != YAML_VAL_AUTO:
            return dynamic_name

        if cls_obj is not None and hasattr(cls_obj, "__plugin_impl_meta__"):
            meta: object = getattr(cls_obj, "__plugin_impl_meta__")  # noqa: B009
            if hasattr(meta, "value"):
                return str(getattr(meta, "value"))  # noqa: B009
        return None

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if cls_obj is not None and hasattr(cls_obj, "__plugin_impl_meta__") and dynamic_name == YAML_VAL_AUTO:
            return ""
        return None


class ClassNameAutomaticKeyStrategy:
    """クラス名から自動的にスネークケース文字列を生成する最終フォールバック命名戦略。"""

    def __init__(self, cls_obj: type[object] | None = None, /) -> None:
        return

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        if cls_obj is not None:
            name = cls_obj.__name__
            snake_name = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            return snake_name
        return None

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str | None:
        snake_name = self.get_key(cls_obj, dynamic_name)
        return f"{snake_name}{YAML_SUFFIX_OPTIONS}" if snake_name else None


class ChainNamingStrategy:
    """登録された複数の具象戦略を配列順に走査し最初に適合したキー名を採用する複合戦略。"""

    def __init__(self, fixed_key_str: str | None = None, /) -> None:
        strategies: list[NamingStrategy] = []
        if fixed_key_str is not None:
            strategies.append(FixedKeyStrategy(fixed_key_str))
        strategies.append(PluginNameKeyStrategy())
        strategies.append(ClassNameAutomaticKeyStrategy())

        self._strategies: Sequence[NamingStrategy] = strategies

    def get_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str:
        for strategy in self._strategies:
            resolved = strategy.get_key(cls_obj, dynamic_name)
            if resolved is not None and resolved != "":
                return resolved
        return ""

    def get_options_key(self, cls_obj: type[object] | None = None, dynamic_name: str | None = None) -> str:
        for strategy in self._strategies:
            resolved = strategy.get_options_key(cls_obj, dynamic_name)
            if resolved is not None and resolved != "":
                return resolved
        return ""


def naming_chain(fixed_key_str: str) -> NamingStrategy:
    return ChainNamingStrategy(fixed_key_str)


class Component[T](ABC):
    """DIコンテナにおける管理オブジェクト生成のメタデータ定義を司る基底仕様書。"""

    def __init__(
        self,
        target_type: type[T] | types.GenericAlias,
        naming_strategy: NamingStrategy,
        mandatory: bool = True,
        scope: ComponentScope = ComponentScope.SINGLETON,
    ) -> None:
        self._target_type = target_type
        self._naming_strategy = naming_strategy
        self._mandatory = mandatory
        self._scope = scope

    @property
    def target_type(self) -> type[T] | types.GenericAlias:
        return self._target_type

    @property
    def naming_strategy(self) -> NamingStrategy:
        return self._naming_strategy

    @property
    def scope(self) -> ComponentScope:
        return self._scope

    @property
    def key(self) -> str:
        if isinstance(self._target_type, types.GenericAlias):
            core_type = getattr(self._target_type, "__origin__", self._target_type)
        else:
            core_type = self._target_type

        resolved = self._naming_strategy.get_key(core_type)
        return resolved if resolved is not None else ""

    @property
    def mandatory(self) -> bool:
        return self._mandatory

    @property
    @abstractmethod
    def plugin_spec_type(self) -> type[object]: ...


class InstanceComponent[T](Component[T]):
    """あらかじめ生成された特定の具象インスタンスを直接登録するための仕様定義書。"""

    def __init__(self, target_type: type[T], instance: T) -> None:
        super().__init__(
            target_type, naming_strategy=ChainNamingStrategy(""), mandatory=True, scope=ComponentScope.SINGLETON
        )
        self._instance = instance

    @property
    def instance(self) -> T:
        return self._instance

    @property
    def plugin_spec_type(self) -> type[object]:
        return self.target_type


class PropertyComponent[T](Component[T]):
    """構成ファイルからの単純なプロパティ値注入を定義するための仕様定義書。"""

    @property
    def plugin_spec_type(self) -> type[object]:
        return self.target_type


class PluginComponent[T](Component[T]):
    """単一のプラグイン実装を自動選定するための仕様定義書。"""

    @property
    def plugin_spec_type(self) -> type[object]:
        return self.target_type


class PluginListComponent[R, T](Component[R]):
    """複数のプラグインをリストまたはカスタムコレクションとして解決・注入するための仕様定義書。"""

    def __init__(
        self,
        target_type: type[R],
        naming_strategy: NamingStrategy,
        nested_component: Component[T],
        ordered: bool = False,
        mandatory: bool = True,
        scope: ComponentScope = ComponentScope.SINGLETON,
    ) -> None:
        super().__init__(target_type, naming_strategy=naming_strategy, mandatory=mandatory, scope=scope)
        self._nested_component = nested_component
        self._ordered = ordered

    @property
    def nested_component(self) -> Component[T]:
        return self._nested_component

    @property
    def ordered(self) -> bool:
        return self._ordered

    @property
    def plugin_spec_type(self) -> type[object]:
        return self.nested_component.plugin_spec_type


class PluginSetting:
    """生の設定データのトポロジーをカプセル化し一貫したドメインセマンティクスを提供する不変の値オブジェクト。"""

    def __init__(
        self,
        raw_value: object,
        strategy: NamingStrategy,
        options_key: str | None = None,
    ) -> None:
        self._raw_value = raw_value
        self._strategy = strategy

        match raw_value:
            case None:
                self._plugin_name = YAML_VAL_AUTO
                self._enabled = True
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