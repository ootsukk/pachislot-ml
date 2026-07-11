from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

# Global configuration constants
DEFAULT_SOURCE: Final[str] = "E:/MyDocument/work/vscode/python/pachislot-ml/common/src/tools"
DEFAULT_DESTINATION: Final[str] = "H:/マイドライブ/python"


def main() -> None:
    """Command-line interface entry point for the gitignore-aware copy tool."""
    parser = argparse.ArgumentParser(
        description="Recursively copy a directory including itself while respecting .gitignore rules."
    )
    parser.add_argument(
        "-src", "--source", type=str, default=DEFAULT_SOURCE, help="Source directory path (default: %(default)s)"
    )
    parser.add_argument(
        "-dest",
        "--destination",
        type=str,
        default=DEFAULT_DESTINATION,
        help="Destination directory path (default: %(default)s)",
    )
    parser.add_argument("-f", "--force", action="store_true", help="Force copy without user confirmation")
    args = parser.parse_args()

    src = Path(args.source)
    dst = Path(args.destination)

    print(f"Source:      {src.resolve()}")
    print(f"Destination: {dst.resolve()}")

    if not args.force:
        try:
            response = input("Proceed with copy operation? (y/N): ").strip().lower()
        except KeyboardInterrupt, EOFError:
            print("\nOperation cancelled by user.", file=sys.stderr)
            sys.exit(1)

        if response not in ("y", "yes"):
            print("Operation aborted.")
            sys.exit(0)

    try:
        copy_with_gitignore(src, dst)
        print("Copy operation completed successfully.")
    except ValueError as e:
        print(f"Validation Error: {e}", file=sys.stderr)
        sys.exit(1)
    except PermissionError as e:
        print(f"Permission Error: {e}", file=sys.stderr)
        sys.exit(1)


def parse_gitignore(gitignore_path: Path, /) -> Sequence[str]:
    """Parse a .gitignore file and extract valid patterns."""
    if not gitignore_path.is_file():
        return []

    patterns: list[str] = []
    with gitignore_path.open(mode="r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            patterns.append(stripped)
    return patterns


def should_exclude(path: Path, base_dir: Path, patterns: Sequence[str], /) -> bool:
    """Determine if a given path matches any gitignore patterns."""
    try:
        relative_path = path.relative_to(base_dir)
    except ValueError:
        return False

    path_str = relative_path.as_posix()

    for pattern in patterns:
        if pattern.endswith("/"):
            clean_pattern = pattern.rstrip("/")
            if any(fnmatch.fnmatch(part, clean_pattern) for part in relative_path.parts):
                return True
        if fnmatch.fnmatch(path_str, pattern) or any(fnmatch.fnmatch(part, pattern) for part in relative_path.parts):
            return True

    return False


def copy_with_gitignore(src_dir: Path, dst_dir: Path, /) -> None:
    """Validate directories and recursively copy the source directory itself into the destination."""
    if not src_dir.is_dir():
        raise ValueError("Source path must be a directory")

    src_resolved = src_dir.resolve()
    dst_resolved = dst_dir.resolve()

    if src_resolved == dst_resolved:
        raise ValueError("Source and destination directories cannot be identical")

    if dst_resolved.is_relative_to(src_resolved):
        raise ValueError("Destination directory cannot be a subdirectory of the source directory")

    # Define the target base directory inside the destination [Effective Python Item 24]
    target_base_dir = dst_resolved / src_resolved.name
    target_base_dir.mkdir(parents=True, exist_ok=True)

    global_patterns = parse_gitignore(src_resolved / ".gitignore")

    for path in src_resolved.rglob("*"):
        if path == dst_resolved or path.is_relative_to(dst_resolved):
            continue

        current_dir = path.parent if path.is_file() else path
        local_gitignore = current_dir / ".gitignore"

        active_patterns = list(global_patterns)
        if local_gitignore.is_file():
            active_patterns.extend(parse_gitignore(local_gitignore))

        if should_exclude(path, src_resolved, active_patterns):
            continue

        # Calculate the destination path relative to the new target base directory
        relative_path = path.relative_to(src_resolved)
        target_path = target_base_dir / relative_path

        if path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_path)


if __name__ == "__main__":
    main()
