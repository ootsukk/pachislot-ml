from __future__ import annotations

import pathlib
import sys
import unicodedata


def normalize_file_content(content: str, /) -> str:
    """文字ストリームを走査し、Ruffの規律に抵触する曖昧なUnicode文字を安全に置換する。

    スライス位置の計算を行わないため、コード構文を破壊するリスクが論理的にゼロとなります。
    """
    buffer: list[str] = []
    for char in content:
        if char == "\u3000":
            buffer.append(" ")
            continue

        normalized = unicodedata.normalize("NFKC", char)
        # NFKC正規化後にASCIIに変換され、かつ元が非ASCIIである文字のみを置換
        if normalized.isascii() and not char.isascii():
            buffer.append(normalized)
        else:
            buffer.append(char)

    return "".join(buffer)


def process_file(file_path: pathlib.Path, /) -> None:
    """単一ファイルを読み込み、正規化を適用して差分がある場合のみ上書き保存する。"""
    try:
        raw_content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        sys.stderr.write(f"Skipped {file_path} due to decode error: {err}\n")
        return

    normalized_content = normalize_file_content(raw_content)

    if raw_content != normalized_content:
        file_path.write_text(normalized_content, encoding="utf-8")
        sys.stdout.write(f"Normalized: {file_path}\n")


def main(*, target_path: str | None = None) -> None:
    """コマンドライン引数または指定パスからファイル・ディレクトリを識別し、再帰処理を実行する。"""
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
