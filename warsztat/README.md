# Workshop deck (HTML and PDF)

The slide deck for the workshop on 2026-10-06, in the version approved by the trainer. The slides are in Polish.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `warsztat.html` | 7522607 | `3379f8382e9afd7a70a77849c556957bbd03c3558f03f4f77aed9c19ae1d7303` |
| `warsztat.pdf` | 2983252 | `73636bdfbd29c91e2c51ab6b259f443bfe2d2d1e2f682c3aa60da0c5f684d689` |

Verify the download: `cd warsztat && sha256sum -c SHA256SUMS` (on macOS: `shasum -a 256 -c SHA256SUMS`).

## How to open

- `warsztat.html` is a single self-contained file (reveal.js, images and styles embedded). Download it and open it in a browser straight from disk; it works offline. Links to external sources inside the slides need an internet connection.
- `warsztat.pdf` has one page per slide (126 pages, 16:9).

## Provenance

- Version date: 2026-10-06.
- Built from commit `df93a009bee669aaa5ea447fe8ffa789c7fc349a` of the source repository, which is not public. Both files come from that one commit.
- To reproduce: in the source tree at that commit, run `make deck` (Quarto build of the HTML) followed by `make deck-pdf` (PDF printed from that HTML).

## Changes since the previous version

The previous version was built from source commit `57738db`. Slide count is unchanged (126).

- Responsibility for AI-delivered code.
- Deterministic validations (tests, linters, type checks) used as gates before a change is accepted.
- TDD with attribution to Kent Beck, in the good-practices and HITL stages.
- A 4th discussion question on manual review.
