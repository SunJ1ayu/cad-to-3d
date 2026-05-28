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
import os
from collections import defaultdict, deque


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


def create_single_line_beam_box(s, e, thickness, height, name, mat, wall_objects):
    """把 CAD 单线作为远侧边，向最近墙体方向生成梁盒。"""
    dx = e[0] - s[0]
    dy = e[1] - s[1]
    ln = math.sqrt(dx**2 + dy**2)
    if ln == 0:
        return None

    nx = -dy / ln
    ny = dx / ln
    mid = ((s[0] + e[0]) / 2 * 0.001, (s[1] + e[1]) / 2 * 0.001)
    endpoints = [
        (s[0] * 0.001, s[1] * 0.001),
        (e[0] * 0.001, e[1] * 0.001),
    ]

    side = 1
    if wall_objects:
        wall_points = [point for wall in wall_objects for point in object_xy_coords(wall)]
        endpoint_candidates = []
        for endpoint in endpoints:
            nearest = min(
                wall_points,
                key=lambda point: (point[0] - endpoint[0]) ** 2 + (point[1] - endpoint[1]) ** 2,
            )
            dist = math.hypot(nearest[0] - endpoint[0], nearest[1] - endpoint[1])
            endpoint_candidates.append((dist, endpoint, nearest))
        endpoint_dist, endpoint, wall_point = min(endpoint_candidates, key=lambda item: item[0])

        if endpoint_dist <= 0.2:
            line_point = endpoint
        else:
            wall_point = min(
                wall_points,
                key=lambda point: (point[0] - mid[0]) ** 2 + (point[1] - mid[1]) ** 2,
            )
            line_point = mid

        wall_side = (wall_point[0] - line_point[0]) * nx + (wall_point[1] - line_point[1]) * ny
        side = 1 if wall_side >= 0 else -1

    ox = nx * thickness * side
    oy = ny * thickness * side
    bm = bmesh.new()
    vb = [bm.verts.new((s[0], s[1], 0)), bm.verts.new((e[0], e[1], 0)),
          bm.verts.new((e[0] + ox, e[1] + oy, 0)), bm.verts.new((s[0] + ox, s[1] + oy, 0))]
    vt = [bm.verts.new((s[0], s[1], height)), bm.verts.new((e[0], e[1], height)),
          bm.verts.new((e[0] + ox, e[1] + oy, height)), bm.verts.new((s[0] + ox, s[1] + oy, height))]
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


def snap_single_line_beam_ends(obj, target_objects, tol=0.02):
    """单线梁保持宽度，只做整体靠边平移和沿长度方向吸端点。"""
    bpy.context.view_layer.update()
    points = object_xy_coords(obj)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width_x = max(xs) - min(xs)
    width_y = max(ys) - min(ys)
    axis = "y" if width_y >= width_x else "x"

    target_points = [point for target in target_objects for point in object_xy_coords(target)]
    if not target_points:
        return

    sx = obj.scale.x if obj.scale.x else 1
    sy = obj.scale.y if obj.scale.y else 1

    if axis == "y":
        side_candidates = []
        for side_x in (min(xs), max(xs)):
            nearby = [
                px
                for px, py in target_points
                if min(ys) - tol <= py <= max(ys) + tol and abs(px - side_x) <= tol
            ]
            if nearby:
                target_x = min(nearby, key=lambda px: abs(px - side_x))
                side_candidates.append((abs(target_x - side_x), target_x - side_x))
        if side_candidates:
            _, dx = min(side_candidates, key=lambda item: item[0])
            for vert in obj.data.vertices:
                vert.co.x = (vert.co.x * sx + dx) / sx
            obj.data.update()
            points = object_xy_coords(obj)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
    else:
        side_candidates = []
        for side_y in (min(ys), max(ys)):
            nearby = [
                py
                for px, py in target_points
                if min(xs) - tol <= px <= max(xs) + tol and abs(py - side_y) <= tol
            ]
            if nearby:
                target_y = min(nearby, key=lambda py: abs(py - side_y))
                side_candidates.append((abs(target_y - side_y), target_y - side_y))
        if side_candidates:
            _, dy = min(side_candidates, key=lambda item: item[0])
            for vert in obj.data.vertices:
                vert.co.y = (vert.co.y * sy + dy) / sy
            obj.data.update()
            points = object_xy_coords(obj)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]

    if axis == "y":
        current_ends = (min(ys), max(ys))
        candidates = [
            py
            for px, py in target_points
            if min(xs) - tol <= px <= max(xs) + tol
        ]
    else:
        current_ends = (min(xs), max(xs))
        candidates = [
            px
            for px, py in target_points
            if min(ys) - tol <= py <= max(ys) + tol
        ]

    targets = list(current_ends)
    for idx, current in enumerate(current_ends):
        nearby = [candidate for candidate in candidates if abs(candidate - current) <= tol]
        if nearby:
            targets[idx] = min(nearby, key=lambda candidate: abs(candidate - current))

    for vert in obj.data.vertices:
        world_x = vert.co.x * sx
        world_y = vert.co.y * sy
        if axis == "y":
            if abs(world_y - current_ends[0]) < 0.001:
                vert.co.y = targets[0] / sy
            elif abs(world_y - current_ends[1]) < 0.001:
                vert.co.y = targets[1] / sy
        else:
            if abs(world_x - current_ends[0]) < 0.001:
                vert.co.x = targets[0] / sx
            elif abs(world_x - current_ends[1]) < 0.001:
                vert.co.x = targets[1] / sx
    obj.data.update()


