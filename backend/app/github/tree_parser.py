"""
tree_parser -- turns GitHubClient.get_tree()'s flat entry list into
structures the file-discovery layer can score against: a set of source
files (binaries excluded), grouped by directory, with lightweight
metadata (extension, depth, looks-like-test, looks-like-config).

No content is fetched here -- this module only looks at *paths*. Content
fetching is file_fetcher.py's job, called lazily only for files the
discovery layer actually decided are relevant.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Extensions we consider "source" for the implementation feature. Anything
# else (images, fonts, compiled artifacts, lockfiles' binary siblings,
# etc.) is excluded from relevance scoring so we never try to AST-parse or
# ship binary content to Claude. This list intentionally covers common
# source/config/doc formats, not just Python -- file discovery still needs
# to see e.g. package.json or a Dockerfile even in a Python-primary repo.
SOURCE_EXTENSIONS = {
    "py", "pyi", "js", "jsx", "ts", "tsx", "java", "go", "rb", "php",
    "c", "h", "cpp", "cc", "hpp", "cs", "rs", "kt", "swift", "scala",
    "json", "yaml", "yml", "toml", "ini", "cfg", "env", "sql", "html",
    "css", "scss", "md", "rst", "txt", "sh", "Dockerfile",
}

BINARY_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "ico", "svg", "woff", "woff2",
    "ttf", "eot", "pdf", "zip", "gz", "tar", "whl", "so", "dylib", "dll",
    "pyc", "class", "jar", "exe", "bin", "lock",
}

IGNORED_DIR_PREFIXES = (
    ".git/", "node_modules/", "__pycache__/", ".venv/", "venv/",
    "dist/", "build/", ".tox/", ".mypy_cache/", ".pytest_cache/",
    "vendor/", "site-packages/",
)


@dataclass
class RepoFile:
    path: str
    extension: str
    size: int
    depth: int
    is_test: bool
    is_config: bool


@dataclass
class ParsedTree:
    files: list = field(default_factory=list)  # list[RepoFile]
    directories: list = field(default_factory=list)  # list[str]
    truncated_note: str = ""


def _extension_of(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if base in ("Dockerfile", "Makefile"):
        return base
    if "." in base:
        return base.rsplit(".", 1)[-1].lower()
    return ""


def _looks_like_test(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    lower = path.lower()
    return (
        base.startswith("test_")
        or base.endswith("_test.py")
        or base.endswith(".test.js")
        or base.endswith(".spec.ts")
        or "/tests/" in lower
        or lower.startswith("tests/")
        or "/test/" in lower
    )


def _looks_like_config(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return base in (
        "settings.py", "config.py", "configuration.py", ".env", ".env.example",
        "docker-compose.yml", "dockerfile", "pyproject.toml", "setup.cfg",
        "setup.py", "requirements.txt", "package.json", "tsconfig.json",
        "alembic.ini",
    ) or base.endswith((".ini", ".cfg", ".yaml", ".yml", ".toml"))


def parse_tree(raw_tree: list) -> ParsedTree:
    """`raw_tree` is GitHubClient.get_tree()'s output. Filters out ignored
    directories and binary files, and annotates every remaining source
    file with the signals file_discovery.py scores against."""
    files: list = []
    directories: list = []

    for entry in raw_tree:
        path = entry["path"]
        if any(path.startswith(p) or f"/{p}" in path for p in IGNORED_DIR_PREFIXES):
            continue

        if entry["type"] == "tree":
            directories.append(path)
            continue

        ext = _extension_of(path)
        if ext in BINARY_EXTENSIONS:
            continue
        if ext not in SOURCE_EXTENSIONS and ext != "":
            # Unknown extension: keep it (better to slightly over-include
            # than to silently drop a relevant file with an unusual
            # extension), but it will score low on relevance signals.
            pass

        files.append(
            RepoFile(
                path=path,
                extension=ext,
                size=entry.get("size") or 0,
                depth=path.count("/"),
                is_test=_looks_like_test(path),
                is_config=_looks_like_config(path),
            )
        )

    return ParsedTree(files=files, directories=directories)
