# CAD to 3D MVP Roadmap

## Current State

The project has reached a minimal MVP:

- `cad_parser.py` converts the current DXF sample into structured JSON.
- `blender_wall.py` converts that JSON into a Blender white model.
- The current sample CAD runs end to end and produces `.blend` / `.fbx` outputs.
- Window generation now avoids boolean wall cutting. It matches the wall thickness from the two end wall lines, then creates lower and upper wall boxes from the `H1` sill height and `H2` window height annotations.

The main open risk is generalization: the current implementation is proven on one CAD file, but not yet on multiple real CAD variants.

## Recommended Sequence

### Phase 1: Validate With More CAD Files

Before large refactors, run the complete flow against 2-3 additional CAD files.

For each CAD file, capture:

- Whether expected layers exist, such as `BS-墙`, `FF-门`, `BS-窗`, `BS-梁`, `SH-文字`.
- Whether walls, doors, windows, beams, and ceiling markers parse successfully.
- Whether windows can infer wall thickness from their two end wall lines.
- Whether doors match nearby wall geometry.
- Whether the generated Blender model is visually usable.
- Which elements are created through normal detection versus fallback logic.

Suggested commands:

```bash
python3 cad_parser.py input.dxf parsed_output.json
blender --background --python blender_wall.py -- parsed_output.json output_boolean_regions.blend
```

### Phase 2: Add a Validation Report

Add a repeatable report so every CAD test produces comparable output.

The report should summarize:

- Parsed entity counts.
- Built object counts.
- Fallback counts.
- Skipped objects and reasons.
- Window wall-thickness match count.
- Window lower/upper wall creation count.
- Door header creation count.
- Beam pairing/fallback count.
- Ceiling marker and region count.

Example report shape:

```text
walls: 82 parsed, 17 wall objects built
windows: 6 parsed, 6 wall-thickness matches, 11 lower/upper wall boxes built
doors: 5 parsed, 2 headers built, 3 skipped
beams: 18 parsed, 9 built
ceilings: 7 markers, 10 regions built
```

### Phase 3: Stabilize the Intermediate Schema

Once multiple CAD files have been tested, define a stable JSON schema for the intermediate representation.

This should clarify:

- Required versus optional fields.
- Units and coordinate conventions.
- How block-based and line-based CAD entities map into the same domain objects.
- How window fields are interpreted:
  - `opening_length`: horizontal opening length.
  - `frame_width`: CAD reference only, not the primary wall depth.
  - `sill_height`: lower wall height from `H1` annotations.
  - `window_height`: window opening height from `H2` annotations.
- How annotations attach to walls, doors, windows, beams, and ceiling regions.
- What fallback data is allowed when CAD information is incomplete.

### Phase 4: Refactor Along Proven Boundaries

Refactor after real failure modes are known.

Prefer boundaries based on responsibility:

- CAD parsing: DXF entities to intermediate JSON.
- Domain model: normalized walls, doors, windows, beams, ceilings.
- Geometry: line pairing, closed polygons, bounding boxes, snapping, difference regions.
- Blender building: mesh/object creation only.
- Validation/reporting: success/fallback/failure summaries.

Avoid splitting too early by visible component type alone, such as wall/window/door/beam/ceiling modules, because many failures cross those boundaries.

### Phase 5: Create Skills

Do not start with a full conversion skill.

First create a validation skill, for example `cad-to-3d-validate`, that guides an agent through:

- Running the parser.
- Running the Blender builder.
- Collecting logs.
- Comparing counts.
- Identifying fallbacks and skipped objects.
- Producing a validation report.

After 2-3 additional CAD files have been tested and failure modes are known, update the validation skill with those patterns.

Only after the pipeline is stable should a production conversion skill, such as `cad-to-3d-convert`, be created.
