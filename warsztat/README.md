# Workshop deck (HTML and PDF)

The slide deck for the workshop on 2026-10-06, in the version approved by the trainer. The slides are in Polish.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `warsztat.html` | 7462338 | `c4ca42013bc11e41261f96f3c7e69f1214cad7340fb13b130b7a1fcc5dbac105` |
| `warsztat.pdf` | 2729396 | `84b81a3861e8340f53b17c7b0981e8bec201bb05ec1c3492bdc848930b9d9be8` |

Verify the download: `cd warsztat && sha256sum -c SHA256SUMS` (on macOS: `shasum -a 256 -c SHA256SUMS`).

## How to open

- `warsztat.html` is a single self-contained file (reveal.js, images and styles embedded). Download it and open it in a browser straight from disk; it works offline. Links to external sources inside the slides need an internet connection.
- `warsztat.pdf` has one page per slide (117 pages, 16:9).

## Provenance

- Version date: 2026-10-05.
- Built from commit `ad16d37aa431ad2dddb98b743fe5ddb4aa9903d0` of the source repository, which is not public. Both files come from that one commit.
- To reproduce: in the source tree at that commit, run `make deck` (Quarto build of the HTML) followed by `make deck-pdf` (PDF printed from that HTML).
