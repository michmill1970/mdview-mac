"""Tab model (pure state machine) and appearance/theme resolution."""

from __future__ import annotations

from pathlib import Path

from mdview.core.appearance import flip, normalize_appearance_mode, resolve_appearance
from mdview.core.document import Document
from mdview.core.settings import Settings
from mdview.core.tabs import TabModel
from mdview.core.theme import default_palette_ids, mode_is_pinned, selection_for

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mdview.core.palette import PaletteRegistry


def make_doc(tmp_path: Path, name: str = "a.md") -> Document:
    """Create a real file on disk (Document.open validates existence)."""
    path = tmp_path / name
    path.write_text(f"# {name}\n", encoding="utf-8")
    return Document.open(path)


# -------------------------------------------------------------------- tabs --


def test_open_deduces_by_path_and_reactivates(tmp_path: Path) -> None:
    model = TabModel()
    doc = make_doc(tmp_path)
    first = model.open_document(doc)
    model.open_document(make_doc(tmp_path, "b.md"))
    second = model.open_document(doc)
    assert first.tab_id == second.tab_id
    assert model.count == 2
    assert model.active_tab_id == first.tab_id


def test_open_preserves_position_of_existing_tab(tmp_path: Path) -> None:
    model = TabModel()
    a = model.open_document(make_doc(tmp_path, "a.md"))
    model.open_document(make_doc(tmp_path, "b.md"))
    model.open_document(a.document)  # re-open a -> stays at index 0, becomes active
    assert model.index_of(a.tab_id) == 0
    assert model.active_tab_id == a.tab_id


def test_close_moves_activation_right_then_left(tmp_path: Path) -> None:
    model = TabModel()
    a = model.open_document(make_doc(tmp_path, "a.md"))
    b = model.open_document(make_doc(tmp_path, "b.md"))
    c = model.open_document(make_doc(tmp_path, "c.md"))
    model.activate(a.tab_id)
    model.close(a.tab_id)
    assert model.active_tab_id == b.tab_id
    model.activate(c.tab_id)
    model.close(c.tab_id)
    assert model.active_tab_id == b.tab_id


def test_close_last_tab_goes_empty(tmp_path: Path) -> None:
    model = TabModel()
    tab = model.open_document(make_doc(tmp_path))
    model.close(tab.tab_id)
    assert model.is_empty and model.active is None
    assert not model.close(tab.tab_id)  # idempotent


def test_next_previous_wrap(tmp_path: Path) -> None:
    model = TabModel()
    a = model.open_document(make_doc(tmp_path, "a.md"))
    b = model.open_document(make_doc(tmp_path, "b.md"))
    assert model.activate_next().tab_id == a.tab_id  # wraps past the end
    assert model.activate_previous().tab_id == b.tab_id


def test_activate_index_bounds(tmp_path: Path) -> None:
    model = TabModel()
    tab = model.open_document(make_doc(tmp_path))
    assert model.activate_index(0).tab_id == tab.tab_id
    assert model.activate_index(99) is None
    assert model.activate(-1) is False


def test_move_and_reorder(tmp_path: Path) -> None:
    model = TabModel()
    ids = [model.open_document(make_doc(tmp_path, f"{n}.md")).tab_id for n in "abc"]
    assert model.move(ids[2], 0)
    assert [t.tab_id for t in model.tabs] == [ids[2], ids[0], ids[1]]
    assert model.reorder_to([ids[0], ids[1], ids[2]])
    assert [t.tab_id for t in model.tabs] == ids
    assert not model.reorder_to([ids[0]])  # incomplete order rejected


def test_scroll_memory_and_stale_flag(tmp_path: Path) -> None:
    model = TabModel()
    doc = make_doc(tmp_path)
    tab = model.open_document(doc)
    model.set_scroll(tab.tab_id, 400)
    assert tab.scroll_y == 400
    stale = model.mark_stale(doc.path, True)
    assert stale is tab and stale.stale
    assert model.mark_stale(Path("/nowhere/x.md")) is None


def test_restore_skips_missing_files(tmp_path: Path) -> None:
    model = TabModel()
    good = make_doc(tmp_path, "keep.md")
    tabs = model.restore([good.path, tmp_path / "gone.md"], active_path=str(good.path))
    assert len(tabs) == 1
    assert model.active_tab_id == tabs[0].tab_id


def test_on_change_callback_fires() -> None:
    calls: list[int] = []
    model = TabModel(on_change=lambda: calls.append(1))
    model.open_document(Document(Path("a.md")))
    model.close_all()
    assert len(calls) >= 2


def test_open_paths_reports_documents() -> None:
    model = TabModel()
    doc = Document(Path("/tmp/mdview-test/x.md"))
    model.open_document(doc)
    assert model.open_paths() == (doc.key,)
    assert model.documents() == (doc,)


# ----------------------------------------------------------------- theme  --


def test_resolve_appearance_matrix() -> None:
    assert resolve_appearance("light", "dark") == "light"
    assert resolve_appearance("dark", "light") == "dark"
    assert resolve_appearance("system", "dark") == "dark"
    assert normalize_appearance_mode("nonsense") == "system"
    assert flip("dark") == "light"
    assert mode_is_pinned("dark") and not mode_is_pinned("system")


def test_selection_for_follows_system(registry: PaletteRegistry) -> None:
    settings = Settings(appearance_mode="system")
    assert selection_for(registry, settings, "dark").palette.id == "github-dark"
    light = selection_for(registry, settings, "light")
    assert light.palette.id == "github-light"
    assert light.is_dark is False


def test_selection_for_pinned_mode_and_override(registry: PaletteRegistry) -> None:
    settings = Settings(appearance_mode="dark", palette_dark="dracula-dark")
    sel = selection_for(registry, settings, "light")
    assert sel.palette.id == "dracula-dark" and sel.is_dark
    override = selection_for(registry, settings, "dark", override="high-contrast-light")
    assert override.palette.id == "high-contrast-light"
    assert override.appearance == "light"  # a preview override flips the appearance too


def test_selection_falls_back_when_palette_deleted(registry: PaletteRegistry) -> None:
    settings = Settings(palette_dark="dracula-dark")
    del registry.palettes["dracula-dark"]
    registry.order.remove("dracula-dark")
    sel = selection_for(registry, settings, "dark")
    assert sel.palette.appearance == "dark"
    assert sel.fell_back is True


def test_default_palette_ids(registry: PaletteRegistry) -> None:
    assert default_palette_ids(registry) == {"light": "github-light", "dark": "dracula-dark"}
