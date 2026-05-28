#!/usr/bin/env python3
"""
CAD → JSON 解析脚本
从室内设计 CAD (.dxf) 中提取墙体、窗户、柱子、标注等信息，输出结构化 JSON。

用法: python3 cad_parser.py <input.dxf> [output.json]

设计标准（基于嘉遇的绘图规范）:
  - BS-墙: 墙体线段
  - BS-窗: 窗户块 (命名: hlw_编号_长度_宽度)
  - BS-柱: 柱子
  - BS-梁: 梁
  - FF-门: 门
  - DS-半高隔断: 半高隔断
  - SH-尺寸: 尺寸标注
  - SH-文字: 立面标注 (窗台高/窗高/层高等)
"""

import ezdxf
import json
import re
import math
import sys
from collections import Counter, defaultdict


def parse_dxf(filepath: str) -> dict:
    """解析 DXF 文件，返回结构化数据"""
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()

    result = {
        "meta": {
            "source_file": filepath,
            "layers": [layer.dxf.name for layer in doc.layers],
        },
        "walls": [],
        "windows": [],
        "doors": [],
        "columns": [],
        "beams": [],
        "partitions": [],   # 半高隔断
        "dimensions": [],
        "annotations": [],
        "ceiling_heights": [],
        "raw_texts": [],     # 保留原始文本，方便调试
    }

    # ============================================================
    # 1. 墙体 (BS-墙) - LINE + LWPOLYLINE 实体
    # ============================================================
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-墙":
            s, end = e.dxf.start, e.dxf.end
            length = math.sqrt((s.x - end.x)**2 + (s.y - end.y)**2)
            result["walls"].append({
                "type": "wall",
                "layer": "BS-墙",
                "demolishable": True,
                "start": [round(s.x, 1), round(s.y, 1)],
                "end": [round(end.x, 1), round(end.y, 1)],
                "length": round(length, 1),
            })
    # LWPOLYLINE（闭合矩形墙体）
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "BS-墙":
            pts = [(round(p[0], 1), round(p[1], 1)) for p in e.get_points(format="xy")]
            for k in range(len(pts)):
                s = pts[k]
                end = pts[(k + 1) % len(pts)]
                length = math.sqrt((s[0]-end[0])**2 + (s[1]-end[1])**2)
                if length > 0:
                    result["walls"].append({
                        "type": "wall",
                        "layer": "BS-墙",
                        "demolishable": True,
                        "start": list(s),
                        "end": list(end),
                        "length": round(length, 1),
                    })

    # ============================================================
    # 2. 窗户 (BS-窗) - INSERT 块引用
    #    块名格式: hlw_编号_长度_宽度
    # ============================================================
    window_blocks = {}
    for e in msp.query("INSERT"):
        if e.dxf.layer == "BS-窗":
            name = e.dxf.name
            pos = e.dxf.insert
            # 解析块名
            parsed = _parse_window_block_name(name)
            win = {
                "type": "window",
                "layer": "BS-窗",
                "block_name": name,
                "position": [round(pos.x, 1), round(pos.y, 1)],
                "opening_length": parsed.get("opening_length"),
                "frame_width": parsed.get("frame_width"),
                "rotation": round(e.dxf.rotation, 1) if e.dxf.rotation else 0,
                # 以下字段待关联 SH-文字 后填充
                "sill_height": None,
                "window_height": None,
                "annotations": [],
            }
            result["windows"].append(win)
            window_blocks[name] = win

    window_lines = []
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-窗":
            s, end = e.dxf.start, e.dxf.end
            window_lines.append({
                "start": (round(s.x, 1), round(s.y, 1)),
                "end": (round(end.x, 1), round(end.y, 1)),
            })
    result["windows"].extend(_pair_lines_to_window_openings(window_lines))

    window_polys = []
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "BS-窗":
            pts = [(round(float(p[0]), 1), round(float(p[1]), 1)) for p in e.get_points(format="xy")]
            window_polys.append({
                "points": pts,
                "closed": bool(e.closed),
            })
    result["windows"].extend(_polylines_to_window_openings(window_polys))

    # ============================================================
    # 3. 门 (FF-门) - INSERT 块引用 或 直接画的 LWPOLYLINE/ARC
    # ============================================================
    for e in msp.query("INSERT"):
        if e.dxf.layer == "FF-门":
            pos = e.dxf.insert
            result["doors"].append({
                "type": "door",
                "layer": "FF-门",
                "representation": "block",
                "block_name": e.dxf.name,
                "position": [round(pos.x, 1), round(pos.y, 1)],
                "rotation": round(e.dxf.rotation, 1) if e.dxf.rotation else 0,
                "door_height": None,
                "door_width": None,
                "annotations": [],
            })
    # 承重墙线段 (BS-柱) - LINE + LWPOLYLINE
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-柱":
            s, end = e.dxf.start, e.dxf.end
            length = math.sqrt((s.x - end.x)**2 + (s.y - end.y)**2)
            result["walls"].append({
                "type": "wall",
                "layer": "BS-柱",
                "demolishable": False,
                "start": [round(s.x, 1), round(s.y, 1)],
                "end": [round(end.x, 1), round(end.y, 1)],
                "length": round(length, 1),
            })
    # BS-柱 的 LWPOLYLINE 也当承重墙
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "BS-柱":
            pts = [(round(p[0], 1), round(p[1], 1)) for p in e.get_points(format="xy")]
            for k in range(len(pts)):
                s = pts[k]
                end = pts[(k + 1) % len(pts)]
                length = math.sqrt((s[0]-end[0])**2 + (s[1]-end[1])**2)
                if length > 0:
                    result["walls"].append({
                        "type": "wall",
                        "layer": "BS-柱",
                        "demolishable": False,
                        "start": list(s),
                        "end": list(end),
                        "length": round(length, 1),
                    })

    # FF-门 和 BS-备用 都不直接作为门洞来源。
    # 门洞只由 MH 标注 + 墙体缺口推断（见 _infer_doors_from_mh_annotations）。

    # ============================================================
    # 4. 柱子/承重墙 (BS-柱) - 不可拆墙体，LINE 实体
    #    BS-柱 = 承重墙（不能拆），BS-墙 = 非承重墙（可以拆）
    # ============================================================
    col_lines = []
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-柱":
            s, end = e.dxf.start, e.dxf.end
            col_lines.append({
                "start": (round(s.x, 1), round(s.y, 1)),
                "end": (round(end.x, 1), round(end.y, 1)),
            })
    # 合并相邻线段为柱子矩形
    result["columns"] = _merge_lines_to_columns(col_lines)

    # ============================================================
    # 5. 梁 (BS-梁)
    # ============================================================
    beam_lines = []
    beam_polylines = []
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-梁":
            s, end = e.dxf.start, e.dxf.end
            beam_lines.append(((s.x, s.y), (end.x, end.y), "direct"))
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "BS-梁":
            pts = [(float(p[0]), float(p[1])) for p in e.get_points(format="xy")]
            beam_polylines.append((pts, "direct"))

    for e in msp.query("INSERT"):
        if e.dxf.layer != "BS-梁":
            continue
        if e.dxf.name not in doc.blocks:
            continue
        for ent in doc.blocks[e.dxf.name]:
            if ent.dxf.layer != "BS-梁":
                continue
            if ent.dxftype() == "LINE":
                s = _transform_insert_point(ent.dxf.start.x, ent.dxf.start.y, e)
                end = _transform_insert_point(ent.dxf.end.x, ent.dxf.end.y, e)
                beam_lines.append((s, end, f"block:{e.dxf.name}"))
            elif ent.dxftype() == "LWPOLYLINE":
                pts = [
                    _transform_insert_point(float(p[0]), float(p[1]), e)
                    for p in ent.get_points(format="xy")
                ]
                beam_polylines.append((pts, f"block:{e.dxf.name}"))

    for s, end, source in beam_lines:
        length = math.sqrt((s[0] - end[0])**2 + (s[1] - end[1])**2)
        result["beams"].append({
            "type": "beam",
            "layer": "BS-梁",
            "source": source,
            "start": [round(s[0], 1), round(s[1], 1)],
            "end": [round(end[0], 1), round(end[1], 1)],
            "length": round(length, 1),
            "beam_height": None,
            "beam_width": None,
            "annotations": [],
        })

    for pts, source in beam_polylines:
        result["beams"].append({
            "type": "beam",
            "layer": "BS-梁",
            "source": source,
            "polyline": [(round(p[0], 1), round(p[1], 1)) for p in pts],
            "beam_height": None,
            "beam_width": None,
            "annotations": [],
        })

    # ============================================================
    # 6. 半高隔断 (DS-半高隔断)
    # ============================================================
    for e in msp.query("LINE"):
        if e.dxf.layer == "DS-半高隔断":
            s, end = e.dxf.start, e.dxf.end
            length = math.sqrt((s.x - end.x)**2 + (s.y - end.y)**2)
            result["partitions"].append({
                "type": "partition",
                "layer": "DS-半高隔断",
                "start": [round(s.x, 1), round(s.y, 1)],
                "end": [round(end.x, 1), round(end.y, 1)],
                "length": round(length, 1),
            })

    # ============================================================
    # 7. 尺寸标注 (SH-尺寸)
    # ============================================================
    for e in msp.query("DIMENSION"):
        if e.dxf.layer == "SH-尺寸":
            try:
                val = e.dxf.actual_measurement
            except:
                val = None
            result["dimensions"].append({
                "type": "dimension",
                "layer": "SH-尺寸",
                "value": round(val, 1) if val is not None else None,
                "defpoint": [round(e.dxf.defpoint.x, 1), round(e.dxf.defpoint.y, 1)],
            })

    # ============================================================
    # 8. 立面标注 (SH-文字) - 在块定义中
    #    模式: H1窗台：xxx, H2窗：xxx, CH=xxx, MH:xxx, H=xxx, W=xxx
    # ============================================================
    annotation_patterns = {
        "sill_height": re.compile(r"H1窗台[：:]\s*(\d+)"),
        "sill_height_alt": re.compile(r"H1地台[：:]\s*(\d+)"),
        "window_height": re.compile(r"H2窗[：:]\s*(\d+)"),
        "window_height_1": re.compile(r"H1窗[：:]\s*(\d+)"),
        "ceiling_height": re.compile(r"CH\s*=\s*(\d+)"),
        "door_height": re.compile(r"MH\s*[：:=]\s*(\d+)"),
        "beam_height": re.compile(r"(?<![\w])H\s*=\s*(\d+)"),
        "beam_width": re.compile(r"(?<![\w])W\s*=\s*(\d+)"),
    }

    # 遍历标注图层中的块（SH-文字 + EL-通用）
    # 关键：每个标注文本在块内有独立坐标，加上块插入点得到绝对坐标
    ANNOTATION_LAYERS = {"SH-文字", "EL-通用"}
    for e in msp.query("INSERT"):
        if e.dxf.layer in ANNOTATION_LAYERS:
            block_insert = e.dxf.insert  # 块的插入点（绝对坐标）
            block_name = e.dxf.name
            # 读取块定义中的文字及其局部坐标
            if block_name in doc.blocks:
                blk = doc.blocks[block_name]
                for ent in blk:
                    text = None
                    local_pos = None
                    if ent.dxftype() == "TEXT":
                        text = ent.dxf.text.strip()
                        local_pos = ent.dxf.insert
                    elif ent.dxftype() == "MTEXT":
                        try:
                            text = ent.plain_text()[:200].strip()
                        except:
                            continue
                        local_pos = ent.dxf.insert

                    if not text or not local_pos:
                        continue

                    # 计算绝对坐标
                    abs_x = round(block_insert.x + local_pos.x, 1)
                    abs_y = round(block_insert.y + local_pos.y, 1)

                    result["raw_texts"].append({
                        "text": text,
                        "block": block_name,
                        "position": [abs_x, abs_y],
                        "layer": "SH-文字",
                    })

                    # 用正则解析
                    parsed = {}
                    for key, pattern in annotation_patterns.items():
                        m = pattern.search(text)
                        if m:
                            parsed[key] = [int(m.group(1))]

                    if parsed:
                        ann_entry = {
                            "type": "annotation",
                            "layer": "SH-文字",
                            "block_name": block_name,
                            "position": [abs_x, abs_y],
                            "parsed": parsed,
                            "raw_texts": [text],
                        }
                        result["annotations"].append(ann_entry)

                        # 收集层高
                        for ch in parsed.get("ceiling_height", []):
                            result["ceiling_heights"].append(ch)

    # ============================================================
    # 9. 将标注关联到窗户（按最近距离）
    # ============================================================
    _infer_doors_from_mh_annotations(result)
    _associate_annotations(result)
    _propagate_beam_annotations(result)

    return result


