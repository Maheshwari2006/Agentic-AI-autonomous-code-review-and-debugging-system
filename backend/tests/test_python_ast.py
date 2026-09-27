from app.parsing.python_ast import extract_symbols, extract_imports, ParseError


def test_extracts_top_level_function():
    src = "def foo(a, b):\n    return a + b\n"
    symbols = extract_symbols(src)
    assert len(symbols) == 1
    assert symbols[0].name == "foo"
    assert symbols[0].symbol_type == "function"
    assert symbols[0].args == ["a", "b"]


def test_extracts_class_and_methods():
    src = (
        "class Foo:\n"
        "    def bar(self):\n"
        "        return helper()\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
    )
    symbols = {s.qualified_name: s for s in extract_symbols(src)}
    assert "Foo" in symbols
    assert symbols["Foo"].symbol_type == "class"
    assert "Foo.bar" in symbols
    assert symbols["Foo.bar"].symbol_type == "method"
    assert symbols["Foo.bar"].parent == "Foo"
    assert "helper" in symbols["Foo.bar"].calls


def test_nested_function_is_tracked():
    src = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    symbols = {s.qualified_name: s for s in extract_symbols(src)}
    assert "outer" in symbols
    assert "outer.inner" in symbols


def test_no_arbitrary_line_limit_large_file():
    """Regression guard for the 'no fixed line-count truncation' requirement:
    a file far larger than any of the old hard-coded limits (300/500/1000
    lines, 12000 chars) must still be fully parsed, with every function
    detected and its full source preserved untruncated."""
    lines = []
    for i in range(600):  # >> any of the old arbitrary limits
        lines.append(f"def func_{i}():\n    return {i}\n")
    src = "\n".join(lines)
    assert len(src) > 12000  # bigger than the removed MAX_DIFF_CHARS constant

    symbols = extract_symbols(src)
    assert len(symbols) == 600
    # every symbol's source must be intact, not cut off
    for s in symbols:
        assert s.source.strip().startswith("def func_")
        assert "return" in s.source


def test_syntax_error_raises_parse_error_not_silent_failure():
    bad_src = "def foo(:\n    pass\n"
    try:
        extract_symbols(bad_src)
        assert False, "expected ParseError"
    except ParseError as e:
        assert e.lineno is not None


def test_extract_imports():
    src = "import os\nfrom typing import Optional, List\n"
    imports = extract_imports(src)
    assert "os" in imports
    assert "typing.Optional" in imports
    assert "typing.List" in imports
