#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import math
import argparse
import random
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

import requests

DEFAULT_LAT = 59.931717
DEFAULT_LON = 30.463896
DEFAULT_API = "http://localhost:8000/ingest/windows"

def meters_per_degree_lat() -> float:
    return 111_320.0

def meters_per_degree_lon_at_lat(lat_deg: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat_deg))

def bbox_from_center(lat: float, lon: float, radius_m: float) -> Tuple[float, float, float, float]:
    dlat = radius_m / meters_per_degree_lat()
    dlon = radius_m / max(1e-9, meters_per_degree_lon_at_lat(lat))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

def score_cycle(i: int) -> float:
    palette = [0.35, 0.7, 1.0, 1.35]  # A, B, C, D
    return palette[i % len(palette)]

def score_ramp(i: int, n: int, lo=0.3, hi=1.5) -> float:
    if n <= 1:
        return lo
    return lo + (hi - lo) * (i / (n - 1))

def score_random(lo=0.25, hi=1.6) -> float:
    return random.uniform(lo, hi)

def gen_scatter_windows(
    lat0: float,
    lon0: float,
    radius_m: float,
    n: int,
    pattern: str,
    base_speed: float,
    start_time: datetime
) -> List[dict]:
    """Случайные точки в круге радиуса radius_m вокруг (lat0, lon0)."""
    wins = []
    m_per_deg_lon = max(1e-9, meters_per_degree_lon_at_lat(lat0))
    m_per_deg_lat = meters_per_degree_lat()
    for i in range(n):
        # равномерно по площади круга
        u = random.random()
        r = radius_m * math.sqrt(u)
        ang = 2 * math.pi * random.random()
        dx = r * math.cos(ang)
        dy = r * math.sin(ang)
        lon = lon0 + dx / m_per_deg_lon
        lat = lat0 + dy / m_per_deg_lat

        if pattern == "cycle":
            score = score_cycle(i)
        elif pattern == "ramp":
            score = score_ramp(i, n)
        elif pattern == "random":
            score = score_random()
        else:
            score = score_cycle(i)

        t0 = start_time + timedelta(seconds=i)
        wins.append({
            "t_start": t0.isoformat() + "Z",
            "t_end": (t0 + timedelta(seconds=1)).isoformat() + "Z",
            "lat": lat,
            "lon": lon,
            "speed_mps": base_speed,
            "roughness_score": score
        })
    return wins

