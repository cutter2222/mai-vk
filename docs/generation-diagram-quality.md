# Content-aware diagrams — first quality increment

Implemented on 2026-09-22. Planner implementation 0.4.4; variant_planner prompt/skill 0.4.1.

## Behaviour

- The model supplies `diagram_kind` and existing `items`, not coordinates or pattern IDs.
- Supported kinds: process, cycle, pyramid, hierarchy, matrix, funnel, timeline, venn.
- Timeline determines its own kind. Other diagrams require an explicit, valid kind;
  an unspecified relationship falls back to a list, not an invented process.
- `sub` is the node heading; `text` is its explanation. Hierarchy supports one parent
  followed by immediate children only. The prompt explicitly prohibits flattening deeper trees.
- Matching admits required diagram slots only when diagram content is available. No changes
  to mode filtering: template_only excludes library compositions, all_new excludes templates,
  mixed considers both. The original variant's exception is unchanged.
- The planner tests the renderer's actual node rectangles with font metrics and conservative
  curved-shape insets. No shrinking below the diagram style's readable sizes to force content in.
- Two to six nodes; Venn at most three. Unfit content stays intact for a text-layout fallback
  or a capacity retry. Cycles/hierarchies are not split or merged into unrelated lists by the
  automatic slide-count controller.
- Fact placeholders in headings and explanations are resolved; extra facts become prose,
  never fabricated process stages. Composition also resolves placeholders from manually edited plans.
- Diagram type and item text survive the PPTX roundtrip as native editable shapes/connectors.
- Body-derived diagram type is 18–24 pt, explanations at least 16 pt. Three-circle Venn geometry
  stays inside its slot. Venn labels are separate editable text boxes above all circle fills,
  positioned away from the central overlap. Cycle spacing reserves visible arrow shafts.

## Validation and limitations

Regression tests cover all eight kinds, malformed/missing kinds, excessive nodes, long text,
facts, unchanged input, diagram-aware matching, and the three mode boundaries. Integration tests
bind content against real analyzed compositions, validate the SlidePlan, compose PPTX, reopen it,
and check all node headings/explanations.

Font-metric checks are conservative estimates, not pixel verification. The resumed check rendered
three eight-slide sample decks through the running ONLYOFFICE worker, verified page/thumbnail
counts and unchanged source hashes/editor revisions, and inspected the images. It exposed and
fixed obscured Venn text and barely visible cycle arrows. Geometry regressions cover Venn layer
order, bounds and non-overlapping label boxes, plus cycle arrows for two through six nodes.
Artifacts from this local check are in `/tmp/mai-diagrams-final-decks` and
`/tmp/mai-diagrams-render-final` (temporary, not committed). The HTML export used its image/text
fallback because these sample PPTX files have no accompanying composed-deck description.

These samples use deterministic model answers. They establish rendering and mode boundaries,
not real-model relationship selection or source-grounding accuracy. The 24 newly recorded model
responses contain six timeline selections but no explicit `diagram_kind`; all-eight-kind model
selection acceptance therefore remains outstanding. Several recorded plans also report capacity
overflow, which must not be treated as clean production-deck acceptance.

Changing the prompt and response schema invalidates existing plan replay keys. The local provider
is available after loading `/Users/nitemin/Desktop/mai-vk/.env`; direct Python invocation does not
load that file automatically. Regeneration uses
`/Users/nitemin/Desktop/mai-vk/.venv/bin/python /Users/nitemin/Desktop/mai-vk/scripts/record_llm_fixtures.py --only plan`
with the configured environment. On the host, use `PD_QUEUE_MODE=inline` for the local limiter
instead of the Docker-only `valkey` hostname. The script completed for mini/rich templates and
all three variants; 24 fresh `plan.slides` 0.4.1 responses were recorded. Existing recordings
were not relabelled or overwritten. Planner 0.4.4 invalidates saved plan-cache entries after the
geometry changes without changing the prompt/response replay keys.

The full generation/library/layout/audit/LLM/contracts replay run no longer needs replay exclusions.
The resumed run completed with 579 passed and 6 skipped; the layout/diagram-content rerun after
the final Venn sizing adjustment completed with 100 passed. PDF text extraction confirmed all
six source heading/explanation fragments on every page of the three sample decks.
Six optional Valkey limiter tests skip without a dedicated test server at `localhost:6399`.
Ruff and mypy on the changed implementation files pass. Repository-wide mypy currently reports
three pre-existing errors in `/Users/nitemin/Desktop/mai-vk/src/presentation_designer/design/fit.py`
and `/Users/nitemin/Desktop/mai-vk/src/presentation_designer/design/guard.py`;
it is not a clean global typing run.

Not included in this increment: multiple datasets per slide, dashboards, new composition families,
pixel-based underfill scoring, or a redesign of chart typography. These require separate validation.