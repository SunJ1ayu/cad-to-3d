#!/usr/bin/env python3
"""
JSON → Blender 3D 白模
闭合围合线直接按面积拉伸，围合线内的短辅助线忽略，未围合长边线再配对。
"""

import bpy
import bmesh
import json
import sys
import math
from collections import defaultdict


def load_json(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def find_closed_polygons(walls):
    """从墙线平面图中提取最小闭合面，避免复合大环重复建模。"""
    TOL = 10.0
    def key(x, y):
        return (round(x / TOL) * TOL, round(y / TOL) * TOL)

    def on_segment(p, a, b):
        cross = (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length == 0 or abs(cross) / length > TOL:
            return False
        return (
            min(a[0], b[0]) - TOL <= p[0] <= max(a[0], b[0]) + TOL
            and min(a[1], b[1]) - TOL <= p[1] <= max(a[1], b[1]) + TOL
        )

    raw_edges = []
    all_nodes = set()
    for w in walls:
        sk = key(w["start"][0], w["start"][1])
        ek = key(w["end"][0], w["end"][1])
        if sk == ek:
            continue
        raw_edges.append((sk, ek))
        all_nodes.add(sk)
        all_nodes.add(ek)

    adj = defaultdict(list)
    for sk, ek in raw_edges:
        edge_nodes = [p for p in all_nodes if on_segment(p, sk, ek)]
        edge_nodes.sort(key=lambda p: (p[0] - sk[0]) ** 2 + (p[1] - sk[1]) ** 2)
        for a, b in zip(edge_nodes, edge_nodes[1:]):
            if a == b:
                continue
            if b not in adj[a]:
                adj[a].append(b)
            if a not in adj[b]:
                adj[b].append(a)

    ordered = {}
    for node, neighbors in adj.items():
        ordered[node] = sorted(
            neighbors,
            key=lambda p: math.atan2(p[1] - node[1], p[0] - node[0]),
        )

    visited = set()
    polygons = []
    for start in ordered:
        for nxt in ordered[start]:
            if (start, nxt) in visited:
                continue
            face = []
            cur, target = start, nxt
            while (cur, target) not in visited:
                visited.add((cur, target))
                face.append(cur)
                neighbors = ordered[target]
                back_idx = neighbors.index(cur)
                cur, target = target, neighbors[(back_idx - 1) % len(neighbors)]

            if len(face) < 3:
                continue
            face.append(face[0])
            area = 0
            for k in range(len(face) - 1):
                area += face[k][0] * face[k + 1][1] - face[k + 1][0] * face[k][1]
            if area > 0:
                polygons.append(face)

    return polygons


def point_in_polygon(point, poly):
    x, y = point
    inside = False
    for i in range(len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        if (y1 > y) != (y2 > y):
            xinters = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xinters:
                inside = not inside
    return inside


def point_on_polygon_boundary(point, poly, tol=10.0):
    x, y = point
    for i in range(len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0 or abs(cross) / length > tol:
            continue
        if (
            min(x1, x2) - tol <= x <= max(x1, x2) + tol
            and min(y1, y2) - tol <= y <= max(y1, y2) + tol
        ):
            return True
    return False


def line_midpoint(w):
    return ((w["start"][0] + w["end"][0]) / 2, (w["start"][1] + w["end"][1]) / 2)


def line_key_for_wall(w, tol=10.0):
    def key(x, y):
        return (round(x / tol) * tol, round(y / tol) * tol)

    sk = key(w["start"][0], w["start"][1])
    ek = key(w["end"][0], w["end"][1])
    return (min(sk, ek), max(sk, ek))


def polygon_line_keys(poly):
    return {
        (min(poly[k], poly[k + 1]), max(poly[k], poly[k + 1]))
        for k in range(len(poly) - 1)
    }


def is_line_covered_by_polygons(w, edge_keys, polygons):
    if line_key_for_wall(w) in edge_keys:
        return True
    mid = line_midpoint(w)
    return any(point_in_polygon(mid, poly) or point_on_polygon_boundary(mid, poly) for poly in polygons)


def create_extruded_polygon(poly_keys, height, name, mat):
    """闭合多边形拉伸"""
    pts = [(k[0], k[1]) for k in poly_keys]
    n = len(pts)
    if n < 3:
        return None
    bm = bmesh.new()
    vb = [bm.verts.new((x, y, 0)) for x, y in pts]
    vt = [bm.verts.new((x, y, height)) for x, y in pts]
    bm.verts.ensure_lookup_table()
    bm.faces.new(vb)
    bm.faces.new(list(reversed(vt)))
    for k in range(n):
        kn = (k + 1) % n
        bm.faces.new([vb[k], vb[kn], vt[kn], vt[k]])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(mat)
    return obj


def make_segment(w):
    sx, sy = w["start"]
    ex, ey = w["end"]
    dx = ex - sx
    dy = ey - sy
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    ux = dx / length
    uy = dy / length
    return {
        "wall": w,
        "length": length,
        "dir": (ux, uy),
        "normal": (-uy, ux),
        "mid": ((sx + ex) / 2, (sy + ey) / 2),
        "points": [(sx, sy), (ex, ey)],
    }


def pair_to_polygon(a, b, blockers=None, extend_caps=True):
    blockers = blockers or []
    ux, uy = a["dir"]
    if ux * b["dir"][0] + uy * b["dir"][1] < 0:
        ux, uy = -ux, -uy

    nx, ny = -uy, ux
    points = a["points"] + b["points"]
    ts = [x * ux + y * uy for x, y in points]
    ds = [x * nx + y * ny for x, y in points]
    t0, t1 = max(min(p[0] * ux + p[1] * uy for p in a["points"]),
                 min(p[0] * ux + p[1] * uy for p in b["points"])), \
             min(max(p[0] * ux + p[1] * uy for p in a["points"]),
                 max(p[0] * ux + p[1] * uy for p in b["points"]))
    if t1 <= t0:
        t0, t1 = min(ts), max(ts)
    d0, d1 = min(ds), max(ds)
    if extend_caps:
        cap_extend = (d1 - d0) / 2
        mid_d = (d0 + d1) / 2
        start_probe = (ux * (t0 - cap_extend) + nx * mid_d, uy * (t0 - cap_extend) + ny * mid_d)
        end_probe = (ux * (t1 + cap_extend) + nx * mid_d, uy * (t1 + cap_extend) + ny * mid_d)
        if not any(point_in_polygon(start_probe, poly) for poly in blockers):
            t0 -= cap_extend
        if not any(point_in_polygon(end_probe, poly) for poly in blockers):
            t1 += cap_extend
    return [
        (ux * t0 + nx * d0, uy * t0 + ny * d0),
        (ux * t1 + nx * d0, uy * t1 + ny * d0),
        (ux * t1 + nx * d1, uy * t1 + ny * d1),
        (ux * t0 + nx * d1, uy * t0 + ny * d1),
        (ux * t0 + nx * d0, uy * t0 + ny * d0),
    ]


def pair_parallel_walls(walls, min_thickness=70, max_thickness=360):
    """把 CAD 双边线合成一堵墙，返回 pairs 和未配对索引。"""
    segs = [make_segment(w) for w in walls]
    candidates = []

    for i, a in enumerate(segs):
        if not a:
            continue
        for j in range(i + 1, len(segs)):
            b = segs[j]
            if not b:
                continue

            dot = abs(a["dir"][0] * b["dir"][0] + a["dir"][1] * b["dir"][1])
            if dot < 0.996:
                continue

            ux, uy = a["dir"]
            nx, ny = a["normal"]
            ai = sorted([p[0] * ux + p[1] * uy for p in a["points"]])
            bi = sorted([p[0] * ux + p[1] * uy for p in b["points"]])
            overlap = min(ai[1], bi[1]) - max(ai[0], bi[0])
            if overlap <= 0:
                continue
            if overlap < min(a["length"], b["length"]) * 0.55:
                continue

            distance = abs((b["mid"][0] - a["mid"][0]) * nx + (b["mid"][1] - a["mid"][1]) * ny)
            if not (min_thickness <= distance <= max_thickness):
                continue

            length_delta = abs(a["length"] - b["length"])
            candidates.append((distance + length_delta * 0.1 - overlap * 0.001, i, j))

    used = set()
    pairs = []
    for _, i, j in sorted(candidates):
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        pairs.append((i, j))

    return pairs, [i for i in range(len(walls)) if i not in used]


def should_create_fallback(w, min_length=1200):
    """短落单线多为端帽/填充辅助线，避免误生成柱状墙体。"""
    return w.get("length", 0) >= min_length


def create_wall_box(s, e, thickness, height, name, mat):
    """单条线段 → box"""
    dx = e[0] - s[0]
    dy = e[1] - s[1]
    ln = math.sqrt(dx**2 + dy**2)
    if ln == 0:
        return None
    nx = -dy / ln * thickness / 2
    ny = dx / ln * thickness / 2
    bm = bmesh.new()
    vb = [bm.verts.new((s[0]+nx, s[1]+ny, 0)), bm.verts.new((s[0]-nx, s[1]-ny, 0)),
          bm.verts.new((e[0]-nx, e[1]-ny, 0)), bm.verts.new((e[0]+nx, e[1]+ny, 0))]
    vt = [bm.verts.new((s[0]+nx, s[1]+ny, height)), bm.verts.new((s[0]-nx, s[1]-ny, height)),
          bm.verts.new((e[0]-nx, e[1]-ny, height)), bm.verts.new((e[0]+nx, e[1]+ny, height))]
    bm.verts.ensure_lookup_table()
    bm.faces.new(vb)
    bm.faces.new(list(reversed(vt)))
    for k in range(4):
        kn = (k + 1) % 4
        bm.faces.new([vb[k], vb[kn], vt[kn], vt[k]])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(mat)
    return obj


def create_oriented_box(center, axis_x, axis_y, size_x, size_y, size_z, name):
    cx, cy, cz = center
    ux, uy = axis_x
    vx, vy = axis_y
    hx = size_x / 2
    hy = size_y / 2
    hz = size_z / 2
    corners = []
    for z in (cz - hz, cz + hz):
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corners.append((
                cx + ux * hx * sx + vx * hy * sy,
                cy + uy * hx * sx + vy * hy * sy,
                z,
            ))

    faces = [
        (0, 1, 2, 3),
        (7, 6, 5, 4),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(corners, [], faces)
    mesh.update()
    return bpy.data.objects.new(name, mesh)


def object_bbox_xy(obj):
    bpy.context.view_layer.update()
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    xs = [v.x for v in coords]
    ys = [v.y for v in coords]
    return min(xs), min(ys), max(xs), max(ys)


def object_xy_coords(obj):
    bpy.context.view_layer.update()
    return [(co.x, co.y) for co in (obj.matrix_world @ v.co for v in obj.data.vertices)]


def bboxes_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def point_in_bbox_xy(point, bbox, tol=0.02):
    x, y = point
    return bbox[0] - tol <= x <= bbox[2] + tol and bbox[1] - tol <= y <= bbox[3] + tol


def create_window_infill_boxes(win, height, mat):
    length = win.get("opening_length")
    frame_width = win.get("frame_width") or 240
    sill = win.get("sill_height")
    window_height = win.get("window_height")
    if not length or sill is None or window_height is None:
        return []

    angle = math.radians(win.get("rotation", 0))
    axis_x = (math.cos(angle), math.sin(angle))
    axis_y = (-axis_x[1], axis_x[0])
    x, y = win["position"]
    top_z = sill + window_height
    specs = []
    if sill > 20:
        specs.append(("窗下墙", sill / 2, sill))
    if height - top_z > 20:
        specs.append(("窗上墙", top_z + (height - top_z) / 2, height - top_z))

    objects = []
    for label, center_z, box_height in specs:
        obj = create_oriented_box(
            (x, y, center_z),
            axis_x,
            axis_y,
            length,
            frame_width,
            box_height,
            label,
        )
        obj.data.materials.append(mat)
        objects.append(obj)
    return objects


def intervals_overlap(a0, a1, b0, b1, tol=0.03):
    return min(a1, b1) + tol >= max(a0, b0)


def snap_box_xy_to_walls(obj, wall_objects, tol=0.03, constrain_bbox=None):
    bpy.context.view_layer.update()
    bbox = object_bbox_xy(obj)
    x0, y0, x1, y1 = bbox
    targets = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    best = {key: tol for key in targets}
    wall_points = []

    for wall in wall_objects:
        wx0, wy0, wx1, wy1 = object_bbox_xy(wall)
        wall_points.extend(object_xy_coords(wall))
        x_candidates = {wx0, wx1}
        y_candidates = {wy0, wy1}
        for px, py in object_xy_coords(wall):
            if y0 - tol <= py <= y1 + tol:
                x_candidates.add(px)
            if x0 - tol <= px <= x1 + tol:
                y_candidates.add(py)
        for side, current, candidates, overlap in [
            ("x0", x0, x_candidates, intervals_overlap(y0, y1, wy0, wy1, tol)),
            ("x1", x1, x_candidates, intervals_overlap(y0, y1, wy0, wy1, tol)),
            ("y0", y0, y_candidates, intervals_overlap(x0, x1, wx0, wx1, tol)),
            ("y1", y1, y_candidates, intervals_overlap(x0, x1, wx0, wx1, tol)),
        ]:
            if not overlap:
                continue
            for candidate in candidates:
                dist = abs(current - candidate)
                if dist < best[side]:
                    best[side] = dist
                    targets[side] = candidate

    if constrain_bbox:
        cx0, cy0, cx1, cy1 = constrain_bbox
        targets["x0"] = max(targets["x0"], cx0)
        targets["y0"] = max(targets["y0"], cy0)
        targets["x1"] = min(targets["x1"], cx1)
        targets["y1"] = min(targets["y1"], cy1)

    sx = obj.scale.x if obj.scale.x else 1
    sy = obj.scale.y if obj.scale.y else 1
    for vert in obj.data.vertices:
        world_x = vert.co.x * sx
        world_y = vert.co.y * sy
        if abs(world_x - x0) < 0.001:
            vert.co.x = targets["x0"] / sx
        elif abs(world_x - x1) < 0.001:
            vert.co.x = targets["x1"] / sx
        if abs(world_y - y0) < 0.001:
            vert.co.y = targets["y0"] / sy
        elif abs(world_y - y1) < 0.001:
            vert.co.y = targets["y1"] / sy

    # Some CAD wall footprints have slight jogs, so a single bbox side can need
    # different snap targets at each corner.
    for vert in obj.data.vertices:
        world_x = vert.co.x * sx
        world_y = vert.co.y * sy
        nearby = [
            (px, py)
            for px, py in wall_points
            if abs(world_x - px) <= tol and abs(world_y - py) <= tol
        ]
        if not nearby:
            continue
        px, py = min(nearby, key=lambda p: (world_x - p[0]) ** 2 + (world_y - p[1]) ** 2)
        vert.co.x = px / sx
        vert.co.y = py / sy
    obj.data.update()


def bbox_from_xy_points(points, scale=1, margin=0):
    xs = [p[0] * scale for p in points]
    ys = [p[1] * scale for p in points]
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


def polygon_center(points):
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if not pts:
        return (0, 0)
    return (
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts),
    )


def bbox_center(bbox):
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def build_ceiling_resolver(data, fallback_height):
    markers = []
    for ann in data.get("annotations", []):
        heights = ann.get("parsed", {}).get("ceiling_height")
        pos = ann.get("position")
        if heights and pos:
            markers.append((pos[0], pos[1], round(heights[0])))

    def ceiling_at(point):
        if not markers:
            return fallback_height
        x, y = point
        return min(
            markers,
            key=lambda marker: (x - marker[0]) ** 2 + (y - marker[1]) ** 2,
        )[2]

    return ceiling_at, markers


def create_ceiling_drop_boxes(markers, wall_height, wall_objects, mat, collection, footprints=None):
    if not markers:
        return []

    def nearest_ceiling_height(point):
        x, y = point
        return min(
            markers,
            key=lambda marker: (x - marker[0]) ** 2 + (y - marker[1]) ** 2,
        )[2]

    objects = []
    if footprints:
        for i, footprint in enumerate(footprints):
            ceiling_height = nearest_ceiling_height(polygon_center(footprint))
            if ceiling_height >= wall_height:
                print(f"层高块{i}: 跳过，CH={ceiling_height}mm 已到统一墙高")
                continue
            size_z = wall_height - ceiling_height
            obj = create_extruded_polygon(footprint, size_z, f"层高块{i:03d}", mat)
            if not obj:
                continue
            for vert in obj.data.vertices:
                vert.co.z += ceiling_height
            obj.data.update()
            collection.objects.link(obj)
            obj.scale = (0.001, 0.001, 0.001)
            objects.append(obj)
            print(f"层高块{i}: CH={ceiling_height}mm, 补高{size_z}mm")
        if objects:
            return objects

    building_bbox = None
    for wall in wall_objects:
        bbox = object_bbox_xy(wall)
        if building_bbox is None:
            building_bbox = list(bbox)
        else:
            building_bbox[0] = min(building_bbox[0], bbox[0])
            building_bbox[1] = min(building_bbox[1], bbox[1])
            building_bbox[2] = max(building_bbox[2], bbox[2])
            building_bbox[3] = max(building_bbox[3], bbox[3])
    if building_bbox is None:
        return []

    xs = sorted({round(marker[0] * 0.001, 6) for marker in markers})
    ys = sorted({round(marker[1] * 0.001, 6) for marker in markers})

    def interval_bounds(values, value, lower, upper):
        idx = values.index(round(value, 6))
        start = lower if idx == 0 else (values[idx - 1] + value) / 2
        end = upper if idx == len(values) - 1 else (value + values[idx + 1]) / 2
        return start, end

    for i, (x_mm, y_mm, ceiling_height) in enumerate(markers):
        if ceiling_height >= wall_height:
            print(f"层高块{i}: 跳过，CH={ceiling_height}mm 已到统一墙高")
            continue

        x = x_mm * 0.001
        y = y_mm * 0.001
        x0, x1 = interval_bounds(xs, x, building_bbox[0], building_bbox[2])
        y0, y1 = interval_bounds(ys, y, building_bbox[1], building_bbox[3])
        size_x = max((x1 - x0) * 1000, 1)
        size_y = max((y1 - y0) * 1000, 1)
        size_z = wall_height - ceiling_height
        obj = create_oriented_box(
            ((x0 + x1) * 500, (y0 + y1) * 500, ceiling_height + size_z / 2),
            (1, 0),
            (0, 1),
            size_x,
            size_y,
            size_z,
            f"层高块{i:03d}",
        )
        obj.data.materials.append(mat)
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        objects.append(obj)
        print(f"层高块{i}: CH={ceiling_height}mm, 补高{size_z}mm")

    return objects


def ceiling_footprints_from_walls_and_beams(data):
    beam_lines = [
        beam
        for beam in data.get("beams", [])
        if beam.get("type") == "beam" and beam.get("start") and beam.get("end")
    ]
    beam_lines = merge_collinear_beam_lines(beam_lines)
    wall_polygons = []
    for walls in [
        [w for w in data.get("walls", []) if not w.get("demolishable")],
        [w for w in data.get("walls", []) if w.get("demolishable")],
    ]:
        wall_polygons.extend(find_closed_polygons(walls))

    beam_polygons = []
    beam_pairs, _ = pair_parallel_walls(beam_lines, min_thickness=30, max_thickness=600)
    for a_idx, b_idx in beam_pairs:
        beam_polygons.append(
            pair_to_polygon(
                make_segment(beam_lines[a_idx]),
                make_segment(beam_lines[b_idx]),
                extend_caps=False,
            )
        )

    blocked_polygons = wall_polygons + beam_polygons
    edges = data.get("walls", []) + beam_lines
    polygons = find_closed_polygons(edges)
    return [
        poly for poly in polygons
        if len(poly) >= 4
        and not any(point_in_polygon(polygon_center(poly), blocked) for blocked in blocked_polygons)
    ]


def merge_collinear_beam_lines(beams, coord_tol=5, gap_tol=260):
    groups = defaultdict(list)
    passthrough = []
    for beam in beams:
        if not beam.get("start") or not beam.get("end"):
            passthrough.append(beam)
            continue
        sx, sy = beam["start"]
        ex, ey = beam["end"]
        if abs(sx - ex) <= coord_tol:
            groups[("v", round(((sx + ex) / 2) / coord_tol))].append(beam)
        elif abs(sy - ey) <= coord_tol:
            groups[("h", round(((sy + ey) / 2) / coord_tol))].append(beam)
        else:
            passthrough.append(beam)

    merged = list(passthrough)
    for (axis, _), items in groups.items():
        spans = []
        for beam in items:
            sx, sy = beam["start"]
            ex, ey = beam["end"]
            const = (sx + ex) / 2 if axis == "v" else (sy + ey) / 2
            a, b = (sy, ey) if axis == "v" else (sx, ex)
            spans.append({
                "beam": beam,
                "const": const,
                "start": min(a, b),
                "end": max(a, b),
            })
        spans.sort(key=lambda item: item["start"])

        current = []
        for span in spans:
            if not current or span["start"] - max(item["end"] for item in current) <= gap_tol:
                current.append(span)
                continue
            merged.append(_merge_beam_span(axis, current))
            current = [span]
        if current:
            merged.append(_merge_beam_span(axis, current))
    return merged


def _merge_beam_span(axis, spans):
    start = min(span["start"] for span in spans)
    end = max(span["end"] for span in spans)
    const = sum(span["const"] for span in spans) / len(spans)
    source_beams = [span["beam"] for span in spans]
    heights = [
        beam.get("beam_height")
        for beam in source_beams
        if beam.get("beam_height") is not None and beam.get("beam_height") > 0
    ]
    widths = [
        beam.get("beam_width")
        for beam in source_beams
        if beam.get("beam_width") is not None and beam.get("beam_width") > 0
    ]
    annotations = []
    for beam in source_beams:
        annotations.extend(beam.get("annotations", []))

    if axis == "v":
        start_point = [round(const, 1), round(start, 1)]
        end_point = [round(const, 1), round(end, 1)]
    else:
        start_point = [round(start, 1), round(const, 1)]
        end_point = [round(end, 1), round(const, 1)]

    return {
        "type": "beam",
        "layer": "BS-梁",
        "start": start_point,
        "end": end_point,
        "length": round(end - start, 1),
        "beam_height": min(heights) if heights else None,
        "beam_width": max(widths) if widths else None,
        "annotations": annotations,
    }


def apply_window_openings(wall_objects, windows, collection, wall_height, mat):
    cutters = []
    applied = 0
    infill_objects = []
    for i, win in enumerate(windows):
        length = win.get("opening_length")
        frame_width = win.get("frame_width") or 240
        sill = win.get("sill_height")
        window_height = win.get("window_height")
        if not length or sill is None or window_height is None or window_height <= 0:
            print(f"窗洞{i}: 跳过，缺少尺寸/高度")
            continue

        angle = math.radians(win.get("rotation", 0))
        axis_x = (math.cos(angle), math.sin(angle))
        axis_y = (-axis_x[1], axis_x[0])
        x, y = win["position"]
        cutter = create_oriented_box(
            (x, y, sill + window_height / 2),
            axis_x,
            axis_y,
            length + 80,
            frame_width + 700,
            window_height + 40,
            f"窗洞切割_{i:03d}",
        )
        collection.objects.link(cutter)
        cutter.scale = (0.001, 0.001, 0.001)
        cutter_bbox = object_bbox_xy(cutter)

        hit = 0
        for obj in wall_objects:
            wall_bbox = object_bbox_xy(obj)
            if not point_in_bbox_xy((x * 0.001, y * 0.001), wall_bbox):
                continue
            if not bboxes_overlap(wall_bbox, cutter_bbox):
                continue
            modifier = obj.modifiers.new(f"窗洞_{i:03d}", "BOOLEAN")
            modifier.operation = "DIFFERENCE"
            modifier.object = cutter
            try:
                modifier.solver = "EXACT"
            except Exception:
                pass
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            obj.select_set(False)
            hit += 1

        cutters.append(cutter)
        applied += hit
        print(f"窗洞{i}: 长{length}mm, 窗台{sill}mm, 窗高{window_height}mm, 命中{hit}个墙体")
        if hit == 0:
            boxes = create_window_infill_boxes(win, wall_height, mat)
            for j, box in enumerate(boxes):
                box.name = f"窗洞{i:03d}_{box.name}_{j:03d}"
                collection.objects.link(box)
                box.scale = (0.001, 0.001, 0.001)
                snap_box_xy_to_walls(box, wall_objects)
                infill_objects.append(box)
            print(f"窗洞{i}: 未命中墙体，补{len(boxes)}块窗上下墙")

    for cutter in cutters:
        bpy.data.objects.remove(cutter, do_unlink=True)
    return applied, infill_objects


def create_door_header_boxes(doors, wall_height, mat, collection):
    objects = []
    for i, door in enumerate(doors):
        door_height = door.get("door_height")
        bbox = door.get("bbox")
        if door_height is None or not bbox:
            print(f"门洞{i}: 跳过，缺少门高或bbox")
            continue

        header_height = wall_height - door_height
        if header_height <= 20:
            print(f"门洞{i}: 跳过，门高已到顶")
            continue

        x0, y0, x1, y1 = bbox
        sx = x1 - x0
        sy = y1 - y0
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        cz = door_height + header_height / 2

        if sx >= sy:
            axis_x = (1, 0)
            axis_y = (0, 1)
            size_x = sx + 20
            size_y = sy
        else:
            axis_x = (0, 1)
            axis_y = (1, 0)
            size_x = sy + 20
            size_y = sx

        obj = create_oriented_box(
            (cx, cy, cz),
            axis_x,
            axis_y,
            size_x,
            size_y,
            header_height,
            f"门洞{i:03d}_门头墙",
        )
        obj.data.materials.append(mat)
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        snap_box_xy_to_walls(obj, [wall for wall in bpy.data.objects if wall.name.startswith(("承重墙", "可拆墙"))])
        objects.append(obj)
        print(f"门洞{i}: 门高{door_height}mm, 补门头墙{header_height:.0f}mm")
    return objects


def create_beam_objects(beams, wall_height, mat, collection, wall_objects):
    objects = []

    line_beams = [
        (i, beam)
        for i, beam in enumerate(merge_collinear_beam_lines(beams))
        if beam.get("type") == "beam" and beam.get("start") and beam.get("end")
    ]
    beam_lines = [beam for _, beam in line_beams]
    pairs, unpaired = pair_parallel_walls(beam_lines, min_thickness=30, max_thickness=600)
    used_line_indices = set()

    def distance_point_to_line(point, line):
        px, py = point
        x1, y1 = line["start"]
        x2, y2 = line["end"]
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        nx = x1 + t * dx
        ny = y1 + t * dy
        return math.hypot(px - nx, py - ny)

    def distance_between_lines(a, b):
        points_a = [a["start"], a["end"]]
        points_b = [b["start"], b["end"]]
        return min(
            min(distance_point_to_line(p, b) for p in points_a),
            min(distance_point_to_line(p, a) for p in points_b),
        )

    def beam_bottom_for(items):
        bottoms = [
            item.get("beam_height")
            for item in items
            if item.get("beam_height") is not None and item.get("beam_height") > 0
        ]
        if bottoms:
            return min(bottoms)

        connected = []
        nearby = []
        for candidate in beam_lines:
            height = candidate.get("beam_height")
            if height is None or height <= 0:
                continue
            distances = [distance_between_lines(item, candidate) for item in items]
            if any(distance <= 20 for distance in distances):
                connected.append(height)
            elif any(distance <= 260 for distance in distances):
                nearby.append(height)
        if connected:
            return min(connected)
        return min(nearby) if nearby else None

    def beam_width_for(items):
        widths = [
            item.get("beam_width")
            for item in items
            if item.get("beam_width") is not None and item.get("beam_width") > 0
        ]
        return max(widths) if widths else None

    def add_beam_object(name, footprint, beam_bottom, beam_width):
        if beam_bottom is None or beam_bottom <= 0:
            print(f"{name}: 跳过，缺少梁底标高")
            return None

        beam_depth = wall_height - beam_bottom
        if beam_depth <= 20:
            print(f"{name}: 跳过，梁底标高{beam_bottom}mm已到顶")
            return None

        obj = create_extruded_polygon(footprint, beam_depth, name, mat)
        if not obj:
            print(f"{name}: 跳过，缺少可建模几何")
            return None

        for vert in obj.data.vertices:
            vert.co.z += beam_bottom
        obj.data.update()
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        snap_box_xy_to_walls(
            obj,
            wall_objects,
            tol=0.06,
            constrain_bbox=bbox_from_xy_points(footprint[:-1], scale=0.001, margin=0.06),
        )
        if objects:
            snap_box_xy_to_walls(
                obj,
                objects,
                tol=0.06,
                constrain_bbox=bbox_from_xy_points(footprint[:-1], scale=0.001, margin=0.06),
            )
        objects.append(obj)
        width_label = f", 梁宽{beam_width}mm" if beam_width else ""
        print(f"{name}: 梁底标高{beam_bottom}mm, 梁厚{beam_depth:.0f}mm{width_label}")
        return obj

    for pair_index, (a_idx, b_idx) in enumerate(pairs):
        used_line_indices.update((a_idx, b_idx))
        _, a = line_beams[a_idx]
        _, b = line_beams[b_idx]
        footprint = pair_to_polygon(make_segment(a), make_segment(b), extend_caps=False)
        beam_bottom = beam_bottom_for([a, b])
        beam_width = beam_width_for([a, b])
        add_beam_object(f"梁{pair_index:03d}", footprint, beam_bottom, beam_width)

    skipped_unpaired = 0
    for line_idx in unpaired:
        if line_idx in used_line_indices:
            continue
        original_idx, beam = line_beams[line_idx]
        if beam.get("beam_height") is None:
            skipped_unpaired += 1
            print(f"梁{original_idx}: 跳过，未配对且缺少梁底标高")
            continue
        skipped_unpaired += 1
        print(f"梁{original_idx}: 跳过，未找到梁边界配对")

    if skipped_unpaired:
        print(f"梁线配对: 跳过{skipped_unpaired}条未配对梁线")

    for i, beam in enumerate(beams):
        if beam.get("type") != "beam" or not beam.get("polyline"):
            continue

        beam_bottom = beam.get("beam_height")
        beam_width = beam.get("beam_width") or 240
        pts = [(p[0], p[1]) for p in beam["polyline"]]
        if len(pts) < 3:
            print(f"梁{i}: 跳过，polyline 点数不足")
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        add_beam_object(f"梁{i:03d}", pts, beam_bottom, beam_width)

    return objects


def setup_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for obj in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def main():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    if not argv:
        print("用法: blender --background --python blender_wall.py -- <input.json> [output.blend]")
        sys.exit(1)

    json_path = argv[0]
    blend_path = argv[1] if len(argv) > 1 else json_path.rsplit(".", 1)[0] + "_walls.blend"

    print(f"加载: {json_path}")
    data = load_json(json_path)
    setup_scene()

    mat_model = bpy.data.materials.new("白模")
    mat_model.diffuse_color = (0.65, 0.65, 0.65, 1.0)

    heights = data.get("ceiling_heights", [2800])
    avg_h = round(max(heights)) + 100
    ceiling_at, ceiling_markers = build_ceiling_resolver(data, avg_h)
    print(f"统一墙高: {avg_h}mm，局部层高标注: {len(ceiling_markers)}个")

    collection = bpy.data.collections.new("建筑构件")
    bpy.context.scene.collection.children.link(collection)
    total = 0
    wall_objects = []

    for walls, mat, label, thick in [
        ([w for w in data["walls"] if not w.get("demolishable")], mat_model, "承重墙", 240),
        ([w for w in data["walls"] if w.get("demolishable")], mat_model, "可拆墙", 100),
    ]:
        polys = find_closed_polygons(walls)
        covered_keys = set()
        for poly in polys:
            covered_keys.update(polygon_line_keys(poly))

        remaining = []
        original_indices = []
        for i, w in enumerate(walls):
            if not is_line_covered_by_polygons(w, covered_keys, polys):
                remaining.append(w)
                original_indices.append(i)

        pairs, unpaired = pair_parallel_walls(remaining)

        fallback = [idx for idx in unpaired if should_create_fallback(remaining[idx])]
        skipped = len(unpaired) - len(fallback)

        print(f"{label}: {len(walls)}条线 → {len(polys)}个围合面 + {len(pairs)}组成对边线 + {len(fallback)}条兜底(跳过{skipped}条短辅助线)")

        # 1. 闭合围合面直接拉伸
        for i, poly in enumerate(polys):
            if len(poly) < 3:
                continue
            obj = create_extruded_polygon(poly, avg_h, f"{label}_围合{i:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                wall_objects.append(obj)
                total += 1

        # 2. 平行双边线 → 一堵墙
        for i, (a_idx, b_idx) in enumerate(pairs):
            poly = pair_to_polygon(make_segment(remaining[a_idx]), make_segment(remaining[b_idx]), polys)
            obj = create_extruded_polygon(poly, avg_h, f"{label}_配对{i:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                wall_objects.append(obj)
                total += 1

        # 3. 落单线段 → box 兜底
        for idx in fallback:
            w = remaining[idx]
            original_idx = original_indices[idx]
            obj = create_wall_box(w["start"], w["end"], thick, avg_h, f"{label}_兜底_{original_idx:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                wall_objects.append(obj)
                total += 1

    window_hits, window_infill = apply_window_openings(
        wall_objects,
        data.get("windows", []),
        collection,
        avg_h,
        mat_model,
    )
    total += len(window_infill)
    print(f"窗洞布尔: 命中{window_hits}次，补窗上下墙{len(window_infill)}块")

    door_headers = create_door_header_boxes(
        data.get("doors", []),
        avg_h,
        mat_model,
        collection,
    )
    total += len(door_headers)
    print(f"门洞门头墙: 补{len(door_headers)}块")

    beam_objects = create_beam_objects(
        data.get("beams", []),
        avg_h,
        mat_model,
        collection,
        wall_objects,
    )
    total += len(beam_objects)
    print(f"梁: 建模{len(beam_objects)}块")

    ceiling_drop_objects = []
    print("层高块: 暂停生成")

    # 缩放 mm→m
    bpy.ops.object.select_all(action='SELECT')
    if bpy.context.selected_objects:
        bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
        bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.select_all(action='DESELECT')

    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"保存: {blend_path}")
    print(f"总共: {total}个对象")


if __name__ == "__main__":
    main()
