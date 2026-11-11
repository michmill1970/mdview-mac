# MDView Demo

A tour of everything the renderer does — all of it offline, from assets
shipped on disk.

## Math (KaTeX, local)

Inline math like $e^{i\pi} + 1 = 0$ and display math:

$$
\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}
$$

## GitHub-flavoured Markdown

| Feature | Where |
| --- | --- |
| Tables | this one |
| [x] → ✅ task lists | below |
| ~~Strikethrough~~ | this row |
| `inline code` | everywhere |

- [x] Multi-tab editing of the same document reloads cleanly
- [x] Palette changes apply without losing scroll position
- [ ] World domination

> Blockquotes keep their left rule, the theme's border colour, and nested
> *emphasis*, obviously.

## Highlighted code (Pygments, recoloured by the active palette)

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FontSize:
    size_px: int = 16  # clamped 10–32

    def percent(self) -> int:
        return round(self.size_px / 16 * 100)
```

```rust
fn main() {
    println!("syntax themes are generated from the palette, not prebuilt");
}
```

## Diagrams (Mermaid, local)

```mermaid
graph LR
    A[Markdown source] --> B[markdown-it]
    B --> C[Pygments + palette]
    B --> D[page assembly]
    D --> E[mdview://page]
    E --> F[QtWebEngine]
    C --> D
```

## Images (served from this document's folder)

![the MDView mark](images/logo.png)

## Links

Normal links open in your browser: <https://commonmark.org> — same-window
navigation only ever happens inside `mdview://`.
