from __future__ import annotations

import ast
import pathlib
import re
import sys
import unicodedata
from collections.abc import Sequence


class DocstringOffsetFinder(ast.NodeVisitor):
    """ASTを走査してdocstringの正確なテキスト座標(開始/終了位置)を抽出するクラス。"""

    def __init__(self) -> None:
        self.offsets: list[tuple[int, int, int, int]] = []

    def visit_Module(self, node: ast.Module) -> None:
        if ast.get_docstring(node, clean=False) is not None:
            self._extract_offset(node.body)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if ast.get_docstring(node, clean=False) is not None:
            self._extract_offset(node.body)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if ast.get_docstring(node, clean=False) is not None:
            self._extract_offset(node.body)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if ast.get_docstring(node, clean=False) is not None:
            self._extract_offset(node.body)
        self.generic_visit(node)

    def _extract_offset(self, body: Sequence[ast.stmt]) -> None:
        if not body:
            return

        first_stmt = body[0]
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(first_stmt.value, ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            node = first_stmt.value
            if (
                node.lineno is not None
                and node.col_offset is not None
                and node.end_lineno is not None
                and node.end_col_offset is not None
            ):
                self.offsets.append(
                    (
                        node.lineno,
                        node.col_offset,
                        node.end_lineno,
                        node.end_col_offset,
                    )
                )


def calculate_flat_index(content: str, lineno: int, col_offset: int, /) -> int:
    """行番号と列オフセットから、文字列全体のフラットなインデックスを計算する。"""
    lines = content.splitlines(keepends=True)
    return sum(len(line) for line in lines[: lineno - 1]) + col_offset


def normalize_docstring_raw(raw_text: str, /) -> str:
    """docstringのクォーテーション表現を維持したまま、内部文字列のみをNFKC正規化する。"""
    match = re.match(r"^(r|u|f)?(\"\"\"|\'\'\'|\"|\')", raw_text, re.IGNORECASE)
    if not match:
        return raw_text

    prefix = match.group(1) or ""
    quotes = match.group(2)

    start_gap = len(prefix) + len(quotes)
    end_gap = len(quotes)

    inner_content = raw_text[start_gap:-end_gap]
    normalized_inner = unicodedata.normalize("NFKC", inner_content)

    return f"{prefix}{quotes}{normalized_inner}{quotes}"


def process_file(file_path: pathlib.Path, /) -> None:
    """単一のPythonファイルを読み込み、docstring内のUnicode文字を正規化して上書き保存する。"""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except (UnicodeDecodeError, SyntaxError) as err:
        sys.stderr.write(f"Skipped {file_path} due to error: {err}\n")
        return

    finder = DocstringOffsetFinder()
    finder.visit(tree)

    if not finder.offsets:
        return

    flat_ranges: list[tuple[int, int]] = []
    for start_line, start_col, end_line, end_col in finder.offsets:
        start_idx = calculate_flat_index(content, start_line, start_col)
        end_idx = calculate_flat_index(content, end_line, end_col)
        flat_ranges.append((start_idx, end_idx))

    flat_ranges.sort(key=lambda x: x[0], reverse=True)

    modified_content = content
    is_modified = False

    for start_idx, end_idx in flat_ranges:
        raw_docstring = modified_content[start_idx:end_idx]
        normalized_docstring = normalize_docstring_raw(raw_docstring)

        if raw_docstring != normalized_docstring:
            modified_content = modified_content[:start_idx] + normalized_docstring + modified_content[end_idx:]
            is_modified = True

    if is_modified:
        file_path.write_text(modified_content, encoding="utf-8")
        sys.stdout.write(f"Normalized: {file_path}\n")


def main(*, target_path: str | None = None) -> None:
    """メインエントリーポイント。ファイルまたはディレクトリのパスを受け取り処理を分岐させる。"""
    if target_path is None:
        if len(sys.argv) < 2:
            sys.stderr.write("Error: Target file or directory path is required.\n")
            sys.exit(1)
        path_str = sys.argv[1]
    else:
        path_str = target_path

    target = pathlib.Path(path_str)

    if target.is_file():
        if target.suffix == ".py":
            process_file(target)
        else:
            sys.stderr.write(f"Error: {target} is not a Python (.py) file.\n")
            sys.exit(1)

    elif target.is_dir():
        for file_path in target.rglob("*.py"):
            process_file(file_path)

    else:
        sys.stderr.write(f"Error: {target} is not a valid file or directory.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
