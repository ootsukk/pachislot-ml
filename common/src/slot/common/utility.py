import re
import uuid
from pathlib import Path
from string import Template
from typing import Any

import jaconv
import pathvalidate
import yaml


def load_yaml_config(file_path: Path | str, context: dict[str, Any] | None = None) -> dict[str, Any]:

    target_path = Path(file_path)

    if not target_path.is_file():
        raise FileNotFoundError(f"Target configuration file not found: {target_path}")

    try:
        with target_path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
            if data is None:
                return {}
            if context:
                data = _format_yaml_values(data, context)

            return data if isinstance(data, dict) else {}

    except yaml.YAMLError as err:
        raise err


def _format_yaml_values(data: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """辞書やリスト内の文字列に対して、contextの値を埋め込みます。"""
    if not context:
        return data

    if isinstance(data, dict):
        return {k: _format_yaml_values(v, context) for k, v in data.items()}

    if isinstance(data, list):
        return [_format_yaml_values(item, context) for item in data]

    if isinstance(data, str):
        template_formatted = Template(data).safe_substitute(context)
        try:
            return template_formatted.format_map(context)
        except KeyError:
            return data

    return data


def normalize_filename(filename: str, max_chars: int = 50) -> str:
    path_obj = Path(filename or "")

    base_name = path_obj.stem
    extension = path_obj.suffix

    # パス区切り文字などを除去して、ファイル名本体が有効かチェック
    invalid_chars = r'[\x00-\x1F\x7F\\/:*?"<>|]'
    clean_base = re.sub(invalid_chars, "", base_name).strip()

    base_name = clean_base if clean_base else uuid.uuid4().hex

    base_name = jaconv.h2z(base_name, ascii=True, digit=True, kana=True)

    base_name = base_name.replace("　", "＿")

    base_name = re.sub(r"＿+", "＿", base_name)

    base_name = base_name.strip("＿")

    if len(base_name) > max_chars:
        base_name = base_name[:max_chars]
        base_name = base_name.rstrip("＿")

    if not base_name:
        base_name = jaconv.h2z(uuid.uuid4().hex[:10], ascii=True, digit=True)

    full_name = f"{base_name}{extension}"

    try:
        clean_name = pathvalidate.sanitize_filename(full_name)
    except ValueError:
        safe_uuid_base = jaconv.h2z(uuid.uuid4().hex[:10], ascii=True, digit=True)
        clean_name = f"{safe_uuid_base}{extension}"

    return clean_name


def normalize_int(val_str: str) -> int:
    if not val_str:
        return 0
    # 特殊なマイナス記号を標準の半角マイナスに置換
    cleaned = val_str.replace("▲", "-").replace("△", "-")
    # 半角数字とハイフン以外をすべて削除
    cleaned = re.sub(r"[^0-9-]", "", cleaned)

    if not cleaned or cleaned == "-":
        return 0
    try:
        return int(cleaned)
    except ValueError:
        return 0


def normalize_float(val_str: str) -> float:
    """文字列からパーセント記号などを取り除き、標準的な浮動小数点型(float)に変換・標準化する。"""
    if not val_str:
        return 0.0
    cleaned = val_str.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0