def snap_single_line_beam_far_side(obj, target_objects, tol=0.02):
    """单线梁靠墙侧已对齐后，允许远侧边吸到附近窗/墙边。"""
    bpy.context.view_layer.update()
    points = object_xy_coords(obj)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width_x = max(xs) - min(xs)
    width_y = max(ys) - min(ys)
    axis = "y" if width_y >= width_x else "x"
    target_points = [point for target in target_objects for point in object_xy_coords(target)]
    if not target_points:
        return

    sx = obj.scale.x if obj.scale.x else 1
    sy = obj.scale.y if obj.scale.y else 1

    if axis == "y":
        side_values = (min(xs), max(xs))
        side_scores = []
        for side_x in side_values:
            matches = [
                px for px, py in target_points
                if min(ys) - tol <= py <= max(ys) + tol and abs(px - side_x) <= 0.001
            ]
            side_scores.append(len(matches))
        anchor_side = side_values[0] if side_scores[0] >= side_scores[1] else side_values[1]
        far_side = side_values[1] if anchor_side == side_values[0] else side_values[0]
        candidates = [
            px for px, py in target_points
            if min(ys) - tol <= py <= max(ys) + tol and abs(px - far_side) <= tol
        ]
        if not candidates:
            return
        target_x = min(candidates, key=lambda px: abs(px - far_side))
        for vert in obj.data.vertices:
            world_x = vert.co.x * sx
            if abs(world_x - far_side) < 0.001:
                vert.co.x = target_x / sx
    else:
        side_values = (min(ys), max(ys))
        side_scores = []
        for side_y in side_values:
            matches = [
                py for px, py in target_points
                if min(xs) - tol <= px <= max(xs) + tol and abs(py - side_y) <= 0.001
            ]
            side_scores.append(len(matches))
        anchor_side = side_values[0] if side_scores[0] >= side_scores[1] else side_values[1]
        far_side = side_values[1] if anchor_side == side_values[0] else side_values[0]
        candidates = [
            py for px, py in target_points
            if min(xs) - tol <= px <= max(xs) + tol and abs(py - far_side) <= tol
        ]
        if not candidates:
            return
        target_y = min(candidates, key=lambda py: abs(py - far_side))
        for vert in obj.data.vertices:
            world_y = vert.co.y * sy
            if abs(world_y - far_side) < 0.001:
                vert.co.y = target_y / sy
    obj.data.update()


def extend_beam_ends_to_walls(obj, wall_objects, tol=0.30):
    """Extend beam end caps to nearby wall boundary points when CAD lines stop at the near face."""
    bpy.context.view_layer.update()
    points = object_xy_coords(obj)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width_x = max(xs) - min(xs)
    width_y = max(ys) - min(ys)
    axis = "y" if width_y >= width_x else "x"
    sx = obj.scale.x if obj.scale.x else 1
    sy = obj.scale.y if obj.scale.y else 1

    wall_edges = []
    for wall in wall_objects:
        footprint = object_footprint_xy(wall)
        if not footprint:
            continue
        pts = footprint[:-1] if footprint[0] == footprint[-1] else footprint
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            wall_edges.append((a, b))

    if axis == "y":
        cross0, cross1 = min(xs), max(xs)
        end0, end1 = min(ys), max(ys)
        wall_boundaries = []
        for (ax, ay), (bx, by) in wall_edges:
            if abs(ay - by) > 0.001:
                continue
            if min(cross1, max(ax, bx)) - max(cross0, min(ax, bx)) > 0:
                wall_boundaries.append(ay)
    else:
        cross0, cross1 = min(ys), max(ys)
        end0, end1 = min(xs), max(xs)
        wall_boundaries = []
        for (ax, ay), (bx, by) in wall_edges:
            if abs(ax - bx) > 0.001:
                continue
            if min(cross1, max(ay, by)) - max(cross0, min(ay, by)) > 0:
                wall_boundaries.append(ax)

    already_touching_tol = 0.02
    lower_already_touches_wall = any(abs(end - end0) <= already_touching_tol for end in wall_boundaries)
    upper_already_touches_wall = any(abs(end - end1) <= already_touching_tol for end in wall_boundaries)
    lower_candidates = [] if lower_already_touches_wall else [
        end
        for end in wall_boundaries
        if 0 < end0 - end <= tol
    ]
    upper_candidates = [] if upper_already_touches_wall else [
        end
        for end in wall_boundaries
        if 0 < end - end1 <= tol
    ]
    targets = [
        min(lower_candidates) if lower_candidates else end0,
        max(upper_candidates) if upper_candidates else end1,
    ]

    if targets == [end0, end1]:
        return

    for vert in obj.data.vertices:
        world_x = vert.co.x * sx
        world_y = vert.co.y * sy
        if axis == "y":
            if abs(world_y - end0) < 0.001:
                vert.co.y = targets[0] / sy
            elif abs(world_y - end1) < 0.001:
                vert.co.y = targets[1] / sy
        else:
            if abs(world_x - end0) < 0.001:
                vert.co.x = targets[0] / sx
            elif abs(world_x - end1) < 0.001:
                vert.co.x = targets[1] / sx
    obj.data.update()


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


def create_extruded_polygon_meters(points, height, name, mat=None, z_base=0):
    pts = [(p[0], p[1]) for p in points]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None

    bm = bmesh.new()
    vb = [bm.verts.new((x, y, z_base)) for x, y in pts]
    vt = [bm.verts.new((x, y, z_base + height)) for x, y in pts]
    bm.verts.ensure_lookup_table()
    try:
        bm.faces.new(vb)
    except ValueError:
        pass
    try:
        bm.faces.new(list(reversed(vt)))
    except ValueError:
        pass
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        try:
            bm.faces.new([vb[i], vb[j], vt[j], vt[i]])
        except ValueError:
            pass

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    if mat:
        obj.data.materials.append(mat)
    return obj


def object_bbox_xy(obj):
    bpy.context.view_layer.update()
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    xs = [v.x for v in coords]
    ys = [v.y for v in coords]
    return min(xs), min(ys), max(xs), max(ys)


def object_footprint_xy(obj):
    bpy.context.view_layer.update()
    horizontal_faces = []
    for face in obj.data.polygons:
        coords = [obj.matrix_world @ obj.data.vertices[index].co for index in face.vertices]
        zs = [coord.z for coord in coords]
        if max(zs) - min(zs) > 0.001:
            continue
        horizontal_faces.append((sum(zs) / len(zs), [(coord.x, coord.y) for coord in coords]))
    if not horizontal_faces:
        return None
    _, points = min(horizontal_faces, key=lambda item: item[0])
    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def object_xy_coords(obj):
    bpy.context.view_layer.update()
    return [(co.x, co.y) for co in (obj.matrix_world @ v.co for v in obj.data.vertices)]


