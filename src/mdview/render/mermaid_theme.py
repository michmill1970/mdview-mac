"""Palette -> Mermaid ``themeVariables`` (pure).

Mermaid's built-in ``default``/``dark`` themes bake in Mermaid's own purple-on-
lavender identity, which reads as a foreign object pasted onto our palette-
themed page (Phase 3 vision review: light-lavender node fills and near-
invisible edges on ``github-dark``).  The fix is ``theme: "base"`` -- which has
no identity of its own -- plus one explicit colour per role, derived from the
same palette dict that drives :mod:`mdview.render.theme_css`.

The mapping runs once per render and is serialised into the page's config JSON
as ``mermaidThemeVariables``.  ``static/app.js`` passes it to
``mermaid.initialize`` and to live palette changes
(``window.mdview.setMermaidTheme`` re-renders diagrams with the new values).

Every value is a *palette token colour*, never a computed blend, so the four
built-ins and user palettes all drive one table.  Contrast notes (worst case
across the built-ins, measured against ``background``): ``lineColor`` uses
``muted_text`` -- edges and arrowheads clear >= 5.9:1 without outranking node
text, which uses ``text``.
"""

from __future__ import annotations

from mdview.core.palette import Palette

__all__ = ["mermaid_theme_vars"]


def mermaid_theme_vars(palette: Palette) -> dict[str, str]:
    """One colour per Mermaid role, straight from ``palette``.

    Covers flowcharts (node fill/border/text, edges, edge labels, subgraph
    clusters), sequence diagrams (actors, lifelines, signals, loop/label/note
    boxes), class-diagram text, and diagram titles -- the diagram types a
    Markdown document realistically embeds.
    """
    colors = palette.colors
    surface = colors["surface"]
    text = colors["text"]
    muted = colors["muted_text"]
    accent = colors["accent"]
    border = colors["border"]
    panel = colors["code_bg"]  # recessed-panel colour: clusters, notes, label boxes
    return {
        # Canvas + text baseline
        "background": colors["background"],
        "textColor": text,
        "titleColor": colors["heading"],
        "mainBkg": surface,  # flowchart (old renderer)
        "nodeBorder": accent,
        "nodeTextColor": text,
        # Flowchart nodes: surface fill + accent-tinted border + text-coloured
        # label.  All three classes equal on purpose: Mermaid cycles
        # primary/secondary/tertiary for `classDef` variety; uniform keeps a
        # palette coherent and the demo diagrams calm.
        "primaryColor": surface,
        "primaryBorderColor": accent,
        "primaryTextColor": text,
        "secondaryColor": surface,
        "secondaryBorderColor": accent,
        "secondaryTextColor": text,
        "tertiaryColor": surface,
        "tertiaryBorderColor": accent,
        "tertiaryTextColor": text,
        # Edges + labels: `muted_text` is clearly visible on every built-in
        # background; the label background masks whatever line it sits on.
        "lineColor": muted,
        "edgeLabelBackground": colors["background"],
        "edgeLabelColor": text,
        # Subgraphs read as recessed panels (same treatment as code blocks).
        "clusterBkg": panel,
        "clusterBorder": border,
        # Sequence diagrams
        "actorBkg": surface,
        "actorBorder": accent,
        "actorTextColor": text,
        "actorLineColor": muted,
        "signalColor": muted,
        "signalTextColor": text,
        "loopTextColor": text,
        "labelBoxBkgColor": panel,
        "labelBoxBorderColor": border,
        "labelTextColor": text,
        "noteBkgColor": panel,
        "noteBorderColor": border,
        "noteTextColor": text,
        # Class diagrams
        "classText": text,
    }
