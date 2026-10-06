# Workshop deck (HTML and PDF)

The slide deck for the workshop on 2026-10-06, in the version approved by the trainer. The slides are in Polish.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `warsztat.html` | 7528830 | `67f76fdbcb3f5a374fd19dbd679c049e47219311184f5e9419a40b2ec3d0638d` |
| `warsztat.pdf` | 2998327 | `7c6f6f2b2a297848c5a8a8a62e70fa46c7b285d81c75413a792ff5f1628029e0` |

Verify the download: `cd warsztat && sha256sum -c SHA256SUMS` (on macOS: `shasum -a 256 -c SHA256SUMS`).

## How to open

- `warsztat.html` is a single self-contained file (reveal.js, images and styles embedded). Download it and open it in a browser straight from disk; it works offline. Links to external sources inside the slides need an internet connection.
- `warsztat.pdf` has one page per slide (129 pages, 16:9).

## Provenance

- Version date: 2026-10-06.
- Built from commit `0485c5ff0db339d9d05dffe6fd783daf46f851c1` of the source repository, which is not public. Both files come from that one commit.
- To reproduce: in the source tree at that commit, run `make deck` (Quarto build of the HTML) followed by `make deck-pdf` (PDF printed from that HTML).

## Changes since the previous version

The previous version was built from source commit `57738db`. The deck grew from 126 to 129 slides.

- Responsibility for AI-delivered code.
- Deterministic validations (tests, linters, type checks) used as gates before a change is accepted.
- TDD with attribution to Kent Beck, in the good-practices and HITL stages.
- A 4th discussion question on manual review.
- A "Plan na każdy etap" (plan for every stage) slide in part 1.
- A regulatory slide in the Canvas part: GDPR, AI Act, DORA, KNF.
