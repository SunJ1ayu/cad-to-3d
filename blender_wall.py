#!/usr/bin/env python3
"""
JSON → Blender 3D 白模（v5 稳定版）
逐段 box 生成墙体，不做多边形检测，不做平行线配对。
每条墙线 = 一个 box，墙厚统一。
"""

import bpy
import bmesh
import json
import sys
import math


def load_json(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def create_wall_box(s, e, thickness, height, name, mat):
    """单条墙线段 → box"""
    dx = e[0] - s[0]
    dy = e[1] - s[1]
    ln = math.sqrt(dx**2 + dy**2)
    if ln == 0:
        return None
    nx = -dy / ln * thickness / 2
    ny = dx / ln * thickness / 2

    bm = bmesh.new()
    vb = [
        bm.verts.new((s[0]+nx, s[1]+ny, 0)),
        bm.verts.new((s[0]-nx, s[1]-ny, 0)),
        bm.verts.new((e[0]-nx, e[1]-ny, 0)),
        bm.verts.new((e[0]+nx, e[1]+ny, 0)),
    ]
    vt = [
        bm.verts.new((s[0]+nx, s[1]+ny, height)),
        bm.verts.new((s[0]-nx, s[1]-ny, height)),
        bm.verts.new((e[0]-nx, e[1]-ny, height)),
        bm.verts.new((e[0]+nx, e[1]+ny, height)),
    ]
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

    # 承重墙 240mm，可拆墙 100mm
    for walls, mat, label, thick in [
        ([w for w in data["walls"] if not w.get("demolishable")], mat_struct, "承重墙", 240),
        ([w for w in data["walls"] if w.get("demolishable")], mat_demo, "可拆墙", 100),
    ]:
        print(f"{label}: {len(walls)}条线, 墙厚={thick}mm")
        for i, w in enumerate(walls):
            obj = create_wall_box(w["start"], w["end"], thick, avg_h, f"{label}_{i:03d}", mat)
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
    print(f"总共: {total}个墙体")


if __name__ == "__main__":
    main()
