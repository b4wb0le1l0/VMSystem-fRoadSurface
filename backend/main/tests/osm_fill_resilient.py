#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import math
import argparse
import random
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_API = "http://localhost:8000/ingest/windows"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]

def meters_per_degree_lat() -> float:
    return 111_320.0

def meters_per_degree_lon_at_lat(lat_deg: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat_deg))

def bbox_from_center(lat: float, lon: float, radius_m: float) -> Tuple[float, float, float, float]:
    dlat = radius_m / meters_per_degree_lat()
    dlon = radius_m / max(1e-9, meters_per_degree_lon_at_lat(lat))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

def make_retrying_session(total=3, backoff=1.5) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=total,
        read=total,
        connect=total,
        status=total,
        backoff_factor=backoff,
        status_forcelist=[429, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

def fetch_roads_overpass(min_lat: float, min_lon: float, max_lat: float, max_lon: float, timeout_read: int = 120) -> List[List[Tuple[float,float]]]:
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    q = f"""
[out:json][timeout:25];
(
  way["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|service"]({bbox});
);
out geom;
"""
    headers = {"User-Agent": "VibroRoadTest/1.0 (edu project)"}
    sess = make_retrying_session()
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        try:
            r = sess.post(url, data=q, headers=headers, timeout=(10, timeout_read))
            r.raise_for_status()
            data = r.json()
            roads = []
            for el in data.get("elements", []):
                if el.get("type") == "way" and "geometry" in el:
                    poly = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
                    if len(poly) >= 2:
                        roads.append(poly)
            if roads:
                return roads
        except Exception as e:
            last_err = e
            time.sleep(1.0)
            continue
    if last_err:
        raise last_err
    return []

def sample_poly(poly: List[Tuple[float,float]], step_m: float = 35.0) -> List[Tuple[float,float]]:
    if len(poly) < 2:
        return poly
    res = [poly[0]]
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

def score_palette(idx: int) -> float:
    pal = [0.35, 0.7, 1.0, 1.35]
    return pal[idx % len(pal)]

def gen_roads_windows(lat: float, lon: float, radius_m: float,
                      limit_roads: int, step_m: float,
                      base_speed: float, start_time: datetime) -> List[dict]:
    min_lat, min_lon, max_lat, max_lon = bbox_from_center(lat, lon, radius_m)
    roads = fetch_roads_overpass(min_lat, min_lon, max_lat, max_lon)
    if not roads:
        return []
    wins = []
    k = 0
    for poly in roads[:limit_roads]:
        pts = sample_poly(poly, step_m=step_m)
        for (la, lo) in pts:
            score = score_palette((k // 20))
            t0 = start_time + timedelta(seconds=k)
            wins.append({
                "t_start": t0.isoformat() + "Z",
                "t_end": (t0 + timedelta(seconds=1)).isoformat() + "Z",
                "lat": la, "lon": lo,
                "speed_mps": base_speed,
                "roughness_score": float(score)
            })
            k += 1
    return wins

def gen_scatter_windows(lat: float, lon: float, radius_m: float, n: int,
                        base_speed: float, start_time: datetime) -> List[dict]:
    wins = []
    mx = max(1e-9, meters_per_degree_lon_at_lat(lat))
    my = meters_per_degree_lat()
    for i in range(n):
        u = random.random()
        r = radius_m * math.sqrt(u)
        ang = 2 * math.pi * random.random()
        dx = r * math.cos(ang)
        dy = r * math.sin(ang)
        lo = lon + dx / mx
        la = lat + dy / my
        t0 = start_time + timedelta(seconds=i)
        wins.append({
            "t_start": t0.isoformat() + "Z",
            "t_end": (t0 + timedelta(seconds=1)).isoformat() + "Z",
            "lat": la, "lon": lo,
            "speed_mps": base_speed,
            "roughness_score": float(0.35 + 1.1 * random.random())
        })
    return wins

def post_windows(api_url: str, device_serial: str, windows: List[dict], batch_size: int = 500, timeout: int = 60):
    url = api_url.rstrip("/")
    total = 0
    for i in range(0, len(windows), batch_size):
        chunk = windows[i:i+batch_size]
        payload = {"device_serial": device_serial, "windows": chunk}
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        total += data.get("inserted", 0)
        print(f"Sent {len(chunk)} -> inserted: {data.get('inserted')} (trip_id={data.get('trip_id')})")
        time.sleep(0.2)
    print(f"Done. Total inserted: {total}")

def main():
    ap = argparse.ArgumentParser(description="Generate test windows along OSM roads (with fallback)")
    ap.add_argument("--api", default=DEFAULT_API, help="Ingest endpoint (default: http://localhost:8000/ingest/windows)")
    ap.add_argument("--serial", default="DEV-OSM", help="Device serial")
    ap.add_argument("--lat", type=float, default=59.931717)
    ap.add_argument("--lon", type=float, default=30.463896)
    ap.add_argument("--radius", type=float, default=1500.0, help="Radius in meters")
    ap.add_argument("--limit_roads", type=int, default=50)
    ap.add_argument("--step_m", type=float, default=35.0)
    ap.add_argument("--speed", type=float, default=13.0)
    ap.add_argument("--scatter_n", type=int, default=600, help="Fallback scatter points if Overpass fails")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    start_time = datetime.utcnow()

    try:
        windows = gen_roads_windows(args.lat, args.lon, args.radius, args.limit_roads, args.step_m, args.speed, start_time)
        if not windows:
            raise RuntimeError("Overpass returned no roads")
        print(f"Generated {len(windows)} windows along OSM roads")
    except Exception as e:
        print(f"[WARN] Overpass failed: {e}. Falling back to scatter...")
        windows = gen_scatter_windows(args.lat, args.lon, args.radius, args.scatter_n, args.speed, start_time)

    post_windows(args.api, args.serial, windows)

    base = args.api.split("/ingest/windows")[0]
    min_lat, min_lon, max_lat, max_lon = bbox_from_center(args.lat, args.lon, args.radius)
    print("\nOpen heatmap URLs:")
    print(f"- Lines: {base}/heatmap_lines?lat={args.lat:.6f}&lon={args.lon:.6f}&radius_m={int(args.radius)}&period=365%20days&overlay_roads=1&opaque=1")
    print(f"- BBox:  {base}/heatmap_bbox?min_lat={min_lat:.6f}&min_lon={min_lon:.6f}&max_lat={max_lat:.6f}&max_lon={max_lon:.6f}&period=365%20days&overlay_roads=1&opaque=1")
    print(f"- Glob:  {base}/heatmap_global_lines?period=365%20days&overlay_roads=1&opaque=1")

if __name__ == "__main__":
    main()