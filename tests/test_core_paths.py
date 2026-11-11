"""Tests for the writable-path layer (``mdview.core.paths``)."""

from __future__ import annotations

import pytest

from mdview.core import paths
from mdview.core.errors import MDViewError


def test_app_dir_unwritable_raises_app_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)  # readable/executable, not writable
    monkeypatch.setenv(paths.DATA_DIR_ENV_VAR, str(locked / "mdview"))
    try:
        with pytest.raises(MDViewError) as excinfo:
            paths.app_dir(paths.AppDir.PALETTES)
        message = str(excinfo.value)
        assert paths.DATA_DIR_ENV_VAR in message  # tells the user the escape hatch
        assert "locked" in message  # and the offending path
    finally:
        locked.chmod(0o700)  # keep pytest's tmp cleanup happy