def match_window_end_walls(win, walls, endpoint_tol=80, center_tol=80):
    length = win.get("opening_length")
    if not length:
        return None

    angle = math.radians(win.get("rotation", 0))
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    x, y = win["position"]
    end_targets = [
        (x - ux * length / 2, y - uy * length / 2),
        (x + ux * length / 2, y + uy * length / 2),
    ]
    matches = []

    for target in end_targets:
        best = None
        for wall in walls:
            sx, sy = wall["start"]
            ex, ey = wall["end"]
            dx, dy = ex - sx, ey - sy
            wall_len = math.hypot(dx, dy)
            if wall_len <= 20:
                continue
            wx, wy = dx / wall_len, dy / wall_len
            if abs(wx * nx + wy * ny) < 0.98:
                continue

            mx, my = (sx + ex) / 2, (sy + ey) / 2
            along_delta = abs((mx - target[0]) * ux + (my - target[1]) * uy)
            center_delta = abs((mx - x) * nx + (my - y) * ny)
            if along_delta > endpoint_tol or center_delta > center_tol:
                continue

            score = (along_delta, center_delta, -wall_len)
            if best is None or score < best["score"]:
                best = {
                    "score": score,
                    "depth": wall_len,
                    "center_delta": (mx - x) * nx + (my - y) * ny,
                }
        if best:
            matches.append(best)

    if len(matches) < 2:
        return None

    depth = sum(match["depth"] for match in matches) / len(matches)
    center_offset = sum(match["center_delta"] for match in matches) / len(matches)
    return {
        "depth": depth,
        "center": (x + nx * center_offset, y + ny * center_offset),
        "source": "end_walls",
    }


def match_window_wall_bbox(win, wall_objects, margin=600):
    length = win.get("opening_length")
    if not length:
        return None

    angle = math.radians(win.get("rotation", 0))
    axis_x = (math.cos(angle), math.sin(angle))
    x, y = win["position"]
    half = length / 2
    horizontal = abs(axis_x[0]) >= abs(axis_x[1])
    best = None

    for wall in wall_objects:
        x0, y0, x1, y1 = [v * 1000 for v in object_bbox_xy(wall)]
        if horizontal:
            span0, span1 = x - half, x + half
            overlap = min(span1, x1) - max(span0, x0)
            perp_dist = 0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
            wall_depth = y1 - y0
            center = (x, (y0 + y1) / 2)
        else:
            span0, span1 = y - half, y + half
            overlap = min(span1, y1) - max(span0, y0)
            perp_dist = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
            wall_depth = x1 - x0
            center = ((x0 + x1) / 2, y)

        if overlap <= min(length * 0.2, 100) or perp_dist > margin or wall_depth <= 20:
            continue

        score = (perp_dist, -overlap, wall_depth)
        if best is None or score < best["score"]:
            best = {
                "score": score,
                "depth": wall_depth,
                "center": center,
                "bbox": (x0, y0, x1, y1),
            }

    return best


def create_window_infill_boxes(win, height, mat, wall_match=None):
    length = win.get("opening_length")
    sill = win.get("sill_height")
    window_height = win.get("window_height")
    if not length or sill is None or window_height is None:
        return []

    angle = math.radians(win.get("rotation", 0))
    axis_x = (math.cos(angle), math.sin(angle))
    axis_y = (-axis_x[1], axis_x[0])
    x, y = wall_match["center"] if wall_match else win["position"]
    depth = wall_match["depth"] if wall_match else max(win.get("frame_width") or 0, 240)
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
            depth,
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


def expand_bbox(bbox, margin):
    x0, y0, x1, y1 = bbox
    return x0 - margin, y0 - margin, x1 + margin, y1 + margin


def polygon_center(points):
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if not pts:
        return (0, 0)
    return (
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts),
    )


def polygon_area(points):
    if len(points) < 3:
        return 0
    area = 0
    for i in range(len(points) - 1):
        area += points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1]
    return abs(area) / 2


def polygon_bbox(points):
    return (
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    )


def same_bbox(a, b, tol=1.0):
    return all(abs(a[i] - b[i]) <= tol for i in range(4))


def bbox_center(bbox):
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def collect_ceiling_markers(data):
    markers = []
    for ann in data.get("annotations", []):
        heights = ann.get("parsed", {}).get("ceiling_height")
        pos = ann.get("position")
        if heights and pos:
            markers.append((pos[0], pos[1], round(heights[0])))
    return markers


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
            marker_heights = [
                marker[2]
                for marker in markers
                if point_in_polygon((marker[0], marker[1]), footprint)
            ]
            ceiling_height = min(marker_heights) if marker_heights else nearest_ceiling_height(polygon_center(footprint))
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


