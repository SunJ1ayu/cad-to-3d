#!/usr/bin/env python3
"""
JSON → Blender 3D 白模（v4）
平行线对生成墙体：每条线找最近的平行伙伴，一对一配对，不重复。
"""

import bpy
import bmesh
import json
import sys
import math


def load_json(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def find_wall_pairs(walls, max_dist=350, min_length=100):
    """一对一配对平行线，每条线只用一次"""
    n = len(walls)
    # 计算所有线的角度和长度
    info = []
    for w in walls:
        dx = w["end"][0] - w["start"][0]
        dy = w["end"][1] - w["start"][1]
        ln = math.sqrt(dx**2 + dy**2)
        angle = math.atan2(dy, dx) if ln > 0 else 0
        info.append({"dx": dx, "dy": dy, "len": ln, "angle": angle})

    # 找所有候选配对
    candidates = []
    for i in range(n):
        if info[i]["len"] < min_length:
            continue
        for j in range(i+1, n):
            if info[j]["len"] < min_length:
                continue
            # 检查平行
            diff = abs(info[i]["angle"] - info[j]["angle"]) % math.pi
            if diff > 0.15 and diff < math.pi - 0.15:
                continue
            # 计算距离
            mx = (walls[i]["start"][0] + walls[i]["end"][0]) / 2
            my = (walls[i]["start"][1] + walls[i]["end"][1]) / 2
            px = mx - walls[j]["start"][0]
            py = my - walls[j]["start"][1]
            dx2 = info[j]["dx"]
            dy2 = info[j]["dy"]
            ln2 = info[j]["len"]
            dist = abs(py * dx2 - px * dy2) / ln2
            if 50 < dist < max_dist:
                candidates.append((dist, i, j))

    # 按距离排序，贪心配对
    candidates.sort()
    used = set()
    pairs = []
    for dist, i, j in candidates:
        if i in used or j in used:
            continue
        pairs.append((i, j, round(dist)))
        used.add(i)
        used.add(j)

    return pairs, used


def create_wall_from_pair(wa, wb, height, name, mat):
    """从两条平行线创建墙体"""
    a1, a2 = wa["start"], wa["end"]
    b1, b2 = wb["start"], wb["end"]

    # 对齐端点
    d11 = (a1[0]-b1[0])**2 + (a1[1]-b1[1])**2
    d12 = (a1[0]-b2[0])**2 + (a1[1]-b2[1])**2
    if d12 < d11:
        b1, b2 = b2, b1

    bm = bmesh.new()
    vb = [bm.verts.new((a1[0],a1[1],0)), bm.verts.new((b1[0],b1[1],0)),
          bm.verts.new((b2[0],b2[1],0)), bm.verts.new((a2[0],a2[1],0))]
    vt = [bm.verts.new((a1[0],a1[1],height)), bm.verts.new((b1[0],b1[1],height)),
          bm.verts.new((b2[0],b2[1],height)), bm.verts.new((a2[0],a2[1],height))]
    bm.verts.ensure_lookup_table()
    bm.faces.new(vb)
    bm.faces.new(list(reversed(vt)))
    for k in range(4):
        kn = (k+1) % 4
        bm.faces.new([vb[k], vb[kn], vt[kn], vt[k]])

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(mat)
    return obj


def create_single_wall(w, thickness, height, name, mat):
    """单条线用默认厚度生成box"""
    s, e = w["start"], w["end"]
    dx = e[0] - s[0]
    dy = e[1] - s[1]
    ln = math.sqrt(dx**2 + dy**2)
    if ln == 0:
        return None
    nx = -dy/ln * thickness/2
    ny = dx/ln * thickness/2

    bm = bmesh.new()
    vb = [bm.verts.new((s[0]+nx,s[1]+ny,0)), bm.verts.new((s[0]-nx,s[1]-ny,0)),
          bm.verts.new((e[0]-nx,e[1]-ny,0)), bm.verts.new((e[0]+nx,e[1]+ny,0))]
    vt = [bm.verts.new((s[0]+nx,s[1]+ny,height)), bm.verts.new((s[0]-nx,s[1]-ny,height)),
          bm.verts.new((e[0]-nx,e[1]-ny,height)), bm.verts.new((e[0]+nx,e[1]+ny,height))]
    bm.verts.ensure_lookup_table()
    bm.faces.new(vb)
    bm.faces.new(list(reversed(vt)))
    for k in range(4):
        kn = (k+1)%4
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

    for walls, mat, label, default_thick in [
        ([w for w in data["walls"] if not w.get("demolishable")], mat_struct, "承重墙", 240),
        ([w for w in data["walls"] if w.get("demolishable")], mat_demo, "可拆墙", 100),
    ]:
        pairs, used = find_wall_pairs(walls)
        print(f"{label}: {len(walls)}条线 → {len(pairs)}对配对")

        for idx, (i, j, dist) in enumerate(pairs):
            obj = create_wall_from_pair(walls[i], walls[j], avg_h, f"{label}_{idx:03d}", mat)
            collection.objects.link(obj)
            obj.scale = (0.001, 0.001, 0.001)
            total += 1

        # 未配对的线用默认厚度
        for i in range(len(walls)):
            if i not in used:
                obj = create_single_wall(walls[i], default_thick, avg_h, f"{label}_单_{i:03d}", mat)
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
    print(f"\n保存: {blend_path}")
    print(f"总共: {total}个对象")


if __name__ == "__main__":
    main()
