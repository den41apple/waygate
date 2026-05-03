#!/usr/bin/env python3
"""Bump'ает X.Y.Z в pyproject.toml: patch++; на 99 кэрри в minor; на 99.99 — в major.

Если задан --tag-prefix (например `agent-v`), берёт максимум между версией в
pyproject и максимальной существующей git-версией с этим префиксом — и bump'ит
от него. Это спасает когда теги ушли вперёд от файла (типичная ситуация после
ручных тегов).

Запуск:
    python scripts/bump_version.py backend/agent/pyproject.toml
    python scripts/bump_version.py backend/agent/pyproject.toml --tag-prefix agent-v

Печатает новую версию в stdout. Exit-код != 0 при ошибке.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', re.MULTILINE)
TAG_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

Version = tuple[int, int, int]


def bump(version: Version) -> Version:
    major, minor, patch = version
    if patch < 99:
        return major, minor, patch + 1
    if minor < 99:
        return major, minor + 1, 0
    return major + 1, 0, 0


def _max_tag_version(*, tag_prefix: str) -> Version | None:
    """Возвращает max X.Y.Z среди тегов вида `<prefix>X.Y.Z`, или None."""
    try:
        output = subprocess.check_output(
            ["git", "tag", "-l", f"{tag_prefix}*"], text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    versions: list[Version] = []
    for raw_line in output.splitlines():
        candidate = raw_line.strip().removeprefix(tag_prefix)
        match = TAG_VERSION_RE.match(candidate)
        if match is not None:
            versions.append(tuple(int(value) for value in match.groups()))  # type: ignore[arg-type]
    return max(versions) if versions else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("pyproject", help="путь к pyproject.toml")
    parser.add_argument(
        "--tag-prefix",
        default=None,
        help="префикс git-тега (напр. 'agent-v') — учесть существующие теги",
    )
    args = parser.parse_args()

    path = Path(args.pyproject)
    if not path.is_file():
        sys.stderr.write(f"не найден файл: {path}\n")
        return 1

    text = path.read_text()
    match = VERSION_RE.search(text)
    if match is None:
        sys.stderr.write(f'не нашёл version = "X.Y.Z" в {path}\n')
        return 1

    file_version: Version = tuple(int(value) for value in match.groups())  # type: ignore[assignment]
    tag_version = _max_tag_version(tag_prefix=args.tag_prefix) if args.tag_prefix else None

    base_version = max(filter(None, [file_version, tag_version])) if tag_version else file_version
    new_major, new_minor, new_patch = bump(base_version)
    new_version_str = f"{new_major}.{new_minor}.{new_patch}"

    path.write_text(VERSION_RE.sub(f'version = "{new_version_str}"', text, count=1))
    print(new_version_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