def ceiling_footprints_from_model_objects(blocker_objects, ceiling_markers=None, min_area_sqm=3.0):
    """用墙/梁真实2D footprint切分外轮廓，返回 mm 单位吊顶 footprint。"""
    blocker_polygons = []
    bboxes = []
    for obj in blocker_objects:
        if obj.type != "MESH":
            continue
        footprint = object_footprint_xy(obj)
        if not footprint:
            continue
        x0, y0, x1, y1 = polygon_bbox(footprint)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        blocker_polygons.append(footprint)
        bboxes.append((x0, y0, x1, y1))
    if not bboxes:
        return []
    expansion_bboxes = [
        bbox
        for bbox in bboxes
        if min(bbox[2] - bbox[0], bbox[3] - bbox[1]) <= 0.8
    ]
    coverage_strip_bboxes = [
        bbox
        for bbox in bboxes
        if min(bbox[2] - bbox[0], bbox[3] - bbox[1]) <= 1.0
    ]

    x_min = min(bbox[0] for bbox in bboxes)
    y_min = min(bbox[1] for bbox in bboxes)
    x_max = max(bbox[2] for bbox in bboxes)
    y_max = max(bbox[3] for bbox in bboxes)
    exterior_margin = 1.0
    xs = sorted(
        {round(value, 6) for bbox in bboxes for value in (bbox[0], bbox[2])}
        | {round(x_min - exterior_margin, 6), round(x_max + exterior_margin, 6)}
    )
    ys = sorted(
        {round(value, 6) for bbox in bboxes for value in (bbox[1], bbox[3])}
        | {round(y_min - exterior_margin, 6), round(y_max + exterior_margin, 6)}
    )
    if len(xs) < 2 or len(ys) < 2:
        return []

    def overlap_1d(a0, a1, b0, b1, tol=1e-6):
        return min(a1, b1) - max(a0, b0) > tol

    def expand_boundary_coverage(boundary):
        """空腔只覆盖净空；层高块要盖住分区，所以扩到相邻墙/梁外边。"""
        x0, y0, x1, y1 = polygon_bbox(boundary)
        x0 *= 0.001
        y0 *= 0.001
        x1 *= 0.001
        y1 *= 0.001
        tol = 0.002

        orig_x0, orig_y0, orig_x1, orig_y1 = x0, y0, x1, y1
        for bx0, by0, bx1, by1 in expansion_bboxes:
            if overlap_1d(orig_y0, orig_y1, by0, by1):
                if abs(bx1 - orig_x0) <= tol and bx0 < x0:
                    x0 = bx0
                if abs(bx0 - orig_x1) <= tol and bx1 > x1:
                    x1 = bx1
            if overlap_1d(orig_x0, orig_x1, bx0, bx1):
                if abs(by1 - orig_y0) <= tol and by0 < y0:
                    y0 = by0
                if abs(by0 - orig_y1) <= tol and by1 > y1:
                    y1 = by1

        return [
            (x0 * 1000, y0 * 1000),
            (x1 * 1000, y0 * 1000),
            (x1 * 1000, y1 * 1000),
            (x0 * 1000, y1 * 1000),
            (x0 * 1000, y0 * 1000),
        ]

    def rect_from_bbox(bbox):
        x0, y0, x1, y1 = bbox
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]

    def should_merge_coverage(a, b, exterior_y1):
        ax0, ay0, ax1, ay1 = polygon_bbox(a)
        bx0, by0, bx1, by1 = polygon_bbox(b)
        touches_top_boundary = abs(ay1 - exterior_y1) <= 300 or abs(by1 - exterior_y1) <= 300
        if not touches_top_boundary:
            return False

        gap_x = max(bx0 - ax1, ax0 - bx1, 0)
        gap_y = max(by0 - ay1, ay0 - by1, 0)
        overlap_x = min(ax1, bx1) - max(ax0, bx0)
        overlap_y = min(ay1, by1) - max(ay0, by0)
        separated_x = ax1 <= bx0 or bx1 <= ax0
        near_x = separated_x and gap_x <= 260 and overlap_y > 500
        if not near_x:
            return False

        ux0, uy0, ux1, uy1 = min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)
        union_area = (ux1 - ux0) * (uy1 - uy0)
        parts_area = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0)
        return union_area <= parts_area * 1.35

    def merge_coverage_footprints(items):
        merged = list(items)
        exterior_y1 = max(polygon_bbox(item)[3] for item in merged)
        changed = True
        while changed:
            changed = False
            for i in range(len(merged)):
                for j in range(i + 1, len(merged)):
                    if not should_merge_coverage(merged[i], merged[j], exterior_y1):
                        continue
                    ax0, ay0, ax1, ay1 = polygon_bbox(merged[i])
                    bx0, by0, bx1, by1 = polygon_bbox(merged[j])
                    merged[i] = rect_from_bbox((
                        min(ax0, bx0),
                        min(ay0, by0),
                        max(ax1, bx1),
                        max(ay1, by1),
                    ))
                    merged.pop(j)
                    changed = True
                    break
                if changed:
                    break
        return merged

    def bbox_overlap_area(a, b):
        x_overlap = min(a[2], b[2]) - max(a[0], b[0])
        y_overlap = min(a[3], b[3]) - max(a[1], b[1])
        if x_overlap <= 0 or y_overlap <= 0:
            return 0
        return x_overlap * y_overlap

    def bbox_overlap_size(a, b):
        return (
            max(min(a[2], b[2]) - max(a[0], b[0]), 0),
            max(min(a[3], b[3]) - max(a[1], b[1]), 0),
        )

    def expand_to_adjacent_coverage_strips(items):
        if not items:
            return items
        original_bboxes = [polygon_bbox(item) for item in items]
        adjusted = []
        for index, item in enumerate(items):
            x0, y0, x1, y1 = polygon_bbox(item)
            for bx0_m, by0_m, bx1_m, by1_m in coverage_strip_bboxes:
                bx0, by0, bx1, by1 = bx0_m * 1000, by0_m * 1000, bx1_m * 1000, by1_m * 1000
                proposals = []
                if abs(bx1 - x0) <= 2 and overlap_1d(y0, y1, by0, by1, tol=1):
                    proposals.append((min(x0, bx0), y0, x1, y1))
                if abs(bx0 - x1) <= 2 and overlap_1d(y0, y1, by0, by1, tol=1):
                    proposals.append((x0, y0, max(x1, bx1), y1))
                if abs(by1 - y0) <= 2 and overlap_1d(x0, x1, bx0, bx1, tol=1):
                    proposals.append((x0, min(y0, by0), x1, y1))
                if abs(by0 - y1) <= 2 and overlap_1d(x0, x1, bx0, bx1, tol=1):
                    proposals.append((x0, y0, x1, max(y1, by1)))

                for proposal in proposals:
                    old_bbox = (x0, y0, x1, y1)
                    grows_into_other_coverage = False
                    for other_index, other_bbox in enumerate(original_bboxes):
                        if other_index == index:
                            continue
                        overlap_width, overlap_height = bbox_overlap_size(proposal, other_bbox)
                        if (
                            bbox_overlap_area(proposal, other_bbox) > bbox_overlap_area(old_bbox, other_bbox) + 10_000
                            and overlap_width > 300
                            and overlap_height > 300
                        ):
                            grows_into_other_coverage = True
                            break
                    if not grows_into_other_coverage:
                        x0, y0, x1, y1 = proposal
            adjusted.append(rect_from_bbox((x0, y0, x1, y1)))
        return adjusted

    def is_blocked(cx, cy):
        return any(
            point_in_polygon((cx, cy), poly) or point_on_polygon_boundary((cx, cy), poly, tol=0.001)
            for poly in blocker_polygons
        )

    width = len(xs) - 1
    height = len(ys) - 1
    free = set()
    for i in range(width):
        for j in range(height):
            cell_width = xs[i + 1] - xs[i]
            cell_height = ys[j + 1] - ys[j]
            if cell_width < 0.05 or cell_height < 0.05:
                continue
            cx = (xs[i] + xs[i + 1]) / 2
            cy = (ys[j] + ys[j + 1]) / 2
            if not is_blocked(cx, cy):
                free.add((i, j))

    def component_boundary(component):
        edges = {}

        def add_edge(a, b):
            opposite = (b, a)
            if opposite in edges:
                del edges[opposite]
            else:
                edges[(a, b)] = True

        for i, j in component:
            p0 = (xs[i], ys[j])
            p1 = (xs[i + 1], ys[j])
            p2 = (xs[i + 1], ys[j + 1])
            p3 = (xs[i], ys[j + 1])
            add_edge(p0, p1)
            add_edge(p1, p2)
            add_edge(p2, p3)
            add_edge(p3, p0)

        outgoing = defaultdict(list)
        for a, b in edges:
            outgoing[a].append(b)

        loops = []
        while outgoing:
            start = min(outgoing)
            current = start
            loop = [current]
            previous = None
            while True:
                candidates = outgoing.get(current)
                if not candidates:
                    break
                if previous is None or len(candidates) == 1:
                    nxt = candidates.pop(0)
                else:
                    nxt = min(
                        candidates,
                        key=lambda p: math.atan2(p[1] - current[1], p[0] - current[0]),
                    )
                    candidates.remove(nxt)
                if not candidates:
                    outgoing.pop(current, None)
                previous, current = current, nxt
                loop.append(current)
                if current == start:
                    break
            if len(loop) >= 4 and loop[0] == loop[-1]:
                loops.append(loop)

        if not loops:
            return None
        return max(loops, key=polygon_area)

    seen = set()
    footprints = []
    for cell in list(free):
        if cell in seen:
            continue
        queue = deque([cell])
        seen.add(cell)
        component = []
        touches_boundary = False
        while queue:
            i, j = queue.popleft()
            component.append((i, j))
            if i == 0 or j == 0 or i == width - 1 or j == height - 1:
                touches_boundary = True
            for neighbor in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if neighbor in free and neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

        area = sum((xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j]) for i, j in component)
        if area < min_area_sqm:
            continue

        boundary = component_boundary(component)
        if not boundary:
            continue
        if touches_boundary:
            continue
        footprints.append(expand_boundary_coverage([(x * 1000, y * 1000) for x, y in boundary]))

    raw_count = len(footprints)
    footprints = merge_coverage_footprints(footprints)
    footprints = expand_to_adjacent_coverage_strips(footprints)
    print(f"吊顶2D分区: 原始{raw_count}块，覆盖扩展后{len(footprints)}块")
    return sorted(footprints, key=lambda poly: (polygon_bbox(poly)[1], polygon_bbox(poly)[0]))


