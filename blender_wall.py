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


def pair_to_polygon(a, b, blockers=None):
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

    mat_struct = bpy.data.materials.new("承重墙")
    mat_struct.diffuse_color = (0.4, 0.4, 0.4, 1.0)
    mat_demo = bpy.data.materials.new("可拆墙")
    mat_demo.diffuse_color = (0.7, 0.7, 0.7, 1.0)

    heights = data.get("ceiling_heights", [2800])
    avg_h = sum(heights) / len(heights)
    print(f"层高: {avg_h:.0f}mm")

    collection = bpy.data.collections.new("建筑构件")
    bpy.context.scene.collection.children.link(collection)
    total = 0

    for walls, mat, label, thick in [
        ([w for w in data["walls"] if not w.get("demolishable")], mat_struct, "承重墙", 240),
        ([w for w in data["walls"] if w.get("demolishable")], mat_demo, "可拆墙", 100),
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
                total += 1

        # 2. 平行双边线 → 一堵墙
        for i, (a_idx, b_idx) in enumerate(pairs):
            poly = pair_to_polygon(make_segment(remaining[a_idx]), make_segment(remaining[b_idx]), polys)
            obj = create_extruded_polygon(poly, avg_h, f"{label}_配对{i:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                total += 1

        # 3. 落单线段 → box 兜底
        for idx in fallback:
            w = remaining[idx]
            original_idx = original_indices[idx]
            obj = create_wall_box(w["start"], w["end"], thick, avg_h, f"{label}_兜底_{original_idx:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                total += 1

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
