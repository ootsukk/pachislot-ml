from __future__ import annotations

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

    @classmethod
    def from_annotation(cls, annotation: object, /) -> ResolvableType[object] | None:
        if annotation is inspect.Parameter.empty:
            return None

        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)

        match origin:
            case typing.Union:
                for arg in args:
                    if arg is type(None):
                        continue
                    if (unwrapped := cls.from_annotation(arg)) is not None:
                        return unwrapped
                return None
            case _:
                if isinstance(annotation, types.UnionType):
                    for arg in args:
                        if arg is type(None):
                            continue
                        if (unwrapped := cls.from_annotation(arg)) is not None:
                            return unwrapped
                    return None

        if origin is not None:
            match annotation:
                case types.GenericAlias():
                    return cls(annotation)
            if isinstance(origin, type):
                return cls(origin)
            return None

        match annotation:
            case type() as t:
                return cls(t)
            case _:
                return None

    @property
    def origin(self) -> type[object]:
        """GenericAliasであればその原型、プレーンな型であれば自身を安全に返却します [Effective Python Item 77]"""
        match self.raw_type:
            case types.GenericAlias() as alias if isinstance(alias.__origin__, type):
                return alias.__origin__
            case type() as t:
                return t
            case _:
                if isinstance(self.raw_type, type):
                    return self.raw_type # type: ignore
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
        """【ジェネリクス化】第1型パラメータを仕様型Tとして安全に抽出・伝播させます [Effective Python Item 41]"""
        args = self.generic_arguments
        if args and isinstance(args[0], type):
            return cast(type[T], args[0])
        return cast(type[T], object)

    def is_assignable_from(self, target_class: type[object], /) -> bool:
        """指定されたクラスが、自身の原型型に対して代入可能か安全に検証します [Effective Python Item 24]"""
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
