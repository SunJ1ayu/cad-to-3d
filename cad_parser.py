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

    # 门框 LWPOLYLINE (FF-门)
    door_polys = []
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "FF-门":
            pts = [(round(p[0], 1), round(p[1], 1)) for p in e.get_points(format="xy")]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            cx = round(sum(xs) / len(xs), 1)
            cy = round(sum(ys) / len(ys), 1)
            w = round(max(xs) - min(xs), 1)
            h = round(max(ys) - min(ys), 1)
            door_polys.append({
                "type": "door",
                "layer": "FF-门",
                "representation": "polyline",
                "position": [cx, cy],
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "width": max(w, h),
                "depth": min(w, h),
                "points": pts,
                "door_height": None,
                "door_width": None,
                "annotations": [],
            })
    result["doors"].extend(door_polys)

    # BS-备用 中有门洞辅助线/矩形，MH 标注通常指向这些门洞而不是 FF-门小构件
    reserve_polys = []
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "BS-备用":
            pts = [(round(p[0], 1), round(p[1], 1)) for p in e.get_points(format="xy")]
            if len(pts) < 4:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            reserve_polys.append({
                "type": "door",
                "layer": "BS-备用",
                "representation": "opening_polyline",
                "position": [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)],
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "width": round(max(max(xs) - min(xs), max(ys) - min(ys)), 1),
                "depth": round(min(max(xs) - min(xs), max(ys) - min(ys)), 1),
                "points": pts,
                "door_height": None,
                "door_width": None,
                "annotations": [],
            })

    reserve_lines = []
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-备用":
            s, end = e.dxf.start, e.dxf.end
            reserve_lines.append({
                "start": (round(s.x, 1), round(s.y, 1)),
                "end": (round(end.x, 1), round(end.y, 1)),
            })
    reserve_polys.extend(_pair_lines_to_door_openings(reserve_lines))
    result["doors"].extend(reserve_polys)

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
    for e in msp.query("LINE"):
        if e.dxf.layer == "BS-梁":
            s, end = e.dxf.start, e.dxf.end
            length = math.sqrt((s.x - end.x)**2 + (s.y - end.y)**2)
            result["beams"].append({
                "type": "beam",
                "layer": "BS-梁",
                "start": [round(s.x, 1), round(s.y, 1)],
                "end": [round(end.x, 1), round(end.y, 1)],
                "length": round(length, 1),
                "beam_height": None,
                "beam_width": None,
                "annotations": [],
            })
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "BS-梁":
            pts = [(round(p[0], 1), round(p[1], 1)) for p in e.get_points(format="xy")]
            result["beams"].append({
                "type": "beam",
                "layer": "BS-梁",
                "polyline": pts,
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
    _associate_annotations(result)

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
            best_door["annotations"].append(ann["raw_texts"][0])


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
