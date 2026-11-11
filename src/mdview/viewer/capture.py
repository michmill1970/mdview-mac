"""Headless screenshot helper for the ``@webengine`` smoke tests.

QtWebEngine paints asynchronously: grabbing right after ``loadFinished``
captures a blank pixmap (measured in this very container, both offscreen and
under Xvfb).  ``capture`` therefore pumps the event loop for a short settle
window before ``grab()`` -- the same trick the probe script used to prove
rendering works headlessly.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget

#: Paint-settle window; generous on a GPU-less container, cheap in a test run.
DEFAULT_SETTLE_MS = 3000


def settle(ms: int = DEFAULT_SETTLE_MS) -> None:
    """Run the event loop for ``ms`` so queued Chromium paints actually happen."""
    loop = QEventLoop()
    timer = QTimer(loop)
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(ms)
    loop.exec()


def grab(widget: QWidget) -> QPixmap:
    """Grab ``widget`` with a settle window (see module docstring)."""
    settle()
    return widget.grab()


def capture(widget: QWidget, path: Path | str, *, settle_ms: int = DEFAULT_SETTLE_MS) -> Path:
    """Save ``widget`` to ``path`` as PNG and return the path.

    The PNG is only guaranteed to contain real content once Chromium finished a
    paint -- tests additionally assert on DOM state via ``runJavaScript``, so a
    blank screenshot still fails the suite loudly.
    """
    target = Path(path)
    settle(settle_ms)
    ok = widget.grab().save(str(target), "PNG")
    if not ok:  # pragma: no cover - only fails on a broken disk
        raise OSError(f"could not write screenshot to {target}")
    return target
