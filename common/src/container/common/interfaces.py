from __future__ import annotations

import contextlib
import types
import typing
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from container.common.metadata import CacheKey

if typing.TYPE_CHECKING:
    from container.common.metadata import CacheKey

@runtime_checkable
class Initializable(Protocol):
    """オブジェクトの初期化ライフサイクルを構造的に保証するプロトコル。"""

    def initialize(self) -> None: ...


@runtime_checkable
class Closable(Protocol):
    """構造的適合性を保証するためのリソース管理プロトコル"""

    def close(self) -> None: ...


class RuntimeContainer(Protocol):
    """コンテナ内アセットに対する読み取り専用の最上位抽象境界インターフェース。"""

    @typing.overload
    def resolve[T](
        self,
        target_type: type[T] | types.GenericAlias,
        /,
        *,
        name: str | None = None,
    ) -> T:
        """単一の具象型またはジェネリックAlias表現に対する厳格なインスタンス解決を実行します。

        対象の型定義がコンテナ内に登録されていない場合、または実体化に失敗した場合は
        ComponentInstantiationError を送出します。
        """
        ...

    @typing.overload
    def resolve[T](
        self,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        /,
        *,
        name: str | None = None,
    ) -> T | None:
        """Union型による型仕様を許容し、None 許容Optionalを内包した動的なインスタンス解決を実行します。

        型仕様に None (type(None)) が含まれており、かつコンテナ内に該当する型が
        登録されていない場合は、例外を送出せず透過的に None を返却します。
        """
        ...

    def resolve[T](
        self,
        target_type: type[T] | types.GenericAlias | types.UnionType,
        /,
        *,
        name: str | None = None,
    ) -> T | None:
        """指定された型仕様および識別名に基づき、依存関係グラフから実体を動的に解決・抽出します。

        位置専用引数およびキーワード専用引数を強制配置することで、クライアント側の引数指定エラーを
        コンパイル静的解析フェーズで完全に防御します。
        """
        ...


@runtime_checkable
class ResolverBuilder(Protocol):
    """InstanceResolver側から再構築を安全にトリガーするためのビルダー抽象インターフェース。"""

    def build(self) -> RuntimeContainer: ...


@runtime_checkable
class ScopeStrategy(Protocol):
    """コンポーネントの生存期間とインスタンス記憶領域を抽象化するスコープ戦略インターフェース。"""

    def get(self, key: CacheKey, /) -> object | None: ...

    def put(self, key: CacheKey, instance: object, /) -> None: ...

    def remove(self, key: CacheKey, /) -> object | None: ...

    def synchronize(self, key: CacheKey, /) -> contextlib.AbstractContextManager[None]: ...

    def clear(self) -> None: ...


class InstantiationStrategy(Protocol):
    """具象プラグインの物理生成手順をカプセル化する戦略インターフェース。"""

    def instantiate[T](self, impl_class: type[T], constructor_kwargs: Mapping[str, object], /) -> T: ...


class ConfigInstantiationStrategy(Protocol):
    """外部構成ペイロードのバリデーションと構造化設定オブジェクトへのパースを司る戦略インターフェース。"""

    def deserialize(self, config_class: type[object], payload: Mapping[str, object], /) -> object: ...


class InstancePostProcessor(Protocol):
    """インスタンス化が完了したオブジェクトに対し初期化の前後に横断加工を施すフックフライト規約。"""

    @property
    def priority(self) -> int: ...

    def post_process_before(self, instance: object, bean_name: str, /) -> object: ...

    def post_process_after(self, instance: object, bean_name: str, /) -> object: ...

@runtime_checkable
class ContextBuilder(Protocol):
    """Container側から再構築や動的実体化を安全にトリガーするためのビルダー抽象インターフェース。"""

    def build(self) -> RuntimeContainer: ...
