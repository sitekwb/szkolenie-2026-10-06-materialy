# Workshop deck (HTML and PDF)

The slide deck for the workshop on 2026-10-06, in the version approved by the trainer. The slides are in Polish.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `warsztat.html` | 7521010 | `1c5cba0fe66e072128cb889c9b5d2242a7bd772136c103b3b02623372ecb9f99` |
| `warsztat.pdf` | 2982957 | `309a3778caa92376fdad6906f4d09c000d8059b714e0516adc91190249e6f028` |

Verify the download: `cd warsztat && sha256sum -c SHA256SUMS` (on macOS: `shasum -a 256 -c SHA256SUMS`).

## How to open

- `warsztat.html` is a single self-contained file (reveal.js, images and styles embedded). Download it and open it in a browser straight from disk; it works offline. Links to external sources inside the slides need an internet connection.
- `warsztat.pdf` has one page per slide (126 pages, 16:9).

## Provenance

- Version date: 2026-10-06.
- Built from commit `57738dbac1faaeb99943179872ec3938fd2347a5` of the source repository, which is not public. Both files come from that one commit.
- To reproduce: in the source tree at that commit, run `make deck` (Quarto build of the HTML) followed by `make deck-pdf` (PDF printed from that HTML).

## Changes since the previous version

The previous version was built from source commit `1515853`.

- Progress board repeated before each part of the day (9 repeats, 126 slides).
