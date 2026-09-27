#!/usr/bin/env bash
# Builds a tiny local Git repository with exactly the three commits
# described in the project spec's "Final Acceptance Test":
#   1. Initial implementation
#   2. Introduce intentional bug
#   3. Fix bug + unrelated change (two independent changes in one commit)
#
# Usage:
#   ./scripts/create_demo_repo.sh [target_dir]
#
# The resulting repo is a normal local Git repo. To analyze it with this
# project, either:
#   a) push it to a real GitHub repo and point the dashboard at that, or
#   b) use it directly with services/verification_runner.py-style local
#      clone URLs (file:///path/to/demo_repo) for offline testing --
#      see backend/tests/test_pipeline_e2e.py for exactly this pattern.
set -euo pipefail

TARGET_DIR="${1:-./data/demo_repo}"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

git init --quiet
git config user.email "demo@example.com"
git config user.name "Demo Author"

# --- Commit 1: initial implementation ---
cat > calculator.py << 'EOF'
"""A tiny calculator module used as the acceptance-test fixture."""


def add(a, b):
    return a + b


def divide(a, b):
    return a / b


class Calculator:
    def __init__(self):
        self.history = []

    def compute(self, op, a, b):
        if op == "add":
            result = add(a, b)
        elif op == "divide":
            result = divide(a, b)
        else:
            raise ValueError(f"Unknown op: {op}")
        self.history.append((op, a, b, result))
        return result
EOF

cat > test_calculator.py << 'EOF'
import unittest
from calculator import add, divide, Calculator


class TestCalculator(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5)

    def test_compute_add(self):
        calc = Calculator()
        self.assertEqual(calc.compute("add", 1, 1), 2)


if __name__ == "__main__":
    unittest.main()
EOF

cat > setup.py << 'EOF'
from setuptools import setup
setup(name="calculator-demo", version="0.1.0", py_modules=["calculator"])
EOF

git add .
git commit --quiet -m "Initial implementation"
echo "Commit 1 (initial implementation): $(git rev-parse HEAD)"

# --- Commit 2: introduce an intentional bug ---
cat > calculator.py << 'EOF'
"""A tiny calculator module used as the acceptance-test fixture."""


def add(a, b):
    return a + b


def divide(a, b):
    # BUG: no guard against division by zero, and the operands are
    # silently swapped, which is the intentional bug for this fixture.
    return b / a


class Calculator:
    def __init__(self):
        self.history = []

    def compute(self, op, a, b):
        if op == "add":
            result = add(a, b)
        elif op == "divide":
            result = divide(a, b)
        else:
            raise ValueError(f"Unknown op: {op}")
        self.history.append((op, a, b, result))
        return result
EOF

git add .
git commit --quiet -m "Optimize divide()"
echo "Commit 2 (intentional bug in divide()): $(git rev-parse HEAD)"

# --- Commit 3: fix the bug + an unrelated logging change ---
cat > calculator.py << 'EOF'
"""A tiny calculator module used as the acceptance-test fixture."""


def add(a, b):
    return a + b


def divide(a, b):
    if a == 0:
        raise ZeroDivisionError("cannot divide by zero")
    return b / a


class Calculator:
    def __init__(self):
        self.history = []

    def compute(self, op, a, b):
        if op == "add":
            result = add(a, b)
        elif op == "divide":
            result = divide(a, b)
        else:
            raise ValueError(f"Unknown op: {op}")
        self.history.append((op, a, b, result))
        return result
EOF

cat > logger_util.py << 'EOF'
"""Unrelated change bundled into the same commit as the bugfix above --
used to exercise the 'multiple independent changes in one commit'
detection path."""
import sys


def write_log(message):
    print(f"[LOG] {message}", file=sys.stderr)
EOF

git add .
git commit --quiet -m "Fix divide() zero-division bug; add basic logging utility"
echo "Commit 3 (fix + unrelated change): $(git rev-parse HEAD)"

echo ""
echo "Demo repo created at: $(pwd)"
echo "3 commits created. Run the acceptance test against it with:"
echo "  cd backend && python -m pytest tests/test_pipeline_e2e.py -v"
