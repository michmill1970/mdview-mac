# Weekly Sync — 2026-11-11

**Attendees:** Pam, Jo, Sam

## Agenda

1. Renderer performance pass
2. Palette editor polish
3. macOS packaging plan

## Decisions

- [x] Ship Pygments highlighting in 0.1
- [x] Vendor KaTeX + Mermaid (no CDN, ever)
- [ ] Code-sign the `.app` bundle — *next week*

## Notes

> "The viewer must open offline on a plane."
> — the product owner, every single meeting

Perf snapshot from last night's run:

| Pass       | p50     | p95     |
| ---------- | ------- | ------- |
| parse      | 3.1 ms  | 7.8 ms  |
| highlight  | 5.4 ms  | 14 ms   |
| assemble   | 1.2 ms  | 2.6 ms  |

Action items live in the [DESIGN doc](DESIGN.md); discussion in `#mdview-mac`.
