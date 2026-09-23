# Content-aware diagrams — first quality increment

Implemented on 2026-09-22. Planner implementation 0.4.3; variant_planner prompt/skill 0.4.1.

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
  now stays inside its slot.

## Validation and limitations

Regression tests cover all eight kinds, malformed/missing kinds, excessive nodes, long text,
facts, unchanged input, diagram-aware matching, and the three mode boundaries. Integration tests
bind content against real analyzed compositions, validate the SlidePlan, compose PPTX, reopen it,
and check all node headings/explanations.

Font-metric checks are conservative estimates, not pixel verification. ONLYOFFICE is not configured
in the local environment, so PDF/pixel visual acceptance remains outstanding. Model output is
supplied deterministically in tests; this does not establish real-model selection frequency or
source-grounding accuracy.

Changing the prompt and response schema invalidates existing plan replay keys. The local LLM
provider is unconfigured, so fresh recordings cannot be created here. Replay-dependent tests
currently fail with `ReplayMissError` (including the pipeline test wrapping that error), not
diagram assertions. With a configured provider, run
`/Users/nitemin/Desktop/mai-vk/.venv/bin/python /Users/nitemin/Desktop/mai-vk/scripts/record_llm_fixtures.py --only plan`
and rerun the generation/layout suite. Existing recordings have not been relabelled as new responses.

Not included in this increment: multiple datasets per slide, dashboards, new composition families,
pixel-based underfill scoring, or a redesign of chart typography. These require separate validation.