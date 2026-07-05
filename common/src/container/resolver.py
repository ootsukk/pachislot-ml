from __future__ import annotations

import typing
from container.exceptions import ComponentInstantiationError

if typing.TYPE_CHECKING:
    from container.context import ResolutionSession
    from container.register import PluginDefinition


class ConstructorResolver:
    """コンストラクタ引数仕様を走査し、コンテナ内部から最適な依存インスタンスを自動探索して引数マップを確定させるリゾルバ。"""

    def resolve_dependencies(
        self, definition: PluginDefinition[object], session: ResolutionSession, /
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        for param_name, dep_type in definition.constructor_dependencies.items():
            try:
                kwargs[param_name] = session.resolve_dependency_node(dep_type, param_name)
            except Exception as err:
                raise ComponentInstantiationError(
                    f"プラグインの引数解決に失敗しました。クラス: {definition.impl_class.__name__}, 引数: {param_name} ({err})"
                ) from err
        return kwargs
