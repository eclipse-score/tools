<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# PlantUML Layout Comparison

Renders the PlantUML diagrams of every SCORE repository with Graphviz and
with Smetana, PlantUML's built-in layout engine, and reports where the
results differ. It answers whether SCORE could render its diagrams without
Graphviz, and how Smetana's orthogonal routing behaves on real diagrams.

## Usage

Requires `uv`, `git`, Graphviz (`dot`) and a full JDK.

```bash
uv run plantuml_compare/compare.py --jdk /path/to/jdk
```

The script

1. updates the [repository cache](../repo_cache/README.md),
2. builds PlantUML from the branch `smetana-orthogonal-routing`,
3. renders every `.puml`, `.plantuml`, `.iuml` and `.pu` file that contains a
   diagram,
4. writes `index.html` to `~/.cache/plantuml-compare/` and the numbers of each
   run to `~/.cache/plantuml-compare/<run>/`.

| Run | Diagrams | Variants |
| --- | --- | --- |
| `ortho` | those that set `linetype ortho`, directly or through a local include | `graphviz`, `smetana-ortho` |
| `other` | all others | `graphviz`, `smetana` |

The diagram files stay untouched. The engine is chosen on the command line:
`-Playout=smetana`, and `-Psmetana_ortho=true` for the orthogonal routing.

Useful options: `--run ortho|other`, `--repo NAME` (repeatable), `--skip-sync`,
`--skip-build`, `--jar FILE`, `--reuse` to keep the renders of an earlier
run, `--jobs N`, `--timeout SECONDS`.

## Reports

- `summary.md`: totals per variant and repository, diagrams that only
  Graphviz can render, and the diagrams that got worse the most.
- `index.html`: a page to pass on, with one tab per run. It shows both
  pictures of each diagram side by side with the numbers between them. Up to
  `--embed-limit` megabytes the pictures are part of the file itself;
  above that they stay separate files and the whole folder has to be passed
  on.
- `results.csv`: every number, one row per diagram and variant.

A diagram is left out of the comparison when every variant paints the same
picture, as for sequence diagrams, which no layout engine touches, when no
variant can render it, or when it is trivial: at most one edge, no crossing
and no label overlap in any variant.

| Number | Meaning |
| --- | --- |
| edges | Connectors found in the SVG. |
| orthogonal | Connectors made of horizontal and vertical pieces only. |
| crossings | Pairs of connectors that cross away from their ends. |
| label overlaps | Pairs of texts on different connectors whose bounds overlap. The bounds are estimated from position, font size and text length. |
| edge length | Length of all edges together, in pixels. |
| bends | How often the orthogonal edges turn, all together. |
| area / graphviz | Size of the picture relative to the Graphviz one. |
| same arrangement | Pairs of elements that lie the same way to each other in both pictures: left or right, above or below. Only on the page. |
| same edge sides | Ends of edges that are attached to the same side of their element in both pictures. Only on the page. |

## Limits

- Only the first diagram of a file is rendered.
- Diagrams embedded in `.rst` or `.md` files are not collected.
- The numbers are read from the SVG and are estimates. They point to diagrams
  worth a look, they do not replace it.
