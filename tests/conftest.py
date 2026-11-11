"""Shared fixtures.

Qt/WebEngine environment variables must be set before *anything* imports
PySide6, so they are applied at conftest import time (pytest imports conftest
before test modules).  ``QT_QPA_PLATFORM=offscreen`` makes the whole suite
headless on this Linux box; on macOS the same tests run against the cocoa
platform unchanged.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage",
)

import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
assert SRC.is_dir(), "src layout missing"
if str(SRC) not in sys.path:  # editable install normally handles this
    sys.path.insert(0, str(SRC))

from mdview.core import paths  # noqa: E402
from mdview.core.palette import Palette, PaletteRegistry  # noqa: E402

VENDOR_CACHE = Path("/tmp/opencode/mdview-vendor-cache")

DEMO_MARKDOWN = """\
# Demo Document

Inline math $E = mc^2$ and a block:

$$\\int_0^1 x^2\\,dx = \\tfrac13$$

| Name | Value |
| ---- | ----- |
| a    | 1     |

- [x] implemented
- [ ] pending

~~struck~~ **bold** `code`

```python
def greet(name):
    return f"hi {name}"  # comment
```

```mermaid
graph TD; A[Start] --> B[Done];
```

[^1]: a footnote
"""


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own writable app directory (never touches $HOME)."""
    data = tmp_path / "mdview-data"
    monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(data))
    return data


@pytest.fixture
def registry() -> PaletteRegistry:
    return PaletteRegistry.from_dirs(paths.builtin_palettes_dir())


@pytest.fixture
def dark_palette(registry: PaletteRegistry) -> Palette:
    return registry.get("github-dark")


@pytest.fixture
def light_palette(registry: PaletteRegistry) -> Palette:
    return registry.get("github-light")


@pytest.fixture
def demo_file(tmp_path: Path) -> Path:
    path = tmp_path / "demo.md"
    path.write_text(DEMO_MARKDOWN, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def vendor_cache() -> Path:
    """Vendored KaTeX/Mermaid shared by webengine tests (fetched at most once)."""
    from mdview.render.fetch import fetch_assets

    target = VENDOR_CACHE / "vendor"
    complete = (
        (target / "lock.json").is_file()
        and (target / "mermaid" / "mermaid.min.js").is_file()
        and (target / "katex" / "katex.min.js").is_file()
    )
    if not complete:
        saved = os.environ.get(paths.DATA_DIR_ENV_VAR)
        os.environ[paths.DATA_DIR_ENV_VAR] = str(VENDOR_CACHE)
        try:
            fetch_assets(force=True)
        finally:
            if saved is None:
                os.environ.pop(paths.DATA_DIR_ENV_VAR, None)
            else:
                os.environ[paths.DATA_DIR_ENV_VAR] = saved
    return target


@pytest.fixture
def seeded_vendor(isolated_data_dir: Path, vendor_cache: Path) -> Path:
    """Seed the per-test data dir with the cached vendor tree (no network)."""
    target = isolated_data_dir / "vendor"
    if vendor_cache.is_dir():
        shutil.copytree(vendor_cache, target, dirs_exist_ok=True)
    return target
