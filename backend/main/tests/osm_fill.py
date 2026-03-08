#!/usr/bin/env python3
import requests, math, time
from datetime import datetime, timedelta

API = "http://localhost:8000/ingest/windows"
DEVICE = "DEV-OSM"

def fetch_roads(min_lat, min_lon, max_lat, max_lon):
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    q = f"""
[out:json][timeout:25];
(
  way["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|service"]({bbox});
);
out geom;
"""
    r = requests.post("https://overpass-api.de/api/interpreter", data=q, headers={"User-Agent":"VibroRoadTest/1.0"}, timeout=40)
    r.raise_for_status()
    data = r.json()
    roads=[]
    for el in data.get("elements", []):
        if el.get("type")=="way" and "geometry" in el:
            poly=[(pt["lat"], pt["lon"]) for pt in el["geometry"]]
            if len(poly)>=2:
                roads.append(poly)
    return roads

def meters_per_deg_lat(): return 111_320.0
def meters_per_deg_lon(lat): return 111_320.0*math.cos(math.radians(lat))

def sample_poly(poly, step_m=30):
    # простая дискретизация по хордам
    res=[poly[0]]
    acc=0.0
    for (la1,lo1),(la2,lo2) in zip(poly[:-1], poly[1:]):
        # грубая метрика в метрах
        mx = meters_per_deg_lon((la1+la2)/2.0)
        my = meters_per_deg_lat()
        dx = (lo2-lo1)*mx
        dy = (la2-la1)*my
        seg = math.hypot(dx,dy)
        if seg<=1e-6:
            continue
        n = max(1, int(seg//step_m))
        for i in range(1, n+1):
            t = i/n
            res.append( (la1 + (la2-la1)*t, lo1 + (lo2-lo1)*t) )
    return res

def post_windows(windows):
    r = requests.post(API, json={"device_serial": DEVICE, "windows": windows}, timeout=60)
    r.raise_for_status()
    print(r.status_code, r.text)

def main():
    # bbox для центра СПб
    min_lat, min_lon, max_lat, max_lon = 59.85, 30.15, 60.05, 30.45
    roads = fetch_roads(min_lat, min_lon, max_lat, max_lon)
    print("roads:", len(roads))
    now = datetime.utcnow()
    windows=[]
    palette=[0.4,0.7,1.0,1.3]
    k=0
    for poly in roads[:50]:  # ограничим 50 дорог для теста
        pts = sample_poly(poly, step_m=35)
        for lat,lon in pts:
            t0 = now + timedelta(seconds=k)
            windows.append({
                "t_start": t0.isoformat()+"Z",
                "t_end": (t0+timedelta(seconds=1)).isoformat()+"Z",
                "lat": lat, "lon": lon,
                "speed_mps": 13.0,
                "roughness_score": palette[(k//20)%len(palette)]
            })
            k+=1
            if len(windows)>=500:
                post_windows(windows)
                windows.clear()
                time.sleep(0.3)
    if windows:
        post_windows(windows)

if __name__=="__main__":
    main()