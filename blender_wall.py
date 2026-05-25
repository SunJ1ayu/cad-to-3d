#!/usr/bin/env python3
"""
JSON → Blender 3D 白模（v6 混合版）
闭合多边形拉伸 + 未覆盖线段 box 补全，不漏不错。
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

        # 记录被多边形覆盖的线段索引
        TOL = 10.0
        def key(x, y):
            return (round(x / TOL) * TOL, round(y / TOL) * TOL)

        poly_line_keys = set()
        for poly in polys:
            for k in range(len(poly)):
                a = poly[k]
                b = poly[(k + 1) % len(poly)]
                poly_line_keys.add((min(a, b), max(a, b)))

        # 找出未被覆盖的线段
        uncovered = []
        for i, w in enumerate(walls):
            sk = key(w["start"][0], w["start"][1])
            ek = key(w["end"][0], w["end"][1])
            line_key = (min(sk, ek), max(sk, ek))
            if line_key not in poly_line_keys:
                uncovered.append(i)

        # 去掉外围多边形（面积最大的）
        def poly_area(p):
            a = 0
            for k in range(len(p) - 1):
                a += p[k][0] * p[k+1][1] - p[k+1][0] * p[k][1]
            return abs(a) / 2

        if len(polys) > 1:
            max_area = max(poly_area(p) for p in polys)
            inner_polys = [p for p in polys if poly_area(p) < max_area * 0.9]
        else:
            inner_polys = polys

        print(f"{label}: {len(walls)}条线 → {len(polys)}个多边形(去掉外围剩{len(inner_polys)}个) + {len(uncovered)}条未覆盖")

        # 1. 多边形拉伸
        for i, poly in enumerate(inner_polys):
            if len(poly) < 3:
                continue
            obj = create_extruded_polygon(poly, avg_h, f"{label}_房间{i:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                total += 1

        # 2. 未覆盖线段 → box 补全
        for idx in uncovered:
            w = walls[idx]
            obj = create_wall_box(w["start"], w["end"], thick, avg_h, f"{label}_补_{idx:03d}", mat)
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
