#!/usr/bin/env python3
"""
JSON → Blender 3D 白模
窄闭合轮廓直接拉伸，平行边线合并成墙，落单线段 box 兜底。
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
    """找闭合多边形"""
    TOL = 10.0
    def key(x, y):
        return (round(x / TOL) * TOL, round(y / TOL) * TOL)

    adj = defaultdict(list)
    edges = []
    for i, w in enumerate(walls):
        sk = key(w["start"][0], w["start"][1])
        ek = key(w["end"][0], w["end"][1])
        adj[sk].append((ek, i))
        adj[ek].append((sk, i))
        edges.append((sk, ek, i))

    used_global = set()
    polygons = []

    def find_cycle(start, cur, path, used):
        if len(path) > 2 and cur == start:
            return path[:]
        for nxt, eidx in adj[cur]:
            if eidx in used:
                continue
            if nxt == start and len(path) > 2:
                return path + [nxt]
            used.add(eidx)
            result = find_cycle(start, nxt, path + [nxt], used)
            if result:
                return result
            used.discard(eidx)
        return None

    for sk, ek, eidx in edges:
        if eidx in used_global:
            continue
        used_global.add(eidx)
        cycle = find_cycle(sk, ek, [sk, ek], {eidx})
        if cycle:
            for k in range(len(cycle) - 1):
                for _, ei in adj[cycle[k]]:
                    if ei not in used_global:
                        for _, ei2 in adj[cycle[k + 1]]:
                            if ei == ei2:
                                used_global.add(ei)
            polygons.append(cycle)

    return polygons


def poly_area(poly):
    a = 0
    for k in range(len(poly) - 1):
        a += poly[k][0] * poly[k + 1][1] - poly[k + 1][0] * poly[k][1]
    return abs(a) / 2


def poly_perimeter(poly):
    return sum(
        math.hypot(poly[k + 1][0] - poly[k][0], poly[k + 1][1] - poly[k][1])
        for k in range(len(poly) - 1)
    )


def is_wall_outline(poly, max_thin_side=600, max_area=650000):
    """只把窄闭合轮廓当墙体，避免把房间/区域实心拉伸。"""
    area = poly_area(poly)
    perim = poly_perimeter(poly)
    if area <= 0 or perim <= 0:
        return False

    xs = [p[0] for p in poly[:-1]]
    ys = [p[1] for p in poly[:-1]]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    if bbox_w <= 0 or bbox_h <= 0:
        return False

    # 这里不用 avg_width 单独判定：大房间轮廓也可能因为周长很长而算出较小均宽。
    # 先按包围盒短边和面积收紧，只接受短墙、柱、墙垛这类窄闭合轮廓。
    return min(bbox_w, bbox_h) <= max_thin_side and area <= max_area


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


def pair_to_polygon(a, b):
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
    t0 -= cap_extend
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
        polys = [p for p in find_closed_polygons(walls) if is_wall_outline(p)]
        covered_keys = set()
        for poly in polys:
            covered_keys.update(polygon_line_keys(poly))

        remaining = []
        original_indices = []
        for i, w in enumerate(walls):
            if line_key_for_wall(w) not in covered_keys:
                remaining.append(w)
                original_indices.append(i)

        pairs, unpaired = pair_parallel_walls(remaining)

        fallback = [idx for idx in unpaired if should_create_fallback(remaining[idx])]
        skipped = len(unpaired) - len(fallback)

        print(f"{label}: {len(walls)}条线 → {len(polys)}个窄闭合轮廓 + {len(pairs)}组成对边线 + {len(fallback)}条兜底(跳过{skipped}条短辅助线)")

        # 1. 窄闭合轮廓拉伸
        for i, poly in enumerate(polys):
            if len(poly) < 3:
                continue
            obj = create_extruded_polygon(poly, avg_h, f"{label}_轮廓{i:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                total += 1

        # 2. 平行双边线 → 一堵墙
        for i, (a_idx, b_idx) in enumerate(pairs):
            poly = pair_to_polygon(make_segment(remaining[a_idx]), make_segment(remaining[b_idx]))
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
