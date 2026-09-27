"""
Agent tools.

This is what makes the review step *agentic* rather than a single
`diff -> LLM -> response` call: Claude is given a set of callable tools
and decides for itself which ones it needs (read a function, find its
callers, look at a test file, request the file listing, etc.) before
producing its final review. See agents/review_agent.py for the loop that
drives this.

Every tool operates against the AnalysisContext already assembled by the
earlier pipeline stages (repository/change/dependency agents) -- so tool
calls are fast local lookups, not new network calls per tool use.
"""
from __future__ import annotations

import json
from typing import Optional

TOOL_DEFINITIONS = [
    {
        "name": "list_changed_files",
        "description": "List every file changed in this commit, with status (added/modified/removed) and additions/deletions counts.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read the full current-version source of a file in this commit (no truncation). Use for files not already provided as changed-symbol context.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_function",
        "description": "Read the full source of a specific function/method by qualified name (e.g. 'ClassName.method' or 'func_name') in a given file, at either the previous or current revision.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "qualified_name": {"type": "string"},
                "revision": {"type": "string", "enum": ["previous", "current"]},
            },
            "required": ["path", "qualified_name", "revision"],
        },
    },
    {
        "name": "find_callers",
        "description": "Find every indexed function/method that calls the given symbol name, anywhere in the analyzed files. Returns each caller's full source.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol_name": {"type": "string"}},
            "required": ["symbol_name"],
        },
    },
    {
        "name": "find_callees",
        "description": "Find every function/method that a given symbol calls, with their full source where available.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "qualified_name": {"type": "string"}},
            "required": ["path", "qualified_name"],
        },
    },
    {
        "name": "search_code",
        "description": "Search indexed file contents for a literal substring or symbol name (case-insensitive). Useful for finding related tests or configuration.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_tests_for_file",
        "description": "Return the content of test files that appear related to a given source file path, based on naming convention (test_<name>.py, <name>_test.py, tests/<name>.py).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


class ToolExecutor:
    """Executes a tool call against an AnalysisContext (see
    services/analysis_service.py for the context's shape) and returns a
    JSON-serializable result."""

    def __init__(self, context):
        self.ctx = context

    def execute(self, name: str, tool_input: dict) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool '{name}'"}
        try:
            return handler(**tool_input)
        except Exception as e:
            return {"error": f"Tool '{name}' failed: {e}"}

    def _tool_list_changed_files(self) -> dict:
        return {
            "files": [
                {
                    "path": f.filename,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                }
                for f in self.ctx.commit.files
            ]
        }

    def _tool_read_file(self, path: str) -> dict:
        for f in self.ctx.commit.files:
            if f.filename == path:
                content = f.current_content if f.current_content is not None else f.previous_content
                if content is None:
                    return {"error": f"No content available for {path}"}
                return {"path": path, "content": content}
        # allow reading files outside the commit diff if indexed (dependency context)
        content = self.ctx.extra_file_contents.get(path)
        if content:
            return {"path": path, "content": content}
        return {"error": f"File not found in this commit's changed files or index: {path}"}

    def _tool_read_function(self, path: str, qualified_name: str, revision: str) -> dict:
        entry = self.ctx.index.get(path, qualified_name)
        if entry and revision == "current":
            return {"path": path, "qualified_name": qualified_name, "source": entry.symbol.source}
        # fall back to searching changed-symbol records for previous revision
        for cs in self.ctx.changed_symbols:
            if cs.file_path == path and cs.qualified_name == qualified_name:
                sym = cs.previous_symbol if revision == "previous" else cs.new_symbol
                if sym:
                    return {"path": path, "qualified_name": qualified_name, "source": sym.source}
        return {"error": f"Symbol {qualified_name} not found in {path} at revision={revision}"}

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

    def _tool_search_code(self, query: str) -> dict:
        results = []
        q = query.lower()
        for path, content in self.ctx.extra_file_contents.items():
            if q in content.lower():
                # return matching line numbers + context, not the whole file
                lines = content.splitlines()
                hits = [
                    {"line": i + 1, "text": line.strip()}
                    for i, line in enumerate(lines)
                    if q in line.lower()
                ][:20]
                results.append({"path": path, "matches": hits})
        return {"results": results}

    def _tool_get_tests_for_file(self, path: str) -> dict:
        import os

        base = os.path.basename(path)
        stem = base.rsplit(".", 1)[0] if "." in base else base
        candidates = [f"test_{stem}.py", f"{stem}_test.py", f"tests/test_{stem}.py"]
        found = []
        for cand_path, content in self.ctx.extra_file_contents.items():
            cand_base = os.path.basename(cand_path)
            if cand_base in candidates or stem in cand_base:
                found.append({"path": cand_path, "content": content})
        return {"tests": found}
