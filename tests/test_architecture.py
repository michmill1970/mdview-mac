"""The strict split: mdview.core and mdview.render must stay Qt-free."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

FORBIDDEN = ("PySide6", "PyQt6", "PyQt5")
PURE_PACKAGES = ("mdview.core", "mdview.render")


def _python_files(package: str) -> list[Path]:
    module = importlib.import_module(package)
    root = Path(str(module.__file__)).parent
    return sorted(root.rglob("*.py"))


def test_pure_sources_never_mention_qt() -> None:
    for package in PURE_PACKAGES:
        for path in _python_files(package):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert not any(name.startswith(qt) for qt in FORBIDDEN), (
                        f"{path}: pure layer must not import {name}"
                    )


def test_importing_pure_modules_pulls_in_no_qt_in_a_clean_process() -> None:
    """Subprocess check: pytest-qt itself preloads the binding, so test in a clean one."""
    script = (
        "import sys; import mdview.render.page, mdview.core.theme, mdview.render.fetch; "
        "leak = [m for m in sys.modules if m.startswith(('PySide6','PyQt'))]; "
        "sys.exit('Qt leaked: %s' % leak if leak else 0)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_qt_layer_is_importable_here() -> None:
    """The complement: this container *has* Qt, so the viewer layer must import."""
    importlib.import_module("mdview.viewer.scheme")
