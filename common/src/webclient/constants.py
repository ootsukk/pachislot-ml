from __future__ import annotations

from typing import Final

# ルートパッケージ名
ROOT_PACKAGE_NAME: Final[str] = __package__.split(".")[0] if __package__ else "webclient"
# コンフィグファイル名
CONFIG_FILE_NAME: Final[str] = "config.yaml"
# エントリーポイントのインポートパス
ENTRY_POINT_TARGET: Final[str] = f"{ROOT_PACKAGE_NAME}.plugins"