def _parse_window_block_name(name: str) -> dict:
    """解析窗块名: hlw_编号_长度_宽度"""
    m = re.match(r"hlw_\d+_(\d+)_(\d+)", name)
    if m:
        return {
            "opening_length": int(m.group(1)),
            "frame_width": int(m.group(2)),
        }
    return {}


def _transform_insert_point(x: float, y: float, insert) -> tuple[float, float]:
    """Transform a block-local XY point into modelspace for simple INSERT cases."""
    sx = getattr(insert.dxf, "xscale", 1) or 1
    sy = getattr(insert.dxf, "yscale", 1) or 1
    angle = math.radians(getattr(insert.dxf, "rotation", 0) or 0)
    px = x * sx
    py = y * sy
    rx = px * math.cos(angle) - py * math.sin(angle)
    ry = px * math.sin(angle) + py * math.cos(angle)
    return insert.dxf.insert.x + rx, insert.dxf.insert.y + ry


def _merge_lines_to_columns(lines: list) -> list:
    """将柱子的线段合并为矩形（简化版：用边界框）"""
    if not lines:
        return []

    # 收集所有端点
    all_points = []
    for l in lines:
        all_points.append(l["start"])
        all_points.append(l["end"])

    if not all_points:
        return []

    # 简单聚类：找到不连续的点集分组
    # 这里用简化方法：直接算整体边界框
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]

    # 如果所有点集中在一个区域，认为是一个柱子
    # 否则需要聚类
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # 简化输出：整个区域作为一个柱子区域
    # 实际项目中可能需要聚类为多个柱子
    return [{
        "type": "column",
        "layer": "BS-柱",
        "bbox": [round(x_min, 1), round(y_min, 1), round(x_max, 1), round(y_max, 1)],
        "width": round(x_max - x_min, 1),
        "depth": round(y_max - y_min, 1),
        "line_count": len(lines),
    }]


