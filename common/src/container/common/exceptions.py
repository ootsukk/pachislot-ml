from __future__ import annotations


class ContainerError(Exception):
    """DIコンテナの内部処理における例外の絶対的基底クラス。"""


class CircularDependencyError(ContainerError):
    """依存関係のトポロジーに閉路(循環参照)が検出された際の例外。"""


class ComponentInstantiationError(ContainerError):
    """オブジェクトの動的生成、設定値のバインディング、または引数解決に失敗した際の例外。"""
