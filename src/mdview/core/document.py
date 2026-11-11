"""Markdown documents on disk (pure).

MDView is a *viewer*: a :class:`Document` is a read-only handle that knows how
to be read, how to be named, and whether it changed on disk (so the watcher in
the GUI layer can offer live reload of externally edited files).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from mdview.core.errors import DocumentError

__all__ = [
    "SUPPORTED_SUFFIXES",
    "Document",
    "error_page_markdown",
    "inspect_openable",
    "is_supported",
]

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown"})
#: Document types registered in Info.plist must match this set exactly.
#: This governs *Finder* double-click registration only -- it is deliberately
#: NOT a gate for opening a path the user named explicitly (see
#: :func:`inspect_openable`; DESIGN.md "Open-file policy").

_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

#: Bytes sniffed for the "this is binary, not text" decision.
_SNIFF_BYTES = 8192


def is_supported(path: Path | str) -> bool:
    """Whether a path has a suffix Finder will route to MDView."""
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def inspect_openable(path: Path | str) -> str | None:
    """Why ``path`` cannot be opened right now, or ``None`` if it can.

    Policy (Phase 6 decision, DESIGN.md "Open-file policy"): a path the user
    named explicitly is opened *as Markdown* whatever its suffix -- a ``.txt``
    file is friendlier to view than to refuse.  Only genuinely unviewable
    inputs fail: missing files, directories, unreadable files, and files that
    do not look like text.  Both the GUI (error tabs) and the CLI (exit codes)
    classify through this one function, so their verdicts cannot drift.
    """
    p = Path(path).expanduser()
    if p.is_dir():
        return f"not a file: {p} is a directory"
    if not p.exists():
        return f"file not found: {p}"
    try:
        with p.open("rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError as exc:
        return f"cannot read {p}: {exc.strerror or exc}"
    if b"\x00" in head:
        return f"not a text file: {p}"
    return None


def error_page_markdown(path: Path | str, reason: str) -> str:
    """The Markdown body of an error tab (rendered like any other page)."""
    p = Path(path)
    lines = [
        f"# Can’t open `{p.name}`",  # noqa: RUF001 - typographic apostrophe is intentional
        "",
        f"**{reason}**",
        "",
        f"- Full path: `{p.expanduser()}`",
        "- MDView opens any readable UTF-8 text file by path; a `.md` suffix",
        "  is not required — the suffix only controls Finder registration.",
        "- Fix the path (*File ▸ Open…* or drag the file into the window)",
        "  and open it again.",
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class Document:
    """One Markdown file on disk."""

    path: Path
    #: Cached display title (first heading, else filename stem).
    _title: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser())

    @classmethod
    def open(cls, path: Path | str) -> Document:
        """Read the file once and derive its title."""
        p = Path(path).expanduser()
        try:
            source = p.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise DocumentError(f"file not found: {p}") from exc
        except OSError as exc:
            raise DocumentError(f"cannot read {p}: {exc.strerror or exc}") from exc
        return cls(path=p, _title=title_from_markdown(source, fallback=p.stem))

    @property
    def title(self) -> str:
        """Tab title: the first heading if present, otherwise the filename."""
        return self._title or self.path.stem

    @property
    def key(self) -> str:
        """De-duplication key: absolute, symlink-resolved path."""
        try:
            return str(self.path.resolve(strict=False))
        except OSError:  # pragma: no cover - e.g. permission denied on a parent
            return str(self.path.absolute())

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> str:
        """Current file contents (UTF-8, BOM stripped, undecodable bytes replaced)."""
        try:
            return self.path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise DocumentError(f"cannot read {self.path}: {exc.strerror or exc}") from exc

    @property
    def stat_token(self) -> tuple[int, int]:
        """Cheap ``(mtime_ns, size)`` pair used to detect external edits."""
        try:
            st = os.stat(self.path)
        except OSError:
            return (-1, -1)
        return (st.st_mtime_ns, st.st_size)

    def matches(self, other: Path | str) -> bool:
        """Whether ``other`` refers to the same file (used for CLI/open events)."""
        try:
            return self.key == str(Path(other).expanduser().resolve(strict=False))
        except OSError:  # pragma: no cover
            return self.key == str(Path(other).expanduser())


def title_from_markdown(source: str, *, fallback: str) -> str:
    """First heading text of a Markdown document, skipping fenced code blocks."""
    fence: str | None = None
    for line in source.splitlines()[:200]:
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        heading = _ATX_HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip()
            if title:
                return title[:120]
    return fallback