def _line_info(line: dict) -> dict | None:
    sx, sy = line["start"]
    ex, ey = line["end"]
    dx = ex - sx
    dy = ey - sy
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    return {
        "line": line,
        "length": length,
        "dir": (dx / length, dy / length),
        "normal": (-dy / length, dx / length),
        "mid": ((sx + ex) / 2, (sy + ey) / 2),
        "points": [(sx, sy), (ex, ey)],
    }


def _pair_lines_to_door_openings(lines: list) -> list:
    infos = [_line_info(line) for line in lines]
    candidates = []
    for i, a in enumerate(infos):
        if not a:
            continue
        for j in range(i + 1, len(infos)):
            b = infos[j]
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
            distance = abs((b["mid"][0] - a["mid"][0]) * nx + (b["mid"][1] - a["mid"][1]) * ny)
            if overlap < 500 or not (80 <= distance <= 400):
                continue
            candidates.append((distance - overlap * 0.001, i, j))

    used = set()
    openings = []
    for _, i, j in sorted(candidates):
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        pts = infos[i]["points"] + infos[j]["points"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        openings.append({
            "type": "door",
            "layer": "BS-备用",
            "representation": "opening_lines",
            "position": [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)],
            "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "width": round(max(max(xs) - min(xs), max(ys) - min(ys)), 1),
            "depth": round(min(max(xs) - min(xs), max(ys) - min(ys)), 1),
            "points": pts,
            "door_height": None,
            "door_width": None,
            "annotations": [],
        })
    return openings


