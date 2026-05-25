#!/usr/bin/env python3
"""
JSON → Blender 3D 白模（v3）
回到第一版：闭合多边形检测 + 统一墙厚拉伸
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
    """从墙线段中找闭合多边形"""
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
                        for _, ei2 in adj[cycle[k+1]]:
                            if ei == ei2:
                                used_global.add(ei)
            polygons.append(cycle)
    return polygons


def create_extruded_polygon(polygon_keys, thickness, height, name, mat):
    """从闭合多边形拉伸成墙体"""
    pts = [(k[0], k[1]) for k in polygon_keys]
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
    mat_door = bpy.data.materials.new("门")
    mat_door.diffuse_color = (0.8, 0.5, 0.2, 1.0)
    mat_win = bpy.data.materials.new("窗")
    mat_win.diffuse_color = (0.3, 0.5, 0.8, 1.0)

    heights = data.get("ceiling_heights", [2800])
    avg_h = sum(heights) / len(heights)
    print(f"层高: {avg_h:.0f}mm")

    collection = bpy.data.collections.new("建筑构件")
    bpy.context.scene.collection.children.link(collection)
    total = 0

    # ================================================================
    # 1. 墙体 - 闭合多边形拉伸
    # ================================================================
    for walls, mat, label in [
        ([w for w in data["walls"] if not w.get("demolishable")], mat_struct, "承重墙"),
        ([w for w in data["walls"] if w.get("demolishable")], mat_demo, "可拆墙"),
    ]:
        polys = find_closed_polygons(walls)
        print(f"{label}: {len(walls)}条线 → {len(polys)}个多边形")

        # 去掉外边界：找包含所有其他多边形的那个
        def point_in_poly(pt, poly):
            """射线法判断点是否在多边形内"""
            x, y = pt
            inside = False
            n = len(poly)
            for i in range(n):
                j = (i + 1) % n
                xi, yi = poly[i]
                xj, yj = poly[j]
                if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                    inside = not inside
            return inside

        def poly_center(p):
            xs = [k[0] for k in p]
            ys = [k[1] for k in p]
            return (sum(xs)/len(xs), sum(ys)/len(ys))

        outer_idx = -1
        for i, pi in enumerate(polys):
            contains_all = True
            for j, pj in enumerate(polys):
                if i == j:
                    continue
                cx, cy = poly_center(pj)
                if not point_in_poly((cx, cy), pi):
                    contains_all = False
                    break
            if contains_all:
                outer_idx = i
                break

        if outer_idx >= 0:
            print(f"  去掉外围多边形{outer_idx}")
            polys = [p for i, p in enumerate(polys) if i != outer_idx]

        for i, poly in enumerate(polys):
            if len(poly) < 3:
                continue
            obj = create_extruded_polygon(poly, 240, avg_h, f"{label}_{i:03d}", mat)
            if obj:
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                total += 1

    # 门、窗暂不生成，先验证墙体

    # ================================================================
    # 缩放 mm→m（不归零，保留 CAD 原点）
    # ================================================================
    bpy.ops.object.select_all(action='SELECT')
    for obj in bpy.context.selected_objects:
        if obj.type == 'MESH':
            obj.scale = (0.001, 0.001, 0.001)
    if bpy.context.selected_objects:
        bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
        bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.select_all(action='DESELECT')

    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"\n保存: {blend_path}")
    print(f"总共: {total}个对象")


if __name__ == "__main__":
    main()
