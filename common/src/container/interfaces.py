from __future__ import annotations

from collections.abc import Mapping
import types
from typing import Protocol, runtime_checkable


@runtime_checkable
class Initializable(Protocol):
    """オブジェクトの初期化ライフサイクルを構造的に保証するプロトコル。"""

    def initialize(self) -> None: ...


class ApplicationContext(Protocol):
    """コンテナ内アセットに対する読み取り専用の最上位抽象境界インターフェース。"""

    def get_instance[T](self, target_type: type[T] | types.GenericAlias, /, *, name: str | None = None) -> T: ...


class InstantiationStrategy(Protocol):
    """具象プラグインの物理生成手順をカプセル化する戦略インターフェース。"""

    def instantiate[T](self, impl_class: type[T], constructor_kwargs: Mapping[str, object], /) -> T: ...


class ConfigInstantiationStrategy(Protocol):
    """外部構成ペイロードのバリデーションと構造化設定オブジェクトへのパースを司る戦略インターフェース。"""

    def deserialize(self, config_class: type[object], payload: Mapping[str, object], /) -> object: ...


class BeanPostProcessor(Protocol):
    """インスタンス化が完了したオブジェクトに対し初期化の前後に横断加工を施すフックフライト規約。"""

    @property
    def priority(self) -> int: ...

    def post_process_before_initialization(self, instance: object, bean_name: str, /) -> object: ...

    def post_process_after_initialization(self, instance: object, bean_name: str, /) -> object: ...