def _arc_swing_to_door_openings(arcs: list, small_rects: list) -> list:
    openings = []
    for arc in arcs:
        cx, cy = arc["center"]
        radius = arc["radius"]
        endpoints = []
        for angle in (arc["start_angle"], arc["end_angle"]):
            rad = math.radians(angle)
            endpoints.append((cx + math.cos(rad) * radius, cy + math.sin(rad) * radius))

        best_endpoint = None
        best_score = None
        for ex, ey in endpoints:
            score = 0
            for rect in small_rects:
                rx, ry = rect["position"]
                if math.hypot(rx - cx, ry - cy) <= 160:
                    score += 1
                if math.hypot(rx - ex, ry - ey) <= 160:
                    score += 1
            score_tuple = (score, abs(ey - cy), abs(ex - cx))
            if best_score is None or score_tuple > best_score:
                best_score = score_tuple
                best_endpoint = (ex, ey)

        if best_endpoint is None:
            continue

        ex, ey = best_endpoint
        horizontal = abs(ex - cx) >= abs(ey - cy)
        matching_rects = [
            rect for rect in small_rects
            if (
                math.hypot(rect["position"][0] - cx, rect["position"][1] - cy) <= 180
                or math.hypot(rect["position"][0] - ex, rect["position"][1] - ey) <= 180
            )
        ]

        if horizontal:
            y_values = []
            for rect in matching_rects:
                y_values.extend([rect["bbox"][1], rect["bbox"][3]])
            depth = max(y_values) - min(y_values) if y_values else 60
            y0 = min(y_values) if y_values else cy - depth / 2
            y1 = max(y_values) if y_values else cy + depth / 2
            x0, x1 = sorted([cx, ex])
        else:
            x_values = []
            for rect in matching_rects:
                x_values.extend([rect["bbox"][0], rect["bbox"][2]])
            depth = max(x_values) - min(x_values) if x_values else 60
            x0 = min(x_values) if x_values else cx - depth / 2
            x1 = max(x_values) if x_values else cx + depth / 2
            y0, y1 = sorted([cy, ey])

        width = round(max(x1 - x0, y1 - y0), 1)
        depth = round(min(x1 - x0, y1 - y0), 1)
        openings.append({
            "type": "door",
            "layer": "FF-门",
            "representation": "arc_opening",
            "position": [round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)],
            "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
            "width": width,
            "depth": depth,
            "door_height": None,
            "door_width": None,
            "annotations": [],
            "arc": {
                "center": [round(cx, 1), round(cy, 1)],
                "radius": round(radius, 1),
                "start_angle": round(arc["start_angle"], 1),
                "end_angle": round(arc["end_angle"], 1),
            },
        })
    return openings


