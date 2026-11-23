"""Geometry helpers. Mirror of packages/validation (pointInPolygon, polygonArea)."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_int = (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi
            if x < x_int:
                inside = not inside
        j = i
    return inside


def polygon_area(polygon: Sequence[Point]) -> float:
    area = 0.0
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        area += xj * yi - xi * yj
        j = i
    return abs(area) / 2.0


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """IoU of two [x1, y1, x2, y2] boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def box_foot_point(box: Sequence[float]) -> Point:
    """Bottom-centre of a person box: the best single point for zone membership."""
    x1, _, x2, y2 = box
    return ((x1 + x2) / 2.0, y2)


def joint_angle(a: Point, b: Point, c: Point) -> float:
    """Angle ABC in degrees (vertex at b). Returns NaN for degenerate input."""
    bax, bay = a[0] - b[0], a[1] - b[1]
    bcx, bcy = c[0] - b[0], c[1] - b[1]
    na = math.hypot(bax, bay)
    nc = math.hypot(bcx, bcy)
    if na < 1e-6 or nc < 1e-6:
        return math.nan
    cos = max(-1.0, min(1.0, (bax * bcx + bay * bcy) / (na * nc)))
    return math.degrees(math.acos(cos))
