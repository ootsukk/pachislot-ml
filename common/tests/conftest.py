from __future__ import annotations

import dataclasses
from collections.abc import Generator

import pytest


@dataclasses.dataclass(frozen=True)
class DummyAppConfig:
    """
    YAMLファイルからデシリアライズされたアプリケーション構成を模倣するデータクラス。
    各プラグイン名（main_service, comp_a等）に対応する設定ペイロードを属性として保持する。
    """

    database: dict[str, object]
    main_service: dict[str, object]
    comp_a: dict[str, object]
    comp_b: dict[str, object]


@pytest.fixture(scope="function")
def yaml_backed_config() -> DummyAppConfig:
    """
    本番環境におけるYAML管理設定の読み込み結果をエミュレートするフィクスチャ。
    builder.pyの __dict__ ルックアップを介して、各コンポーネントへ正確なペイロードを分配する。
    """
    return DummyAppConfig(
        database={
            "url": "sqlite:///:memory:",
            "timeout": 30,
        },
        main_service={
            "enabled": True,
            "max_connections": 5,
        },
        comp_a={
            "enabled": True,
        },
        comp_b={
            "enabled": True,
        },
    )


@pytest.fixture(scope="function")
def context_isolation() -> Generator[None]:
    """テスト実行ごとのグローバル状態およびキャッシュの独立性を担保するフィクスチャ。"""
    yield