def _infer_doors_from_mh_annotations(result: dict):
    """Infer door openings from MH door-height annotations and nearby wall gaps.

    For each MH annotation, find the nearest wall gap. If an existing door
    already overlaps that gap, just update its door_height. Otherwise create
    a new door entry.
    """
    mh_annotations = [
        ann for ann in result["annotations"]
        if ann.get("parsed", {}).get("door_height")
    ]
    if not mh_annotations:
        return

    for ann in mh_annotations:
        opening = _find_wall_gap_near_point(result["walls"], ann["position"])
        if not opening:
            continue

        door_height = ann["parsed"]["door_height"][0]
        raw_text = ann["raw_texts"][0]

        # Check if an existing door already covers this gap
        merged = False
        for door in result["doors"]:
            if not door.get("bbox"):
                continue
            if _bboxes_overlap(door["bbox"], opening["bbox"], margin=200):
                door["door_height"] = door_height
                if raw_text not in door.get("annotations", []):
                    door.setdefault("annotations", []).append(raw_text)
                merged = True
                break

        if not merged:
            opening["type"] = "door"
            opening["layer"] = "MH"
            opening["representation"] = "mh_wall_gap"
            opening["door_height"] = door_height
            opening["door_width"] = opening["width"]
            opening["annotations"] = [raw_text]
            result["doors"].append(opening)


def _bboxes_overlap(a: list, b: list, margin: float = 0) -> bool:
    """Check if two bboxes [x0,y0,x1,y1] overlap (with optional margin)."""
    return not (
        a[2] + margin < b[0] - margin
        or b[2] + margin < a[0] - margin
        or a[3] + margin < b[1] - margin
        or b[3] + margin < a[1] - margin
    )


def _find_wall_gap_near_point(walls: list, point: list) -> dict | None:
    ax, ay = point
    best = None
    for horizontal in (True, False):
        openings = _wall_gap_candidates(walls, horizontal)
        for opening in openings:
            x0, y0, x1, y1 = opening["bbox"]
            cx, cy = opening["position"]
            dx = max(x0 - ax, 0, ax - x1)
            dy = max(y0 - ay, 0, ay - y1)
            outside = math.hypot(dx, dy)
            center_dist = math.hypot(cx - ax, cy - ay)
            if outside > 2500 and center_dist > 2500:
                continue
            score = (outside, center_dist, -opening["width"])
            if best is None or score < best[0]:
                best = (score, opening)
    return best[1] if best else None


def _wall_gap_candidates(walls: list, horizontal: bool) -> list:
    segments = []
    for wall in walls:
        sx, sy = wall["start"]
        ex, ey = wall["end"]
        dx = ex - sx
        dy = ey - sy
        if horizontal:
            if abs(dy) > 5 or abs(dx) < 100:
                continue
            cross = round((sy + ey) / 2 / 10) * 10
            start, end = sorted([sx, ex])
        else:
            if abs(dx) > 5 or abs(dy) < 100:
                continue
            cross = round((sx + ex) / 2 / 10) * 10
            start, end = sorted([sy, ey])
        segments.append((cross, start, end))

    by_cross = defaultdict(list)
    for cross, start, end in segments:
        by_cross[cross].append((start, end))

    candidates = []
    for cross, spans in by_cross.items():
        spans = sorted(spans)
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            gap = b0 - a1
            if not (500 <= gap <= 1600):
                continue
            # The opposing wall face should have a nearby parallel span.
            depth = None
            for other_cross, other_spans in by_cross.items():
                cross_gap = abs(other_cross - cross)
                if not (40 <= cross_gap <= 400):
                    continue
                for c0, c1 in other_spans:
                    if min(a1, b0) <= max(c0, c1) and max(a1, b0) >= min(c0, c1):
                        depth = cross_gap if depth is None else min(depth, cross_gap)
            if depth is None:
                depth = 200
            if horizontal:
                bbox = [round(a1, 1), round(cross - depth / 2, 1), round(b0, 1), round(cross + depth / 2, 1)]
            else:
                bbox = [round(cross - depth / 2, 1), round(a1, 1), round(cross + depth / 2, 1), round(b0, 1)]
            candidates.append({
                "position": [round((bbox[0] + bbox[2]) / 2, 1), round((bbox[1] + bbox[3]) / 2, 1)],
                "bbox": bbox,
                "width": round(gap, 1),
                "depth": round(depth, 1),
            })
    return candidates


