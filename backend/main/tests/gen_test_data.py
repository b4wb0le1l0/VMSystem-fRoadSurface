#!/usr/bin/env python3
import math
import argparse
import random
from datetime import datetime, timedelta
import requests

def meters_per_degree_lat():
    return 111_320.0

def meters_per_degree_lon_at_lat(lat_deg: float):
    return 111_320.0 * math.cos(math.radians(lat_deg))

def gen_points_along_line(lat0: float, lon0: float, n: int, length_m: float):
    # Линия восток↔запад длиной length_m, n точек, центр в (lat0, lon0)
    m_per_deg_lon = meters_per_degree_lon_at_lat(lat0) or 1e-6
    start_lon = lon0 - (length_m / 2.0) / m_per_deg_lon
    dlon = (length_m / m_per_deg_lon) / max(1, (n - 1))
    for i in range(n):
        yield (lat0, start_lon + i * dlon)

def score_cycle(i: int):
    palette = [0.3, 0.7, 1.0, 1.4]  # A, B, C, D
    return palette[i % len(palette)]

def score_ramp(i: int, n: int, lo=0.3, hi=1.5):
    if n <= 1:
        return lo
    return lo + (hi - lo) * (i / (n - 1))

def score_random(lo=0.2, hi=1.6):
    return random.uniform(lo, hi)

def build_windows(lat0, lon0, n, length_m, base_speed, pattern, start_time):
    windows = []
    for idx, (lat, lon) in enumerate(gen_points_along_line(lat0, lon0, n, length_m)):
        if pattern == "cycle":
            score = score_cycle(idx)
        elif pattern == "ramp":
            score = score_ramp(idx, n)
        elif pattern == "random":
            score = score_random()
        else:
            score = score_cycle(idx)

        t0 = start_time + timedelta(seconds=idx)
        windows.append({
            "t_start": t0.isoformat() + "Z",
            "t_end":   (t0 + timedelta(seconds=1)).isoformat() + "Z",
            "lat": lat,
            "lon": lon,
            "speed_mps": base_speed,
            "roughness_score": score
        })
    return windows

def post_windows(api_base, device_serial, windows, batch_size=200, timeout=30):
    url = api_base.rstrip("/") + "/ingest/windows"
    total = 0
    for i in range(0, len(windows), batch_size):
        chunk = windows[i:i+batch_size]
        payload = {
            "device_serial": device_serial,
            "windows": chunk
        }
        r = requests.post(url, json=payload, timeout=timeout)
        try:
            r.raise_for_status()
        except Exception:
            print("Request failed:", r.status_code, r.text)
            raise
        data = r.json()
        total += data.get("inserted", 0)
        print(f"Sent {len(chunk)} windows, server inserted: {data.get('inserted')} (trip_id={data.get('trip_id')})")
    print(f"Done. Total inserted reported by server: {total}")

def main():
    parser = argparse.ArgumentParser(description="Generate test vibro windows and ingest to API")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL (default: http://localhost:8000)")
    parser.add_argument("--serial", default="DEV-TEST", help="Device serial (default: DEV-TEST)")
    parser.add_argument("--lat", type=float, default=59.93015, help="Center latitude")
    parser.add_argument("--lon", type=float, default=30.31110, help="Center longitude")
    parser.add_argument("--n", type=int, default=200, help="Number of windows (points)")
    parser.add_argument("--length", type=float, default=1200.0, help="Line length in meters")
    parser.add_argument("--speed", type=float, default=12.0, help="Speed m/s to embed into windows")
    parser.add_argument("--pattern", choices=["cycle", "ramp", "random"], default="cycle", help="Score pattern")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (for pattern=random)")
    args = parser.parse_args()

    if args.pattern == "random":
        random.seed(args.seed)

    start_time = datetime.utcnow()
    windows = build_windows(
        lat0=args.lat,
        lon0=args.lon,
        n=args.n,
        length_m=args.length,
        base_speed=args.speed,
        pattern=args.pattern,
        start_time=start_time
    )

    print(f"Generated {len(windows)} windows around ({args.lat}, {args.lon}), length={args.length} m, pattern={args.pattern}")
    post_windows(api_base=args.api, device_serial=args.serial, windows=windows)

    # Подсказка для просмотра карты
    print("\nOpen heatmap URL (adjust period/radius if needed):")
    print(f"{args.api.rstrip('/')}/heatmap?lat={args.lat:.6f}&lon={args.lon:.6f}&radius_m={int(max(600, args.length))}&period=365%20days&metric=p95&img_w=900&img_h=900")

if __name__ == "__main__":
    main()