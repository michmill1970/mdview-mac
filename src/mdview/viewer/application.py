"""Application bootstrap: QtWebEngine setup, profile, window, macOS open events.

Order matters here (this is the classic QtWebEngine footgun):

1. set ``AA_ShareOpenGLContexts`` and any ``QTWEBENGINE_*`` flags,
2. register the ``mdview`` URL scheme,
3. create ``QApplication`` (a :class:`MDViewApplication`, so Finder open events
   reach us), and only then
4. create the ``QWebEngineProfile`` and install the scheme handler on it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QApplication

from mdview.core.palette import PaletteRegistry
from mdview.core.paths import AppDir, app_dir, builtin_palettes_dir, settings_path
from mdview.core.settings import Settings, load_settings_or_default
from mdview.render.assets import vendored_report
from mdview.render.fetch import fetch_assets
from mdview.viewer.main_window import MainWindow, resolve_initial_settings
from mdview.viewer.scheme import (
    SCHEME_NAME,
    MdViewSchemeHandler,
    OfflineInterceptor,
    register_mdview_scheme,
)

ORGANIZATION_NAME = "mdview"
ORGANIZATION_DOMAIN = "mdview.invalid"

#: Chromium flags used only in headless/Linux dev containers (never on macOS).
_CONTAINER_CHROMIUM_FLAGS = ("--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage")


@dataclass(slots=True)
class AppContext:
    """Everything a running app needs, in one inspectable bundle."""

    app: QApplication
    profile: QWebEngineProfile
    scheme_handler: MdViewSchemeHandler
    interceptor: OfflineInterceptor
    registry: PaletteRegistry
    settings: Settings
    window: MainWindow
    settings_path: Path
    startup_warnings: tuple[str, ...] = ()


class MDViewApplication(QApplication):
    """QApplication that turns macOS ``FileOpen`` events into open requests.

    This is what makes Finder double-click *and* Dock-icon drops work in the
    bundled .app: Launch Services delivers a ``QFileOpenEvent`` to the
    application, not argv, and without this handler the document would simply
    be ignored.  The handler routes through the same ``open_paths`` logic as
    in-window drag-and-drop and ``File ▸ Open…`` (DESIGN.md §7).
    """

    def __init__(self, argv: Sequence[str] = ()) -> None:
        super().__init__(list(argv) or [sys.argv[0]])
        self.setApplicationName("MDView")
        self.setApplicationDisplayName("MDView")
        self.setOrganizationName(ORGANIZATION_NAME)
        self.setOrganizationDomain(ORGANIZATION_DOMAIN)
        self._open_paths: list[str] = []
        self._on_open: object | None = None

    def set_open_handler(self, handler: object) -> None:
        """Register ``callable(Sequence[str])``; queued early events are replayed."""
        self._on_open = handler
        if self._open_paths:
            pending, self._open_paths = self._open_paths, []
            handler(pending)  # type: ignore[operator]

    def event(self, event: QEvent) -> bool:
        if isinstance(event, QFileOpenEvent):
            path = event.file() or (event.url().toLocalFile() if event.url().isValid() else "")
            if path:
                if self._on_open is None:
                    self._open_paths.append(path)
                else:
                    self._on_open([path])  # type: ignore[operator]
                return True
        return super().event(event)


def prepare_webengine_environment(*, force_container_flags: bool = False) -> None:
    """Chromium attributes/flags. Must run **before** ``QApplication``.

    With no GPU and no user namespace (container, CI) Chromium exits before it
    paints unless the sandbox and GPU are disabled.  On macOS we set nothing, so
    the shipped app always uses the real GPU path.
    """
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    linux_like = sys.platform.startswith("linux") or force_container_flags
    if not linux_like:
        return
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    missing = [flag for flag in _CONTAINER_CHROMIUM_FLAGS if flag not in existing]
    if missing:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join([*missing, existing]).strip()
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")


def create_application(argv: Sequence[str] = ()) -> MDViewApplication:
    """The one and only QApplication (idempotent: reuses an existing instance)."""
    existing = QApplication.instance()
    if isinstance(existing, MDViewApplication):
        return existing
    return MDViewApplication(argv)


def build_profile(
    app: QApplication, scheme_handler: MdViewSchemeHandler
) -> tuple[QWebEngineProfile, OfflineInterceptor]:
    """One off-the-record profile for the whole app, with our scheme + interceptor.

    Returns the profile and its interceptor: Qt does not take ownership of the
    interceptor, so the app must keep a reference to it alive.
    """
    profile = QWebEngineProfile(app)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    # The scheme *flags* are registered globally (register_mdview_scheme); the
    # profile only needs the handler, keyed by the scheme name as bytes.
    profile.installUrlSchemeHandler(SCHEME_NAME, scheme_handler)
    interceptor = OfflineInterceptor(profile)
    profile.setUrlRequestInterceptor(interceptor)
    return profile, interceptor


def ensure_assets(*, allow_download: bool = True) -> tuple[str, ...]:
    """Make sure KaTeX/Mermaid are on disk; returns human-readable notes."""
    notes: list[str] = []
    report = vendored_report()
    if report["missing"] and allow_download:
        try:
            notes.append(f"first run: {fetch_assets().summary()}")
        except Exception as exc:
            notes.append(f"could not fetch bundled assets: {exc}")
    still_missing = vendored_report()["missing"]
    if still_missing:
        notes.append(
            f"{len(still_missing)} bundled asset files missing -> uv run mdview fetch-assets"
        )
    return tuple(notes)


def bootstrap(
    argv: Sequence[str] = (),
    *,
    ensure_bundled_assets: bool = True,
    force_container_flags: bool = False,
) -> AppContext:
    """Create the application and its window.  Split from :func:`run` for tests."""
    prepare_webengine_environment(force_container_flags=force_container_flags)
    register_mdview_scheme()
    app = create_application(argv)

    warnings: list[str] = []
    if ensure_bundled_assets:
        warnings.extend(ensure_assets())

    settings_file = settings_path()
    settings, warning = load_settings_or_default(settings_file)
    if warning:
        warnings.append(warning)
    registry = PaletteRegistry.from_dirs(builtin_palettes_dir(), app_dir(AppDir.PALETTES))
    settings = resolve_initial_settings(settings, registry)

    scheme_handler = MdViewSchemeHandler(parent=app)
    profile, interceptor = build_profile(app, scheme_handler)
    window = MainWindow(settings, registry, scheme_handler, profile)

    def open_files(paths: Sequence[str]) -> None:
        window.open_paths([Path(p) for p in paths])
        window.raise_()
        window.activateWindow()

    app.set_open_handler(open_files)
    return AppContext(
        app=app,
        profile=profile,
        scheme_handler=scheme_handler,
        interceptor=interceptor,
        registry=registry,
        settings=settings,
        window=window,
        settings_path=settings_file,
        startup_warnings=tuple(warnings),
    )


def run(argv: Sequence[str] = (), *, paths: Sequence[Path] = ()) -> int:
    """GUI entry point for ``mdview`` / ``python -m mdview``."""
    context = bootstrap(argv)
    for warning in context.startup_warnings:
        print(f"mdview: {warning}", file=sys.stderr)
    explicit = list(paths)
    to_open = explicit or [Path(p) for p in context.settings.open_files]
    if to_open:
        # Startup session restore is not an explicit open request: a file that
        # vanished since last run is skipped quietly instead of tabifying.
        context.window.open_paths(to_open, quiet_missing=not explicit)
    context.window.show()
    return int(context.app.exec())
