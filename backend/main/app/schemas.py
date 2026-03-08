from pydantic import BaseModel, Field
from typing import Optional, List

class Window(BaseModel):
    t_start: str
    t_end: str
    lat: float
    lon: float
    speed_mps: Optional[float] = None
    hdop: Optional[float] = None
    sats: Optional[int] = None
    a_rms: Optional[float] = None
    a_p95: Optional[float] = None
    a_max: Optional[float] = None
    jerk_p95: Optional[float] = None
    peaks: Optional[int] = None
    e_0_5: Optional[float] = None
    e_5_12: Optional[float] = None
    e_12_30: Optional[float] = None
    roughness_score: Optional[float] = None

class IngestWindows(BaseModel):
    device_serial: str = Field(..., min_length=1)
    trip_id: Optional[int] = None
    windows: List[Window]

class IngestResult(BaseModel):
    inserted: int
    trip_id: int

class LegendItem(BaseModel):
    label: str
    threshold: float
    color_rgba: tuple

class Legend(BaseModel):
    metric: str
    items: List[LegendItem]