import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.models import Activity


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                activity_id INTEGER PRIMARY KEY,
                activity_name TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                start_time TEXT NOT NULL,
                timezone TEXT,
                duration_seconds REAL,
                distance_meters REAL,
                avg_pace_seconds_per_km REAL,
                avg_heartrate INTEGER,
                max_heartrate INTEGER,
                avg_cadence REAL,
                avg_power REAL,
                elevation_gain REAL,
                elevation_loss REAL,
                calories INTEGER,
                avg_temperature REAL,
                weather TEXT,
                geo_json TEXT,
                raw_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_time TEXT NOT NULL,
                activities_fetched INTEGER,
                activities_new INTEGER,
                details_fetched INTEGER DEFAULT 0,
                details_failed INTEGER DEFAULT 0,
                status TEXT
            )
        """)
        # 迁移：为已有数据库添加 details_fetched 和 details_failed 列
        try:
            conn.execute("ALTER TABLE sync_history ADD COLUMN details_fetched INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE sync_history ADD COLUMN details_failed INTEGER DEFAULT 0")
        except Exception:
            pass  # 列已存在会报错，忽略即可
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_details (
                activity_id INTEGER PRIMARY KEY,
                details_json TEXT,
                splits_json TEXT,
                has_details INTEGER DEFAULT 0,
                fetched_at TEXT,
                FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_details_has_details
            ON activity_details(has_details)
        """)
        conn.commit()
        conn.close()

    def save_activity(self, activity: Activity) -> bool:
        """Save or update an activity. Returns True if new, False if updated."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Check if exists
        cursor.execute(
            "SELECT 1 FROM activities WHERE activity_id = ?", (activity.activity_id,)
        )
        exists = cursor.fetchone() is not None

        cursor.execute(
            """
            INSERT OR REPLACE INTO activities (
                activity_id, activity_name, activity_type, start_time, timezone,
                duration_seconds, distance_meters, avg_pace_seconds_per_km,
                avg_heartrate, max_heartrate, avg_cadence, avg_power,
                elevation_gain, elevation_loss, calories, avg_temperature,
                weather, geo_json, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity.activity_id,
                activity.activity_name,
                activity.activity_type,
                activity.start_time.isoformat(),
                activity.timezone,
                activity.duration_seconds,
                activity.distance_meters,
                activity.avg_pace_seconds_per_km,
                activity.avg_heartrate,
                activity.max_heartrate,
                activity.avg_cadence,
                activity.avg_power,
                activity.elevation_gain,
                activity.elevation_loss,
                activity.calories,
                activity.avg_temperature,
                activity.weather,
                activity.geo_json,
                activity.raw_data,
            ),
        )
        conn.commit()
        conn.close()
        return not exists

    def save_activities(self, activities: List[Activity]) -> tuple[int, int]:
        """Save multiple activities. Returns (fetched, new_count)."""
        new_count = 0
        for activity in activities:
            is_new = self.save_activity(activity)
            if is_new:
                new_count += 1
        return len(activities), new_count

    def get_activities(
        self,
        activity_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Activity]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = "SELECT * FROM activities WHERE 1=1"
        params = []

        if activity_type:
            query += " AND activity_type = ?"
            params.append(activity_type)

        if start_date:
            query += " AND start_time >= ?"
            params.append(start_date.isoformat())

        if end_date:
            query += " AND start_time <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY start_time DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_activity(row) for row in rows]

    def _row_to_activity(self, row: sqlite3.Row) -> Activity:
        return Activity(
            activity_id=row["activity_id"],
            activity_name=row["activity_name"],
            activity_type=row["activity_type"],
            start_time=datetime.fromisoformat(row["start_time"]),
            timezone=row["timezone"] or "",
            duration_seconds=row["duration_seconds"] or 0,
            distance_meters=row["distance_meters"] or 0,
            avg_pace_seconds_per_km=row["avg_pace_seconds_per_km"],
            avg_heartrate=row["avg_heartrate"],
            max_heartrate=row["max_heartrate"],
            avg_cadence=row["avg_cadence"],
            avg_power=row["avg_power"],
            elevation_gain=row["elevation_gain"],
            elevation_loss=row["elevation_loss"],
            calories=row["calories"],
            avg_temperature=row["avg_temperature"],
            weather=row["weather"],
            geo_json=row["geo_json"],
            raw_data=row["raw_data"],
        )

    def log_sync(self, fetched: int, new: int, status: str, details_fetched: int = 0, details_failed: int = 0):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO sync_history (sync_time, activities_fetched, activities_new, details_fetched, details_failed, status) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), fetched, new, details_fetched, details_failed, status),
        )
        conn.commit()
        conn.close()

    def get_recent_syncs(self, limit: int = 10) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM sync_history ORDER BY sync_time DESC LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def save_activity_details(self, activity_id: int, details: dict, splits: dict) -> bool:
        """保存活动详情，返回是否新增成功"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM activity_details WHERE activity_id = ?", (activity_id,)
        )
        exists = cursor.fetchone() is not None

        cursor.execute(
            """
            INSERT OR REPLACE INTO activity_details (
                activity_id, details_json, splits_json, has_details, fetched_at
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (activity_id, json.dumps(details), json.dumps(splits), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return not exists

    def get_activity_details(self, activity_id: int) -> Optional[tuple[dict, dict]]:
        """获取活动详情，返回 (details, splits) 或 None"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT details_json, splits_json FROM activity_details WHERE activity_id = ? AND has_details = 1",
            (activity_id,),
        )
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return None
        return json.loads(row["details_json"]), json.loads(row["splits_json"])

    def has_activity_details(self, activity_id: int) -> bool:
        """检查是否已有详情数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM activity_details WHERE activity_id = ? AND has_details = 1",
            (activity_id,),
        )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def get_activities_without_details(self, limit: int = 100) -> List[int]:
        """获取尚未获取详情的活动 ID 列表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.activity_id FROM activities a
            LEFT JOIN activity_details ad ON a.activity_id = ad.activity_id
            WHERE ad.activity_id IS NULL OR ad.has_details = 0
            ORDER BY a.start_time DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
