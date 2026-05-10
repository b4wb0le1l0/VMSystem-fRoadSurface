from typing import List, Tuple, Optional, Literal
from functools import lru_cache
import math
import requests
import numpy as np
from fastapi import APIRouter, Response, HTTPException, Query
from fastapi.responses import JSONResponse

from .db import get_conn
from .schemas import IngestWindows, IngestResult
from .utils import bbox_from_center, cell_deg_size
from .rendering import render_points_png, render_lines_png, legend_items

ENGINE_BASELINE = {
    "a_rms": 0.18,
    "a_p95": 0.35,
    "a_max": 0.55,
    "jerk_p95": 8.0,
}

def _nz(v: float, base: float) -> float:
    return max(0.0, v - base)

router = APIRouter()

def compute_score_py(w: dict) -> float:
    a_rms = float(w.get("a_rms") or 0.0)
    a_p95 = float(w.get("a_p95") or 0.0)
    a_max = float(w.get("a_max") or 0.0)
    jerk_p95 = float(w.get("jerk_p95") or 0.0)
    peaks = int(w.get("peaks") or 0)
    speed_mps = float(w.get("speed_mps") or 0.0)

    # вычитаем мягкий baseline
    a_rms_eff = _nz(a_rms, ENGINE_BASELINE["a_rms"])
    a_p95_eff = _nz(a_p95, ENGINE_BASELINE["a_p95"])
    a_max_eff = _nz(a_max, ENGINE_BASELINE["a_max"])
    jerk_eff = _nz(jerk_p95, ENGINE_BASELINE["jerk_p95"])

    score = (
        0.40 * a_rms_eff +
        0.32 * a_p95_eff +
        0.18 * a_max_eff +
        0.05 * (jerk_eff / 10.0) +
        0.05 * min(peaks, 10)
    )

    if speed_mps > 2.0:
        speed_factor = max(1.0, min(1.5, 0.9 + speed_mps / 12.0))
        score = score / speed_factor

    return float(score)

@router.get("/health")
def health():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1;")
        cur.fetchone()
    return {"status": "ok"}

