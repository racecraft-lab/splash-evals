# Reader-first design contract

The site answers, in order: what we are trying to learn, why it matters, what we tested, what we found, and what it means. Overview, Results, What it means, How we tested, and Sources are the reader-facing destinations in the header. Run it yourself is for builders and lives in the footer. Technical records are supporting repository evidence, not separate reading paths.

## Voice

Main-path pages (Overview, Results, What it means, and each result's opening) are written for non-technical readers: friendly "we", short sentences, and everyday words, at about a grade 8-9 reading level. Technical detail stays one click away in the same page (disclosures, tables, the method page). Every number keeps its source and unit; plain phrasings ("about 7 in 10", "just over half") sit next to the exact figure, never instead of it.

## Brand and color

Use the existing Racecraft SVG wordmark in the header and a smaller version in the footer. Do not replace it with generated lettering. Fonts are Space Grotesk (display), Geist (body), and Fira Code (labels, readouts, and code). Colors come from the [Racecraft color system](https://github.com/racecraft-lab/racecraft/blob/main/docs/brand/color-system.md): warm neutrals (#f1f0ec base), red #dc143c, and blue #3c89c6; dark mode uses #0a0a0a and #1f2937 surfaces. Small red text uses darker tints for contrast (#b8102f on light, #ff6b84 on dark). Red is a structural accent, not a success or failure signal.

## The lab bench

Every page reads as an experiment on an electronics lab bench.

- **Bench mat:** the page background is Racecraft's texture (a 40px grid plus 4px dots, per [Racecraft's source CSS](https://github.com/racecraft-lab/racecraft/blob/main/website/src/styles/global.css)). Dark mode uses blue-gray #7cb3dd at 7%/4%, a design choice rather than a source token.
- **Sheet:** each page is one sheet of engineering paper (a fine blue grid) taped to the mat, lifted by a light-from-above shadow stack. The footer and the benchmark tabs sit on the same bench with the same lift and no tape.
- **Notepad pages:** reading text never sits on a texture. Every block of copy is a solid notepad page in the sheet's own paper color, with a blue notebook margin rule (never red, so it cannot read as a second signal trace), so the grid shows only around the pages.
- **Signal trace:** each reader section is a numbered test point on a vertical trace. The trace fills to the reading line as you scroll and the current test point lights up. The trace is an overlay beside the content, never a wrapper, so the page stays plain server-rendered HTML.
- **Instruments:** result readouts use Fira Code numbers; the scope screens on result cards are decorative and identical on every card, so they never imply a comparison.

These are skeuomorphic accents, kept light. All diagram copy is real HTML; no generated image is used as UI.

## Evidence presentation

The Results pages follow [model-card reporting practice](https://huggingface.co/docs/hub/model-cards) and [HELM's multi-scenario reporting principles](https://crfm.stanford.edu/2022/11/17/helm.html), without claiming certification or an overall model score. Each benchmark keeps its own metric; percentages are never combined, and the site never ranks one test's result against the other's. Capability and runtime stay separate. Unknown values are text, never zero-length bars.

Published cloud-model scores are directional context. Do not rank models, show comparative bars, or state an exact gap. Source-number verification means accurate transcription, not independent reproduction. Retired pages are deleted, not redirected.

The explorers progressively enhance the source-labeled tables rather than creating a second data model. They use one fixed 0–100 scale whose labels sit on the track at every width, keep the local row pinned, and provide the whole table when JavaScript is unavailable. The Operations boundary uses semantic HTML as both diagram and full text equivalent.

## Motion

Motion is subtle and always optional: the scroll-linked trace, a slow pulse on the delegation wires, a moving glow on the scope screens, and a breathing ring on the active test point. Nothing flickers, and every number is readable without animation. Under [reduced-motion preferences](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) all loops stop and the trace shows statically. Links and disclosures use short transitions and visible keyboard outlines; disclosures and standalone navigation links are at least 44px tall.

## Verification boundary

Automated route, search, keyboard-disclosure, theme, contrast ([WCAG 2.2 minimums](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)), paper-not-texture, and 320px reflow checks plus browser inspection qualify the implementation. They are not a usability study. A follow-up study should ask first-time readers to explain the project's purpose, what ran, what it shows, and why it is not a ranking.