def create_clean_outline_from_wall_lines(data):
    points = [
        point
        for wall in data.get("walls", [])
        for point in (wall.get("start"), wall.get("end"))
        if point
    ]
    if not points:
        return None
    x0 = min(point[0] for point in points) * 0.001
    y0 = min(point[1] for point in points) * 0.001
    x1 = max(point[0] for point in points) * 0.001
    y1 = max(point[1] for point in points) * 0.001
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def create_outer_outline_from_wall_lines(data):
    clean_outline = create_clean_outline_from_wall_lines(data)
    wall_polygons = find_closed_polygons(data.get("walls", []))
    if wall_polygons and clean_outline:
        outer = max(wall_polygons, key=polygon_area)
        clean_area = polygon_area(clean_outline)
        outer_area = polygon_area([(x * 0.001, y * 0.001) for x, y in outer])
        if clean_area > 0 and outer_area >= clean_area * 0.6:
            return [(x * 0.001, y * 0.001) for x, y in outer], "closed_wall_outline"
    return clean_outline, "wall_bbox_fallback"


def rect_from_bbox_meters(bbox):
    x0, y0, x1, y1 = bbox
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def buffered_polygon_bbox(poly, buffer):
    x0, y0, x1, y1 = polygon_bbox(poly)
    return (x0 - buffer, y0 - buffer, x1 + buffer, y1 + buffer)


def simplify_orthogonal_polygon(points, tol=0.001):
    if len(points) >= 2 and points[0] == points[-1]:
        pts = points[:-1]
    else:
        pts = list(points)
    if len(pts) < 3:
        return points

    changed = True
    while changed and len(pts) >= 3:
        changed = False
        simplified = []
        for i, point in enumerate(pts):
            prev = pts[i - 1]
            nxt = pts[(i + 1) % len(pts)]
            collinear_x = abs(prev[0] - point[0]) <= tol and abs(point[0] - nxt[0]) <= tol
            collinear_y = abs(prev[1] - point[1]) <= tol and abs(point[1] - nxt[1]) <= tol
            if collinear_x or collinear_y:
                changed = True
                continue
            simplified.append(point)
        pts = simplified
    pts.append(pts[0])
    return pts


