"""
AST-based symbol extraction for Python source files.

This is the heart of the "no arbitrary 300-line limit" requirement: instead
of slicing a file or diff by character/line count, we parse it into a real
syntax tree and extract each function/method/class as its own `Symbol`,
with exact line boundaries and full source text -- regardless of whether
the file is 50 lines or 50,000 lines. A 10,000-line file with only two
changed functions yields exactly two (plus dependencies) relevant symbols
for downstream analysis, not a truncated blob of the file.

Also builds a best-effort intra-file call graph: for every symbol, which
other *names* (functions/methods) does its body call. This feeds
analysis/dependency.py so the dependency agent can walk from a changed
function to its callers/callees without re-reading the whole file.
"""
from __future__ import annotations

import ast
from typing import Optional

from .base import Symbol


class ParseError(Exception):
    def __init__(self, message: str, lineno: Optional[int] = None):
        super().__init__(message)
        self.lineno = lineno


def _get_source_segment(source_lines: list, node: ast.AST) -> str:
    """Extract exact source text for a node using 1-based line numbers,
    including any leading decorator lines. No truncation."""
    start = node.lineno
    end = getattr(node, "end_lineno", None) or start
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list)
    return "\n".join(source_lines[start - 1:end])


def _collect_calls(node: ast.AST) -> list:
    calls = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                calls.append(func.id)
            elif isinstance(func, ast.Attribute):
                calls.append(func.attr)
    # de-duplicate, keep order
    seen = set()
    ordered = []
    for c in calls:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _args_of(node) -> list:
    try:
        a = node.args
        names = [arg.arg for arg in a.posonlyargs] if hasattr(a, "posonlyargs") else []
        names += [arg.arg for arg in a.args]
        if a.vararg:
            names.append("*" + a.vararg.arg)
        names += [arg.arg for arg in a.kwonlyargs]
        if a.kwarg:
            names.append("**" + a.kwarg.arg)
        return names
    except Exception:
        return []


def extract_symbols(source: str) -> list:
    """Parse `source` (a full Python file's text) and return a flat list
    of Symbol objects for every top-level and nested function/class."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ParseError(f"Python syntax error: {e.msg} (line {e.lineno})", lineno=e.lineno)

    source_lines = source.splitlines()
    symbols: list = []

    def visit(node: ast.AST, parent_class: Optional[str], qualifier: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sym_type = "method" if parent_class else "function"
                qname = f"{qualifier}.{child.name}" if qualifier else child.name
                symbols.append(
                    Symbol(
                        name=child.name,
                        qualified_name=qname,
                        symbol_type=sym_type,
                        start_line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno),
                        source=_get_source_segment(source_lines, child),
                        parent=parent_class,
                        calls=_collect_calls(child),
                        decorators=[
                            ast.unparse(d) if hasattr(ast, "unparse") else ""
                            for d in child.decorator_list
                        ],
                        docstring=ast.get_docstring(child),
                        args=_args_of(child),
                    )
                )
                # nested functions (closures) -- still tracked, qualified further
                visit(child, parent_class, qname)
            elif isinstance(child, ast.ClassDef):
                qname = f"{qualifier}.{child.name}" if qualifier else child.name
                symbols.append(
                    Symbol(
                        name=child.name,
                        qualified_name=qname,
                        symbol_type="class",
                        start_line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno),
                        source=_get_source_segment(source_lines, child),
                        parent=parent_class,
                        calls=[],
                        decorators=[
                            ast.unparse(d) if hasattr(ast, "unparse") else ""
                            for d in child.decorator_list
                        ],
                        docstring=ast.get_docstring(child),
                        args=[],
                    )
                )
                visit(child, child.name, qname)
            else:
                # module-level statements / control flow: keep walking so we
                # don't miss functions defined inside `if __name__ == ...`
                # blocks etc.
                visit(child, parent_class, qualifier)

    visit(tree, None, "")
    return symbols


def extract_imports(source: str) -> list:
    """Return a flat list of imported names/modules, used by the
    dependency agent for lightweight cross-file resolution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if module else alias.name)
    return imports
