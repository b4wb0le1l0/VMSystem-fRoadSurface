from typing import Tuple, List, Optional
from PIL import Image, ImageDraw
import numpy as np
import math
import io

RGBA = Tuple[int, int, int, int]
BBox = Tuple[float, float, float, float]

def color_for_score(score: Optional[float]) -> RGBA:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return (200, 200, 200, 0)
    if score < 0.35:
        return (0, 180, 0, 200)      # green
    if score < 0.90:
        return (210, 190, 0, 210)    # yellow
    if score < 1.80:
        return (240, 140, 0, 220)    # orange
    return (220, 0, 0, 230)          # red

def _to_px_builder(bbox: BBox, img_w: int, img_h: int):
    min_lat, min_lon, max_lat, max_lon = bbox
    dx = max_lon - min_lon or 1e-12
    dy = max_lat - min_lat or 1e-12
    def to_px(lat: float, lon: float) -> Tuple[int, int]:
        x = int((lon - min_lon) / dx * (img_w - 1))
        y = int((max_lat - lat) / dy * (img_h - 1))
        return x, y
    return to_px

def render_points_png(
    rows: List[Tuple[float, float, float, float, int]],
    bbox: BBox,
    img_w: int,
    img_h: int,
    use_metric: str = "p95",
    cell_size_m: int = 25,
    roads: Optional[List[List[Tuple[float, float]]]] = None,
    opaque_bg: bool = False
) -> bytes:
    min_lat, min_lon, max_lat, max_lon = bbox
    bg = (255, 255, 255, 255) if opaque_bg else (0, 0, 0, 0)
    img = Image.new("RGBA", (img_w, img_h), bg)
    draw = ImageDraw.Draw(img, "RGBA")
    to_px = _to_px_builder(bbox, img_w, img_h)

    lon_m = 111_320.0 * math.cos(math.radians((min_lat + max_lat) / 2.0))
    r = max(2, int(0.5 * cell_size_m * img_w / max(1, (max_lon - min_lon) * lon_m)))

    if roads:
        w = max(1, min(3, int(0.2 * cell_size_m * img_w / max(1, (max_lon - min_lon) * lon_m))))
        for poly in roads:
            if len(poly) >= 2:
                pts = [to_px(lat, lon) for lat, lon in poly]
                draw.line(pts, fill=(120, 120, 120, 180), width=w)

    for cx, cy, s_avg, s_p95, _n in rows:
        x, y = to_px(cy, cx)
        s = s_p95 if use_metric.lower() == "p95" else s_avg
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color_for_score(s))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def render_lines_png(
    lines: List[List[Tuple[float, float, float]]],
    bbox: BBox,
    img_w: int,
    img_h: int,
    line_w_m: float = 6.0,
    roads: Optional[List[List[Tuple[float, float]]]] = None,
    opaque_bg: bool = False
) -> bytes:
    min_lat, min_lon, max_lat, max_lon = bbox
    bg = (255, 255, 255, 255) if opaque_bg else (0, 0, 0, 0)
    img = Image.new("RGBA", (img_w, img_h), bg)
    draw = ImageDraw.Draw(img, "RGBA")
    to_px = _to_px_builder(bbox, img_w, img_h)

    lon_m = 111_320.0 * math.cos(math.radians((min_lat + max_lat) / 2.0))
    px_per_m = (img_w - 1) / max(1e-9, (max_lon - min_lon) * lon_m)
    w_px = max(2, int(line_w_m * px_per_m))

    if roads:
        rw = max(1, min(3, int(w_px * 0.5)))
        for poly in roads:
            if len(poly) >= 2:
                pts = [to_px(lat, lon) for lat, lon in poly]
                draw.line(pts, fill=(120, 120, 120, 180), width=rw)

    for line in lines:
        if len(line) < 2:
            continue

        for (la1, lo1, s1), (la2, lo2, s2) in zip(line[:-1], line[1:]):
            steps = 6  # 4-8 обычно достаточно

            for i in range(steps):
                t0 = i / steps
                t1 = (i + 1) / steps

                lat_a = la1 + (la2 - la1) * t0
                lon_a = lo1 + (lo2 - lo1) * t0
                lat_b = la1 + (la2 - la1) * t1
                lon_b = lo1 + (lo2 - lo1) * t1

                s_mid = s1 + (s2 - s1) * ((t0 + t1) * 0.5)

                draw.line(
                    [to_px(lat_a, lon_a), to_px(lat_b, lon_b)],
                    fill=color_for_score(s_mid),
                    width=w_px
                )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def legend_items() -> List[dict]:
    return [
        {"label": "Green (good)", "max": 0.35, "color_rgba": [0, 180, 0, 160]},
        {"label": "Yellow",       "max": 0.90, "color_rgba": [210, 190, 0, 170]},
        {"label": "Orange",       "max": 1.80, "color_rgba": [240, 140, 0, 190]},
        {"label": "Red (bad)",    "max": None, "color_rgba": [220, 0, 0, 200]},
    ]