def structural_footprints_from_objects(
    data,
    objects,
    buffer=0.0,
    max_bbox_area=10.0,
    include_data_footprints=True,
):
    footprints = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        footprint = object_footprint_xy(obj)
        if not footprint:
            continue
        x0, y0, x1, y1 = polygon_bbox(footprint)
        if x1 - x0 <= 0.01 or y1 - y0 <= 0.01 or polygon_area(footprint) <= 0.0001:
            continue
        if buffer > 0:
            footprints.append(rect_from_bbox_meters(buffered_polygon_bbox(footprint, buffer)))
        else:
            footprints.append(simplify_orthogonal_polygon(footprint))

    if include_data_footprints:
        for key in ("columns", "shafts", "openings", "voids"):
            for item in data.get(key, []):
                bbox = item.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = [value * 0.001 for value in bbox]
                x0, y0, x1, y1 = x0 - buffer, y0 - buffer, x1 + buffer, y1 + buffer
                if x1 - x0 <= 0.01 or y1 - y0 <= 0.01:
                    continue
                if key == "columns" and (x1 - x0) * (y1 - y0) > max_bbox_area:
                    print(f"柱footprint: 跳过疑似聚合bbox，面积{(x1 - x0) * (y1 - y0):.1f}㎡")
                    continue
                footprints.append(rect_from_bbox_meters((x0, y0, x1, y1)))

    return footprints


def difference_footprints_from_outline(outline_polygon, structural_footprints, min_area_sqm=0.05):
    if not outline_polygon:
        return []

    outline_bbox = polygon_bbox(outline_polygon)
    xs = {round(outline_bbox[0], 6), round(outline_bbox[2], 6)}
    ys = {round(outline_bbox[1], 6), round(outline_bbox[3], 6)}
    anchor_xs = set()
    anchor_ys = set()
    for poly in structural_footprints:
        for x, y in poly:
            if outline_bbox[0] - 0.001 <= x <= outline_bbox[2] + 0.001:
                xs.add(round(x, 6))
                anchor_xs.add(round(x, 6))
            if outline_bbox[1] - 0.001 <= y <= outline_bbox[3] + 0.001:
                ys.add(round(y, 6))
                anchor_ys.add(round(y, 6))

    def snap_axis_values(values, anchors, tol=0.03):
        snapped = []
        cluster = []

        def flush_cluster():
            if not cluster:
                return
            midpoint = sum(cluster) / len(cluster)
            anchor_candidates = [value for value in cluster if value in anchors]
            if anchor_candidates:
                snapped.append(min(anchor_candidates, key=lambda value: abs(value - midpoint)))
            else:
                snapped.append(midpoint)

        for value in sorted(values):
            if cluster and abs(value - cluster[-1]) > tol:
                flush_cluster()
                cluster = [value]
            else:
                cluster.append(value)
        flush_cluster()
        return snapped

    xs = snap_axis_values(xs, anchor_xs)
    ys = snap_axis_values(ys, anchor_ys)
    if len(xs) < 2 or len(ys) < 2:
        return []

    def is_blocked(cx, cy):
        return any(
            point_in_polygon((cx, cy), poly) or point_on_polygon_boundary((cx, cy), poly, tol=0.001)
            for poly in structural_footprints
        )

    width = len(xs) - 1
    height = len(ys) - 1
    free = set()
    for i in range(width):
        for j in range(height):
            cell_width = xs[i + 1] - xs[i]
            cell_height = ys[j + 1] - ys[j]
            if cell_width < 0.02 or cell_height < 0.02:
                continue
            cx = (xs[i] + xs[i + 1]) / 2
            cy = (ys[j] + ys[j + 1]) / 2
            if not point_in_polygon((cx, cy), outline_polygon):
                continue
            if is_blocked(cx, cy):
                continue
            free.add((i, j))

    def component_boundary(component):
        edges = {}

        def add_edge(a, b):
            opposite = (b, a)
            if opposite in edges:
                del edges[opposite]
            else:
                edges[(a, b)] = True

        for i, j in component:
            p0 = (xs[i], ys[j])
            p1 = (xs[i + 1], ys[j])
            p2 = (xs[i + 1], ys[j + 1])
            p3 = (xs[i], ys[j + 1])
            add_edge(p0, p1)
            add_edge(p1, p2)
            add_edge(p2, p3)
            add_edge(p3, p0)

        outgoing = defaultdict(list)
        for a, b in edges:
            outgoing[a].append(b)

        loops = []
        while outgoing:
            start = min(outgoing)
            current = start
            loop = [current]
            while True:
                candidates = outgoing.get(current)
                if not candidates:
                    break
                nxt = candidates.pop(0)
                if not candidates:
                    outgoing.pop(current, None)
                current = nxt
                loop.append(current)
                if current == start:
                    break
            if len(loop) >= 4 and loop[0] == loop[-1]:
                loops.append(loop)
        return max(loops, key=polygon_area) if loops else None

    regions = []
    seen = set()
    for cell in list(free):
        if cell in seen:
            continue
        queue = deque([cell])
        seen.add(cell)
        component = []
        while queue:
            i, j = queue.popleft()
            component.append((i, j))
            for neighbor in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if neighbor in free and neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

        area = sum((xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j]) for i, j in component)
        if area < min_area_sqm:
            continue
        boundary = component_boundary(component)
        if boundary:
            regions.append(simplify_orthogonal_polygon(boundary))

    return sorted(regions, key=lambda poly: (polygon_bbox(poly)[1], polygon_bbox(poly)[0]))


def touches_outline_corner(region, outline_polygon, tol=0.02):
    rx0, ry0, rx1, ry1 = polygon_bbox(region)
    ox0, oy0, ox1, oy1 = polygon_bbox(outline_polygon)
    touches_left = abs(rx0 - ox0) <= tol
    touches_right = abs(rx1 - ox1) <= tol
    touches_bottom = abs(ry0 - oy0) <= tol
    touches_top = abs(ry1 - oy1) <= tol
    return (
        (touches_left and touches_top)
        or (touches_left and touches_bottom)
        or (touches_right and touches_top)
        or (touches_right and touches_bottom)
    )


