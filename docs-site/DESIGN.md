# Reader-first design contract

The site answers, in order: what this project is, what actually ran, what we learned, and what remains unknown. Overview, Results, How we tested, Run it yourself, and Sources are the five reader-facing destinations. Technical records are supporting repository evidence, not separate reading paths.

## Brand and color rhythm

Use the existing Racecraft SVG wordmark in the header and a smaller version in the footer. Do not replace it with generated lettering. Retain Space Grotesk, Geist, the warm-neutral light palette, navy/charcoal dark palette, and blue links. Red is a restrained structural accent, not a success/failure signal.

The lab-bench texture follows [Racecraft's source CSS](https://github.com/racecraft-lab/racecraft/blob/main/website/src/styles/global.css): a 40px grid plus 4px dots. Light uses the source black marks at 5%/3% opacity. Dark uses the brand's blue-gray #7cb3dd at 7%/4%; the geometry stays the same. This dark adaptation is a design choice, not a claimed source token.

Sections progress from warm paper to a low-chroma blue-tinted process section and back to a warm-neutral definitions section. Dark mode uses corresponding navy, blue-gray, and warm charcoal surfaces. Spacing and headings carry the hierarchy; large saturated bands, strong boundary lines, and decorative card grids are avoided.

The texture is the desk beneath the content, never the reading surface. All reading copy, including the hero, report sections, reference documents, and footer, sits on opaque surfaces. Every page has one continuous main reading sheet, with restrained cool/warm sections inside it at meaningful topic changes. Short pages need only one inset section; longer reports use several. Do not split every paragraph into a separate tile.

This applies [NN/g's common-region guidance](https://www.nngroup.com/articles/common-region/): backgrounds can group related content, but too many high-contrast bands create clutter or false endings. The exact surface colors are project design choices, not universal color-psychology claims.

Text must meet [WCAG 2.2 contrast minimums](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), including where the texture is visible. Validate both themes, keyboard focus, reduced motion, and 320px reflow. Color must never be the only carrier of result status.

## Evidence presentation

Page openings pair an editorial headline with a compact page-specific explanation. Process pages use a numbered path; Results shows the measured 54.55% GPQA Diamond result, runtime performance, source-labeled directional frontier context, and unsupported-claim boundary. These are static explanatory graphics, not simulated live instruments. The homepage concept's redundant icon column was omitted to keep the diagram readable at the narrower documentation width. All diagram copy is real accessible HTML, with no generated image used as UI. The compact Splash/Evals typographic lockup is secondary to the official Racecraft SVG; the SVG remains unchanged.

The Results board follows [model-card reporting practice](https://huggingface.co/docs/hub/model-cards) and [HELM's multi-scenario reporting principles](https://crfm.stanford.edu/2022/11/17/helm.html), without claiming official certification or an overall model score. Capability and runtime performance stay separate. Historical scores share a benchmark and metric family but remain directional until their full protocols align. Unknown values are text, never zero-length bars.

`results/public/gpqa-diamond-splash-local-2026-09-20.json` is the reviewed presentation record. It contains aggregates and provenance hashes only. Its validator rejects missing values, raw/private field names, and local absolute paths.

Do not turn one benchmark into broad capability coverage. Do not rank models, show comparative bars, or infer equivalence or exact gaps from publisher-reported context. Retired reader URLs redirect to the closest retained section. Source-number verification means accurate transcription, not independent reproduction or comparable testing.

## Section hierarchy and feedback

The September 2026 refinement follows [NN/g's scanning research](https://www.nngroup.com/articles/layer-cake-pattern-scanning/): recognizable headings and meaningful groups let readers find a section before reading it. A short crimson heading rule provides orientation without adding another label or decoration-heavy card grid. Process steps have a connected numbered rail; definitions use semantic term/description rows; evidence tables use stronger headers, quiet row contrast, and generous cell spacing. Technical disclosures have a full-width control and a directional indicator. The opaque reading sheet and section palette remain shared across all pages.

Three preview-only section concepts guided the implementation: a cool process timeline, warm editorial definitions, and dark evidence/disclosure treatments. Fidelity checks compare heading hierarchy, brand typography, opaque surfaces, content spacing, connector geometry, and table/disclosure structure. Intentional differences: keep the site's full evidence caveats and table columns instead of abbreviated concept copy; use the established responsive documentation width; avoid decorative arrows on every link. Generated UI images are not shipped.

Following [Carbon's motion guidance](https://carbondesignsystem.com/elements/motion/overview/), feedback is limited to links and disclosure indicators: short 150ms transitions, persistent underlines, and visible keyboard outlines. Static content does not lift or pretend to be clickable. No scroll reveals, looping motion, count-ups, or hover-only evidence. [Reduced-motion preferences](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) disable the new transitions. Disclosures and standalone navigation links target at least 44px height; inline prose links retain natural line spacing, consistent with the [WCAG target-size exception for inline links](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html). These are implementation choices, not a claim of full WCAG certification.

## Verification boundary

Automated route, search, keyboard-disclosure, theme, contrast, and reflow checks plus browser inspection qualify the implementation. They are not a novice usability study. A follow-up study should ask real first-time readers to explain the project's purpose, identify what ran, describe what it proves, and find why historical comparison is unavailable.
