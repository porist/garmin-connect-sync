from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Activity:
    """运动活动数据模型"""

    activity_id: int
    activity_name: str
    activity_type: str  # running, cycling, swimming, etc.
    start_time: datetime
    timezone: str
    duration_seconds: float
    distance_meters: float
    avg_pace_seconds_per_km: Optional[float] = None
    avg_heartrate: Optional[int] = None
    max_heartrate: Optional[int] = None
    avg_cadence: Optional[float] = None
    avg_power: Optional[float] = None
    elevation_gain: Optional[float] = None
    elevation_loss: Optional[float] = None
    calories: Optional[int] = None
    avg_temperature: Optional[float] = None
    weather: Optional[str] = None
    geo_json: Optional[str] = None  # GPS track as GeoJSON
    raw_data: Optional[str] = None  # Original JSON for extensibility

    @property
    def distance_km(self) -> float:
        return self.distance_meters / 1000

    @property
    def duration_minutes(self) -> float:
        return self.duration_seconds / 60

    def __repr__(self) -> str:
        return f"<Activity {self.activity_id}: {self.activity_name} ({self.activity_type})>"