def _pair_lines_to_window_openings(lines: list) -> list:
    infos = [_line_info(line) for line in lines]
    candidates = []
    for i, a in enumerate(infos):
        if not a:
            continue
        for j in range(i + 1, len(infos)):
            b = infos[j]
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
            distance = abs((b["mid"][0] - a["mid"][0]) * nx + (b["mid"][1] - a["mid"][1]) * ny)
            if overlap < 400 or not (80 <= distance <= 500):
                continue
            candidates.append((distance - overlap * 0.001, i, j))

    used = set()
    openings = []
    for _, i, j in sorted(candidates):
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        pts = infos[i]["points"] + infos[j]["points"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ux, uy = infos[i]["dir"]
        rotation = round(math.degrees(math.atan2(uy, ux)), 1)
        openings.append({
            "type": "window",
            "layer": "BS-窗",
            "representation": "line_pair",
            "position": [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)],
            "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "opening_length": round(max(max(xs) - min(xs), max(ys) - min(ys)), 1),
            "frame_width": round(min(max(xs) - min(xs), max(ys) - min(ys)), 1),
            "rotation": rotation,
            "sill_height": None,
            "window_height": None,
            "annotations": [],
            "points": pts,
        })
    return openings


def _polylines_to_window_openings(polylines: list) -> list:
    """Convert BS-窗 closed rectangular polylines into window openings.

    Some CAD files draw each window as two concentric rectangles: a wall-depth
    opening, often about 200mm deep, plus a thinner frame line, often about 40mm.
    Group rectangles that share center and long-span so each physical window is
    returned once.
    """
    rects = []
    for poly in polylines:
        pts = poly.get("points") or []
        if not poly.get("closed") or len(pts) < 4:
            continue

        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        width = x1 - x0
        height = y1 - y0
        long_span = max(width, height)
        short_span = min(width, height)

        if long_span < 400 or short_span <= 0 or short_span > 600:
            continue

        rects.append({
            "points": pts,
            "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
            "position": [round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)],
            "opening_length": round(long_span, 1),
            "frame_width": round(short_span, 1),
            "rotation": 0 if width >= height else 90,
        })

    groups = []
    for rect in rects:
        matched = None
        rx, ry = rect["position"]
        for group in groups:
            gx, gy = group[0]["position"]
            if (
                abs(rx - gx) <= 5
                and abs(ry - gy) <= 5
                and abs(rect["opening_length"] - group[0]["opening_length"]) <= 5
                and rect["rotation"] == group[0]["rotation"]
            ):
                matched = group
                break
        if matched is None:
            groups.append([rect])
        else:
            matched.append(rect)

    windows = []
    for group in groups:
        opening = max(group, key=lambda r: r["frame_width"])
        frame = min(group, key=lambda r: r["frame_width"])
        win = {
            "type": "window",
            "layer": "BS-窗",
            "representation": "polyline_rect",
            "position": opening["position"],
            "bbox": opening["bbox"],
            "opening_length": opening["opening_length"],
            "frame_width": opening["frame_width"],
            "rotation": opening["rotation"],
            "sill_height": None,
            "window_height": None,
            "annotations": [],
            "points": opening["points"],
            "source_rect_count": len(group),
        }
        if frame is not opening:
            win["inner_frame_width"] = frame["frame_width"]
            win["inner_bbox"] = frame["bbox"]
        windows.append(win)

    return windows


