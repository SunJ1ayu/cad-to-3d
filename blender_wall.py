#!/usr/bin/env python3
"""
JSON → Blender 3D 白模生成脚本（v2）
基于平行线对生成墙体，每对平行线 = 一面墙的内外表面。

用法: blender --background --python blender_wall.py -- <input.json> [output.blend]
"""

import bpy
import bmesh
import json
import sys
import math


def load_json(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def find_parallel_pairs(walls, min_dist=50, max_dist=350, min_length=100):
    """找平行线对，返回配对结果"""
    pairs = []
    used = set()
    for i in range(len(walls)):
        for j in range(i+1, len(walls)):
            a, b = walls[i], walls[j]
            dx1 = a["end"][0] - a["start"][0]
            dy1 = a["end"][1] - a["start"][1]
            dx2 = b["end"][0] - b["start"][0]
            dy2 = b["end"][1] - b["start"][1]
            len1 = math.sqrt(dx1**2 + dy1**2)
            len2 = math.sqrt(dx2**2 + dy2**2)
            if len1 < min_length or len2 < min_length:
                continue
            a1 = math.atan2(dy1, dx1)
            a2 = math.atan2(dy2, dy2) if dx2 == 0 and dy2 == 0 else math.atan2(dy2, dx2)
            diff = abs(a1 - a2) % math.pi
            if diff > 0.1 and diff < math.pi - 0.1:
                continue
            # 距离：线段a中点到线段b的距离
            mx = (a["start"][0] + a["end"][0]) / 2
            my = (a["start"][1] + a["end"][1]) / 2
            px = mx - b["start"][0]
            py = my - b["start"][1]
            dist = abs(py * dx2 - px * dy2) / len2
            if min_dist < dist < max_dist:
                pairs.append({"i": i, "j": j, "dist": round(dist)})
                used.add(i)
                used.add(j)
    return pairs, used


def create_wall_from_pair(wall_a, wall_b, height, name, mat):
    """
    从平行线对创建墙体。
    wall_a 和 wall_b 是两条平行线，它们的4个端点围成矩形截面，
    沿线段方向拉伸到层高。
    """
    # 两条线的端点
    a1 = wall_a["start"]
    a2 = wall_a["end"]
    b1 = wall_b["start"]
    b2 = wall_b["end"]

    # 对齐：确保 a1-b1 是对应端
    # 用中点对齐判断
    mid_a = ((a1[0]+a2[0])/2, (a1[1]+a2[1])/2)
    mid_b = ((b1[0]+b2[0])/2, (b1[1]+b2[1])/2)

    # 如果 b1 距离 a1 更远，反转 b
    d11 = (a1[0]-b1[0])**2 + (a1[1]-b1[1])**2
    d12 = (a1[0]-b2[0])**2 + (a1[1]-b2[1])**2
    if d12 < d11:
        b1, b2 = b2, b1

    # 4个底面顶点（矩形截面）
    bm = bmesh.new()
    verts_b = [
        bm.verts.new((a1[0], a1[1], 0)),
        bm.verts.new((b1[0], b1[1], 0)),
        bm.verts.new((b2[0], b2[1], 0)),
        bm.verts.new((a2[0], a2[1], 0)),
    ]
    # 4个顶面顶点
    verts_t = [
        bm.verts.new((a1[0], a1[1], height)),
        bm.verts.new((b1[0], b1[1], height)),
        bm.verts.new((b2[0], b2[1], height)),
        bm.verts.new((a2[0], a2[1], height)),
    ]
    bm.verts.ensure_lookup_table()

    # 底面、顶面、4个侧面
    bm.faces.new(verts_b)
    bm.faces.new(list(reversed(verts_t)))
    for k in range(4):
        kn = (k+1) % 4
        bm.faces.new([verts_b[k], verts_b[kn], verts_t[kn], verts_t[k]])

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
    else:
        argv = []

    if len(argv) < 1:
        print("用法: blender --background --python blender_wall.py -- <input.json> [output.blend]")
        sys.exit(1)

    json_path = argv[0]
    blend_path = argv[1] if len(argv) > 1 else json_path.rsplit(".", 1)[0] + "_walls.blend"

    print(f"加载: {json_path}")
    data = load_json(json_path)

    setup_scene()

    # 材质
    mat_struct = bpy.data.materials.new("承重墙")
    mat_struct.diffuse_color = (0.4, 0.4, 0.4, 1.0)
    mat_demo = bpy.data.materials.new("可拆墙")
    mat_demo.diffuse_color = (0.7, 0.7, 0.7, 1.0)
    mat_door = bpy.data.materials.new("门")
    mat_door.diffuse_color = (0.8, 0.5, 0.2, 1.0)
    mat_win = bpy.data.materials.new("窗")
    mat_win.diffuse_color = (0.3, 0.5, 0.8, 1.0)

    ceiling_heights = data.get("ceiling_heights", [2800])
    avg_height = sum(ceiling_heights) / len(ceiling_heights)
    print(f"层高: {avg_height:.0f}mm")

    collection = bpy.data.collections.new("建筑构件")
    bpy.context.scene.collection.children.link(collection)

    total = 0

    # ================================================================
    # 1. 墙体 - 基于平行线对
    # ================================================================
    for walls, mat, label in [
        ([w for w in data["walls"] if not w.get("demolishable")], mat_struct, "承重墙"),
        ([w for w in data["walls"] if w.get("demolishable")], mat_demo, "可拆墙"),
    ]:
        pairs, used = find_parallel_pairs(walls)
        print(f"{label}: {len(walls)}条线 → {len(pairs)}个平行线对")

        for idx, p in enumerate(pairs):
            obj = create_wall_from_pair(
                walls[p["i"]], walls[p["j"]],
                avg_height, f"{label}_{idx:03d}", mat
            )
            collection.objects.link(obj)
            obj.scale = (0.001, 0.001, 0.001)
            total += 1

        # 未配对的线段（孤立墙）单独处理
        for i in range(len(walls)):
            if i not in used:
                w = walls[i]
                s, e = w["start"], w["end"]
                dx = e[0]-s[0]
                dy = e[1]-s[1]
                ln = math.sqrt(dx**2+dy**2)
                if ln < 50:
                    continue
                # 用默认墙厚
                thick = 240 if mat == mat_struct else 100
                nx = -dy/ln * thick/2
                ny = dx/ln * thick/2
                bm = bmesh.new()
                vb = [
                    bm.verts.new((s[0]+nx, s[1]+ny, 0)),
                    bm.verts.new((s[0]-nx, s[1]-ny, 0)),
                    bm.verts.new((e[0]-nx, e[1]-ny, 0)),
                    bm.verts.new((e[0]+nx, e[1]+ny, 0)),
                ]
                vt = [
                    bm.verts.new((s[0]+nx, s[1]+ny, avg_height)),
                    bm.verts.new((s[0]-nx, s[1]-ny, avg_height)),
                    bm.verts.new((e[0]-nx, e[1]-ny, avg_height)),
                    bm.verts.new((e[0]+nx, e[1]+ny, avg_height)),
                ]
                bm.verts.ensure_lookup_table()
                bm.faces.new(vb)
                bm.faces.new(list(reversed(vt)))
                for k in range(4):
                    kn = (k+1)%4
                    bm.faces.new([vb[k], vb[kn], vt[kn], vt[k]])
                mesh = bpy.data.meshes.new(f"{label}_单_{i:03d}")
                bm.to_mesh(mesh)
                bm.free()
                obj = bpy.data.objects.new(f"{label}_单_{i:03d}", mesh)
                obj.data.materials.append(mat)
                collection.objects.link(obj)
                obj.scale = (0.001, 0.001, 0.001)
                total += 1

    # ================================================================
    # 2. 门占位框
    # ================================================================
    door_heights = []
    for ann in data["annotations"]:
        if "door_height" in ann.get("parsed", {}):
            door_heights.append(ann["parsed"]["door_height"][0])
    default_door_h = door_heights[0] if door_heights else 2100

    print(f"门: {len(data['doors'])}个, 高={default_door_h}mm")
    for i, door in enumerate(data["doors"]):
        if door.get("representation") == "polyline":
            cx, cy = door["position"]
            w, d = door["width"], door["depth"]
            hw, hd = w/2, d/2
            bm = bmesh.new()
            vb = [
                bm.verts.new((cx-hw, cy-hd, 0)),
                bm.verts.new((cx+hw, cy-hd, 0)),
                bm.verts.new((cx+hw, cy+hd, 0)),
                bm.verts.new((cx-hw, cy+hd, 0)),
            ]
            vt = [
                bm.verts.new((cx-hw, cy-hd, default_door_h)),
                bm.verts.new((cx+hw, cy-hd, default_door_h)),
                bm.verts.new((cx+hw, cy+hd, default_door_h)),
                bm.verts.new((cx-hw, cy+hd, default_door_h)),
            ]
            bm.verts.ensure_lookup_table()
            bm.faces.new(vb)
            bm.faces.new(list(reversed(vt)))
            for k in range(4):
                kn = (k+1)%4
                bm.faces.new([vb[k], vb[kn], vt[kn], vt[k]])
            mesh = bpy.data.meshes.new(f"门_{i:03d}")
            bm.to_mesh(mesh)
            bm.free()
            obj = bpy.data.objects.new(f"门_{i:03d}", mesh)
            obj.data.materials.append(mat_door)
            collection.objects.link(obj)
            obj.scale = (0.001, 0.001, 0.001)
            total += 1

    # ================================================================
    # 3. 窗占位框
    # ================================================================
    print(f"窗: {len(data['windows'])}个")
    for i, win in enumerate(data["windows"]):
        cx, cy = win["position"]
        w = win["opening_length"]
        sill = win.get("sill_height") or 900
        wh = win.get("window_height") or 1500
        fw = win.get("frame_width", 240) / 2
        hw = w / 2
        bm = bmesh.new()
        vb = [
            bm.verts.new((cx-hw, cy-fw, sill)),
            bm.verts.new((cx+hw, cy-fw, sill)),
            bm.verts.new((cx+hw, cy+fw, sill)),
            bm.verts.new((cx-hw, cy+fw, sill)),
        ]
        vt = [
            bm.verts.new((cx-hw, cy-fw, sill+wh)),
            bm.verts.new((cx+hw, cy-fw, sill+wh)),
            bm.verts.new((cx+hw, cy+fw, sill+wh)),
            bm.verts.new((cx-hw, cy+fw, sill+wh)),
        ]
        bm.verts.ensure_lookup_table()
        bm.faces.new(vb)
        bm.faces.new(list(reversed(vt)))
        for k in range(4):
            kn = (k+1)%4
            bm.faces.new([vb[k], vb[kn], vt[kn], vt[k]])
        mesh = bpy.data.meshes.new(f"窗_{i:03d}")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"窗_{i:03d}", mesh)
        obj.data.materials.append(mat_win)
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        total += 1

    # ================================================================
    # 归零
    # ================================================================
    bpy.ops.object.select_all(action='SELECT')
    bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
    bpy.ops.object.transform_apply(scale=True)

    min_x = min_y = min_z = float('inf')
    max_x = max_y = max_z = float('-inf')
    for obj in bpy.context.selected_objects:
        if obj.type == 'MESH':
            for v in obj.data.vertices:
                wc = obj.matrix_world @ v.co
                min_x, max_x = min(min_x, wc.x), max(max_x, wc.x)
                min_y, max_y = min(min_y, wc.y), max(max_y, wc.y)
                min_z, max_z = min(min_z, wc.z), max(max_z, wc.z)

    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    for obj in bpy.context.selected_objects:
        obj.location.x -= cx
        obj.location.y -= cy
        obj.location.z -= min_z

    bpy.ops.object.select_all(action='DESELECT')
    print(f"归零: 中心({cx:.2f},{cy:.2f}) 底面Z={min_z:.2f}→0")

    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"\n保存: {blend_path}")
    print(f"总共: {total}个对象")
    print(f"  墙体: {len([o for o in collection.objects if '墙' in o.name])}")
    print(f"  门: {len([o for o in collection.objects if '门' in o.name])}")
    print(f"  窗: {len([o for o in collection.objects if '窗' in o.name])}")


if __name__ == "__main__":
    main()