def clean_ceiling_regions(
    regions,
    outline_polygon=None,
    outline_source=None,
    min_area_sqm=2.0,
    min_width=0.35,
):
    cleaned = []
    for region in regions:
        simplified = simplify_orthogonal_polygon(region)
        if polygon_area(simplified) < min_area_sqm:
            continue
        x0, y0, x1, y1 = polygon_bbox(simplified)
        if min(x1 - x0, y1 - y0) < min_width:
            continue
        if (
            outline_polygon
            and outline_source == "wall_bbox_fallback"
            and touches_outline_corner(simplified, outline_polygon)
        ):
            continue
        cleaned.append(simplified)
    return sorted(
        cleaned,
        key=lambda poly: (polygon_bbox(poly)[1], polygon_bbox(poly)[0]),
    )


def snap_polygons_to_footprint_edges(polygons, footprints, tol=0.005):
    anchor_xs = [x for footprint in footprints for x, _ in footprint]
    anchor_ys = [y for footprint in footprints for _, y in footprint]
    snapped = []
    for polygon in polygons:
        new_poly = []
        for x, y in polygon:
            nearby_x = [anchor for anchor in anchor_xs if abs(anchor - x) <= tol]
            nearby_y = [anchor for anchor in anchor_ys if abs(anchor - y) <= tol]
            if nearby_x:
                x = min(nearby_x, key=lambda anchor: abs(anchor - x))
            if nearby_y:
                y = min(nearby_y, key=lambda anchor: abs(anchor - y))
            new_poly.append((x, y))
        snapped.append(simplify_orthogonal_polygon(new_poly))
    return snapped


def ceiling_height_for_region(region, ceiling_markers, fallback_height):
    if not ceiling_markers:
        return fallback_height

    marker_heights = [
        height
        for x_mm, y_mm, height in ceiling_markers
        if point_in_polygon((x_mm * 0.001, y_mm * 0.001), region)
    ]
    if marker_heights:
        return min(marker_heights)

    cx, cy = polygon_center(region)
    return min(
        ceiling_markers,
        key=lambda marker: (cx - marker[0] * 0.001) ** 2 + (cy - marker[1] * 0.001) ** 2,
    )[2]


def create_structural_difference_ceiling_regions(
    data,
    outline_polygons,
    outline_source,
    structural_objects,
    ceiling_markers,
    mat,
    collection,
    wall_height,
):
    if not outline_polygons:
        return []
    if outline_polygons and isinstance(outline_polygons[0][0], (int, float)):
        outline_polygons = [outline_polygons]

    beam_bottoms = []
    for obj in structural_objects:
        if obj.type != "MESH" or not obj.name.startswith("梁"):
            continue
        zs = [(obj.matrix_world @ vert.co).z for vert in obj.data.vertices]
        if zs:
            beam_bottoms.append(min(zs))
    fallback_z_base = min(beam_bottoms) if beam_bottoms else wall_height - 0.4

    structural_footprints = structural_footprints_from_objects(data, structural_objects)
    beam_footprints = structural_footprints_from_objects(
        data,
        [obj for obj in structural_objects if obj.name.startswith("梁")],
        include_data_footprints=False,
    )
    raw_regions = []
    for outline_polygon in outline_polygons:
        raw_regions.extend(difference_footprints_from_outline(outline_polygon, structural_footprints))
    regions = clean_ceiling_regions(raw_regions)
    regions = snap_polygons_to_footprint_edges(regions, beam_footprints)

    objects = []
    for i, region in enumerate(regions):
        ceiling_height = ceiling_height_for_region(
            region,
            ceiling_markers,
            round(fallback_z_base * 1000),
        )
        z_base = ceiling_height * 0.001
        block_height = wall_height - z_base
        if block_height <= 0.02:
            print(f"布尔吊顶块{i:03d}: 跳过，CH={ceiling_height}mm 已到顶")
            continue
        obj = create_extruded_polygon_meters(
            region,
            block_height,
            f"布尔吊顶块{i:03d}",
            mat,
            z_base=z_base,
        )
        if not obj:
            continue
        collection.objects.link(obj)
        objects.append(obj)
        print(f"布尔吊顶块{i:03d}: CH={ceiling_height}mm, Z={z_base:.3f}..{wall_height:.3f}")

    print(
        f"2D结构差集吊顶块: 外轮廓{len(outline_polygons)}个({outline_source}), 结构footprint{len(structural_footprints)}个, "
        f"原始{len(raw_regions)}块, 清理后{len(objects)}块"
    )
    return objects


def create_space_outlines_from_boundary_objects(data, boundary_objects):
    search_outline, outline_source = create_outer_outline_from_wall_lines(data)
    if not search_outline:
        return [], outline_source

    boundary_footprints = structural_footprints_from_objects(
        data,
        boundary_objects,
        include_data_footprints=False,
    )
    raw_spaces = difference_footprints_from_outline(search_outline, boundary_footprints)
    spaces = clean_ceiling_regions(
        raw_spaces,
        outline_polygon=search_outline,
        outline_source=outline_source,
        min_area_sqm=0.5,
        min_width=0.35,
    )
    if not spaces:
        return [search_outline], outline_source
    return spaces, f"space_outlines_from_{outline_source}"


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


