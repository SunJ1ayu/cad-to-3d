#!/usr/bin/env python3
"""
JSON → Blender 3D 白模生成脚本
从 CAD 解析后的 JSON 数据生成室内设计基础建筑模型。

用法: blender --background --python blender_wall.py -- <input.json> [output.blend]

第一版：只做墙体（承重墙 + 可拆墙）
- 自动检测闭合多边形
- 按多边形截面拉伸到层高
- 承重墙和可拆墙分不同材质
"""

import bpy
import bmesh
import json
import sys
import os
import math
from mathutils import Vector


def load_json(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def find_closed_polygons(walls: list) -> list:
    """
    从墙线段中找到闭合多边形。
    线段共享端点 → 连成链 → 首尾相连 → 闭合多边形。
    """
    # 建立端点连接图
    # 用容差匹配端点（CAD 坐标可能有微小误差）
    TOLERANCE = 5.0  # 5mm 容差

    def pt_key(x, y):
        """将坐标量化为容差网格 key"""
        return (round(x / TOLERANCE) * TOLERANCE, round(y / TOLERANCE) * TOLERANCE)

    # 构建邻接表: endpoint -> [(other_endpoint, wall_index)]
    from collections import defaultdict
    adjacency = defaultdict(list)
    endpoints = []  # (start_key, end_key, wall_index)

    for i, w in enumerate(walls):
        s = w["start"]
        e = w["end"]
        sk = pt_key(s[0], s[1])
        ek = pt_key(e[0], e[1])
        adjacency[sk].append((ek, i))
        adjacency[ek].append((sk, i))
        endpoints.append((sk, ek, i))

    # DFS 找闭合环
    visited_edges = set()
    polygons = []

    def find_cycle(start_key, current_key, path, used_edges):
        if len(path) > 2 and current_key == start_key:
            return path[:]
        for next_key, edge_idx in adjacency[current_key]:
            if edge_idx in used_edges:
                continue
            if next_key == start_key and len(path) > 2:
                return path[:] + [next_key]
            if edge_idx not in used_edges:
                used_edges.add(edge_idx)
                result = find_cycle(start_key, next_key, path + [next_key], used_edges)
                if result:
                    return result
                used_edges.discard(edge_idx)
        return None

    used_global = set()
    for start_key, end_key, edge_idx in endpoints:
        if edge_idx in used_global:
            continue
        used_global.add(edge_idx)
        cycle = find_cycle(start_key, end_key, [start_key, end_key], {edge_idx})
        if cycle:
            # 记录用到的边
            for i in range(len(cycle) - 1):
                for _, eidx in adjacency[cycle[i]]:
                    if eidx not in used_global:
                        for _, eidx2 in adjacency[cycle[i+1]]:
                            if eidx == eidx2:
                                used_global.add(eidx)
            polygons.append(cycle)

    return polygons


def get_polygon_points(polygon_keys, TOLERANCE=5.0):
    """将量化 key 列表转回实际坐标（取原始线段端点的平均值）"""
    # 这里简化：直接用 key 作为坐标（已经量化过了）
    return [(k[0], k[1]) for k in polygon_keys]


def create_wall_mesh(polygon_2d: list, height: float, name: str, material):
    """
    从 2D 闭合多边形创建 3D 墙体（拉伸）。
    polygon_2d: [(x, y), ...] 闭合多边形顶点
    height: 拉伸高度（层高）
    """
    bm = bmesh.new()

    # 创建底部顶点
    verts_bottom = []
    for x, y in polygon_2d:
        v = bm.verts.new((x, y, 0))
        verts_bottom.append(v)

    # 创建顶部顶点
    verts_top = []
    for x, y in polygon_2d:
        v = bm.verts.new((x, y, height))
        verts_top.append(v)

    bm.verts.ensure_lookup_table()

    n = len(polygon_2d)

    # 底面
    bottom_face = bm.faces.new(verts_bottom)

    # 顶面
    top_face = bm.faces.new(list(reversed(verts_top)))

    # 侧面
    for i in range(n):
        j = (i + 1) % n
        side_face = bm.faces.new([
            verts_bottom[i],
            verts_bottom[j],
            verts_top[j],
            verts_top[i],
        ])

    # 创建 mesh 对象
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material)

    # 设置原点（用 CAD 坐标系）
    # Blender 的坐标系：X=右, Y=前, Z=上
    # CAD 坐标系：X=右, Y=上（平面图），Z=高度
    # 转换：CAD(x,y) → Blender(x, -y, 0), CAD z → Blender z

    return obj


