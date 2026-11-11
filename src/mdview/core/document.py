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

__all__ = ["SUPPORTED_SUFFIXES", "Document", "is_supported"]

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown"})
#: Document types registered in Info.plist must match this set exactly.

_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def is_supported(path: Path | str) -> bool:
    """Whether a path has a viewable suffix."""
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


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
