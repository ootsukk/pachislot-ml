
import re
from abc import ABC, abstractmethod
from graphlib import TopologicalSorter
from typing import Any

from webclient.base import (
    BodyDecoder,
    BodyEncoder,
    ClientHttpConnector,
    CookieStore,
    ExchangeFilter,
    PrioritizedFilter,
    ProxyOptions,
    RedirectOptions,
)


class NamingStrategy(ABC):

    def __init__(self, next_strategy: NamingStrategy | None = None) -> None:
        self._next_strategy = next_strategy

    @abstractmethod
    def handle_get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        """各具象戦略が、自身の規約に則ってキー解決を試みる内部フック。解決できない場合は None を返します。"""
        pass

    @abstractmethod
    def handle_get_options_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        """各具象戦略が、自身の規約に則ってオプションキー解決を試みる内部フック。"""
        pass

    def get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str:
        """責任の連鎖を上から下へ駆動させ、最初に解決に成功したキーを返却する公開インターフェース。"""
        resolved = self.handle_get_key(cls_obj, dynamic_name)
        if resolved is not None and resolved != "":
            return resolved

        if self._next_strategy is not None:
            return self._next_strategy.get_key(cls_obj, dynamic_name)

        return ""

    def get_options_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str:
        """責任の連鎖を上から下へ駆動させ、最初に解決に成功したオプションキーを返却する公開インターフェース。"""
        resolved = self.handle_get_options_key(cls_obj, dynamic_name)
        if resolved is not None and resolved != "":
            return resolved

        if self._next_strategy is not None:
            return self._next_strategy.get_options_key(cls_obj, dynamic_name)

        return ""


class FixedKey(NamingStrategy):
    """最優先責任:トップレベル用の固定明示キー戦略(例: 'encoder' や 'filters')。"""

    def __init__(self, key: str, next_strategy: NamingStrategy | None = None) -> None:
        super().__init__(next_strategy)
        self._key = key

    def handle_get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == "auto":
            return None
        return self._key

    def handle_get_options_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name == "auto" or self._key == "":
            return None
        return f"{self._key}_options"


class PluginNameKey(NamingStrategy):
    """第二責任:設定値、または実装クラスの @plugin_impl.value メタデータからの動的解決戦略。"""

    def handle_get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        if dynamic_name and dynamic_name != "auto":
            return dynamic_name
        if cls_obj and hasattr(cls_obj, "__plugin_impl_meta__"):
            return str(cls_obj.__plugin_impl_meta__.value)
        return None

    def handle_get_options_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        if cls_obj and hasattr(cls_obj, "__plugin_impl_meta__") and dynamic_name == "auto":
            return ""
        return None


class ClassNameAutomaticKey(NamingStrategy):

    def handle_get_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        if cls_obj:
            name = cls_obj.__name__
            snake_name = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            return snake_name
        return None

    def handle_get_options_key(self, cls_obj: type[Any] | None = None, dynamic_name: str | None = None) -> str | None:
        snake_name = self.handle_get_key(cls_obj, dynamic_name)
        return f"{snake_name}_options" if snake_name else None


def naming_chain(fixed_key_str: str) -> NamingStrategy:
    return FixedKey(key=fixed_key_str, next_strategy=PluginNameKey(next_strategy=ClassNameAutomaticKey()))


class PluginSetting:
    """生の設定データのトポロジーをカプセル化し、一貫したドメインセマンティクスを提供する不変の値オブジェクト。"""

    def __init__(self, raw_value: Any, strategy: NamingStrategy) -> None:
        self._raw_value = raw_value
        self._strategy = strategy

        if isinstance(raw_value, str):
            self._plugin_name = strategy.get_key(dynamic_name=raw_value)
            self._options_dict = {}
        elif isinstance(raw_value, dict):
            self._plugin_name = strategy.get_key(dynamic_name="auto")
            self._options_dict = {k: v for k, v in raw_value.items() if not k.startswith("_")}
        else:
            self._plugin_name = "auto"
            self._options_dict = {}

    @property
    def is_empty(self) -> bool:
        return self._raw_value is None

    @property
    def plugin_name(self) -> str:
        return self._plugin_name

    @property
    def options_payload(self) -> dict[str, Any]:
        return self._options_dict.copy()


