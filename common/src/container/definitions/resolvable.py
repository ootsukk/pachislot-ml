import inspect
import types
import typing
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, cast


@dataclass(frozen=True)
class ResolvableType[T]:
    """型およびGenericAliasのメタ操作をカプセル化する不変のドメイン型オブジェクト"""

    raw_type: Final[type[object] | types.GenericAlias]
    is_optional: Final[bool] = False

    @classmethod
    def from_annotation(cls, annotation: object, /) -> ResolvableType[object] | None:
        """型アノテーションからUnion型およびOptional性を解体抽出し、適切な状態を持つ不変インスタンスを鋳造します。"""
        if annotation is inspect.Parameter.empty:
            return None

        match annotation:
            case _ if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
                args = typing.get_args(annotation)
                is_opt = type(None) in args
                remaining = [a for a in args if a is not type(None)]
                if remaining and isinstance(remaining[0], type | types.GenericAlias):
                    return cls(remaining[0], is_optional=is_opt)
                return cls(object, is_optional=is_opt)

            case types.GenericAlias() as alias:
                return cls(alias, is_optional=False)

            case type() as t:
                return cls(t, is_optional=False)

            case _:
                return None

    @property
    def origin(self) -> type[object]:
        if (origin_type := typing.get_origin(self.raw_type)) is not None and isinstance(origin_type, type):
            return cast(type[object], origin_type)

        if isinstance(self.raw_type, type):
            return cast(type[object], self.raw_type)

        raise TypeError(f"メタ型コンテキストをクラスオブジェクトとして確定できません: {self.raw_type}")

    @property
    def is_generic(self) -> bool:
        """型パラメータを持つジェネリック表現であるか判定します。"""
        return isinstance(self.raw_type, types.GenericAlias)

    @property
    def generic_arguments(self) -> tuple[object, ...]:
        """内包する型パラメータのタプルを返却します。"""
        match self.raw_type:
            case types.GenericAlias() as alias:
                return alias.__args__
            case _:
                return ()

    @property
    def first_generic_argument(self) -> type[T]:
        """第1型パラメータを仕様型Tとして安全に抽出・伝播させます [Effective Python Item 41]。"""
        args = self.generic_arguments
        if args and isinstance(args[0], type):
            return cast(type[T], args[0])
        return cast(type[T], object)

    def is_assignable_from(self, target_class: type[object], /) -> bool:
        """指定されたクラスが、自身の原型型に対して代入可能か安全に検証します [Effective Python Item 24]。"""
        try:
            return issubclass(target_class, self.origin)
        except TypeError:
            return False

    def is_collection(self) -> bool:
        """原型がシーケンスやリストなどのコレクションコンテナであるか判定します。"""
        try:
            return issubclass(self.origin, Sequence) and self.origin is not str
        except TypeError:
            return False