def _associate_annotations(result: dict):
    """将 SH-文字 标注关联到最近的窗户/门
    只关联窗相关标注（H1窗台、H2窗、H1窗），其他标注（CH、H=、W=）保留原文不强制关联。
    """
    # 窗相关标注
    window_keys = {"sill_height", "sill_height_alt", "window_height", "window_height_1"}
    door_keys = {"door_height"}

    # 关联窗标注
    for ann in result["annotations"]:
        parsed = ann["parsed"]
        has_window_key = any(k in parsed for k in window_keys)
        if not has_window_key:
            continue

        ax, ay = ann["position"]
        best_win = None
        best_dist = float("inf")
        for win in result["windows"]:
            wx, wy = win["position"]
            dist = math.sqrt((ax - wx)**2 + (ay - wy)**2)
            if dist < best_dist:
                best_dist = dist
                best_win = win

        if best_win and best_dist < 8000:
            if parsed.get("sill_height"):
                best_win["sill_height"] = parsed["sill_height"][0]
            elif parsed.get("sill_height_alt"):
                best_win["sill_height"] = parsed["sill_height_alt"][0]
            if parsed.get("window_height"):
                best_win["window_height"] = parsed["window_height"][0]
            elif parsed.get("window_height_1") and best_win.get("window_height") is None:
                best_win["window_height"] = parsed["window_height_1"][0]
                if best_win.get("sill_height") is None:
                    best_win["sill_height"] = 0
            best_win["annotations"].append(ann["raw_texts"][0])

    # 关联梁标注 (H=/W=) 到最近的梁体
    beam_keys = {"beam_height", "beam_width"}
    for ann in result["annotations"]:
        parsed = ann["parsed"]
        if not any(k in parsed for k in beam_keys):
            continue

        physical_beams = [b for b in result["beams"] if b.get("type") == "beam"]
        ax, ay = ann["position"]
        best_beam = None
        best_dist = float("inf")
        for beam in physical_beams:
            dist = _distance_to_beam((ax, ay), beam)
            if dist < best_dist:
                best_dist = dist
                best_beam = beam

        if best_beam and best_dist < 3000:
            def beam_direction(beam):
                if not (beam.get("start") and beam.get("end")):
                    return None
                x1, y1 = beam["start"]
                x2, y2 = beam["end"]
                length = math.hypot(x2 - x1, y2 - y1)
                if length == 0:
                    return None
                return ((x2 - x1) / length, (y2 - y1) / length)

            if parsed.get("beam_height"):
                best_beam["beam_height"] = parsed["beam_height"][0]
            if parsed.get("beam_width"):
                best_beam["beam_width"] = parsed["beam_width"][0]
            best_beam.setdefault("annotations", []).append(ann["raw_texts"][0])

            # 单线梁常只有一条边界线，标注会落在相邻梁线附近；给近距离且
            # 尚无对应字段的梁线补同一个标注，建模阶段再用宽度兜底成梁盒。
            best_dir = beam_direction(best_beam)
            for beam in physical_beams:
                if beam is best_beam:
                    continue
                if _distance_to_beam((ax, ay), beam) > 900:
                    continue
                beam_dir = beam_direction(beam)
                if best_dir and beam_dir and abs(best_dir[0] * beam_dir[0] + best_dir[1] * beam_dir[1]) > 0.2:
                    continue
                changed = False
                if parsed.get("beam_height") and beam.get("beam_height") is None:
                    beam["beam_height"] = parsed["beam_height"][0]
                    changed = True
                if parsed.get("beam_width") and beam.get("beam_width") is None:
                    beam["beam_width"] = parsed["beam_width"][0]
                    changed = True
                if changed:
                    beam.setdefault("annotations", []).append(ann["raw_texts"][0])
            continue

        # 没有可关联梁几何时仍保留原始梁标注，方便排查 CAD。
        for k in beam_keys:
            if parsed.get(k):
                result["beams"].append({
                    "type": "beam_annotation",
                    "value": parsed[k][0],
                    "key": k,
                    "position": ann["position"],
                    "raw_text": ann["raw_texts"][0],
                })

    # 关联门标注
    for ann in result["annotations"]:
        parsed = ann["parsed"]
        if not any(k in parsed for k in door_keys):
            continue
        if not result["doors"]:
            continue

        ax, ay = ann["position"]
        best_door = None
        best_dist = float("inf")
        for door in result["doors"]:
            dist = _distance_to_bbox_or_position((ax, ay), door)
            if dist < best_dist:
                best_dist = dist
                best_door = door
        if best_door and best_dist < 1500:
            best_door["door_height"] = parsed["door_height"][0]
            raw = ann["raw_texts"][0]
            if raw not in best_door.get("annotations", []):
                best_door["annotations"].append(raw)