class Component(ABC):

    def __init__(self, target_type: type, naming_strategy: NamingStrategy, mandatory: bool = True) -> None:
        self._target_type = target_type
        self._naming_strategy = naming_strategy
        self._mandatory = mandatory

    @property
    def target_type(self) -> type:
        return self._target_type

    @property
    def naming_strategy(self) -> NamingStrategy:
        return self._naming_strategy

    @property
    def key(self) -> str:
        return self._naming_strategy.get_key()

    @property
    def mandatory(self) -> bool:
        return self._mandatory

    @property
    @abstractmethod
    def plugin_spec_type(self) -> type:
        pass


class InstanceComponent(Component):

    def __init__(self, target_type: type, instance: object) -> None:
        super().__init__(target_type, naming_strategy=FixedKey(""), mandatory=True)
        self._instance = instance

    @property
    def instance(self) -> object:
        return self._instance

    @property
    def plugin_spec_type(self) -> type:
        return self.target_type


class PropertyComponent(Component):

    @property
    def plugin_spec_type(self) -> type:
        return self.target_type


class PluginComponent(Component):

    @property
    def plugin_spec_type(self) -> type:
        return self.target_type


class PluginListComponent(Component):

    def __init__(
        self,
        target_type: type,
        naming_strategy: NamingStrategy,
        nested_component: Component,
        ordered: bool = False,
        mandatory: bool = True,
    ) -> None:
        super().__init__(target_type, naming_strategy=naming_strategy, mandatory=mandatory)
        self._nested_component = nested_component
        self._ordered = ordered

    @property
    def nested_component(self) -> Component:
        return self._nested_component

    @property
    def ordered(self) -> bool:
        return self._ordered

    @property
    def plugin_spec_type(self) -> type:
        return self.nested_component.plugin_spec_type


# カタログの一元集約:WebClientがデフォルトで解決すべき宇宙の構成要素の固定定義
DEFAULT_COMPONENTS: list[Component] = [
    PropertyComponent(str, naming_chain("base_url"), mandatory=False),
    PropertyComponent(str, naming_chain("api_version"), mandatory=False),
    PropertyComponent(float, naming_chain("timeout"), mandatory=False),
    PropertyComponent(dict, naming_chain("default_headers"), mandatory=False),
    PropertyComponent(dict, naming_chain("default_cookies"), mandatory=False),
    PropertyComponent(list, naming_chain("plugin_groups"), mandatory=False),
    PropertyComponent(ProxyOptions, naming_chain("proxy"), mandatory=False),
    PropertyComponent(RedirectOptions, naming_chain("redirect"), mandatory=False),
    PluginComponent(BodyEncoder, naming_chain("encoder"), mandatory=True),
    PluginComponent(BodyDecoder, naming_chain("decoder"), mandatory=True),
    PluginComponent(ClientHttpConnector, naming_chain("http_connector"), mandatory=True),
    PluginComponent(CookieStore, naming_chain("cookie_store"), mandatory=False),
    PluginListComponent(
        PrioritizedFilter,
        naming_chain("filters"),
        nested_component=PluginComponent(ExchangeFilter, PluginNameKey(), mandatory=False),
        ordered=True,
        mandatory=False,
    ),
]

_DYNAMIC_COMPONENTS: list[Component] = []


def register_component(component: Component) -> None:
    """外部の拡張パッケージから、新しい器の設計図を動的に受け入れる公式マウンター。"""
    if component not in _DYNAMIC_COMPONENTS:
        _DYNAMIC_COMPONENTS.append(component)


def _sort_components(components: list[Component]) -> list[Component]:
    """与えられた設計図群のメタデータを解析し、完全なトポロジカルソート順を算出します。"""
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


def compute_ordered_components(config: object) -> list[Component]:
    """WebClientConfig のバリデータ層からダイレクトにキックされ、カタログの集約とソート順の固定を執行します。"""
    from webclient.config import WebClientConfig

    active_components: list[Component] = []
    active_components.append(InstanceComponent(WebClientConfig, None))
    active_components.extend(DEFAULT_COMPONENTS)
    active_components.extend(_DYNAMIC_COMPONENTS)
    return _sort_components(active_components)


def get_all_active_components() -> list[Component]:
    """resolver側から参照され、現在有効なすべての生のコンポーネントカタログの結合リストを返却します。"""
    return DEFAULT_COMPONENTS + _DYNAMIC_COMPONENTS
