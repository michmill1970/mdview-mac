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
from collections.abc import Callable, Sequence
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

#: ``callable(paths)`` -- receives open paths as one ordered batch.
OpenHandler = Callable[[Sequence[str]], None]


@dataclass(slots=True)
class AppContext:
    """Everything a running app needs, in one inspectable bundle."""

    app: MDViewApplication
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

    This is what makes Finder double-click, multi-select "Open With", and
    Dock-icon drops work in the bundled .app: Launch Services delivers a
    ``QFileOpenEvent`` *per file* to the application, not argv, and without
    this handler the documents would simply be ignored.  Every event routes
    through the same ``open_paths`` logic as in-window drag-and-drop and
    ``File ▸ Open…`` (DESIGN.md §7), so a batch lands as ordered tabs with the
    last file active -- for a running instance each per-file event opens
    live, one open request at a time.

    Cold launch (Phase 12) is the fiddly half: the first events can arrive
    while :func:`bootstrap` is still building the window, before any handler
    exists.  Such events are queued here **in arrival order**; installing the
    window's route via :meth:`set_open_handler` flushes the queue as *one*
    batch -- exactly like one multi-file drop -- and because ``run()`` shows
    the window only after bootstrap, the first visible frame already carries
    the launched tabs: the empty no-tab state never gets a chance to flash.
    """

    def __init__(self, argv: Sequence[str] = ()) -> None:
        super().__init__(list(argv) or [sys.argv[0]])
        self.setApplicationName("MDView")
        self.setApplicationDisplayName("MDView")
        self.setOrganizationName(ORGANIZATION_NAME)
        self.setOrganizationDomain(ORGANIZATION_DOMAIN)
        self._open_queue: list[str] = []
        self._open_handler: OpenHandler | None = None
        self._launch_open_paths: list[str] = []

    # ------------------------------------------------------------- open --
    def set_open_handler(self, handler: OpenHandler) -> None:
        """Install ``handler(paths)`` and flush the cold-launch queue, in order.

        The flush is a single batch call, so the window sees files delivered
        before it existed as one ordered open request, not N racing ones.
        """
        self._open_handler = handler
        if self._open_queue:
            pending, self._open_queue = self._open_queue, []
            self._launch_open_paths.extend(pending)
            handler(pending)

    def pending_open_paths(self) -> tuple[str, ...]:
        """Open paths that arrived before any handler existed, in arrival order."""
        return tuple(self._open_queue)

    def launch_open_paths(self) -> tuple[str, ...]:
        """Paths Finder delivered at cold launch (already flushed to the window).

        :func:`run` skips session restore when this is non-empty: launching via
        Launch Services is an explicit open request, not a resumption, and the
        restored tabs would bury the launched files and steal the active tab.
        """
        return tuple(self._launch_open_paths)

    def _reset_open_state(self) -> None:
        """Back to cold-launch state: drop the handler and both path records.

        Test seam, not app logic: the suite shares one application object
        across the whole session, and cold-launch scenarios need a
        window-less (handler-less) app again between tests.
        """
        self._open_handler = None
        self._open_queue.clear()
        self._launch_open_paths.clear()

    def event(self, event: QEvent) -> bool:
        if isinstance(event, QFileOpenEvent):
            path = event.file() or (event.url().toLocalFile() if event.url().isValid() else "")
            if path:
                if self._open_handler is None:
                    self._open_queue.append(path)  # window not ready: keep order
                else:
                    self._open_handler([path])
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


def startup_paths(
    *,
    explicit: Sequence[Path] = (),
    finder_opened: Sequence[str] = (),
    saved_session: Sequence[str] = (),
) -> list[Path]:
    """The paths :func:`run` opens before the window is first shown.

    CLI paths win outright.  A Finder launch (``finder_opened``) *suppresses*
    session restore (Phase 12): bootstrap has already opened those files as
    ordered tabs, and restoring the previous session on top would bury them
    and steal the active tab.  Everything else restores the last session.
    """
    if explicit:
        return list(explicit)
    if finder_opened:
        return []
    return [Path(p) for p in saved_session]


def run(argv: Sequence[str] = (), *, paths: Sequence[Path] = ()) -> int:
    """GUI entry point for ``mdview`` / ``python -m mdview``."""
    context = bootstrap(argv)
    for warning in context.startup_warnings:
        print(f"mdview: {warning}", file=sys.stderr)
    to_open = startup_paths(
        explicit=paths,
        finder_opened=context.app.launch_open_paths(),
        saved_session=context.settings.open_files,
    )
    if to_open:
        # Startup session restore is not an explicit open request: a file that
        # vanished since last run is skipped quietly instead of tabifying.
        context.window.open_paths(to_open, quiet_missing=not paths)
    context.window.show()
    return int(context.app.exec())
