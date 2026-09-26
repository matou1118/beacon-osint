"""No duplicate top-level definitions anywhere in the package.

This bug class appeared twice in one session and both times it was invisible to
the unit tests:

* storage/models.py declared PolicyTopic, TopicDocLink, FieldExtraction,
  TopicSubscription and UserNotice twice. SQLAlchemy raised "Table
  'policy_topics' is already defined" on import, so every entry point that
  imports storage.db failed -- including the scripts, the tests never noticed
  because they imported the extractors directly.
* collectors/html_list.py defined _playwright twice: the old in-process
  implementation was shadowed by a stub that raises, so ~17 lines of dead code
  kept a Playwright sync-API import alive.

Both were leftovers from an edit. Python silently keeps the last definition, so
only a structural check catches them.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Generated or vendored trees: not ours to police.
SKIP_PARTS = {".venv", "venv", "__pycache__", "site-packages", "node_modules"}


def _python_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*.py"):
        parts = set(path.relative_to(ROOT).parts)
        if parts & SKIP_PARTS:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        files.append(path)
    return files


class NoDuplicateDefinitionTests(unittest.TestCase):
    def test_no_duplicate_function_or_class_definitions(self) -> None:
        duplicates: list[str] = []
        for path in _python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for scope in [tree, *ast.walk(tree)]:
                if not isinstance(scope, (ast.Module, ast.ClassDef)):
                    continue
                seen: dict[str, list[int]] = {}
                for node in scope.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        seen.setdefault(node.name, []).append(node.lineno)
                for name, lines in seen.items():
                    if len(lines) > 1:
                        rel = path.relative_to(ROOT)
                        duplicates.append(f"{rel}: {name} defined at lines {lines}")
        self.assertEqual(duplicates, [], "duplicate definitions:\n" + "\n".join(duplicates))

    def test_every_module_parses(self) -> None:
        broken = []
        for path in _python_files():
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
        self.assertEqual(broken, [], "syntax errors:\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
