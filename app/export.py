from datetime import datetime
from typing import List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.models import Activity


def format_pace(seconds_per_km: float) -> str:
    """格式化配速为分:秒格式"""
    if seconds_per_km is None or seconds_per_km <= 0:
        return ""
    minutes = int(seconds_per_km // 60)
    secs = int(seconds_per_km % 60)
    return f"{minutes}:{secs:02d}"


def export_activities_xls(activities: List[Activity], output_path: str):
    """导出活动列表到 XLS 文件"""
    wb = Workbook()
    ws = wb.active
    ws.title = "活动记录"

    # 表头
    headers = [
        "日期",
        "类型",
        "名称",
        "时长(分钟)",
        "距离(km)",
        "配速(/km)",
        "平均心率",
        "最大心率",
        "步频",
        "功率(W)",
        "爬升(m)",
        "下降(m)",
        "热量(kcal)",
        "天气",
    ]
    ws.append(headers)

    # 表头样式
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    # 数据行
    for a in activities:
        ws.append([
            a.start_time.strftime("%Y-%m-%d %H:%M") if a.start_time else "",
            a.activity_type or "",
            a.activity_name or "",
            round(a.duration_minutes, 1) if a.duration_minutes else "",
            round(a.distance_km, 2) if a.distance_km else "",
            format_pace(a.avg_pace_seconds_per_km),
            a.avg_heartrate or "",
            a.max_heartrate or "",
            round(a.avg_cadence, 1) if a.avg_cadence else "",
            round(a.avg_power, 1) if a.avg_power else "",
            round(a.elevation_gain, 1) if a.elevation_gain else "",
            round(a.elevation_loss, 1) if a.elevation_loss else "",
            a.calories or "",
            a.weather or "",
        ])

    # 调整列宽
    column_widths = [18, 10, 30, 12, 10, 10, 10, 10, 8, 8, 8, 8, 10, 20]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)].width = width

    wb.save(output_path)
    print(f"已导出 {len(activities)} 条活动到 {output_path}")
