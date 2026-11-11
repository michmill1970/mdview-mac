# Release Checklist — 0.1.0

## Code

- [x] `uv run pytest` green
- [x] `uv run ruff check` clean
- [x] `uv run mypy src` clean
- [ ] PyInstaller build smoke-passed on a clean macOS runner

## Freeze versions

```python
import json
import subprocess

frozen = subprocess.run(
    ["uv", "pip", "freeze"], capture_output=True, text=True, check=True
).stdout
assert "pyside6==6.12.0" in frozen.lower()
```

## Budgets

Build weight stays under $\approx 90\ \text{MB}$ and first paint under
$200\ \text{ms}$ on a 2019-era laptop.

```bash
uv build
python -m twine check dist/*
```

## Sign-off

| Role          | Who  | Done |
| ------------- | ---- | ---- |
| Engineering   | Jo   | ✅   |
| Design        | Pam  | ⏳   |
| Release bot   | 🤖   | —    |

---

See also: [the demo document](demo.md) and [meeting notes](meeting-notes.md).
