# Reader-first design contract

The site answers, in order: what this project is, what actually ran, what we learned, and what remains unknown. Overview, Results, How we test, and Run it yourself are the primary destinations. Technical records are supporting evidence, not the first reading task.

## Brand and color rhythm

Use the existing Racecraft SVG wordmark in the header and a smaller version in the footer. Do not replace it with generated lettering. Retain Space Grotesk, Geist, the warm-neutral light palette, navy/charcoal dark palette, and blue links. Red is a restrained structural accent, not a success/failure signal.

The lab-bench texture follows [Racecraft's source CSS](https://github.com/racecraft-lab/racecraft/blob/main/website/src/styles/global.css): a 40px grid plus 4px dots. Light uses the source black marks at 5%/3% opacity. Dark uses the brand's blue-gray #7cb3dd at 7%/4%; the geometry stays the same. This dark adaptation is a design choice, not a claimed source token.

Sections progress from warm paper to a low-chroma blue-tinted process section and back to a warm-neutral definitions section. Dark mode uses corresponding navy, blue-gray, and warm charcoal surfaces. Spacing and headings carry the hierarchy; large saturated bands, strong boundary lines, and decorative card grids are avoided.

The texture is the desk beneath the content, never the reading surface. All reading copy, including the hero, report sections, reference documents, and footer, sits on opaque surfaces. Every page has one continuous main reading sheet, with restrained cool/warm sections inside it at meaningful topic changes. Short pages need only one inset section; longer reports use several. Do not split every paragraph into a separate tile.

This applies [NN/g's common-region guidance](https://www.nngroup.com/articles/common-region/): backgrounds can group related content, but too many high-contrast bands create clutter or false endings. The exact surface colors are project design choices, not universal color-psychology claims.

Text must meet [WCAG 2.2 contrast minimums](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), including where the texture is visible. Validate both themes, keyboard focus, reduced motion, and 320px reflow. Color must never be the only carrier of result status.

## Evidence presentation

Page openings pair an editorial headline with a compact page-specific explanation. Process pages use a numbered path; Results uses the existing aggregate and an adjacent qualification caveat; the comparison page separates unlike evidence. These are static explanatory graphics, not simulated live instruments. The homepage concept's redundant icon column was omitted to keep the diagram readable at the narrower documentation width. All diagram copy is real accessible HTML, with no generated image used as UI. The compact Splash/Evals typographic lockup is secondary to the official Racecraft SVG; the SVG remains unchanged.

The Results board follows [model-card reporting practice](https://huggingface.co/docs/hub/model-cards) and [HELM's multi-scenario reporting principles](https://crfm.stanford.edu/2022/11/17/helm.html), without claiming official certification or an overall model score. Historical scores are grouped by benchmark and metric. Unknown IFEval variants, unresolved GPQA splits, dates, extraction caveats, and Aider attempt semantics stay visible. Unmeasured local results are text, never zero-length bars. The historical tables are checked against all 15 source catalog records by `tests/unit/test_dashboard_references.py`.

`docs/qualification-summary.json` is the single presentation record for the already reviewed aggregate. It is not a new raw-data export. The generator inserts a shared finding, count table, and technical classification into canonical Markdown markers. Its validator rejects missing counts, contradictory classifications, and incompatible success wording. Keep the adjacent capability and historical-comparison caveats visible.

Do not turn planned task ceilings into completed coverage. Do not rank models, show comparative bars, or infer per-task outcomes from this aggregate. Preserve all existing page URLs. Source-number verification means accurate transcription, not independent reproduction or comparable testing.

## Verification boundary

Automated route, search, keyboard-disclosure, theme, contrast, and reflow checks plus browser inspection qualify the implementation. They are not a novice usability study. A follow-up study should ask real first-time readers to explain the project's purpose, identify what ran, describe what it proves, and find why historical comparison is unavailable.
