from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import pkgutil
import re
from graphlib import TopologicalSorter
from importlib.metadata import entry_points
from typing import Any

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
from webclient.cookies import CookieStore
from webclient.plugin import Component, ConfigPropertyComponent, FlatComponent, ListComponent, PluginNameKey


class UniversalPluginResolver:
    """規約と Component の依存グラフに基づいて、一元的にDI解決を行うエンジン"""

    # レジストリキャッシュ構造：{ cache_key: { 仕様の型(type): { 省略プラグイン名(str): 具象クラスの型(type) } } }
    _registry_cache: dict[str, dict[type[Any], dict[str, type[Any]]]] = {}

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
        """🚀 【新設】外部の拡張パッケージ（サードパーティ）から、新しい器の設計図を動的に受け入れる公式マウンター"""
        if component not in cls._COMPONENTS:
            cls._COMPONENTS.append(component)

    @classmethod
    def get_topo_sorted_components(cls) -> list[Component]:
        """仕様（target_type）が内包する depends_on の依存グラフを解析し、
        正しい解決タイムライン順に並んだ Component のリストを一元算出します。
        """
        component_map = {c.target_type: c for c in cls._COMPONENTS}
        graph: dict[type[Any], list[type[Any]]] = {}

        for c in cls._COMPONENTS:
            target = c.target_type
            if isinstance(c, ListComponent):
                target = c.nested_component.target_type

            meta = getattr(target, "__plugin_meta__", None)
            depends_on = meta.depends_on if meta else []

            # 依存先が ListComponent 要素の仕様型（例: ExchangeFilter）である場合のトポロジーマッピングの正規化
            graph[c.target_type] = [
                component_map[dep].target_type if dep in component_map else dep
                for dep in depends_on
                if dep in component_map
                or any(
                    isinstance(xc, ListComponent) and xc.nested_component.target_type == dep for xc in cls._COMPONENTS
                )
            ]

        ts = TopologicalSorter(graph)
        sorted_specs = list(ts.static_order())
        return [component_map[spec] for spec in sorted_specs if spec in component_map]

    @classmethod
    def resolve_all(
        cls, config: WebClientConfig, client_extension_pool: dict[type[Any], Any], explicit_pool: dict[type[Any], Any]
    ) -> dict[type[Any], Any]:
        """トポロジカルソート順に、すべてのコンポーネントを透過的に解決するメインループ"""
        local_pool = dict(client_extension_pool)
        plugin_groups = getattr(config, "plugin_groups", ["webclient.plugins"])

        local_pool[WebClientConfig] = config

        for component in cls.get_topo_sorted_components():
            resolved_asset = component.resolve_asset(config, local_pool, explicit_pool, plugin_groups)
            if resolved_asset is not None:
                local_pool[component.target_type] = resolved_asset

        return local_pool

    @classmethod
    def _instantiate_flat_core(
        cls,
        component: Component,
        raw_section: Any,
        key_name: str,
        type_pool: dict[type[Any], Any],
        plugin_groups: list[str],
    ) -> Any:
        """文字列名からのクラス逆引き、Configurableの自動構成、
        リフレクションによる引数自動マッピングをすべて一元執行するコア。
        """
        spec_type = component.target_type

        # 1. YAML等の設定値からターゲット名を取得（未指定、空、または辞書内包の場合は None や auto になり得る）
        target_name: str | None = None
        if isinstance(raw_section, str):
            target_name = raw_section
        elif isinstance(raw_section, dict):
            target_name = key_name

        # キャッシュ化されたプラグイン＆コンポーネントレジストリをロード
        regs = cls._load_registries_cached(plugin_groups)
        spec_registry = regs.get(spec_type, {})

        impl_class: type[Any] | None = None

        # 優先度選出アルゴリズム
        # 名前が未指定（None）または "auto" の場合は、個別カテゴリのハードコードを一切行わず、
        # レジストリに適合登録されている全具象クラスを priority の降順（大きい順）でソートし、
        # 現在の環境で生存している最高プライオリティの実装クラスを「全自動」で最優先引き当てする。
        if not target_name or target_name == "auto":
            available_classes = list(spec_registry.values())
            if available_classes:
                available_classes.sort(key=lambda c: getattr(c.__plugin_impl_meta__, "priority", 100), reverse=True)
                impl_class = available_classes[0]
        else:
            # 明示的な名前指定がある場合は、ピンポイントでレジストリから引き当て
            impl_class = spec_registry.get(target_name)

        if impl_class is None:
            if component.mandatory:
                raise LookupError(
                    f"【必須プラグイン未発見エラー】生存に必須な仕様 '{spec_type.__name__}' に対する "
                    f"有効な実装プラグイン（指定名: '{target_name}'）が環境内に見つかりません。 "
                    f"パッケージのインストール状態、または @dependency_module の検証条件を確認してください。"
                )
            return None


        config_object: Any = None
        local_type_pool = dict(type_pool)

        # 2. Configurable なクラスだった場合の、専用 Config オブジェクトの動的生成
        if issubclass(impl_class, Configurable):
            config_class = cls._extract_config_type(impl_class)

            # 入力元ソースデータの規約マッピング
            source_input = (
                raw_section
                if isinstance(raw_section, dict)
                else (
                    getattr(
                        type_pool.get(WebClientConfig),
                        component.strategy.get_key(spec_type, key_name).replace("_name", "") + "_options",
                        {},
                    )
                    if type_pool.get(WebClientConfig)
                    else {}
                )
            )

            if config_class and isinstance(source_input, dict):
                # プライベート属性（_アンダースコア開始）を弾いて dataclass を安全にマウント
                source_input = config_class(**{k: v for k, v in source_input.items() if not k.startswith("_")})

            # 具象クラスの作法に従って専用設計図オブジェクトを錬成
            config_object = impl_class.create_config(source_input, type_pool=local_type_pool)
            if config_object is not None and config_class is not None:
                local_type_pool[type(config_object)] = config_object
                local_type_pool[config_class] = config_object

        # 3. 【リフレクション】シグネチャをスキャンし、引数を依存関係プールから全自動インジェクション
        sig = inspect.signature(impl_class)
        kwargs: dict[str, Any] = {}

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            p_type = param.annotation
            resolved_val = cls._find_from_pool(p_type, local_type_pool)
            if resolved_val is not None:
                kwargs[param_name] = resolved_val

        # 4. 完全自動インジェクションされた引数を流し込んで、ピュアにインスタンス化して出荷
        try:
            return impl_class(**kwargs)
        except TypeError:
            return impl_class()

    @classmethod
    def evaluate_version(cls, actual_version: str, constraint_expr: str) -> bool:
        """'==1.0' や '>=0.5.0' といったセマンティックバージョン条件式を外部依存なしでロジカルに評価する"""
        match = re.match(r"^([>=<!]+)\s*([\d.]+)", constraint_expr.strip())
        if not match:
            return True
        operator, required_str = match.groups()

        actual_parts = [int(x) for x in re.findall(r"\d+", actual_version)]
        required_parts = [int(x) for x in re.findall(r"\d+", required_str)]

        # 配列の長さを長い方に揃える（[1, 2] と [1, 2, 0] を安全に同一視する）
        max_len = max(len(actual_parts), len(required_parts))
        actual_parts += [0] * (max_len - len(actual_parts))
        required_parts += [0] * (max_len - len(required_parts))

        if operator == "==":  # noqa: SIM116
            return actual_parts == required_parts
        elif operator == ">=":
            return actual_parts >= required_parts
        elif operator == "<=":
            return actual_parts <= required_parts
        elif operator == ">":
            return actual_parts > required_parts
        elif operator == "<":
            return actual_parts < required_parts

        return False

    @classmethod
    def _find_from_pool(cls, p_type: Any, type_pool: dict[type[Any], Any]) -> Any | None:
        """型プールから完全一致、あるいはインターフェース互換（サブクラス関係）のある実体を引き当てる"""
        if p_type in type_pool:
            return type_pool[p_type]
        for pool_type, pool_obj in type_pool.items():
            if isinstance(p_type, type) and issubclass(pool_type, p_type):
                return pool_obj
        return None

    @classmethod
    def _load_registries_cached(cls, plugin_groups: list[str]) -> dict[type[Any], dict[str, type[Any]]]:
        """プラグイングループの文字列表現をキーに、スキャン結果のレジストリをメモリキャッシュ統治する"""
        cache_key = "|".join(sorted(plugin_groups))
        if cache_key in cls._registry_cache:
            return {spec: slot.copy() for spec, slot in cls._registry_cache[cache_key].items()}

        # 🚀 【タイムラインの調律】具象クラス（実装）をスキャンし始める「前」に、
        # サードパーティ側がエントリーポイントに仕込んだ Component の動的拡張ファイル（mount.py）を全探索して強制 import する！
        try:
            for ep in entry_points(group="webclient.components"):
                # ep.load() が走った瞬間に拡張側の mount.py が評価され、
                # 内部から UniversalPluginResolver.register_component がキックされて _COMPONENTS が自動拡張される。
                ep.load()
        except Exception:
            pass

        # 拡張が完全に完了した、最新の _COMPONENTS をベースにして初期の空マップを動的生成
        new_registries: dict[type[Any], dict[str, type[Any]]] = {}
        for c in cls._COMPONENTS:
            target = c.nested_component.target_type if isinstance(c, ListComponent) else c.target_type
            new_registries[target] = {}

        # 1. webclient パッケージ配下の全モジュールを自動走査
        import webclient

        for module_info in pkgutil.walk_packages(webclient.__path__, webclient.__name__ + "."):
            try:
                mod = importlib.import_module(module_info.name)
                for _, cls_obj in inspect.getmembers(mod, inspect.isclass):
                    if not cls_obj.__module__.startswith("webclient."):
                        continue
                    cls._classify_and_register(cls_obj, new_registries)
            except ImportError:
                continue

        # 2. pip install された外部のサードパーティ拡張エントリーポイント（プラグイン）を自動走査
        for group_name in plugin_groups:
            try:
                for ep in entry_points(group=group_name):
                    cls._classify_and_register(ep.load(), new_registries)
            except Exception:
                continue

        cls._registry_cache[cache_key] = new_registries
        return {spec: slot.copy() for spec, slot in new_registries.items()}

    @classmethod
    def _classify_and_register(cls, cls_obj: type[Any], registries: dict[type[Any], dict[str, type[Any]]]) -> None:
        """スキャンした具象クラスをメタデータ駆動で自律検証し、適合する仕様スロットへ自動マウントする"""
        if inspect.isabstract(cls_obj):
            return

        for spec_type in registries:
            if issubclass(cls_obj, spec_type) and cls_obj is not spec_type:
                # 💡 【1. @plugin_impl の厳格な義務付け一元チェック】
                # サフィックス推論マジックは全廃。スキャンした完成品クラスにデコレータがなければ即座に例外爆破して起動を止める。
                impl_meta = getattr(cls_obj, "__plugin_impl_meta__", None)
                if not impl_meta:
                    raise TypeError(
                        f"【厳格規約違反】具象実装クラス '{cls_obj.__name__}' に "
                        f"@plugin_impl デコレータが付与されていません。義務付けられています。"
                    )

                # 💡 【2. コネクター具象クラスだった場合の、@dependency_module 義務付け＆オーディション】
                if issubclass(cls_obj, ClientHttpConnector):
                    dep_meta = getattr(cls_obj, "__dependency_meta__", None)
                    if not dep_meta:
                        raise TypeError(
                            f"【厳格規約違反】コネクター具象クラス '{cls_obj.__name__}' に "
                            f"@dependency_module デコレータが付与されていません。義務付けられています。"
                        )

                    is_env_valid = False
                    try:
                        # 実際の環境にある pip パッケージの実態をスキャンしてオーディションを実行
                        for dist in importlib.metadata.distributions():
                            if (
                                dist.metadata["Name"].lower() == dep_meta.module_name.lower().replace("-", "_")
                                and cls.evaluate_version(dist.version, dep_meta.version)
                            ):
                                is_env_valid = True
                                break
                    except Exception:
                        is_env_valid = False

                    # 生存条件を満たしていない不適合コネクターは、存在しなかったものとしてレジストリ登録を即座に却下
                    if not is_env_valid:
                        return

                # オーディションを完全にクリアしたプラグインを、アノテーションされた本物の名前を直撃してレジストリへ登録
                registries[spec_type][impl_meta.value] = cls_obj
                break

    @classmethod
    def _extract_config_type(cls, impl_class: type[Any]) -> type[Any] | None:
        """Configurableな具象クラスの、基底ジェネリクス引数から本物のオプション型（dataclass）をリフレクション抽出する"""
        for base in impl_class.__orig_bases__:  # type: ignore
            if base.__origin__ is Configurable:
                args = base.__args__
                if args and isinstance(args[0], type):
                    return args[0]
        return None
