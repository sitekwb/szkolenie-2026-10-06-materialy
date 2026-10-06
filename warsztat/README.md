# Workshop deck (HTML and PDF)

The slide deck for the workshop on 2026-10-06, in the version approved by the trainer. The slides are in Polish.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `warsztat.html` | 7469208 | `97950fa856585ff9347130a1e8a622562e364722338b0bbd58d23be4ae7497e9` |
| `warsztat.pdf` | 2745178 | `4a6a1eb32ce5946025b2b7c9288b55fe63474aeb0567994becd79e9c2863f4f8` |

Verify the download: `cd warsztat && sha256sum -c SHA256SUMS` (on macOS: `shasum -a 256 -c SHA256SUMS`).

## How to open

- `warsztat.html` is a single self-contained file (reveal.js, images and styles embedded). Download it and open it in a browser straight from disk; it works offline. Links to external sources inside the slides need an internet connection.
- `warsztat.pdf` has one page per slide (117 pages, 16:9).

## Provenance

- Version date: 2026-10-05.
- Built from commit `151585355fc783d879e45a03708f10fa8f1f1cf3` of the source repository, which is not public. Both files come from that one commit.
- To reproduce: in the source tree at that commit, run `make deck` (Quarto build of the HTML) followed by `make deck-pdf` (PDF printed from that HTML).

## Changes since the previous version

The previous version was built from source commit `ad16d37`.

- Neutral author line on the title slide.
- Slides revised after an end-to-end run on a workshop VM, so they match the materials in `hitl-gate/`, `agent-sdk-demo/` and `serwer-mcp-nbp/` of this repository.
- Homework assignments updated to match those materials.
