from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Global configuration constants
DEFAULT_ROOT_DIR: Final[str] = "E:/MyDocument/work/vscode/python/pachislot-ml"
DEFAULT_SOURCE: Final[str] = "E:/MyDocument/work/vscode/python/pachislot-ml/common/src/container"
DEFAULT_DESTINATION: Final[str] = "H:/マイドライブ/python"


@dataclass(frozen=True, slots=True)
class GitignorePattern:
    """Immutable Value Object representing a single gitignore pattern rule."""

    raw_pattern: str
    cleaned_pattern: str
    is_dir_only: bool

    def matches(self, relative_path: Path, /) -> bool:
        """Evaluate if the given relative path matches this pattern constraint directly via an inlined condition."""
        path_str = relative_path.as_posix()

        # Inlined conditional evaluation optimizing short-circuit mechanics
        return (
            any(fnmatch.fnmatch(part, self.cleaned_pattern) for part in relative_path.parts)
            or fnmatch.fnmatch(path_str, self.raw_pattern)
            or fnmatch.fnmatch(path_str, self.cleaned_pattern)
            or (self.is_dir_only and path_str.startswith(self.cleaned_pattern + "/"))
        )


# Encapsulate system-wide implicit exclusion rules as standard domain data
SYSTEM_DEFAULT_PATTERNS: Final[Sequence[GitignorePattern]] = (
    GitignorePattern(raw_pattern="__pycache__/", cleaned_pattern="__pycache__", is_dir_only=True),
    GitignorePattern(raw_pattern=".git/", cleaned_pattern=".git", is_dir_only=True),
)


@dataclass(frozen=True, slots=True)
class GitignoreMatcher:
    """First-Class Collection that encapsulates a group of GitignorePatterns."""

    patterns: Sequence[GitignorePattern]

    def should_exclude(self, path: Path, base_dir: Path, /) -> bool:
        """Determine if a concrete path matches any internal domain patterns via data-driven execution."""
        try:
            relative_path = path.relative_to(base_dir)
        except ValueError:
            return False

        return any(pattern.matches(relative_path) for pattern in self.patterns)

    def merge(self, other: GitignoreMatcher, /) -> GitignoreMatcher:
        """Merge two matchers into a new combined immutable GitignoreMatcher."""
        return GitignoreMatcher((*self.patterns, *other.patterns))


def main() -> None:
    """Command-line interface entry point for the gitignore-aware copy tool."""
    parser = argparse.ArgumentParser(
        description="Recursively copy a directory including itself while respecting .gitignore rules."
    )
    parser.add_argument("-root", "--root-dir", type=str, default=DEFAULT_ROOT_DIR)
    parser.add_argument("-src", "--source", type=str, default=DEFAULT_SOURCE)
    parser.add_argument("-dest", "--destination", type=str, default=DEFAULT_DESTINATION)
    parser.add_argument("-f", "--force", action="store_true")
    args = parser.parse_args()

    root = Path(args.root_dir)
    src = Path(args.source)
    dst = Path(args.destination)

    print(f"Project Root: {root.resolve()}")
    print(f"Source:       {src.resolve()}")
    print(f"Destination:  {dst.resolve()}")

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
        copy_with_gitignore(src, dst, root_dir=root)
        print("Copy operation completed successfully.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def parse_gitignore(gitignore_path: Path, /) -> GitignoreMatcher:
    """Parse a single .gitignore file and return a structured GitignoreMatcher instance."""
    patterns: list[GitignorePattern] = []

    if not gitignore_path.is_file():
        return GitignoreMatcher(patterns)

    with gitignore_path.open(mode="r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            is_dir_only = stripped.endswith("/")
            cleaned = stripped.strip("/")
            patterns.append(
                GitignorePattern(
                    raw_pattern=stripped,
                    cleaned_pattern=cleaned,
                    is_dir_only=is_dir_only,
                )
            )
    return GitignoreMatcher(patterns)


def create_ignore_callback(
    base_dir: Path, global_matcher: GitignoreMatcher, /
) -> Callable[[str, list[str]], Sequence[str]]:
    """Create a high-order function compliant with shutil.copytree ignore protocol."""

    def _ignore(current_dir_str: str, names: list[str]) -> list[str]:
        current_dir = Path(current_dir_str)
        ignored_names: list[str] = []

        local_gitignore = current_dir / ".gitignore"
        active_matcher = global_matcher
        if local_gitignore.is_file():
            active_matcher = global_matcher.merge(parse_gitignore(local_gitignore))

        for name in names:
            path = current_dir / name
            if active_matcher.should_exclude(path, base_dir):
                ignored_names.append(name)

        return ignored_names

    return _ignore


def copy_with_gitignore(src_dir: Path, dst_dir: Path, /, *, root_dir: Path = Path(DEFAULT_ROOT_DIR)) -> None:
    """Validate directories and recursively copy the source directory itself into the destination."""
    if not src_dir.is_dir():
        raise ValueError("Source path must be a directory")

    src_resolved = src_dir.resolve()
    dst_resolved = dst_dir.resolve()
    root_resolved = root_dir.resolve()

    if src_resolved == dst_resolved or dst_resolved.is_relative_to(src_resolved):
        raise ValueError("Invalid directory configuration")

    target_base_dir = dst_resolved / src_resolved.name

    system_matcher = GitignoreMatcher(SYSTEM_DEFAULT_PATTERNS)
    root_matcher = parse_gitignore(root_resolved / ".gitignore")
    src_matcher = parse_gitignore(src_resolved / ".gitignore")

    global_matcher = system_matcher.merge(root_matcher).merge(src_matcher)
    ignore_callback = create_ignore_callback(src_resolved, global_matcher)

    shutil.copytree(src_resolved, target_base_dir, ignore=ignore_callback, dirs_exist_ok=True, symlinks=False)


if __name__ == "__main__":
    main()