def fetch_osm_roads(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> List[List[Tuple[float,float]]]:
    """Тянем дороги из Overpass (исключаем пешеходные/сервисные)."""
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    q = f"""
[out:json][timeout:25];
(
  way["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|service"]({bbox});
);
out geom;
"""
    headers = {"User-Agent": "VibroRoadTest/1.0 (edu project)"}
    r = requests.post("https://overpass-api.de/api/interpreter", data=q, headers=headers, timeout=40)
    r.raise_for_status()
    data = r.json()
    roads = []
    for el in data.get("elements", []):
        if el.get("type") == "way" and "geometry" in el:
            poly = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
            if len(poly) >= 2:
                roads.append(poly)
    return roads

def sample_poly(poly: List[Tuple[float,float]], step_m: float = 35.0) -> List[Tuple[float,float]]:
    """Дискретизация линии по шагу в метрах."""
    if len(poly) < 2:
        return poly
    res = [poly[0]]
    acc = 0.0
    m_per_deg_lat = meters_per_degree_lat()
    for (la1, lo1), (la2, lo2) in zip(poly[:-1], poly[1:]):
        mx = meters_per_degree_lon_at_lat((la1 + la2) / 2.0)
        dx = (lo2 - lo1) * mx
        dy = (la2 - la1) * m_per_deg_lat
        seg = math.hypot(dx, dy)
        if seg <= 1e-6:
            continue
        n = max(1, int(seg // step_m))
        for i in range(1, n + 1):
            t = i / n
            res.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
    return res

def gen_roads_windows(
    lat0: float,
    lon0: float,
    radius_m: float,
    limit_roads: int,
    step_m: float,
    pattern: str,
    base_speed: float,
    start_time: datetime
) -> List[dict]:
    """Точки вдоль дорог в радиусе вокруг центра."""
    min_lat, min_lon, max_lat, max_lon = bbox_from_center(lat0, lon0, radius_m)
    roads = fetch_osm_roads(min_lat, min_lon, max_lat, max_lon)
    if not roads:
        return []

    wins = []
    k = 0
    palette = [0.35, 0.7, 1.0, 1.35]  # A,B,C,D
    for poly in roads[:limit_roads]:
        pts = sample_poly(poly, step_m=step_m)
        for (lat, lon) in pts:
            if pattern == "cycle":
                score = palette[(k // 20) % len(palette)]
            elif pattern == "ramp":
                # используем «медленную» рампу по мере продвижения
                score = score_ramp(k, 1000)
            elif pattern == "random":
                score = score_random()
            else:
                score = palette[(k // 20) % len(palette)]

            t0 = start_time + timedelta(seconds=k)
            wins.append({
                "t_start": t0.isoformat() + "Z",
                "t_end": (t0 + timedelta(seconds=1)).isoformat() + "Z",
                "lat": lat,
                "lon": lon,
                "speed_mps": base_speed,
                "roughness_score": float(score)
            })
            k += 1
    return wins

def post_windows(api_url: str, device_serial: str, windows: List[dict], batch_size: int = 500, timeout: int = 60):
    url = api_url.rstrip("/")
    total = 0
    for i in range(0, len(windows), batch_size):
        chunk = windows[i:i+batch_size]
        payload = {"device_serial": device_serial, "windows": chunk}
        r = requests.post(url, json=payload, timeout=timeout)
        try:
            r.raise_for_status()
        except Exception:
            print("Request failed:", r.status_code, r.text[:500])
            raise
        data = r.json()
        total += data.get("inserted", 0)
        print(f"Sent {len(chunk)} windows -> inserted: {data.get('inserted')} (trip_id={data.get('trip_id')})")
        time.sleep(0.2)
    print(f"Done. Total inserted: {total}")

def main():
    p = argparse.ArgumentParser(description="Generate synthetic vibro windows around a point")
    p.add_argument("--api", default=DEFAULT_API, help="Ingest endpoint (default: http://localhost:8000/ingest/windows)")
    p.add_argument("--serial", default="DEV-AROUND", help="Device serial")
    p.add_argument("--lat", type=float, default=DEFAULT_LAT, help="Center latitude")
    p.add_argument("--lon", type=float, default=DEFAULT_LON, help="Center longitude")
    p.add_argument("--radius", type=float, default=1500.0, help="Radius in meters")
    p.add_argument("--mode", choices=["scatter", "roads"], default="scatter", help="Generation mode")
    p.add_argument("--n", type=int, default=800, help="Number of windows for scatter")
    p.add_argument("--step_m", type=float, default=35.0, help="Sampling step along roads (meters)")
    p.add_argument("--limit_roads", type=int, default=60, help="Max roads to sample in roads mode")
    p.add_argument("--speed", type=float, default=13.0, help="speed_mps to embed")
    p.add_argument("--pattern", choices=["cycle", "ramp", "random"], default="cycle", help="Score pattern")
    p.add_argument("--seed", type=int, default=42, help="Random seed (for reproducibility)")
    args = p.parse_args()

    random.seed(args.seed)
    start_time = datetime.utcnow()

    if args.mode == "scatter":
        windows = gen_scatter_windows(
            lat0=args.lat, lon0=args.lon, radius_m=args.radius, n=args.n,
            pattern=args.pattern, base_speed=args.speed, start_time=start_time
        )
    else:
        windows = gen_roads_windows(
            lat0=args.lat, lon0=args.lon, radius_m=args.radius,
            limit_roads=args.limit_roads, step_m=args.step_m,
            pattern=args.pattern, base_speed=args.speed, start_time=start_time
        )

    if not windows:
        print("No windows generated (maybe Overpass returned nothing?). Try larger radius or mode=scatter.")
        return

    print(f"Generated {len(windows)} windows around ({args.lat}, {args.lon}), mode={args.mode}")
    post_windows(api_url=args.api, device_serial=args.serial, windows=windows)

    # Подсказки по просмотру
    min_lat, min_lon, max_lat, max_lon = bbox_from_center(args.lat, args.lon, args.radius)
    base = args.api.split("/ingest/windows")[0]
    print("\nOpen heatmap URLs (adjust as needed):")
    print(f"- Global: {base}/heatmap_global?period=365%20days&overlay_roads=1&opaque=1&img_w=1200&img_h=800")
    print(f"- BBox:   {base}/heatmap_bbox?min_lat={min_lat:.6f}&min_lon={min_lon:.6f}&max_lat={max_lat:.6f}&max_lon={max_lon:.6f}&period=365%20days&overlay_roads=1&opaque=1")
    print(f"- Center: {base}/heatmap?lat={args.lat:.6f}&lon={args.lon:.6f}&radius_m={int(args.radius)}&period=365%20days&overlay_roads=1&opaque=1")

if __name__ == "__main__":
    main()