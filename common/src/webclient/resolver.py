from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import logging
import operator
import pkgutil
import re
import sys
from collections.abc import Callable
from graphlib import TopologicalSorter
from importlib.metadata import entry_points
from typing import Any, cast

from webclient.base import (
    ClientHttpConnector,
    Configurable,
    ExchangeFilter,
    PrioritizedFilter,
    ProxyOptions,
    RedirectOptions,
)
from webclient.codec import BodyDecoder, BodyEncoder
from webclient.config import WebClientConfig
from webclient.constants import ROOT_PACKAGE_NAME
from webclient.cookies import CookieStore
from webclient.plugin import Component, ConfigPropertyComponent, FlatComponent, ListComponent, PluginNameKey

# 本モジュール専用のロガーの捕捉
_LOGGER = logging.getLogger(f"{ROOT_PACKAGE_NAME}.resolver")


class UniversalPluginResolver:
    """規約と Component の依存グラフに基づいて、一元的にDI解決を行うエンジン。

    特定の具象名やパッケージ名に対する直接的な知識を持たず、
    メタデータと依存関係グラフのトポロジー解析によって環境の具象を一元解決します。
    """

    # レジストリキャッシュ構造：{ cache_key: { 仕様クラス型: { 省略プラグイン名: 具象クラス型 } } }
    _registry_cache: dict[str, dict[type, dict[str, type]]] = {}

    # コアシステムが標準装備している基本Componentの設計図リスト
    _COMPONENTS: list[Component] = [
        ConfigPropertyComponent(ProxyOptions, key="proxy", mandatory=False),
        ConfigPropertyComponent(RedirectOptions, key="redirect", mandatory=False),
        FlatComponent(BodyEncoder, key="encoder"),
        FlatComponent(BodyDecoder, key="decoder"),
        FlatComponent(ClientHttpConnector, key="http_connector"),
        FlatComponent(CookieStore, key="cookie_store", mandatory=False),
        ListComponent(
            PrioritizedFilter,
            key="filters",
            nested_component=FlatComponent(ExchangeFilter, key=PluginNameKey(), mandatory=False),
            ordered=True,
            mandatory=False,
        ),
    ]


    @classmethod
    def register_component(cls, component: Component) -> None:
        """外部の拡張パッケージから、新しい器の設計図を動的に受け入れる公式マウンター。"""
        if component not in cls._COMPONENTS:
            cls._COMPONENTS.append(component)

    @classmethod
    def topological_ordered_components(cls) -> list[Component]:
        """仕様が内包する depends_on の依存グラフを解析し、正しい解決順の Component リストを算出します。"""

        # マップ構造：{ 仕様のクラス型(type): Componentオブジェクト(Component) }
        component_map: dict[type, Component] = {c.target_type: c for c in cls._COMPONENTS}

        # グラフ構造：{ 依存元の仕様クラス型(type): [依存先の仕様クラス型(type), ...] }
        graph: dict[type, list[type]] = {}

        for c in cls._COMPONENTS:
            target = c.plugin_spec_type

            meta = getattr(target, "__plugin_meta__", None)
            depends_on: list[type] = meta.depends_on if meta else []

            valid_dependencies: list[type] = []

            for dep in depends_on:
                # 依存先がシステムに登録されている有効なパーツか否かを検証
                is_registered = dep in component_map
                is_plugin_spec = any(xc.plugin_spec_type == dep for xc in cls._COMPONENTS)

                if not (is_registered or is_plugin_spec):
                    continue  # 未登録の無効な依存先であれば安全にスキップ

                # グラフのノード（型）として正しく機能するように変換
                # component_map に登録されている型であればその target_type を使い、そうでなければそのまま使用
                node_type = component_map[dep].target_type if dep in component_map else dep
                valid_dependencies.append(node_type)

            graph[c.target_type] = valid_dependencies

        ts = TopologicalSorter(graph)

        # リスト構造：[ 依存関係順に並んだ仕様クラス型(type), ... ]
        sorted_specs: list[type] = list(ts.static_order())

        # ソートされた型の順番に従って、元の Component オブジェクトのリストへと復元する
        return [component_map[spec] for spec in sorted_specs if spec in component_map]

    @classmethod
    def resolve_all(
        cls,
        config: WebClientConfig,
        client_extension_pool: dict[type, object],
        explicit_pool: dict[type, object],
    ) -> dict[type, object]:
        """トポロジカルソート順に、すべてのコンポーネントを透過的に解決するメインループ。"""
        local_pool: dict[type, object] = dict(client_extension_pool)

        # エントリーポイントのグループ名を動的定数に基づいてブレンド
        plugin_groups = getattr(config, "plugin_groups", [f"{ROOT_PACKAGE_NAME}.plugins"])

        local_pool[WebClientConfig] = config

        for component in cls.topological_ordered_components():
            resolved_asset = component.resolve_asset(config, local_pool, explicit_pool, plugin_groups)
            if resolved_asset is not None:
                local_pool[component.target_type] = resolved_asset

        return local_pool

    @classmethod
    def resolve_asset(
        cls,
        component: Component,
        raw_setting: dict[str, object] | str | None,
        key_name: str,
        type_pool: dict[type, object],
        plugin_groups: list[str],
    ) -> object | None:
        """文字列名からのクラス逆引き、Configurableの自動構成、インジェクションを一元執行するコア。

        すべての具体的な生成手順を下請け関数へ委譲し、自身はライフサイクルの統治に特化します Lights out.
        """
        spec_type = component.plugin_spec_type

        # プラグイン実装名の特定（設定データのトポロジー分析）
        plugin_name: str | None = (
            raw_setting if isinstance(raw_setting, str)
            else (key_name if isinstance(raw_setting, dict) else None)
        )

        # キャッシュ化されたレジストリからこの仕様（spec_type）に適合するプラグインマップを取得
        regs = cls._get_plugin_registry(plugin_groups)
        spec_registry = regs.get(spec_type, {})

        # 具象クラスの動的逆引き
        impl_class = cls._resolve_implementation_class(
            spec_registry=spec_registry,
            plugin_name=plugin_name
        )

        if impl_class is None:
            if component.mandatory:
                raise LookupError(
                    f"生存に必須な仕様 '{spec_type.__name__}' に対する "
                    f"有効な実装プラグイン（指定名: '{plugin_name}'）が環境内に見つかりません。"
                )
            return None

        # 今回の解決タイムラインでのみ使用する、ローカルな型プールを複製
        local_type_pool = dict(type_pool)

        # 3. Configurableな専用Configオブジェクトの動的生成
        config_object = cls._build_configurable_options(
            component=component,
            impl_class=impl_class,
            raw_setting=raw_setting,
            key_name=key_name,
            type_pool=local_type_pool,
        )

        if config_object is not None:
            config_class = cls._extract_config_type(impl_class)
            if config_class is not None:
                local_type_pool[type(config_object)] = config_object
                local_type_pool[config_class] = config_object

        # 4. 【関数化】リフレクションによる自動依存インジェクションとインスタンス化の執行
        return cls._inject_dependencies_and_instantiate(
            impl_class=impl_class,
            type_pool=local_type_pool
        )

    @classmethod
    def _resolve_implementation_class(
        cls,
        spec_registry: dict[str, type],
        plugin_name: str | None,
    ) -> type | None:
        """レジストリとプラグイン名、および優先度（priority）規約に基づいて具象実装クラスを逆引きします。"""
        # 名前が未指定、または "auto" の場合は、生存している最高プライオリティの実装クラスを自動ハント
        if not plugin_name or plugin_name == "auto":
            available_classes = list(spec_registry.values())
            if not available_classes:
                return None

            # @plugin_impl に定義された priority の降順（大きい順）でソート
            available_classes.sort(key=lambda c: getattr(c.__plugin_impl_meta__, "priority", 100), reverse=True)
            return available_classes[0]

        # 明示的な名前指定がある場合は、ピンポイントでレジストリから引き当て
        return spec_registry.get(plugin_name)

    @classmethod
    def _build_configurable_options(
        cls,
        component: Component,
        impl_class: type,
        raw_setting: dict[str, object] | str | None,
        key_name: str,
        type_pool: dict[type, object],
    ) -> object | None:
        """具象クラスがConfigurableな場合、設定値から専用のConfigオブジェクトを錬成する独立関数。"""
        if not issubclass(impl_class, Configurable):
            return None

        config_class = cls._extract_config_type(impl_class)
        if not config_class:
            return None

        # 入力元ソースデータ（生の設定値）の抽出
        source_input: dict[str, object] = {}
        if isinstance(raw_setting, dict):
            source_input = raw_setting
        else:
            client_config = type_pool.get(WebClientConfig)
            if isinstance(client_config, WebClientConfig):
                strategy_key = component.strategy.get_key(component.target_type, key_name).replace("_name", "") + "_options"
                source_input = cast(dict[str, object], getattr(client_config, strategy_key, {}))

        clean_input: dict[str, object] = {k: v for k, v in source_input.items() if not k.startswith("_")}
        options_instance = config_class(**clean_input)

        return impl_class.create_config(options_instance, type_pool=type_pool)


    @classmethod
    def _extract_config_type(cls, impl_class: type) -> type | None:
        """Configurableな具象クラスの、基底ジェネリクス引数から本物のオプション型をリフレクション抽出する。"""
        orig_bases = getattr(impl_class, "__orig_bases__", [])
        for base in orig_bases:
            if getattr(base, "__origin__", None) is Configurable:
                args = base.__args__
                if args and isinstance(args[0], type):
                    return args[0]
        return None


    @classmethod
    def _inject_dependencies_and_instantiate(
        cls,
        impl_class: type,
        type_pool: dict[type, object],
    ) -> object:
        """ターゲット実装クラスのコンストラクタを解析し、プールから型安全に自動インジェクションして実体を錬成します。"""
        # 具象クラスのコンストラクタのシグネチャをスキャン
        sig = inspect.signature(impl_class)

        # マップ構造：{ コンストラクタの引数名(str): 注入するインスタンス実体(object) }
        # 例：{ "config": LogTrackerOptions, "logger": Logger }
        kwargs: dict[str, object] = {}

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            p_type = param.annotation
            resolved_val = cls._find_from_pool(p_type, type_pool)
            if resolved_val is not None:
                kwargs[param_name] = resolved_val

        # 完全に調律され、自動インジェクションされた引数群を流し込んで具象インスタンスを new して出荷
        try:
            return impl_class(**kwargs)
        except TypeError:
            # 引数なしのフォールバックルート
            return impl_class()

    @classmethod
    def evaluate_version(cls, actual_version: str, constraint_expr: str) -> bool:
        """セマンティックバージョン条件式を外部依存なしでロジカルに評価する。"""
        match = re.match(r"^([>=<!]+)\s*([\d.]+)", constraint_expr.strip())
        if not match:
            return True
        op_str, required_str = match.groups()

        actual_parts = [int(x) for x in re.findall(r"\d+", actual_version)]
        required_parts = [int(x) for x in re.findall(r"\d+", required_str)]

        max_len = max(len(actual_parts), len(required_parts))
        actual_parts += [0] * (max_len - len(actual_parts))
        required_parts += [0] * (max_len - len(required_parts))

        version_operators: dict[str, Callable[[list[int], list[int]], bool]] = {
            "==": operator.eq,
            ">=": operator.ge,
            "<=": operator.le,
            ">": operator.gt,
            "<": operator.lt,
        }

        comp_func = version_operators.get(op_str)
        if comp_func:
            return comp_func(actual_parts, required_parts)

        return False

    @classmethod
    def _find_from_pool(cls, p_type: type | Any, type_pool: dict[type, object]) -> object | None:
        """型プールから完全一致、あるいはインターフェース互換のある実体を引き当てる。"""
        # 第一段階：完全一致（Identity Match）の最優先探索
        if p_type in type_pool:
            return type_pool[p_type]

        # 第二段階：見つからない場合のみ、サブクラス互換（Assignability Match）を探索
        for pool_type, pool_obj in type_pool.items():
            if isinstance(p_type, type) and issubclass(pool_type, p_type):
                return pool_obj
        return None

    @classmethod
    def _get_plugin_registry(cls, plugin_groups: list[str]) -> dict[type, dict[str, type]]:
        """プラグイングループの設定に基づいて、スキャン済みのプラグインレジストリをハントします。
        すでに同一グループでのスキャン実績がある場合は、メモリキャッシュから即座に返却します。
        """
        # プラグイングループの組み合わせからユニークなキャッシュキーを生成
        cache_key = "|".join(sorted(plugin_groups))
        if cache_key in cls._registry_cache:
            # マップ構造：{ 仕様クラス型(type): { 省略プラグイン名(str): 具象クラス型(type) } }
            return {spec: slot.copy() for spec, slot in cls._registry_cache[cache_key].items()}

        # 外部のサードパーティが仕込んだ拡張コンポーネント（mount.py）を事前ロード
        cls._load_external_extension_components()

        # スキャン結果を格納する初期の空レジストリマップを用意
        new_registries: dict[type, dict[str, type]] = {}
        for c in cls._COMPONENTS:
            new_registries[c.plugin_spec_type] = {}

        # コアパッケージ（webclient.*）配下の全クラスを自動 walk スキャン
        cls._scan_internal_packages(new_registries)

        # pipインストールされた外部のサードパーティ拡張プラグインを自動走査
        cls._scan_external_entry_points(plugin_groups, new_registries)

        # 今回のスキャン結果をメモリキャッシュへ記憶して、最終形態のレジストリを返却
        cls._registry_cache[cache_key] = new_registries
        return {spec: slot.copy() for spec, slot in new_registries.items()}

    @classmethod
    def _load_external_extension_components(cls) -> None:
        """サードパーティがエントリーポイントにマウントしたコンポーネント拡張設計図を強制ロードします。"""
        for ep in entry_points(group=f"{ROOT_PACKAGE_NAME}.components"):
            try:
                # load() が走った瞬間に拡張側の mount.py が評価され、register_component がキックされる
                ep.load()
            except Exception as err:
                _LOGGER.warning(f"外部コンポーネントのロードに失敗しました (EntryPt: {ep.name}): {err}")

    @classmethod
    def _scan_internal_packages(cls, registries: dict[type, dict[str, type]]) -> None:
        """動的ルートパッケージの配下を物理探索し、規約を満たす内部プラグインをレジストリへ登録します。"""
        root_module = sys.modules.get(ROOT_PACKAGE_NAME)
        if not (root_module and hasattr(root_module, "__path__")):
            return

        # パッケージの物理ディレクトリ空間を再帰的に walk 走査
        for module_info in pkgutil.walk_packages(root_module.__path__, root_module.__name__ + "."):
            try:
                mod = importlib.import_module(module_info.name)
                # モジュール内に定義されているクラス群をリフレクションスキャン
                for _, cls_obj in inspect.getmembers(mod, inspect.isclass):
                    # 他の無関係な外部パッケージから import されて紛れ込んだクラスを厳格に除外
                    if not cls_obj.__module__.startswith(f"{ROOT_PACKAGE_NAME}."):
                        continue
                    cls._classify_and_register(cls_obj, registries)
            except ImportError:
                continue

    @classmethod
    def _scan_external_entry_points(cls, plugin_groups: list[str], registries: dict[type, dict[str, type]]) -> None:
        """サードパーティ製の実装プラグインのエントリーポイント群を走査し、レジストリへ合流させます。"""
        for group_name in plugin_groups:
            try:
                for ep in entry_points(group=group_name):
                    # エントリーポイントから具象クラスを動的インポートして分類登録を実行
                    cls._classify_and_register(ep.load(), registries)
            except Exception as err:
                _LOGGER.warning(f"プラグイングループのロード中にエラーが発生しました (Group: {group_name}): {err}")
                continue

    @classmethod
    def _classify_and_register(cls, cls_obj: type, registries: dict[type, dict[str, type]]) -> None:
        """スキャンした具象クラスをメタデータ駆動で自律検証し、適合する仕様スロットへ自動マウントします。"""
        if inspect.isabstract(cls_obj):
            return

        # レジストリの器のトポロジー構造：
        # { 仕様クラス型(type): { 省略プラグイン名(str): 具象クラス型(type) } }
        for spec_type in registries:
            # 自身が仕様クラスそのものである場合は除外し、純粋な具象サブクラス（実装）のみを対象とする
            if cls_obj is spec_type or not issubclass(cls_obj, spec_type):
                continue

            # @plugin_impl デコレータによるメタデータの義務付け検証
            impl_meta = getattr(cls_obj, "__plugin_impl_meta__", None)
            if not impl_meta:
                raise TypeError(
                    f"【厳格規約違反】具象実装クラス '{cls_obj.__name__}' に "
                    f"@plugin_impl デコレータが付与されていません。義務付けられています。"
                )

            # ClientHttpConnectorの条件を満たさない場合は登録しない
            if issubclass(cls_obj, ClientHttpConnector) and not cls._is_connector_dependency_satisfied(cls_obj):
                return

            # 具象実装クラスを、省略名をキーにして登録する
            # マップ構造：registries[仕様型][省略プラグイン名] = 具象実装型
            registries[spec_type][impl_meta.value] = cls_obj
            break

    @classmethod
    def _is_connector_dependency_satisfied(cls, connector_class: type) -> bool:
        """コネクター具象クラスが要求する外部サードパーティ製ライブラリのインストール状態とバージョンを検証します。"""
        # @dependency_module デコレータによる依存メタデータの義務付け検証
        dep_meta = getattr(connector_class, "__dependency_meta__", None)
        if not dep_meta:
            raise TypeError(
                f"【厳格規約違反】コネクター具象クラス '{connector_class.__name__}' に "
                f"@dependency_module デコレータが付与されていません。義務付けられています。"
            )

        try:
            # 現在の実行環境（仮想環境含む）に pip インストールされている全ディストリビューションを走査
            for dist in importlib.metadata.distributions():
                # 大文字小文字の区別、およびハイフンとアンダースコアの表記揺れを排除した正規化マッチング
                normalized_dist_name = dist.metadata["Name"].lower().replace("-", "_")
                normalized_target_name = dep_meta.module_name.lower().replace("-", "_")

                # パッケージ名が完全一致し、かつ条件式のセマンティックバージョン評価をクリアした場合のみ適合
                if normalized_dist_name == normalized_target_name and cls.evaluate_version(dist.version, dep_meta.version):
                    return True
        except Exception:
            # スキャン中の予期せぬI/Oエラーや破損パッケージ遭遇時は、安全のために不適合扱いとする
            return False

        return False
