# CAD to 3D MVP Roadmap

## Current State

The project has reached a minimal MVP:

- `cad_parser.py` converts the current DXF sample into structured JSON.
- `blender_wall.py` converts that JSON into a Blender white model.
- The current sample CAD runs end to end and produces `.blend` / `.fbx` outputs.
- Window generation now avoids boolean wall cutting. It matches the wall thickness from the two end wall lines, then creates lower and upper wall boxes from the `H1` sill height and `H2` window height annotations.

The main open risk is generalization: the current implementation is proven on one CAD file, but not yet on multiple real CAD variants.

## Latest Progress

As of 2026-05-28:

### Beam 标注传播（已完成）

- 新增 `_propagate_beam_annotations()` 函数，在标注关联后做两步传播：
  1. **链式传播**：端点相连（<50mm）的梁组成链，链内 beam_height/beam_width 互相传播
  2. **平行推断**：链内仍缺失时，找平行且距离 200-600mm 的已标注梁继承
- 效果：beam_height 覆盖率 56% → 100%，beam_width 56% → 94%，Blender 梁建模 9 → 18 块

### 2点 Polyline 修复（已完成）

- 2点 LWPOLYLINE（多段线）现在在 parser 阶段转为 LINE 处理，不再被 blender 跳过
- 效果：+1 根梁（18 → 19 块）

### BS-柱 围合过滤（待做）

- BS-柱 围合内部的管线等非结构构件应该被忽略
- 当前问题：围合005/007 是 BS-柱 短线围出的小三角，不应算独立墙体区域

### 端点找墙补边 — 异形低区梁识别（下一步重点）

**问题**：梁线被墙体遮挡后不再闭合，但语义上仍是完整低区。当前逻辑要求梁线自身闭合才能建模，导致异形低区梁被拆断。

**核心思路**：墙体本身就是梁区边界的一部分。梁线不闭合没关系，只要它能和 BS-柱 墙体边界共同闭合，就可以生成低区。

**第一步实现方案**（增量改动，不重构）：

1. 梁线端点延长到墙边：端点离 BS-柱 墙体边界 < 300mm 时，延长至墙边
2. 借用墙边闭合：梁线 + 墙边界共同围合 → 生成低区 polygon
3. 同高合并：多个低区 polygon 如果 H 标注相同且边界相连/相邻 → 合并为一个低区
4. 容差设置：
   - `snap_tolerance` = 5~20mm（端点吸墙）
   - `extend_tolerance` = 50~300mm（梁线延长找墙）
   - `gap_close_tolerance` = 10~50mm（小缝闭合）

**后续方向**（等更多 CAD 文件验证后再做）：

- 统一 planar graph polygonize：所有墙边 + 梁线 + 外轮廓一起 polygonize，不再单独处理梁
- 三层识别：几何层（线和面）→ 语义层（H/W/CH 标注赋值）→ 修正层（补边、合并碎面）

**设计原则**：

- 宁可多一两根独立梁（用户手动合并快），也不要脚本猜错把不同高的梁混在一起
- 闭合多边形 = 同高，直接合并（零歧义）
- 不闭合 + 能借墙闭合 + 同高标注 → 补边后合并
- 不闭合 + 无法借墙 → 保持独立

Current verified generation summary (v6):

```text
walls: 133 parsed, 27 wall regions built
windows: 9 parsed, 9 wall-thickness matches, 18 lower/upper wall boxes built
doors: 1 parsed, 1 header built
beams: 38 parsed, 19 built
ceilings: 12 markers, 13 regions built
total Blender objects: 78
```

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
