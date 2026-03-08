import math

def meters_per_degree_lat() -> float:
    return 111_320.0

def meters_per_degree_lon_at_lat(lat_deg: float) -> float:
    # Экватор: ~111.320 км/град, уменьшается к полюсам
    return 111_320.0 * math.cos(math.radians(lat_deg))

def bbox_from_center(lat: float, lon: float, radius_m: float):
    m_per_deg_lat = meters_per_degree_lat()
    m_per_deg_lon = meters_per_degree_lon_at_lat(lat) or 1e-6
    dlat = radius_m / m_per_deg_lat
    dlon = radius_m / m_per_deg_lon
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

def cell_deg_size(lat_center: float, cell_size_m: float):
    dlat = cell_size_m / meters_per_degree_lat()
    dlon = cell_size_m / (meters_per_degree_lon_at_lat(lat_center) or 1e-6)
    return dlat, dlon

def clamp(v, lo, hi):
    return max(lo, min(hi, v))