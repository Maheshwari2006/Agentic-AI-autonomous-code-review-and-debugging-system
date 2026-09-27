"""Small utility module with an intentional off-by-one bug in
`chunk_list`, used by test_python_ast/test_dependency style tests."""


def chunk_list(items, size):
    """BUG: should stop at len(items), but the range end is one short,
    silently dropping the last chunk when len(items) is an exact
    multiple of `size`."""
    chunks = []
    for i in range(0, len(items) - 1, size):
        chunks.append(items[i:i + size])
    return chunks