@router.post("/ingest/windows", response_model=IngestResult)
def ingest_windows(payload: IngestWindows):
    if not payload.windows:
        raise HTTPException(status_code=400, detail="No windows provided")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO devices(serial) VALUES (%s) ON CONFLICT (serial) DO NOTHING RETURNING id;",
            (payload.device_serial,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM devices WHERE serial=%s;", (payload.device_serial,))
            row = cur.fetchone()
        device_id = row[0]

        trip_id = payload.trip_id
        if trip_id is not None:
            cur.execute("SELECT id FROM trips WHERE id = %s AND device_id = %s;", (trip_id, device_id))
            if cur.fetchone() is None:
                trip_id = None
        
        if trip_id is None:
            cur.execute(
                "INSERT INTO trips(device_id, started_at) VALUES (%s, now()) RETURNING id;",
                (device_id,)
            )
            trip_id = cur.fetchone()[0]

        rows = []
        for w in payload.windows:
            wd = w.model_dump()

            speed_mps = float(wd.get("speed_mps") or 0.0)

            # пока фильтруем почти полную стоянку
            if speed_mps < 0.3:
                continue

            score = wd.get("roughness_score")
            if score is None:
                score = compute_score_py(wd)

            rows.append((
                device_id, trip_id,
                wd["t_start"], wd["t_end"],
                wd["lat"], wd["lon"],
                wd.get("speed_mps"), wd.get("hdop"), wd.get("sats"),
                wd.get("a_rms"), wd.get("a_p95"), wd.get("a_max"),
                wd.get("jerk_p95"), wd.get("peaks"),
                wd.get("e_0_5"), wd.get("e_5_12"), wd.get("e_12_30"),
                score, None
            ))

        if not rows:
            return IngestResult(inserted=0, trip_id=trip_id)

        cur.executemany("""
            INSERT INTO imu_windows(
                device_id, trip_id, t_start, t_end, lat, lon,
                speed_mps, hdop, sats,
                a_rms, a_p95, a_max, jerk_p95, peaks, e_0_5, e_5_12, e_12_30,
                roughness_score, payload
            )
            VALUES (%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s)
        """, rows)

    return IngestResult(inserted=len(rows), trip_id=trip_id)

def _aggregate_and_render(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    period: str, cell_size_m: int, img_w: int, img_h: int,
    metric: Literal["avg", "p95"],
    overlay_roads: bool = False,
    opaque_bg: bool = False
) -> bytes:
    dlat, dlon = cell_deg_size((min_lat + max_lat) / 2.0, cell_size_m)
    sql = """
    WITH p AS (
      SELECT ST_MakeEnvelope(%s,%s,%s,%s,4326) AS env,
             %s::double precision AS dlat,
             %s::double precision AS dlon,
             (now() - %s::interval) AS t_from,
             %s::double precision AS ox,
             %s::double precision AS oy
    ),
    g AS (
      SELECT ST_SnapToGrid((w.geom)::geometry, p.ox, p.oy, p.dlon, p.dlat) AS cell,
             w.roughness_score AS s
      FROM imu_windows w, p
      WHERE w.t_end >= p.t_from
        AND (w.geom)::geometry && p.env
    )
    SELECT ST_X(ST_Centroid(cell)) AS cx,
           ST_Y(ST_Centroid(cell)) AS cy,
           COUNT(*) AS n,
           AVG(s) AS s_avg,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s) AS s_p95
    FROM g
    GROUP BY cell;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (min_lon, min_lat, max_lon, max_lat, dlat, dlon, period, min_lon, min_lat))
        rows = [(cx, cy, s_avg, s_p95, n) for cx, cy, n, s_avg, s_p95 in cur.fetchall()]
    roads = _fetch_osm_roads(min_lat, min_lon, max_lat, max_lon) if overlay_roads else None
    buildings = _fetch_osm_buildings(min_lat, min_lon, max_lat, max_lon) if overlay_roads else None
    return render_points_png(
        rows=rows,
        bbox=(min_lat, min_lon, max_lat, max_lon),
        img_w=img_w, img_h=img_h,
        use_metric=metric,
        cell_size_m=cell_size_m,
        roads=roads,
        buildings=buildings,
        opaque_bg=opaque_bg
    )

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _collect_trip_lines(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    period: str, max_gap_s: int, max_seg_m: int
) -> List[List[Tuple[float, float, float]]]:
    sql = """
    WITH p AS (SELECT (now() - %s::interval) AS t_from),
    w AS (
      SELECT device_id, trip_id, t_end, lat, lon, roughness_score
      FROM imu_windows, p
      WHERE t_end >= p.t_from
        AND (geom)::geometry && ST_MakeEnvelope(%s,%s,%s,%s,4326)
      ORDER BY device_id, trip_id, t_end
    )
    SELECT device_id, trip_id, EXTRACT(EPOCH FROM t_end)::bigint AS tse, lat, lon, roughness_score
    FROM w;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (period, min_lon, min_lat, max_lon, max_lat))
        rows = cur.fetchall()

    lines: List[List[Tuple[float, float, float]]] = []
    cur_line: List[Tuple[float, float, float]] = []
    prev: Optional[Tuple[int, int, int, float, float]] = None
    for dev, trip, tse, lat, lon, s in rows:
        if prev is None:
            cur_line = [(lat, lon, float(s))]
            prev = (dev, trip, tse, lat, lon)
            continue
        p_dev, p_trip, p_tse, p_lat, p_lon = prev
        gap_s = int(tse - p_tse)
        dist_m = _haversine_m(p_lat, p_lon, lat, lon)
        if (dev != p_dev) or (trip != p_trip) or (gap_s > max_gap_s) or (dist_m > max_seg_m):
            if len(cur_line) >= 2:
                lines.append(cur_line)
            cur_line = [(lat, lon, float(s))]
        else:
            cur_line.append((lat, lon, float(s)))
        prev = (dev, trip, tse, lat, lon)
    if len(cur_line) >= 2:
        lines.append(cur_line)
    return lines