def apply_window_openings(wall_objects, windows, collection, wall_height, mat, wall_lines=None):
    infill_objects = []
    for i, win in enumerate(windows):
        length = win.get("opening_length")
        sill = win.get("sill_height")
        window_height = win.get("window_height")
        if not length or sill is None or window_height is None or window_height <= 0:
            print(f"窗洞{i}: 跳过，缺少尺寸/高度")
            continue

        wall_match = match_window_end_walls(win, wall_lines or [])
        if not wall_match:
            wall_match = match_window_wall_bbox(win, wall_objects)
        boxes = create_window_infill_boxes(win, wall_height, mat, wall_match)
        for j, box in enumerate(boxes):
            box.name = f"窗洞{i:03d}_{box.name}_{j:03d}"
            collection.objects.link(box)
            box.scale = (0.001, 0.001, 0.001)
            snap_box_xy_to_walls(box, wall_objects)
            infill_objects.append(box)

        if wall_match:
            print(
                f"窗洞{i}: 长{length}mm, 窗台{sill}mm, 窗高{window_height}mm, "
                f"墙厚{wall_match['depth']:.0f}mm, 补{len(boxes)}块窗上下墙"
            )
        else:
            print(
                f"窗洞{i}: 长{length}mm, 窗台{sill}mm, 窗高{window_height}mm, "
                f"未匹配墙体，按默认厚度补{len(boxes)}块窗上下墙"
            )

    return 0, infill_objects


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
            snap_box_xy_to_walls(
                obj,
                wall_objects,
                tol=0.06,
                constrain_bbox=bbox_from_xy_points(footprint[:-1], scale=0.001, margin=0.06),
            )
        extend_beam_ends_to_walls(obj, wall_objects)
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
        beam_bottom = beam_bottom_for([beam])
        if beam_bottom is None:
            skipped_unpaired += 1
            print(f"梁{original_idx}: 跳过，未配对且缺少梁底标高")
            continue
        beam_width = beam_width_for([beam]) or 240
        beam_depth = wall_height - beam_bottom
        if beam_depth <= 20:
            skipped_unpaired += 1
            print(f"梁{original_idx}: 跳过，梁底标高{beam_bottom}mm已到顶")
            continue
        obj = create_single_line_beam_box(
            beam["start"],
            beam["end"],
            beam_width,
            beam_depth,
            f"梁{original_idx:03d}",
            mat,
            wall_objects,
        )
        if not obj:
            skipped_unpaired += 1
            print(f"梁{original_idx}: 跳过，缺少可建模几何")
            continue
        for vert in obj.data.vertices:
            vert.co.z += beam_bottom
        obj.data.update()
        collection.objects.link(obj)
        obj.scale = (0.001, 0.001, 0.001)
        snap_single_line_beam_ends(obj, wall_objects + objects, tol=0.02)
        snap_single_line_beam_far_side(obj, wall_objects + objects, tol=0.02)
        extend_beam_ends_to_walls(obj, wall_objects)
        obj["single_line_beam_fallback"] = True
        objects.append(obj)
        print(f"梁{original_idx}: 未配对单线兜底，梁底标高{beam_bottom}mm, 梁厚{beam_depth:.0f}mm, 梁宽{beam_width}mm")

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

    for obj in objects:
        if obj.get("single_line_beam_fallback"):
            continue
        peers = [peer for peer in objects if peer != obj]
        if peers:
            snap_box_xy_to_walls(
                obj,
                peers,
                tol=0.06,
                constrain_bbox=expand_bbox(object_bbox_xy(obj), 0.06),
            )
        snap_box_xy_to_walls(
            obj,
            wall_objects,
            tol=0.06,
            constrain_bbox=expand_bbox(object_bbox_xy(obj), 0.06),
        )

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
    boolean_regions_only = "boolean_regions" in os.path.basename(blend_path)

    print(f"加载: {json_path}")
    data = load_json(json_path)
    setup_scene()

    mat_model = bpy.data.materials.new("白模")
    mat_model.diffuse_color = (0.65, 0.65, 0.65, 1.0)
    mat_boolean = bpy.data.materials.new("布尔吊顶测试")
    mat_boolean.diffuse_color = (0.2, 0.55, 0.95, 1.0)

    heights = data.get("ceiling_heights", [2800])
    avg_h = round(max(heights)) + 100
    ceiling_markers = collect_ceiling_markers(data)
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
        # 移除与更大围合重叠的小围合（柱内碎面/分割面）
        # 规则：bbox 重叠面积超过小围合 50% → 移除小围合
        polys_info = [(p, polygon_area(p), polygon_bbox(p)) for p in polys]
        polys_info.sort(key=lambda x: -x[1])  # 面积从大到小
        filtered = []
        for i, (poly, area_i, bbox_i) in enumerate(polys_info):
            dominated = False
            for j, (_, area_j, bbox_j) in enumerate(polys_info):
                if i == j or area_j <= area_i:
                    continue
                overlap_x = max(0, min(bbox_i[2], bbox_j[2]) - max(bbox_i[0], bbox_j[0]))
                overlap_y = max(0, min(bbox_i[3], bbox_j[3]) - max(bbox_i[1], bbox_j[1]))
                if overlap_x * overlap_y > area_i * 0.5:
                    dominated = True
                    break
            if not dominated:
                filtered.append(poly)
        polys = filtered
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

    _, window_infill = apply_window_openings(
        wall_objects,
        data.get("windows", []),
        collection,
        avg_h,
        mat_model,
        data.get("walls", []),
    )
    total += len(window_infill)
    wall_objects.extend(window_infill)
    print(f"窗洞上下墙: 补{len(window_infill)}块")

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

    if boolean_regions_only:
        structural_objects = wall_objects + door_headers + beam_objects
        boundary_objects = wall_objects + door_headers
        outer_outlines, outline_source = create_space_outlines_from_boundary_objects(
            data,
            boundary_objects,
        )
        boolean_regions = create_structural_difference_ceiling_regions(
            data,
            outer_outlines,
            outline_source,
            structural_objects,
            ceiling_markers,
            mat_boolean,
            collection,
            wall_height=avg_h * 0.001,
        )
        total += len(boolean_regions)
    else:
        ceiling_footprints = ceiling_footprints_from_model_objects(
            wall_objects + beam_objects,
            ceiling_markers,
        )
        ceiling_drop_objects = create_ceiling_drop_boxes(
            ceiling_markers,
            avg_h,
            wall_objects,
            mat_model,
            collection,
            footprints=ceiling_footprints,
        )
        total += len(ceiling_drop_objects)
        print(f"层高块: 建模{len(ceiling_drop_objects)}块")

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