def _propagate_beam_annotations(result: dict):
    """通过链式连接和平行配对，将已有的 beam_height/beam_width 传播给缺失的梁线。

    规则:
    1. 端点相连（<50mm）的梁组成链，链内传播 h/w
    2. 链仍缺失时，找平行且距离 200-600mm 的已标注梁，继承其 h/w
    """
    TOL_CONNECT = 50      # 端点相连判定 (mm)
    TOL_PARALLEL_MIN = 50  # 平行梁最小间距 (mm) — 排除重叠线
    TOL_PARALLEL_MAX = 600 # 平行梁最大间距 (mm)

    beams = [b for b in result["beams"]
             if b.get("type") == "beam" and b.get("start") and b.get("end")]
    if not beams:
        return

    def _close(p1, p2):
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1]) < TOL_CONNECT

    # --- 1. 构建连接链 ---
    n = len(beams)
    adj = [set() for _ in range(n)]
    for i in range(n):
        si, ei = beams[i]["start"], beams[i]["end"]
        for j in range(i + 1, n):
            sj, ej = beams[j]["start"], beams[j]["end"]
            if _close(si, sj) or _close(si, ej) or _close(ei, sj) or _close(ei, ej):
                adj[i].add(j)
                adj[j].add(i)

    visited = set()
    chains = []
    for i in range(n):
        if i in visited:
            continue
        chain = []
        queue = [i]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            chain.append(node)
            for nb in adj[node]:
                if nb not in visited:
                    queue.append(nb)
        chains.append(chain)

    # --- 2. 链内传播 ---
    def _beam_dir(beam):
        x1, y1 = beam["start"]
        x2, y2 = beam["end"]
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            return None
        return ((x2 - x1) / length, (y2 - y1) / length)

    def _distance_point_to_segment(px, py, beam):
        x1, y1 = beam["start"]
        x2, y2 = beam["end"]
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    def _min_distance_between(a, b):
        d1 = _distance_point_to_segment(a["start"][0], a["start"][1], b)
        d2 = _distance_point_to_segment(a["end"][0], a["end"][1], b)
        d3 = _distance_point_to_segment(b["start"][0], b["start"][1], a)
        d4 = _distance_point_to_segment(b["end"][0], b["end"][1], a)
        return min(d1, d2, d3, d4)

    changed = True
    while changed:
        changed = False
        for chain in chains:
            # 收集链内已有标注
            h_vals = [beams[i]["beam_height"] for i in chain
                      if beams[i].get("beam_height") is not None and beams[i]["beam_height"] > 0]
            w_vals = [beams[i]["beam_width"] for i in chain
                      if beams[i].get("beam_width") is not None and beams[i]["beam_width"] > 0]
            if not h_vals and not w_vals:
                continue
            h_val = min(h_vals) if h_vals else None
            w_val = max(w_vals) if w_vals else None
            for i in chain:
                b = beams[i]
                if h_val and (b.get("beam_height") is None or b["beam_height"] <= 0):
                    b["beam_height"] = h_val
                    b.setdefault("annotations", []).append(f"链式传播H={h_val}")
                    changed = True
                if w_val and (b.get("beam_width") is None or b["beam_width"] <= 0):
                    b["beam_width"] = w_val
                    b.setdefault("annotations", []).append(f"链式传播W={w_val}")
                    changed = True

    # --- 3. 平行相邻推断（仅对链内仍缺失的梁） ---
    for chain in chains:
        missing = [i for i in chain
                   if beams[i].get("beam_height") is None or beams[i]["beam_height"] <= 0]
        if not missing:
            continue

        for mi in missing:
            mb = beams[mi]
            m_dir = _beam_dir(mb)
            if m_dir is None:
                continue

            best_h, best_w = None, None
            best_dist = float("inf")
            for j, other in enumerate(beams):
                if j in chain:
                    continue
                if other.get("beam_height") is None or other["beam_height"] <= 0:
                    continue
                o_dir = _beam_dir(other)
                if o_dir is None:
                    continue
                # 平行判定: 方向向量点积接近 ±1
                dot = abs(m_dir[0] * o_dir[0] + m_dir[1] * o_dir[1])
                if dot < 0.95:
                    continue
                dist = _min_distance_between(mb, other)
                if dist < TOL_PARALLEL_MIN or dist > TOL_PARALLEL_MAX:
                    continue
                if dist < best_dist:
                    best_dist = dist
                    best_h = other["beam_height"]
                    best_w = other.get("beam_width")

            if best_h is not None:
                mb["beam_height"] = best_h
                mb.setdefault("annotations", []).append(f"平行推断H={best_h}")
            if best_w is not None and (mb.get("beam_width") is None or mb["beam_width"] <= 0):
                mb["beam_width"] = best_w
                mb.setdefault("annotations", []).append(f"平行推断W={best_w}")


def _distance_to_bbox_or_position(point: tuple, item: dict) -> float:
    px, py = point
    bbox = item.get("bbox")
    if bbox:
        x0, y0, x1, y1 = bbox
        dx = max(x0 - px, 0, px - x1)
        dy = max(y0 - py, 0, py - y1)
        return math.hypot(dx, dy)

    dx, dy = item["position"]
    return math.hypot(px - dx, py - dy)


def _distance_to_beam(point: tuple, beam: dict) -> float:
    px, py = point
    if beam.get("start") and beam.get("end"):
        x1, y1 = beam["start"]
        x2, y2 = beam["end"]
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        nx = x1 + t * dx
        ny = y1 + t * dy
        return math.hypot(px - nx, py - ny)

    pts = beam.get("polyline") or []
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        dx = max(x0 - px, 0, px - x1)
        dy = max(y0 - py, 0, py - y1)
        return math.hypot(dx, dy)

    return float("inf")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 cad_parser.py <input.dxf> [output.json]")
        sys.exit(1)

    dxf_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else dxf_path.rsplit(".", 1)[0] + ".json"

    print(f"解析: {dxf_path}")
    data = parse_dxf(dxf_path)

    # 统计
    print(f"  墙体: {len(data['walls'])} 条")
    print(f"  窗户: {len(data['windows'])} 个")
    print(f"  门:   {len(data['doors'])} 个")
    print(f"  柱子: {len(data['columns'])} 个")
    print(f"  梁:   {len(data['beams'])} 条")
    print(f"  半高隔断: {len(data['partitions'])} 条")
    print(f"  尺寸标注: {len(data['dimensions'])} 个")
    print(f"  立面标注: {len(data['annotations'])} 组")
    print(f"  层高: {data['ceiling_heights']}")

    # 输出
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n输出: {out_path}")


if __name__ == "__main__":
    main()