def setup_scene():
    """初始化 Blender 场景"""
    # 清空默认场景
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    # 删除默认 cube, camera, light
    for obj in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def create_materials():
    """创建墙体材质"""
    # 承重墙 - 深灰
    mat_struct = bpy.data.materials.new("承重墙")
    mat_struct.diffuse_color = (0.4, 0.4, 0.4, 1.0)

    # 可拆墙 - 浅灰
    mat_demo = bpy.data.materials.new("可拆墙")
    mat_demo.diffuse_color = (0.7, 0.7, 0.7, 1.0)

    return mat_struct, mat_demo


def main():
    # 解析命令行参数
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []

    if len(argv) < 1:
        print("用法: blender --background --python blender_wall.py -- <input.json> [output.blend]")
        sys.exit(1)

    json_path = argv[0]
    if len(argv) > 1:
        blend_path = argv[1]
    else:
        blend_path = json_path.rsplit(".", 1)[0] + "_walls.blend"

    print(f"加载数据: {json_path}")
    data = load_json(json_path)

    # 设置场景
    setup_scene()
    mat_struct, mat_demo = create_materials()

    # 分离承重墙和可拆墙
    demo_walls = [w for w in data["walls"] if w.get("demolishable")]
    struct_walls = [w for w in data["walls"] if not w.get("demolishable")]

    # 获取层高（取平均值）
    ceiling_heights = data.get("ceiling_heights", [2800])
    avg_height = sum(ceiling_heights) / len(ceiling_heights)
    print(f"层高: {avg_height:.0f}mm")

    # 处理构件
    collection = bpy.data.collections.new("建筑构件")
    bpy.context.scene.collection.children.link(collection)

    total = 0

    # ================================================================
    # 1. 墙体 - 每条线段单独拉伸，统一墙厚
    #    承重墙 240mm，可拆墙 100mm（从平行线间距统计得出）
    # ================================================================
    WALL_THICKNESS = {"struct": 240, "demo": 100}

    def create_wall_segment(wall, thickness, height, name, mat):
        """将单条墙线段拉伸为 box"""
        s = wall["start"]
        e = wall["end"]
        dx = e[0] - s[0]
        dy = e[1] - s[1]
        length = math.sqrt(dx**2 + dy**2)
        if length == 0:
            return None

        # 法线方向（墙厚方向）
        nx = -dy / length * thickness / 2
        ny = dx / length * thickness / 2

        bm = bmesh.new()
        verts_b = [
            bm.verts.new((s[0]+nx, s[1]+ny, 0)),
            bm.verts.new((s[0]-nx, s[1]-ny, 0)),
            bm.verts.new((e[0]-nx, e[1]-ny, 0)),
            bm.verts.new((e[0]+nx, e[1]+ny, 0)),
        ]
        verts_t = [
            bm.verts.new((s[0]+nx, s[1]+ny, height)),
            bm.verts.new((s[0]-nx, s[1]-ny, height)),
            bm.verts.new((e[0]-nx, e[1]-ny, height)),
            bm.verts.new((e[0]+nx, e[1]+ny, height)),
        ]
        bm.verts.ensure_lookup_table()
        bm.faces.new(verts_b)
        bm.faces.new(list(reversed(verts_t)))
        for j in range(4):
            jn = (j+1) % 4
            bm.faces.new([verts_b[j], verts_b[jn], verts_t[jn], verts_t[j]])

        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        obj.data.materials.append(mat)
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        return obj

    for wall_type, walls, mat, label in [
        ("struct", struct_walls, mat_struct, "承重墙"),
        ("demo", demo_walls, mat_demo, "可拆墙"),
    ]:
        thickness = WALL_THICKNESS[wall_type]
        print(f"{label}: {len(walls)}条线段, 统一墙厚={thickness}mm")
        for i, w in enumerate(walls):
            obj = create_wall_segment(w, thickness, avg_height, f"{label}_{i:03d}", mat)
            if obj:
                total += 1

    # ================================================================
    # 2. 梁（虚线几何 + H/W 标注）
    # ================================================================
    mat_beam = bpy.data.materials.new("梁")
    mat_beam.diffuse_color = (0.6, 0.3, 0.1, 1.0)  # 棕色

    # 配对梁标注 H/W
    beam_anns = [b for b in data["beams"] if b.get("type") == "beam_annotation"]
    beam_pairs = {}
    for b in beam_anns:
        pk = f"{b['position'][0]:.0f},{b['position'][1]:.0f}"
        found = False
        for existing in beam_pairs:
            px, py = map(float, existing.split(","))
            dist = ((b["position"][0]-px)**2 + (b["position"][1]-py)**2)**0.5
            if dist < 200:
                beam_pairs[existing][b["key"]] = b["value"]
                beam_pairs[existing]["pos"] = b["position"]
                found = True
                break
        if not found:
            beam_pairs[pk] = {b["key"]: b["value"], "pos": b["position"]}

    beam_geom = [b for b in data["beams"] if b.get("type") != "beam_annotation"]
    print(f"梁: {len(beam_geom)}条几何 + {len(beam_pairs)}组标注")

    for i, bg in enumerate(beam_geom):
        if "start" not in bg:
            continue
        s = bg["start"]
        e = bg["end"]
        # 找最近的梁标注
        mx = (s[0]+e[0])/2
        my = (s[1]+e[1])/2
        best_h, best_w = avg_height, 200  # 默认值
        best_dist = float("inf")
        for pk, vals in beam_pairs.items():
            px, py = vals["pos"]
            dist = ((mx-px)**2 + (my-py)**2)**0.5
            if dist < best_dist:
                best_dist = dist
                best_h = vals.get("beam_height", avg_height)
                best_w = vals.get("beam_width", 200)

        # 梁底 = 层高 - 梁高（梁从顶部往下）
        beam_bottom = avg_height - best_h
        length = bg["length"]

        # 创建梁 box
        bm = bmesh.new()
        # 沿线段方向拉伸
        dx = e[0] - s[0]
        dy = e[1] - s[1]
        ln = math.sqrt(dx**2 + dy**2)
        if ln == 0:
            continue
        # 法线方向（墙厚方向）
        nx, ny = -dy/ln * best_w/2, dx/ln * best_w/2
        # 四个底角
        corners = [
            (s[0]+nx, s[1]+ny, beam_bottom),
            (s[0]-nx, s[1]-ny, beam_bottom),
            (e[0]-nx, e[1]-ny, beam_bottom),
            (e[0]+nx, e[1]+ny, beam_bottom),
        ]
        verts_b = [bm.verts.new(c) for c in corners]
        verts_t = [bm.verts.new((c[0], c[1], beam_bottom+best_h)) for c in corners]
        bm.verts.ensure_lookup_table()
        bm.faces.new(verts_b)
        bm.faces.new(list(reversed(verts_t)))
        for j in range(4):
            jn = (j+1) % 4
            bm.faces.new([verts_b[j], verts_b[jn], verts_t[jn], verts_t[j]])

        mesh = bpy.data.meshes.new(f"梁_{i:03d}")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"梁_{i:03d}", mesh)
        obj.data.materials.append(mat_beam)
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        total += 1

    # ================================================================
    # 3. 门窗占位框
    # ================================================================
    mat_door = bpy.data.materials.new("门")
    mat_door.diffuse_color = (0.8, 0.5, 0.2, 1.0)  # 橙色
    mat_win = bpy.data.materials.new("窗")
    mat_win.diffuse_color = (0.3, 0.5, 0.8, 1.0)  # 蓝色

    # 门
    door_heights = []
    for ann in data["annotations"]:
        if "door_height" in ann.get("parsed", {}):
            door_heights.append(ann["parsed"]["door_height"][0])
    default_door_h = door_heights[0] if door_heights else 2100

    print(f"门: {len(data['doors'])}个, 默认门高={default_door_h}mm")
    for i, door in enumerate(data["doors"]):
        if door.get("representation") == "polyline":
            cx, cy = door["position"]
            w = door["width"]
            d = door["depth"]
            bm = bmesh.new()
            hw, hd = w/2, d/2
            verts_b = [
                bm.verts.new((cx-hw, cy-hd, 0)),
                bm.verts.new((cx+hw, cy-hd, 0)),
                bm.verts.new((cx+hw, cy+hd, 0)),
                bm.verts.new((cx-hw, cy+hd, 0)),
            ]
            verts_t = [
                bm.verts.new((cx-hw, cy-hd, default_door_h)),
                bm.verts.new((cx+hw, cy-hd, default_door_h)),
                bm.verts.new((cx+hw, cy+hd, default_door_h)),
                bm.verts.new((cx-hw, cy+hd, default_door_h)),
            ]
            bm.verts.ensure_lookup_table()
            bm.faces.new(verts_b)
            bm.faces.new(list(reversed(verts_t)))
            for j in range(4):
                jn = (j+1) % 4
                bm.faces.new([verts_b[j], verts_b[jn], verts_t[jn], verts_t[j]])
            mesh = bpy.data.meshes.new(f"门_{i:03d}")
            bm.to_mesh(mesh)
            bm.free()
            obj = bpy.data.objects.new(f"门_{i:03d}", mesh)
            obj.data.materials.append(mat_door)
            collection.objects.link(obj)
            obj.scale = (0.001, 0.001, 0.001)
            total += 1

    # 窗
    print(f"窗: {len(data['windows'])}个")
    for i, win in enumerate(data["windows"]):
        cx, cy = win["position"]
        w = win["opening_length"]
        sill = win.get("sill_height") or 900
        wh = win.get("window_height") or 1500
        bm = bmesh.new()
        hw = w / 2
        # 窗框厚度用窗宽
        fw = win.get("frame_width", 240) / 2
        verts_b = [
            bm.verts.new((cx-hw, cy-fw, sill)),
            bm.verts.new((cx+hw, cy-fw, sill)),
            bm.verts.new((cx+hw, cy+fw, sill)),
            bm.verts.new((cx-hw, cy+fw, sill)),
        ]
        verts_t = [
            bm.verts.new((cx-hw, cy-fw, sill+wh)),
            bm.verts.new((cx+hw, cy-fw, sill+wh)),
            bm.verts.new((cx+hw, cy+fw, sill+wh)),
            bm.verts.new((cx-hw, cy+fw, sill+wh)),
        ]
        bm.verts.ensure_lookup_table()
        bm.faces.new(verts_b)
        bm.faces.new(list(reversed(verts_t)))
        for j in range(4):
            jn = (j+1) % 4
            bm.faces.new([verts_b[j], verts_b[jn], verts_t[jn], verts_t[j]])
        mesh = bpy.data.meshes.new(f"窗_{i:03d}")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"窗_{i:03d}", mesh)
        obj.data.materials.append(mat_win)
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        total += 1

    # 设置视图
    bpy.ops.object.select_all(action='SELECT')

    # 保存
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"\n保存: {blend_path}")
    print(f"总共生成: {total} 个对象")
    print(f"  墙体: {len([o for o in collection.objects if '墙' in o.name])}")
    print(f"  梁: {len([o for o in collection.objects if '梁' in o.name])}")
    print(f"  门: {len([o for o in collection.objects if '门' in o.name])}")
    print(f"  窗: {len([o for o in collection.objects if '窗' in o.name])}")


if __name__ == "__main__":
    main()
