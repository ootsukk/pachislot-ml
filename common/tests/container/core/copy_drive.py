import os
import shutil
from pathlib import Path
import pathspec

SRC_DIR = Path(".").resolve()
# 💡 環境変数から取得、未設定ならデフォルトパスを使用
DST_DIR = Path(os.getenv("GEMINI_SYNC_DIR", "/Users/username/GoogleDrive/my_project_sync")).resolve()

ALWAYS_IGNORE = {".git", ".tmp.driveupload", ".venv"}


def load_gitignore_spec(gitignore_path: Path) -> pathspec.PathSpec:
    if not gitignore_path.exists():
        return pathspec.PathSpec.from_lines("gitwildmatch", [])

    with open(gitignore_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def remove_empty_folders(path: Path):
    """コピー後に発生した空のディレクトリを再帰的に削除する"""
    for root, dirs, files in os.walk(path, topdown=False):
        for dir_name in dirs:
            dir_path = Path(root) / dir_name
            if not os.listdir(dir_path):
                dir_path.rmdir()


def main():
    if SRC_DIR in DST_DIR.parents or SRC_DIR == DST_DIR:
        print("Error: Destination directory cannot be inside the source directory.")
        return

    gitignore_path = SRC_DIR / ".gitignore"
    spec = load_gitignore_spec(gitignore_path)

    def gitignore_copy_filter(directory, contents):
        ignored = []
        dir_path = Path(directory)

        for item in contents:
            full_path = dir_path / item
            relative_path = full_path.relative_to(SRC_DIR)

            if item in ALWAYS_IGNORE:
                ignored.append(item)
                continue

            match_path = (
                f"{relative_path}/" if full_path.is_dir() else str(relative_path)
            )

            if spec.match_file(match_path):
                ignored.append(item)

        return ignored

    try:
        shutil.copytree(
            SRC_DIR, DST_DIR, ignore=gitignore_copy_filter, dirs_exist_ok=True
        )
        remove_empty_folders(DST_DIR)
        print("Success: Synced with .gitignore rules.")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