@lru_cache(maxsize=64)
def _fetch_osm_roads_cached(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> List[List[Tuple[float, float]]]:
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    q = f"""
[out:json][timeout:25];
(
  way["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|service"]({bbox});
);
out geom;
"""
    r = requests.post("https://overpass-api.de/api/interpreter", data=q, headers={"User-Agent": "VibroRoad/1.0"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    roads = []
    for el in data.get("elements", []):
        if el.get("type") == "way" and "geometry" in el:
            poly = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
            if len(poly) >= 2:
                roads.append(poly)
    return roads

@lru_cache(maxsize=64)
def _fetch_osm_buildings_cached(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> List[List[Tuple[float, float]]]:
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    q = f"""
[out:json][timeout:25];
(
  way["building"]({bbox});
);
out geom;
"""
    r = requests.post(
        "https://overpass-api.de/api/interpreter",
        data=q,
        headers={"User-Agent": "VibroRoad/1.0"},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()

    buildings = []
    for el in data.get("elements", []):
        if el.get("type") == "way" and "geometry" in el:
            poly = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
            if len(poly) >= 3:
                buildings.append(poly)
    return buildings

def _fetch_osm_buildings(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> Optional[List[List[Tuple[float, float]]]]:
    try:
        return _fetch_osm_buildings_cached(
            round(min_lat, 4),
            round(min_lon, 4),
            round(max_lat, 4),
            round(max_lon, 4)
        )
    except Exception:
        return None

def _fetch_osm_roads(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> Optional[List[List[Tuple[float, float]]]]:
    try:
        return _fetch_osm_roads_cached(round(min_lat, 4), round(min_lon, 4), round(max_lat, 4), round(max_lon, 4))
    except Exception:
        return None

@router.get("/heatmap")
def heatmap(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius_m: int = Query(1000, ge=50, le=20000),
    period: str = Query("30 days"),
    cell_size_m: int = Query(25, ge=5, le=500),
    img_w: int = Query(800, ge=100, le=4096),
    img_h: int = Query(800, ge=100, le=4096),
    metric: Literal["avg", "p95"] = "p95",
    overlay_roads: bool = False,
    opaque: bool = False
):
    min_lat, min_lon, max_lat, max_lon = bbox_from_center(lat, lon, radius_m)
    png = _aggregate_and_render(min_lat, min_lon, max_lat, max_lon, period, cell_size_m, img_w, img_h, metric, overlay_roads, opaque)
    return Response(content=png, media_type="image/png")

@router.get("/heatmap_global")
def heatmap_global(
    period: str = Query("30 days"),
    cell_size_m: int = Query(50, ge=5, le=1000),
    img_w: int = Query(1200, ge=200, le=4096),
    img_h: int = Query(800, ge=200, le=4096),
    metric: Literal["avg", "p95"] = "p95",
    overlay_roads: bool = False,
    opaque: bool = False
):
    extent_sql = """
    WITH p AS (SELECT (now() - %s::interval) AS t_from),
    e AS (
      SELECT ST_Extent((w.geom)::geometry) AS env
      FROM imu_windows w, p
      WHERE w.t_end >= p.t_from
    )
    SELECT ST_XMin(env), ST_YMin(env), ST_XMax(env), ST_YMax(env) FROM e;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(extent_sql, (period,))
        row = cur.fetchone()
    if not row or any(v is None for v in row):
        raise HTTPException(status_code=404, detail="No data for the selected period")
    minx, miny, maxx, maxy = map(float, row)
    pad_x, pad_y = (maxx - minx) * 0.05, (maxy - miny) * 0.05
    min_lon, max_lon = max(-180.0, minx - pad_x), min(180.0, maxx + pad_x)
    min_lat, max_lat = max(-90.0,  miny - pad_y), min(90.0,  maxy + pad_y)
    png = _aggregate_and_render(min_lat, min_lon, max_lat, max_lon, period, cell_size_m, img_w, img_h, metric, overlay_roads, opaque)
    return Response(content=png, media_type="image/png")

@router.get("/heatmap_bbox")
def heatmap_bbox(
    min_lat: float = Query(..., ge=-90.0, le=90.0),
    min_lon: float = Query(..., ge=-180.0, le=180.0),
    max_lat: float = Query(..., ge=-90.0, le=90.0),
    max_lon: float = Query(..., ge=-180.0, le=180.0),
    period: str = Query("30 days"),
    cell_size_m: int = Query(25, ge=5, le=1000),
    img_w: int = Query(1000, ge=200, le=4096),
    img_h: int = Query(800, ge=200, le=4096),
    metric: Literal["avg", "p95"] = "p95",
    overlay_roads: bool = False,
    opaque: bool = False
):
    if max_lat <= min_lat or max_lon <= min_lon:
        raise HTTPException(status_code=400, detail="Invalid bbox")
    png = _aggregate_and_render(min_lat, min_lon, max_lat, max_lon, period, cell_size_m, img_w, img_h, metric, overlay_roads, opaque)
    return Response(content=png, media_type="image/png")

@router.get("/heatmap_lines")
def heatmap_lines(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius_m: int = Query(1500, ge=50, le=20000),
    period: str = Query("30 days"),
    img_w: int = Query(1000, ge=200, le=4096),
    img_h: int = Query(800, ge=200, le=4096),
    line_w_m: float = Query(6.0, ge=1.0, le=50.0),
    max_gap_s: int = Query(30, ge=1, le=3600),
    max_seg_m: int = Query(120, ge=10, le=2000),
    overlay_roads: bool = False,
    opaque: bool = False
):
    min_lat, min_lon, max_lat, max_lon = bbox_from_center(lat, lon, radius_m)
    lines = _collect_trip_lines(min_lat, min_lon, max_lat, max_lon, period, max_gap_s, max_seg_m)
    roads = _fetch_osm_roads(min_lat, min_lon, max_lat, max_lon) if overlay_roads else None
    buildings = _fetch_osm_buildings(min_lat, min_lon, max_lat, max_lon) if overlay_roads else None
    png = render_lines_png(lines,(min_lat, min_lon, max_lat, max_lon), img_w,img_h, line_w_m, roads, buildings, opaque)
    return Response(content=png, media_type="image/png")

@router.get("/heatmap_global_lines")
def heatmap_global_lines(
    period: str = Query("30 days"),
    img_w: int = Query(1800, ge=400, le=4096),
    img_h: int = Query(1000, ge=400, le=4096),
    line_w_m: float = Query(6.0, ge=1.0, le=50.0),
    max_gap_s: int = Query(30, ge=1, le=3600),
    max_seg_m: int = Query(120, ge=10, le=2000),
    overlay_roads: bool = False,
    opaque: bool = False
):
    extent_sql = """
    WITH p AS (SELECT (now() - %s::interval) AS t_from),
    e AS (
      SELECT ST_Extent((w.geom)::geometry) AS env
      FROM imu_windows w, p
      WHERE w.t_end >= p.t_from
    )
    SELECT ST_XMin(env), ST_YMin(env), ST_XMax(env), ST_YMax(env) FROM e;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(extent_sql, (period,))
        row = cur.fetchone()
    if not row or any(v is None for v in row):
        raise HTTPException(status_code=404, detail="No data for the selected period")
    minx, miny, maxx, maxy = map(float, row)
    pad_x, pad_y = (maxx - minx) * 0.05, (maxy - miny) * 0.05
    min_lon, max_lon = max(-180.0, minx - pad_x), min(180.0, maxx + pad_x)
    min_lat, max_lat = max(-90.0,  miny - pad_y), min(90.0,  maxy + pad_y)
    lines = _collect_trip_lines(min_lat, min_lon, max_lat, max_lon, period, max_gap_s, max_seg_m)
    roads = _fetch_osm_roads(min_lat, min_lon, max_lat, max_lon) if overlay_roads else None
    buildings = _fetch_osm_buildings(min_lat, min_lon, max_lat, max_lon) if overlay_roads else None
    png = render_lines_png(lines,(min_lat, min_lon, max_lat, max_lon), img_w,img_h, line_w_m, roads, buildings, opaque)
    return Response(content=png, media_type="image/png")

@router.get("/legend")
def legend():
    return JSONResponse({"metric": "roughness_score", "classes": legend_items()})
