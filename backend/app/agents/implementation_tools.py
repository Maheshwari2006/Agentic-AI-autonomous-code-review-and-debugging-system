"""
Tools for the implementation agent (agents/implementation_agent.py).

Mirrors the design of agents/tools.py (used by the existing commit-review
ReviewAgent): the LLM is given a small set of callable tools and decides
for itself what additional repository context it needs before producing
its final structured answer. The key difference from tools.py is scope --
these tools operate over an *entire repository at a ref* (via
ImplementationContext / FileFetcher, fetching lazily and caching), not
just the files already touched by one commit.
"""
from __future__ import annotations

import os

TOOL_DEFINITIONS_IMPLEMENTATION = [
    {
        "name": "list_directory",
        "description": "List files and subdirectories directly under a path in the repository tree (non-recursive). Use '' or '.' for the repository root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the full current content of a file in the repository at the analyzed ref (no truncation). Fetches on demand if not already loaded.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_function",
        "description": "Read the full source of a specific function/method/class by qualified name (e.g. 'ClassName.method' or 'func_name') in a given file. The file must already have been read via read_file so it's indexed.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "qualified_name": {"type": "string"}},
            "required": ["path", "qualified_name"],
        },
    },
    {
        "name": "find_callers",
        "description": "Find every indexed (already-read) function/method that calls the given symbol name. Returns each caller's full source. Only searches files already read via read_file/list_directory in this session -- read the files you suspect are relevant first.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol_name": {"type": "string"}},
            "required": ["symbol_name"],
        },
    },
    {
        "name": "find_callees",
        "description": "Find what a given (already-read) symbol calls, with full source where the callee has also been read/indexed.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "qualified_name": {"type": "string"}},
            "required": ["path", "qualified_name"],
        },
    },
    {
        "name": "search_filenames",
        "description": "Search the repository's file paths for a substring (case-insensitive). Use this to find likely-relevant files by name before reading them, e.g. 'auth', 'jwt', 'user_model'.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_content",
        "description": "Search the content of files already read in this session for a literal substring (case-insensitive). Returns matching lines with line numbers. Read likely files first with read_file, then search across them.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_tests_for_file",
        "description": "Find and read test files related to a given source file path, by naming convention (test_<name>.py, <name>_test.py, tests/test_<name>.py) and fetch their content.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


class ImplementationToolExecutor:
    """Executes a tool call against an ImplementationContext. Every read
    goes through ctx.fetcher, which caches content per path for the life
    of the request -- so repeated tool calls to the same file are free
    after the first fetch, and every fetched file is also indexed into
    ctx.index so find_callers/find_callees work across everything the
    agent has looked at, not just the initial file-discovery shortlist."""

    def __init__(self, ctx):
        self.ctx = ctx

    def execute(self, name: str, tool_input: dict) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool '{name}'"}
        try:
            return handler(**tool_input)
        except Exception as e:
            return {"error": f"Tool '{name}' failed: {e}"}

    def _index_if_needed(self, path: str, content: str) -> None:
        from ..parsing.base import detect_language, get_parser

        if detect_language(path) and get_parser(detect_language(path)):
            self.ctx.index.add_file(path, content)

    def _tool_list_directory(self, path: str) -> dict:
        norm = "" if path in (".", "/", "") else path.strip("/")
        entries = []
        seen_dirs = set()
        for f in self.ctx.repo_handle.tree.files:
            if norm and not f.path.startswith(norm + "/"):
                continue
            rel = f.path[len(norm) + 1:] if norm else f.path
            if "/" in rel:
                top = rel.split("/", 1)[0]
                if top not in seen_dirs:
                    seen_dirs.add(top)
                    entries.append({"name": top, "type": "directory"})
            else:
                entries.append({"name": rel, "type": "file", "size": f.size})
        return {"path": norm or ".", "entries": entries}

    def _tool_read_file(self, path: str) -> dict:
        content = self.ctx.fetcher.get(path)
        if content is None:
            return {"error": f"Could not fetch {path} (not found, binary, or inaccessible)."}
        self._index_if_needed(path, content)
        self.ctx.log("implementation_agent", f"tool read_file({path})")
        return {"path": path, "content": content}

    def _tool_read_function(self, path: str, qualified_name: str) -> dict:
        entry = self.ctx.index.get(path, qualified_name)
        if not entry:
            return {"error": f"Symbol '{qualified_name}' not found in {path}. Read the file first with read_file."}
        return {"path": path, "qualified_name": qualified_name, "source": entry.symbol.source}

    def _tool_find_callers(self, symbol_name: str) -> dict:
        callers = self.ctx.index.find_callers(symbol_name)
        return {
            "callers": [
                {"file": c.file_path, "qualified_name": c.symbol.qualified_name, "source": c.symbol.source}
                for c in callers
            ]
        }

    def _tool_find_callees(self, path: str, qualified_name: str) -> dict:
        callees = self.ctx.index.find_callees(path, qualified_name)
        return {
            "callees": [
                {"file": c.file_path, "qualified_name": c.symbol.qualified_name, "source": c.symbol.source}
                for c in callees
            ]
        }

    def _tool_search_filenames(self, query: str) -> dict:
        q = query.lower()
        matches = [f.path for f in self.ctx.repo_handle.tree.files if q in f.path.lower()][:50]
        return {"matches": matches}

    def _tool_search_content(self, query: str) -> dict:
        q = query.lower()
        results = []
        for path in self.ctx.fetcher.cached_paths():
            content = self.ctx.fetcher.get(path)
            if content and q in content.lower():
                lines = content.splitlines()
                hits = [
                    {"line": i + 1, "text": line.strip()}
                    for i, line in enumerate(lines)
                    if q in line.lower()
                ][:20]
                results.append({"path": path, "matches": hits})
        return {"results": results}

    def _tool_get_tests_for_file(self, path: str) -> dict:
        base = os.path.basename(path)
        stem = base.rsplit(".", 1)[0] if "." in base else base
        candidates = {f"test_{stem}.py", f"{stem}_test.py"}
        found = []
        for f in self.ctx.repo_handle.tree.files:
            fbase = os.path.basename(f.path)
            if fbase in candidates or (f.is_test and stem in fbase):
                content = self.ctx.fetcher.get(f.path)
                if content:
                    found.append({"path": f.path, "content": content})
        return {"tests": found}
